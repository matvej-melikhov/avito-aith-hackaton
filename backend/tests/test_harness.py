from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from review_platform import __version__
from review_platform.settings import Settings


def test_package_version_is_importable() -> None:
    assert __version__ == "0.1.0"


def test_settings_are_offline_and_secret_free_by_default() -> None:
    settings = Settings()

    assert settings.environment == "test"
    assert settings.live_providers_enabled is False
    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.credential_encryption_key_ref is None


def test_clock_and_uuid_fixtures_are_deterministic(
    fixed_clock: Callable[[], datetime], uuid7_factory: Callable[[], UUID]
) -> None:
    assert fixed_clock() == datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    assert uuid7_factory() == UUID("00000000-0000-7000-8000-000000000001")
    assert uuid7_factory() == UUID("00000000-0000-7000-8000-000000000002")
