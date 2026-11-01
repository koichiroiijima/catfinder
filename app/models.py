from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings
from app.schemas import ModelsResponse

# The catalog is refreshed by background-free lazy fetches. A long TTL keeps
# repeat `/api/models` calls cheap, a short one recovers quickly from a
# transient OpenRouter outage.
SUCCESS_TTL_SECONDS = 600.0
FAILURE_TTL_SECONDS = 60.0
LIST_TIMEOUT_SECONDS = 10.0

FetcherFn = Callable[[str, str, float], Awaitable[list[str]]]


def _supports_vision(entry: dict[str, object]) -> bool:
    """Keep entries that advertise image input, and unknown-shaped entries.

    OpenRouter returns ``architecture.input_modalities`` while most other
    OpenAI-compatible servers only return ``{"id": ...}``. The safest policy
    is to drop entries when we positively know that images are unsupported.
    """
    architecture = entry.get("architecture")
    if not isinstance(architecture, dict):
        return True
    inputs = architecture.get("input_modalities")
    if isinstance(inputs, list):
        return "image" in [str(item).lower() for item in inputs]
    modality = architecture.get("modality")
    if isinstance(modality, str):
        request_side = modality.partition("->")[0]
        return "image" in [part.strip().lower() for part in request_side.split("+")]
    return True


def _price(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("inf")


def _is_free(entry: dict[str, object]) -> bool:
    """A model is free when both token prices are exactly 0."""
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return False
    return _price(pricing.get("prompt")) == 0.0 and _price(pricing.get("completion")) == 0.0


def sort_model_ids(vision_entries: list[dict[str, object]]) -> list[str]:
    """Free models first, then by ID. Sorting happens on raw provider entries."""

    def key(item: dict[str, object]) -> tuple[int, str]:
        free = 0 if _is_free(item) else 1
        name = str(item.get("id") or "")
        return free, name

    return [str(item.get("id")) for item in sorted(vision_entries, key=key)]


async def fetch_vision_models(base_url: str, api_key: str, timeout: float) -> list[str]:
    """List vision model ids from an OpenAI-compatible ``/models`` endpoint."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()

    items = []
    if isinstance(payload, dict):
        items = payload.get("data") or []
    if not isinstance(items, list):
        return []

    vision: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        if model_id in seen or not _supports_vision(item):
            continue
        seen.add(model_id)
        vision.append(item)
    return sort_model_ids(vision)


class ModelCatalog:
    """Lazy, cached view of the models offered by the configured provider."""

    def __init__(
        self,
        settings: Settings,
        *,
        fetch: FetcherFn | None = None,
    ) -> None:
        self._settings = settings
        self._fetch = fetch or fetch_vision_models
        self._models: list[str] | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    @property
    def default(self) -> str:
        return self._settings.llm_model

    async def list(self) -> ModelsResponse:
        default = self.default.strip()

        async with self._lock:
            now = time.monotonic()
            if self._models is None or now >= self._expires:
                await self._refresh(now)

            entries = list(self._models or [])
            if default:
                entries = [default] + [item for item in entries if item != default]
            valid = [item for item in entries if item.strip()]
            return ModelsResponse(models=valid, default=default)

    async def _refresh(self, now: float) -> None:
        base_url = self._settings.llm_base_url
        try:
            if self._settings.llm_configured and base_url:
                models = await self._fetch(
                    base_url,
                    self._settings.llm_api_key,
                    LIST_TIMEOUT_SECONDS,
                )
            else:
                models = []
        except Exception:
            models = []
            self._expires = now + FAILURE_TTL_SECONDS
        else:
            self._models = models
            self._expires = now + SUCCESS_TTL_SECONDS

    async def is_allowed(self, model: str) -> bool:
        catalog = await self.list()
        return model in catalog.models
