from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import RFC_4122

import pytest

from review_platform.domain.primitives import (
    RevisionConflict,
    UUID7Generator,
    canonical_json_sha256,
    require_utc,
    sanitize_error,
    sha256_digest,
    validate_digest,
    verify_and_increment_revision,
)


def test_uuid7_is_deterministic_valid_and_monotonic_with_an_injected_clock() -> None:
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    generator = UUID7Generator(clock=lambda: now, random_bytes=lambda size: b"\x00" * size)

    first = generator()
    second = generator()

    assert first.version == second.version == 7
    assert first.variant == second.variant == RFC_4122
    assert first.int < second.int
    assert first.int >> 80 == int(now.timestamp() * 1000)


def test_utc_primitives_accept_only_aware_zero_offset_values() -> None:
    value = datetime(2026, 9, 4, 12, 0, tzinfo=timezone(timedelta(0)))
    assert require_utc(value).tzinfo is UTC
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        require_utc(value.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        require_utc(datetime(2026, 9, 4, 15, 0, tzinfo=timezone(timedelta(hours=3))))


def test_sha256_helpers_use_prefixed_lowercase_digest_and_rfc8785_json() -> None:
    assert sha256_digest(b"abc") == (
        "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    left = canonical_json_sha256({"b": 2, "a": 1})
    right = canonical_json_sha256({"a": 1, "b": 2})
    assert left == right
    assert validate_digest(left) == left
    with pytest.raises(ValueError):
        validate_digest("sha256:ABC")


def test_revision_helper_is_compare_and_swap() -> None:
    assert verify_and_increment_revision(current=4, expected=4) == 5
    with pytest.raises(RevisionConflict) as error:
        verify_and_increment_revision(current=4, expected=3)
    assert (error.value.expected, error.value.actual) == (3, 4)


def test_sanitized_error_preserves_actionable_fields_and_is_bounded() -> None:
    secret = "live-secret-token-value"
    sanitized = sanitize_error(
        {
            "code": "provider_unavailable",
            "action": "retry_later",
            "message": "Bearer " + secret + " " + ("ы" * 10_000),
            "raw_body": secret,
            "provider_response": {"body": secret, "safe_code": "timeout"},
        }
    )
    encoded = json.dumps(sanitized, ensure_ascii=False, sort_keys=True).encode("utf-8")

    assert sanitized["code"] == "provider_unavailable"
    assert sanitized["action"] == "retry_later"
    assert secret not in encoded.decode("utf-8")
    assert "raw_body" not in sanitized
    assert len(encoded) <= 2048
