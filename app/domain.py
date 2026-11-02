from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas import AnalysisStatus, ChatOptions, ImageStatus, TokenUsage


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
    usage: TokenUsage | None = None


@dataclass(slots=True)
class AnalysisJob:
    id: str
    directory: Path
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    model: str | None = None
    options: ChatOptions = field(default_factory=ChatOptions)
    # Whether the selected provider advertises temperature for this model.
    # Not persisted: recovered jobs are never re-run, so the default is safe.
    supports_temperature: bool = True
    status: AnalysisStatus = AnalysisStatus.QUEUED
    images: list[JobImage] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
