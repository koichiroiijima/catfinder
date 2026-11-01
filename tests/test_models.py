from __future__ import annotations

from app.models import ModelCatalog, _supports_vision, sort_model_ids
from app.schemas import ModelsResponse


async def _ok_fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
    return ["m-a", "m-b", "m-a"]


async def _fail_fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
    raise RuntimeError("provider unreachable")


async def _echo_fetch(base_url: str, api_key: str, timeout: float) -> list[str]:
    return [f"seen:{base_url}:{api_key}"]


async def test_catalog_lists_fetched_models_with_default_first(settings):
    catalog = ModelCatalog(settings, fetch=_ok_fetch)
    response = await catalog.list()

    assert response.default == settings.llm_model
    assert response.models[0] == settings.llm_model
    # Duplicates from the provider are dropped and the default never appears twice.
    assert response.models.count(settings.llm_model) == 1
    assert "m-a" in response.models
    assert "m-b" in response.models


async def test_catalog_falls_back_to_default_only_on_fetch_failure(settings):
    catalog = ModelCatalog(settings, fetch=_fail_fetch)
    response = await catalog.list()

    assert response == ModelsResponse(models=[settings.llm_model], default=settings.llm_model)


async def test_catalog_passes_base_url_and_api_key_to_fetcher(settings):
    configured = settings.model_copy(
        update={"llm_base_url": "https://example.com/api/v1", "llm_api_key": "sk-test"}
    )
    catalog = ModelCatalog(configured, fetch=_echo_fetch)
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

    assert await catalog.is_allowed(settings.llm_model) is True
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
