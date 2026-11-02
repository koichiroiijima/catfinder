from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from app.analyzer import CatAnalyzer
from app.config import Settings
from app.domain import AnalysisJob, JobImage
from app.images import StoredImage
from app.schemas import (
    AnalysisListItem,
    AnalysisStatus,
    AnalysisSummary,
    ChatOptions,
    ImageResult,
    ImageStatus,
    LogEntry,
    TokenUsage,
)
from app.storage import AnalysisStore

logger = logging.getLogger(__name__)

__all__ = ["AnalysisJob", "AnalysisService", "JobImage", "estimate_remaining_seconds"]


def _aggregate_usage(usages: list[TokenUsage | None]) -> TokenUsage | None:
    present = [usage for usage in usages if usage is not None]
    if not present:
        return None

    reasoning = [
        usage.reasoning_tokens
        for usage in present
        if usage.reasoning_tokens is not None
    ]
    costs = [usage.cost for usage in present if usage.cost is not None]
    return TokenUsage(
        input_tokens=sum(usage.input_tokens for usage in present),
        output_tokens=sum(usage.output_tokens for usage in present),
        total_tokens=sum(usage.total_tokens for usage in present),
        reasoning_tokens=sum(reasoning) if reasoning else None,
        cost=sum(costs) if costs else None,
    )


def estimate_remaining_seconds(
    *,
    total: int,
    completed: int,
    elapsed_seconds: float,
) -> int | None:
    """Very rough ETA based on the completion rate observed so far."""
    remaining = total - completed
    if remaining <= 0 or completed <= 0 or elapsed_seconds <= 0:
        return None
    return max(1, round(elapsed_seconds / completed * remaining))


class AnalysisService:
    """Job runner backed by SQLite, so results outlive the browser and restarts.

    Images are written to a working directory while the model reads them and
    persisted as BLOBs before analysis starts. The working directory is removed
    when the job finishes; serving always reads from SQLite.
    """

    def __init__(
        self,
        settings: Settings,
        analyzer: CatAnalyzer | None,
        *,
        upload_root: Path | None = None,
        store: AnalysisStore | None = None,
    ) -> None:
        self._settings = settings
        self._analyzer = analyzer
        self._semaphore = asyncio.Semaphore(settings.llm_concurrency)
        self._jobs: dict[str, AnalysisJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._store = store or AnalysisStore(settings.database_path)

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

    @property
    def store(self) -> AnalysisStore:
        return self._store

    async def initialize(self) -> None:
        """Mark unfinished jobs from a previous process as interrupted."""
        recovered = await self._store.recover_interrupted()
        if recovered:
            logger.warning("Marked %s interrupted job(s) as completed", recovered)

    async def new_job(
        self,
        model: str | None = None,
        options: ChatOptions | None = None,
        *,
        supports_temperature: bool = True,
    ) -> AnalysisJob:
        job_id = uuid.uuid4().hex
        directory = self._root / job_id
        directory.mkdir(parents=True, exist_ok=False)
        job = AnalysisJob(
            id=job_id,
            directory=directory,
            created_at=time.time(),
            model=model,
            options=options if options is not None else ChatOptions(),
            supports_temperature=supports_temperature,
        )
        await self._store.create_job(job)
        self._jobs[job_id] = job
        await self._log(job.id, "info", "解析ジョブを作成しました。")
        return job

    async def attach_image(self, job: AnalysisJob, image: StoredImage) -> JobImage:
        item = JobImage(
            id=image.id,
            filename=image.filename,
            path=image.path,
            media_type=image.media_type,
            size_bytes=image.size_bytes,
        )
        position = len(job.images)
        await self._store.add_image(job.id, item, image.data, position)
        job.images.append(item)
        return item

    async def get(self, job_id: str) -> AnalysisJob | None:
        job = self._jobs.get(job_id)
        if job is not None:
            return job
        return await self._store.get_job(job_id)

    async def image_bytes(self, job_id: str, image_id: str) -> tuple[bytes, str] | None:
        return await self._store.get_image(job_id, image_id)

    async def logs(self, job_id: str) -> list[LogEntry]:
        return await self._store.get_logs(job_id)

    async def history(self, limit: int = 50, offset: int = 0) -> list[AnalysisListItem]:
        return await self._store.list_jobs(limit=limit, offset=offset)

    def start(self, job: AnalysisJob) -> None:
        task = asyncio.create_task(self._run(job), name=f"analysis-{job.id}")
        job.task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _log(self, job_id: str, level: str, message: str) -> None:
        try:
            await self._store.append_log(job_id, level, message)
        except Exception:
            logger.exception("Failed to persist a log entry for %s", job_id)

    async def _run(self, job: AnalysisJob) -> None:
        try:
            job.started_at = time.time()
            job.status = AnalysisStatus.RUNNING
            await self._store.update_job(job)
            await self._log(
                job.id,
                "info",
                f"判定を開始しました（{len(job.images)} 枚 / モデル: {job.model or '既定'}）。",
            )
            await asyncio.gather(*(self._analyze_one(job, item) for item in job.images))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Analysis job %s crashed", job.id)
            await self._log(job.id, "error", "判定処理が異常終了しました。")
        finally:
            interrupted = [
                item
                for item in job.images
                if item.status in (ImageStatus.PENDING, ImageStatus.RUNNING)
            ]
            for item in interrupted:
                item.status = ImageStatus.ERROR
                item.error = "判定が中断されました。"
                try:
                    await self._store.update_image(item)
                except Exception:
                    logger.exception("Failed to persist an interrupted image %s", item.id)
            job.finished_at = time.time()
            job.status = AnalysisStatus.COMPLETED
            try:
                await self._store.update_job(job)
            except Exception:
                logger.exception("Failed to persist the final job state for %s", job.id)
            if interrupted:
                await self._log(
                    job.id,
                    "error",
                    f"中断された画像が {len(interrupted)} 枚あります。",
                )
            else:
                await self._log(job.id, "info", "判定が完了しました。")
            shutil.rmtree(job.directory, ignore_errors=True)

    async def _analyze_one(self, job: AnalysisJob, item: JobImage) -> None:
        async with self._semaphore:
            item.status = ImageStatus.RUNNING
            await self._store.update_image(item)
            await self._log(job.id, "info", f"判定中: {item.filename}")
            if self._analyzer is None:
                item.status = ImageStatus.ERROR
                item.error = "LLM が設定されていません。"
                await self._store.update_image(item)
                await self._log(job.id, "error", f"{item.filename}: {item.error}")
                return
            try:
                outcome = await self._analyzer.analyze(
                    item.path,
                    item.media_type,
                    model=job.model,
                    options=job.options,
                    supports_temperature=job.supports_temperature,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Image %s failed: %s", item.id, exc)
                item.status = ImageStatus.ERROR
                item.error = f"判定に失敗しました: {exc}"[:300]
                await self._store.update_image(item)
                await self._log(job.id, "error", f"{item.filename}: {item.error}")
                return

            item.usage = outcome.usage
            verdict = outcome.verdict
            item.has_cat = verdict.has_cat
            item.confidence = verdict.confidence
            item.reason = verdict.reason
            item.status = ImageStatus.CAT if verdict.has_cat else ImageStatus.NO_CAT
            await self._store.update_image(item)
            label = "猫あり" if verdict.has_cat else "猫なし"
            await self._log(
                job.id,
                "info",
                f"{item.filename}: {label}（確信度 {verdict.confidence:.2f}）",
            )

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
        completed = cat_count + no_cat_count + error_count
        eta_seconds = None
        if job.status == AnalysisStatus.RUNNING and job.started_at is not None:
            eta_seconds = estimate_remaining_seconds(
                total=len(images),
                completed=completed,
                elapsed_seconds=time.time() - job.started_at,
            )
        return AnalysisSummary(
            id=job.id,
            status=job.status,
            total=len(images),
            completed=completed,
            cat_count=cat_count,
            no_cat_count=no_cat_count,
            error_count=error_count,
            model=job.model,
            options=job.options,
            usage=_aggregate_usage([item.usage for item in job.images]),
            eta_seconds=eta_seconds,
            images=images,
        )

    async def delete(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()
            try:
                await job.task
            except (asyncio.CancelledError, Exception):
                pass
        if job is not None:
            shutil.rmtree(job.directory, ignore_errors=True)
        return await self._store.delete_job(job_id)

    async def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            if job.task is not None and not job.task.done():
                job.task.cancel()
        for job in list(self._jobs.values()):
            if job.task is not None:
                try:
                    await job.task
                except (asyncio.CancelledError, Exception):
                    pass
            shutil.rmtree(job.directory, ignore_errors=True)
        self._jobs.clear()
        await self._store.recover_interrupted()
        if self._owns_root:
            shutil.rmtree(self._root, ignore_errors=True)
