from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings
from app.schemas import ModelsResponse

logger = logging.getLogger(__name__)

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


def _supports_structured_output(entry: dict[str, object]) -> bool:
    """Keep entries that advertise structured outputs, and unknown-shaped entries.

    OpenRouter lists ``structured_outputs`` in ``supported_parameters`` when a
    model can honour a ``json_schema`` response format. Most other
    OpenAI-compatible servers do not return parameter metadata at all, so the
    safest policy is to keep them rather than hide every model.
    """
    parameters = entry.get("supported_parameters")
    if not isinstance(parameters, list):
        return True
    return "structured_outputs" in [str(item).lower() for item in parameters]


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
    """List vision models that support structured output from ``/models``."""
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
    return select_model_ids(items)


def select_model_ids(items: list[object]) -> list[str]:
    """Keep deduplicated vision models that can honour a JSON schema."""
    usable: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        if (
            model_id in seen
            or not _supports_vision(item)
            or not _supports_structured_output(item)
        ):
            continue
        seen.add(model_id)
        usable.append(item)
    return sort_model_ids(usable)


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
        configured = self.default.strip()

        async with self._lock:
            now = time.monotonic()
            if self._models is None or now >= self._expires:
                await self._refresh(now)

            if self._models is None:
                # The provider catalog is unavailable, so we cannot verify the
                # configured model. Keep trusting it rather than hiding it.
                valid = [configured] if configured else []
                return ModelsResponse(models=valid, default=configured)

            entries = list(dict.fromkeys(item for item in self._models if item.strip()))
            if configured and configured in entries:
                entries = [configured] + [item for item in entries if item != configured]
            elif configured:
                logger.warning(
                    "LLM_MODEL %r is not offered as a structured-output vision model; "
                    "using %r instead",
                    configured,
                    entries[0] if entries else "",
                )

            default = configured if configured in entries else (entries[0] if entries else "")
            return ModelsResponse(models=entries, default=default)

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
