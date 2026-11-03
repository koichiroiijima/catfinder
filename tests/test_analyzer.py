from __future__ import annotations

import base64

import pytest
from langchain_core.messages import AIMessage

from app.analyzer import (
    MAX_CACHED_ANALYZERS,
    LangChainCatAnalyzer,
    MultiModelCatAnalyzer,
    build_messages,
    create_analyzer,
    extract_usage,
    is_openrouter,
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
        *,
        structured_result: CatVerdict | None = None,
        structured_error: bool = False,
        parsing_error: bool = False,
        usage: dict | None = None,
        cost: float | None = None,
    ) -> None:
        self.structured_result = structured_result
        self.structured_error = structured_error
        self.parsing_error = parsing_error
        self.usage = usage
        self.cost = cost
        self.method: str | None = None
        self.include_raw: bool | None = None
        self.messages = None

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


async def _cached_analyzer(
    analyzer: MultiModelCatAnalyzer,
    model: str,
    options: ChatOptions | None = None,
    *,
    supports_temperature: bool = True,
) -> LangChainCatAnalyzer:
    entry = await analyzer._acquire(
        model,
        options,
        supports_temperature=supports_temperature,
    )
    try:
        return entry.analyzer
    finally:
        await analyzer._release(entry)


class DummyChatModel:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.root_async_client = None
        self.root_client = None


class AsyncClosableClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class SyncClosableClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ClosableModel:
    def __init__(self) -> None:
        self.root_async_client = AsyncClosableClient()
        self.root_client = SyncClosableClient()


def test_create_analyzer_requires_a_model():
    assert create_analyzer(Settings(_env_file=None, llm_model="")) is None
    analyzer = create_analyzer(Settings(_env_file=None))
    assert isinstance(analyzer, MultiModelCatAnalyzer)


async def test_multi_model_analyzer_caches_one_chat_model_per_name_and_options():
    analyzer = create_analyzer(Settings(_env_file=None))
    assert isinstance(analyzer, MultiModelCatAnalyzer)

    first = await _cached_analyzer(analyzer, "model-a")
    second = await _cached_analyzer(analyzer, "model-a")
    default_temperature = await _cached_analyzer(
        analyzer,
        "model-a",
        ChatOptions(temperature=0.0),
    )
    effort = await _cached_analyzer(
        analyzer,
        "model-a",
        ChatOptions(reasoning_effort="high"),
    )
    limited = await _cached_analyzer(
        analyzer,
        "model-a",
        ChatOptions(max_tokens=500),
    )
    other = await _cached_analyzer(analyzer, "model-b")

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

    await analyzer.aclose()


async def test_multi_model_analyzer_omits_temperature_when_unsupported():
    analyzer = MultiModelCatAnalyzer(Settings(_env_file=None))

    supported = await _cached_analyzer(analyzer, "model-a")
    unsupported = await _cached_analyzer(
        analyzer,
        "model-a",
        supports_temperature=False,
    )

    assert supported is not unsupported
    assert supported._model.temperature == 0.0
    assert unsupported._model.temperature is None

    await analyzer.aclose()


async def test_openrouter_clients_require_capable_endpoints():
    settings = Settings(_env_file=None)
    analyzer = MultiModelCatAnalyzer(settings)

    client = (await _cached_analyzer(analyzer, "model-a"))._model

    assert client.extra_body == {"provider": {"require_parameters": True}}

    await analyzer.aclose()


async def test_other_providers_do_not_get_openrouter_routing():
    settings = Settings(
        _env_file=None,
        llm_base_url="http://127.0.0.1:11434/v1",
    )
    analyzer = MultiModelCatAnalyzer(settings)

    client = (await _cached_analyzer(analyzer, "model-a"))._model

    assert client.extra_body is None

    await analyzer.aclose()


async def test_multi_model_analyzer_bounds_cache_and_closes_evicted(monkeypatch):
    monkeypatch.setattr("app.analyzer.ChatOpenAI", DummyChatModel)
    closed: list[LangChainCatAnalyzer] = []

    async def fake_aclose(self: LangChainCatAnalyzer) -> None:
        closed.append(self)

    monkeypatch.setattr(LangChainCatAnalyzer, "aclose", fake_aclose)
    analyzer = MultiModelCatAnalyzer(Settings(_env_file=None))

    built = [
        await _cached_analyzer(
            analyzer,
            "model-a",
            ChatOptions(max_tokens=index + 1),
        )
        for index in range(MAX_CACHED_ANALYZERS + 2)
    ]

    assert len(analyzer._analyzers) == MAX_CACHED_ANALYZERS
    assert closed == built[:2]

    await analyzer.aclose()

    assert closed == built
    assert analyzer._analyzers == {}


async def test_evicted_analyzer_closes_after_its_request_finishes(monkeypatch):
    monkeypatch.setattr("app.analyzer.ChatOpenAI", DummyChatModel)
    closed: list[LangChainCatAnalyzer] = []

    async def fake_aclose(self: LangChainCatAnalyzer) -> None:
        closed.append(self)

    monkeypatch.setattr(LangChainCatAnalyzer, "aclose", fake_aclose)
    analyzer = MultiModelCatAnalyzer(Settings(_env_file=None))

    in_flight = await analyzer._acquire("model-a", ChatOptions(max_tokens=1))
    for index in range(MAX_CACHED_ANALYZERS):
        entry = await analyzer._acquire(
            "model-a",
            ChatOptions(max_tokens=index + 2),
        )
        await analyzer._release(entry)

    assert in_flight.retired is True
    assert in_flight.analyzer not in closed

    await analyzer._release(in_flight)

    assert closed == [in_flight.analyzer]
    await analyzer.aclose()


async def test_analyzer_closes_provider_clients():
    model = ClosableModel()

    await LangChainCatAnalyzer(model).aclose()

    assert model.root_async_client.closed is True
    assert model.root_client.closed is True


async def test_closing_one_analyzer_does_not_close_another():
    analyzer = MultiModelCatAnalyzer(Settings(_env_file=None))
    first = await analyzer._build_analyzer(
        "model-a",
        temperature=0.0,
        effort=None,
        max_tokens=None,
    )
    second = await analyzer._build_analyzer(
        "model-b",
        temperature=0.0,
        effort=None,
        max_tokens=None,
    )
    first_async = first._model.http_async_client
    first_sync = first._model.http_client
    second_async = second._model.http_async_client
    second_sync = second._model.http_client

    assert first_async is not second_async
    assert first_sync is not second_sync

    await first.aclose()

    assert first_async.is_closed is True
    assert first_sync.is_closed is True
    assert second_async.is_closed is False
    assert second_sync.is_closed is False

    await second.aclose()


def test_is_openrouter_matches_the_host_only():
    assert is_openrouter("https://openrouter.ai/api/v1") is True
    assert is_openrouter("https://api.openrouter.ai/v1") is True
    assert is_openrouter("https://example.com/openrouter.ai") is False
    assert is_openrouter("http://127.0.0.1:11434/v1") is False
    assert is_openrouter(None) is False


def test_default_model_targets_openrouter():
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_model == "nvidia/nemotron-3.5-super-vl:free"


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


async def test_analyzer_uses_json_schema_structured_output(tmp_path):
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


async def test_analyzer_raises_without_a_json_fallback(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(structured_error=True)

    with pytest.raises(RuntimeError, match="unsupported"):
        await LangChainCatAnalyzer(model).analyze(image, "image/png")


async def test_analyzer_raises_when_structured_output_is_not_parsed(tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(make_image())
    model = FakeModel(parsing_error=True)

    with pytest.raises(ValueError, match="bad json"):
        await LangChainCatAnalyzer(model).analyze(image, "image/png")
