"""Configured SQL worker for idempotent review-requirement impact projection."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.services.homeworks import HomeworkRequirementsChanged
from review_platform.application.services.review_requirement_impacts import (
    ReviewRequirementImpactAuditPort,
    ReviewRequirementImpactOperationPort,
    ReviewRequirementImpactRepository,
    ReviewRequirementImpactService,
)
from review_platform.domain.primitives import utc_now, uuid7
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    Operation,
    OperationAttempt,
)
from review_platform.infrastructure.db.repositories.operations import (
    AuditEventRepository,
    OutboxMessageRepository,
)
from review_platform.infrastructure.db.repositories.publications import (
    SqlReviewRequirementImpactRepository,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.settings import get_settings

from .registry import Handler, HandlerRegistryError, task_handler

REVIEW_IMPACT_HANDLER_FACTORY_ENV = "REVIEW_PLATFORM_REVIEW_IMPACT_HANDLER_FACTORY"
IN_TREE_REVIEW_IMPACT_FACTORY = (
    "review_platform.infrastructure.tasks.review_impacts:build_sql_review_impact_handler"
)


class ReviewImpactTaskError(HandlerRegistryError):
    """Review impact worker composition or message provenance is invalid."""


@dataclass(frozen=True, slots=True)
class ReviewImpactTaskRuntime:
    session_factory: AsyncSessionFactory
    repository: ReviewRequirementImpactRepository
    operations: ReviewRequirementImpactOperationPort
    audit: ReviewRequirementImpactAuditPort
    id_factory: Callable[[], UUID] = uuid7
    clock: Callable[[], datetime] = utc_now


class ReviewImpactTaskHandler:
    def __init__(self, runtime: ReviewImpactTaskRuntime) -> None:
        self._runtime = runtime

    async def __call__(
        self,
        *,
        organization_id: str,
        message_id: str,
    ) -> Mapping[str, object]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with session_scope(self._runtime.session_factory) as session:
            message = await OutboxMessageRepository(session).get(
                organization_uuid,
                message_uuid,
            )
            if message is None:
                raise ReviewImpactTaskError(
                    "tenant HomeworkRequirementsChanged outbox message was not found"
                )
            if (
                message.organization_id != organization_uuid
                or message.event_type != "HomeworkRequirementsChanged"
                or message.payload_version != "1.1.0"
                or message.aggregate_type != "course_run_homework"
            ):
                raise ReviewImpactTaskError(
                    "outbox row is not a supported HomeworkRequirementsChanged event"
                )
            payload = cast(Mapping[str, object], message.payload)
            event = _homework_requirements_event(payload)
            if (
                event.organization_id != organization_uuid
                or event.course_run_homework_id != message.aggregate_id
            ):
                raise ReviewImpactTaskError(
                    "HomeworkRequirementsChanged aggregate provenance mismatched"
                )
            result = await ReviewRequirementImpactService(
                repository=self._runtime.repository,
                operations=self._runtime.operations,
                audit=self._runtime.audit,
                id_factory=self._runtime.id_factory,
                clock=self._runtime.clock,
            ).consume(
                source_event_id=message_uuid,
                event=event,
                transaction=session,
            )
        return {
            "organization_id": organization_id,
            "message_id": message_id,
            "impact_ids": sorted(
                str(identity)
                for identity in (
                    *result.created_impact_ids,
                    *result.replayed_impact_ids,
                )
            ),
            "proposals": [
                {
                    "review_case_id": str(item.review_case_id),
                    "predecessor_iteration_id": str(
                        item.predecessor_iteration_id
                    ),
                    "effective_homework_version_id": str(
                        item.effective_homework_version_id
                    ),
                    "target_homework_version_id": str(
                        item.target_homework_version_id
                    ),
                    "target_criterion_set_id": str(item.target_criterion_set_id),
                    "origin": item.origin,
                }
                for item in result.proposals
            ],
        }


class SqlReviewImpactOperationRecorder(ReviewRequirementImpactOperationPort):
    """Record every worker execution using the nearest frozen Operation kind."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock

    async def record_ingestion(
        self,
        *,
        organization_id: UUID,
        source_event_id: UUID,
        created_count: int,
        replayed_count: int,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        now = self._clock()
        operation_id = _operation_id(organization_id, source_event_id)
        input_version = f"review-impact:1.1.0:{source_event_id}"
        operation = await session.scalar(
            select(Operation)
            .where(
                Operation.organization_id == organization_id,
                Operation.id == operation_id,
            )
            .with_for_update()
        )
        if operation is None:
            # The frozen Operation kind vocabulary predates local projections;
            # the bounded course-import lane is also used by this handler.
            operation = Operation(
                id=operation_id,
                organization_id=organization_id,
                kind="course_import",
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
        elif operation.kind != "course_import" or operation.input_version != input_version:
            raise ReviewImpactTaskError("review impact Operation provenance mismatched")
        attempt_number = int(
            await session.scalar(
                select(func.max(OperationAttempt.attempt_number)).where(
                    OperationAttempt.organization_id == organization_id,
                    OperationAttempt.operation_id == operation_id,
                )
            )
            or 0
        ) + 1
        session.add(
            OperationAttempt(
                id=self._id_factory(),
                organization_id=organization_id,
                operation_id=operation_id,
                attempt_number=attempt_number,
                worker_identity=(
                    f"review-impact:{source_event_id}:"
                    f"created={created_count}:replayed={replayed_count}"
                ),
                started_at=now,
                finished_at=now,
                outcome="succeeded",
                error_code=None,
                sanitized_error=None,
            )
        )
        operation.state = "succeeded"
        operation.updated_at = now
        operation.finished_at = now
        operation.error_code = None
        operation.sanitized_error = None
        operation.revision += 1
        await session.flush()


class SqlReviewImpactAuditRecorder(ReviewRequirementImpactAuditPort):
    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock

    async def record_impacts(
        self,
        *,
        organization_id: UUID,
        source_event_id: UUID,
        created_impact_ids: tuple[UUID, ...],
        affected_iteration_ids: tuple[UUID, ...],
        transaction: object,
    ) -> None:
        session = _session(transaction)
        await AuditEventRepository(session).append(
            AuditEvent(
                id=self._id_factory(),
                organization_id=organization_id,
                actor_type="worker",
                actor_user_id=None,
                installation_operator_id=None,
                agent_id=None,
                agent_authorization_id=None,
                action="project_review_requirement_impacts",
                entity_type="outbox_message",
                entity_id=source_event_id,
                before_revision=None,
                after_revision=None,
                request_id=source_event_id,
                trace_id=source_event_id,
                outcome="succeeded",
                sanitized_details={
                    "impact_ids": [str(value) for value in created_impact_ids],
                    "affected_iteration_ids": [
                        str(value) for value in affected_iteration_ids
                    ],
                },
                occurred_at=self._clock(),
            )
        )


def build_sql_review_impact_handler() -> Handler:
    """Return an in-tree handler while validating mandatory DB configuration."""

    settings = get_settings()
    if settings.database_url is None:
        raise ReviewImpactTaskError("REVIEW_PLATFORM_DATABASE_URL is required")
    database_url = settings.database_url

    async def configured(*, organization_id: str, message_id: str) -> object:
        engine = create_database_engine(database_url)
        try:
            ids = uuid7
            clock = utc_now
            return await ReviewImpactTaskHandler(
                ReviewImpactTaskRuntime(
                    session_factory=create_session_factory(engine),
                    repository=SqlReviewRequirementImpactRepository(),
                    operations=SqlReviewImpactOperationRecorder(
                        id_factory=ids,
                        clock=clock,
                    ),
                    audit=SqlReviewImpactAuditRecorder(
                        id_factory=ids,
                        clock=clock,
                    ),
                    id_factory=ids,
                    clock=clock,
                )
            )(organization_id=organization_id, message_id=message_id)
        finally:
            await engine.dispose()

    return cast(Handler, configured)


_configured_spec: str | None = None
_configured_handler: Handler | None = None


def validate_review_impact_configuration() -> None:
    _resolve_configured_handler()


def _resolve_configured_handler() -> Handler:
    global _configured_handler, _configured_spec
    specification = os.environ.get(REVIEW_IMPACT_HANDLER_FACTORY_ENV)
    if not specification:
        raise ReviewImpactTaskError(f"{REVIEW_IMPACT_HANDLER_FACTORY_ENV} is required")
    if _configured_handler is not None and _configured_spec == specification:
        return _configured_handler
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise ReviewImpactTaskError(
            f"{REVIEW_IMPACT_HANDLER_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise ReviewImpactTaskError("review impact handler factory is not callable")
    configured = factory()
    if isinstance(configured, ReviewImpactTaskRuntime):
        handler = cast(Handler, ReviewImpactTaskHandler(configured))
    elif callable(configured):
        handler = cast(Handler, configured)
    else:
        raise ReviewImpactTaskError("review impact factory result is not callable")
    _configured_spec = specification
    _configured_handler = handler
    return handler


@task_handler(
    name="review_platform.review_requirement_impacts",
    # The frozen broker/model vocabulary has no projection-specific kind.
    kind="course_import",
    event_type="HomeworkRequirementsChanged",
    requires_auth_revalidation=False,
    startup_validator=validate_review_impact_configuration,
)
async def handle_homework_requirements_changed(
    *,
    organization_id: str,
    message_id: str,
) -> object:
    handler = _resolve_configured_handler()
    result = handler(organization_id=organization_id, message_id=message_id)
    if isinstance(result, Awaitable):
        return await result
    return result


def _homework_requirements_event(
    payload: Mapping[str, object],
) -> HomeworkRequirementsChanged:
    if payload.get("contract_version") != "1.1.0":
        raise ReviewImpactTaskError(
            "HomeworkRequirementsChanged contract version is unsupported"
        )
    publication_sequence = payload.get("publication_sequence")
    if (
        not isinstance(publication_sequence, int)
        or isinstance(publication_sequence, bool)
        or publication_sequence < 1
    ):
        raise ReviewImpactTaskError(
            "HomeworkRequirementsChanged publication sequence is invalid"
        )
    return HomeworkRequirementsChanged(
        organization_id=_required_uuid(payload, "organization_id"),
        course_run_id=_required_uuid(payload, "course_run_id"),
        course_run_homework_id=_required_uuid(
            payload,
            "course_run_homework_id",
        ),
        homework_id=_required_uuid(payload, "homework_id"),
        previous_homework_version_id=_optional_uuid(
            payload,
            "previous_homework_version_id",
        ),
        current_homework_version_id=_required_uuid(
            payload,
            "current_homework_version_id",
        ),
        previous_publication_id=_optional_uuid(
            payload,
            "previous_publication_id",
        ),
        current_publication_id=_required_uuid(
            payload,
            "current_publication_id",
        ),
        publication_sequence=publication_sequence,
    )


def _required_uuid(payload: Mapping[str, object], field: str) -> UUID:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ReviewImpactTaskError(f"HomeworkRequirementsChanged {field} is invalid")
    return _uuid(value, field=field)


def _optional_uuid(payload: Mapping[str, object], field: str) -> UUID | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReviewImpactTaskError(f"HomeworkRequirementsChanged {field} is invalid")
    return _uuid(value, field=field)


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ReviewImpactTaskError(
            f"HomeworkRequirementsChanged {field} must be a UUID"
        ) from error


def _operation_id(organization_id: UUID, source_event_id: UUID) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"review-platform:{organization_id}:review-impact:{source_event_id}",
    )


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise ReviewImpactTaskError("review impact SQL adapter requires AsyncSession")
    return transaction


__all__ = [
    "IN_TREE_REVIEW_IMPACT_FACTORY",
    "REVIEW_IMPACT_HANDLER_FACTORY_ENV",
    "ReviewImpactTaskError",
    "ReviewImpactTaskHandler",
    "ReviewImpactTaskRuntime",
    "SqlReviewImpactAuditRecorder",
    "SqlReviewImpactOperationRecorder",
    "build_sql_review_impact_handler",
    "handle_homework_requirements_changed",
    "validate_review_impact_configuration",
]
