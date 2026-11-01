from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

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


def create_analyzer(settings: Settings) -> MultiModelCatAnalyzer | None:
    """Build the configured analyzer, or None when no model is configured."""
    if not settings.llm_configured:
        return None
    return MultiModelCatAnalyzer(settings)


class MultiModelCatAnalyzer:
    """Dispatch each request to a cached per-model analyzer.

    The default model (from ``LLM_MODEL``) is used whenever a request does
    not name one. Clients are cached by the effective request options as well
    as the model, because ``ChatOpenAI`` holds those values on the instance.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._analyzers: dict[
            tuple[str, str | None, float, int | None], LangChainCatAnalyzer
        ] = {}

    async def analyze(
        self,
        path: Path,
        media_type: str,
        *,
        model: str | None = None,
        options: ChatOptions | None = None,
    ) -> AnalysisOutcome:
        name = model if model is not None else self._settings.llm_model
        analyzer = self._analyzer_for(name, options)
        return await analyzer.analyze(path, media_type)

    def _analyzer_for(
        self,
        model: str,
        options: ChatOptions | None = None,
    ) -> LangChainCatAnalyzer:
        resolved = options if options is not None else ChatOptions()
        temperature = resolved.temperature if resolved.temperature is not None else 0.0
        effort = str(resolved.reasoning_effort) if resolved.reasoning_effort is not None else None
        key = (model, effort, temperature, resolved.max_tokens)
        existing = self._analyzers.get(key)
        if existing is not None:
            return existing

        kwargs: dict[str, object] = {
            "model": model,
            "api_key": self._settings.llm_api_key,
            "timeout": self._settings.llm_timeout_seconds,
            "max_retries": self._settings.llm_max_retries,
            "temperature": temperature,
        }
        if effort is not None:
            kwargs["reasoning_effort"] = effort
        if resolved.max_tokens is not None:
            kwargs["max_tokens"] = resolved.max_tokens
        if self._settings.llm_base_url:
            kwargs["base_url"] = self._settings.llm_base_url
            if is_openrouter(self._settings.llm_base_url):
                # Only route to endpoints that support the requested
                # parameters, including ``structured_outputs``.
                kwargs["extra_body"] = {"provider": {"require_parameters": True}}

        try:
            chat_model = ChatOpenAI(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            raise AnalyzerConfigurationError(
                f"LLM クライアントを作成できません: {exc}"
            ) from exc

        analyzer = LangChainCatAnalyzer(chat_model)
        self._analyzers[key] = analyzer
        return analyzer
