"""Tenant-scoped SQL adapters for US3 submissions, artifacts, and reviews."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.authorization import (
    AuthorizationGrant,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.ports.providers import JsonValue
from review_platform.application.request_context import RequestActor
from review_platform.application.services.artifact_capture import (
    ArtifactCaptureAuthorizationPort,
    ArtifactCaptureBundle,
    ArtifactCaptureCommand,
    ArtifactCaptureCredentialPort,
    ArtifactCaptureFailureRecord,
    ArtifactCaptureRepository,
    ArtifactPromotionCaptureRecord,
    ArtifactPromotionOutbox,
    ArtifactPromotionRequested,
    ArtifactReferenceCaptureRecord,
    ArtifactVersionCaptureRecord,
)
from review_platform.application.services.artifact_preflight import (
    ArchivedPreflightDenied,
    ArtifactCredentialBinding,
    ArtifactCredentialBindingPort,
    ArtifactPreflightArchiveGuard,
    ArtifactPreflightRepository,
    ArtifactProviderContractViolation,
    ArtifactProviderName,
    CourseRunHomeworkPreflightContext,
    FeedbackCapability,
    InvalidArtifactCredentialBinding,
    ReadCapability,
)
from review_platform.application.services.artifact_preflight import (
    ArtifactReferenceRecord as PreflightArtifactReferenceRecord,
)
from review_platform.application.services.artifact_preflight import (
    SubmissionRecord as PreflightSubmissionRecord,
)
from review_platform.application.services.review_iterations import (
    ReviewIterationRepository,
)
from review_platform.application.services.submissions import (
    ArtifactCaptureRequest,
    CaptureScheduler,
    EffectiveHomeworkPublication,
    SubmissionRecord,
    SubmissionRepository,
    SubmissionScopeAuthorization,
    SubmissionScopeDenied,
    SubmissionVersionRecord,
)
from review_platform.application.services.submissions import (
    ArtifactReferenceRecord as SubmissionArtifactReferenceRecord,
)
from review_platform.domain.primitives import canonical_json_sha256, sanitize_error, utc_now, uuid7
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import ExternalCredential
from review_platform.infrastructure.db.models.learning import Course, CourseMembership, CourseRun
from review_platform.infrastructure.db.models.operations import Operation, OperationAttempt
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.submission import (
    ArtifactPromotion,
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.object_storage.promotions import (
    ArtifactPromotionRepository,
    PromotionFailure,
    PromotionLease,
)


class SubmissionRepositoryError(RuntimeError):
    """A US3 persistence invariant was rejected."""


class InvalidSubmissionTransaction(SubmissionRepositoryError):
    pass


class SubmissionPersistenceConflict(SubmissionRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactPromotionStatus:
    organization_id: UUID
    promotion_id: UUID
    artifact_version_id: UUID
    operation_id: UUID
    state: str
    attempts: int
    max_attempts: int
    staged_key: str
    final_key: str
    error: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class SubmissionVersionHistory:
    id: UUID
    sequence: int
    homework_version_id: UUID
    artifact_reference_id: UUID
    artifact_version_id: UUID | None
    capture_operation_id: UUID | None
    submitted_at: datetime
    effective_deadline: datetime
    phase: str
    status: str
    revision: int


@dataclass(frozen=True, slots=True)
class ArtifactVersionHistory:
    id: UUID
    artifact_reference_id: UUID
    provider: str
    provider_version: str
    content_digest: str
    media_type: str
    byte_size: int
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class InitialReviewIterationHistory:
    id: UUID
    iteration_number: int
    submission_version_id: UUID
    artifact_version_id: UUID
    homework_version_id: UUID
    criterion_set_id: UUID
    effective_deadline: datetime
    origin: str
    status: str
    revision: int


@dataclass(frozen=True, slots=True)
class SubmissionHistoryProjection:
    submission_id: UUID
    course_run_id: UUID
    homework_id: UUID
    current_submission_version_id: UUID | None
    current_publication_id: None
    versions: tuple[SubmissionVersionHistory, ...]
    artifact_versions: tuple[ArtifactVersionHistory, ...]
    review_iterations: tuple[InitialReviewIterationHistory, ...]


class SqlArtifactPreflightRepository:
    async def lock_context(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        student_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> CourseRunHomeworkPreflightContext | None:
        session = _session(transaction)
        relation = await session.scalar(
            select(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == organization_id,
                CourseRunHomework.id == course_run_homework_id,
                CourseRunHomework.revision == expected_revision,
            )
            .with_for_update()
        )
        if relation is None:
            return None
        publication: CourseRunHomeworkPublication | None = None
        version: HomeworkVersion | None = None
        if relation.current_publication_id is not None:
            publication = await session.scalar(
                select(CourseRunHomeworkPublication).where(
                    CourseRunHomeworkPublication.organization_id == organization_id,
                    CourseRunHomeworkPublication.course_run_homework_id == relation.id,
                    CourseRunHomeworkPublication.id == relation.current_publication_id,
                )
            )
            if publication is not None:
                version = await session.scalar(
                    select(HomeworkVersion).where(
                        HomeworkVersion.organization_id == organization_id,
                        HomeworkVersion.homework_id == relation.homework_id,
                        HomeworkVersion.id == publication.homework_version_id,
                    )
                )
        enrolled = await session.scalar(
            select(CourseMembership.id)
            .where(
                CourseMembership.organization_id == organization_id,
                CourseMembership.course_run_id == relation.course_run_id,
                CourseMembership.user_id == student_id,
                CourseMembership.kind == "student",
                CourseMembership.status == "active",
            )
            .limit(1)
        )
        kinds = tuple(version.artifact_kinds) if version is not None else ()
        if any(kind not in {"github", "google_docs"} for kind in kinds):
            raise SubmissionPersistenceConflict("HomeworkVersion has unknown artifact kind")
        return CourseRunHomeworkPreflightContext(
            organization_id=relation.organization_id,
            course_run_homework_id=relation.id,
            course_run_id=relation.course_run_id,
            homework_id=relation.homework_id,
            current_publication_id=(publication.id if publication is not None else None),
            current_homework_version_id=(version.id if version is not None else None),
            allowed_artifact_kinds=cast(tuple[ArtifactProviderName, ...], kinds),
            revision=relation.revision,
            status=relation.status,
            student_enrolled=enrolled is not None,
        )

    async def get_or_create_submission(
        self,
        context: CourseRunHomeworkPreflightContext,
        *,
        student_id: UUID,
        new_submission_id: UUID,
        transaction: object,
    ) -> PreflightSubmissionRecord:
        session = _session(transaction)
        relation = await session.scalar(
            select(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == context.organization_id,
                CourseRunHomework.id == context.course_run_homework_id,
                CourseRunHomework.course_run_id == context.course_run_id,
                CourseRunHomework.homework_id == context.homework_id,
            )
            .with_for_update()
        )
        if relation is None:
            raise SubmissionPersistenceConflict("preflight context relation changed")
        existing = await _submission_by_scope(
            session,
            context.organization_id,
            context.course_run_id,
            context.homework_id,
            student_id,
            for_update=True,
        )
        if existing is not None:
            if existing.course_run_homework_id != context.course_run_homework_id:
                raise SubmissionPersistenceConflict("Submission points to another relation")
            return _preflight_submission_record(existing)
        proposed = Submission(
            id=new_submission_id,
            organization_id=context.organization_id,
            course_run_homework_id=context.course_run_homework_id,
            course_run_id=context.course_run_id,
            homework_id=context.homework_id,
            student_id=student_id,
            current_predeadline_version_id=None,
            revision=0,
        )
        try:
            async with session.begin_nested():
                session.add(proposed)
                await session.flush([proposed])
        except IntegrityError:
            winner = await _submission_by_scope(
                session,
                context.organization_id,
                context.course_run_id,
                context.homework_id,
                student_id,
                for_update=True,
            )
            if winner is None or winner.course_run_homework_id != context.course_run_homework_id:
                raise SubmissionPersistenceConflict(
                    "Submission unique race has no tenant-consistent winner"
                ) from None
            return _preflight_submission_record(winner)
        return _preflight_submission_record(proposed)

    async def upsert_available_reference(
        self,
        candidate: PreflightArtifactReferenceRecord,
        *,
        transaction: object,
    ) -> PreflightArtifactReferenceRecord:
        session = _session(transaction)
        organization = await session.scalar(
            select(Organization)
            .where(Organization.id == candidate.organization_id)
            .with_for_update()
        )
        if organization is None:
            raise ArtifactProviderContractViolation("artifact reference tenant was not found")
        credential = await session.scalar(
            select(ExternalCredential)
            .where(
                ExternalCredential.organization_id == candidate.organization_id,
                ExternalCredential.id == candidate.credential_binding_id,
                ExternalCredential.binding_version
                == candidate.credential_binding_version,
                ExternalCredential.provider == candidate.provider,
                ExternalCredential.status == "active",
            )
            .with_for_update()
        )
        if credential is None:
            raise InvalidArtifactCredentialBinding(
                "artifact reference exact active credential binding was not found"
            )
        existing = await session.scalar(
            select(ArtifactReference)
            .where(
                ArtifactReference.organization_id == candidate.organization_id,
                ArtifactReference.provider == candidate.provider,
                ArtifactReference.credential_binding_id
                == candidate.credential_binding_id,
                ArtifactReference.credential_binding_version
                == candidate.credential_binding_version,
                ArtifactReference.original_url == candidate.original_url,
            )
            .with_for_update()
        )
        if existing is None:
            row = ArtifactReference(
                id=candidate.artifact_reference_id,
                organization_id=candidate.organization_id,
                provider=candidate.provider,
                credential_binding_id=candidate.credential_binding_id,
                credential_binding_version=candidate.credential_binding_version,
                original_url=candidate.original_url,
                locator=dict(candidate.locator),
                read_capability=candidate.read_capability,
                feedback_capability=candidate.feedback_capability,
                last_checked_at=candidate.last_checked_at,
                revision=0,
            )
            session.add(row)
            await session.flush([row])
            return _preflight_reference_record(row)
        if dict(existing.locator or {}) != dict(candidate.locator):
            raise ArtifactProviderContractViolation(
                "canonical artifact URL resolved to a different locator"
            )
        changed = (
            existing.read_capability != candidate.read_capability
            or existing.feedback_capability != candidate.feedback_capability
            or existing.last_checked_at != candidate.last_checked_at
        )
        existing.read_capability = candidate.read_capability
        existing.feedback_capability = candidate.feedback_capability
        existing.last_checked_at = candidate.last_checked_at
        if changed:
            existing.revision += 1
        await session.flush([existing])
        return _preflight_reference_record(existing)


class SqlArtifactCredentialBindings:
    async def require_exact_active(
        self,
        organization_id: UUID,
        binding: ArtifactCredentialBinding,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        row = await session.scalar(
            select(ExternalCredential)
            .where(
                ExternalCredential.organization_id == organization_id,
                ExternalCredential.id == binding.credential_binding_id,
                ExternalCredential.binding_version == binding.credential_binding_version,
                ExternalCredential.status == "active",
            )
            .with_for_update()
        )
        if row is None or row.provider != binding.provider:
            raise InvalidArtifactCredentialBinding(
                "exact active artifact credential binding was not found"
            )


class SqlArtifactPreflightArchiveGuard:
    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        discovered = await session.scalar(
            select(CourseRun).where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
            )
        )
        if discovered is None:
            raise ArchivedPreflightDenied("tenant CourseRun was not found")
        course = await session.scalar(
            select(Course)
            .where(
                Course.organization_id == organization_id,
                Course.id == discovered.course_id,
            )
            .with_for_update()
        )
        course_run = await session.scalar(
            select(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
                CourseRun.course_id == discovered.course_id,
            )
            .with_for_update()
        )
        if course is None or course_run is None:
            raise ArchivedPreflightDenied("tenant course scope changed")
        if course.status == "archived" or course_run.status == "archived":
            raise ArchivedPreflightDenied("archived course scope rejects artifact preflight")


class SqlArtifactCaptureAuthorization:
    def __init__(self, authorizer: Authorizer) -> None:
        self._authorizer = authorizer

    async def authorize(
        self,
        *,
        actor: RequestActor,
        organization_id: UUID,
        artifact_reference_id: UUID,
        transaction: object,
    ) -> object:
        del artifact_reference_id, transaction
        return await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=AuthorizationPolicy(required_roles=frozenset({"student"})),
        )

    async def revalidate_for_commit(
        self,
        grant: object,
        *,
        transaction: object,
    ) -> None:
        if not isinstance(grant, AuthorizationGrant):
            raise SubmissionScopeDenied("artifact capture authorization grant is invalid")
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)


class SqlArtifactCaptureRepository:
    def __init__(self, *, id_factory: Callable[[], UUID] = uuid7) -> None:
        self._id_factory = id_factory

    async def lock_reference(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        *,
        transaction: object,
    ) -> ArtifactReferenceCaptureRecord | None:
        session = _session(transaction)
        row = await session.scalar(
            select(ArtifactReference)
            .where(
                ArtifactReference.organization_id == organization_id,
                ArtifactReference.id == artifact_reference_id,
            )
            .with_for_update()
        )
        return None if row is None else _capture_reference_record(row)

    async def find_by_reference_digest(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        content_digest: str,
        *,
        transaction: object,
    ) -> ArtifactCaptureBundle | None:
        return await _capture_bundle_by_digest(
            _session(transaction),
            organization_id,
            artifact_reference_id,
            content_digest,
        )

    async def record_success(
        self,
        *,
        command: ArtifactCaptureCommand,
        input_version: str,
        artifact_version: ArtifactVersionCaptureRecord,
        promotion: ArtifactPromotionCaptureRecord,
        attempt_metadata: Mapping[str, JsonValue],
        now: datetime,
        transaction: object,
    ) -> ArtifactCaptureBundle:
        session = _session(transaction)
        replay = await _capture_bundle_by_digest(
            session,
            command.organization_id,
            command.artifact_reference_id,
            artifact_version.content_digest,
        )
        if replay is not None:
            return _replayed_bundle(replay)
        if (
            artifact_version.organization_id != command.organization_id
            or artifact_version.artifact_reference_id != command.artifact_reference_id
            or promotion.organization_id != command.organization_id
            or promotion.artifact_version_id != artifact_version.artifact_version_id
            or promotion.operation_id != command.operation_id
        ):
            raise SubmissionPersistenceConflict("artifact capture bundle provenance mismatched")
        try:
            async with session.begin_nested():
                operation = await _capture_operation(
                    session,
                    command,
                    input_version=input_version,
                    now=now,
                )
                version_row = ArtifactVersion(
                    id=artifact_version.artifact_version_id,
                    organization_id=artifact_version.organization_id,
                    artifact_reference_id=artifact_version.artifact_reference_id,
                    provider_version=artifact_version.provider_version,
                    content_digest=artifact_version.content_digest,
                    object_key=artifact_version.object_key,
                    media_type=artifact_version.media_type,
                    byte_size=artifact_version.byte_size,
                    captured_at=artifact_version.captured_at,
                    artifact_metadata=dict(artifact_version.metadata),
                )
                session.add(version_row)
                await session.flush([version_row])
                attempt_number = await _next_attempt_number(
                    session,
                    command.organization_id,
                    command.operation_id,
                )
                attempt = OperationAttempt(
                    id=self._id_factory(),
                    organization_id=command.organization_id,
                    operation_id=command.operation_id,
                    attempt_number=attempt_number,
                    worker_identity=_attempt_identity(command, attempt_metadata),
                    started_at=now,
                    finished_at=now,
                    outcome="succeeded",
                    error_code=None,
                    sanitized_error=None,
                )
                session.add(attempt)
                await session.flush([attempt])
                promotion_row = ArtifactPromotion(
                    id=promotion.promotion_id,
                    organization_id=promotion.organization_id,
                    artifact_version_id=promotion.artifact_version_id,
                    operation_id=promotion.operation_id,
                    staged_key=promotion.staged_key,
                    final_key=promotion.final_key,
                    state="db_committed",
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    attempts=0,
                    max_attempts=promotion.max_attempts,
                    error_code=None,
                    sanitized_error=None,
                    revision=0,
                )
                session.add(promotion_row)
                operation.input_version = input_version
                operation.state = "processing"
                operation.revision += 1
                operation.updated_at = now
                operation.finished_at = None
                operation.error_code = None
                operation.sanitized_error = None
                await session.flush()
        except IntegrityError:
            winner = await _capture_bundle_by_digest(
                session,
                command.organization_id,
                command.artifact_reference_id,
                artifact_version.content_digest,
            )
            if winner is None:
                raise SubmissionPersistenceConflict(
                    "artifact capture unique race has no tenant-consistent winner"
                ) from None
            return _replayed_bundle(winner)
        return ArtifactCaptureBundle(
            artifact_version=artifact_version,
            promotion=promotion,
            operation_id=command.operation_id,
            operation_state="processing",
            attempt_number=attempt_number,
            replayed=False,
        )

    async def record_failure(
        self,
        *,
        command: ArtifactCaptureCommand,
        input_version: str,
        state: Literal["retryable_failed", "action_required"],
        error: Mapping[str, JsonValue],
        attempt_metadata: Mapping[str, JsonValue],
        now: datetime,
        transaction: object,
    ) -> ArtifactCaptureFailureRecord:
        if state not in {"retryable_failed", "action_required"}:
            raise SubmissionPersistenceConflict("unknown artifact capture failure state")
        session = _session(transaction)
        operation = await _capture_operation(
            session,
            command,
            input_version=input_version,
            now=now,
        )
        attempt_number = await _next_attempt_number(
            session,
            command.organization_id,
            command.operation_id,
        )
        bounded = sanitize_error(cast(Mapping[str, Any], error))
        session.add(
            OperationAttempt(
                id=self._id_factory(),
                organization_id=command.organization_id,
                operation_id=command.operation_id,
                attempt_number=attempt_number,
                worker_identity=_attempt_identity(command, attempt_metadata),
                started_at=now,
                finished_at=now,
                outcome=state,
                error_code=str(bounded.get("code") or "artifact_capture_failed"),
                sanitized_error=bounded,
            )
        )
        operation.input_version = input_version
        operation.state = state
        operation.revision += 1
        operation.updated_at = now
        operation.finished_at = now if state == "action_required" else None
        operation.error_code = str(bounded.get("code") or "artifact_capture_failed")
        operation.sanitized_error = bounded
        await session.flush()
        return ArtifactCaptureFailureRecord(
            organization_id=command.organization_id,
            operation_id=command.operation_id,
            operation_state=state,
            attempt_number=attempt_number,
            error=cast(Mapping[str, JsonValue], bounded),
        )


class SqlArtifactPromotionOutbox:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 5,
    ) -> None:
        self._clock = clock
        self._max_attempts = max_attempts

    async def append(
        self,
        event: ArtifactPromotionRequested,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        await OutboxService(OutboxMessageRepository(session), clock=self._clock).create(
            OutboxDraft(
                organization_id=event.organization_id,
                message_id=event.message_id,
                aggregate_type="artifact_promotion",
                aggregate_id=event.promotion_id,
                event_type="ArtifactPromotionRequested",
                payload_version=event.contract_version,
                payload={
                    "organization_id": str(event.organization_id),
                    "artifact_reference_id": str(event.artifact_reference_id),
                    "artifact_version_id": str(event.artifact_version_id),
                    "promotion_id": str(event.promotion_id),
                    "operation_id": str(event.operation_id),
                    "staged_key": event.staged_key,
                    "final_key": event.final_key,
                    "content_digest": event.content_digest,
                    "byte_size": event.byte_size,
                },
                available_at=self._clock(),
                max_attempts=self._max_attempts,
            )
        )


class SqlArtifactPromotionRepository:
    """Short-transaction durable leases and terminal Operation updates."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._id_factory = id_factory
        self._clock = clock

    async def claim(
        self,
        organization_id: UUID,
        promotion_id: UUID,
        *,
        owner: str,
        token: UUID,
        now: datetime,
        lease_seconds: int,
    ) -> PromotionLease | None:
        if not owner or len(owner) > 255 or not 1 <= lease_seconds <= 3600:
            raise SubmissionPersistenceConflict("promotion lease configuration is invalid")
        async with session_scope(self._session_factory) as session:
            promotion = await session.scalar(
                select(ArtifactPromotion)
                .where(
                    ArtifactPromotion.organization_id == organization_id,
                    ArtifactPromotion.id == promotion_id,
                )
                .with_for_update()
            )
            if promotion is None or not _promotion_claimable(promotion, now=now):
                return None
            if promotion.attempts >= promotion.max_attempts:
                await self._force_exhausted(session, promotion, now=now)
                return None
            recovering_expired_lease = promotion.state == "promoting"
            expired_owner = promotion.lease_owner
            version = await session.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.organization_id == organization_id,
                    ArtifactVersion.id == promotion.artifact_version_id,
                )
            )
            operation = await session.scalar(
                select(Operation)
                .where(
                    Operation.organization_id == organization_id,
                    Operation.id == promotion.operation_id,
                    Operation.kind == "artifact_capture",
                )
                .with_for_update()
            )
            if version is None or operation is None:
                raise SubmissionPersistenceConflict(
                    "promotion ArtifactVersion or Operation provenance is missing"
                )
            if recovering_expired_lease:
                attempt_number = await _next_attempt_number(
                    session,
                    organization_id,
                    promotion.operation_id,
                )
                expired_error = {
                    "code": "artifact_promotion_lease_expired",
                    "message": "Previous artifact promotion worker lease expired",
                }
                session.add(
                    OperationAttempt(
                        id=self._id_factory(),
                        organization_id=organization_id,
                        operation_id=promotion.operation_id,
                        attempt_number=attempt_number,
                        worker_identity=_expired_promotion_worker_identity(
                            expired_owner,
                            promotion.id,
                        ),
                        started_at=promotion.lease_expires_at or now,
                        finished_at=now,
                        outcome="retryable_failed",
                        error_code="artifact_promotion_lease_expired",
                        sanitized_error=expired_error,
                    )
                )
            expires_at = now + timedelta(seconds=lease_seconds)
            promotion.state = "promoting"
            promotion.lease_owner = owner
            promotion.lease_token = token
            promotion.lease_expires_at = expires_at
            promotion.attempts += 1
            promotion.error_code = None
            promotion.sanitized_error = None
            promotion.revision += 1
            operation.state = "processing"
            operation.updated_at = now
            operation.finished_at = None
            operation.error_code = None
            operation.sanitized_error = None
            operation.revision += 1
            await session.flush()
            return PromotionLease(
                organization_id=promotion.organization_id,
                promotion_id=promotion.id,
                artifact_version_id=promotion.artifact_version_id,
                operation_id=promotion.operation_id,
                staged_key=promotion.staged_key,
                final_key=promotion.final_key,
                content_digest=version.content_digest,
                byte_size=version.byte_size,
                media_type=version.media_type,
                owner=owner,
                token=token,
                expires_at=expires_at,
                attempts=promotion.attempts,
                max_attempts=promotion.max_attempts,
            )

    async def complete(self, lease: PromotionLease, *, now: datetime) -> bool:
        async with session_scope(self._session_factory) as session:
            promotion = await _locked_promotion(session, lease)
            if not _active_promotion_lease(promotion, lease, now=now):
                return False
            assert promotion is not None
            operation = await _locked_promotion_operation(session, lease)
            if operation is None:
                raise SubmissionPersistenceConflict("promotion Operation provenance changed")
            attempt_number = await _next_attempt_number(
                session,
                lease.organization_id,
                lease.operation_id,
            )
            session.add(
                OperationAttempt(
                    id=self._id_factory(),
                    organization_id=lease.organization_id,
                    operation_id=lease.operation_id,
                    attempt_number=attempt_number,
                    worker_identity=_promotion_worker_identity(lease),
                    started_at=now,
                    finished_at=now,
                    outcome="succeeded",
                    error_code=None,
                    sanitized_error=None,
                )
            )
            promotion.state = "promoted"
            promotion.lease_owner = None
            promotion.lease_token = None
            promotion.lease_expires_at = None
            promotion.error_code = None
            promotion.sanitized_error = None
            promotion.revision += 1
            operation.state = "succeeded"
            operation.finished_at = now
            operation.updated_at = now
            operation.error_code = None
            operation.sanitized_error = None
            operation.revision += 1
            await session.flush()
            return True

    async def fail(
        self,
        lease: PromotionLease,
        *,
        error: Mapping[str, object],
        available_at: datetime,
        exhausted: bool,
    ) -> PromotionFailure | None:
        now = self._clock()
        async with session_scope(self._session_factory) as session:
            promotion = await _locked_promotion(session, lease)
            if not _active_promotion_lease(promotion, lease, now=now):
                return None
            assert promotion is not None
            operation = await _locked_promotion_operation(session, lease)
            if operation is None:
                raise SubmissionPersistenceConflict("promotion Operation provenance changed")
            bounded = sanitize_error(cast(Mapping[str, Any], error))
            state = "action_required" if exhausted else "db_committed"
            attempt_state = "action_required" if exhausted else "retryable_failed"
            attempt_number = await _next_attempt_number(
                session,
                lease.organization_id,
                lease.operation_id,
            )
            session.add(
                OperationAttempt(
                    id=self._id_factory(),
                    organization_id=lease.organization_id,
                    operation_id=lease.operation_id,
                    attempt_number=attempt_number,
                    worker_identity=_promotion_worker_identity(lease),
                    started_at=now,
                    finished_at=now,
                    outcome=attempt_state,
                    error_code=str(bounded.get("code") or "artifact_promotion_failed"),
                    sanitized_error=bounded,
                )
            )
            promotion.state = state
            promotion.lease_owner = None
            promotion.lease_token = None
            # There is no dedicated retry-at column in the frozen model. For a
            # retryable db_committed intent, the cleared lease expiry is the
            # earliest next claim time.
            promotion.lease_expires_at = None if exhausted else available_at
            promotion.error_code = str(
                bounded.get("code") or "artifact_promotion_failed"
            )
            promotion.sanitized_error = bounded
            promotion.revision += 1
            operation.state = attempt_state
            operation.updated_at = now
            operation.finished_at = now if exhausted else None
            operation.error_code = promotion.error_code
            operation.sanitized_error = bounded
            operation.revision += 1
            await session.flush()
            return PromotionFailure(
                state=state,
                attempts=promotion.attempts,
                max_attempts=promotion.max_attempts,
                error=bounded,
            )

    async def has_live_staged_intent(
        self,
        organization_id: UUID,
        staged_key: str,
    ) -> bool:
        async with self._session_factory() as session:
            found = await session.scalar(
                select(ArtifactPromotion.id)
                .where(
                    ArtifactPromotion.organization_id == organization_id,
                    ArtifactPromotion.staged_key == staged_key,
                    ArtifactPromotion.state.in_(("staged", "db_committed", "promoting")),
                )
                .limit(1)
            )
            return found is not None

    async def list_recoverable(
        self,
        organization_id: UUID,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[UUID, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("promotion recovery limit must be between 1 and 1000")
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ArtifactPromotion.id)
                .where(
                    ArtifactPromotion.organization_id == organization_id,
                    or_(
                        (
                            (ArtifactPromotion.state == "db_committed")
                            & or_(
                                ArtifactPromotion.lease_expires_at.is_(None),
                                ArtifactPromotion.lease_expires_at <= now,
                            )
                        ),
                        (
                            (ArtifactPromotion.state == "promoting")
                            & (ArtifactPromotion.lease_expires_at <= now)
                        ),
                    ),
                )
                .order_by(
                    ArtifactPromotion.lease_expires_at,
                    ArtifactPromotion.created_at,
                    ArtifactPromotion.id,
                )
                .limit(limit)
            )
            return tuple(rows)

    async def status(
        self,
        organization_id: UUID,
        promotion_id: UUID,
    ) -> ArtifactPromotionStatus | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ArtifactPromotion).where(
                    ArtifactPromotion.organization_id == organization_id,
                    ArtifactPromotion.id == promotion_id,
                )
            )
            if row is None:
                return None
            return ArtifactPromotionStatus(
                organization_id=row.organization_id,
                promotion_id=row.id,
                artifact_version_id=row.artifact_version_id,
                operation_id=row.operation_id,
                state=row.state,
                attempts=row.attempts,
                max_attempts=row.max_attempts,
                staged_key=row.staged_key,
                final_key=row.final_key,
                error=(
                    cast(Mapping[str, object], dict(row.sanitized_error))
                    if row.sanitized_error is not None
                    else None
                ),
            )

    async def _force_exhausted(
        self,
        session: AsyncSession,
        promotion: ArtifactPromotion,
        *,
        now: datetime,
    ) -> None:
        was_promoting = promotion.state == "promoting"
        expired_owner = promotion.lease_owner
        expired_at = promotion.lease_expires_at
        promotion.state = "action_required"
        promotion.lease_owner = None
        promotion.lease_token = None
        promotion.lease_expires_at = None
        promotion.error_code = "artifact_promotion_attempts_exhausted"
        promotion.sanitized_error = {
            "code": "artifact_promotion_attempts_exhausted",
            "message": "Artifact promotion exhausted its retry budget",
        }
        promotion.revision += 1
        operation = await session.scalar(
            select(Operation)
            .where(
                Operation.organization_id == promotion.organization_id,
                Operation.id == promotion.operation_id,
            )
            .with_for_update()
        )
        if operation is not None:
            if was_promoting:
                attempt_number = await _next_attempt_number(
                    session,
                    promotion.organization_id,
                    promotion.operation_id,
                )
                session.add(
                    OperationAttempt(
                        id=self._id_factory(),
                        organization_id=promotion.organization_id,
                        operation_id=promotion.operation_id,
                        attempt_number=attempt_number,
                        worker_identity=_expired_promotion_worker_identity(
                            expired_owner,
                            promotion.id,
                        ),
                        started_at=expired_at or now,
                        finished_at=now,
                        outcome="action_required",
                        error_code=promotion.error_code,
                        sanitized_error=promotion.sanitized_error,
                    )
                )
            operation.state = "action_required"
            operation.updated_at = now
            operation.finished_at = now
            operation.error_code = promotion.error_code
            operation.sanitized_error = promotion.sanitized_error
            operation.revision += 1
        await session.flush()


class SqlSubmissionRepository:
    async def lock_submission(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> SubmissionRecord | None:
        session = _session(transaction)
        row = await session.scalar(
            select(Submission)
            .where(
                Submission.organization_id == organization_id,
                Submission.id == submission_id,
                Submission.revision == expected_revision,
            )
            .with_for_update()
        )
        return None if row is None else _submission_record(row)

    async def lock_current_publication(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        homework_id: UUID,
        *,
        transaction: object,
    ) -> EffectiveHomeworkPublication | None:
        session = _session(transaction)
        relation = await session.scalar(
            select(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == organization_id,
                CourseRunHomework.course_run_id == course_run_id,
                CourseRunHomework.homework_id == homework_id,
            )
            .with_for_update()
        )
        if relation is None or relation.current_publication_id is None:
            return None
        publication = await session.scalar(
            select(CourseRunHomeworkPublication).where(
                CourseRunHomeworkPublication.organization_id == organization_id,
                CourseRunHomeworkPublication.course_run_homework_id == relation.id,
                CourseRunHomeworkPublication.id == relation.current_publication_id,
                CourseRunHomeworkPublication.homework_id == homework_id,
            )
        )
        if publication is None:
            return None
        return EffectiveHomeworkPublication(
            organization_id=organization_id,
            course_run_homework_id=relation.id,
            publication_id=publication.id,
            course_run_id=course_run_id,
            homework_id=homework_id,
            homework_version_id=publication.homework_version_id,
            submission_deadline=publication.submission_deadline,
        )

    async def get_artifact_reference(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        *,
        transaction: object,
    ) -> SubmissionArtifactReferenceRecord | None:
        row = await _session(transaction).scalar(
            select(ArtifactReference).where(
                ArtifactReference.organization_id == organization_id,
                ArtifactReference.id == artifact_reference_id,
            )
        )
        if row is None:
            return None
        return SubmissionArtifactReferenceRecord(
            organization_id=row.organization_id,
            artifact_reference_id=row.id,
            provider=row.provider,
            credential_binding_id=row.credential_binding_id,
            credential_binding_version=row.credential_binding_version,
            usable=row.read_capability == "available" and row.locator is not None,
        )

    async def next_version_sequence(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        transaction: object,
    ) -> int:
        session = _session(transaction)
        parent = await session.scalar(
            select(Submission)
            .where(
                Submission.organization_id == organization_id,
                Submission.id == submission_id,
            )
            .with_for_update()
        )
        if parent is None:
            raise SubmissionPersistenceConflict("tenant Submission was not found")
        latest = await session.scalar(
            select(SubmissionVersion.sequence)
            .where(
                SubmissionVersion.organization_id == organization_id,
                SubmissionVersion.submission_id == submission_id,
            )
            .order_by(SubmissionVersion.sequence.desc(), SubmissionVersion.id.desc())
            .limit(1)
            .with_for_update()
        )
        return 1 if latest is None else latest + 1

    async def append_version(
        self,
        version: SubmissionVersionRecord,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        parent = await session.scalar(
            select(Submission)
            .where(
                Submission.organization_id == version.organization_id,
                Submission.id == version.submission_id,
            )
            .with_for_update()
        )
        if parent is None:
            raise SubmissionPersistenceConflict("tenant Submission for version was not found")
        reference = await session.scalar(
            select(ArtifactReference.id).where(
                ArtifactReference.organization_id == version.organization_id,
                ArtifactReference.id == version.artifact_reference_id,
                ArtifactReference.read_capability == "available",
            )
        )
        if reference is None:
            raise SubmissionPersistenceConflict("usable tenant ArtifactReference was not found")
        session.add(
            SubmissionVersion(
                id=version.version_id,
                organization_id=version.organization_id,
                submission_id=version.submission_id,
                course_run_id=parent.course_run_id,
                homework_id=parent.homework_id,
                sequence=version.sequence,
                homework_version_id=version.homework_version_id,
                artifact_reference_id=version.artifact_reference_id,
                artifact_version_id=None,
                submitted_at=version.submitted_at,
                effective_deadline=version.effective_deadline,
                phase=version.phase,
                status=version.status,
                capture_operation_id=version.capture_operation_id,
                revision=version.revision,
            )
        )
        # T084 schedules the referenced capture Operation immediately after
        # append_version in the same caller-owned transaction.  Delaying this
        # flush lets SQLAlchemy order Operation before the FK-dependent row.

    async def mark_superseded(
        self,
        organization_id: UUID,
        version_id: UUID,
        *,
        transaction: object,
    ) -> None:
        result = await _session(transaction).execute(
            update(SubmissionVersion)
            .where(
                SubmissionVersion.organization_id == organization_id,
                SubmissionVersion.id == version_id,
                SubmissionVersion.status != "superseded",
            )
            .values(
                status="superseded",
                revision=SubmissionVersion.revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        if result.rowcount != 1:
            raise SubmissionPersistenceConflict("SubmissionVersion could not be superseded")

    async def compare_and_set_submission(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        expected_revision: int,
        current_predeadline_version_id: UUID | None,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        if current_predeadline_version_id is not None:
            version = await session.scalar(
                select(SubmissionVersion.id).where(
                    SubmissionVersion.organization_id == organization_id,
                    SubmissionVersion.submission_id == submission_id,
                    SubmissionVersion.id == current_predeadline_version_id,
                )
            )
            if version is None:
                return False
        result = await session.execute(
            update(Submission)
            .where(
                Submission.organization_id == organization_id,
                Submission.id == submission_id,
                Submission.revision == expected_revision,
            )
            .values(
                current_predeadline_version_id=current_predeadline_version_id,
                revision=expected_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1

    async def history(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        transaction: object,
    ) -> SubmissionHistoryProjection | None:
        session = _session(transaction)
        submission = await session.scalar(
            select(Submission).where(
                Submission.organization_id == organization_id,
                Submission.id == submission_id,
            )
        )
        if submission is None:
            return None
        versions = (
            await session.scalars(
                select(SubmissionVersion)
                .where(
                    SubmissionVersion.organization_id == organization_id,
                    SubmissionVersion.submission_id == submission_id,
                )
                .order_by(SubmissionVersion.sequence, SubmissionVersion.id)
            )
        ).all()
        reference_ids = {row.artifact_reference_id for row in versions}
        artifact_ids = {row.artifact_version_id for row in versions if row.artifact_version_id}
        artifact_rows: Sequence[ArtifactVersion] = ()
        if artifact_ids:
            artifact_rows = (
                await session.scalars(
                    select(ArtifactVersion)
                    .where(
                        ArtifactVersion.organization_id == organization_id,
                        ArtifactVersion.id.in_(artifact_ids),
                    )
                    .order_by(ArtifactVersion.captured_at, ArtifactVersion.id)
                )
            ).all()
        references: dict[UUID, ArtifactReference] = {}
        if reference_ids:
            reference_rows = (
                await session.scalars(
                    select(ArtifactReference).where(
                        ArtifactReference.organization_id == organization_id,
                        ArtifactReference.id.in_(reference_ids),
                    )
                )
            ).all()
            references = {row.id: row for row in reference_rows}
        iterations = (
            (
                await session.scalars(
                    select(ReviewIteration)
                    .where(
                        ReviewIteration.organization_id == organization_id,
                        ReviewIteration.submission_version_id.in_([row.id for row in versions]),
                        ReviewIteration.origin.in_(("initial", "resubmission")),
                    )
                    .order_by(
                        ReviewIteration.iteration_number,
                        ReviewIteration.id,
                    )
                )
            ).all()
            if versions
            else []
        )
        return SubmissionHistoryProjection(
            submission_id=submission.id,
            course_run_id=submission.course_run_id,
            homework_id=submission.homework_id,
            current_submission_version_id=submission.current_predeadline_version_id,
            current_publication_id=None,
            versions=tuple(_submission_version_history(row) for row in versions),
            artifact_versions=tuple(
                _artifact_version_history(row, references=references) for row in artifact_rows
            ),
            review_iterations=tuple(_review_iteration_history(row) for row in iterations),
        )


class SqlSubmissionScopeAuthorization:
    async def require_owner_and_enrollment(
        self,
        *,
        actor: RequestActor,
        submission: SubmissionRecord,
        publication: EffectiveHomeworkPublication,
        transaction: object,
    ) -> None:
        if (
            actor.organization_id != submission.organization_id
            or actor.user_id != submission.student_id
            or "student" not in actor.roles
        ):
            raise SubmissionScopeDenied("actor is not the tenant Submission owner")
        enrolled = await _session(transaction).scalar(
            select(CourseMembership.id)
            .where(
                CourseMembership.organization_id == submission.organization_id,
                CourseMembership.course_run_id == publication.course_run_id,
                CourseMembership.user_id == submission.student_id,
                CourseMembership.kind == "student",
                CourseMembership.status == "active",
            )
            .limit(1)
        )
        if enrolled is None:
            raise SubmissionScopeDenied("student is not enrolled in the exact CourseRun")


class SqlCaptureScheduler:
    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 5,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._max_attempts = max_attempts

    async def schedule(
        self,
        request: ArtifactCaptureRequest,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        if request.credential_binding_version < 1:
            raise SubmissionPersistenceConflict(
                "capture scheduling credential binding version must be positive"
            )
        reference = await session.scalar(
            select(ArtifactReference)
            .where(
                ArtifactReference.organization_id == request.organization_id,
                ArtifactReference.id == request.artifact_reference_id,
                ArtifactReference.provider == request.provider,
                ArtifactReference.credential_binding_id
                == request.credential_binding_id,
                ArtifactReference.credential_binding_version
                == request.credential_binding_version,
            )
            .with_for_update()
        )
        credential = await session.scalar(
            select(ExternalCredential)
            .where(
                ExternalCredential.organization_id == request.organization_id,
                ExternalCredential.id == request.credential_binding_id,
                ExternalCredential.binding_version
                == request.credential_binding_version,
                ExternalCredential.provider == request.provider,
                ExternalCredential.status == "active",
            )
            .with_for_update()
        )
        if reference is None or credential is None:
            raise SubmissionPersistenceConflict(
                "capture request reference or exact active credential provenance mismatched"
            )
        payload = {
            "organization_id": str(request.organization_id),
            "operation_id": str(request.operation_id),
            "submission_id": str(request.submission_id),
            "submission_version_id": str(request.submission_version_id),
            "artifact_reference_id": str(request.artifact_reference_id),
            "provider": request.provider,
            "credential_binding_id": str(request.credential_binding_id),
            "credential_binding_version": request.credential_binding_version,
            "course_run_id": str(request.course_run_id),
            "homework_id": str(request.homework_id),
            "homework_version_id": str(request.homework_version_id),
            "course_run_homework_id": str(request.course_run_homework_id),
            "homework_publication_id": str(request.homework_publication_id),
            "actor": {
                "type": "user",
                "user_id": str(request.actor_user_id),
                "roles": ["student"],
                "membership_revision": request.actor_membership_revision,
                "auth_epoch": request.actor_auth_epoch,
            },
        }
        input_version = f"artifact-capture-request:1.1.0:{canonical_json_sha256(payload)}"
        existing = await session.scalar(
            select(Operation)
            .where(
                Operation.organization_id == request.organization_id,
                Operation.id == request.operation_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.kind != "artifact_capture" or existing.input_version != input_version:
                raise SubmissionPersistenceConflict("capture Operation provenance mismatched")
            await _flush_capture_dependencies(
                session,
                operation_id=request.operation_id,
            )
            return
        now = self._clock()
        operation = Operation(
            id=request.operation_id,
            organization_id=request.organization_id,
            kind="artifact_capture",
            input_version=input_version,
            state="pending",
            revision=0,
            created_at=now,
            updated_at=now,
            finished_at=None,
            error_code=None,
            sanitized_error=None,
        )
        session.add(operation)
        # Explicit ordering is required because autoflush is disabled and the
        # mapped graph intentionally has no ORM relationship that can order
        # the Operation FK and Submission's current-version self-reference.
        await session.flush([operation])
        await _flush_capture_dependencies(
            session,
            operation_id=request.operation_id,
        )
        await OutboxService(
            OutboxMessageRepository(session),
            token_factory=self._id_factory,
            clock=self._clock,
        ).create(
            OutboxDraft(
                organization_id=request.organization_id,
                message_id=self._id_factory(),
                aggregate_type="operation",
                aggregate_id=request.operation_id,
                event_type="ArtifactCaptureRequested",
                payload_version="1.1.0",
                payload=payload,
                available_at=now,
                max_attempts=self._max_attempts,
            )
        )


async def _flush_capture_dependencies(
    session: AsyncSession,
    *,
    operation_id: UUID,
) -> None:
    pending_versions = [
        candidate
        for candidate in session.new
        if isinstance(candidate, SubmissionVersion)
        and candidate.capture_operation_id == operation_id
    ]
    if pending_versions:
        # Operation is already durable inside this same uncommitted unit of
        # work. Flushing only its dependent rows makes the later current-
        # version CAS FK-safe without exposing any partial state.
        await session.flush(pending_versions)


class SqlReviewIterationRepository:
    """Session-bound adapter matching the T085 repository lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_submission_version(
        self,
        organization_id: UUID,
        submission_version_id: UUID,
        *,
        for_update: bool,
    ) -> SubmissionVersion | None:
        statement = select(SubmissionVersion).where(
            SubmissionVersion.organization_id == organization_id,
            SubmissionVersion.id == submission_version_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(SubmissionVersion | None, await self._session.scalar(statement))

    async def get_submission(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        for_update: bool,
    ) -> Submission | None:
        statement = select(Submission).where(
            Submission.organization_id == organization_id,
            Submission.id == submission_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(Submission | None, await self._session.scalar(statement))

    async def get_course_run(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        for_update: bool,
    ) -> CourseRun | None:
        statement = select(CourseRun).where(
            CourseRun.organization_id == organization_id,
            CourseRun.id == course_run_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(CourseRun | None, await self._session.scalar(statement))

    async def get_artifact_version(
        self,
        organization_id: UUID,
        artifact_version_id: UUID,
    ) -> ArtifactVersion | None:
        return cast(
            ArtifactVersion | None,
            await self._session.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.organization_id == organization_id,
                    ArtifactVersion.id == artifact_version_id,
                )
            ),
        )

    async def get_homework_version(
        self,
        organization_id: UUID,
        homework_version_id: UUID,
    ) -> HomeworkVersion | None:
        return cast(
            HomeworkVersion | None,
            await self._session.scalar(
                select(HomeworkVersion).where(
                    HomeworkVersion.organization_id == organization_id,
                    HomeworkVersion.id == homework_version_id,
                )
            ),
        )

    async def get_criterion_set_for_version(
        self,
        organization_id: UUID,
        homework_version_id: UUID,
    ) -> CriterionSet | None:
        return cast(
            CriterionSet | None,
            await self._session.scalar(
                select(CriterionSet).where(
                    CriterionSet.organization_id == organization_id,
                    CriterionSet.homework_version_id == homework_version_id,
                )
            ),
        )

    async def lock_or_create_case(self, candidate: ReviewCase) -> ReviewCase:
        course_run = await self.get_course_run(
            candidate.organization_id,
            candidate.course_run_id,
            for_update=True,
        )
        homework = await self._session.scalar(
            select(Homework)
            .where(
                Homework.organization_id == candidate.organization_id,
                Homework.id == candidate.homework_id,
            )
            .with_for_update()
        )
        if course_run is None or homework is None or course_run.course_id != homework.course_id:
            raise SubmissionPersistenceConflict("ReviewCase tenant course scope mismatched")
        existing = await _review_case_by_scope(
            self._session,
            candidate.organization_id,
            candidate.course_run_id,
            candidate.homework_id,
            candidate.student_id,
            for_update=True,
        )
        if existing is not None:
            return existing
        try:
            async with self._session.begin_nested():
                self._session.add(candidate)
                await self._session.flush([candidate])
        except IntegrityError:
            winner = await _review_case_by_scope(
                self._session,
                candidate.organization_id,
                candidate.course_run_id,
                candidate.homework_id,
                candidate.student_id,
                for_update=True,
            )
            if winner is None:
                raise SubmissionPersistenceConflict(
                    "ReviewCase unique race has no tenant-consistent winner"
                ) from None
            return winner
        return candidate

    async def list_iterations(
        self,
        organization_id: UUID,
        review_case_id: UUID,
    ) -> Sequence[ReviewIteration]:
        return (
            await self._session.scalars(
                select(ReviewIteration)
                .where(
                    ReviewIteration.organization_id == organization_id,
                    ReviewIteration.review_case_id == review_case_id,
                )
                .order_by(ReviewIteration.iteration_number, ReviewIteration.id)
            )
        ).all()

    async def find_by_submission_version(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        submission_version_id: UUID,
    ) -> ReviewIteration | None:
        return cast(
            ReviewIteration | None,
            await self._session.scalar(
                select(ReviewIteration).where(
                    ReviewIteration.organization_id == organization_id,
                    ReviewIteration.review_case_id == review_case_id,
                    ReviewIteration.submission_version_id == submission_version_id,
                )
            ),
        )

    async def add_iteration(self, iteration: ReviewIteration) -> ReviewIteration:
        self._session.add(iteration)
        await self._session.flush([iteration])
        return iteration

    async def compare_and_set_current(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        expected_revision: int,
        expected_current_iteration_id: UUID | None,
        new_iteration_id: UUID,
    ) -> bool:
        iteration = await self._session.scalar(
            select(ReviewIteration.id).where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.review_case_id == review_case_id,
                ReviewIteration.id == new_iteration_id,
            )
        )
        if iteration is None:
            return False
        current_predicate = (
            ReviewCase.current_iteration_id.is_(None)
            if expected_current_iteration_id is None
            else ReviewCase.current_iteration_id == expected_current_iteration_id
        )
        result = await self._session.execute(
            update(ReviewCase)
            .where(
                ReviewCase.organization_id == organization_id,
                ReviewCase.id == review_case_id,
                ReviewCase.revision == expected_revision,
                current_predicate,
            )
            .values(
                current_iteration_id=new_iteration_id,
                revision=expected_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1


async def _submission_by_scope(
    session: AsyncSession,
    organization_id: UUID,
    course_run_id: UUID,
    homework_id: UUID,
    student_id: UUID,
    *,
    for_update: bool,
) -> Submission | None:
    statement = select(Submission).where(
        Submission.organization_id == organization_id,
        Submission.course_run_id == course_run_id,
        Submission.homework_id == homework_id,
        Submission.student_id == student_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return cast(Submission | None, await session.scalar(statement))


async def _review_case_by_scope(
    session: AsyncSession,
    organization_id: UUID,
    course_run_id: UUID,
    homework_id: UUID,
    student_id: UUID,
    *,
    for_update: bool,
) -> ReviewCase | None:
    statement = select(ReviewCase).where(
        ReviewCase.organization_id == organization_id,
        ReviewCase.course_run_id == course_run_id,
        ReviewCase.homework_id == homework_id,
        ReviewCase.student_id == student_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return cast(ReviewCase | None, await session.scalar(statement))


async def _capture_operation(
    session: AsyncSession,
    command: ArtifactCaptureCommand,
    *,
    input_version: str,
    now: datetime,
) -> Operation:
    operation = await session.scalar(
        select(Operation)
        .where(
            Operation.organization_id == command.organization_id,
            Operation.id == command.operation_id,
        )
        .with_for_update()
    )
    if operation is None:
        operation = Operation(
            id=command.operation_id,
            organization_id=command.organization_id,
            kind="artifact_capture",
            input_version=input_version,
            state="pending",
            revision=0,
            created_at=now,
            updated_at=now,
            finished_at=None,
            error_code=None,
            sanitized_error=None,
        )
        session.add(operation)
        await session.flush([operation])
    elif operation.kind != "artifact_capture":
        raise SubmissionPersistenceConflict("Operation kind is not artifact_capture")
    return operation


async def _next_attempt_number(
    session: AsyncSession,
    organization_id: UUID,
    operation_id: UUID,
) -> int:
    latest = await session.scalar(
        select(OperationAttempt.attempt_number)
        .where(
            OperationAttempt.organization_id == organization_id,
            OperationAttempt.operation_id == operation_id,
        )
        .order_by(OperationAttempt.attempt_number.desc(), OperationAttempt.id.desc())
        .limit(1)
        .with_for_update()
    )
    return 1 if latest is None else latest + 1


async def _capture_bundle_by_digest(
    session: AsyncSession,
    organization_id: UUID,
    artifact_reference_id: UUID,
    content_digest: str,
) -> ArtifactCaptureBundle | None:
    result = await session.execute(
        select(ArtifactVersion, ArtifactPromotion, Operation, ArtifactReference.provider)
        .join(
            ArtifactPromotion,
            (ArtifactPromotion.organization_id == ArtifactVersion.organization_id)
            & (ArtifactPromotion.artifact_version_id == ArtifactVersion.id),
        )
        .join(
            Operation,
            (Operation.organization_id == ArtifactPromotion.organization_id)
            & (Operation.id == ArtifactPromotion.operation_id),
        )
        .join(
            ArtifactReference,
            (ArtifactReference.organization_id == ArtifactVersion.organization_id)
            & (ArtifactReference.id == ArtifactVersion.artifact_reference_id),
        )
        .where(
            ArtifactVersion.organization_id == organization_id,
            ArtifactVersion.artifact_reference_id == artifact_reference_id,
            ArtifactVersion.content_digest == content_digest,
        )
        .with_for_update()
    )
    row = result.one_or_none()
    if row is None:
        return None
    version, promotion, operation, provider = row
    if provider not in {"github", "google_docs"}:
        raise SubmissionPersistenceConflict("ArtifactReference provider is invalid")
    if promotion.state != "db_committed":
        raise SubmissionPersistenceConflict(
            "capture replay requires a db_committed promotion intent"
        )
    attempt_number = await session.scalar(
        select(func.max(OperationAttempt.attempt_number)).where(
            OperationAttempt.organization_id == organization_id,
            OperationAttempt.operation_id == operation.id,
        )
    )
    return ArtifactCaptureBundle(
        artifact_version=ArtifactVersionCaptureRecord(
            organization_id=version.organization_id,
            artifact_version_id=version.id,
            artifact_reference_id=version.artifact_reference_id,
            provider=cast(ArtifactProviderName, provider),
            provider_version=version.provider_version,
            content_digest=version.content_digest,
            object_key=version.object_key,
            media_type=version.media_type,
            byte_size=version.byte_size,
            captured_at=version.captured_at,
            metadata=cast(Mapping[str, JsonValue], dict(version.artifact_metadata)),
        ),
        promotion=ArtifactPromotionCaptureRecord(
            organization_id=promotion.organization_id,
            promotion_id=promotion.id,
            artifact_version_id=promotion.artifact_version_id,
            operation_id=promotion.operation_id,
            staged_key=promotion.staged_key,
            final_key=promotion.final_key,
            state="db_committed",
            attempts=promotion.attempts,
            max_attempts=promotion.max_attempts,
        ),
        operation_id=operation.id,
        operation_state=operation.state,
        attempt_number=int(attempt_number or 0),
        replayed=True,
    )


def _promotion_claimable(promotion: ArtifactPromotion, *, now: datetime) -> bool:
    if promotion.state == "db_committed":
        return promotion.lease_expires_at is None or promotion.lease_expires_at <= now
    return (
        promotion.state == "promoting"
        and promotion.lease_expires_at is not None
        and promotion.lease_expires_at <= now
    )


async def _locked_promotion(
    session: AsyncSession,
    lease: PromotionLease,
) -> ArtifactPromotion | None:
    return cast(
        ArtifactPromotion | None,
        await session.scalar(
            select(ArtifactPromotion)
            .where(
                ArtifactPromotion.organization_id == lease.organization_id,
                ArtifactPromotion.id == lease.promotion_id,
                ArtifactPromotion.artifact_version_id == lease.artifact_version_id,
                ArtifactPromotion.operation_id == lease.operation_id,
            )
            .with_for_update()
        ),
    )


def _active_promotion_lease(
    promotion: ArtifactPromotion | None,
    lease: PromotionLease,
    *,
    now: datetime,
) -> bool:
    return (
        promotion is not None
        and promotion.state == "promoting"
        and promotion.lease_owner == lease.owner
        and promotion.lease_token == lease.token
        and promotion.lease_expires_at == lease.expires_at
        and promotion.lease_expires_at is not None
        and promotion.lease_expires_at > now
    )


async def _locked_promotion_operation(
    session: AsyncSession,
    lease: PromotionLease,
) -> Operation | None:
    return cast(
        Operation | None,
        await session.scalar(
            select(Operation)
            .where(
                Operation.organization_id == lease.organization_id,
                Operation.id == lease.operation_id,
                Operation.kind == "artifact_capture",
            )
            .with_for_update()
        ),
    )


def _promotion_worker_identity(lease: PromotionLease) -> str:
    value = f"{lease.owner}|artifact-promotion={lease.promotion_id}"
    if len(value) > 255:
        raise SubmissionPersistenceConflict("promotion worker provenance exceeds storage bound")
    return value


def _expired_promotion_worker_identity(owner: str | None, promotion_id: UUID) -> str:
    value = f"{owner or 'unknown-worker'}|expired-artifact-promotion={promotion_id}"
    if len(value) > 255:
        raise SubmissionPersistenceConflict("expired promotion provenance exceeds storage bound")
    return value


def _replayed_bundle(bundle: ArtifactCaptureBundle) -> ArtifactCaptureBundle:
    return ArtifactCaptureBundle(
        artifact_version=bundle.artifact_version,
        promotion=bundle.promotion,
        operation_id=bundle.operation_id,
        operation_state=bundle.operation_state,
        attempt_number=bundle.attempt_number,
        replayed=True,
    )


def _attempt_identity(
    command: ArtifactCaptureCommand,
    metadata: Mapping[str, object],
) -> str:
    credential = metadata.get("credential_binding_id")
    version = metadata.get("credential_binding_version")
    value = (
        f"{command.worker_identity}|provider={command.provider}|credential={credential}@{version}"
    )
    if len(value) > 255:
        raise SubmissionPersistenceConflict("artifact attempt provenance exceeds storage bound")
    return value


def _preflight_submission_record(row: Submission) -> PreflightSubmissionRecord:
    return PreflightSubmissionRecord(
        organization_id=row.organization_id,
        submission_id=row.id,
        course_run_homework_id=row.course_run_homework_id,
        course_run_id=row.course_run_id,
        homework_id=row.homework_id,
        student_id=row.student_id,
        current_revision=row.revision,
    )


def _submission_record(row: Submission) -> SubmissionRecord:
    return SubmissionRecord(
        organization_id=row.organization_id,
        submission_id=row.id,
        course_run_id=row.course_run_id,
        homework_id=row.homework_id,
        student_id=row.student_id,
        current_predeadline_version_id=row.current_predeadline_version_id,
        revision=row.revision,
    )


def _preflight_reference_record(row: ArtifactReference) -> PreflightArtifactReferenceRecord:
    if row.provider not in {"github", "google_docs"}:
        raise SubmissionPersistenceConflict("ArtifactReference provider is invalid")
    if row.read_capability not in {"available", "requires_action", "unavailable"}:
        raise SubmissionPersistenceConflict("ArtifactReference read capability is invalid")
    if row.feedback_capability not in {"available", "not_supported", "requires_action"}:
        raise SubmissionPersistenceConflict("ArtifactReference feedback capability is invalid")
    return PreflightArtifactReferenceRecord(
        organization_id=row.organization_id,
        artifact_reference_id=row.id,
        provider=cast(ArtifactProviderName, row.provider),
        credential_binding_id=row.credential_binding_id,
        credential_binding_version=row.credential_binding_version,
        original_url=row.original_url,
        locator=cast(Mapping[str, str], dict(row.locator or {})),
        read_capability=cast(ReadCapability, row.read_capability),
        feedback_capability=cast(FeedbackCapability, row.feedback_capability),
        last_checked_at=row.last_checked_at,
        revision=row.revision,
    )


def _capture_reference_record(row: ArtifactReference) -> ArtifactReferenceCaptureRecord:
    if row.provider not in {"github", "google_docs"}:
        raise SubmissionPersistenceConflict("ArtifactReference provider is invalid")
    return ArtifactReferenceCaptureRecord(
        organization_id=row.organization_id,
        artifact_reference_id=row.id,
        provider=cast(ArtifactProviderName, row.provider),
        locator=cast(Mapping[str, str], dict(row.locator or {})),
        read_capability=row.read_capability,
        revision=row.revision,
    )


def _submission_version_history(row: SubmissionVersion) -> SubmissionVersionHistory:
    return SubmissionVersionHistory(
        id=row.id,
        sequence=row.sequence,
        homework_version_id=row.homework_version_id,
        artifact_reference_id=row.artifact_reference_id,
        artifact_version_id=row.artifact_version_id,
        capture_operation_id=row.capture_operation_id,
        submitted_at=row.submitted_at,
        effective_deadline=row.effective_deadline,
        phase=row.phase,
        status=row.status,
        revision=row.revision,
    )


def _artifact_version_history(
    row: ArtifactVersion,
    *,
    references: Mapping[UUID, ArtifactReference],
) -> ArtifactVersionHistory:
    reference = references.get(row.artifact_reference_id)
    if reference is None:
        raise SubmissionPersistenceConflict("ArtifactVersion reference projection is missing")
    return ArtifactVersionHistory(
        id=row.id,
        artifact_reference_id=row.artifact_reference_id,
        provider=reference.provider,
        provider_version=row.provider_version,
        content_digest=row.content_digest,
        media_type=row.media_type,
        byte_size=row.byte_size,
        captured_at=row.captured_at,
    )


def _review_iteration_history(row: ReviewIteration) -> InitialReviewIterationHistory:
    return InitialReviewIterationHistory(
        id=row.id,
        iteration_number=row.iteration_number,
        submission_version_id=row.submission_version_id,
        artifact_version_id=row.artifact_version_id,
        homework_version_id=row.homework_version_id,
        criterion_set_id=row.criterion_set_id,
        effective_deadline=row.effective_deadline,
        origin=row.origin,
        status=row.status,
        revision=row.revision,
    )


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise InvalidSubmissionTransaction("repository requires caller-owned AsyncSession")
    return transaction


_preflight_protocol: ArtifactPreflightRepository = SqlArtifactPreflightRepository()
_credential_protocol: ArtifactCredentialBindingPort = SqlArtifactCredentialBindings()
_capture_credential_protocol: ArtifactCaptureCredentialPort = SqlArtifactCredentialBindings()
_archive_protocol: ArtifactPreflightArchiveGuard = SqlArtifactPreflightArchiveGuard()
_capture_repository_protocol: ArtifactCaptureRepository = SqlArtifactCaptureRepository()
_capture_authorization_protocol: Callable[[Authorizer], ArtifactCaptureAuthorizationPort] = (
    SqlArtifactCaptureAuthorization
)
_promotion_outbox_protocol: ArtifactPromotionOutbox = SqlArtifactPromotionOutbox()
_promotion_repository_protocol: Callable[
    [AsyncSessionFactory], ArtifactPromotionRepository
] = SqlArtifactPromotionRepository
_submission_protocol: SubmissionRepository = SqlSubmissionRepository()
_submission_scope_protocol: SubmissionScopeAuthorization = SqlSubmissionScopeAuthorization()
_capture_scheduler_protocol: CaptureScheduler = SqlCaptureScheduler()


def _review_repository_protocol(session: AsyncSession) -> ReviewIterationRepository:
    return SqlReviewIterationRepository(session)


__all__ = [
    "ArtifactPromotionStatus",
    "ArtifactVersionHistory",
    "InitialReviewIterationHistory",
    "InvalidSubmissionTransaction",
    "SqlArtifactCaptureAuthorization",
    "SqlArtifactCaptureRepository",
    "SqlArtifactCredentialBindings",
    "SqlArtifactPreflightArchiveGuard",
    "SqlArtifactPreflightRepository",
    "SqlArtifactPromotionOutbox",
    "SqlArtifactPromotionRepository",
    "SqlCaptureScheduler",
    "SqlReviewIterationRepository",
    "SqlSubmissionRepository",
    "SqlSubmissionScopeAuthorization",
    "SubmissionHistoryProjection",
    "SubmissionPersistenceConflict",
    "SubmissionRepositoryError",
    "SubmissionVersionHistory",
]
