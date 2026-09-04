from __future__ import annotations

from datetime import UTC, datetime, timedelta

import anyio
import pytest

from review_platform.application.foundation_runtime import (
    BoundaryViolation,
    FoundationRuntime,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.anyio]

ORG = "00000000-0000-7000-8000-000000000001"
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


async def _enqueue(runtime: FoundationRuntime) -> None:
    for number in range(4):
        await runtime.enqueue_outbox(
            organization_id=ORG,
            message_id=f"00000000-0000-7000-8000-{number + 1:012d}",
            payload={"number": number},
            max_attempts=2,
        )


async def test_two_relays_claim_disjoint_batches_with_skip_locked(
    foundation_runtime: FoundationRuntime,
) -> None:
    runtime = foundation_runtime
    await _enqueue(runtime)
    claims: dict[str, set[str]] = {}

    async def claim(owner: str) -> None:
        leases = await runtime.lease_outbox(owner=owner, now=NOW, limit=2, lease_seconds=30)
        claims[owner] = {lease.message_id for lease in leases}

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(claim, "relay-a")
        task_group.start_soon(claim, "relay-b")

    assert len(claims["relay-a"]) == len(claims["relay-b"]) == 2
    assert claims["relay-a"].isdisjoint(claims["relay-b"])


async def test_expired_lease_is_recoverable_with_a_new_token(
    foundation_runtime: FoundationRuntime,
) -> None:
    runtime = foundation_runtime
    await _enqueue(runtime)
    first = (await runtime.lease_outbox(owner="relay-a", now=NOW, limit=1, lease_seconds=30))[0]
    recovered = (
        await runtime.lease_outbox(
            owner="relay-b", now=NOW + timedelta(seconds=31), limit=1, lease_seconds=30
        )
    )[0]

    assert recovered.message_id == first.message_id
    assert recovered.owner == "relay-b"
    assert recovered.token != first.token


async def test_stale_or_duplicate_lease_completion_is_rejected(
    foundation_runtime: FoundationRuntime,
) -> None:
    runtime = foundation_runtime
    await _enqueue(runtime)
    lease = (await runtime.lease_outbox(owner="relay-a", now=NOW, limit=1, lease_seconds=30))[0]
    await runtime.complete_outbox(lease=lease)

    with pytest.raises(BoundaryViolation, match="lease token"):
        await runtime.complete_outbox(lease=lease)


async def test_exhausted_message_remains_actionable_instead_of_succeeding_silently(
    foundation_runtime: FoundationRuntime,
) -> None:
    runtime = foundation_runtime
    await runtime.enqueue_outbox(
        organization_id=ORG,
        message_id="00000000-0000-7000-8000-000000000001",
        payload={"number": 1},
        max_attempts=2,
    )
    view = None
    for attempt in range(2):
        lease = (
            await runtime.lease_outbox(
                owner=f"relay-{attempt}",
                now=NOW + timedelta(minutes=attempt),
                limit=1,
                lease_seconds=30,
            )
        )[0]
        view = await runtime.fail_outbox(
            lease=lease,
            error={"code": "poison", "message": "cannot deserialize", "action": "inspect"},
            now=NOW + timedelta(minutes=attempt),
        )

    assert view is not None
    assert view["state"] == "action_required"
    assert view["attempts"] == view["max_attempts"] == 2
    assert view["error"]["action"] == "inspect"
