from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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

JSON_INSTRUCTION = """\
Is there a cat in this image? Reply with only a JSON object like:
{"has_cat": true, "confidence": 0.87, "reason": "日本語の短い理由"}
Do not wrap it in Markdown fences.
"""


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


def merge_usage(first: TokenUsage | None, second: TokenUsage | None) -> TokenUsage | None:
    """Sum usage from a structured attempt and its JSON fallback."""
    if first is None:
        return second
    if second is None:
        return first

    reasoning = [
        value
        for value in (first.reasoning_tokens, second.reasoning_tokens)
        if value is not None
    ]
    costs = [value for value in (first.cost, second.cost) if value is not None]
    return TokenUsage(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        total_tokens=first.total_tokens + second.total_tokens,
        reasoning_tokens=sum(reasoning) if reasoning else None,
        cost=sum(costs) if costs else None,
    )


def build_messages(
    data: bytes,
    media_type: str,
    *,
    json_mode: bool = False,
) -> list[SystemMessage | HumanMessage]:
    text = JSON_INSTRUCTION if json_mode else "Is there a cat in this image?"
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=[
                {"type": "text", "text": text},
                {
                    "type": "image",
                    "base64": base64.b64encode(data).decode("ascii"),
                    "mime_type": media_type,
                },
            ]
        ),
    ]


def message_text(message: object) -> str:
    """Flatten the text blocks of an AIMessage across LangChain versions."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def parse_verdict_text(text: str) -> CatVerdict:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("モデルの応答に JSON オブジェクトが含まれていません。")

    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("モデルの応答 JSON を解析できませんでした。") from exc

    return CatVerdict.model_validate(payload)


class LangChainCatAnalyzer:
    """Vision classifier backed by a LangChain chat model."""

    def __init__(self, model: ChatOpenAI, *, mode: str = "auto") -> None:
        if mode not in {"auto", "function_calling", "json_schema", "json"}:
            raise ValueError(f"Unsupported structured output mode: {mode}")
        self._model = model
        self._mode = mode

    async def analyze(self, path: Path, media_type: str) -> AnalysisOutcome:
        data = path.read_bytes()

        if self._mode == "json":
            return await self._invoke_json(data, media_type)

        method = "json_schema" if self._mode == "auto" else self._mode
        try:
            structured = self._model.with_structured_output(
                CatVerdict,
                method=method,  # type: ignore[arg-type]
                include_raw=True,
            )
            result = await structured.ainvoke(build_messages(data, media_type))
        except Exception:
            if self._mode != "auto":
                raise
            logger.warning(
                "Structured output failed; falling back to JSON parsing",
                exc_info=True,
            )
            return await self._invoke_json(data, media_type)

        raw = result.get("raw") if isinstance(result, dict) else result
        parsed = result.get("parsed") if isinstance(result, dict) else result
        parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
        usage = extract_usage(raw)

        if parsing_error is None and parsed is not None:
            verdict = (
                parsed if isinstance(parsed, CatVerdict) else CatVerdict.model_validate(parsed)
            )
            return AnalysisOutcome(verdict=verdict, usage=usage)

        if self._mode != "auto":
            if parsing_error is not None:
                raise parsing_error
            raise ValueError("モデルの構造化出力を解析できませんでした。")

        logger.warning(
            "Structured output was not parsed; falling back to JSON parsing",
            exc_info=parsing_error,
        )
        fallback = await self._invoke_json(data, media_type)
        return AnalysisOutcome(
            verdict=fallback.verdict,
            usage=merge_usage(usage, fallback.usage),
        )

    async def _invoke_json(self, data: bytes, media_type: str) -> AnalysisOutcome:
        message = await self._model.ainvoke(build_messages(data, media_type, json_mode=True))
        return AnalysisOutcome(
            verdict=parse_verdict_text(message_text(message)),
            usage=extract_usage(message),
        )


def create_analyzer(settings: Settings) -> MultiModelCatAnalyzer | None:
    """Build the configured analyzer, or None when no model is configured."""
    if not settings.llm_configured:
        return None
    return MultiModelCatAnalyzer(settings, mode=settings.llm_structured_output)


class MultiModelCatAnalyzer:
    """Dispatch each request to a cached per-model analyzer.

    The default model (from ``LLM_MODEL``) is used whenever a request does
    not name one. Structured-output settings apply equally to every model.
    Clients are cached by the effective request options as well as the model,
    because ``ChatOpenAI`` holds those values on the instance.
    """

    def __init__(self, settings: Settings, *, mode: str = "auto") -> None:
        if mode not in {"auto", "function_calling", "json_schema", "json"}:
            raise ValueError(f"Unsupported structured output mode: {mode}")
        self._settings = settings
        self._mode = mode
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

        try:
            chat_model = ChatOpenAI(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            raise AnalyzerConfigurationError(
                f"LLM クライアントを作成できません: {exc}"
            ) from exc

        analyzer = LangChainCatAnalyzer(chat_model, mode=self._mode)
        self._analyzers[key] = analyzer
        return analyzer
