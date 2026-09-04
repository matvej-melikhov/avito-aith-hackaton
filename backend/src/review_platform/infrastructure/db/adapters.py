"""SQLAlchemy adapters for the Foundation application persistence ports."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import TableClause

from review_platform.application.audit import (
    AppendOnlyAuditRepository as AppendOnlyAuditRepositoryProtocol,
)
from review_platform.application.audit import AuditEvent as ApplicationAuditEvent
from review_platform.application.command_bus import (
    RevisionConflict,
    RevisionStore,
    TransactionContext,
    TransactionManager,
)
from review_platform.application.idempotency import (
    IdempotencyReceipt,
    IdempotencyReceiptRepository,
    StableResultReference,
)
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    CommandReceipt,
    Operation,
)
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.repositories.operations import (
    AuditEventRepository,
    CommandReceiptRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope


def _require_session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise TypeError("SQL persistence transaction must be an AsyncSession")
    return transaction


class SqlTransactionManager:
    """Open committing application transactions from an async session factory."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    def begin(self) -> TransactionContext:
        context: AbstractAsyncContextManager[AsyncSession] = session_scope(self._session_factory)
        return cast(TransactionContext, context)


class SqlIdempotencyReceiptRepository:
    """Atomically insert or return the tenant's existing command receipt."""

    async def reserve(
        self,
        proposed: IdempotencyReceipt,
        *,
        transaction: object,
    ) -> tuple[IdempotencyReceipt, bool]:
        session = _require_session(transaction)
        repository = CommandReceiptRepository(session)
        existing = await repository.get_by_idempotency_key(
            proposed.organization_id,
            proposed.idempotency_key,
        )
        if existing is not None:
            return self._to_application(existing), False

        row = CommandReceipt(
            id=proposed.receipt_id,
            organization_id=proposed.organization_id,
            idempotency_key=proposed.idempotency_key,
            request_id=proposed.request_id,
            command_name=proposed.command_name,
            target_id=proposed.target_id,
            expected_revision=proposed.expected_revision,
            payload_digest=proposed.payload_digest,
            actor_snapshot={},
            status="reserved",
            result_reference=dict(proposed.result_reference),
        )
        try:
            # A savepoint contains the expected duplicate-key error without
            # rolling back the caller's domain transaction.
            async with session.begin_nested():
                session.add(row)
                await session.flush([row])
        except IntegrityError:
            # SELECT FOR UPDATE is a current read under InnoDB REPEATABLE READ,
            # so it observes the concurrent winner after the unique-key wait.
            existing = await repository.get_by_idempotency_key(
                proposed.organization_id,
                proposed.idempotency_key,
                for_update=True,
            )
            if existing is None:
                raise
            return self._to_application(existing), False
        return proposed, True

    @staticmethod
    def _to_application(row: CommandReceipt) -> IdempotencyReceipt:
        result_reference = cast(
            StableResultReference,
            dict(row.result_reference) if row.result_reference is not None else {},
        )
        return IdempotencyReceipt(
            receipt_id=row.id,
            organization_id=row.organization_id,
            idempotency_key=row.idempotency_key,
            request_id=row.request_id,
            command_name=row.command_name,
            target_id=row.target_id,
            expected_revision=row.expected_revision,
            payload_digest=row.payload_digest,
            result_reference=result_reference,
        )


class SqlAppendOnlyAuditRepository:
    """Map an application audit DTO to one append-only ORM row."""

    async def append(
        self,
        event: ApplicationAuditEvent,
        *,
        transaction: object,
    ) -> None:
        session = _require_session(transaction)
        row = AuditEvent(
            id=event.event_id,
            organization_id=event.organization_id,
            actor_type=event.actor_type,
            actor_user_id=event.actor_user_id,
            installation_operator_id=event.installation_operator_id,
            agent_id=event.agent_id,
            agent_authorization_id=event.agent_authorization_id,
            action=event.action,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            before_revision=event.before_revision,
            after_revision=event.after_revision,
            request_id=event.request_id,
            trace_id=event.trace_id,
            outcome=event.outcome,
            sanitized_details=dict(event.sanitized_details),
            occurred_at=event.occurred_at,
        )
        await AuditEventRepository(session).append(row)


@dataclass(frozen=True, slots=True)
class RevisionBinding:
    """A table whose revision is protected by tenant-aware locking and CAS."""

    table: TableClause
    tenant_owned: bool


FOUNDATION_REVISION_BINDINGS: Mapping[str, RevisionBinding] = {
    "organization": RevisionBinding(
        cast(TableClause, Organization.__table__), tenant_owned=False
    ),
    "operation": RevisionBinding(cast(TableClause, Operation.__table__), tenant_owned=True),
}


class SqlRevisionStore:
    """Tenant-aware row locking and compare-and-set for revisioned entities."""

    def __init__(
        self,
        bindings: Mapping[str, RevisionBinding] = FOUNDATION_REVISION_BINDINGS,
    ) -> None:
        self._bindings = dict(bindings)

    async def lock_and_check(
        self,
        *,
        organization_id: UUID,
        revision_target: str,
        target_id: UUID,
        expected_revision: int,
        transaction: object,
    ) -> None:
        session = _require_session(transaction)
        binding = self._binding(revision_target)
        result = await session.execute(
            select(binding.table.c.revision)
            .where(*self._identity_predicates(binding, organization_id, target_id))
            .with_for_update()
        )
        current_revision = result.scalar_one_or_none()
        if current_revision is None:
            raise RevisionConflict("tenant-scoped revision target was not found")
        if current_revision != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, current revision is {current_revision}"
            )

    async def compare_and_set(
        self,
        *,
        organization_id: UUID,
        revision_target: str,
        target_id: UUID,
        expected_revision: int,
        values: Mapping[str, Any],
        transaction: object,
    ) -> bool:
        """Update one exact tenant row and increment its revision atomically."""

        forbidden = {"id", "organization_id", "revision"}.intersection(values)
        if forbidden:
            rendered = ", ".join(sorted(forbidden))
            raise ValueError(f"CAS values cannot replace identity/revision columns: {rendered}")
        session = _require_session(transaction)
        binding = self._binding(revision_target)
        result = await session.execute(
            update(binding.table)
            .where(
                *self._identity_predicates(binding, organization_id, target_id),
                binding.table.c.revision == expected_revision,
            )
            .values(**dict(values), revision=expected_revision + 1)
        )
        return result.rowcount == 1

    def _binding(self, revision_target: str) -> RevisionBinding:
        try:
            return self._bindings[revision_target]
        except KeyError as exc:
            raise RevisionConflict(f"unsupported revision target: {revision_target}") from exc

    @staticmethod
    def _identity_predicates(
        binding: RevisionBinding,
        organization_id: UUID,
        target_id: UUID,
    ) -> tuple[Any, ...]:
        if binding.tenant_owned:
            return (
                binding.table.c.organization_id == organization_id,
                binding.table.c.id == target_id,
            )
        return (
            binding.table.c.id == organization_id,
            binding.table.c.id == target_id,
        )


# These assignments are compile-time contract checks under strict mypy.
_idempotency_protocol: IdempotencyReceiptRepository = SqlIdempotencyReceiptRepository()
_audit_protocol: AppendOnlyAuditRepositoryProtocol = SqlAppendOnlyAuditRepository()
_revision_protocol: RevisionStore = SqlRevisionStore()


def _transaction_protocol(session_factory: AsyncSessionFactory) -> TransactionManager:
    return SqlTransactionManager(session_factory)


__all__ = [
    "FOUNDATION_REVISION_BINDINGS",
    "RevisionBinding",
    "SqlAppendOnlyAuditRepository",
    "SqlIdempotencyReceiptRepository",
    "SqlRevisionStore",
    "SqlTransactionManager",
]
