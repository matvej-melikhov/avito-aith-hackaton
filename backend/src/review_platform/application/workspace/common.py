"""Shared scope checks and existing SQL receipt/audit integration."""

from __future__ import annotations

import hashlib
import json
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import IdempotencyReceipt
from review_platform.application.request_context import RequestActor
from review_platform.contracts.workspace import QuotaView, WorkspaceCommand
from review_platform.infrastructure.db.adapters import SqlIdempotencyReceiptRepository
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AuditEvent,
    CommandReceipt,
    Course,
    CourseMembership,
    CourseRun,
)


class WorkspaceFailure(Exception):
    def __init__(self, code: str, message: str, status: int = 409, quota: QuotaView | None = None):
        super().__init__(message)
        self.code, self.status, self.quota = code, status, quota


def digest(value: JsonValue) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    )


async def row[M: Base](
    session: AsyncSession, model: type[M], org: UUID, identity: UUID, *, lock: bool = False
) -> M:
    statement = select(model).filter_by(organization_id=org, id=identity)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    result = await session.scalar(statement)
    if result is None:
        raise WorkspaceFailure("not_found", "Объект не найден или недоступен.", 404)
    return result


def require_roles(actor: RequestActor, *roles: str) -> UUID:
    if actor.actor_type != "user" or actor.user_id is None or not actor.roles.intersection(roles):
        raise WorkspaceFailure("forbidden", "Действие недоступно этой роли.", 403)
    return actor.user_id


async def course_scope(
    session: AsyncSession, actor: RequestActor, run_id: UUID, *, write: bool = False
) -> CourseRun:
    run = await row(session, CourseRun, actor.organization_id, run_id)
    course = await row(session, Course, actor.organization_id, run.course_id)
    if write and (run.status != "active" or course.status != "active"):
        raise WorkspaceFailure("archived", "Поток или курс закрыт для новых действий.")
    if "methodologist" not in actor.roles:
        membership = await session.scalar(
            select(CourseMembership.id).where(
                CourseMembership.organization_id == actor.organization_id,
                CourseMembership.course_run_id == run_id,
                CourseMembership.user_id == actor.user_id,
                CourseMembership.status == "active",
                CourseMembership.kind.in_(list(actor.roles)),
            )
        )
        if membership is None:
            raise WorkspaceFailure("forbidden", "Нет доступа к этому потоку.", 403)
    return run


async def reserve[T: BaseModel](
    session: AsyncSession,
    runtime: FoundationRuntime,
    actor: RequestActor,
    command: WorkspaceCommand[T],
    name: str,
    target: UUID,
) -> tuple[CommandReceipt, bool]:
    if command.command_name != name or command.target_id != target:
        raise WorkspaceFailure("command_mismatch", "Команда не соответствует маршруту.", 422)
    await runtime.user_auth_guard.revalidate(actor=actor)
    payload = cast(
        JsonValue,
        {
            "command": command.model_dump(mode="json"),
            "user_id": str(actor.user_id),
            "auth_epoch": actor.auth_epoch,
        },
    )
    proposed = IdempotencyReceipt(
        receipt_id=runtime.id_factory(),
        organization_id=actor.organization_id,
        idempotency_key=command.idempotency_key,
        request_id=command.request_id,
        command_name=name,
        target_id=target,
        expected_revision=command.expected_revision,
        payload_digest=digest(payload),
        result_reference={},
    )
    found, created = await SqlIdempotencyReceiptRepository().reserve(proposed, transaction=session)
    if found.payload_digest != proposed.payload_digest:
        raise WorkspaceFailure(
            "idempotency_conflict", "Ключ запроса уже использован: содержимое отличается."
        )
    receipt = await row(session, CommandReceipt, actor.organization_id, found.receipt_id, lock=True)
    if not created and receipt.status != "succeeded":
        raise WorkspaceFailure("request_pending", "Этот запрос ещё выполняется.")
    return receipt, created


def finish(
    session: AsyncSession,
    runtime: FoundationRuntime,
    actor: RequestActor,
    receipt: CommandReceipt,
    value: JsonValue,
    details: dict[str, JsonValue] | None = None,
) -> JsonValue:
    receipt.status = "succeeded"
    receipt.result_reference = {"value": value}
    session.add(
        AuditEvent(
            id=runtime.id_factory(),
            organization_id=actor.organization_id,
            actor_type="user",
            actor_user_id=actor.user_id,
            action=receipt.command_name,
            entity_type="workspace",
            entity_id=receipt.target_id,
            before_revision=receipt.expected_revision,
            after_revision=None,
            request_id=receipt.request_id,
            trace_id=receipt.request_id,
            outcome="succeeded",
            sanitized_details=details or {},
            occurred_at=runtime.clock(),
        )
    )
    return value


def replay(receipt: CommandReceipt) -> JsonValue:
    return cast(JsonValue, (receipt.result_reference or {}).get("value"))


def revision(actual: int, expected: int) -> None:
    if actual != expected:
        raise WorkspaceFailure(
            "revision_conflict", "Данные изменились. Обновите их перед сохранением."
        )


async def student_epoch_valid(
    session: AsyncSession,
    org: UUID,
    user: UUID,
    course_run_id: UUID,
    membership_revision: int,
    auth_epoch: int,
) -> bool:
    """Fence asynchronous completion against the same enrollment and auth epoch."""
    from review_platform.infrastructure.db.models import CourseMembership, OrganizationMembership

    member = await session.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == org,
            OrganizationMembership.user_id == user,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        member is None
        or member.status != "active"
        or "student" not in member.roles
        or member.revision != membership_revision
        or member.auth_epoch != auth_epoch
    ):
        return False
    enrolled = await session.scalar(
        select(CourseMembership.id)
        .where(
            CourseMembership.organization_id == org,
            CourseMembership.user_id == user,
            CourseMembership.course_run_id == course_run_id,
            CourseMembership.status == "active",
            CourseMembership.kind == "student",
        )
        .with_for_update()
    )
    return enrolled is not None
