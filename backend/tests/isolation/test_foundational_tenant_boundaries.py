from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import (
    BoundaryViolation,
    FoundationRuntime,
)
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    CommandReceipt,
    Operation,
    OperationAttempt,
    OutboxMessage,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.anyio]

ORG_A = "00000000-0000-7000-8000-000000000001"
ORG_B = "00000000-0000-7000-8000-000000000002"
ROW = "00000000-0000-7000-8000-000000000003"


@pytest.mark.parametrize(
    ("model", "identity_column"),
    [
        (CommandReceipt, "id"),
        (Operation, "id"),
        (AuditEvent, "id"),
        (OutboxMessage, "message_id"),
    ],
)
def test_every_foundational_row_is_organization_scoped(
    model: type[object], identity_column: str
) -> None:
    assert "organization_id" in model.__table__.c  # type: ignore[attr-defined]
    tenant_keys = {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints  # type: ignore[attr-defined]
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("organization_id", identity_column) in tenant_keys


@pytest.mark.anyio
async def test_composite_foreign_key_rejects_cross_tenant_reference(
    foundation_session: AsyncSession,
) -> None:
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    operation = Operation(
        id=UUID(ROW),
        organization_id=UUID(ORG_A),
        kind="course_import",
        input_version="fixture",
        state="pending",
        revision=0,
        created_at=now,
        updated_at=now,
    )
    foundation_session.add(operation)
    await foundation_session.flush()
    foundation_session.add(
        OperationAttempt(
            id=UUID("00000000-0000-7000-8000-000000000004"),
            organization_id=UUID(ORG_B),
            operation_id=UUID(ROW),
            attempt_number=1,
            worker_identity="fixture",
            started_at=now,
            outcome="processing",
        )
    )
    with pytest.raises(IntegrityError):
        await foundation_session.flush()


async def test_redis_namespace_starts_with_the_organization_id(
    foundation_runtime: FoundationRuntime,
) -> None:
    redis_key = foundation_runtime.redis_key(
        organization_id=ORG_A, category="operation", identity=ROW
    )

    assert redis_key.startswith(f"review-platform:{ORG_A}:")
    assert ORG_B not in redis_key


async def test_s3_namespace_starts_with_organization_and_artifact_version(
    foundation_runtime: FoundationRuntime,
) -> None:
    s3_key = foundation_runtime.s3_key(organization_id=ORG_A, artifact_version_id=ROW)

    assert s3_key.startswith(f"{ORG_A}/{ROW}/")
    assert ORG_B not in s3_key


async def test_signed_artifact_url_requires_the_requesting_tenant_to_match(
    foundation_runtime: FoundationRuntime,
) -> None:
    with pytest.raises(BoundaryViolation, match="organization"):
        foundation_runtime.sign_artifact_read(
            organization_id=ORG_A,
            artifact_version_id=ROW,
            requested_by_organization_id=ORG_B,
        )


@pytest.mark.anyio
async def test_generic_api_read_never_falls_back_to_global_id_lookup(
    foundation_runtime: FoundationRuntime,
) -> None:
    created = await foundation_runtime.create_operation(
        organization_id=ORG_A,
        kind="course_import",
        input_version="fixture",
    )
    assert await foundation_runtime.get_operation(
        organization_id=ORG_B,
        operation_id=created.operation_id,
    ) is None
