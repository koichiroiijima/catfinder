from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class ImageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CAT = "cat"
    NO_CAT = "no_cat"
    ERROR = "error"


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"


class CatVerdict(BaseModel):
    """The only shape accepted from the vision model."""

    has_cat: bool = Field(description="True if at least one cat is visible in the image.")
    confidence: float = Field(
        ge=0,
        le=1,
        description="Confidence between 0 and 1.",
    )
    reason: str = Field(description="A short Japanese explanation of what was seen.")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: object) -> float:
        # Some OpenAI-compatible models answer with a percentage despite the
        # prompt. Accept both 0-1 and 0-100 rather than failing the image.
        if isinstance(value, str):
            value = value.strip().rstrip("%")
        number = float(value)  # type: ignore[arg-type]
        if 1 < number <= 100:
            number /= 100
        return min(max(number, 0.0), 1.0)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip()[:500] or "判定理由は返されませんでした。"


class ImageResult(BaseModel):
    id: str
    filename: str
    status: ImageStatus
    has_cat: bool | None = None
    confidence: float | None = None
    reason: str | None = None
    error: str | None = None
    url: str


class AnalysisSummary(BaseModel):
    id: str
    status: AnalysisStatus
    total: int
    completed: int
    cat_count: int
    no_cat_count: int
    error_count: int
    model: str | None = None
    images: list[ImageResult]


class AnalysisCreated(BaseModel):
    id: str
    status: AnalysisStatus
    total: int


class ModelsResponse(BaseModel):
    """Vision models offered by the configured OpenAI-compatible provider."""

    models: list[str]
    default: str


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool
    model: str
