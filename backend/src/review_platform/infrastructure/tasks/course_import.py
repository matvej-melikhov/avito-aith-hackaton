"""Registered Taskiq adapter for durable, tenant-scoped course imports."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime
from importlib import import_module
from typing import Any, cast
from uuid import UUID

from review_platform.application.auth_guards.membership import UserMembershipAuthGuard
from review_platform.application.ports.providers import (
    CONTRACT_VERSION,
    CourseImportProvider,
    JsonValue,
    ProviderPayloadValidator,
)
from review_platform.application.request_context import AuthVersionGuard, RequestActor, Role
from review_platform.application.services.course_import import (
    CourseImportCommand,
    CourseImportRepositories,
    CourseImportService,
    CourseImportView,
)
from review_platform.domain.primitives import require_utc, utc_now, uuid7
from review_platform.infrastructure.db.models.operations import OutboxMessage
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.operations import (
    OutboxMessageRepository,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.providers.mocks import JsonSchemaPayloadValidator
from review_platform.settings import get_settings

from .broker import RetryableTaskError
from .registry import task_handler

COURSE_IMPORT_PROVIDER_FACTORY_ENV = "REVIEW_PLATFORM_COURSE_IMPORT_PROVIDER_FACTORY"


class CourseImportTaskError(RuntimeError):
    """A durable CourseImportRequested message is malformed or unavailable."""


class CourseImportTaskHandler:
    """Execute one persisted import request with early and final auth checks."""

    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        provider: CourseImportProvider,
        validator: ProviderPayloadValidator,
        auth_guard: AuthVersionGuard | None = None,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        worker_identity: str = "taskiq-course-import",
    ) -> None:
        if not worker_identity or len(worker_identity) > 255:
            raise ValueError("worker identity must contain 1..255 characters")
        self._session_factory = session_factory
        self._provider = provider
        self._validator = validator
        self._auth_guard = auth_guard or UserMembershipAuthGuard(session_factory)
        self._id_factory = id_factory
        self._clock = clock
        self._worker_identity = worker_identity

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        view: CourseImportView
        async with session_scope(self._session_factory) as session:
            outbox_repository = OutboxMessageRepository(session)
            message = await outbox_repository.get(
                organization_uuid,
                message_uuid,
                for_update=False,
            )
            if message is None:
                raise CourseImportTaskError("tenant-scoped outbox message was not found")
            payload = _validate_message(message, organization_id=organization_uuid)
            actor = _actor(payload, organization_id=organization_uuid)
            await self._auth_guard.revalidate(actor=actor)
            command = _command(
                payload,
                organization_id=organization_uuid,
                worker_identity=self._worker_identity,
            )
            service = CourseImportService(
                provider=self._provider,
                validator=self._validator,
                repositories=CourseImportRepositories.from_session(session),
                id_factory=self._id_factory,
                clock=self._clock,
            )
            view = await service.execute(command)
            if not view.complete and view.next_cursor is not None and view.state == "partial":
                await self._enqueue_next_page(
                    message=message,
                    payload=payload,
                    command=command,
                    next_cursor=view.next_cursor,
                    outbox_repository=outbox_repository,
                )
            # Provider import is read-only externally.  If authority changed
            # while it ran, rolling back this transaction is safe and prevents
            # a claimed job from committing after revocation.
            await self._auth_guard.lock_and_revalidate(actor=actor, transaction=session)

        result = _view_payload(view)
        if view.state == "retryable_failed":
            raise RetryableTaskError(str(result.get("error") or "course import retryable failure"))
        return result

    async def _enqueue_next_page(
        self,
        *,
        message: OutboxMessage,
        payload: Mapping[str, JsonValue],
        command: CourseImportCommand,
        next_cursor: str,
        outbox_repository: OutboxMessageRepository,
    ) -> None:
        next_message_id = self._id_factory()
        next_payload = dict(payload)
        next_payload["cursor"] = next_cursor
        await OutboxService(
            outbox_repository,
            token_factory=self._id_factory,
            clock=self._clock,
        ).create(
            OutboxDraft(
                organization_id=command.organization_id,
                message_id=next_message_id,
                aggregate_type=message.aggregate_type,
                aggregate_id=message.aggregate_id,
                event_type=message.event_type,
                payload_version=message.payload_version,
                payload=next_payload,
                available_at=require_utc(self._clock()),
                max_attempts=message.max_attempts,
            )
        )


def _validate_message(
    message: OutboxMessage,
    *,
    organization_id: UUID,
) -> Mapping[str, JsonValue]:
    if message.organization_id != organization_id:
        raise CourseImportTaskError("outbox tenant does not match Taskiq tenant")
    if message.event_type != "CourseImportRequested":
        raise CourseImportTaskError("outbox event is not CourseImportRequested")
    if message.payload_version != CONTRACT_VERSION:
        raise CourseImportTaskError("outbox payload contract version is unsupported")
    if message.aggregate_type != "operation":
        raise CourseImportTaskError("course import outbox aggregate must be an Operation")
    payload = cast(Mapping[str, JsonValue], message.payload)
    operation_id = _required_uuid(payload, "operation_id")
    if operation_id != message.aggregate_id:
        raise CourseImportTaskError("outbox aggregate does not match import Operation")
    return payload


def _actor(payload: Mapping[str, JsonValue], *, organization_id: UUID) -> RequestActor:
    raw = payload.get("actor")
    if not isinstance(raw, Mapping):
        raise CourseImportTaskError("course import payload is missing actor provenance")
    if raw.get("type") != "user":
        raise CourseImportTaskError("course import requires user actor provenance")
    roles_value = raw.get("roles")
    if not isinstance(roles_value, list) or not all(isinstance(role, str) for role in roles_value):
        raise CourseImportTaskError("course import actor roles are invalid")
    roles = cast(list[Role], roles_value)
    if "methodologist" not in roles:
        raise CourseImportTaskError("course import actor must be a methodologist")
    revision = _required_int(raw, "membership_revision", minimum=0)
    auth_epoch = _required_int(raw, "auth_epoch", minimum=0)
    return RequestActor.user(
        organization_id=organization_id,
        user_id=_required_uuid(raw, "user_id"),
        roles=roles,
        membership_revision=revision,
        auth_epoch=auth_epoch,
    )


def _command(
    payload: Mapping[str, JsonValue],
    *,
    organization_id: UUID,
    worker_identity: str,
) -> CourseImportCommand:
    cursor = payload.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise CourseImportTaskError("course import cursor must be a string or null")
    return CourseImportCommand(
        organization_id=organization_id,
        operation_id=_required_uuid(payload, "operation_id"),
        credential_binding_id=_required_uuid(payload, "credential_binding_id"),
        credential_binding_version=_required_int(
            payload,
            "credential_binding_version",
            minimum=1,
        ),
        provider=_required_string(payload, "provider"),
        external_url=_required_string(payload, "external_url"),
        cursor=cursor,
        page_size=_optional_int(payload, "page_size", default=100, minimum=1),
        course_binding_version=_optional_int(
            payload,
            "course_binding_version",
            default=1,
            minimum=1,
        ),
        worker_identity=worker_identity,
    )


def _view_payload(view: CourseImportView) -> dict[str, Any]:
    return {
        "operation_id": str(view.operation_id),
        "organization_id": str(view.organization_id),
        "kind": view.kind,
        "input_version": view.input_version,
        "state": view.state,
        "attempts": [
            {
                "attempt_number": attempt.attempt_number,
                "state": attempt.state,
                "started_at": attempt.started_at.isoformat(),
                "finished_at": (
                    attempt.finished_at.isoformat() if attempt.finished_at is not None else None
                ),
                "error": dict(attempt.error) if attempt.error is not None else None,
            }
            for attempt in view.attempts
        ],
        "error": dict(view.error) if view.error is not None else None,
        "course_id": str(view.course_id) if view.course_id is not None else None,
        "course_run_ids": [str(run_id) for run_id in view.course_run_ids],
        "complete": view.complete,
        "next_cursor": view.next_cursor,
    }


def _required_string(payload: Mapping[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise CourseImportTaskError(f"{field} must be a non-empty string")
    return value


def _required_uuid(payload: Mapping[str, JsonValue], field: str) -> UUID:
    return _uuid(_required_string(payload, field), field=field)


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise CourseImportTaskError(f"{field} must be a UUID") from error


def _required_int(
    payload: Mapping[str, JsonValue],
    field: str,
    *,
    minimum: int,
) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise CourseImportTaskError(f"{field} must be an integer >= {minimum}")
    return value


def _optional_int(
    payload: Mapping[str, JsonValue],
    field: str,
    *,
    default: int,
    minimum: int,
) -> int:
    if field not in payload:
        return default
    return _required_int(payload, field, minimum=minimum)


def _load_provider(specification: str) -> CourseImportProvider:
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise CourseImportTaskError(
            f"{COURSE_IMPORT_PROVIDER_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise CourseImportTaskError("course import provider factory is not callable")
    provider = factory()
    if not isinstance(provider, CourseImportProvider):
        raise CourseImportTaskError("factory result does not satisfy CourseImportProvider")
    return provider


async def _run_configured(*, organization_id: str, message_id: str) -> Mapping[str, Any]:
    settings = get_settings()
    if settings.database_url is None:
        raise CourseImportTaskError("REVIEW_PLATFORM_DATABASE_URL is required")
    provider_spec = os.environ.get(COURSE_IMPORT_PROVIDER_FACTORY_ENV)
    if provider_spec is None:
        raise CourseImportTaskError(f"{COURSE_IMPORT_PROVIDER_FACTORY_ENV} is required")
    engine = create_database_engine(settings.database_url)
    try:
        handler = CourseImportTaskHandler(
            session_factory=create_session_factory(engine),
            provider=_load_provider(provider_spec),
            validator=JsonSchemaPayloadValidator(),
        )
        return await handler(organization_id=organization_id, message_id=message_id)
    finally:
        await engine.dispose()


@task_handler(
    name="review_platform.course_import",
    kind="course_import",
    event_type="CourseImportRequested",
    # The handler carries full actor provenance and performs both early and
    # final locked revalidation itself; a label-only middleware check is not a
    # substitute for the before-commit DB check.
    requires_auth_revalidation=False,
)
async def handle_course_import(*, organization_id: str, message_id: str) -> Mapping[str, Any]:
    return await _run_configured(organization_id=organization_id, message_id=message_id)


__all__ = [
    "COURSE_IMPORT_PROVIDER_FACTORY_ENV",
    "CourseImportTaskError",
    "CourseImportTaskHandler",
    "handle_course_import",
]
