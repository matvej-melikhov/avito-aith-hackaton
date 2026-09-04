"""Production composition boundary for the shared backend Foundation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import boto3
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from review_platform.api.middleware import Redactor
from review_platform.application.auth_guards.membership import UserMembershipAuthGuard
from review_platform.application.authorization import Authorizer
from review_platform.application.command_bus import CommandBus
from review_platform.application.idempotency import (
    IdempotencyConflict,
    IdempotencyCoordinator,
)
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import ApplicationCommand, WireCommand
from review_platform.domain.primitives import sanitize_error, utc_now, uuid7
from review_platform.infrastructure.db.adapters import (
    SqlIdempotencyReceiptRepository,
    SqlRevisionStore,
    SqlTransactionManager,
)
from review_platform.infrastructure.db.models.operations import Operation, OperationAttempt
from review_platform.infrastructure.db.outbox import (
    OutboxDraft,
    OutboxLease,
    SqlOutboxUnitOfWork,
    StaleOutboxLease,
)
from review_platform.infrastructure.db.repositories.operations import (
    OperationRepository,
    OperationRepositoryFactory,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import (
    ObjectStorageError,
    S3Client,
    S3ObjectStorage,
)
from review_platform.infrastructure.tasks.broker import MembershipWorkerAuthRevalidator
from review_platform.settings import Settings, get_settings


class BoundaryViolation(ValueError):
    """A request crossed a declared command, actor, state, or tenant boundary."""


class RuntimeConfigurationError(RuntimeError):
    """Required local infrastructure configuration is absent."""


@dataclass(frozen=True, slots=True)
class OperationView:
    operation_id: str
    organization_id: str
    kind: str
    input_version: str
    state: str
    attempts: tuple[Mapping[str, Any], ...]
    error: Mapping[str, Any] | None
    created_at: datetime | str
    updated_at: datetime | str
    finished_at: datetime | str | None


@dataclass(frozen=True, slots=True)
class Lease:
    message_id: str
    organization_id: str
    owner: str
    token: str
    expires_at: datetime
    attempts: int
    max_attempts: int


class FoundationRuntime:
    """Compose SQL, outbox, storage, command, and redaction adapters."""

    _TERMINAL_OPERATION_STATES = frozenset({"succeeded", "action_required", "stale"})
    _HUMAN_ONLY_COMMANDS = frozenset(
        {"publish_review", "grant_agent_authorization", "revoke_agent_authorization"}
    )
    _OPERATOR_COMMANDS = frozenset({"activate_bootstrap", "recover_methodologist"})

    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        object_storage: S3ObjectStorage,
        settings: Settings,
        engine: AsyncEngine | None = None,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._engine = engine
        self._id_factory = id_factory
        self._clock = clock
        self._transactions = SqlTransactionManager(session_factory)
        self._operation_repositories = OperationRepositoryFactory(session_factory)
        self._revisions = SqlRevisionStore()
        self._idempotency = SqlIdempotencyReceiptRepository()
        self._outbox = SqlOutboxUnitOfWork(
            session_factory,
            token_factory=id_factory,
            clock=clock,
            retry_initial_seconds=settings.retry_initial_seconds,
            retry_max_seconds=settings.retry_max_seconds,
        )
        self._object_storage = object_storage
        self._redactor = Redactor()
        self._membership_guard = UserMembershipAuthGuard(session_factory)
        self._worker_auth_revalidator = MembershipWorkerAuthRevalidator(self._membership_guard)
        self._command_bus = CommandBus(
            transactions=self._transactions,
            revisions=self._revisions,
            authorizer=Authorizer(self._membership_guard, clock=clock),
        )

    def components(self) -> Mapping[str, object]:
        """Expose the actual production graph for architecture assertions."""

        return {
            "command_bus": self._command_bus,
            "operation_repository": self._operation_repositories,
            "outbox_repository": self._outbox,
            "object_storage": self._object_storage,
            "redactor": self._redactor,
        }

    @property
    def command_bus(self) -> CommandBus:
        return self._command_bus

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def id_factory(self) -> Callable[[], UUID]:
        return self._id_factory

    @property
    def clock(self) -> Callable[[], datetime]:
        return self._clock

    @property
    def worker_auth_revalidator(self) -> MembershipWorkerAuthRevalidator:
        return self._worker_auth_revalidator

    @property
    def user_auth_guard(self) -> UserMembershipAuthGuard:
        return self._membership_guard

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Open the caller-owned unit of work used by route/service adapters."""

        async with session_scope(self._session_factory) as session:
            yield session

    def bind_rest_command(
        self,
        *,
        route_command: str,
        path_target_id: str | None,
        wire_command: Mapping[str, Any],
        actor: RequestActor,
        trace_id: UUID,
    ) -> ApplicationCommand:
        """Build the exact application command from server-owned context."""

        if {"organization_id", "actor", "transport", "trace_id"}.intersection(wire_command):
            raise BoundaryViolation("client supplied server-owned command context")
        try:
            wire = WireCommand.model_validate(dict(wire_command))
        except Exception as error:
            raise BoundaryViolation("wire command violates the frozen command contract") from error
        if wire.command_name != route_command:
            raise BoundaryViolation("route command does not match exact command variant")
        if path_target_id is not None and str(wire.target_id) != path_target_id:
            raise BoundaryViolation("path target does not match command target")
        if wire.command_name in self._HUMAN_ONLY_COMMANDS and actor.actor_type != "user":
            raise BoundaryViolation("command requires an interactive human REST session")
        try:
            return ApplicationCommand.model_validate(
                {
                    **wire.model_dump(mode="python"),
                    "organization_id": actor.organization_id,
                    "actor": _contract_actor(actor),
                    "transport": "rest",
                    "trace_id": trace_id,
                }
            )
        except Exception as error:
            raise BoundaryViolation("server-owned application command is invalid") from error

    def bind_operator_command(
        self,
        *,
        command: Mapping[str, Any],
        organization_id: UUID,
        operator_id: str,
        reason: str,
        trace_id: UUID,
    ) -> ApplicationCommand:
        if command.get("command_name") not in self._OPERATOR_COMMANDS:
            raise BoundaryViolation("operator transport accepts only bootstrap or recovery")
        actor = RequestActor.installation_operator(
            organization_id=organization_id,
            installation_operator_id=operator_id,
            reason=reason,
        )
        try:
            return ApplicationCommand.model_validate(
                {
                    **dict(command),
                    "organization_id": organization_id,
                    "actor": _contract_actor(actor),
                    "transport": "operator",
                    "trace_id": trace_id,
                }
            )
        except Exception as error:
            raise BoundaryViolation("operator command violates the frozen contract") from error

    async def reserve_command(
        self,
        *,
        organization_id: str,
        idempotency_key: str,
        request_id: str,
        command_name: str,
        target_id: str,
        expected_revision: int,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        result_id = self._id_factory()
        coordinator = IdempotencyCoordinator(
            self._idempotency,
            receipt_id_factory=self._id_factory,
            result_reference_factory=lambda: {"kind": "command_receipt", "id": str(result_id)},
        )
        try:
            async with self._transactions.begin() as transaction:
                reservation = await coordinator.reserve(
                    organization_id=UUID(organization_id),
                    idempotency_key=idempotency_key,
                    request_id=UUID(request_id),
                    command_name=command_name,
                    target_id=UUID(target_id),
                    expected_revision=expected_revision,
                    payload=payload,
                    transaction=transaction,
                )
        except IdempotencyConflict as error:
            raise BoundaryViolation("idempotency key payload conflict") from error
        receipt = reservation.receipt
        return {
            "receipt_id": str(receipt.receipt_id),
            "organization_id": str(receipt.organization_id),
            "idempotency_key": receipt.idempotency_key,
            "payload_digest": receipt.payload_digest,
            "result_reference": dict(receipt.result_reference),
            "disposition": reservation.disposition,
        }

    async def create_operation(
        self, *, organization_id: str, kind: str, input_version: str
    ) -> OperationView:
        now = self._clock()
        operation = Operation(
            id=self._id_factory(),
            organization_id=UUID(organization_id),
            kind=kind,
            input_version=input_version,
            state="pending",
            revision=0,
            created_at=now,
            updated_at=now,
            finished_at=None,
            error_code=None,
            sanitized_error=None,
            attempts=[],
        )
        async with self._operation_repositories.transaction() as repository:
            await repository.add(operation)
            return _operation_view(operation)

    async def get_operation(
        self, *, organization_id: str, operation_id: str
    ) -> OperationView | None:
        async with self._operation_repositories.transaction() as repository:
            operation = await repository.get(
                UUID(organization_id),
                UUID(operation_id),
            )
            return None if operation is None else _operation_view(operation)

    async def transition_operation(
        self,
        *,
        organization_id: str,
        operation_id: str,
        state: str,
        error: Mapping[str, Any] | None = None,
    ) -> OperationView:
        async with self._operation_repositories.transaction() as repository:
            operation = await _require_operation(
                repository,
                UUID(organization_id),
                UUID(operation_id),
                for_update=True,
            )
            if operation.state in self._TERMINAL_OPERATION_STATES and state != operation.state:
                raise BoundaryViolation("terminal operation state cannot regress")
            bounded = sanitize_error(error) if error is not None else None
            operation.state = state
            operation.revision += 1
            operation.updated_at = self._clock()
            operation.finished_at = (
                self._clock() if state in self._TERMINAL_OPERATION_STATES else None
            )
            operation.error_code = (
                cast(str, bounded.get("code")) if bounded and bounded.get("code") else None
            )
            operation.sanitized_error = bounded
            return _operation_view(operation)

    async def append_operation_attempt(
        self,
        *,
        organization_id: str,
        operation_id: str,
        outcome: str,
        error: Mapping[str, Any] | None = None,
    ) -> OperationView:
        async with self._operation_repositories.transaction() as repository:
            organization_uuid = UUID(organization_id)
            operation_uuid = UUID(operation_id)
            operation = await _require_operation(
                repository,
                organization_uuid,
                operation_uuid,
                for_update=True,
            )
            bounded = sanitize_error(error) if error is not None else None
            now = self._clock()
            attempt = OperationAttempt(
                id=self._id_factory(),
                organization_id=organization_uuid,
                operation_id=operation_uuid,
                attempt_number=await repository.next_attempt_number(
                    organization_uuid,
                    operation_uuid,
                ),
                worker_identity="foundation-runtime",
                started_at=now,
                finished_at=now,
                outcome=outcome,
                error_code=(
                    cast(str, bounded.get("code")) if bounded and bounded.get("code") else None
                ),
                sanitized_error=bounded,
            )
            await repository.add_attempt(attempt)
            operation.state = outcome
            operation.revision += 1
            operation.updated_at = now
            operation.finished_at = (
                now if outcome in self._TERMINAL_OPERATION_STATES else None
            )
            operation.error_code = attempt.error_code
            operation.sanitized_error = bounded
            await repository.flush()
            await repository.refresh_attempts(operation)
            return _operation_view(operation)

    async def enqueue_outbox(
        self,
        *,
        organization_id: str,
        message_id: str,
        payload: Mapping[str, Any],
        max_attempts: int,
    ) -> None:
        message_uuid = UUID(message_id)
        event_type = str(payload.get("event_type", "FoundationEvent"))
        async with self._outbox.transaction() as service:
            await service.create(
                OutboxDraft(
                    organization_id=UUID(organization_id),
                    message_id=message_uuid,
                    aggregate_type="foundation",
                    aggregate_id=message_uuid,
                    event_type=event_type,
                    payload_version=self._settings.contract_version,
                    payload=payload,
                    available_at=self._clock(),
                    max_attempts=max_attempts,
                )
            )

    async def lease_outbox(
        self,
        *,
        owner: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> Sequence[Lease]:
        async with self._outbox.transaction() as service:
            leases = await service.lease(
                owner=owner,
                now=now,
                limit=limit,
                lease_seconds=lease_seconds,
            )
        return tuple(_lease_view(lease) for lease in leases)

    async def complete_outbox(self, *, lease: Lease) -> None:
        async with self._outbox.transaction() as service:
            try:
                await service.complete(_outbox_lease(lease), now=self._clock())
            except StaleOutboxLease as error:
                raise BoundaryViolation("outbox lease token is stale") from error

    async def fail_outbox(
        self, *, lease: Lease, error: Mapping[str, Any], now: datetime
    ) -> Mapping[str, Any]:
        async with self._outbox.transaction() as service:
            try:
                view = await service.fail(_outbox_lease(lease), error=error, now=now)
            except StaleOutboxLease as caught:
                raise BoundaryViolation("outbox lease token is stale") from caught
        return {
            "organization_id": str(view.organization_id),
            "message_id": str(view.message_id),
            "state": view.state,
            "attempts": view.attempts,
            "max_attempts": view.max_attempts,
            "available_at": view.available_at,
            "error": view.error,
        }

    def redis_key(self, *, organization_id: str, category: str, identity: str) -> str:
        return f"review-platform:{organization_id}:{category}:{identity}"

    def s3_key(self, *, organization_id: str, artifact_version_id: str) -> str:
        return self._object_storage.key(
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
        )

    def sign_artifact_read(
        self,
        *,
        organization_id: str,
        artifact_version_id: str,
        requested_by_organization_id: str,
    ) -> str:
        key = self.s3_key(
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
        )
        try:
            return self._object_storage.sign_read(
                organization_id=organization_id,
                artifact_version_id=artifact_version_id,
                requested_by_organization_id=requested_by_organization_id,
                key=key,
                expires_in_seconds=self._settings.ai_signed_url_ttl_seconds,
            )
        except (ObjectStorageError, ValueError) as error:
            raise BoundaryViolation("artifact organization boundary violation") from error

    def write_shared_sink(
        self, *, sink: str, organization_id: str, details: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._redactor.sanitize(
            sink=sink,
            organization_id=organization_id,
            details=details,
        )

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()


def build_foundation_runtime(
    settings: Settings | None = None,
    *,
    session_factory: AsyncSessionFactory | None = None,
    s3_client: S3Client | None = None,
    id_factory: Callable[[], UUID] = uuid7,
    clock: Callable[[], datetime] = utc_now,
) -> FoundationRuntime:
    """Build only real production adapters; never substitute test/in-memory stores."""

    selected = settings or get_settings()
    engine: AsyncEngine | None = None
    if session_factory is None:
        if selected.database_url is None:
            raise RuntimeConfigurationError("REVIEW_PLATFORM_DATABASE_URL is required")
        engine = create_database_engine(selected.database_url)
        session_factory = create_session_factory(engine)
    if s3_client is None:
        if selected.s3_endpoint_url is None:
            raise RuntimeConfigurationError("REVIEW_PLATFORM_S3_ENDPOINT_URL is required")
        access_key = (
            selected.s3_access_key_id.get_secret_value()
            if selected.s3_access_key_id is not None
            else None
        )
        secret_key = (
            selected.s3_secret_access_key.get_secret_value()
            if selected.s3_secret_access_key is not None
            else None
        )
        s3_client = cast(
            S3Client,
            boto3.client(
                "s3",
                endpoint_url=selected.s3_endpoint_url,
                region_name=selected.s3_region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            ),
        )
    object_storage = S3ObjectStorage(
        client=s3_client,
        bucket=selected.s3_bucket,
        max_object_bytes=selected.artifact_total_max_bytes,
    )
    return FoundationRuntime(
        session_factory=session_factory,
        object_storage=object_storage,
        settings=selected,
        engine=engine,
        id_factory=id_factory,
        clock=clock,
    )


async def _require_operation(
    repository: OperationRepository,
    organization_id: UUID,
    operation_id: UUID,
    *,
    for_update: bool,
) -> Operation:
    operation = await repository.get(
        organization_id,
        operation_id,
        for_update=for_update,
    )
    if operation is None:
        raise BoundaryViolation("tenant-scoped operation not found")
    return operation


def _operation_view(operation: Operation) -> OperationView:
    attempts = tuple(
        {
            "attempt_number": attempt.attempt_number,
            "state": attempt.outcome,
            "started_at": attempt.started_at,
            "finished_at": attempt.finished_at,
            "error": attempt.sanitized_error,
        }
        for attempt in operation.attempts
    )
    return OperationView(
        operation_id=str(operation.id),
        organization_id=str(operation.organization_id),
        kind=operation.kind,
        input_version=operation.input_version,
        state=operation.state,
        attempts=attempts,
        error=operation.sanitized_error,
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        finished_at=operation.finished_at,
    )


def _lease_view(lease: OutboxLease) -> Lease:
    return Lease(
        message_id=str(lease.message_id),
        organization_id=str(lease.organization_id),
        owner=lease.owner,
        token=str(lease.token),
        expires_at=lease.expires_at,
        attempts=lease.attempts,
        max_attempts=lease.max_attempts,
    )


def _outbox_lease(lease: Lease) -> OutboxLease:
    return OutboxLease(
        organization_id=UUID(lease.organization_id),
        message_id=UUID(lease.message_id),
        aggregate_type="foundation",
        aggregate_id=UUID(lease.message_id),
        event_type="FoundationEvent",
        payload_version="1.1.0",
        owner=lease.owner,
        token=UUID(lease.token),
        expires_at=lease.expires_at,
        attempts=lease.attempts,
        max_attempts=lease.max_attempts,
    )


def _contract_actor(actor: RequestActor) -> Mapping[str, Any]:
    if actor.actor_type == "user":
        return {
            "type": "user",
            "user_id": actor.user_id,
            "membership_revision": actor.membership_revision,
            "auth_epoch": actor.auth_epoch,
        }
    if actor.actor_type == "agent":
        return {
            "type": "agent",
            "user_id": actor.user_id,
            "membership_revision": actor.membership_revision,
            "auth_epoch": actor.auth_epoch,
            "agent_id": actor.agent_id,
            "agent_authorization_id": actor.agent_authorization_id,
        }
    return {
        "type": "installation_operator",
        "installation_operator_id": actor.installation_operator_id,
        "reason": actor.reason,
    }


__all__ = [
    "BoundaryViolation",
    "FoundationRuntime",
    "Lease",
    "OperationView",
    "RuntimeConfigurationError",
    "build_foundation_runtime",
]
