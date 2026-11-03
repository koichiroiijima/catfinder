from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from app.domain import AnalysisJob, JobImage
from app.schemas import AnalysisStatus, ChatOptions, ImageStatus, TokenUsage
from app.storage import AnalysisStore


def _job(job_id: str = "job-1") -> AnalysisJob:
    return AnalysisJob(
        id=job_id,
        directory=Path(),
        created_at=time.time(),
        model="vision-x",
        options=ChatOptions(reasoning_effort="high", temperature=0.2, max_tokens=500),
        status=AnalysisStatus.RUNNING,
    )


def _image(image_id: str = "image-1") -> JobImage:
    return JobImage(
        id=image_id,
        filename="cat.png",
        path=Path(),
        media_type="image/png",
        size_bytes=5,
        status=ImageStatus.RUNNING,
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
async def test_store_restricts_database_permissions(tmp_path):
    path = tmp_path / "private" / "catfinder.db"
    store = AnalysisStore(path)
    await store.create_job(_job())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(f"{path.name}{suffix}")
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


async def test_store_round_trips_job_images_usage_and_logs(tmp_path):
    path = tmp_path / "catfinder.db"
    store = AnalysisStore(path)
    job = _job()
    image = _image()

    await store.create_job(job)
    await store.add_image(job.id, image, b"image-bytes", position=0)
    await store.append_log(job.id, "info", "判定を開始しました。")

    image.status = ImageStatus.CAT
    image.has_cat = True
    image.confidence = 0.91
    image.reason = "猫が写っています。"
    image.usage = TokenUsage(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cost=0.001,
    )
    await store.update_image(image)
    job.status = AnalysisStatus.COMPLETED
    job.finished_at = time.time()
    await store.update_job(job)
    await store.append_log(job.id, "info", "猫あり（確信度 0.91）")

    reopened = AnalysisStore(path)
    loaded = await reopened.get_job(job.id)

    assert loaded is not None
    assert loaded.model == "vision-x"
    assert loaded.status == AnalysisStatus.COMPLETED
    assert loaded.options.reasoning_effort == "high"
    assert loaded.options.max_tokens == 500
    assert len(loaded.images) == 1
    restored = loaded.images[0]
    assert restored.status == ImageStatus.CAT
    assert restored.has_cat is True
    assert restored.confidence == 0.91
    assert restored.usage is not None
    assert restored.usage.cost == 0.001

    assert await reopened.get_image(job.id, image.id) == (b"image-bytes", "image/png")

    logs = await reopened.get_logs(job.id)
    assert [entry.message for entry in logs] == [
        "判定を開始しました。",
        "猫あり（確信度 0.91）",
    ]


async def test_store_lists_newest_jobs_with_counts(tmp_path):
    store = AnalysisStore(tmp_path / "catfinder.db")
    older = _job("older")
    older.created_at = 1.0
    newer = _job("newer")
    newer.created_at = 2.0
    await store.create_job(older)
    await store.create_job(newer)

    cat = _image("cat")
    cat.status = ImageStatus.CAT
    await store.add_image("newer", cat, b"a", 0)
    no_cat = _image("no-cat")
    no_cat.status = ImageStatus.NO_CAT
    await store.add_image("newer", no_cat, b"b", 1)

    listing = await store.list_jobs()

    assert [item.id for item in listing] == ["newer", "older"]
    assert listing[0].total == 2
    assert listing[0].cat_count == 1
    assert listing[0].no_cat_count == 1
    assert listing[0].error_count == 0
    assert listing[1].total == 0


async def test_store_recovery_marks_unfinished_jobs_as_interrupted(tmp_path):
    store = AnalysisStore(tmp_path / "catfinder.db")
    job = _job()
    await store.create_job(job)
    image = _image()
    await store.add_image(job.id, image, b"a", 0)

    assert await store.recover_interrupted() == 1

    loaded = await store.get_job(job.id)
    assert loaded is not None
    assert loaded.status == AnalysisStatus.COMPLETED
    assert loaded.images[0].status == ImageStatus.ERROR
    assert "再起動" in (loaded.images[0].error or "")
    logs = await store.get_logs(job.id)
    assert "再起動" in logs[-1].message


async def test_store_delete_removes_images_logs_and_job(tmp_path):
    store = AnalysisStore(tmp_path / "catfinder.db")
    job = _job()
    await store.create_job(job)
    await store.add_image(job.id, _image(), b"a", 0)
    await store.append_log(job.id, "info", "hello")

    assert await store.delete_job(job.id) is True
    assert await store.get_job(job.id) is None
    assert await store.get_image(job.id, "image-1") is None
    assert await store.get_logs(job.id) == []
    assert await store.delete_job(job.id) is False
