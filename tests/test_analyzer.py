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
    message_text,
    parse_verdict_text,
)
from app.config import Settings
from app.schemas import CatVerdict
from tests.conftest import make_image


class FailingStructured:
    async def ainvoke(self, _messages):
        raise RuntimeError("structured output unsupported")


class FixedStructured:
    def __init__(self, result: CatVerdict) -> None:
        self.result = result

    async def ainvoke(self, _messages):
        return self.result


class FakeModel:
    def __init__(
        self,
        text: str = "",
        *,
        structured_result: CatVerdict | None = None,
        structured_error: bool = False,
    ) -> None:
        self.text = text
        self.structured_result = structured_result
        self.structured_error = structured_error
        self.method: str | None = None
        self.plain_messages = None

    def with_structured_output(self, _schema, method: str = "json_schema"):
        self.method = method
        if self.structured_error:
            return FailingStructured()
        assert self.structured_result is not None
        return FixedStructured(self.structured_result)

    async def ainvoke(self, messages):
        self.plain_messages = messages
        return AIMessage(content=self.text)


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


def test_multi_model_analyzer_caches_one_chat_model_per_name():
    analyzer = create_analyzer(Settings(_env_file=None))

    first = analyzer._analyzer_for("model-a")
    second = analyzer._analyzer_for("model-a")
    other = analyzer._analyzer_for("model-b")

    assert first is second
    assert first is not other
    assert all(isinstance(item, LangChainCatAnalyzer) for item in (first, other))


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


async def test_analyzer_uses_structured_output(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    expected = CatVerdict(has_cat=True, confidence=0.91, reason="猫です。")
    model = FakeModel(structured_result=expected)

    verdict = await LangChainCatAnalyzer(model).analyze(image, "image/png")

    assert verdict == expected
    assert model.method == "json_schema"


async def test_analyzer_falls_back_to_json_on_structured_failure(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(
        text=json.dumps(
            {"has_cat": True, "confidence": 0.75, "reason": "猫が写っています。"},
            ensure_ascii=False,
        ),
        structured_error=True,
    )

    verdict = await LangChainCatAnalyzer(model, mode="auto").analyze(image, "image/png")

    assert verdict.has_cat is True
    assert verdict.confidence == 0.75
    assert model.plain_messages is not None


async def test_analyzer_does_not_fall_back_outside_auto_mode(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(structured_error=True)

    with pytest.raises(RuntimeError, match="unsupported"):
        await LangChainCatAnalyzer(model, mode="function_calling").analyze(image, "image/png")


async def test_analyzer_json_mode_skips_structured_output(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(text='{"has_cat": false, "confidence": 0.6, "reason": "猫はいません。"}')

    verdict = await LangChainCatAnalyzer(model, mode="json").analyze(image, "image/png")

    assert verdict.has_cat is False
    assert model.method is None
    assert model.plain_messages is not None
    prompt = model.plain_messages[1].content[0]["text"]
    assert "JSON" in prompt
