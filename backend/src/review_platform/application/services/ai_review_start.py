"""Idempotent start of AI review for one immutable ReviewIteration input."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.contracts.ai_review import (
    AIArtifactDownload,
    AIArtifactEnvelope,
    AICriteria,
    AIHomework,
    AIReviewRequest,
)
from review_platform.contracts.registry import CONTRACT_VERSION
from review_platform.domain.ai_fingerprint import (
    FINGERPRINT_ALGORITHM,
    ImmutableAIReviewInput,
    compute_ai_fingerprint,
)
from review_platform.domain.primitives import require_utc, utc_now, uuid7
from review_platform.infrastructure.db.models.ai_review import AIReviewAttempt, AIReviewRun

type AIReviewRunStatus = Literal[
    "pending",
    "running",
    "partial",
    "succeeded",
    "retryable_failed",
    "action_required",
    "stale",
]
_START_POLICY = AuthorizationPolicy(
    required_roles=frozenset({"reviewer", "methodologist"}),
    required_scopes=frozenset({"ai_reviews:start"}),
)
_REPLAYABLE_STATUSES = frozenset(
    {"pending", "running", "partial", "succeeded", "action_required", "stale"}
)
_AI_COMPONENT_PROVIDER = "ai_review"
_CONTRACT_VERSION: Literal["1.1.0"] = "1.1.0"
_FINGERPRINT_ALGORITHM: Literal["jcs-sha256-v1"] = "jcs-sha256-v1"

if CONTRACT_VERSION != _CONTRACT_VERSION or FINGERPRINT_ALGORITHM != _FINGERPRINT_ALGORITHM:
    raise RuntimeError("AI review application constants differ from the frozen contract")


class AIReviewStartError(RuntimeError):
    """Base typed failure for the start boundary."""


class AIReviewStartNotFound(AIReviewStartError):
    pass


class AIReviewStartStale(AIReviewStartError):
    pass


class AIReviewStartArchived(AIReviewStartError):
    pass


class AIReviewCredentialMismatch(AIReviewStartError):
    pass


class AIReviewStartConflict(AIReviewStartError):
    pass


class AIReviewSignedGrantError(AIReviewStartError):
    pass


@dataclass(frozen=True, slots=True)
class StartAIReviewCommand:
    organization_id: UUID
    review_iteration_id: UUID
    expected_review_iteration_revision: int
    request_id: UUID
    trace_id: UUID

    def __post_init__(self) -> None:
        if self.expected_review_iteration_revision < 0:
            raise ValueError("expected ReviewIteration revision must be nonnegative")


@dataclass(frozen=True, slots=True)
class AIComponentCredentialBinding:
    organization_id: UUID
    credential_binding_id: UUID
    credential_binding_version: int
    provider: str = _AI_COMPONENT_PROVIDER

    def __post_init__(self) -> None:
        if self.credential_binding_version < 1:
            raise ValueError("credential binding version must be positive")
        if self.provider != _AI_COMPONENT_PROVIDER:
            raise ValueError("AI component credential provider must be ai_review")


@dataclass(frozen=True, slots=True)
class AIReviewInputSnapshot:
    """Tenant-locked immutable inputs selected by one ReviewIteration."""

    organization_id: UUID
    review_iteration_id: UUID
    review_iteration_revision: int
    is_current: bool
    course_run_id: UUID
    submission_version_id: UUID
    artifact: AIArtifactEnvelope
    homework: AIHomework
    criteria: AICriteria
    current_human_revision_id: UUID | None


@dataclass(frozen=True, slots=True)
class SignedArtifactGrant:
    organization_id: UUID
    artifact_version_id: UUID
    object_key: str
    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AIReviewDispatch:
    """Atomic Operation/outbox input owned by the T107 scheduler adapter."""

    organization_id: UUID
    operation_id: UUID
    run_id: UUID
    attempt_id: UUID
    attempt_number: int
    input_version: str
    event_type: Literal["AIReviewRequested"]
    payload_version: Literal["1.1.0"]
    request: AIReviewRequest
    request_id: UUID
    trace_id: UUID
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class AIReviewStartResult:
    organization_id: UUID
    run_id: UUID
    operation_id: UUID
    input_fingerprint: str
    status: AIReviewRunStatus
    attempt_id: UUID
    attempt_number: int
    credential_binding_id: UUID
    credential_binding_version: int
    replayed: bool
    request: AIReviewRequest | None


class AIReviewInputRepository(Protocol):
    async def lock_input_snapshot(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> AIReviewInputSnapshot | None: ...


class AIReviewRunRepository(Protocol):
    """Session-bound subset implemented by T103; it must not commit internally."""

    async def create_or_get_run(
        self,
        candidate: AIReviewRun,
    ) -> tuple[AIReviewRun, bool]: ...

    async def history(
        self,
        organization_id: UUID,
        run_id: UUID,
    ) -> AIReviewHistoryView | None: ...

    async def add_attempt(
        self,
        attempt: AIReviewAttempt,
    ) -> AIReviewAttempt: ...

    async def compare_and_set_current_attempt(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        expected_revision: int,
        expected_current_attempt_no: int,
        new_attempt_no: int,
    ) -> bool: ...


class AIReviewHistoryView(Protocol):
    @property
    def run(self) -> AIReviewRun: ...

    @property
    def attempts(self) -> tuple[AIReviewAttempt, ...]: ...


class AIComponentCredentialRepository(Protocol):
    async def require_exact_active(
        self,
        binding: AIComponentCredentialBinding,
        *,
        transaction: object,
    ) -> AIComponentCredentialBinding: ...


class AIReviewArchivedGuard(Protocol):
    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        transaction: object,
    ) -> None: ...


class ArtifactGrantSigner(Protocol):
    def sign_read(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        object_key: str,
        expires_in_seconds: int,
        now: datetime,
    ) -> SignedArtifactGrant: ...


class AIReviewScheduler(Protocol):
    """Create/get Operation and append AIReviewRequested in the caller's transaction."""

    async def schedule(
        self,
        dispatch: AIReviewDispatch,
        *,
        transaction: object,
    ) -> None: ...


class AIReviewStartService:
    def __init__(
        self,
        *,
        input_repository: AIReviewInputRepository,
        run_repository: AIReviewRunRepository,
        credential_repository: AIComponentCredentialRepository,
        archived_guard: AIReviewArchivedGuard,
        grant_signer: ArtifactGrantSigner,
        scheduler: AIReviewScheduler,
        authorizer: Authorizer,
        audit: AuditRecorder,
        signed_url_ttl_seconds: int = 900,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not 1 <= signed_url_ttl_seconds <= 3600:
            raise ValueError("signed artifact URL TTL must be between 1 and 3600 seconds")
        self._input_repository = input_repository
        self._run_repository = run_repository
        self._credential_repository = credential_repository
        self._archived_guard = archived_guard
        self._grant_signer = grant_signer
        self._scheduler = scheduler
        self._authorizer = authorizer
        self._audit = audit
        self._signed_url_ttl_seconds = signed_url_ttl_seconds
        self._id_factory = id_factory
        self._clock = clock

    async def start(
        self,
        command: StartAIReviewCommand,
        *,
        actor: RequestActor,
        credential_binding: AIComponentCredentialBinding,
        transaction: object,
    ) -> AIReviewStartResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=command.organization_id,
            policy=_START_POLICY,
        )
        snapshot = await self._input_repository.lock_input_snapshot(
            command.organization_id,
            command.review_iteration_id,
            expected_revision=command.expected_review_iteration_revision,
            transaction=transaction,
        )
        if snapshot is None:
            raise AIReviewStartNotFound(
                "ReviewIteration is missing or its revision is stale"
            )
        self._validate_snapshot(command, snapshot)
        await self._archived_guard.require_active(
            organization_id=command.organization_id,
            course_run_id=snapshot.course_run_id,
            transaction=transaction,
        )
        exact_binding = await self._credential_repository.require_exact_active(
            credential_binding,
            transaction=transaction,
        )
        self._validate_binding(command.organization_id, credential_binding, exact_binding)

        now = require_utc(self._clock())
        immutable_input = self._immutable_input(snapshot)
        input_fingerprint = compute_ai_fingerprint(immutable_input)
        candidate = self._run_candidate(
            snapshot,
            run_id=self._id_factory(),
            input_fingerprint=input_fingerprint,
            created_at=now,
        )
        run, created = await self._run_repository.create_or_get_run(candidate)
        self._validate_run(run, candidate)

        if not created:
            current_attempt = await self._current_attempt(run)
            if run.status in _REPLAYABLE_STATUSES:
                self._require_same_attempt_binding(current_attempt, exact_binding)
                await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
                return self._result(
                    run,
                    current_attempt,
                    replayed=True,
                    request=None,
                )
            if run.status != "retryable_failed":
                raise AIReviewStartConflict(f"unsupported AIReviewRun status {run.status!r}")
            next_attempt_number = run.current_attempt_no + 1
        else:
            if run.current_attempt_no != 0 or run.status != "pending":
                raise AIReviewStartConflict("new AIReviewRun has invalid initial state")
            next_attempt_number = 1

        before_revision = run.revision
        before_attempt_number = run.current_attempt_no
        attempt = AIReviewAttempt(
            organization_id=command.organization_id,
            id=self._id_factory(),
            ai_review_run_id=run.id,
            attempt_number=next_attempt_number,
            credential_binding_id=exact_binding.credential_binding_id,
            credential_binding_version=exact_binding.credential_binding_version,
            status="pending",
            last_sequence=0,
            started_at=now,
            finished_at=None,
            error_code=None,
            sanitized_error=None,
        )
        await self._run_repository.add_attempt(attempt)
        updated = await self._run_repository.compare_and_set_current_attempt(
            command.organization_id,
            run.id,
            expected_revision=before_revision,
            expected_current_attempt_no=before_attempt_number,
            new_attempt_no=attempt.attempt_number,
        )
        if not updated:
            raise AIReviewStartConflict("AIReviewRun current attempt changed concurrently")

        signed = self._grant_signer.sign_read(
            organization_id=command.organization_id,
            artifact_version_id=snapshot.artifact.artifact_version_id,
            object_key=snapshot.artifact.object.key,
            expires_in_seconds=self._signed_url_ttl_seconds,
            now=now,
        )
        self._validate_signed_grant(snapshot, signed, now=now)
        artifact_download = AIArtifactDownload.model_validate(
            {"url": signed.url, "expires_at": signed.expires_at}
        )
        request = AIReviewRequest(
            contract_version=_CONTRACT_VERSION,
            run_id=run.id,
            input_fingerprint=input_fingerprint,
            fingerprint_algorithm=_FINGERPRINT_ALGORITHM,
            organization_id=command.organization_id,
            course_run_id=snapshot.course_run_id,
            submission_version_id=snapshot.submission_version_id,
            review_iteration_id=snapshot.review_iteration_id,
            credential_binding_id=exact_binding.credential_binding_id,
            credential_binding_version=exact_binding.credential_binding_version,
            artifact=snapshot.artifact,
            artifact_download=artifact_download,
            homework=snapshot.homework,
            criteria=snapshot.criteria,
        )
        operation_id = run.id
        await self._scheduler.schedule(
            AIReviewDispatch(
                organization_id=command.organization_id,
                operation_id=operation_id,
                run_id=run.id,
                attempt_id=attempt.id,
                attempt_number=attempt.attempt_number,
                input_version=(
                    f"ai-review:{_CONTRACT_VERSION}:{input_fingerprint}"
                ),
                event_type="AIReviewRequested",
                payload_version=_CONTRACT_VERSION,
                request=request,
                request_id=command.request_id,
                trace_id=command.trace_id,
                requested_at=now,
            ),
            transaction=transaction,
        )
        await self._audit.record(
            AuditEventDraft(
                organization_id=command.organization_id,
                actor=actor,
                action="start_ai_review",
                entity_type="ai_review_run",
                entity_id=run.id,
                before_revision=before_revision,
                after_revision=before_revision + 1,
                request_id=command.request_id,
                trace_id=command.trace_id,
                outcome="succeeded",
                details={
                    "operation_id": str(operation_id),
                    "attempt_number": attempt.attempt_number,
                    "input_fingerprint": input_fingerprint,
                    "credential_binding_id": str(exact_binding.credential_binding_id),
                    "credential_binding_version": (
                        exact_binding.credential_binding_version
                    ),
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return AIReviewStartResult(
            organization_id=command.organization_id,
            run_id=run.id,
            operation_id=operation_id,
            input_fingerprint=input_fingerprint,
            status="running",
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            credential_binding_id=attempt.credential_binding_id,
            credential_binding_version=attempt.credential_binding_version,
            replayed=False,
            request=request,
        )

    @staticmethod
    def _validate_snapshot(
        command: StartAIReviewCommand,
        snapshot: AIReviewInputSnapshot,
    ) -> None:
        if snapshot.organization_id != command.organization_id:
            raise AIReviewStartNotFound("ReviewIteration tenant mismatched")
        if snapshot.review_iteration_id != command.review_iteration_id:
            raise AIReviewStartNotFound("ReviewIteration identity mismatched")
        if snapshot.review_iteration_revision != command.expected_review_iteration_revision:
            raise AIReviewStartStale("ReviewIteration revision changed")
        if not snapshot.is_current:
            raise AIReviewStartStale("ReviewIteration is no longer current")
        if snapshot.artifact.organization_id != command.organization_id:
            raise AIReviewStartNotFound("ArtifactVersion tenant mismatched")

    @staticmethod
    def _validate_binding(
        organization_id: UUID,
        requested: AIComponentCredentialBinding,
        stored: AIComponentCredentialBinding,
    ) -> None:
        if requested.organization_id != organization_id:
            raise AIReviewCredentialMismatch("credential binding tenant mismatched")
        if stored != requested:
            raise AIReviewCredentialMismatch(
                "credential repository did not return the exact requested binding"
            )

    @staticmethod
    def _immutable_input(snapshot: AIReviewInputSnapshot) -> ImmutableAIReviewInput:
        return ImmutableAIReviewInput(
            contract_version=_CONTRACT_VERSION,
            organization_id=snapshot.organization_id,
            course_run_id=snapshot.course_run_id,
            submission_version_id=snapshot.submission_version_id,
            review_iteration_id=snapshot.review_iteration_id,
            artifact_version_id=snapshot.artifact.artifact_version_id,
            artifact_content_digest=snapshot.artifact.content_digest,
            homework_version_id=snapshot.homework.version_id,
            homework_digest=snapshot.homework.digest,
            criterion_set_id=snapshot.criteria.set_id,
            criterion_set_digest=snapshot.criteria.digest,
        )

    @staticmethod
    def _run_candidate(
        snapshot: AIReviewInputSnapshot,
        *,
        run_id: UUID,
        input_fingerprint: str,
        created_at: datetime,
    ) -> AIReviewRun:
        return AIReviewRun(
            id=run_id,
            organization_id=snapshot.organization_id,
            review_iteration_id=snapshot.review_iteration_id,
            course_run_id=snapshot.course_run_id,
            submission_version_id=snapshot.submission_version_id,
            artifact_version_id=snapshot.artifact.artifact_version_id,
            content_digest=snapshot.artifact.content_digest,
            homework_version_id=snapshot.homework.version_id,
            homework_digest=snapshot.homework.digest,
            criterion_set_id=snapshot.criteria.set_id,
            criteria_digest=snapshot.criteria.digest,
            contract_version=_CONTRACT_VERSION,
            fingerprint_algorithm=_FINGERPRINT_ALGORITHM,
            input_fingerprint=input_fingerprint,
            status="pending",
            current_attempt_no=0,
            revision=0,
            created_at=created_at,
            updated_at=created_at,
            finished_at=None,
        )

    @staticmethod
    def _validate_run(stored: AIReviewRun, candidate: AIReviewRun) -> None:
        immutable_fields = (
            "organization_id",
            "review_iteration_id",
            "course_run_id",
            "submission_version_id",
            "artifact_version_id",
            "content_digest",
            "homework_version_id",
            "homework_digest",
            "criterion_set_id",
            "criteria_digest",
            "contract_version",
            "fingerprint_algorithm",
            "input_fingerprint",
        )
        if any(
            getattr(stored, field) != getattr(candidate, field)
            for field in immutable_fields
        ):
            raise AIReviewStartConflict(
                "stored AIReviewRun has different immutable provenance"
            )

    async def _current_attempt(
        self,
        run: AIReviewRun,
    ) -> AIReviewAttempt:
        if run.current_attempt_no < 1:
            raise AIReviewStartConflict("existing AIReviewRun has no current attempt")
        history = await self._run_repository.history(
            run.organization_id,
            run.id,
        )
        if history is None or history.run.id != run.id:
            raise AIReviewStartConflict("AIReviewRun history is missing or mismatched")
        attempt = next(
            (
                item
                for item in history.attempts
                if item.attempt_number == run.current_attempt_no
            ),
            None,
        )
        if attempt is None:
            raise AIReviewStartConflict("AIReviewRun current attempt is missing")
        if (
            attempt.organization_id != run.organization_id
            or attempt.ai_review_run_id != run.id
            or attempt.attempt_number != run.current_attempt_no
        ):
            raise AIReviewStartConflict("AIReviewRun current attempt provenance mismatched")
        return attempt

    @staticmethod
    def _require_same_attempt_binding(
        attempt: AIReviewAttempt,
        binding: AIComponentCredentialBinding,
    ) -> None:
        if (
            attempt.credential_binding_id != binding.credential_binding_id
            or attempt.credential_binding_version != binding.credential_binding_version
        ):
            raise AIReviewCredentialMismatch(
                "current attempt is bound to a different exact AI credential"
            )

    def _validate_signed_grant(
        self,
        snapshot: AIReviewInputSnapshot,
        signed: SignedArtifactGrant,
        *,
        now: datetime,
    ) -> None:
        expires_at = require_utc(signed.expires_at)
        if (
            signed.organization_id != snapshot.organization_id
            or signed.artifact_version_id != snapshot.artifact.artifact_version_id
            or signed.object_key != snapshot.artifact.object.key
        ):
            raise AIReviewSignedGrantError("signed artifact grant scope mismatched")
        maximum = now + timedelta(seconds=self._signed_url_ttl_seconds)
        if expires_at <= now or expires_at > maximum:
            raise AIReviewSignedGrantError(
                "signed artifact grant expiry exceeds configured TTL"
            )

    @staticmethod
    def _result(
        run: AIReviewRun,
        attempt: AIReviewAttempt,
        *,
        replayed: bool,
        request: AIReviewRequest | None,
    ) -> AIReviewStartResult:
        return AIReviewStartResult(
            organization_id=run.organization_id,
            run_id=run.id,
            operation_id=run.id,
            input_fingerprint=run.input_fingerprint,
            status=_validated_run_status(run.status),
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            credential_binding_id=attempt.credential_binding_id,
            credential_binding_version=attempt.credential_binding_version,
            replayed=replayed,
            request=request,
        )


def _validated_run_status(value: str) -> AIReviewRunStatus:
    if value not in {
        "pending",
        "running",
        "partial",
        "succeeded",
        "retryable_failed",
        "action_required",
        "stale",
    }:
        raise AIReviewStartConflict(f"unsupported AIReviewRun status {value!r}")
    return cast(AIReviewRunStatus, value)


__all__ = [
    "AIComponentCredentialBinding",
    "AIComponentCredentialRepository",
    "AIReviewArchivedGuard",
    "AIReviewCredentialMismatch",
    "AIReviewDispatch",
    "AIReviewInputRepository",
    "AIReviewInputSnapshot",
    "AIReviewRunRepository",
    "AIReviewScheduler",
    "AIReviewSignedGrantError",
    "AIReviewStartArchived",
    "AIReviewStartConflict",
    "AIReviewStartError",
    "AIReviewStartNotFound",
    "AIReviewStartResult",
    "AIReviewStartService",
    "AIReviewStartStale",
    "ArtifactGrantSigner",
    "SignedArtifactGrant",
    "StartAIReviewCommand",
]
