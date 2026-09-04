"""Atomic SubmissionVersion creation and artifact-capture scheduling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc, utc_now, uuid7

type ArtifactProvider = Literal["github", "google_docs"]
type SubmissionPhase = Literal["before_deadline", "revision"]
type SubmissionVersionStatus = Literal["validating", "pending_review", "superseded"]

_STUDENT = AuthorizationPolicy(required_roles=frozenset({"student"}))
_PROVIDERS = frozenset[ArtifactProvider]({"github", "google_docs"})


class SubmissionServiceError(RuntimeError):
    """Base typed submit-work error."""


class SubmissionNotFound(SubmissionServiceError):
    pass


class SubmissionRevisionConflict(SubmissionServiceError):
    pass


class SubmissionScopeDenied(SubmissionServiceError):
    pass


class ArtifactReferenceUnavailable(SubmissionServiceError):
    pass


class EffectivePublicationNotFound(SubmissionServiceError):
    pass


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    organization_id: UUID
    submission_id: UUID
    course_run_id: UUID
    homework_id: UUID
    student_id: UUID
    current_predeadline_version_id: UUID | None
    revision: int


@dataclass(frozen=True, slots=True)
class ArtifactReferenceRecord:
    organization_id: UUID
    artifact_reference_id: UUID
    provider: str
    credential_binding_id: UUID
    credential_binding_version: int
    usable: bool


@dataclass(frozen=True, slots=True)
class EffectiveHomeworkPublication:
    organization_id: UUID
    course_run_homework_id: UUID
    publication_id: UUID
    course_run_id: UUID
    homework_id: UUID
    homework_version_id: UUID
    submission_deadline: datetime


@dataclass(frozen=True, slots=True)
class SubmissionVersionRecord:
    organization_id: UUID
    version_id: UUID
    submission_id: UUID
    sequence: int
    homework_version_id: UUID
    artifact_reference_id: UUID
    capture_operation_id: UUID
    submitted_at: datetime
    effective_deadline: datetime
    phase: SubmissionPhase
    status: SubmissionVersionStatus
    revision: int = 0


@dataclass(frozen=True, slots=True)
class ArtifactCaptureRequest:
    organization_id: UUID
    operation_id: UUID
    submission_id: UUID
    submission_version_id: UUID
    artifact_reference_id: UUID
    provider: ArtifactProvider
    credential_binding_id: UUID
    credential_binding_version: int
    course_run_id: UUID
    homework_id: UUID
    homework_version_id: UUID
    course_run_homework_id: UUID
    homework_publication_id: UUID
    actor_user_id: UUID
    actor_membership_revision: int
    actor_auth_epoch: int


@dataclass(frozen=True, slots=True)
class SubmitWorkResult:
    submission_id: UUID
    submission_revision: int
    version: SubmissionVersionRecord


class SubmissionRepository(Protocol):
    """Tenant-only persistence port implemented by T086."""

    async def lock_submission(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> SubmissionRecord | None: ...

    async def lock_current_publication(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        homework_id: UUID,
        *,
        transaction: object,
    ) -> EffectiveHomeworkPublication | None: ...

    async def get_artifact_reference(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        *,
        transaction: object,
    ) -> ArtifactReferenceRecord | None: ...

    async def next_version_sequence(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        transaction: object,
    ) -> int: ...

    async def append_version(
        self, version: SubmissionVersionRecord, *, transaction: object
    ) -> None: ...

    async def mark_superseded(
        self,
        organization_id: UUID,
        version_id: UUID,
        *,
        transaction: object,
    ) -> None: ...

    async def compare_and_set_submission(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        expected_revision: int,
        current_predeadline_version_id: UUID | None,
        transaction: object,
    ) -> bool: ...


class SubmissionScopeAuthorization(Protocol):
    async def require_owner_and_enrollment(
        self,
        *,
        actor: RequestActor,
        submission: SubmissionRecord,
        publication: EffectiveHomeworkPublication,
        transaction: object,
    ) -> None: ...


class CaptureScheduler(Protocol):
    """Atomically create observable Operation and durable capture intent."""

    async def schedule(self, request: ArtifactCaptureRequest, *, transaction: object) -> None: ...


class SubmissionService:
    def __init__(
        self,
        *,
        repository: SubmissionRepository,
        scope_authorization: SubmissionScopeAuthorization,
        capture_scheduler: CaptureScheduler,
        authorizer: Authorizer,
        audit: AuditRecorder,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._scope_authorization = scope_authorization
        self._capture_scheduler = capture_scheduler
        self._authorizer = authorizer
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def submit_work(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        submission_id: UUID,
        expected_submission_revision: int,
        artifact_reference_id: UUID,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> SubmitWorkResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_STUDENT,
        )
        submission = await self._repository.lock_submission(
            organization_id,
            submission_id,
            expected_revision=expected_submission_revision,
            transaction=transaction,
        )
        if submission is None:
            raise SubmissionRevisionConflict("Submission is missing or revision is stale")
        if submission.organization_id != organization_id:
            raise SubmissionNotFound("Submission tenant mismatched")
        publication = await self._repository.lock_current_publication(
            organization_id,
            submission.course_run_id,
            submission.homework_id,
            transaction=transaction,
        )
        if publication is None:
            raise EffectivePublicationNotFound("current CourseRun homework publication is absent")
        if (
            publication.organization_id != organization_id
            or publication.course_run_id != submission.course_run_id
            or publication.homework_id != submission.homework_id
        ):
            raise SubmissionScopeDenied("effective publication provenance mismatched")
        await self._scope_authorization.require_owner_and_enrollment(
            actor=actor,
            submission=submission,
            publication=publication,
            transaction=transaction,
        )
        reference = await self._repository.get_artifact_reference(
            organization_id,
            artifact_reference_id,
            transaction=transaction,
        )
        if reference is None or not reference.usable:
            raise ArtifactReferenceUnavailable("usable tenant artifact reference was not found")
        if (
            reference.organization_id != organization_id
            or reference.artifact_reference_id != artifact_reference_id
            or reference.provider not in _PROVIDERS
            or not isinstance(reference.credential_binding_version, int)
            or isinstance(reference.credential_binding_version, bool)
            or reference.credential_binding_version < 1
        ):
            raise ArtifactReferenceUnavailable("artifact provider or tenant is not allowed")

        submitted_at = require_utc(self._clock())
        deadline = require_utc(publication.submission_deadline)
        before_deadline = submitted_at <= deadline
        version_id = self._id_factory()
        operation_id = self._id_factory()
        version = SubmissionVersionRecord(
            organization_id=organization_id,
            version_id=version_id,
            submission_id=submission_id,
            sequence=await self._repository.next_version_sequence(
                organization_id, submission_id, transaction=transaction
            ),
            homework_version_id=publication.homework_version_id,
            artifact_reference_id=artifact_reference_id,
            capture_operation_id=operation_id,
            submitted_at=submitted_at,
            effective_deadline=deadline,
            phase="before_deadline" if before_deadline else "revision",
            status="validating" if before_deadline else "pending_review",
        )
        await self._repository.append_version(version, transaction=transaction)
        if actor.user_id is None or actor.membership_revision is None or actor.auth_epoch is None:
            raise SubmissionScopeDenied("student actor authority snapshot is incomplete")
        if reference.provider == "github":
            provider: ArtifactProvider = "github"
        elif reference.provider == "google_docs":
            provider = "google_docs"
        else:
            raise ArtifactReferenceUnavailable("artifact provider is not allowed")
        await self._capture_scheduler.schedule(
            ArtifactCaptureRequest(
                organization_id=organization_id,
                operation_id=operation_id,
                submission_id=submission_id,
                submission_version_id=version_id,
                artifact_reference_id=artifact_reference_id,
                provider=provider,
                credential_binding_id=reference.credential_binding_id,
                credential_binding_version=reference.credential_binding_version,
                course_run_id=submission.course_run_id,
                homework_id=submission.homework_id,
                homework_version_id=publication.homework_version_id,
                course_run_homework_id=publication.course_run_homework_id,
                homework_publication_id=publication.publication_id,
                actor_user_id=actor.user_id,
                actor_membership_revision=actor.membership_revision,
                actor_auth_epoch=actor.auth_epoch,
            ),
            transaction=transaction,
        )
        if before_deadline and submission.current_predeadline_version_id is not None:
            await self._repository.mark_superseded(
                organization_id,
                submission.current_predeadline_version_id,
                transaction=transaction,
            )
        new_current = version_id if before_deadline else submission.current_predeadline_version_id
        if not await self._repository.compare_and_set_submission(
            organization_id,
            submission_id,
            expected_revision=expected_submission_revision,
            current_predeadline_version_id=new_current,
            transaction=transaction,
        ):
            raise SubmissionRevisionConflict("Submission CAS lost a concurrent race")
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="submit_work",
                entity_type="submission_version",
                entity_id=version_id,
                before_revision=expected_submission_revision,
                after_revision=expected_submission_revision + 1,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "phase": version.phase,
                    "status": version.status,
                    "capture_operation_id": str(operation_id),
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return SubmitWorkResult(
            submission_id=submission_id,
            submission_revision=expected_submission_revision + 1,
            version=version,
        )


__all__ = [
    "ArtifactCaptureRequest",
    "ArtifactProvider",
    "ArtifactReferenceRecord",
    "ArtifactReferenceUnavailable",
    "CaptureScheduler",
    "EffectiveHomeworkPublication",
    "EffectivePublicationNotFound",
    "SubmissionNotFound",
    "SubmissionPhase",
    "SubmissionRecord",
    "SubmissionRepository",
    "SubmissionRevisionConflict",
    "SubmissionScopeAuthorization",
    "SubmissionScopeDenied",
    "SubmissionService",
    "SubmissionServiceError",
    "SubmissionVersionRecord",
    "SubmissionVersionStatus",
    "SubmitWorkResult",
]
