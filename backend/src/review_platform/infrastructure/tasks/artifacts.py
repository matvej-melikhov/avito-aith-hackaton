"""Registered durable artifact capture, promotion, recovery, and cleanup tasks."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditError, AuditRecorder
from review_platform.application.auth_guards.membership import UserMembershipAuthGuard
from review_platform.application.authorization import AuthorizationDenied, Authorizer
from review_platform.application.ports.providers import (
    CONTRACT_VERSION,
    ArtifactProvider,
    JsonValue,
)
from review_platform.application.request_context import AuthVersionGuard, RequestActor, Role
from review_platform.application.services.artifact_capture import (
    ArtifactCaptureCommand,
    ArtifactCaptureLimits,
    ArtifactCaptureResult,
    ArtifactCaptureService,
    ArtifactContentMismatch,
    ArtifactStagingError,
)
from review_platform.application.services.artifact_preflight import (
    ArtifactCredentialBinding,
    ArtifactProviderName,
)
from review_platform.domain.primitives import require_utc, sanitize_error, utc_now, uuid7
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.identity import ExternalCredential
from review_platform.infrastructure.db.models.operations import (
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactPromotion,
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.repositories.submissions import (
    ArtifactPromotionStatus,
    SqlArtifactCaptureAuthorization,
    SqlArtifactCaptureRepository,
    SqlArtifactCredentialBindings,
    SqlArtifactPromotionOutbox,
    SqlArtifactPromotionRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.object_storage.promotions import (
    ArtifactOrphanCleaner,
    ArtifactPromotionCoordinator,
    ArtifactPromotionError,
    BoundedContentFetcher,
    CleanupResult,
    PromotionAuditPort,
    PromotionNotClaimed,
    S3ArtifactStaging,
    S3PromotionObjects,
    SqlArtifactPromotionAuditRecorder,
    StagedObjectCandidate,
    StagedObjectInventory,
    StalePromotionLease,
)
from review_platform.infrastructure.object_storage.s3 import S3ObjectStorage

from .broker import RetryableTaskError
from .registry import task_handler

ARTIFACT_TASK_RUNTIME_FACTORY_ENV = "REVIEW_PLATFORM_ARTIFACT_TASK_RUNTIME_FACTORY"


class ArtifactTaskError(RuntimeError):
    """A durable artifact job is malformed or cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class ArtifactTaskRuntime:
    """Explicit composition boundary; no provider or network fallback is implicit."""

    session_factory: AsyncSessionFactory
    storage: S3ObjectStorage
    providers: Mapping[str, ArtifactProvider]
    fetcher: BoundedContentFetcher
    inventory: StagedObjectInventory
    capture_limits: ArtifactCaptureLimits
    auth_guard: AuthVersionGuard | None = None
    id_factory: Callable[[], UUID] = uuid7
    clock: Callable[[], datetime] = utc_now
    capture_max_attempts: int = 5
    promotion_lease_seconds: int = 60
    promotion_retry_seconds: int = 60
    recovery_batch_size: int = 100
    promotion_audit_factory: Callable[[str], PromotionAuditPort] | None = None

    def __post_init__(self) -> None:
        if set(self.providers).difference({"github", "google_docs"}):
            raise ValueError("artifact runtime contains an unknown provider")
        if not self.providers:
            raise ValueError("artifact runtime requires at least one configured provider")
        if not 1 <= self.capture_max_attempts <= 10:
            raise ValueError("capture retry budget must be between 1 and 10")
        if not 1 <= self.recovery_batch_size <= 1000:
            raise ValueError("promotion recovery batch must be between 1 and 1000")


class ArtifactCaptureTaskHandler:
    def __init__(self, runtime: ArtifactTaskRuntime) -> None:
        self._runtime = runtime
        self._guard = runtime.auth_guard or UserMembershipAuthGuard(runtime.session_factory)

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        retryable = False
        response: dict[str, Any]
        async with session_scope(self._runtime.session_factory) as session:
            message = await OutboxMessageRepository(session).get(
                organization_uuid,
                message_uuid,
                for_update=False,
            )
            payload = _capture_payload(message, organization_id=organization_uuid)
            assert message is not None
            submission_version, operation = await _lock_capture_scope(
                session,
                payload,
                organization_id=organization_uuid,
                operation_id=message.aggregate_id,
            )
            duplicate = await _capture_duplicate(
                session,
                organization_id=organization_uuid,
                submission_version=submission_version,
                operation=operation,
                message_id=message_uuid,
            )
            if duplicate is not None:
                return duplicate
            actor = _actor(payload, organization_id=organization_uuid)
            command = _capture_command(
                payload,
                organization_id=organization_uuid,
                operation_id=operation.id,
                actor=actor,
                message_id=message_uuid,
                runtime=self._runtime,
            )
            provider = self._runtime.providers.get(command.provider)
            if provider is None:
                raise ArtifactTaskError(f"artifact provider {command.provider!r} is not configured")
            repository = SqlArtifactCaptureRepository(id_factory=self._runtime.id_factory)
            service = ArtifactCaptureService(
                repository=repository,
                staging=S3ArtifactStaging(
                    storage=self._runtime.storage,
                    fetcher=self._runtime.fetcher,
                ),
                credential_bindings=SqlArtifactCredentialBindings(),
                authorization=SqlArtifactCaptureAuthorization(
                    Authorizer(self._guard, clock=self._runtime.clock)
                ),
                outbox=SqlArtifactPromotionOutbox(
                    clock=self._runtime.clock,
                    max_attempts=command.max_promotion_attempts,
                ),
                provider=provider,
                audit=AuditRecorder(
                    SqlAppendOnlyAuditRepository(),
                    event_id_factory=self._runtime.id_factory,
                    clock=self._runtime.clock,
                ),
                id_factory=self._runtime.id_factory,
                clock=self._runtime.clock,
            )
            try:
                result = await service.capture(command, transaction=session)
            except AuthorizationDenied:
                # Revoked work must roll back without manufacturing a durable
                # result under authority that no longer exists.
                raise
            except AuditError:
                raise
            except Exception as error:
                result = await self._record_unhandled_failure(
                    repository=repository,
                    command=command,
                    operation=operation,
                    error=error,
                    transaction=session,
                )
                await service.record_audit(
                    command,
                    result,
                    reference_revision=None,
                    transaction=session,
                )
                await self._guard.lock_and_revalidate(actor=actor, transaction=session)
            result = await _apply_capture_result(
                session,
                submission_version=submission_version,
                result=result,
            )
            if (
                result.state == "retryable_failed"
                and result.attempt_number >= self._runtime.capture_max_attempts
            ):
                result = await _exhaust_capture_retry(
                    session,
                    result=result,
                    now=require_utc(self._runtime.clock()),
                )
            retryable = result.state == "retryable_failed"
            response = _capture_result_payload(result, message_id=message_uuid)
        if retryable:
            raise RetryableTaskError("artifact capture failed with a retryable outcome")
        return response

    async def _record_unhandled_failure(
        self,
        *,
        repository: SqlArtifactCaptureRepository,
        command: ArtifactCaptureCommand,
        operation: Operation,
        error: Exception,
        transaction: AsyncSession,
    ) -> ArtifactCaptureResult:
        retryable = not isinstance(error, ArtifactContentMismatch | AuthorizationDenied)
        if isinstance(error, ArtifactStagingError):
            retryable = error.retryable
        current_attempts = await _operation_attempt_count(
            transaction,
            command.organization_id,
            command.operation_id,
        )
        exhausted = current_attempts + 1 >= self._runtime.capture_max_attempts
        failure = await repository.record_failure(
            command=command,
            input_version=operation.input_version,
            state=("retryable_failed" if retryable and not exhausted else "action_required"),
            error=cast(
                Mapping[str, JsonValue],
                sanitize_error(
                    {
                        "code": "artifact_capture_failed",
                        "message": str(error),
                        "retryable": retryable and not exhausted,
                        "action": "retry" if retryable and not exhausted else "inspect_artifact",
                    },
                    max_bytes=1900,
                ),
            ),
            attempt_metadata={
                "provider": command.provider,
                "credential_binding_id": str(command.credential_binding.credential_binding_id),
                "credential_binding_version": (
                    command.credential_binding.credential_binding_version
                ),
            },
            now=require_utc(self._runtime.clock()),
            transaction=transaction,
        )
        return ArtifactCaptureResult(
            organization_id=command.organization_id,
            artifact_reference_id=command.artifact_reference_id,
            artifact_version_id=None,
            promotion_id=None,
            operation_id=command.operation_id,
            state=failure.operation_state,
            attempt_number=failure.attempt_number,
            replayed=False,
            error=failure.error,
        )


class ArtifactPromotionTaskHandler:
    def __init__(self, runtime: ArtifactTaskRuntime, *, owner: str) -> None:
        if not owner or len(owner) > 255:
            raise ValueError("artifact promotion worker identity is invalid")
        self._runtime = runtime
        self._owner = owner
        self._repository = SqlArtifactPromotionRepository(
            runtime.session_factory,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        )

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with self._runtime.session_factory() as session:
            message = await OutboxMessageRepository(session).get(
                organization_uuid,
                message_uuid,
            )
        payload = _promotion_payload(message, organization_id=organization_uuid)
        assert message is not None
        promotion_id = _required_uuid(payload, "promotion_id")
        status = await self._repository.status(organization_uuid, promotion_id)
        _validate_promotion_provenance(status, payload, message=message)
        if status is not None and status.state == "promoted":
            return _promotion_status_payload(status, message_id=message_uuid, replayed=True)
        coordinator = ArtifactPromotionCoordinator(
            repository=self._repository,
            objects=S3PromotionObjects(self._runtime.storage),
            audit=self._promotion_audit(),
            owner=self._owner,
            token_factory=self._runtime.id_factory,
            clock=self._runtime.clock,
            lease_seconds=self._runtime.promotion_lease_seconds,
            retry_seconds=self._runtime.promotion_retry_seconds,
        )
        try:
            result = await coordinator.promote(
                organization_id=organization_uuid,
                promotion_id=promotion_id,
            )
        except (PromotionNotClaimed, StalePromotionLease):
            current = await self._repository.status(organization_uuid, promotion_id)
            if current is not None and current.state in {"promoted", "action_required"}:
                return _promotion_status_payload(
                    current,
                    message_id=message_uuid,
                    replayed=True,
                )
            raise RetryableTaskError("artifact promotion lease is currently unavailable") from None
        except ArtifactPromotionError as error:
            current = await self._repository.status(organization_uuid, promotion_id)
            if current is not None and current.state == "action_required":
                return _promotion_status_payload(
                    current,
                    message_id=message_uuid,
                    replayed=False,
                )
            raise RetryableTaskError("artifact promotion failed retryably") from error
        return {
            "organization_id": organization_id,
            "message_id": message_id,
            "promotion_id": str(result.promotion_id),
            "artifact_version_id": str(result.artifact_version_id),
            "operation_id": str(result.operation_id),
            "state": result.state,
            "replayed": False,
            "recovered_existing_final": result.recovered_existing_final,
            "staged_deleted": result.staged_deleted,
        }

    async def recover(
        self,
        organization_id: UUID,
        *,
        limit: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        batch_size = limit or self._runtime.recovery_batch_size
        ids = await self._repository.list_recoverable(
            organization_id,
            now=require_utc(self._runtime.clock()),
            limit=batch_size,
        )
        results: list[dict[str, Any]] = []
        for promotion_id in ids:
            try:
                promoted = await ArtifactPromotionCoordinator(
                    repository=self._repository,
                    objects=S3PromotionObjects(self._runtime.storage),
                    audit=self._promotion_audit(),
                    owner=self._owner,
                    token_factory=self._runtime.id_factory,
                    clock=self._runtime.clock,
                    lease_seconds=self._runtime.promotion_lease_seconds,
                    retry_seconds=self._runtime.promotion_retry_seconds,
                ).promote(
                    organization_id=organization_id,
                    promotion_id=promotion_id,
                )
                results.append(
                    {
                        "promotion_id": str(promoted.promotion_id),
                        "state": promoted.state,
                        "recovered_existing_final": promoted.recovered_existing_final,
                    }
                )
            except (ArtifactPromotionError, PromotionNotClaimed, StalePromotionLease):
                current = await self._repository.status(organization_id, promotion_id)
                results.append(
                    {
                        "promotion_id": str(promotion_id),
                        "state": current.state if current is not None else "unavailable",
                        "recovered_existing_final": False,
                    }
                )
        return tuple(results)

    def _promotion_audit(self) -> PromotionAuditPort:
        if self._runtime.promotion_audit_factory is not None:
            return self._runtime.promotion_audit_factory(self._owner)
        return SqlArtifactPromotionAuditRecorder(
            worker_identity=self._owner,
            id_factory=self._runtime.id_factory,
            clock=self._runtime.clock,
        )


class ArtifactCleanupTaskHandler:
    def __init__(self, runtime: ArtifactTaskRuntime) -> None:
        self._runtime = runtime
        self._repository = SqlArtifactPromotionRepository(
            runtime.session_factory,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        )

    async def cleanup(
        self,
        organization_id: UUID,
        *,
        older_than: datetime,
        dry_run: bool,
    ) -> CleanupResult:
        cleaner = ArtifactOrphanCleaner(
            repository=self._repository,
            inventory=_TenantInventory(self._runtime.inventory, organization_id),
            objects=S3PromotionObjects(self._runtime.storage),
        )
        return await cleaner.cleanup(older_than=older_than, dry_run=dry_run)

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with self._runtime.session_factory() as session:
            message = await OutboxMessageRepository(session).get(
                organization_uuid,
                message_uuid,
            )
        payload = _maintenance_payload(
            message,
            organization_id=organization_uuid,
            event_type="ArtifactStagedCleanupRequested",
        )
        older_than = _required_datetime(payload, "older_than")
        dry_run = payload.get("dry_run")
        if not isinstance(dry_run, bool):
            raise ArtifactTaskError("artifact cleanup dry_run must be boolean")
        result = await self.cleanup(
            organization_uuid,
            older_than=older_than,
            dry_run=dry_run,
        )
        return {
            "organization_id": organization_id,
            "message_id": message_id,
            "candidates": result.candidates,
            "protected": result.protected,
            "eligible": list(result.eligible),
            "deleted": list(result.deleted),
            "dry_run": result.dry_run,
        }


class _TenantInventory:
    def __init__(self, inventory: StagedObjectInventory, organization_id: UUID) -> None:
        self._inventory = inventory
        self._organization_id = organization_id

    async def list_staged(self, *, older_than: datetime) -> Sequence[StagedObjectCandidate]:
        candidates = await self._inventory.list_staged(older_than=older_than)
        return tuple(
            candidate
            for candidate in candidates
            if candidate.organization_id == self._organization_id
        )


def _capture_payload(
    message: OutboxMessage | None,
    *,
    organization_id: UUID,
) -> Mapping[str, JsonValue]:
    if message is None:
        raise ArtifactTaskError("tenant-scoped artifact capture outbox message was not found")
    if message.organization_id != organization_id:
        raise ArtifactTaskError("artifact capture outbox tenant mismatched")
    if (
        message.event_type != "ArtifactCaptureRequested"
        or message.payload_version != CONTRACT_VERSION
        or message.aggregate_type != "operation"
    ):
        raise ArtifactTaskError("outbox row is not a supported artifact capture request")
    payload = cast(Mapping[str, JsonValue], message.payload)
    if _required_uuid(payload, "organization_id") != organization_id:
        raise ArtifactTaskError("artifact capture payload tenant mismatched")
    if _required_uuid(payload, "operation_id") != message.aggregate_id:
        raise ArtifactTaskError("artifact capture aggregate Operation mismatched")
    return payload


async def _lock_capture_scope(
    session: AsyncSession,
    payload: Mapping[str, JsonValue],
    *,
    organization_id: UUID,
    operation_id: UUID,
) -> tuple[SubmissionVersion, Operation]:
    version_id = _required_uuid(payload, "submission_version_id")
    submission_version = await session.scalar(
        select(SubmissionVersion)
        .where(
            SubmissionVersion.organization_id == organization_id,
            SubmissionVersion.id == version_id,
            SubmissionVersion.submission_id == _required_uuid(payload, "submission_id"),
            SubmissionVersion.artifact_reference_id
            == _required_uuid(payload, "artifact_reference_id"),
            SubmissionVersion.capture_operation_id == operation_id,
            SubmissionVersion.course_run_id == _required_uuid(payload, "course_run_id"),
            SubmissionVersion.homework_id == _required_uuid(payload, "homework_id"),
            SubmissionVersion.homework_version_id == _required_uuid(payload, "homework_version_id"),
        )
        .with_for_update()
    )
    operation = await session.scalar(
        select(Operation)
        .where(
            Operation.organization_id == organization_id,
            Operation.id == operation_id,
            Operation.kind == "artifact_capture",
        )
        .with_for_update()
    )
    if submission_version is None or operation is None:
        raise ArtifactTaskError("capture submission version or Operation provenance mismatched")
    submission = await session.scalar(
        select(Submission).where(
            Submission.organization_id == organization_id,
            Submission.id == submission_version.submission_id,
            Submission.student_id == _actor_user_id(payload),
        )
    )
    if submission is None:
        raise ArtifactTaskError("capture actor does not own the exact Submission")
    provider = _required_string(payload, "provider")
    credential_id = _required_uuid(payload, "credential_binding_id")
    credential_version = _required_int(
        payload,
        "credential_binding_version",
        minimum=1,
    )
    reference = await session.scalar(
        select(ArtifactReference)
        .where(
            ArtifactReference.organization_id == organization_id,
            ArtifactReference.id == submission_version.artifact_reference_id,
            ArtifactReference.provider == provider,
            ArtifactReference.credential_binding_id == credential_id,
            ArtifactReference.credential_binding_version == credential_version,
        )
        .with_for_update()
    )
    credential = await session.scalar(
        select(ExternalCredential)
        .where(
            ExternalCredential.organization_id == organization_id,
            ExternalCredential.id == credential_id,
            ExternalCredential.binding_version == credential_version,
            ExternalCredential.provider == provider,
            ExternalCredential.status == "active",
        )
        .with_for_update()
    )
    if reference is None or credential is None:
        raise ArtifactTaskError(
            "capture persisted reference or exact active credential provenance mismatched"
        )
    return submission_version, operation


async def _capture_duplicate(
    session: AsyncSession,
    *,
    organization_id: UUID,
    submission_version: SubmissionVersion,
    operation: Operation,
    message_id: UUID,
) -> dict[str, Any] | None:
    if submission_version.artifact_version_id is None:
        return None
    row = (
        await session.execute(
            select(ArtifactVersion, ArtifactPromotion)
            .join(
                ArtifactPromotion,
                (ArtifactPromotion.organization_id == ArtifactVersion.organization_id)
                & (ArtifactPromotion.artifact_version_id == ArtifactVersion.id),
            )
            .where(
                ArtifactVersion.organization_id == organization_id,
                ArtifactVersion.id == submission_version.artifact_version_id,
                ArtifactVersion.artifact_reference_id == submission_version.artifact_reference_id,
                ArtifactPromotion.operation_id == operation.id,
            )
        )
    ).one_or_none()
    if row is None:
        raise ArtifactTaskError("linked capture bundle provenance is incomplete")
    version, promotion = row
    return {
        "organization_id": str(organization_id),
        "message_id": str(message_id),
        "operation_id": str(operation.id),
        "artifact_reference_id": str(version.artifact_reference_id),
        "artifact_version_id": str(version.id),
        "promotion_id": str(promotion.id),
        "state": promotion.state,
        "attempt_number": await _operation_attempt_count(
            session,
            organization_id,
            operation.id,
        ),
        "replayed": True,
        "error": promotion.sanitized_error,
    }


async def _apply_capture_result(
    session: AsyncSession,
    *,
    submission_version: SubmissionVersion,
    result: ArtifactCaptureResult,
) -> ArtifactCaptureResult:
    if result.operation_id != submission_version.capture_operation_id:
        raise ArtifactTaskError("capture result Operation provenance mismatched")
    if result.artifact_version_id is not None:
        exists = await session.scalar(
            select(ArtifactVersion.id).where(
                ArtifactVersion.organization_id == result.organization_id,
                ArtifactVersion.id == result.artifact_version_id,
                ArtifactVersion.artifact_reference_id == submission_version.artifact_reference_id,
            )
        )
        if exists is None:
            raise ArtifactTaskError("capture result ArtifactVersion provenance mismatched")
        if (
            submission_version.artifact_version_id is not None
            and submission_version.artifact_version_id != result.artifact_version_id
        ):
            raise ArtifactTaskError("SubmissionVersion already links a different artifact")
        submission_version.artifact_version_id = result.artifact_version_id
        if submission_version.phase == "before_deadline":
            submission_version.status = "ready"
        await session.flush([submission_version])
    elif result.state == "action_required":
        submission_version.status = "access_error"
        await session.flush([submission_version])
    return result


async def _exhaust_capture_retry(
    session: AsyncSession,
    *,
    result: ArtifactCaptureResult,
    now: datetime,
) -> ArtifactCaptureResult:
    operation = await session.scalar(
        select(Operation)
        .where(
            Operation.organization_id == result.organization_id,
            Operation.id == result.operation_id,
        )
        .with_for_update()
    )
    attempt = await session.scalar(
        select(OperationAttempt)
        .where(
            OperationAttempt.organization_id == result.organization_id,
            OperationAttempt.operation_id == result.operation_id,
            OperationAttempt.attempt_number == result.attempt_number,
        )
        .with_for_update()
    )
    if operation is None or attempt is None:
        raise ArtifactTaskError("capture retry exhaustion lost Operation attempt")
    error = sanitize_error(
        {
            "code": "artifact_capture_attempts_exhausted",
            "message": "Artifact capture exhausted its retry budget",
            "retryable": False,
            "action": "inspect_artifact",
        }
    )
    operation.state = "action_required"
    operation.finished_at = now
    operation.updated_at = now
    operation.error_code = "artifact_capture_attempts_exhausted"
    operation.sanitized_error = error
    operation.revision += 1
    attempt.outcome = "action_required"
    attempt.error_code = operation.error_code
    attempt.sanitized_error = error
    submission_version = await session.scalar(
        select(SubmissionVersion)
        .where(
            SubmissionVersion.organization_id == result.organization_id,
            SubmissionVersion.capture_operation_id == result.operation_id,
        )
        .with_for_update()
    )
    if submission_version is None:
        raise ArtifactTaskError("capture retry exhaustion lost SubmissionVersion")
    submission_version.status = "access_error"
    await session.flush()
    return ArtifactCaptureResult(
        organization_id=result.organization_id,
        artifact_reference_id=result.artifact_reference_id,
        artifact_version_id=None,
        promotion_id=None,
        operation_id=result.operation_id,
        state="action_required",
        attempt_number=result.attempt_number,
        replayed=False,
        error=cast(Mapping[str, JsonValue], error),
    )


def _capture_command(
    payload: Mapping[str, JsonValue],
    *,
    organization_id: UUID,
    operation_id: UUID,
    actor: RequestActor,
    message_id: UUID,
    runtime: ArtifactTaskRuntime,
) -> ArtifactCaptureCommand:
    provider_value = _required_string(payload, "provider")
    if provider_value not in {"github", "google_docs"}:
        raise ArtifactTaskError("artifact capture provider is not supported")
    provider = cast(ArtifactProviderName, provider_value)
    return ArtifactCaptureCommand(
        organization_id=organization_id,
        artifact_reference_id=_required_uuid(payload, "artifact_reference_id"),
        operation_id=operation_id,
        provider=provider,
        credential_binding=ArtifactCredentialBinding(
            provider=provider,
            credential_binding_id=_required_uuid(payload, "credential_binding_id"),
            credential_binding_version=_required_int(
                payload,
                "credential_binding_version",
                minimum=1,
            ),
        ),
        limits=runtime.capture_limits,
        actor=actor,
        request_id=operation_id,
        trace_id=message_id,
        max_promotion_attempts=runtime.capture_max_attempts,
    )


def _promotion_payload(
    message: OutboxMessage | None,
    *,
    organization_id: UUID,
) -> Mapping[str, JsonValue]:
    if message is None:
        raise ArtifactTaskError("tenant-scoped promotion outbox message was not found")
    if (
        message.organization_id != organization_id
        or message.event_type != "ArtifactPromotionRequested"
        or message.payload_version != CONTRACT_VERSION
        or message.aggregate_type != "artifact_promotion"
    ):
        raise ArtifactTaskError("outbox row is not a supported artifact promotion request")
    payload = cast(Mapping[str, JsonValue], message.payload)
    if _required_uuid(payload, "organization_id") != organization_id:
        raise ArtifactTaskError("artifact promotion payload tenant mismatched")
    if _required_uuid(payload, "promotion_id") != message.aggregate_id:
        raise ArtifactTaskError("artifact promotion aggregate mismatched")
    return payload


def _validate_promotion_provenance(
    status: ArtifactPromotionStatus | None,
    payload: Mapping[str, JsonValue],
    *,
    message: OutboxMessage,
) -> None:
    if status is None:
        raise ArtifactTaskError("tenant-scoped ArtifactPromotion was not found")
    if (
        status.promotion_id != message.aggregate_id
        or status.artifact_version_id != _required_uuid(payload, "artifact_version_id")
        or status.operation_id != _required_uuid(payload, "operation_id")
        or status.staged_key != _required_string(payload, "staged_key")
        or status.final_key != _required_string(payload, "final_key")
    ):
        raise ArtifactTaskError("promotion outbox and DB intent provenance mismatched")


def _maintenance_payload(
    message: OutboxMessage | None,
    *,
    organization_id: UUID,
    event_type: str,
) -> Mapping[str, JsonValue]:
    if message is None:
        raise ArtifactTaskError("tenant-scoped maintenance outbox message was not found")
    if (
        message.organization_id != organization_id
        or message.event_type != event_type
        or message.payload_version != CONTRACT_VERSION
        or message.aggregate_type != "organization"
        or message.aggregate_id != organization_id
    ):
        raise ArtifactTaskError("outbox row is not the requested tenant maintenance job")
    return cast(Mapping[str, JsonValue], message.payload)


def _actor(payload: Mapping[str, JsonValue], *, organization_id: UUID) -> RequestActor:
    raw = payload.get("actor")
    if not isinstance(raw, Mapping) or raw.get("type") != "user":
        raise ArtifactTaskError("artifact capture requires user actor provenance")
    roles_value = raw.get("roles")
    if not isinstance(roles_value, list) or not all(isinstance(role, str) for role in roles_value):
        raise ArtifactTaskError("artifact capture actor roles are invalid")
    roles = cast(list[Role], roles_value)
    if "student" not in roles:
        raise ArtifactTaskError("artifact capture actor must include student role")
    return RequestActor.user(
        organization_id=organization_id,
        user_id=_required_uuid(raw, "user_id"),
        roles=roles,
        membership_revision=_required_int(raw, "membership_revision", minimum=0),
        auth_epoch=_required_int(raw, "auth_epoch", minimum=0),
    )


def _actor_user_id(payload: Mapping[str, JsonValue]) -> UUID:
    raw = payload.get("actor")
    if not isinstance(raw, Mapping):
        raise ArtifactTaskError("artifact capture actor provenance is absent")
    return _required_uuid(raw, "user_id")


async def _operation_attempt_count(
    transaction: AsyncSession,
    organization_id: UUID,
    operation_id: UUID,
) -> int:
    value = await transaction.scalar(
        select(func.max(OperationAttempt.attempt_number)).where(
            OperationAttempt.organization_id == organization_id,
            OperationAttempt.operation_id == operation_id,
        )
    )
    return int(value or 0)


def _capture_result_payload(
    result: ArtifactCaptureResult,
    *,
    message_id: UUID,
) -> dict[str, Any]:
    return {
        "organization_id": str(result.organization_id),
        "message_id": str(message_id),
        "operation_id": str(result.operation_id),
        "artifact_reference_id": str(result.artifact_reference_id),
        "artifact_version_id": (
            str(result.artifact_version_id) if result.artifact_version_id is not None else None
        ),
        "promotion_id": str(result.promotion_id) if result.promotion_id is not None else None,
        "state": result.state,
        "attempt_number": result.attempt_number,
        "replayed": result.replayed,
        "error": dict(result.error) if result.error is not None else None,
    }


def _promotion_status_payload(
    status: ArtifactPromotionStatus,
    *,
    message_id: UUID,
    replayed: bool,
) -> dict[str, Any]:
    return {
        "organization_id": str(status.organization_id),
        "message_id": str(message_id),
        "promotion_id": str(status.promotion_id),
        "artifact_version_id": str(status.artifact_version_id),
        "operation_id": str(status.operation_id),
        "state": status.state,
        "attempts": status.attempts,
        "max_attempts": status.max_attempts,
        "replayed": replayed,
        "error": dict(status.error) if status.error is not None else None,
    }


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ArtifactTaskError(f"artifact task {field} must be a non-empty string")
    return value


def _required_uuid(payload: Mapping[str, object], field: str) -> UUID:
    return _uuid(_required_string(payload, field), field=field)


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ArtifactTaskError(f"artifact task {field} must be a UUID") from error


def _required_int(
    payload: Mapping[str, object],
    field: str,
    *,
    minimum: int,
) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ArtifactTaskError(f"artifact task {field} must be an integer >= {minimum}")
    return value


def _required_datetime(payload: Mapping[str, object], field: str) -> datetime:
    value = _required_string(payload, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArtifactTaskError(f"artifact task {field} must be a date-time") from error
    return require_utc(parsed)


def _load_runtime() -> ArtifactTaskRuntime:
    specification = os.environ.get(ARTIFACT_TASK_RUNTIME_FACTORY_ENV)
    if not specification:
        raise ArtifactTaskError(f"{ARTIFACT_TASK_RUNTIME_FACTORY_ENV} is required")
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise ArtifactTaskError(
            f"{ARTIFACT_TASK_RUNTIME_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise ArtifactTaskError("artifact task runtime factory is not callable")
    runtime = factory()
    if not isinstance(runtime, ArtifactTaskRuntime):
        raise ArtifactTaskError("artifact task factory did not return ArtifactTaskRuntime")
    return runtime


@task_handler(
    name="review_platform.artifact_capture",
    kind="artifact_capture",
    event_type="ArtifactCaptureRequested",
    requires_auth_revalidation=False,
)
async def handle_artifact_capture(*, organization_id: str, message_id: str) -> Mapping[str, Any]:
    return await ArtifactCaptureTaskHandler(_load_runtime())(
        organization_id=organization_id,
        message_id=message_id,
    )


@task_handler(
    name="review_platform.artifact_promotion",
    kind="artifact_capture",
    event_type="ArtifactPromotionRequested",
    requires_auth_revalidation=False,
)
async def handle_artifact_promotion(
    *,
    organization_id: str,
    message_id: str,
) -> Mapping[str, Any]:
    return await ArtifactPromotionTaskHandler(
        _load_runtime(),
        owner="taskiq-artifact-promotion",
    )(
        organization_id=organization_id,
        message_id=message_id,
    )


@task_handler(
    name="review_platform.artifact_promotion_recovery",
    kind="artifact_capture",
    event_type="ArtifactPromotionRecoveryRequested",
    requires_auth_revalidation=False,
)
async def handle_artifact_promotion_recovery(
    *,
    organization_id: str,
    message_id: str,
) -> Mapping[str, Any]:
    runtime = _load_runtime()
    organization_uuid = _uuid(organization_id, field="organization_id")
    message_uuid = _uuid(message_id, field="message_id")
    async with runtime.session_factory() as session:
        message = await OutboxMessageRepository(session).get(
            organization_uuid,
            message_uuid,
        )
    payload = _maintenance_payload(
        message,
        organization_id=organization_uuid,
        event_type="ArtifactPromotionRecoveryRequested",
    )
    limit = _required_int(payload, "limit", minimum=1)
    results = await ArtifactPromotionTaskHandler(
        runtime,
        owner="taskiq-artifact-recovery",
    ).recover(organization_uuid, limit=limit)
    return {
        "organization_id": organization_id,
        "message_id": message_id,
        "processed": len(results),
        "results": list(results),
    }


@task_handler(
    name="review_platform.artifact_staged_cleanup",
    kind="artifact_capture",
    event_type="ArtifactStagedCleanupRequested",
    requires_auth_revalidation=False,
)
async def handle_artifact_staged_cleanup(
    *,
    organization_id: str,
    message_id: str,
) -> Mapping[str, Any]:
    return await ArtifactCleanupTaskHandler(_load_runtime())(
        organization_id=organization_id,
        message_id=message_id,
    )


__all__ = [
    "ARTIFACT_TASK_RUNTIME_FACTORY_ENV",
    "ArtifactCaptureTaskHandler",
    "ArtifactCleanupTaskHandler",
    "ArtifactPromotionTaskHandler",
    "ArtifactTaskError",
    "ArtifactTaskRuntime",
    "handle_artifact_capture",
    "handle_artifact_promotion",
    "handle_artifact_promotion_recovery",
    "handle_artifact_staged_cleanup",
]
