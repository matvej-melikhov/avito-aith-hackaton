"""Frozen US3 preflight, submission, history, and explicit review-open routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import (
    AuthorizationError,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import IdempotencyCoordinator, IdempotencyError
from review_platform.application.ports.providers import ArtifactProvider
from review_platform.application.request_context import RequestActor
from review_platform.application.services.artifact_preflight import (
    ArtifactCapabilityResult,
    ArtifactCredentialBinding,
    ArtifactKindNotAllowed,
    ArtifactPreflightAuditPort,
    ArtifactPreflightError,
    ArtifactPreflightService,
    provider_for_artifact_url,
)
from review_platform.application.services.review_iterations import (
    OpenReviewIterationCommand,
    ReviewIterationAuditPort,
    ReviewIterationAuthorizationPort,
    ReviewIterationError,
    ReviewIterationService,
)
from review_platform.application.services.submissions import (
    ArtifactReferenceUnavailable,
    SubmissionService,
    SubmissionServiceError,
)
from review_platform.contracts.commands import (
    OpenReviewIterationPayload,
    PreflightSubmissionPayload,
    SubmitWorkPayload,
    WireCommand,
)
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
)
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.models.submission import Submission
from review_platform.infrastructure.db.repositories.operations import CommandReceiptRepository
from review_platform.infrastructure.db.repositories.submissions import (
    SqlArtifactCredentialBindings,
    SqlArtifactPreflightArchiveGuard,
    SqlArtifactPreflightRepository,
    SqlCaptureScheduler,
    SqlReviewIterationRepository,
    SqlSubmissionRepository,
    SqlSubmissionScopeAuthorization,
    SubmissionHistoryProjection,
)

router = APIRouter(tags=["submissions"])


class SubmissionRouteError(ValueError):
    pass


class _ReviewAuthorization(ReviewIterationAuthorizationPort):
    def __init__(self, runtime: FoundationRuntime) -> None:
        self._runtime = runtime

    async def authorize_open(
        self, *, actor: RequestActor, organization_id: UUID, transaction: object
    ) -> None:
        await Authorizer(self._runtime.user_auth_guard, clock=self._runtime.clock).authorize(
            actor=actor,
            organization_id=organization_id,
            policy=AuthorizationPolicy(required_roles=frozenset({"reviewer", "methodologist"})),
        )

    async def revalidate_for_commit(self, *, actor: RequestActor, transaction: object) -> None:
        await self._runtime.user_auth_guard.lock_and_revalidate(
            actor=actor, transaction=transaction
        )


class _ReviewAudit(ReviewIterationAuditPort):
    def __init__(self, runtime: FoundationRuntime) -> None:
        self._recorder = AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=runtime.id_factory,
            clock=runtime.clock,
        )

    async def record_open(
        self,
        *,
        actor: RequestActor,
        iteration: ReviewIteration,
        before_revision: int,
        after_revision: int,
        request_id: UUID,
        trace_id: UUID,
        transaction: object,
    ) -> None:
        await self._recorder.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action="open_review_iteration",
                entity_type="review_iteration",
                entity_id=iteration.id,
                before_revision=before_revision,
                after_revision=after_revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={"submission_version_id": str(iteration.submission_version_id)},
            ),
            transaction=transaction,
        )


class _PreflightAudit(ArtifactPreflightAuditPort):
    def __init__(self, runtime: FoundationRuntime) -> None:
        self._recorder = AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=runtime.id_factory,
            clock=runtime.clock,
        )

    async def record_preflight(
        self,
        *,
        actor: RequestActor,
        result: ArtifactCapabilityResult,
        course_run_homework_id: UUID,
        expected_revision: int,
        request_id: UUID,
        trace_id: UUID,
        transaction: object,
    ) -> None:
        await self._recorder.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action="preflight_submission",
                entity_type=(
                    "artifact_reference"
                    if result.artifact_reference_id is not None
                    else "submission"
                ),
                entity_id=result.artifact_reference_id or result.submission_id,
                before_revision=expected_revision,
                after_revision=expected_revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "provider": result.provider,
                    "read_capability": result.read_capability,
                    "feedback_capability": result.feedback_capability,
                    "course_run_homework_id": str(course_run_homework_id),
                },
            ),
            transaction=transaction,
        )


@router.post(
    "/v1/course-run-homeworks/{courseRunHomeworkId}/submissions/preflight",
    operation_id="preflightSubmission",
)
async def preflight_submission(
    courseRunHomeworkId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "preflight_submission", courseRunHomeworkId)
        payload = cast(PreflightSubmissionPayload, command.payload)
        provider_name = provider_for_artifact_url(payload.artifact_url)
        provider = _provider(request, provider_name)
        binding = ArtifactCredentialBinding(
            credential_binding_id=_state_uuid(request, f"{provider_name}_credential_binding_id"),
            credential_binding_version=_state_int(
                request, f"{provider_name}_credential_binding_version"
            ),
            provider=provider_name,
        )
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            replay = await _reserve(runtime, transaction, command, actor.organization_id)
            if replay is None:
                result = await ArtifactPreflightService(
                    repository=SqlArtifactPreflightRepository(),
                    credential_bindings=SqlArtifactCredentialBindings(),
                    provider=provider,
                    archived_guard=SqlArtifactPreflightArchiveGuard(),
                    authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
                    audit=_PreflightAudit(runtime),
                    id_factory=runtime.id_factory,
                    clock=runtime.clock,
                ).preflight(
                    transaction=transaction,
                    organization_id=actor.organization_id,
                    course_run_homework_id=courseRunHomeworkId,
                    expected_revision=command.expected_revision,
                    artifact_url=payload.artifact_url,
                    credential_binding=binding,
                    actor=actor,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                )
                response_payload = {
                    "provider": result.provider,
                    "read_capability": result.read_capability,
                    "feedback_capability": result.feedback_capability,
                    "submission_id": str(result.submission_id),
                    "submission_revision": result.submission_revision,
                    "artifact_reference_id": (
                        str(result.artifact_reference_id)
                        if result.artifact_reference_id is not None
                        else None
                    ),
                    "error": _artifact_error_object(result.error),
                }
                await _complete(transaction, actor.organization_id, command, response_payload)
            else:
                response_payload = replay
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        return JSONResponse(response_payload)
    except _ERRORS as error:
        return _error(error, unavailable=isinstance(error, ArtifactKindNotAllowed))


@router.post("/v1/submissions/{submissionId}/versions", operation_id="submitWork", status_code=201)
async def submit_work(submissionId: UUID, request: Request, body: Mapping[str, Any]) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "submit_work", submissionId)
        payload = cast(SubmitWorkPayload, command.payload)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            replay = await _reserve(runtime, transaction, command, actor.organization_id)
            if replay is None:
                result = await SubmissionService(
                    repository=SqlSubmissionRepository(),
                    scope_authorization=SqlSubmissionScopeAuthorization(),
                    capture_scheduler=SqlCaptureScheduler(
                        id_factory=runtime.id_factory,
                        clock=runtime.clock,
                        max_attempts=runtime.settings.provider_max_attempts,
                    ),
                    authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
                    audit=AuditRecorder(
                        SqlAppendOnlyAuditRepository(),
                        event_id_factory=runtime.id_factory,
                        clock=runtime.clock,
                    ),
                    id_factory=runtime.id_factory,
                    clock=runtime.clock,
                ).submit_work(
                    transaction=transaction,
                    organization_id=actor.organization_id,
                    submission_id=submissionId,
                    expected_submission_revision=command.expected_revision,
                    artifact_reference_id=payload.artifact_reference_id,
                    actor=actor,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                )
                response_payload = {
                    "submission_id": str(result.submission_id),
                    "submission_revision": result.submission_revision,
                    "submission_version_id": str(result.version.version_id),
                    "submission_version_revision": result.version.revision,
                    "capture_operation_id": str(result.version.capture_operation_id),
                }
                await _complete(transaction, actor.organization_id, command, response_payload)
            else:
                response_payload = replay
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        return JSONResponse(response_payload, status_code=201)
    except _ERRORS as error:
        return _error(error, unavailable=isinstance(error, ArtifactReferenceUnavailable))


@router.get("/v1/submissions/{submissionId}", operation_id="getSubmissionHistory")
async def get_submission_history(submissionId: UUID, request: Request) -> Response:
    try:
        runtime, actor = _context(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            row = await transaction.scalar(
                select(Submission).where(
                    Submission.organization_id == actor.organization_id,
                    Submission.id == submissionId,
                )
            )
            if row is None:
                raise SubmissionRouteError("tenant Submission was not found")
            if actor.user_id != row.student_id and actor.roles.isdisjoint(
                {"methodologist", "reviewer"}
            ):
                raise AuthorizationError("Submission is not visible to actor")
            projection = await SqlSubmissionRepository().history(
                actor.organization_id, submissionId, transaction=transaction
            )
            if projection is None:
                raise SubmissionRouteError("tenant Submission history was not found")
        return JSONResponse(_history(projection))
    except _ERRORS as error:
        return _error(error)


@router.post(
    "/v1/review-cases/{reviewCaseId}/iterations",
    operation_id="openReviewIteration",
    status_code=201,
)
async def open_review_iteration(
    reviewCaseId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "open_review_iteration", reviewCaseId)
        payload = cast(OpenReviewIterationPayload, command.payload)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            replay = await _reserve(runtime, transaction, command, actor.organization_id)
            if replay is None:
                result = await ReviewIterationService(
                    repository=SqlReviewIterationRepository(transaction),
                    authorization=_ReviewAuthorization(runtime),
                    audit=_ReviewAudit(runtime),
                    id_factory=runtime.id_factory,
                    clock=runtime.clock,
                ).open(
                    OpenReviewIterationCommand(
                        organization_id=actor.organization_id,
                        review_case_id=reviewCaseId,
                        submission_version_id=payload.submission_version_id,
                        expected_review_case_revision=command.expected_revision,
                        request_id=command.request_id,
                        trace_id=runtime.id_factory(),
                    ),
                    actor=actor,
                    transaction=transaction,
                )
                response_payload = {
                    "review_iteration_id": str(result.review_iteration_id),
                    "review_case_id": str(result.review_case_id),
                    "revision": result.review_case_revision,
                }
                await _complete(transaction, actor.organization_id, command, response_payload)
            else:
                response_payload = replay
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        return JSONResponse(response_payload, status_code=201)
    except _ERRORS as error:
        return _error(error)


async def _reserve(
    runtime: FoundationRuntime,
    transaction: object,
    command: WireCommand,
    organization_id: UUID,
) -> dict[str, Any] | None:
    reservation = await IdempotencyCoordinator(
        SqlIdempotencyReceiptRepository(),
        receipt_id_factory=runtime.id_factory,
        result_reference_factory=lambda: {"kind": "pending", "payload": {}},
    ).reserve(
        organization_id=organization_id,
        idempotency_key=command.idempotency_key,
        request_id=command.request_id,
        command_name=str(command.command_name),
        target_id=command.target_id,
        expected_revision=command.expected_revision,
        payload=command.payload,
        transaction=transaction,
    )
    if reservation.disposition == "reserved":
        return None
    payload = reservation.receipt.result_reference.get("payload")
    if not isinstance(payload, dict):
        raise SubmissionRouteError("idempotency result payload is incomplete")
    return cast(dict[str, Any], payload)


async def _complete(
    transaction: object,
    organization_id: UUID,
    command: WireCommand,
    payload: Mapping[str, Any],
) -> None:
    if not isinstance(transaction, AsyncSession):
        raise TypeError("receipt completion requires AsyncSession")
    repository = CommandReceiptRepository(transaction)
    receipt = await repository.get_by_idempotency_key(
        organization_id, command.idempotency_key, for_update=True
    )
    if receipt is None or not await repository.compare_and_set_status(
        organization_id,
        receipt.id,
        expected_status="reserved",
        new_status="succeeded",
        result_reference={"kind": "response", "payload": dict(payload)},
    ):
        raise SubmissionRouteError("idempotency receipt completion failed")


def _context(request: Request) -> tuple[FoundationRuntime, RequestActor]:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(runtime, FoundationRuntime):
        raise SubmissionRouteError("Foundation runtime is not configured")
    if not isinstance(actor, RequestActor):
        raise AuthorizationError("authenticated request actor is required")
    return runtime, actor


def _wire(body: Mapping[str, Any], name: str, target: UUID) -> WireCommand:
    command = WireCommand.model_validate(dict(body))
    if command.command_name != name or command.target_id != target:
        raise SubmissionRouteError("route command or path target mismatched")
    return command


def _provider(request: Request, name: str) -> ArtifactProvider:
    provider = getattr(request.app.state, f"{name}_artifact_provider", None)
    if not isinstance(provider, ArtifactProvider):
        raise SubmissionRouteError("configured artifact provider is missing")
    return provider


def _state_uuid(request: Request, name: str) -> UUID:
    value = getattr(request.app.state, name, None)
    if isinstance(value, UUID):
        return value
    raise SubmissionRouteError(f"server state {name} is missing")


def _state_int(request: Request, name: str) -> int:
    value = getattr(request.app.state, name, None)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    raise SubmissionRouteError(f"server state {name} is invalid")


def _history(value: SubmissionHistoryProjection) -> dict[str, Any]:
    return {
        "submission_id": str(value.submission_id),
        "course_run_id": str(value.course_run_id),
        "homework_id": str(value.homework_id),
        "current_submission_version_id": (
            str(value.current_submission_version_id)
            if value.current_submission_version_id is not None
            else None
        ),
        "current_publication_id": None,
        "versions": [
            {
                "id": str(item.id),
                "sequence": item.sequence,
                "homework_version_id": str(item.homework_version_id),
                "artifact_reference_id": str(item.artifact_reference_id),
                "artifact_version_id": str(item.artifact_version_id)
                if item.artifact_version_id
                else None,
                "capture_operation_id": str(item.capture_operation_id)
                if item.capture_operation_id
                else None,
                "submitted_at": item.submitted_at.isoformat(),
                "effective_deadline": item.effective_deadline.isoformat(),
                "phase": item.phase,
                "status": item.status,
            }
            for item in value.versions
        ],
        "artifact_versions": [
            {
                "id": str(item.id),
                "artifact_reference_id": str(item.artifact_reference_id),
                "provider": item.provider,
                "provider_version": item.provider_version,
                "content_digest": item.content_digest,
                "media_type": item.media_type,
                "byte_size": item.byte_size,
                "captured_at": item.captured_at.isoformat(),
            }
            for item in value.artifact_versions
        ],
        "review_iterations": [
            {
                "id": str(item.id),
                "iteration_number": item.iteration_number,
                "submission_version_id": str(item.submission_version_id),
                "homework_version_id": str(item.homework_version_id),
                "criterion_set_id": str(item.criterion_set_id),
                "origin": item.origin,
                "status": item.status,
                "revision": item.revision,
            }
            for item in value.review_iterations
        ],
        "review_revisions": [],
        "publications": [],
    }


def _artifact_error_object(
    value: Mapping[str, object] | None,
) -> dict[str, str | None] | None:
    """Project internal/provider failure metadata to frozen ErrorObject."""

    if value is None:
        return None
    raw_code = value.get("code")
    raw_message = value.get("message")
    raw_action = value.get("action")
    code = raw_code if isinstance(raw_code, str) and raw_code else "artifact_unavailable"
    message = raw_message if isinstance(raw_message, str) else "Artifact is unavailable"
    action = raw_action if isinstance(raw_action, str) else None
    return {
        "code": code[:128],
        "message": message[:2048],
        "action": action[:512] if action is not None else None,
    }


def _error(error: BaseException, *, unavailable: bool = False) -> JSONResponse:
    if isinstance(error, AuthorizationError):
        status, code = 403, "authorization_denied"
    elif unavailable:
        status, code = 422, "artifact_unavailable"
    else:
        status, code = 409, "command_conflict"
    return JSONResponse(
        {"code": code, "message": str(error)[:2048], "action": None}, status_code=status
    )


_ERRORS = (
    ArtifactPreflightError,
    AuthorizationError,
    IdempotencyError,
    ReviewIterationError,
    SubmissionRouteError,
    SubmissionServiceError,
    ValidationError,
    ValueError,
)

__all__ = ["router"]
