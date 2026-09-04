"""Environment-backed configuration with non-live defaults."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings.

    Credentials intentionally have no defaults.  The default configuration can
    collect and run offline tests without contacting a database or provider.
    """

    model_config = SettingsConfigDict(
        env_prefix="REVIEW_PLATFORM_",
        env_file=None,
        extra="forbid",
        frozen=True,
    )

    environment: str = "test"
    contract_version: str = "1.1.0"
    live_providers_enabled: bool = False
    database_url: str | None = None
    redis_url: str | None = None
    s3_endpoint_url: str | None = None
    s3_bucket: str = "review-platform-offline"
    credential_encryption_key_ref: str | None = None
    provider_timeout_seconds: int = Field(default=15, ge=1, le=60)
    provider_max_attempts: int = Field(default=5, ge=1, le=10)
    retry_initial_seconds: int = Field(default=60, ge=1, le=86_400)
    retry_max_seconds: int = Field(default=3_600, ge=1, le=86_400)
    command_body_limit_bytes: int = Field(default=1_048_576, ge=1, le=5_242_880)
    course_import_concurrency: int = Field(default=2, ge=1, le=10)
    ai_review_concurrency: int = Field(default=10, ge=1, le=50)
    delivery_concurrency: int = Field(default=20, ge=1, le=100)

    github_max_files: int = Field(default=10_000, ge=1, le=50_000)
    single_blob_max_bytes: int = Field(default=10_485_760, ge=1, le=52_428_800)
    artifact_total_max_bytes: int = Field(default=104_857_600, ge=1, le=524_288_000)
    archive_max_bytes: int = Field(default=104_857_600, ge=1, le=524_288_000)
    unpacked_max_bytes: int = Field(default=262_144_000, ge=1, le=1_073_741_824)
    google_docx_max_bytes: int = Field(default=10_000_000, ge=1, le=10_000_000)
    ai_signed_url_ttl_seconds: int = Field(default=900, ge=1, le=3_600)

    artifact_bytes_retention_days: int = Field(default=90, ge=1)
    history_retention_days: int = Field(default=365, ge=1)
    operation_retention_days: int = Field(default=90, ge=1)
    log_retention_days: int = Field(default=30, ge=1)

    stepik_sandbox_enabled: bool = False
    github_sandbox_enabled: bool = False
    google_docs_sandbox_enabled: bool = False
    email_sandbox_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return one immutable settings snapshot per process."""

    return Settings()
