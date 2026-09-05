"""Настройки сервиса. Читаются из окружения и из .env в корне репозитория."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parents[1]  # prereview/
REPO_ROOT = PROJECT_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PREREVIEW_",
        env_file=(REPO_ROOT / ".env", PROJECT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Модель за интерфейсом: провайдер и модель меняются конфигом, не кодом.
    llm_provider: Literal["deepseek", "fake"] = "deepseek"
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: int = 120
    deepseek_api_key: SecretStr | None = Field(default=None, alias="DEEPSEEK_API_KEY")

    prompts_dir: Path = REPO_ROOT / "prompts"
    assignments_dir: Path = PROJECT_DIR / "assignments"
    pricing_file: Path = PROJECT_DIR / "pricing.json"
    data_dir: Path = PROJECT_DIR / "data"

    # Bearer, который платформа шлёт в Authorization; пусто = не проверяем.
    token: SecretStr | None = None
    # Какой файл задания применять по умолчанию (slug из assignments/), пусто = подбор по key.
    assignment: str | None = None

    # Бюджеты.
    max_artifact_bytes: int = 10_000_000
    max_file_bytes: int = 200_000
    judge_context_chars: int = 60_000
    judge_repeats: int = 2
    judge_max_tokens: int = 3_000

    # DeepSeek Harness как субагент-исследователь репозиториев.
    harness_enabled: bool = True
    harness_bin: str = "dsh"
    harness_timeout_seconds: int = 240
    harness_max_output_bytes: int = 1_000_000

    # Курс для «₽ за работу». Допущение, уточнить на дату защиты.
    usd_rub: float = 80.0

    @property
    def api_key(self) -> str:
        return self.deepseek_api_key.get_secret_value() if self.deepseek_api_key else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
