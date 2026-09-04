from __future__ import annotations

import json

import pytest

from review_platform.application.foundation_runtime import build_foundation_runtime

pytestmark = pytest.mark.behavioral

ORG = "00000000-0000-7000-8000-000000000001"
SECRET = "live-secret-token-value"
MAGIC_LINK = "https://review.example.test/invite/private-magic-token"


@pytest.mark.parametrize(
    "sink",
    ["log", "api_error", "operation_attempt", "outbox_message", "audit_event"],
)
def test_every_shared_sink_redacts_tokens_links_and_nested_provider_bodies(sink: str) -> None:
    runtime = build_foundation_runtime()
    stored = runtime.write_shared_sink(
        sink=sink,
        organization_id=ORG,
        details={
            "authorization": f"Bearer {SECRET}",
            "magic_link": MAGIC_LINK,
            "provider_response": {"body": SECRET, "safe_code": "provider_unavailable"},
            "safe_action": "reconnect_provider",
        },
    )
    rendered = json.dumps(stored, sort_keys=True)

    assert SECRET not in rendered
    assert "private-magic-token" not in rendered
    assert "provider_unavailable" in rendered
    assert "reconnect_provider" in rendered


def test_sanitized_error_is_bounded_and_preserves_actionable_fields() -> None:
    runtime = build_foundation_runtime()
    stored = runtime.write_shared_sink(
        sink="operation_attempt",
        organization_id=ORG,
        details={
            "code": "provider_unavailable",
            "message": "x" * 10_000,
            "action": "retry_later",
            "raw_body": SECRET,
        },
    )

    assert stored["code"] == "provider_unavailable"
    assert stored["action"] == "retry_later"
    assert len(stored["message"].encode()) <= 2048
    assert "raw_body" not in stored
