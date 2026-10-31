from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Protocol

from langchain.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.schemas import CatVerdict

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


class CatAnalyzer(Protocol):
    async def analyze(self, path: Path, media_type: str) -> CatVerdict: ...


class AnalyzerConfigurationError(RuntimeError):
    pass


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

    async def analyze(self, path: Path, media_type: str) -> CatVerdict:
        data = path.read_bytes()

        if self._mode == "json":
            return await self._invoke_json(data, media_type)

        method = "json_schema" if self._mode == "auto" else self._mode
        try:
            structured = self._model.with_structured_output(
                CatVerdict,
                method=method,  # type: ignore[arg-type]
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

        if isinstance(result, CatVerdict):
            return result
        return CatVerdict.model_validate(result)

    async def _invoke_json(self, data: bytes, media_type: str) -> CatVerdict:
        message = await self._model.ainvoke(build_messages(data, media_type, json_mode=True))
        return parse_verdict_text(message_text(message))


def create_analyzer(settings: Settings) -> LangChainCatAnalyzer | None:
    """Build the configured analyzer, or None when no model is configured."""
    if not settings.llm_configured:
        return None

    kwargs: dict[str, object] = {
        "model": settings.llm_model,
        "api_key": settings.llm_api_key,
        "timeout": settings.llm_timeout_seconds,
        "max_retries": settings.llm_max_retries,
        "temperature": 0,
    }
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url

    try:
        model = ChatOpenAI(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - depends on client validation
        raise AnalyzerConfigurationError(f"LLM クライアントを作成できません: {exc}") from exc

    return LangChainCatAnalyzer(model, mode=settings.llm_structured_output)
