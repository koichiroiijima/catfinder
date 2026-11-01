from __future__ import annotations

import base64
import json

import pytest
from langchain_core.messages import AIMessage

from app.analyzer import (
    LangChainCatAnalyzer,
    MultiModelCatAnalyzer,
    build_messages,
    create_analyzer,
    extract_usage,
    message_text,
    parse_verdict_text,
)
from app.config import Settings
from app.schemas import CatVerdict, ChatOptions, TokenUsage
from tests.conftest import make_image


def ai_message(
    text: str = "",
    *,
    usage: dict | None = None,
    cost: float | None = None,
) -> AIMessage:
    response_metadata = {}
    if cost is not None:
        response_metadata["token_usage"] = {"cost": cost}
    return AIMessage(content=text, usage_metadata=usage, response_metadata=response_metadata)


class FailingStructured:
    async def ainvoke(self, _messages):
        raise RuntimeError("structured output unsupported")


class FixedStructured:
    def __init__(self, result: CatVerdict, message: AIMessage) -> None:
        self.result = result
        self.message = message

    async def ainvoke(self, _messages):
        return {"raw": self.message, "parsed": self.result, "parsing_error": None}


class UnparsedStructured:
    def __init__(self, message: AIMessage) -> None:
        self.message = message

    async def ainvoke(self, _messages):
        return {
            "raw": self.message,
            "parsed": None,
            "parsing_error": ValueError("bad json"),
        }


class FakeModel:
    def __init__(
        self,
        text: str = "",
        *,
        structured_result: CatVerdict | None = None,
        structured_error: bool = False,
        parsing_error: bool = False,
        usage: dict | None = None,
        cost: float | None = None,
    ) -> None:
        self.text = text
        self.structured_result = structured_result
        self.structured_error = structured_error
        self.parsing_error = parsing_error
        self.usage = usage
        self.cost = cost
        self.method: str | None = None
        self.include_raw: bool | None = None
        self.plain_messages = None

    def with_structured_output(
        self,
        _schema,
        method: str = "json_schema",
        include_raw: bool = False,
    ):
        self.method = method
        self.include_raw = include_raw
        if self.structured_error:
            return FailingStructured()
        if self.parsing_error:
            return UnparsedStructured(ai_message(usage=self.usage, cost=self.cost))
        assert self.structured_result is not None
        return FixedStructured(
            self.structured_result,
            ai_message(usage=self.usage, cost=self.cost),
        )

    async def ainvoke(self, messages):
        self.plain_messages = messages
        return ai_message(self.text, usage=self.usage, cost=self.cost)


def test_parse_verdict_accepts_plain_and_fenced_json():
    plain = '{"has_cat": true, "confidence": 0.8, "reason": "猫がいます。"}'
    fenced = f"```json\n{plain}\n```"
    prose = f"Result:\n{plain}\nDone."

    for text in (plain, fenced, prose):
        verdict = parse_verdict_text(text)
        assert verdict.has_cat is True
        assert verdict.confidence == 0.8


def test_parse_verdict_normalizes_percentage_and_clamps():
    high = parse_verdict_text('{"has_cat": false, "confidence": 87, "reason": "なし"}')
    percent = parse_verdict_text('{"has_cat": false, "confidence": "87%", "reason": "なし"}')
    low = parse_verdict_text('{"has_cat": false, "confidence": -3, "reason": "なし"}')
    assert high.confidence == 0.87
    assert percent.confidence == 0.87
    assert low.confidence == 0.0


def test_create_analyzer_requires_a_model():
    assert create_analyzer(Settings(_env_file=None, llm_model="")) is None
    analyzer = create_analyzer(Settings(_env_file=None))
    assert isinstance(analyzer, MultiModelCatAnalyzer)


def test_multi_model_analyzer_caches_one_chat_model_per_name_and_options():
    analyzer = create_analyzer(Settings(_env_file=None))

    first = analyzer._analyzer_for("model-a", ChatOptions())
    second = analyzer._analyzer_for("model-a", ChatOptions())
    default_temperature = analyzer._analyzer_for("model-a", ChatOptions(temperature=0.0))
    effort = analyzer._analyzer_for("model-a", ChatOptions(reasoning_effort="high"))
    limited = analyzer._analyzer_for("model-a", ChatOptions(max_tokens=500))
    other = analyzer._analyzer_for("model-b", ChatOptions())

    assert first is second
    assert first is default_temperature
    assert first is not effort
    assert first is not limited
    assert first is not other
    assert all(
        isinstance(item, LangChainCatAnalyzer)
        for item in (first, effort, limited, other)
    )
    assert effort._model.reasoning_effort == "high"
    assert limited._model.max_tokens == 500
    assert first._model.temperature == 0.0


def test_default_model_targets_openrouter():
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_model == "nvidia/nemotron-3.5-super-vl:free"


def test_parse_verdict_rejects_non_json():
    with pytest.raises(ValueError):
        parse_verdict_text("There is a cat.")


def test_build_messages_embeds_image_as_base64():
    data = make_image()
    messages = build_messages(data, "image/png")
    content = messages[1].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "Is there a cat in this image?"}
    image_block = content[1]
    assert image_block["type"] == "image"
    assert image_block["mime_type"] == "image/png"
    assert base64.b64decode(image_block["base64"]) == data


def test_message_text_flattens_content_blocks():
    message = AIMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    assert message_text(message) == "a\nb"


def test_extract_usage_returns_none_without_usage_metadata():
    assert extract_usage(AIMessage(content="")) is None


def test_extract_usage_reads_reasoning_tokens_and_cost():
    message = ai_message(
        usage={
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "output_token_details": {"reasoning": 12},
        },
        cost=0.00042,
    )

    assert extract_usage(message) == TokenUsage(
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        reasoning_tokens=12,
        cost=0.00042,
    )


async def test_analyzer_uses_structured_output(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    expected = CatVerdict(has_cat=True, confidence=0.91, reason="猫です。")
    model = FakeModel(structured_result=expected)

    outcome = await LangChainCatAnalyzer(model).analyze(image, "image/png")

    assert outcome.verdict == expected
    assert outcome.usage is None
    assert model.method == "json_schema"
    assert model.include_raw is True


async def test_analyzer_reports_usage_from_structured_output(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    expected = CatVerdict(has_cat=True, confidence=0.91, reason="猫です。")
    model = FakeModel(
        structured_result=expected,
        usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        cost=0.001,
    )

    outcome = await LangChainCatAnalyzer(model).analyze(image, "image/png")

    assert outcome.usage == TokenUsage(
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cost=0.001,
    )


async def test_analyzer_falls_back_to_json_on_structured_failure(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(
        text=json.dumps(
            {"has_cat": True, "confidence": 0.75, "reason": "猫が写っています。"},
            ensure_ascii=False,
        ),
        structured_error=True,
        usage={"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
    )

    outcome = await LangChainCatAnalyzer(model, mode="auto").analyze(image, "image/png")

    assert outcome.verdict.has_cat is True
    assert outcome.verdict.confidence == 0.75
    assert outcome.usage == TokenUsage(
        input_tokens=40,
        output_tokens=10,
        total_tokens=50,
    )
    assert model.plain_messages is not None


async def test_analyzer_sums_usage_when_falling_back_after_parse_error(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    usage = {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50}
    model = FakeModel(
        text=json.dumps(
            {"has_cat": True, "confidence": 0.75, "reason": "猫が写っています。"},
            ensure_ascii=False,
        ),
        parsing_error=True,
        usage=usage,
    )

    outcome = await LangChainCatAnalyzer(model, mode="auto").analyze(image, "image/png")

    assert outcome.verdict.has_cat is True
    assert outcome.usage == TokenUsage(
        input_tokens=80,
        output_tokens=20,
        total_tokens=100,
    )


async def test_analyzer_does_not_fall_back_outside_auto_mode(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(structured_error=True)

    with pytest.raises(RuntimeError, match="unsupported"):
        await LangChainCatAnalyzer(model, mode="function_calling").analyze(image, "image/png")


async def test_analyzer_json_mode_skips_structured_output(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(
        text='{"has_cat": false, "confidence": 0.6, "reason": "猫はいません。"}',
        usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
    )

    outcome = await LangChainCatAnalyzer(model, mode="json").analyze(image, "image/png")

    assert outcome.verdict.has_cat is False
    assert outcome.usage == TokenUsage(input_tokens=5, output_tokens=3, total_tokens=8)
    assert model.method is None
    assert model.plain_messages is not None
    prompt = model.plain_messages[1].content[0]["text"]
    assert "JSON" in prompt
