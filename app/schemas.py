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


class ReasoningEffort(StrEnum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChatOptions(BaseModel):
    """Per-request chat completion parameters chosen in the UI."""

    reasoning_effort: ReasoningEffort | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)


class TokenUsage(BaseModel):
    """Token counts reported by the provider for a successful verdict."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int | None = None
    cost: float | None = None


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
    options: ChatOptions = Field(default_factory=ChatOptions)
    usage: TokenUsage | None = None
    eta_seconds: int | None = None
    images: list[ImageResult]


class AnalysisCreated(BaseModel):
    id: str
    status: AnalysisStatus
    total: int


class AnalysisListItem(BaseModel):
    """One row in the persisted analysis history."""

    id: str
    status: AnalysisStatus
    total: int
    cat_count: int
    no_cat_count: int
    error_count: int
    model: str | None = None
    created_at: float
    finished_at: float | None = None


class AnalysisListResponse(BaseModel):
    analyses: list[AnalysisListItem]


class LogEntry(BaseModel):
    id: int
    created_at: float
    level: str
    message: str


class AnalysisLogs(BaseModel):
    id: str
    logs: list[LogEntry]


class ModelCapabilities(BaseModel):
    """Sampling controls advertised by the provider for one model.

    Entries are omitted when the provider does not expose parameter metadata.
    Callers should treat an unknown model as supporting temperature so that
    generic OpenAI-compatible servers keep working.
    """

    supports_temperature: bool = True
    supports_reasoning_effort: bool = False
    supported_efforts: list[str] = Field(default_factory=list)


class ModelsResponse(BaseModel):
    """Vision models offered by the configured OpenAI-compatible provider."""

    models: list[str]
    default: str
    capabilities: dict[str, ModelCapabilities] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool
    model: str
