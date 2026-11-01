from __future__ import annotations

import io
import os

from PIL import Image

from app.models import ModelCatalog
from app.schemas import CatVerdict
from tests.conftest import FakeAnalyzer, client_for, make_image, wait_for_completion


async def fetch_two_models(base_url: str, api_key: str, timeout: float) -> list[str]:
    return ["other-model", "third-model"]


def _catalog(settings) -> ModelCatalog:
    return ModelCatalog(settings, fetch=fetch_two_models)


def _upload(name: str, data: bytes, media_type: str = "image/png"):
    return ("files", (name, data, media_type))


def _analyzer_for(cat: bytes, no_cat: bytes, broken: bytes) -> FakeAnalyzer:
    return FakeAnalyzer(
        verdicts={
            cat: CatVerdict(has_cat=True, confidence=0.94, reason="猫が写っています。"),
            no_cat: CatVerdict(has_cat=False, confidence=0.88, reason="猫は写っていません。"),
        },
        failures={broken},
    )


def test_health_reports_llm_configuration(settings):
    with client_for(settings, FakeAnalyzer()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "llm_configured": True,
            "model": "test-vision-model",
        }


def test_post_requires_configured_llm(settings):
    unconfigured = settings.model_copy(update={"llm_model": ""})
    with client_for(unconfigured, None) as client:
        data = make_image()
        response = client.post("/api/analyses", files=[_upload("cat.png", data)])
        assert response.status_code == 503

        health = client.get("/api/health").json()
        assert health["llm_configured"] is False
        assert health["model"] == ""


def test_mixed_results_and_image_serving(settings):
    cat = make_image((220, 80, 40))
    no_cat = make_image((40, 80, 220))
    broken = make_image((20, 200, 20))
    analyzer = _analyzer_for(cat, no_cat, broken)

    with client_for(settings, analyzer) as client:
        response = client.post(
            "/api/analyses",
            files=[
                _upload("cat.png", cat),
                _upload("dog.png", no_cat),
                _upload("bad.png", broken),
            ],
        )
        assert response.status_code == 202
        created = response.json()
        assert created["total"] == 3

        payload = wait_for_completion(client, created["id"])
        assert payload["status"] == "completed"
        assert payload["total"] == 3
        assert payload["completed"] == 3
        assert payload["cat_count"] == 1
        assert payload["no_cat_count"] == 1
        assert payload["error_count"] == 1

        by_name = {item["filename"]: item for item in payload["images"]}
        assert by_name["cat.png"]["status"] == "cat"
        assert by_name["cat.png"]["has_cat"] is True
        assert by_name["cat.png"]["confidence"] == 0.94
        assert by_name["dog.png"]["status"] == "no_cat"
        assert by_name["bad.png"]["status"] == "error"
        assert "model exploded" in by_name["bad.png"]["error"]

        image_response = client.get(by_name["cat.png"]["url"])
        assert image_response.status_code == 200
        assert image_response.headers["content-type"] == "image/png"
        assert image_response.content == cat


def test_post_without_files_returns_400(settings):
    with client_for(settings, FakeAnalyzer()) as client:
        response = client.post("/api/analyses")
        assert response.status_code == 400
        assert "1枚以上" in response.json()["detail"]


def test_rejects_non_image_upload(settings):
    with client_for(settings, FakeAnalyzer()) as client:
        response = client.post(
            "/api/analyses",
            files=[_upload("not-an-image.png", b"this is not an image")],
        )
        assert response.status_code == 400
        assert "画像として読み込めない" in response.json()["detail"]

    upload_dir = settings.upload_dir
    assert upload_dir is not None
    assert list(upload_dir.iterdir()) == []


def test_rejects_unsupported_format(settings):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(buffer, format="GIF")
    with client_for(settings, FakeAnalyzer()) as client:
        response = client.post(
            "/api/analyses",
            files=[_upload("anim.gif", buffer.getvalue(), "image/gif")],
        )
        assert response.status_code == 400
        assert "JPEG / PNG / WebP" in response.json()["detail"]


def test_rejects_too_many_files(settings):
    data = make_image()
    with client_for(settings, FakeAnalyzer()) as client:
        response = client.post(
            "/api/analyses",
            files=[_upload(f"{index}.png", data) for index in range(settings.max_files + 1)],
        )
        assert response.status_code == 400
        assert str(settings.max_files) in response.json()["detail"]


def test_rejects_oversized_file(settings):
    pixels = os.urandom(1200 * 1200 * 3)
    image = Image.frombytes("RGB", (1200, 1200), pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    assert len(data) > settings.max_file_bytes

    with client_for(settings, FakeAnalyzer()) as client:
        response = client.post("/api/analyses", files=[_upload("huge.png", data)])
        assert response.status_code == 413
        assert "大きすぎます" in response.json()["detail"]


def test_list_models_reports_provider_catalog(settings):
    with client_for(settings, FakeAnalyzer(), catalog=_catalog(settings)) as client:
        response = client.get("/api/models")
        assert response.status_code == 200
        payload = response.json()
        assert payload["default"] == "test-vision-model"
        assert payload["models"] == [
            "test-vision-model",
            "other-model",
            "third-model",
        ]


def test_post_with_model_uses_requested_model(settings):
    data = make_image()
    analyzer = FakeAnalyzer(
        verdicts={data: CatVerdict(has_cat=True, confidence=0.9, reason="猫。")}
    )
    with client_for(settings, analyzer, catalog=_catalog(settings)) as client:
        response = client.post(
            "/api/analyses",
            data={"model": "other-model"},
            files=[_upload("cat.png", data)],
        )
        assert response.status_code == 202

        payload = wait_for_completion(client, response.json()["id"])
        assert payload["model"] == "other-model"
        assert analyzer.models == ["other-model"]


def test_post_without_model_uses_default(settings):
    data = make_image()
    analyzer = FakeAnalyzer(
        verdicts={data: CatVerdict(has_cat=True, confidence=0.9, reason="猫。")}
    )
    with client_for(settings, analyzer, catalog=_catalog(settings)) as client:
        response = client.post("/api/analyses", files=[_upload("cat.png", data)])
        assert response.status_code == 202

        payload = wait_for_completion(client, response.json()["id"])
        assert payload["model"] == "test-vision-model"
        assert analyzer.models == ["test-vision-model"]


def test_post_with_unknown_model_returns_400(settings):
    data = make_image()
    with client_for(settings, FakeAnalyzer(), catalog=_catalog(settings)) as client:
        response = client.post(
            "/api/analyses",
            data={"model": "not-listed-model"},
            files=[_upload("cat.png", data)],
        )
        assert response.status_code == 400
        assert "対応していないモデル" in response.json()["detail"]


def test_post_with_blank_model_falls_back_to_default(settings):
    data = make_image()
    analyzer = FakeAnalyzer(
        verdicts={data: CatVerdict(has_cat=True, confidence=0.9, reason="猫。")}
    )
    with client_for(settings, analyzer, catalog=_catalog(settings)) as client:
        response = client.post(
            "/api/analyses",
            data={"model": "  "},
            files=[_upload("cat.png", data)],
        )
        assert response.status_code == 202
        payload = wait_for_completion(client, response.json()["id"])
        assert payload["model"] == "test-vision-model"


def test_unknown_job_returns_404(settings):
    with client_for(settings, FakeAnalyzer()) as client:
        assert client.get("/api/analyses/missing").status_code == 404
        assert client.get("/api/analyses/missing/images/nope").status_code == 404
        assert client.delete("/api/analyses/missing").status_code == 404


def test_delete_removes_job_and_files(settings):
    data = make_image()
    analyzer = FakeAnalyzer(
        verdicts={data: CatVerdict(has_cat=True, confidence=0.9, reason="猫。")}
    )
    with client_for(settings, analyzer) as client:
        created = client.post("/api/analyses", files=[_upload("cat.png", data)]).json()
        wait_for_completion(client, created["id"])

        assert client.delete(f"/api/analyses/{created['id']}").status_code == 204
        assert client.get(f"/api/analyses/{created['id']}").status_code == 404

    upload_dir = settings.upload_dir
    assert upload_dir is not None
    assert list(upload_dir.iterdir()) == []
