from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.domain import AnalysisJob, JobImage
from app.schemas import (
    AnalysisListItem,
    AnalysisStatus,
    ChatOptions,
    ImageStatus,
    LogEntry,
    TokenUsage,
)

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    status TEXT NOT NULL,
    model TEXT,
    options_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_images (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    has_cat INTEGER,
    confidence REAL,
    reason TEXT,
    error TEXT,
    usage_json TEXT,
    image BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS job_images_job_id ON job_images(job_id, position);

CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS job_logs_job_id ON job_logs(job_id, id);
"""


def _dump_usage(usage: TokenUsage | None) -> str | None:
    return usage.model_dump_json() if usage is not None else None


def _load_usage(raw: object) -> TokenUsage | None:
    if not isinstance(raw, str) or not raw:
        return None
    return TokenUsage.model_validate_json(raw)


class AnalysisStore:
    """SQLite persistence for jobs, images, verdicts and execution logs.

    Every method runs its blocking SQLite work in a worker thread. A single
    lock serializes writes, which keeps the simple synchronous API safe
    without a connection pool.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(SCHEMA)

    async def _run(self, function, *args):
        async with self._lock:
            return await asyncio.to_thread(function, *args)

    async def create_job(self, job: AnalysisJob) -> None:
        await self._run(self._create_job, job)

    def _create_job(self, job: AnalysisJob) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, created_at, started_at, finished_at, status, model, options_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.created_at,
                    job.started_at,
                    job.finished_at,
                    str(job.status),
                    job.model,
                    job.options.model_dump_json(),
                ),
            )

    async def update_job(self, job: AnalysisJob) -> None:
        await self._run(self._update_job, job)

    def _update_job(self, job: AnalysisJob) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET started_at = ?, finished_at = ?, status = ?, model = ?, options_json = ?
                WHERE id = ?
                """,
                (
                    job.started_at,
                    job.finished_at,
                    str(job.status),
                    job.model,
                    job.options.model_dump_json(),
                    job.id,
                ),
            )

    async def add_image(self, job_id: str, item: JobImage, data: bytes, position: int) -> None:
        await self._run(self._add_image, job_id, item, data, position)

    def _add_image(self, job_id: str, item: JobImage, data: bytes, position: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO job_images (
                    id, job_id, position, filename, media_type, size_bytes,
                    status, has_cat, confidence, reason, error, usage_json, image
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    job_id,
                    position,
                    item.filename,
                    item.media_type,
                    item.size_bytes,
                    str(item.status),
                    None if item.has_cat is None else int(item.has_cat),
                    item.confidence,
                    item.reason,
                    item.error,
                    _dump_usage(item.usage),
                    data,
                ),
            )

    async def update_image(self, item: JobImage) -> None:
        await self._run(self._update_image, item)

    def _update_image(self, item: JobImage) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE job_images
                SET status = ?, has_cat = ?, confidence = ?, reason = ?, error = ?, usage_json = ?
                WHERE id = ?
                """,
                (
                    str(item.status),
                    None if item.has_cat is None else int(item.has_cat),
                    item.confidence,
                    item.reason,
                    item.error,
                    _dump_usage(item.usage),
                    item.id,
                ),
            )

    async def append_log(self, job_id: str, level: str, message: str) -> None:
        await self._run(self._append_log, job_id, level, message)

    def _append_log(self, job_id: str, level: str, message: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO job_logs (job_id, created_at, level, message) VALUES (?, ?, ?, ?)",
                (job_id, time.time(), level, message),
            )

    async def get_logs(self, job_id: str, limit: int = 1000) -> list[LogEntry]:
        return await self._run(self._get_logs, job_id, limit)

    def _get_logs(self, job_id: str, limit: int) -> list[LogEntry]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, level, message
                FROM job_logs
                WHERE job_id = ?
                ORDER BY id
                LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
        return [
            LogEntry(
                id=int(row["id"]),
                created_at=float(row["created_at"]),
                level=str(row["level"]),
                message=str(row["message"]),
            )
            for row in rows
        ]

    async def get_job(self, job_id: str) -> AnalysisJob | None:
        return await self._run(self._get_job, job_id)

    def _get_job(self, job_id: str) -> AnalysisJob | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            image_rows = connection.execute(
                "SELECT * FROM job_images WHERE job_id = ? ORDER BY position",
                (job_id,),
            ).fetchall()

        images = [
            JobImage(
                id=str(image["id"]),
                filename=str(image["filename"]),
                path=Path(),
                media_type=str(image["media_type"]),
                size_bytes=int(image["size_bytes"]),
                status=ImageStatus(image["status"]),
                has_cat=None if image["has_cat"] is None else bool(image["has_cat"]),
                confidence=image["confidence"],
                reason=image["reason"],
                error=image["error"],
                usage=_load_usage(image["usage_json"]),
            )
            for image in image_rows
        ]
        return AnalysisJob(
            id=str(row["id"]),
            directory=Path(),
            created_at=float(row["created_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=AnalysisStatus(row["status"]),
            model=row["model"],
            options=ChatOptions.model_validate_json(row["options_json"]),
            images=images,
        )

    async def get_image(self, job_id: str, image_id: str) -> tuple[bytes, str] | None:
        return await self._run(self._get_image, job_id, image_id)

    def _get_image(self, job_id: str, image_id: str) -> tuple[bytes, str] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT image, media_type FROM job_images WHERE job_id = ? AND id = ?",
                (job_id, image_id),
            ).fetchone()
        if row is None:
            return None
        return bytes(row["image"]), str(row["media_type"])

    async def list_jobs(self, limit: int = 50, offset: int = 0) -> list[AnalysisListItem]:
        return await self._run(self._list_jobs, limit, offset)

    def _list_jobs(self, limit: int, offset: int) -> list[AnalysisListItem]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    j.id,
                    j.status,
                    j.model,
                    j.created_at,
                    j.finished_at,
                    COUNT(i.id) AS total,
                    COALESCE(SUM(CASE WHEN i.status = 'cat'
                        THEN 1 ELSE 0 END), 0) AS cat_count,
                    COALESCE(SUM(CASE WHEN i.status = 'no_cat'
                        THEN 1 ELSE 0 END), 0) AS no_cat_count,
                    COALESCE(SUM(CASE WHEN i.status = 'error'
                        THEN 1 ELSE 0 END), 0) AS error_count
                FROM jobs AS j
                LEFT JOIN job_images AS i ON i.job_id = j.id
                GROUP BY j.id
                ORDER BY j.created_at DESC, j.id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [
            AnalysisListItem(
                id=str(row["id"]),
                status=AnalysisStatus(row["status"]),
                total=int(row["total"]),
                cat_count=int(row["cat_count"]),
                no_cat_count=int(row["no_cat_count"]),
                error_count=int(row["error_count"]),
                model=row["model"],
                created_at=float(row["created_at"]),
                finished_at=row["finished_at"],
            )
            for row in rows
        ]

    async def delete_job(self, job_id: str) -> bool:
        return await self._run(self._delete_job, job_id)

    def _delete_job(self, job_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cursor.rowcount > 0

    async def recover_interrupted(self) -> int:
        return await self._run(self._recover_interrupted)

    def _recover_interrupted(self) -> int:
        now = time.time()
        message = "サーバー再起動により中断されました。"
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE status IN (?, ?)",
                (str(AnalysisStatus.QUEUED), str(AnalysisStatus.RUNNING)),
            ).fetchall()
            for row in rows:
                job_id = str(row["id"])
                connection.execute(
                    "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
                    (str(AnalysisStatus.COMPLETED), now, job_id),
                )
                connection.execute(
                    """
                    UPDATE job_images
                    SET status = ?, error = ?
                    WHERE job_id = ? AND status IN (?, ?)
                    """,
                    (
                        str(ImageStatus.ERROR),
                        message,
                        job_id,
                        str(ImageStatus.PENDING),
                        str(ImageStatus.RUNNING),
                    ),
                )
                connection.execute(
                    "INSERT INTO job_logs (job_id, created_at, level, message) VALUES (?, ?, ?, ?)",
                    (job_id, now, "error", message),
                )
        return len(rows)
