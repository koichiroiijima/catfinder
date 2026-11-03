from __future__ import annotations

import base64
import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx
from langchain.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.schemas import CatVerdict, ChatOptions, TokenUsage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a careful image classifier for a tool that finds cat photos.
Decide whether the image contains at least one cat. Count real cats, including
kittens and partially visible cats. Do not count toys, statues, cartoons,
illustrations, logos, or text.

Give confidence between 0 and 1. Write the reason in Japanese, in one or two
short sentences. Follow the requested output schema exactly.
"""

OPENROUTER_HOST = "openrouter.ai"
MAX_CACHED_ANALYZERS = 32


@dataclass(slots=True)
class AnalysisOutcome:
    """A verdict plus the provider-reported token usage for it."""

    verdict: CatVerdict
    usage: TokenUsage | None = None


class CatAnalyzer(Protocol):
    async def analyze(
        self,
        path: Path,
        media_type: str,
        *,
        model: str | None = None,
        options: ChatOptions | None = None,
        supports_temperature: bool = True,
    ) -> AnalysisOutcome: ...


class AnalyzerConfigurationError(RuntimeError):
    pass


def extract_usage(message: object) -> TokenUsage | None:
    """Read LangChain's normalized usage metadata from a raw AIMessage."""
    metadata = getattr(message, "usage_metadata", None)
    if not isinstance(metadata, dict):
        return None

    input_tokens = int(metadata.get("input_tokens") or 0)
    output_tokens = int(metadata.get("output_tokens") or 0)
    total_tokens = int(metadata.get("total_tokens") or input_tokens + output_tokens)
    details = metadata.get("output_token_details")
    reasoning = details.get("reasoning") if isinstance(details, dict) else None

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=int(reasoning) if reasoning is not None else None,
        cost=_response_cost(message),
    )


def _response_cost(message: object) -> float | None:
    """OpenRouter puts the credit cost in the raw response metadata."""
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    token_usage = metadata.get("token_usage")
    if not isinstance(token_usage, dict):
        return None
    cost = token_usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return None
    return float(cost)


def is_openrouter(base_url: str | None) -> bool:
    if not base_url:
        return False
    host = urlparse(base_url).hostname or ""
    return host == OPENROUTER_HOST or host.endswith(f".{OPENROUTER_HOST}")


def build_messages(data: bytes, media_type: str) -> list[SystemMessage | HumanMessage]:
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=[
                {"type": "text", "text": "Is there a cat in this image?"},
                {
                    "type": "image",
                    "base64": base64.b64encode(data).decode("ascii"),
                    "mime_type": media_type,
                },
            ]
        ),
    ]


class LangChainCatAnalyzer:
    """Vision classifier backed by a LangChain chat model.

    Every verdict is requested through ``json_schema`` structured output, so the
    provider must advertise structured-output support. There is no free-form
    JSON fallback: a model that cannot honour the schema fails that image.
    """

    def __init__(self, model: ChatOpenAI) -> None:
        self._model = model

    async def analyze(self, path: Path, media_type: str) -> AnalysisOutcome:
        data = path.read_bytes()
        structured = self._model.with_structured_output(
            CatVerdict,
            method="json_schema",
            include_raw=True,
        )
        result = await structured.ainvoke(build_messages(data, media_type))

        raw = result.get("raw") if isinstance(result, dict) else result
        parsed = result.get("parsed") if isinstance(result, dict) else result
        parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
        usage = extract_usage(raw)

        if parsing_error is not None:
            raise parsing_error
        if parsed is None:
            raise ValueError("モデルの構造化出力を解析できませんでした。")

        verdict = (
            parsed if isinstance(parsed, CatVerdict) else CatVerdict.model_validate(parsed)
        )
        return AnalysisOutcome(verdict=verdict, usage=usage)

    async def aclose(self) -> None:
        """Release the HTTP clients held by the wrapped chat model.

        ``ChatOpenAI`` can share its default ``httpx`` clients with other
        instances, so close the dedicated clients first. The root clients are
        a fallback for chat models that were constructed without them.
        """
        http_async_client = getattr(self._model, "http_async_client", None)
        if http_async_client is not None:
            try:
                await http_async_client.aclose()
            except Exception:
                logger.warning("Failed to close the async LLM client", exc_info=True)
        else:
            root_async_client = getattr(self._model, "root_async_client", None)
            if root_async_client is not None:
                try:
                    await root_async_client.close()
                except Exception:
                    logger.warning("Failed to close the async LLM client", exc_info=True)

        http_client = getattr(self._model, "http_client", None)
        if http_client is not None:
            try:
                http_client.close()
            except Exception:
                logger.warning("Failed to close the sync LLM client", exc_info=True)
        else:
            root_client = getattr(self._model, "root_client", None)
            if root_client is not None:
                try:
                    root_client.close()
                except Exception:
                    logger.warning("Failed to close the sync LLM client", exc_info=True)


def create_analyzer(settings: Settings) -> MultiModelCatAnalyzer | None:
    """Build the configured analyzer, or None when no model is configured."""
    if not settings.llm_configured:
        return None
    return MultiModelCatAnalyzer(settings)


@dataclass(slots=True)
class _CachedAnalyzer:
    """A cached analyzer plus the number of requests currently using it."""

    analyzer: LangChainCatAnalyzer
    in_use: int = 0
    retired: bool = False


class MultiModelCatAnalyzer:
    """Dispatch each request to a cached per-model analyzer.

    The default model (from ``LLM_MODEL``) is used whenever a request does
    not name one. Clients are cached by the effective request options as well
    as the model, because ``ChatOpenAI`` holds those values on the instance.
    The cache is an LRU with a fixed upper bound, and evicted clients are
    closed after any in-flight request finishes.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._analyzers: OrderedDict[
            tuple[str, str | None, float | None, int | None], _CachedAnalyzer
        ] = OrderedDict()

    async def analyze(
        self,
        path: Path,
        media_type: str,
        *,
        model: str | None = None,
        options: ChatOptions | None = None,
        supports_temperature: bool = True,
    ) -> AnalysisOutcome:
        name = model if model is not None else self._settings.llm_model
        entry = await self._acquire(
            name,
            options,
            supports_temperature=supports_temperature,
        )
        try:
            return await entry.analyzer.analyze(path, media_type)
        finally:
            await self._release(entry)

    async def _acquire(
        self,
        model: str,
        options: ChatOptions | None = None,
        *,
        supports_temperature: bool = True,
    ) -> _CachedAnalyzer:
        resolved = options if options is not None else ChatOptions()
        # Keep the historical temperature=0 default only for models that
        # advertise temperature. Sending it to the others makes OpenRouter
        # fail with "no endpoints found that can handle the requested
        # parameters" when provider.require_parameters is enabled.
        temperature: float | None = None
        if supports_temperature:
            temperature = resolved.temperature if resolved.temperature is not None else 0.0
        effort = str(resolved.reasoning_effort) if resolved.reasoning_effort is not None else None
        key = (model, effort, temperature, resolved.max_tokens)

        cached = self._analyzers.get(key)
        if cached is not None:
            self._analyzers.move_to_end(key)
            cached.in_use += 1
            return cached

        analyzer = await self._build_analyzer(
            model,
            temperature=temperature,
            effort=effort,
            max_tokens=resolved.max_tokens,
        )
        entry = _CachedAnalyzer(analyzer=analyzer, in_use=1)
        self._analyzers[key] = entry
        await self._evict()
        return entry

    async def _build_analyzer(
        self,
        model: str,
        *,
        temperature: float | None,
        effort: str | None,
        max_tokens: int | None,
    ) -> LangChainCatAnalyzer:
        # Do not rely on langchain-openai's process-wide httpx cache: closing
        # one model would otherwise close the HTTP client of another.
        http_client = httpx.Client()
        http_async_client = httpx.AsyncClient()
        kwargs: dict[str, object] = {
            "model": model,
            "api_key": self._settings.llm_api_key,
            "timeout": self._settings.llm_timeout_seconds,
            "max_retries": self._settings.llm_max_retries,
            "http_client": http_client,
            "http_async_client": http_async_client,
            # The custom clients already handle the transport; skip
            # langchain-openai's own transport injection and its warning.
            "http_socket_options": (),
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if effort is not None:
            kwargs["reasoning_effort"] = effort
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if self._settings.llm_base_url:
            kwargs["base_url"] = self._settings.llm_base_url
            if is_openrouter(self._settings.llm_base_url):
                # Only route to endpoints that support the requested
                # parameters, including ``structured_outputs``.
                kwargs["extra_body"] = {"provider": {"require_parameters": True}}

        try:
            chat_model = ChatOpenAI(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            http_client.close()
            await http_async_client.aclose()
            raise AnalyzerConfigurationError(
                f"LLM クライアントを作成できません: {exc}"
            ) from exc

        return LangChainCatAnalyzer(chat_model)

    async def _evict(self) -> None:
        while len(self._analyzers) > MAX_CACHED_ANALYZERS:
            _, entry = self._analyzers.popitem(last=False)
            if entry.in_use == 0:
                await entry.analyzer.aclose()
            else:
                entry.retired = True

    async def _release(self, entry: _CachedAnalyzer) -> None:
        entry.in_use -= 1
        if entry.retired and entry.in_use == 0:
            await entry.analyzer.aclose()

    async def aclose(self) -> None:
        """Retire every cached client.

        Clients with in-flight requests are closed when those requests are
        released, so shutdown never pulls the HTTP client from under a call.
        """
        entries = list(self._analyzers.values())
        self._analyzers.clear()
        for entry in entries:
            entry.retired = True
            if entry.in_use == 0:
                await entry.analyzer.aclose()
