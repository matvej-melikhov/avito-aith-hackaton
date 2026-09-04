"""Transactional SQL outbox service with expiring compare-and-set leases."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from review_platform.domain.primitives import require_utc, sanitize_error, utc_now, uuid7
from review_platform.infrastructure.db.models.operations import OutboxMessage
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope


class OutboxError(RuntimeError):
    """Base durable-outbox error."""


class StaleOutboxLease(OutboxError):
    """A completion/failure attempted to use an old or expired lease token."""


class OutboxMessageNotFound(OutboxError):
    """The tenant-scoped outbox message does not exist."""


@dataclass(frozen=True, slots=True)
class OutboxDraft:
    organization_id: UUID
    message_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload_version: str
    payload: Mapping[str, Any]
    available_at: datetime
    max_attempts: int


@dataclass(frozen=True, slots=True)
class OutboxLease:
    organization_id: UUID
    message_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload_version: str
    owner: str
    token: UUID
    expires_at: datetime
    attempts: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class OutboxRecoveryView:
    organization_id: UUID
    message_id: UUID
    state: str
    attempts: int
    max_attempts: int
    available_at: datetime
    error: Mapping[str, Any] | None


class OutboxRepository(Protocol):
    async def add(self, message: OutboxMessage) -> OutboxMessage: ...

    async def get(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        for_update: bool = False,
    ) -> OutboxMessage | None: ...

    async def lease_available(
        self,
        *,
        now: datetime,
        owner: str,
        lease_seconds: int,
        limit: int,
        token_factory: Callable[[], UUID],
    ) -> Sequence[OutboxMessage]: ...

    async def complete_lease(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        lease_token: UUID,
        completed_at: datetime,
    ) -> bool: ...

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
    ) -> bool: ...


class OutboxService:
    """Operate on one caller-owned SQL transaction.

    Constructing this service around ``OutboxMessageRepository(session)`` lets
    domain mutations and message creation commit atomically.  The relay uses
    ``SqlOutboxUnitOfWork`` below to give each claim/CAS step its own short
    transaction.
    """

    def __init__(
        self,
        repository: OutboxRepository,
        *,
        token_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        retry_initial_seconds: int = 60,
        retry_max_seconds: int = 3600,
    ) -> None:
        if retry_initial_seconds < 1 or retry_max_seconds < retry_initial_seconds:
            raise ValueError("outbox retry bounds are invalid")
        self._repository = repository
        self._token_factory = token_factory
        self._clock = clock
        self._retry_initial_seconds = retry_initial_seconds
        self._retry_max_seconds = retry_max_seconds

    async def create(self, draft: OutboxDraft) -> OutboxMessage:
        self._validate_draft(draft)
        message = OutboxMessage(
            organization_id=draft.organization_id,
            message_id=draft.message_id,
            aggregate_type=draft.aggregate_type,
            aggregate_id=draft.aggregate_id,
            event_type=draft.event_type,
            payload_version=draft.payload_version,
            payload=dict(draft.payload),
            available_at=require_utc(draft.available_at),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            enqueue_state="pending",
            completed_at=None,
            attempts=0,
            max_attempts=draft.max_attempts,
            error_code=None,
            sanitized_error=None,
        )
        return await self._repository.add(message)

    async def lease(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> tuple[OutboxLease, ...]:
        if not owner or len(owner) > 255:
            raise ValueError("lease owner must contain 1..255 characters")
        if limit < 1 or limit > 1000:
            raise ValueError("lease batch limit must be between 1 and 1000")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease duration must be between 1 and 3600 seconds")
        claimed_at = require_utc(now or self._clock())
        messages = await self._repository.lease_available(
            now=claimed_at,
            owner=owner,
            lease_seconds=lease_seconds,
            limit=limit,
            token_factory=self._token_factory,
        )
        leases: list[OutboxLease] = []
        seen_tokens: set[UUID] = set()
        for message in messages:
            if message.lease_token is None or message.lease_expires_at is None:
                raise OutboxError("repository returned a message without a complete lease")
            if message.lease_token in seen_tokens:
                raise OutboxError("repository issued a duplicate lease token")
            seen_tokens.add(message.lease_token)
            leases.append(self._lease_view(message))
        return tuple(leases)

    async def complete(
        self,
        lease: OutboxLease,
        *,
        now: datetime | None = None,
    ) -> None:
        completed_at = require_utc(now or self._clock())
        row = await self._current_lease(lease, now=completed_at)
        if row.attempts != lease.attempts:
            raise StaleOutboxLease("outbox lease attempt is stale")
        updated = await self._repository.complete_lease(
            lease.organization_id,
            lease.message_id,
            lease_token=lease.token,
            completed_at=completed_at,
        )
        if not updated:
            raise StaleOutboxLease("outbox lease token is stale")

    async def fail(
        self,
        lease: OutboxLease,
        *,
        error: Mapping[str, Any] | BaseException | str,
        now: datetime | None = None,
    ) -> OutboxRecoveryView:
        failed_at = require_utc(now or self._clock())
        row = await self._current_lease(lease, now=failed_at)
        if row.attempts != lease.attempts:
            raise StaleOutboxLease("outbox lease attempt is stale")
        bounded_error = sanitize_error(error, max_bytes=2048)
        raw_code = bounded_error.get("code")
        error_code = raw_code if isinstance(raw_code, str) else "outbox_publish_failed"
        exhausted = row.attempts >= row.max_attempts
        available_at = failed_at if exhausted else failed_at + self._retry_delay(row.attempts)
        updated = await self._repository.fail_lease(
            lease.organization_id,
            lease.message_id,
            lease_token=lease.token,
            available_at=available_at,
            exhausted=exhausted,
            error_code=error_code,
            sanitized_error=bounded_error,
        )
        if not updated:
            raise StaleOutboxLease("outbox lease token is stale")
        return OutboxRecoveryView(
            organization_id=row.organization_id,
            message_id=row.message_id,
            state="action_required" if exhausted else "pending",
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            available_at=available_at,
            error=bounded_error,
        )

    async def recovery_view(
        self,
        *,
        organization_id: UUID,
        message_id: UUID,
    ) -> OutboxRecoveryView | None:
        """Expose retry budget and sanitized failure without a global-ID fallback."""

        row = await self._repository.get(organization_id, message_id)
        if row is None:
            return None
        return OutboxRecoveryView(
            organization_id=row.organization_id,
            message_id=row.message_id,
            state=row.enqueue_state,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            available_at=_database_utc(row.available_at),
            error=row.sanitized_error,
        )

    async def _current_lease(self, lease: OutboxLease, *, now: datetime) -> OutboxMessage:
        row = await self._repository.get(
            lease.organization_id,
            lease.message_id,
            for_update=True,
        )
        if row is None:
            raise OutboxMessageNotFound("tenant-scoped outbox message not found")
        if (
            row.enqueue_state != "leased"
            or row.lease_token != lease.token
            or row.lease_owner != lease.owner
            or row.lease_expires_at is None
            or _database_utc(row.lease_expires_at) <= now
        ):
            raise StaleOutboxLease("outbox lease token is stale or expired")
        return row

    def _retry_delay(self, attempts: int) -> timedelta:
        exponent = max(0, attempts - 1)
        seconds = min(
            self._retry_initial_seconds * (2**exponent),
            self._retry_max_seconds,
        )
        return timedelta(seconds=seconds)

    @staticmethod
    def _lease_view(message: OutboxMessage) -> OutboxLease:
        assert message.lease_token is not None
        assert message.lease_owner is not None
        assert message.lease_expires_at is not None
        return OutboxLease(
            organization_id=message.organization_id,
            message_id=message.message_id,
            aggregate_type=message.aggregate_type,
            aggregate_id=message.aggregate_id,
            event_type=message.event_type,
            payload_version=message.payload_version,
            owner=message.lease_owner,
            token=message.lease_token,
            expires_at=_database_utc(message.lease_expires_at),
            attempts=message.attempts,
            max_attempts=message.max_attempts,
        )

    @staticmethod
    def _validate_draft(draft: OutboxDraft) -> None:
        require_utc(draft.available_at)
        if draft.max_attempts < 1 or draft.max_attempts > 10:
            raise ValueError("outbox max_attempts must be between 1 and 10")
        for name, value, maximum in (
            ("aggregate_type", draft.aggregate_type, 128),
            ("event_type", draft.event_type, 128),
            ("payload_version", draft.payload_version, 64),
        ):
            if not value or len(value) > maximum:
                raise ValueError(f"{name} must contain 1..{maximum} characters")


class OutboxUnitOfWork(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[OutboxService]: ...


class SqlOutboxUnitOfWork:
    """Create short, committing outbox transactions for relay processes."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        token_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        retry_initial_seconds: int = 60,
        retry_max_seconds: int = 3600,
    ) -> None:
        self._session_factory = session_factory
        self._token_factory = token_factory
        self._clock = clock
        self._retry_initial_seconds = retry_initial_seconds
        self._retry_max_seconds = retry_max_seconds

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[OutboxService]:
        async with session_scope(self._session_factory) as session:
            yield OutboxService(
                OutboxMessageRepository(session),
                token_factory=self._token_factory,
                clock=self._clock,
                retry_initial_seconds=self._retry_initial_seconds,
                retry_max_seconds=self._retry_max_seconds,
            )


def _database_utc(value: datetime) -> datetime:
    """Normalize MySQL DATETIME values, whose driver representation is naive."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else require_utc(value)


__all__ = [
    "OutboxDraft",
    "OutboxError",
    "OutboxLease",
    "OutboxMessageNotFound",
    "OutboxRecoveryView",
    "OutboxRepository",
    "OutboxService",
    "OutboxUnitOfWork",
    "SqlOutboxUnitOfWork",
    "StaleOutboxLease",
]
