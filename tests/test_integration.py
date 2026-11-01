from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.analyzer import create_analyzer
from app.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LLM_INTEGRATION") != "1",
    reason="set RUN_LLM_INTEGRATION=1 to call the real vision model",
)

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


async def test_real_vision_model() -> None:
    raw_path = os.getenv("LLM_INTEGRATION_IMAGE")
    if not raw_path:
        pytest.skip("set LLM_INTEGRATION_IMAGE to a local image path")

    path = Path(raw_path)
    assert path.is_file(), f"image not found: {path}"
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    assert media_type is not None, f"unsupported image suffix: {path.suffix}"

    analyzer = create_analyzer(Settings())
    assert analyzer is not None, "LLM_MODEL is not configured"

    outcome = await analyzer.analyze(path, media_type)
    assert isinstance(outcome.verdict.has_cat, bool)
    assert 0 <= outcome.verdict.confidence <= 1
    assert outcome.verdict.reason
