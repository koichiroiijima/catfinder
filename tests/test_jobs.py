from __future__ import annotations

import asyncio
import time

from app.images import StoredImage
from app.jobs import AnalysisService, JobImage, estimate_remaining_seconds
from app.schemas import AnalysisStatus, ImageStatus
from tests.conftest import FakeAnalyzer


def test_estimate_remaining_seconds_uses_completion_rate():
    assert estimate_remaining_seconds(total=10, completed=2, elapsed_seconds=10) == 40
    assert estimate_remaining_seconds(total=3, completed=1, elapsed_seconds=1.2) == 2
    assert estimate_remaining_seconds(total=10, completed=0, elapsed_seconds=10) is None
    assert estimate_remaining_seconds(total=10, completed=10, elapsed_seconds=10) is None
    assert estimate_remaining_seconds(total=10, completed=2, elapsed_seconds=0) is None


async def test_summary_reports_eta_only_while_running(settings, tmp_path):
    service = AnalysisService(settings, FakeAnalyzer(), upload_root=tmp_path)
    job = await service.new_job()
    job.started_at = time.time() - 10
    job.status = AnalysisStatus.RUNNING
    job.images = [
        JobImage(
            id="cat",
            filename="cat.png",
            path=tmp_path / "cat.png",
            media_type="image/png",
            size_bytes=10,
            status=ImageStatus.CAT,
        ),
        JobImage(
            id="pending",
            filename="pending.png",
            path=tmp_path / "pending.png",
            media_type="image/png",
            size_bytes=10,
        ),
    ]

    summary = service.summary(job)
    assert summary.completed == 1
    assert summary.eta_seconds is not None
    assert 9 <= summary.eta_seconds <= 11

    job.status = AnalysisStatus.COMPLETED
    assert service.summary(job).eta_seconds is None

    job.status = AnalysisStatus.RUNNING
    job.started_at = None
    assert service.summary(job).eta_seconds is None


async def test_shutdown_marks_running_images_as_interrupted(settings, tmp_path):
    service = AnalysisService(settings, FakeAnalyzer(), upload_root=tmp_path)
    job = await service.new_job()
    image = StoredImage(
        id="image-1",
        filename="cat.png",
        path=tmp_path / "cat.png",
        media_type="image/png",
        size_bytes=1,
        data=b"x",
    )
    await service.attach_image(job, image)

    service.start(job)
    await asyncio.sleep(0)
    await service.shutdown()

    stored = await service.store.get_job(job.id)
    assert stored is not None
    assert stored.status == AnalysisStatus.COMPLETED
    assert stored.images[0].status == ImageStatus.ERROR
    assert stored.images[0].error == "判定が中断されました。"
