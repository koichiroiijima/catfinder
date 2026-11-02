from __future__ import annotations

from app.models import (
    ModelCatalog,
    _supports_structured_output,
    _supports_vision,
    select_model_ids,
    select_models,
    sort_model_ids,
)
from app.schemas import ModelsResponse


async def _ok_fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
    return ["m-a", "m-b", "m-a"]


async def _fail_fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
    raise RuntimeError("provider unreachable")


async def _echo_fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
    return [f"seen:{base_url}:{api_key}"]


async def test_catalog_uses_first_model_when_default_is_not_listed(settings):
    catalog = ModelCatalog(settings, fetch=_ok_fetch)
    response = await catalog.list()

    # The configured default is not structured-output capable (it is absent
    # from the provider catalog), so the first capable model is the default.
    assert response.default == "m-a"
    assert response.models == ["m-a", "m-b"]
    assert response.models.count(settings.llm_model) == 0


async def test_catalog_puts_default_first_when_listed(settings):
    async def fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
        return ["m-a", settings.llm_model, "m-b"]

    catalog = ModelCatalog(settings, fetch=fetch)
    response = await catalog.list()

    assert response.default == settings.llm_model
    assert response.models[0] == settings.llm_model
    assert response.models.count(settings.llm_model) == 1
    assert response.models == [settings.llm_model, "m-a", "m-b"]


async def test_catalog_falls_back_to_default_only_on_fetch_failure(settings):
    catalog = ModelCatalog(settings, fetch=_fail_fetch)
    response = await catalog.list()

    assert response == ModelsResponse(models=[settings.llm_model], default=settings.llm_model)


async def test_catalog_returns_empty_when_nothing_is_usable(settings):
    async def fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
        return []

    catalog = ModelCatalog(settings, fetch=fetch)
    response = await catalog.list()

    assert response == ModelsResponse(models=[], default="")


async def test_catalog_passes_base_url_and_api_key_to_fetcher(settings):
    configured = settings.model_copy(
        update={"llm_base_url": "https://example.com/api/v1", "llm_api_key": "sk-test"}
    )
    async def fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
        return [settings.llm_model, f"seen:{base_url}:{api_key}"]

    catalog = ModelCatalog(configured, fetch=fetch)
    response = await catalog.list()

    assert response.models == [
        settings.llm_model,
        "seen:https://example.com/api/v1:sk-test",
    ]


async def test_catalog_lists_nothing_when_not_configured(settings):
    unconfigured = settings.model_copy(update={"llm_model": "", "llm_base_url": ""})
    catalog = ModelCatalog(unconfigured, fetch=_ok_fetch)
    response = await catalog.list()

    assert response == ModelsResponse(models=[], default="")


async def test_catalog_is_allowed_accepts_listed_models_only(settings):
    catalog = ModelCatalog(settings, fetch=_ok_fetch)

    assert await catalog.is_allowed(settings.llm_model) is False
    assert await catalog.is_allowed("m-a") is True
    assert await catalog.is_allowed("m-b") is True
    assert await catalog.is_allowed("not-listed-model") is False


async def test_catalog_is_allowed_after_fetch_failure(settings):
    catalog = ModelCatalog(settings, fetch=_fail_fetch)

    assert await catalog.is_allowed(settings.llm_model) is True
    assert await catalog.is_allowed("not-listed-model") is False


def test_sort_model_ids_puts_free_models_first_then_sorts_by_id():
    entries = [
        {"id": "z-paid", "pricing": {"prompt": "0.5", "completion": "0.001"}},
        {"id": "b-free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "a-paid", "pricing": {"prompt": 1.0}},  # completion missing -> not free
        {"id": "z-free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "no-pricing"},
    ]

    ids = sort_model_ids([entry for entry in entries if _supports_vision(entry)])

    assert ids == ["b-free", "z-free", "a-paid", "no-pricing", "z-paid"]


def test_supports_vision_drops_known_text_only_entries():
    assert _supports_vision({"id": "m", "architecture": {"input_modalities": ["text"]}}) is False
    assert _supports_vision(
        {"id": "m", "architecture": {"input_modalities": ["text", "image"]}}
    ) is True
    assert _supports_vision(
        {"id": "m", "architecture": {"modality": "text->text"}}
    ) is False
    assert _supports_vision(
        {"id": "m", "architecture": {"modality": "text+image->text"}}
    ) is True
    assert _supports_vision({"id": "m"}) is True  # unknown shape: keep


def test_supports_structured_output_requires_the_advertised_parameter():
    assert _supports_structured_output(
        {"id": "m", "supported_parameters": ["temperature", "structured_outputs"]}
    ) is True
    assert _supports_structured_output(
        {"id": "m", "supported_parameters": ["temperature", "response_format"]}
    ) is False
    assert _supports_structured_output({"id": "m", "supported_parameters": []}) is False
    assert _supports_structured_output({"id": "m"}) is True  # unknown shape: keep


def test_select_model_ids_requires_vision_and_structured_output():
    items = [
        {
            "id": "vision-structured",
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["structured_outputs"],
        },
        {
            "id": "vision-only",
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["temperature"],
        },
        {
            "id": "text-structured",
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["structured_outputs"],
        },
        {"id": "unknown-shape"},
        {"id": "vision-structured"},  # duplicate
        "not-a-dict",
    ]

    assert select_model_ids(items) == ["unknown-shape", "vision-structured"]


def test_select_models_extracts_sampling_capabilities():
    items = [
        {
            "id": "temp-model",
            "supported_parameters": ["temperature", "structured_outputs"],
            "reasoning": {"supported_efforts": ["low", "high"]},
        },
        {
            "id": "no-temp-model",
            "supported_parameters": ["structured_outputs", "reasoning_effort"],
            "reasoning": {"supported_efforts": ["minimal", "low"]},
        },
        {"id": "unknown-model"},
    ]

    ids, capabilities = select_models(items)

    assert ids == ["no-temp-model", "temp-model", "unknown-model"]
    assert capabilities["temp-model"].supports_temperature is True
    assert capabilities["no-temp-model"].supports_temperature is False
    assert capabilities["no-temp-model"].supports_reasoning_effort is True
    assert capabilities["no-temp-model"].supported_efforts == ["minimal", "low"]
    # Providers that do not expose parameter metadata stay unknown, so the
    # caller keeps sending parameters instead of hiding the model.
    assert "unknown-model" not in capabilities


async def test_catalog_reports_model_capabilities(settings):
    async def fetch(base_url: str, api_key: str, timeout: float) -> list[object]:
        return [
            {
                "id": settings.llm_model,
                "supported_parameters": ["temperature", "structured_outputs"],
            },
            {
                "id": "no-temp-model",
                "supported_parameters": ["structured_outputs"],
            },
        ]

    catalog = ModelCatalog(settings, fetch=fetch)
    response = await catalog.list()

    assert response.models == [settings.llm_model, "no-temp-model"]
    assert response.capabilities[settings.llm_model].supports_temperature is True
    assert response.capabilities["no-temp-model"].supports_temperature is False
