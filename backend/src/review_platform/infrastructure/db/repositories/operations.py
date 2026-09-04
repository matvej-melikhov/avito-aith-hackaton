"""Tenant-scoped repositories for Foundation persistence.

Repository methods deliberately require ``organization_id`` even where a UUID
is globally unique.  This makes an omitted tenant predicate impossible at the
public persistence boundary and avoids a dangerous global-ID fallback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    CommandReceipt,
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope


class OperationRepositoryFactory:
    """Open short caller-visible transactions around operation repositories."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[OperationRepository]:
        async with session_scope(self._session_factory) as session:
            yield OperationRepository(session)


class CommandReceiptRepository:
    """Persistence primitives for tenant-local command idempotency."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, receipt: CommandReceipt) -> CommandReceipt:
        self._session.add(receipt)
        await self._session.flush()
        return receipt

    async def get(
        self,
        organization_id: UUID,
        receipt_id: UUID,
        *,
        for_update: bool = False,
    ) -> CommandReceipt | None:
        statement = select(CommandReceipt).where(
            CommandReceipt.organization_id == organization_id,
            CommandReceipt.id == receipt_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self,
        organization_id: UUID,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> CommandReceipt | None:
        statement = select(CommandReceipt).where(
            CommandReceipt.organization_id == organization_id,
            CommandReceipt.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def compare_and_set_status(
        self,
        organization_id: UUID,
        receipt_id: UUID,
        *,
        expected_status: str,
        new_status: str,
        result_reference: Mapping[str, Any] | None = None,
    ) -> bool:
        values: dict[str, Any] = {"status": new_status}
        if result_reference is not None:
            values["result_reference"] = dict(result_reference)
        result = await self._session.execute(
            update(CommandReceipt)
            .where(
                CommandReceipt.organization_id == organization_id,
                CommandReceipt.id == receipt_id,
                CommandReceipt.status == expected_status,
            )
            .values(**values)
        )
        return result.rowcount == 1


class OperationRepository:
    """Tenant-scoped Operation and ordered-attempt persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def flush(self) -> None:
        await self._session.flush()

    async def refresh_attempts(self, operation: Operation) -> None:
        await self._session.refresh(
            operation,
            attribute_names=[
                "attempts",
                "created_at",
                "updated_at",
                "finished_at",
                "state",
                "revision",
                "error_code",
                "sanitized_error",
            ],
        )

    async def add(self, operation: Operation) -> Operation:
        self._session.add(operation)
        await self._session.flush()
        return operation

    async def get(
        self,
        organization_id: UUID,
        operation_id: UUID,
        *,
        for_update: bool = False,
        include_attempts: bool = True,
    ) -> Operation | None:
        statement = select(Operation).where(
            Operation.organization_id == organization_id,
            Operation.id == operation_id,
        )
        if include_attempts:
            statement = statement.options(selectinload(Operation.attempts))
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: UUID,
        *,
        limit: int = 100,
    ) -> Sequence[Operation]:
        result = await self._session.execute(
            select(Operation)
            .where(Operation.organization_id == organization_id)
            .options(selectinload(Operation.attempts))
            .order_by(Operation.created_at.desc(), Operation.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def add_attempt(self, attempt: OperationAttempt) -> OperationAttempt:
        """Append an already numbered attempt.

        Callers allocate the next number while holding the parent Operation
        lock; the unique tenant/operation/number constraint is the final race
        guard.
        """

        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def next_attempt_number(
        self,
        organization_id: UUID,
        operation_id: UUID,
    ) -> int:
        operation = await self.get(
            organization_id,
            operation_id,
            for_update=True,
            include_attempts=False,
        )
        if operation is None:
            raise LookupError("tenant-scoped operation not found")
        result = await self._session.execute(
            select(func.coalesce(func.max(OperationAttempt.attempt_number), 0)).where(
                OperationAttempt.organization_id == organization_id,
                OperationAttempt.operation_id == operation_id,
            )
        )
        return int(result.scalar_one()) + 1

    async def compare_and_set_state(
        self,
        organization_id: UUID,
        operation_id: UUID,
        *,
        expected_revision: int,
        new_state: str,
        finished_at: datetime | None = None,
        error_code: str | None = None,
        sanitized_error: Mapping[str, Any] | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "state": new_state,
            "revision": expected_revision + 1,
            "finished_at": finished_at,
            "error_code": error_code,
            "sanitized_error": dict(sanitized_error) if sanitized_error is not None else None,
            "updated_at": func.current_timestamp(),
        }
        result = await self._session.execute(
            update(Operation)
            .where(
                Operation.organization_id == organization_id,
                Operation.id == operation_id,
                Operation.revision == expected_revision,
            )
            .values(**values)
        )
        return result.rowcount == 1


class AuditEventRepository:
    """Append-only tenant audit access."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> AuditEvent:
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_entity(
        self,
        organization_id: UUID,
        *,
        entity_type: str,
        entity_id: UUID,
        limit: int = 100,
    ) -> Sequence[AuditEvent]:
        result = await self._session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.entity_type == entity_type,
                AuditEvent.entity_id == entity_id,
            )
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
        )
        return result.scalars().all()


class OutboxMessageRepository:
    """Tenant-safe storage plus lock/CAS primitives for the relay."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: OutboxMessage) -> OutboxMessage:
        self._session.add(message)
        await self._session.flush()
        return message

    async def get(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        for_update: bool = False,
    ) -> OutboxMessage | None:
        statement = select(OutboxMessage).where(
            OutboxMessage.organization_id == organization_id,
            OutboxMessage.message_id == message_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def lock_available(self, *, now: datetime, limit: int) -> Sequence[OutboxMessage]:
        """Lock a relay batch using MySQL ``FOR UPDATE SKIP LOCKED``."""

        result = await self._session.execute(self.available_statement(now=now, limit=limit))
        return result.scalars().all()

    @staticmethod
    def available_statement(*, now: datetime, limit: int) -> Select[tuple[OutboxMessage]]:
        """Build the deterministic lock query, exposed for focused tests."""

        return (
            select(OutboxMessage)
            .where(
                OutboxMessage.completed_at.is_(None),
                OutboxMessage.enqueue_state.in_(("pending", "leased")),
                OutboxMessage.available_at <= now,
                OutboxMessage.attempts < OutboxMessage.max_attempts,
                or_(
                    OutboxMessage.lease_expires_at.is_(None),
                    OutboxMessage.lease_expires_at <= now,
                ),
            )
            .order_by(OutboxMessage.available_at, OutboxMessage.message_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def lease_available(
        self,
        *,
        now: datetime,
        owner: str,
        lease_seconds: int,
        limit: int,
        token_factory: Callable[[], UUID],
    ) -> Sequence[OutboxMessage]:
        await self.recover_expired_exhausted(now=now)
        messages = await self.lock_available(now=now, limit=limit)
        for message in messages:
            message.lease_owner = owner
            message.lease_token = token_factory()
            message.lease_expires_at = now + timedelta(seconds=lease_seconds)
            message.enqueue_state = "leased"
            message.attempts += 1
        await self._session.flush()
        return messages

    async def recover_expired_exhausted(self, *, now: datetime) -> int:
        """Make a crashed final lease operator-visible after its expiry.

        The predicates are the compare-and-set guard: a completion that wins
        before this statement changes the state/token and is not overwritten.
        Concurrent relays may execute the recovery safely; only one updates a
        particular expired row.
        """

        result = await self._session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.enqueue_state == "leased",
                OutboxMessage.completed_at.is_(None),
                OutboxMessage.lease_expires_at.is_not(None),
                OutboxMessage.lease_expires_at <= now,
                OutboxMessage.attempts >= OutboxMessage.max_attempts,
            )
            .values(
                enqueue_state="action_required",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                error_code="outbox_final_lease_expired",
                sanitized_error={
                    "code": "outbox_final_lease_expired",
                    "message": "final outbox lease expired before completion",
                    "action": "inspect_and_requeue",
                },
            )
        )
        return result.rowcount

    async def complete_lease(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        lease_token: UUID,
        completed_at: datetime,
    ) -> bool:
        return await self._lease_cas(
            organization_id,
            message_id,
            lease_token=lease_token,
            values={
                "enqueue_state": "completed",
                "completed_at": completed_at,
                "lease_owner": None,
                "lease_token": None,
                "lease_expires_at": None,
                "error_code": None,
                "sanitized_error": None,
            },
        )

    async def fail_lease(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        lease_token: UUID,
        available_at: datetime,
        exhausted: bool,
        error_code: str,
        sanitized_error: Mapping[str, Any],
    ) -> bool:
        return await self._lease_cas(
            organization_id,
            message_id,
            lease_token=lease_token,
            values={
                "enqueue_state": "action_required" if exhausted else "pending",
                "available_at": available_at,
                "lease_owner": None,
                "lease_token": None,
                "lease_expires_at": None,
                "error_code": error_code,
                "sanitized_error": dict(sanitized_error),
            },
        )

    async def _lease_cas(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        lease_token: UUID,
        values: Mapping[str, Any],
    ) -> bool:
        result = await self._session.execute(
            update(OutboxMessage)
            .where(
                and_(
                    OutboxMessage.organization_id == organization_id,
                    OutboxMessage.message_id == message_id,
                    OutboxMessage.lease_token == lease_token,
                    OutboxMessage.enqueue_state == "leased",
                    OutboxMessage.completed_at.is_(None),
                )
            )
            .values(**dict(values))
        )
        return result.rowcount == 1


AuditRepository = AuditEventRepository
OutboxRepository = OutboxMessageRepository
ReceiptRepository = CommandReceiptRepository

__all__ = [
    "AuditEventRepository",
    "AuditRepository",
    "CommandReceiptRepository",
    "OperationRepository",
    "OperationRepositoryFactory",
    "OutboxMessageRepository",
    "OutboxRepository",
    "ReceiptRepository",
]
