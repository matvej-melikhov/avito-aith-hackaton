"""MySQL acceptance tests for Foundation SQL adapters and lease recovery."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import anyio
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEvent as ApplicationAuditEvent
from review_platform.application.command_bus import RevisionConflict
from review_platform.application.idempotency import IdempotencyReceipt
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
    SqlRevisionStore,
    SqlTransactionManager,
)
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AuditEvent,
    CommandReceipt,
    Operation,
    OperationAttempt,
    Organization,
    OutboxMessage,
)
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
FOUNDATION_TABLES = [
    Organization.__table__,
    CommandReceipt.__table__,
    AuditEvent.__table__,
    Operation.__table__,
    OperationAttempt.__table__,
    OutboxMessage.__table__,
]


async def _prepare(mysql_container: object) -> tuple[object, AsyncSessionFactory]:
    connection_url = cast(str, mysql_container.get_connection_url())
    engine = create_database_engine(connection_url)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=FOUNDATION_TABLES,
            )
        )
    return engine, create_session_factory(engine)


async def _clean_organization(factory: AsyncSessionFactory, organization_id: UUID) -> None:
    async with session_scope(factory) as session:
        for model in (OperationAttempt, OutboxMessage, AuditEvent, CommandReceipt, Operation):
            await session.execute(delete(model).where(model.organization_id == organization_id))
        await session.execute(delete(Organization).where(Organization.id == organization_id))


def _receipt(*, receipt_id: UUID, organization_id: UUID) -> IdempotencyReceipt:
    return IdempotencyReceipt(
        receipt_id=receipt_id,
        organization_id=organization_id,
        idempotency_key="foundation-race-key-0001",
        request_id=UUID("00000000-0000-7000-8000-000000000113"),
        command_name="archive_course",
        target_id=UUID("00000000-0000-7000-8000-000000000114"),
        expected_revision=0,
        payload_digest="sha256:" + "a" * 64,
        result_reference={"operation_id": "00000000-0000-7000-8000-000000000115"},
    )


async def test_sql_adapters_reserve_race_append_audit_and_tenant_cas(
    mysql_container: object,
) -> None:
    organization_id = UUID("00000000-0000-7000-8000-000000000101")
    other_organization_id = UUID("00000000-0000-7000-8000-000000000102")
    operation_id = UUID("00000000-0000-7000-8000-000000000103")
    event_id = UUID("00000000-0000-7000-8000-000000000104")
    engine, factory = await _prepare(mysql_container)
    try:
        async with session_scope(factory) as session:
            session.add_all(
                [
                    Organization(id=organization_id, slug="adapter-org", name="Adapter Org"),
                    Organization(
                        id=other_organization_id,
                        slug="adapter-other-org",
                        name="Adapter Other Org",
                    ),
                ]
            )
            await session.flush()
            session.add(
                Operation(
                    id=operation_id,
                    organization_id=organization_id,
                    kind="course_import",
                    input_version="fixture:1",
                    state="pending",
                )
            )

        proposed = {
            "first": _receipt(
                receipt_id=UUID("00000000-0000-7000-8000-000000000111"),
                organization_id=organization_id,
            ),
            "second": _receipt(
                receipt_id=UUID("00000000-0000-7000-8000-000000000112"),
                organization_id=organization_id,
            ),
        }
        reservations: dict[str, tuple[IdempotencyReceipt, bool]] = {}

        async def reserve(name: str) -> None:
            async with session_scope(factory) as session:
                reservations[name] = await SqlIdempotencyReceiptRepository().reserve(
                    proposed[name],
                    transaction=session,
                )

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(reserve, "first")
            task_group.start_soon(reserve, "second")

        assert sorted(created for _, created in reservations.values()) == [False, True]
        assert len({receipt.receipt_id for receipt, _ in reservations.values()}) == 1

        rolled_back_receipt = replace(
            proposed["first"],
            receipt_id=UUID("00000000-0000-7000-8000-000000000118"),
            idempotency_key="foundation-rollback-key-0001",
        )
        async with factory() as session:
            transaction = await session.begin()
            _, created = await SqlIdempotencyReceiptRepository().reserve(
                rolled_back_receipt,
                transaction=session,
            )
            assert created
            await transaction.rollback()

        transaction_manager = SqlTransactionManager(factory)
        async with transaction_manager.begin() as transaction:
            session = cast(AsyncSession, transaction)
            revisions = SqlRevisionStore()
            await revisions.lock_and_check(
                organization_id=organization_id,
                revision_target="organization",
                target_id=organization_id,
                expected_revision=0,
                transaction=session,
            )
            assert await revisions.compare_and_set(
                organization_id=organization_id,
                revision_target="organization",
                target_id=organization_id,
                expected_revision=0,
                values={"name": "Adapter Org Updated"},
                transaction=session,
            )
            await revisions.lock_and_check(
                organization_id=organization_id,
                revision_target="operation",
                target_id=operation_id,
                expected_revision=0,
                transaction=session,
            )
            assert await revisions.compare_and_set(
                organization_id=organization_id,
                revision_target="operation",
                target_id=operation_id,
                expected_revision=0,
                values={"state": "processing"},
                transaction=session,
            )
            await SqlAppendOnlyAuditRepository().append(
                ApplicationAuditEvent(
                    event_id=event_id,
                    organization_id=organization_id,
                    actor_type="installation_operator",
                    actor_user_id=None,
                    installation_operator_id="local-operator",
                    agent_id=None,
                    agent_authorization_id=None,
                    action="foundation_cas_verified",
                    entity_type="operation",
                    entity_id=operation_id,
                    before_revision=0,
                    after_revision=1,
                    request_id=UUID("00000000-0000-7000-8000-000000000116"),
                    trace_id=UUID("00000000-0000-7000-8000-000000000117"),
                    outcome="succeeded",
                    sanitized_details={"code": "verified"},
                    occurred_at=NOW,
                ),
                transaction=session,
            )

        rolled_back_event_id = UUID("00000000-0000-7000-8000-000000000119")
        async with factory() as session:
            transaction = await session.begin()
            await SqlAppendOnlyAuditRepository().append(
                ApplicationAuditEvent(
                    event_id=rolled_back_event_id,
                    organization_id=organization_id,
                    actor_type="installation_operator",
                    actor_user_id=None,
                    installation_operator_id="local-operator",
                    agent_id=None,
                    agent_authorization_id=None,
                    action="must_rollback",
                    entity_type="operation",
                    entity_id=operation_id,
                    before_revision=1,
                    after_revision=1,
                    request_id=UUID("00000000-0000-7000-8000-000000000120"),
                    trace_id=UUID("00000000-0000-7000-8000-000000000121"),
                    outcome="succeeded",
                    sanitized_details={"code": "rollback"},
                    occurred_at=NOW,
                ),
                transaction=session,
            )
            await transaction.rollback()

        async with session_scope(factory) as session:
            with pytest.raises(RevisionConflict, match="not found"):
                await SqlRevisionStore().lock_and_check(
                    organization_id=other_organization_id,
                    revision_target="operation",
                    target_id=operation_id,
                    expected_revision=1,
                    transaction=session,
                )
            assert await session.scalar(
                select(func.count()).select_from(CommandReceipt).where(
                    CommandReceipt.organization_id == organization_id
                )
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.id == event_id)
            ) == 1
            assert await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.id == rolled_back_event_id)
            ) == 0
            assert await session.scalar(
                select(func.count())
                .select_from(CommandReceipt)
                .where(CommandReceipt.idempotency_key == "foundation-rollback-key-0001")
            ) == 0
            operation = await session.scalar(
                select(Operation).where(
                    Operation.organization_id == organization_id,
                    Operation.id == operation_id,
                )
            )
            assert operation is not None
            assert (operation.state, operation.revision) == ("processing", 1)
    finally:
        await _clean_organization(factory, organization_id)
        await _clean_organization(factory, other_organization_id)
        await engine.dispose()


async def test_expired_final_outbox_lease_becomes_actionable(mysql_container: object) -> None:
    organization_id = UUID("00000000-0000-7000-8000-000000000201")
    message_id = UUID("00000000-0000-7000-8000-000000000202")
    lease_token = UUID("00000000-0000-7000-8000-000000000203")
    engine, factory = await _prepare(mysql_container)
    try:
        async with session_scope(factory) as session:
            session.add(Organization(id=organization_id, slug="lease-org", name="Lease Org"))
            await session.flush()
            await OutboxService(
                OutboxMessageRepository(session),
                token_factory=lambda: lease_token,
                clock=lambda: NOW,
            ).create(
                OutboxDraft(
                    organization_id=organization_id,
                    message_id=message_id,
                    aggregate_type="course_import",
                    aggregate_id=UUID("00000000-0000-7000-8000-000000000204"),
                    event_type="CourseImportRequested",
                    payload_version="1.1.0",
                    payload={"safe": True},
                    available_at=NOW,
                    max_attempts=1,
                )
            )

        # Claim the only allowed attempt and commit the lease, then simulate a
        # relay crash by deliberately doing nothing with the returned token.
        async with session_scope(factory) as session:
            leases = await OutboxService(
                OutboxMessageRepository(session),
                token_factory=lambda: lease_token,
                clock=lambda: NOW,
            ).lease(owner="relay-crashed", limit=1, lease_seconds=30)
            assert len(leases) == 1
            assert leases[0].attempts == leases[0].max_attempts == 1

        after_expiry = NOW + timedelta(seconds=31)
        async with session_scope(factory) as session:
            recovered_claims = await OutboxService(
                OutboxMessageRepository(session),
                clock=lambda: after_expiry,
            ).lease(owner="relay-recovery", limit=1, lease_seconds=30)
            assert recovered_claims == ()

        async with session_scope(factory) as session:
            row = await OutboxMessageRepository(session).get(organization_id, message_id)
            assert row is not None
            assert row.enqueue_state == "action_required"
            assert row.attempts == row.max_attempts == 1
            assert row.lease_owner is row.lease_token is row.lease_expires_at is None
            assert row.error_code == "outbox_final_lease_expired"
            assert row.sanitized_error == {
                "code": "outbox_final_lease_expired",
                "message": "final outbox lease expired before completion",
                "action": "inspect_and_requeue",
            }
    finally:
        await _clean_organization(factory, organization_id)
        await engine.dispose()
