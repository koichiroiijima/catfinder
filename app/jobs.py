from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.analyzer import CatAnalyzer
from app.config import Settings
from app.images import StoredImage
from app.schemas import (
    AnalysisStatus,
    AnalysisSummary,
    ImageResult,
    ImageStatus,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class JobImage:
    id: str
    filename: str
    path: Path
    media_type: str
    size_bytes: int
    status: ImageStatus = ImageStatus.PENDING
    has_cat: bool | None = None
    confidence: float | None = None
    reason: str | None = None
    error: str | None = None


@dataclass(slots=True)
class AnalysisJob:
    id: str
    directory: Path
    created_at: float
    status: AnalysisStatus = AnalysisStatus.QUEUED
    images: list[JobImage] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


class AnalysisService:
    """In-memory job registry. Results disappear on restart by design."""

    def __init__(
        self,
        settings: Settings,
        analyzer: CatAnalyzer | None,
        *,
        upload_root: Path | None = None,
    ) -> None:
        self._settings = settings
        self._analyzer = analyzer
        self._semaphore = asyncio.Semaphore(settings.llm_concurrency)
        self._jobs: dict[str, AnalysisJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

        if upload_root is not None:
            self._root = upload_root
            self._owns_root = False
            self._root.mkdir(parents=True, exist_ok=True)
        elif settings.upload_dir is not None:
            self._root = settings.upload_dir
            self._owns_root = False
            self._root.mkdir(parents=True, exist_ok=True)
        else:
            self._root = Path(tempfile.mkdtemp(prefix="catfinder-"))
            self._owns_root = True

    @property
    def analyzer_available(self) -> bool:
        return self._analyzer is not None

    def new_job(self) -> AnalysisJob:
        job_id = uuid.uuid4().hex
        directory = self._root / job_id
        directory.mkdir(parents=True, exist_ok=False)
        job = AnalysisJob(
            id=job_id,
            directory=directory,
            created_at=time.monotonic(),
        )
        self._jobs[job_id] = job
        return job

    def attach_image(self, job: AnalysisJob, image: StoredImage) -> JobImage:
        item = JobImage(
            id=image.id,
            filename=image.filename,
            path=image.path,
            media_type=image.media_type,
            size_bytes=image.size_bytes,
        )
        job.images.append(item)
        return item

    def get(self, job_id: str) -> AnalysisJob | None:
        return self._jobs.get(job_id)

    def start(self, job: AnalysisJob) -> None:
        task = asyncio.create_task(self._run(job), name=f"analysis-{job.id}")
        job.task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: AnalysisJob) -> None:
        job.status = AnalysisStatus.RUNNING
        try:
            await asyncio.gather(*(self._analyze_one(job, item) for item in job.images))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Analysis job %s crashed", job.id)
        finally:
            if job.status != AnalysisStatus.COMPLETED:
                job.status = AnalysisStatus.COMPLETED

    async def _analyze_one(self, job: AnalysisJob, item: JobImage) -> None:
        async with self._semaphore:
            item.status = ImageStatus.RUNNING
            if self._analyzer is None:
                item.status = ImageStatus.ERROR
                item.error = "LLM が設定されていません。"
                return
            try:
                verdict = await self._analyzer.analyze(item.path, item.media_type)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Image %s failed: %s", item.id, exc)
                item.status = ImageStatus.ERROR
                item.error = f"判定に失敗しました: {exc}"[:300]
                return

            item.has_cat = verdict.has_cat
            item.confidence = verdict.confidence
            item.reason = verdict.reason
            item.status = ImageStatus.CAT if verdict.has_cat else ImageStatus.NO_CAT

    def summary(self, job: AnalysisJob) -> AnalysisSummary:
        images = [
            ImageResult(
                id=item.id,
                filename=item.filename,
                status=item.status,
                has_cat=item.has_cat,
                confidence=item.confidence,
                reason=item.reason,
                error=item.error,
                url=f"/api/analyses/{job.id}/images/{item.id}",
            )
            for item in job.images
        ]
        cat_count = sum(1 for item in images if item.status == ImageStatus.CAT)
        no_cat_count = sum(1 for item in images if item.status == ImageStatus.NO_CAT)
        error_count = sum(1 for item in images if item.status == ImageStatus.ERROR)
        return AnalysisSummary(
            id=job.id,
            status=job.status,
            total=len(images),
            completed=cat_count + no_cat_count + error_count,
            cat_count=cat_count,
            no_cat_count=no_cat_count,
            error_count=error_count,
            images=images,
        )

    async def delete(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        if job.task is not None and not job.task.done():
            job.task.cancel()
            try:
                await job.task
            except asyncio.CancelledError, Exception:
                pass
        shutil.rmtree(job.directory, ignore_errors=True)
        return True

    async def cleanup_expired(self) -> int:
        cutoff = time.monotonic() - self._settings.analysis_ttl_seconds
        expired = [job.id for job in self._jobs.values() if job.created_at < cutoff]
        for job_id in expired:
            await self.delete(job_id)
        return len(expired)

    async def cleanup_loop(self) -> None:
        interval = max(5.0, min(60.0, self._settings.analysis_ttl_seconds / 2))
        while True:
            await asyncio.sleep(interval)
            try:
                await self.cleanup_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Cleanup failed")

    async def shutdown(self) -> None:
        for job_id in list(self._jobs):
            await self.delete(job_id)
        if self._owns_root:
            shutil.rmtree(self._root, ignore_errors=True)
