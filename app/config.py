from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter is the default provider. Any other OpenAI-compatible endpoint
    # (OpenAI, Ollama, vLLM, LiteLLM, ...) can be selected with LLM_BASE_URL.
    llm_base_url: str | None = "https://openrouter.ai/api/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "nvidia/nemotron-3.5-super-vl:free"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_concurrency: int = Field(default=8, ge=1, le=32)

    max_files: int = Field(default=500, ge=1, le=500)
    max_file_mb: int = Field(default=15, ge=1, le=100)
    analysis_ttl_minutes: int = Field(default=60, ge=1)

    upload_dir: Path | None = None

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_model.strip())

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def analysis_ttl_seconds(self) -> float:
        return self.analysis_ttl_minutes * 60
