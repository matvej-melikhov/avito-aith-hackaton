from __future__ import annotations

from datetime import UTC, datetime

import pytest

from review_platform.application.foundation_runtime import (
    BoundaryViolation,
    FoundationRuntime,
)

pytestmark = pytest.mark.behavioral

ORG = "00000000-0000-7000-8000-000000000001"
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_command_receipt_replays_one_logical_result_and_rejects_payload_conflict(
    foundation_runtime: FoundationRuntime,
) -> None:
    first = await foundation_runtime.reserve_command(
        organization_id=ORG,
        idempotency_key="stable-idempotency-key",
        request_id="00000000-0000-7000-8000-000000000010",
        command_name="archive_course",
        target_id="00000000-0000-7000-8000-000000000011",
        expected_revision=1,
        payload={"reason": "complete"},
    )
    replay = await foundation_runtime.reserve_command(
        organization_id=ORG,
        idempotency_key="stable-idempotency-key",
        request_id="00000000-0000-7000-8000-000000000010",
        command_name="archive_course",
        target_id="00000000-0000-7000-8000-000000000011",
        expected_revision=1,
        payload={"reason": "complete"},
    )
    assert replay["receipt_id"] == first["receipt_id"]
    assert replay["result_reference"] == first["result_reference"]

    with pytest.raises(BoundaryViolation, match="idempotency.*payload"):
        await foundation_runtime.reserve_command(
            organization_id=ORG,
            idempotency_key="stable-idempotency-key",
            request_id="00000000-0000-7000-8000-000000000010",
            command_name="archive_course",
            target_id="00000000-0000-7000-8000-000000000011",
            expected_revision=1,
            payload={"reason": "different"},
        )


@pytest.mark.anyio
async def test_operation_exposes_kind_input_version_and_ordered_attempt_history(
    foundation_runtime: FoundationRuntime,
) -> None:
    operation = await foundation_runtime.create_operation(
        organization_id=ORG, kind="course_import", input_version="stepik:course:1"
    )
    operation = await foundation_runtime.append_operation_attempt(
        organization_id=ORG,
        operation_id=operation.operation_id,
        outcome="retryable_failed",
        error={"code": "timeout", "message": "provider timed out", "action": "retry"},
    )
    operation = await foundation_runtime.append_operation_attempt(
        organization_id=ORG,
        operation_id=operation.operation_id,
        outcome="succeeded",
    )

    assert operation.kind == "course_import"
    assert operation.input_version == "stepik:course:1"
    assert [attempt["attempt_number"] for attempt in operation.attempts] == [1, 2]


@pytest.mark.anyio
async def test_terminal_failure_stays_visible_and_cannot_regress(
    foundation_runtime: FoundationRuntime,
) -> None:
    operation = await foundation_runtime.create_operation(
        organization_id=ORG, kind="artifact_capture", input_version="artifact-reference:1"
    )
    terminal = await foundation_runtime.transition_operation(
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
        await foundation_runtime.transition_operation(
            organization_id=ORG, operation_id=operation.operation_id, state="running"
        )


@pytest.mark.anyio
async def test_outbox_lease_exposes_owner_token_expiry_and_attempt_budget(
    foundation_runtime: FoundationRuntime,
) -> None:
    await foundation_runtime.enqueue_outbox(
        organization_id=ORG,
        message_id="00000000-0000-7000-8000-000000000010",
        payload={"event_type": "CourseImportRequested"},
        max_attempts=5,
    )
    lease = (
        await foundation_runtime.lease_outbox(
            owner="relay-a", now=NOW, limit=1, lease_seconds=30
        )
    )[0]

    assert lease.organization_id == ORG
    assert lease.owner == "relay-a"
    assert lease.token
    assert lease.expires_at > NOW
