from __future__ import annotations

from datetime import UTC, datetime

import pytest

from review_platform.application.foundation_runtime import (
    BoundaryViolation,
    build_foundation_runtime,
)

pytestmark = pytest.mark.behavioral

ORG = "00000000-0000-7000-8000-000000000001"
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def test_command_receipt_replays_one_logical_result_and_rejects_payload_conflict() -> None:
    runtime = build_foundation_runtime()
    first = runtime.reserve_command(
        organization_id=ORG,
        idempotency_key="stable-idempotency-key",
        payload={"command_name": "archive_course", "target_id": "course-1"},
    )
    replay = runtime.reserve_command(
        organization_id=ORG,
        idempotency_key="stable-idempotency-key",
        payload={"command_name": "archive_course", "target_id": "course-1"},
    )
    assert replay["receipt_id"] == first["receipt_id"]
    assert replay["result_reference"] == first["result_reference"]

    with pytest.raises(BoundaryViolation, match="idempotency.*payload"):
        runtime.reserve_command(
            organization_id=ORG,
            idempotency_key="stable-idempotency-key",
            payload={"command_name": "archive_course", "target_id": "course-2"},
        )


def test_operation_exposes_kind_input_version_and_ordered_attempt_history() -> None:
    runtime = build_foundation_runtime()
    operation = runtime.create_operation(
        organization_id=ORG, kind="course_import", input_version="stepik:course:1"
    )
    operation = runtime.append_operation_attempt(
        organization_id=ORG,
        operation_id=operation.operation_id,
        outcome="retryable_failed",
        error={"code": "timeout", "message": "provider timed out", "action": "retry"},
    )
    operation = runtime.append_operation_attempt(
        organization_id=ORG,
        operation_id=operation.operation_id,
        outcome="succeeded",
    )

    assert operation.kind == "course_import"
    assert operation.input_version == "stepik:course:1"
    assert [attempt["attempt_number"] for attempt in operation.attempts] == [1, 2]


def test_terminal_failure_stays_visible_and_cannot_regress() -> None:
    runtime = build_foundation_runtime()
    operation = runtime.create_operation(
        organization_id=ORG, kind="artifact_capture", input_version="artifact-reference:1"
    )
    terminal = runtime.transition_operation(
        organization_id=ORG,
        operation_id=operation.operation_id,
        state="action_required",
        error={"code": "access_denied", "message": "grant access", "action": "grant_access"},
    )
    assert terminal.error == {
        "code": "access_denied",
        "message": "grant access",
        "action": "grant_access",
    }

    with pytest.raises(BoundaryViolation, match="terminal"):
        runtime.transition_operation(
            organization_id=ORG, operation_id=operation.operation_id, state="running"
        )


@pytest.mark.anyio
async def test_outbox_lease_exposes_owner_token_expiry_and_attempt_budget() -> None:
    runtime = build_foundation_runtime()
    await runtime.enqueue_outbox(
        organization_id=ORG,
        message_id="00000000-0000-7000-8000-000000000010",
        payload={"event_type": "CourseImportRequested"},
        max_attempts=5,
    )
    lease = (await runtime.lease_outbox(owner="relay-a", now=NOW, limit=1, lease_seconds=30))[0]

    assert lease.organization_id == ORG
    assert lease.owner == "relay-a"
    assert lease.token
    assert lease.expires_at > NOW
