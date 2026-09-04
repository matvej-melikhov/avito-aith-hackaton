"""Frozen US2 homework HTTP routes."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import AuthorizationError, Authorizer
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import IdempotencyCoordinator, IdempotencyError
from review_platform.application.request_context import RequestActor
from review_platform.application.services.courses import CourseArchivedStateGuard, CourseService
from review_platform.application.services.homeworks import (
    HomeworkHistory,
    HomeworkRequirementsChanged,
    HomeworkRequirementsOutbox,
    HomeworkService,
    HomeworkServiceError,
)
from review_platform.contracts.commands import (
    CreateHomeworkPayload,
    CreateHomeworkVersionPayload,
    PublishHomeworkVersionPayload,
    WireCommand,
)
from review_platform.domain.homework import CriterionRequirement, HomeworkRequirements
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
)
from review_platform.infrastructure.db.models.homework import CourseRunHomework, Criterion
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxError, OutboxService
from review_platform.infrastructure.db.repositories.homeworks import SqlHomeworkRepository
from review_platform.infrastructure.db.repositories.learning import (
    CourseRepository,
    CourseRunRepository,
)
from review_platform.infrastructure.db.repositories.operations import (
    CommandReceiptRepository,
    OutboxMessageRepository,
)

router = APIRouter(tags=["homeworks"])


class HomeworkRouteBoundary(ValueError):
    pass


class SqlHomeworkRequirementsOutbox(HomeworkRequirementsOutbox):
    def __init__(self, runtime: FoundationRuntime) -> None:
        self._runtime = runtime

    async def append(self, event: HomeworkRequirementsChanged, *, transaction: object) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(transaction, AsyncSession):
            raise TypeError("homework outbox requires caller-owned AsyncSession")
        payload = {
            "contract_version": event.contract_version,
            "organization_id": str(event.organization_id),
            "course_run_id": str(event.course_run_id),
            "course_run_homework_id": str(event.course_run_homework_id),
            "homework_id": str(event.homework_id),
            "previous_homework_version_id": (
                str(event.previous_homework_version_id)
                if event.previous_homework_version_id is not None
                else None
            ),
            "current_homework_version_id": str(event.current_homework_version_id),
            "previous_publication_id": (
                str(event.previous_publication_id)
                if event.previous_publication_id is not None
                else None
            ),
            "current_publication_id": str(event.current_publication_id),
            "publication_sequence": event.publication_sequence,
        }
        await OutboxService(
            OutboxMessageRepository(transaction),
            token_factory=self._runtime.id_factory,
            clock=self._runtime.clock,
        ).create(
            OutboxDraft(
                organization_id=event.organization_id,
                message_id=self._runtime.id_factory(),
                aggregate_type="course_run_homework",
                aggregate_id=event.course_run_homework_id,
                event_type="HomeworkRequirementsChanged",
                payload_version=event.contract_version,
                payload=payload,
                available_at=self._runtime.clock(),
                max_attempts=self._runtime.settings.provider_max_attempts,
            )
        )


@router.post(
    "/v1/course-runs/{courseRunId}/homeworks",
    operation_id="createHomework",
    status_code=201,
)
async def create_homework(courseRunId: UUID, request: Request, body: Mapping[str, Any]) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "create_homework", courseRunId)
        payload = cast(CreateHomeworkPayload, command.payload)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            replay = await _reserve(runtime, transaction, command, actor.organization_id)
            if replay is not None:
                await runtime.user_auth_guard.lock_and_revalidate(
                    actor=actor, transaction=transaction
                )
                return _created(replay)
            homework, _ = await _service(runtime, transaction).create_homework(
                transaction=transaction,
                organization_id=actor.organization_id,
                course_run_id=courseRunId,
                expected_course_run_revision=command.expected_revision,
                title=payload.title,
                actor=actor,
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
            await _complete(transaction, actor.organization_id, command, homework.homework_id)
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        return _created(homework.homework_id)
    except _ERRORS as error:
        return _error(error)


@router.post(
    "/v1/homeworks/{homeworkId}/versions",
    operation_id="createHomeworkVersion",
    status_code=201,
)
async def create_homework_version(
    homeworkId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "create_homework_version", homeworkId)
        payload = cast(CreateHomeworkVersionPayload, command.payload)
        requirements = HomeworkRequirements(
            student_text=payload.student_text,
            max_score=Decimal(str(payload.max_score)),
            artifact_kinds=tuple(payload.artifact_kinds),
            estimated_review_minutes=payload.estimated_review_minutes,
            criteria=tuple(
                CriterionRequirement(
                    key=item.key,
                    title=item.title,
                    description=item.description,
                    max_points=Decimal(str(item.max_points)),
                )
                for item in payload.criteria
            ),
        )
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            replay = await _reserve(runtime, transaction, command, actor.organization_id)
            if replay is not None:
                await runtime.user_auth_guard.lock_and_revalidate(
                    actor=actor, transaction=transaction
                )
                return _created(replay)
            version = await _service(runtime, transaction).create_version(
                transaction=transaction,
                organization_id=actor.organization_id,
                homework_id=homeworkId,
                expected_homework_revision=command.expected_revision,
                requirements=requirements,
                actor=actor,
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
            await _complete(transaction, actor.organization_id, command, version.version_id)
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        return _created(version.version_id)
    except _ERRORS as error:
        return _error(error)


@router.post(
    "/v1/homework-versions/{homeworkVersionId}/publish",
    operation_id="publishHomeworkVersion",
    status_code=201,
)
async def publish_homework_version(
    homeworkVersionId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "publish_homework_version", homeworkVersionId)
        payload = cast(PublishHomeworkVersionPayload, command.payload)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            replay = await _reserve(runtime, transaction, command, actor.organization_id)
            if replay is not None:
                await runtime.user_auth_guard.lock_and_revalidate(
                    actor=actor, transaction=transaction
                )
                return _created(replay)
            publication = await _service(runtime, transaction).publish_version(
                transaction=transaction,
                organization_id=actor.organization_id,
                course_run_id=payload.course_run_id,
                homework_version_id=homeworkVersionId,
                expected_version_revision=command.expected_revision,
                submission_deadline=payload.submission_deadline,
                review_deadline=payload.review_deadline,
                actor=actor,
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
            await _complete(transaction, actor.organization_id, command, publication.publication_id)
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        return _created(publication.publication_id)
    except _ERRORS as error:
        return _error(error)


@router.get("/v1/course-runs/{courseRunId}/homeworks", operation_id="listPublishedHomeworks")
async def list_published_homeworks(courseRunId: UUID, request: Request) -> Response:
    try:
        runtime, actor = _context(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            homework_ids = (
                (
                    await transaction.execute(
                        select(CourseRunHomework.homework_id).where(
                            CourseRunHomework.organization_id == actor.organization_id,
                            CourseRunHomework.course_run_id == courseRunId,
                        )
                    )
                )
                .scalars()
                .all()
            )
            items = []
            for homework_id in homework_ids:
                history = await _service(runtime, transaction).history(
                    transaction=transaction,
                    organization_id=actor.organization_id,
                    homework_id=homework_id,
                    actor=actor,
                )
                await _require_history_visible(runtime, transaction, actor, history)
                summary = _published_summary(history, courseRunId)
                if summary is not None:
                    items.append(summary)
        return JSONResponse({"items": items})
    except _ERRORS as error:
        return _error(error)


@router.get("/v1/homeworks/{homeworkId}", operation_id="getHomeworkHistory")
async def get_homework_history(homeworkId: UUID, request: Request) -> Response:
    try:
        runtime, actor = _context(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            history = await _service(runtime, transaction).history(
                transaction=transaction,
                organization_id=actor.organization_id,
                homework_id=homeworkId,
                actor=actor,
            )
            await _require_history_visible(runtime, transaction, actor, history)
            criterion_ids = await _criterion_identity_map(transaction, history)
        return JSONResponse(_history_json(history, criterion_ids))
    except _ERRORS as error:
        return _error(error)


def _service(runtime: FoundationRuntime, transaction: object) -> HomeworkService:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(transaction, AsyncSession):
        raise TypeError("homework service requires caller-owned AsyncSession")
    repository = SqlHomeworkRepository(id_factory=runtime.id_factory)
    return HomeworkService(
        repository=repository,
        archived_guard=CourseArchivedStateGuard(
            courses=CourseRepository(transaction),
            course_runs=CourseRunRepository(transaction),
        ),
        outbox=SqlHomeworkRequirementsOutbox(runtime),
        authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
        audit=AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=runtime.id_factory,
            clock=runtime.clock,
        ),
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    )


async def _require_history_visible(
    runtime: FoundationRuntime,
    transaction: object,
    actor: RequestActor,
    history: HomeworkHistory,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if "methodologist" in actor.roles:
        return
    if not isinstance(transaction, AsyncSession):
        raise TypeError("homework visibility requires caller-owned AsyncSession")
    visible = await CourseService(
        transaction,
        authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
        audit=AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=runtime.id_factory,
            clock=runtime.clock,
        ),
    ).list_courses(
        organization_id=actor.organization_id,
        actor=actor,
        statuses={"active", "archived"},
    )
    if history.homework.course_id not in {course.id for course in visible}:
        raise AuthorizationError("Homework is not visible to the current actor")


async def _reserve(
    runtime: FoundationRuntime,
    transaction: object,
    command: WireCommand,
    organization_id: UUID,
) -> UUID | None:
    placeholder = runtime.id_factory()
    reservation = await IdempotencyCoordinator(
        SqlIdempotencyReceiptRepository(),
        receipt_id_factory=runtime.id_factory,
        result_reference_factory=lambda: {"kind": "pending", "id": str(placeholder)},
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
    raw = reservation.receipt.result_reference.get("id")
    if not isinstance(raw, str) or reservation.receipt.result_reference.get("kind") == "pending":
        raise HomeworkRouteBoundary("idempotency receipt result is incomplete")
    return UUID(raw)


async def _complete(
    transaction: object, organization_id: UUID, command: WireCommand, result_id: UUID
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(transaction, AsyncSession):
        raise TypeError("homework receipt completion requires AsyncSession")
    receipt = await CommandReceiptRepository(transaction).get_by_idempotency_key(
        organization_id, command.idempotency_key, for_update=True
    )
    if receipt is None:
        raise HomeworkRouteBoundary("idempotency receipt disappeared")
    if not await CommandReceiptRepository(transaction).compare_and_set_status(
        organization_id,
        receipt.id,
        expected_status="reserved",
        new_status="succeeded",
        result_reference={"kind": "created_resource", "id": str(result_id)},
    ):
        raise HomeworkRouteBoundary("idempotency receipt completion lost CAS")


def _context(request: Request) -> tuple[FoundationRuntime, RequestActor]:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(runtime, FoundationRuntime):
        raise HomeworkRouteBoundary("Foundation runtime is not configured")
    if not isinstance(actor, RequestActor):
        raise AuthorizationError("authenticated request actor is required")
    return runtime, actor


def _wire(body: Mapping[str, Any], name: str, target: UUID) -> WireCommand:
    command = WireCommand.model_validate(dict(body))
    if command.command_name != name:
        raise HomeworkRouteBoundary("route command does not match exact command")
    if command.target_id != target:
        raise HomeworkRouteBoundary("path target does not match command target")
    return command


def _created(identity: UUID) -> JSONResponse:
    return JSONResponse({"id": str(identity), "revision": 0}, status_code=201)


async def _criterion_identity_map(
    transaction: object,
    history: HomeworkHistory,
) -> dict[tuple[UUID, str], UUID]:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(transaction, AsyncSession):
        raise TypeError("homework history requires caller-owned AsyncSession")
    criterion_set_ids = [version.criterion_set_id for version in history.versions]
    if not criterion_set_ids:
        return {}
    rows = (
        await transaction.execute(
            select(Criterion.criterion_set_id, Criterion.stable_key, Criterion.id).where(
                Criterion.organization_id == history.homework.organization_id,
                Criterion.criterion_set_id.in_(criterion_set_ids),
            )
        )
    ).all()
    return {
        (criterion_set_id, stable_key): criterion_id
        for criterion_set_id, stable_key, criterion_id in rows
    }


def _history_json(
    history: HomeworkHistory,
    criterion_ids: Mapping[tuple[UUID, str], UUID],
) -> dict[str, Any]:
    return {
        "homework_id": str(history.homework.homework_id),
        "homework_revision": history.homework.revision,
        "versions": [_version_json(version, criterion_ids) for version in history.versions],
        "course_run_publications": [
            _publication_json(publication) for publication in history.publications
        ],
    }


def _version_json(
    version: Any,
    criterion_ids: Mapping[tuple[UUID, str], UUID],
) -> dict[str, Any]:
    return {
        "id": str(version.version_id),
        "revision": version.revision,
        "version_number": version.version_number,
        "criterion_set_id": str(version.criterion_set_id),
        "student_text": version.requirements.student_text,
        "max_score": float(version.requirements.max_score),
        "artifact_kinds": list(version.requirements.artifact_kinds),
        "estimated_review_minutes": version.requirements.estimated_review_minutes,
        "criteria": [
            {
                "id": str(criterion_ids[(version.criterion_set_id, criterion.key)]),
                "key": criterion.key,
                "title": criterion.title,
                "description": criterion.description,
                "max_points": float(criterion.max_points),
                "position": criterion.position,
            }
            for criterion in version.requirements.criteria
        ],
    }


def _publication_json(publication: Any) -> dict[str, Any]:
    return {
        "id": str(publication.publication_id),
        "course_run_homework_id": str(publication.course_run_homework_id),
        "course_run_id": str(publication.course_run_id),
        "homework_version_id": str(publication.homework_version_id),
        "publication_sequence": publication.publication_sequence,
        "submission_deadline": publication.submission_deadline.isoformat(),
        "review_deadline": publication.review_deadline.isoformat(),
        "published_at": publication.published_at.isoformat(),
        "is_current": bool(getattr(publication, "is_current", False)),
    }


def _published_summary(history: HomeworkHistory, course_run_id: UUID) -> dict[str, Any] | None:
    publications = [
        publication
        for publication in history.publications
        if publication.course_run_id == course_run_id and getattr(publication, "is_current", False)
    ]
    if not publications:
        return None
    publication = publications[-1]
    version = next(
        item for item in history.versions if item.version_id == publication.homework_version_id
    )
    relation = next(
        item
        for item in history.course_run_homeworks
        if item.course_run_homework_id == publication.course_run_homework_id
    )
    return {
        "course_run_homework_id": str(relation.course_run_homework_id),
        "revision": relation.revision,
        "homework_id": str(history.homework.homework_id),
        "current_version_id": str(version.version_id),
        "title": history.homework.title,
        "max_score": float(version.requirements.max_score),
        "artifact_kinds": list(version.requirements.artifact_kinds),
        "submission_deadline": publication.submission_deadline.isoformat(),
        "review_deadline": publication.review_deadline.isoformat(),
    }


def _error(error: BaseException) -> JSONResponse:
    status = 403 if isinstance(error, AuthorizationError) else 409
    code = "authorization_denied" if status == 403 else "command_conflict"
    return JSONResponse(
        {"code": code, "message": str(error)[:2048], "action": None}, status_code=status
    )


_ERRORS = (
    AuthorizationError,
    HomeworkRouteBoundary,
    HomeworkServiceError,
    IdempotencyError,
    OutboxError,
    ValidationError,
    ValueError,
)

__all__ = ["SqlHomeworkRequirementsOutbox", "router"]
