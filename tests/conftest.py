from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.analyzer import AnalysisOutcome
from app.config import Settings
from app.main import create_app
from app.models import ModelCatalog
from app.schemas import CatVerdict, ChatOptions, TokenUsage


def make_image(
    color: tuple[int, int, int] = (200, 120, 40),
    *,
    size: tuple[int, int] = (32, 32),
    image_format: str = "PNG",
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    return buffer.getvalue()


class FakeAnalyzer:
    def __init__(
        self,
        verdicts: dict[bytes, CatVerdict] | None = None,
        failures: set[bytes] | None = None,
        usage: TokenUsage | None = None,
        usages: dict[bytes, TokenUsage] | None = None,
    ) -> None:
        self.verdicts = verdicts or {}
        self.failures = failures or set()
        self.usage = usage
        self.usages = usages or {}
        self.calls: list[bytes] = []
        self.models: list[str | None] = []
        self.options: list[ChatOptions | None] = []

    async def analyze(
        self,
        path,
        media_type: str,
        *,
        model: str | None = None,
        options: ChatOptions | None = None,
    ) -> AnalysisOutcome:
        await asyncio.sleep(0.01)
        data = path.read_bytes()
        self.calls.append(data)
        self.models.append(model)
        self.options.append(options)
        if data in self.failures:
            raise RuntimeError("model exploded")
        return AnalysisOutcome(
            verdict=self.verdicts[data],
            usage=self.usages.get(data, self.usage),
        )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        llm_model="test-vision-model",
        llm_concurrency=2,
        max_files=3,
        max_file_mb=2,
        analysis_ttl_minutes=60,
        upload_dir=tmp_path / "uploads",
    )


@contextmanager
def client_for(
    settings: Settings,
    analyzer,
    catalog: ModelCatalog | None = None,
) -> Iterator[TestClient]:
    with TestClient(create_app(settings, analyzer, catalog)) as client:
        yield client


def wait_for_completion(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/analyses/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == "completed":
            return payload
        time.sleep(0.02)
    raise AssertionError(f"analysis {job_id} did not complete")
