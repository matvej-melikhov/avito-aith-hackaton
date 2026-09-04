"""MySQL-backed course service lifecycle, visibility, and archive guard tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditRecorder
from review_platform.application.auth_guards.membership import UserMembershipAuthGuard
from review_platform.application.authorization import AuthorizationDenied, Authorizer
from review_platform.application.request_context import RequestActor, Role
from review_platform.application.services.courses import (
    ArchivedCourseActionDenied,
    CourseNotFound,
    CourseRevisionConflict,
    CourseRunNotFound,
    CourseService,
)
from review_platform.domain.primitives import uuid7
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.identity import OrganizationMembership, User
from review_platform.infrastructure.db.models.learning import Course, CourseMembership, CourseRun
from review_platform.infrastructure.db.models.operations import AuditEvent, OutboxMessage
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
METHOD = UUID("00000000-0000-7000-8000-000000000201")
REVIEWER = UUID("00000000-0000-7000-8000-000000000202")
STUDENT = UUID("00000000-0000-7000-8000-000000000203")
OTHER_METHOD = UUID("00000000-0000-7000-8000-000000000204")
COURSE_ACTIVE = UUID("00000000-0000-7000-8000-000000000211")
COURSE_HISTORY = UUID("00000000-0000-7000-8000-000000000212")
COURSE_OTHER = UUID("00000000-0000-7000-8000-000000000213")
RUN_ACTIVE = UUID("00000000-0000-7000-8000-000000000221")
RUN_HISTORY = UUID("00000000-0000-7000-8000-000000000222")
RUN_OTHER = UUID("00000000-0000-7000-8000-000000000223")
MESSAGE = UUID("00000000-0000-7000-8000-000000000231")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _actor(
    *,
    organization_id: UUID,
    user_id: UUID,
    roles: set[Role],
) -> RequestActor:
    return RequestActor.user(
        organization_id=organization_id,
        user_id=user_id,
        roles=roles,
        membership_revision=0,
        auth_epoch=0,
    )


METHOD_ACTOR = _actor(
    organization_id=ORG_A,
    user_id=METHOD,
    roles={"methodologist"},
)
REVIEWER_ACTOR = _actor(
    organization_id=ORG_A,
    user_id=REVIEWER,
    roles={"reviewer"},
)
STUDENT_ACTOR = _actor(
    organization_id=ORG_A,
    user_id=STUDENT,
    roles={"student"},
)
OTHER_METHOD_ACTOR = _actor(
    organization_id=ORG_B,
    user_id=OTHER_METHOD,
    roles={"methodologist"},
)


async def _seed_world(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=METHOD, display_name="Methodologist", status="active"),
                User(id=REVIEWER, display_name="Reviewer", status="active"),
                User(id=STUDENT, display_name="Student", status="active"),
                User(id=OTHER_METHOD, display_name="Other methodologist", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _organization_membership(
                    organization_id=ORG_A,
                    membership_id=UUID("00000000-0000-7000-8000-000000000241"),
                    user_id=METHOD,
                    roles=["methodologist"],
                ),
                _organization_membership(
                    organization_id=ORG_A,
                    membership_id=UUID("00000000-0000-7000-8000-000000000242"),
                    user_id=REVIEWER,
                    roles=["reviewer"],
                ),
                _organization_membership(
                    organization_id=ORG_A,
                    membership_id=UUID("00000000-0000-7000-8000-000000000243"),
                    user_id=STUDENT,
                    roles=["student"],
                ),
                _organization_membership(
                    organization_id=ORG_B,
                    membership_id=UUID("00000000-0000-7000-8000-000000000244"),
                    user_id=OTHER_METHOD,
                    roles=["methodologist"],
                ),
            ]
        )
        session.add_all(
            [
                _course(
                    organization_id=ORG_A,
                    course_id=COURSE_ACTIVE,
                    title="Active Course",
                    status="active",
                ),
                _course(
                    organization_id=ORG_A,
                    course_id=COURSE_HISTORY,
                    title="History Course",
                    status="archived",
                ),
                _course(
                    organization_id=ORG_B,
                    course_id=COURSE_OTHER,
                    title="Other Tenant Course",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _course_run(
                    organization_id=ORG_A,
                    course_id=COURSE_ACTIVE,
                    course_run_id=RUN_ACTIVE,
                    title="Active Run",
                    status="active",
                ),
                _course_run(
                    organization_id=ORG_A,
                    course_id=COURSE_HISTORY,
                    course_run_id=RUN_HISTORY,
                    title="History Run",
                    status="archived",
                ),
                _course_run(
                    organization_id=ORG_B,
                    course_id=COURSE_OTHER,
                    course_run_id=RUN_OTHER,
                    title="Other Run",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _course_membership(
                    membership_id=UUID("00000000-0000-7000-8000-000000000251"),
                    course_run_id=RUN_ACTIVE,
                    user_id=REVIEWER,
                    kind="reviewer",
                    status="active",
                    source="self_selected",
                ),
                _course_membership(
                    membership_id=UUID("00000000-0000-7000-8000-000000000252"),
                    course_run_id=RUN_ACTIVE,
                    user_id=STUDENT,
                    kind="student",
                    status="active",
                    source="imported",
                ),
                _course_membership(
                    membership_id=UUID("00000000-0000-7000-8000-000000000253"),
                    course_run_id=RUN_HISTORY,
                    user_id=STUDENT,
                    kind="student",
                    status="archived",
                    source="imported",
                ),
            ]
        )


def _organization_membership(
    *,
    organization_id: UUID,
    membership_id: UUID,
    user_id: UUID,
    roles: list[str],
) -> OrganizationMembership:
    return OrganizationMembership(
        id=membership_id,
        organization_id=organization_id,
        user_id=user_id,
        roles=roles,
        status="active",
        revision=0,
        auth_epoch=0,
        revoked_at=None,
        revoked_by=None,
    )


def _course(
    *,
    organization_id: UUID,
    course_id: UUID,
    title: str,
    status: str,
) -> Course:
    return Course(
        id=course_id,
        organization_id=organization_id,
        title=title,
        description="",
        source_kind="standalone",
        status=status,
        revision=0,
    )


def _course_run(
    *,
    organization_id: UUID,
    course_id: UUID,
    course_run_id: UUID,
    title: str,
    status: str,
) -> CourseRun:
    return CourseRun(
        id=course_run_id,
        organization_id=organization_id,
        course_id=course_id,
        title=title,
        starts_at=None,
        ends_at=None,
        timezone="Europe/Moscow",
        status=status,
        revision=0,
    )


def _course_membership(
    *,
    membership_id: UUID,
    course_run_id: UUID,
    user_id: UUID,
    kind: str,
    status: str,
    source: str,
) -> CourseMembership:
    return CourseMembership(
        id=membership_id,
        organization_id=ORG_A,
        course_run_id=course_run_id,
        user_id=user_id,
        kind=kind,
        source=source,
        status=status,
        external_version="fixture-v1" if source == "imported" else None,
        joined_at=NOW,
        removed_at=None,
    )


def _service(
    session: AsyncSession,
    factory: AsyncSessionFactory,
    *,
    id_factory: Callable[[], UUID] = uuid7,
) -> CourseService:
    return CourseService(
        session,
        authorizer=Authorizer(UserMembershipAuthGuard(factory), clock=lambda: NOW),
        audit=AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=id_factory,
            clock=lambda: NOW,
        ),
    )


async def test_role_visibility_roster_history_and_tenant_boundary(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_world(foundation_session_factory)
    async with foundation_session_factory() as session:
        service = _service(session, foundation_session_factory)

        method_courses = await service.list_courses(
            organization_id=ORG_A,
            actor=METHOD_ACTOR,
            statuses={"active", "archived"},
        )
        reviewer_courses = await service.list_courses(
            organization_id=ORG_A,
            actor=REVIEWER_ACTOR,
            statuses={"active"},
        )
        student_history = await service.list_courses(
            organization_id=ORG_A,
            actor=STUDENT_ACTOR,
            statuses={"archived"},
        )
        assert [course.id for course in method_courses] == [COURSE_ACTIVE, COURSE_HISTORY]
        assert [course.id for course in reviewer_courses] == [COURSE_ACTIVE]
        assert [course.id for course in student_history] == [COURSE_HISTORY]

        reviewer_runs = await service.list_course_runs(
            organization_id=ORG_A,
            actor=REVIEWER_ACTOR,
            course_id=COURSE_ACTIVE,
            statuses={"active", "archived"},
        )
        assert [course_run.id for course_run in reviewer_runs] == [RUN_ACTIVE]

        active_roster = await service.read_roster(
            organization_id=ORG_A,
            actor=METHOD_ACTOR,
            course_run_id=RUN_ACTIVE,
            include_history=False,
        )
        history_roster = await service.read_roster(
            organization_id=ORG_A,
            actor=METHOD_ACTOR,
            course_run_id=RUN_HISTORY,
            include_history=True,
        )
        assert [(row.user_id, row.kind) for row in active_roster] == [
            (REVIEWER, "reviewer"),
            (STUDENT, "student"),
        ]
        assert [(row.user_id, row.status) for row in history_roster] == [
            (STUDENT, "archived")
        ]
        with pytest.raises(AuthorizationDenied):
            await service.read_roster(
                organization_id=ORG_A,
                actor=REVIEWER_ACTOR,
                course_run_id=RUN_ACTIVE,
                include_history=True,
            )
        with pytest.raises(AuthorizationDenied):
            await service.list_courses(
                organization_id=ORG_B,
                actor=METHOD_ACTOR,
                statuses={"active"},
            )
        other_service = _service(session, foundation_session_factory)
        with pytest.raises(CourseNotFound):
            await other_service.list_course_runs(
                organization_id=ORG_B,
                actor=OTHER_METHOD_ACTOR,
                course_id=COURSE_ACTIVE,
                statuses={"active"},
            )
        await session.rollback()


async def test_archive_restore_uses_exact_revision_and_writes_exact_audit(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_world(foundation_session_factory)
    audit_ids = iter(
        [
            UUID("00000000-0000-7000-8000-000000000261"),
            UUID("00000000-0000-7000-8000-000000000262"),
        ]
    )
    async with session_scope(foundation_session_factory) as session:
        service = _service(session, foundation_session_factory, id_factory=lambda: next(audit_ids))
        archived = await service.archive_course(
            organization_id=ORG_A,
            course_id=COURSE_ACTIVE,
            expected_revision=0,
            actor=METHOD_ACTOR,
            request_id=UUID("00000000-0000-7000-8000-000000000271"),
            trace_id=UUID("00000000-0000-7000-8000-000000000272"),
            reason="course complete",
        )
        assert (archived.status, archived.revision) == ("archived", 1)
        with pytest.raises(CourseRevisionConflict, match="current revision"):
            await service.restore_course(
                organization_id=ORG_A,
                course_id=COURSE_ACTIVE,
                expected_revision=0,
                actor=METHOD_ACTOR,
                request_id=UUID("00000000-0000-7000-8000-000000000273"),
                trace_id=UUID("00000000-0000-7000-8000-000000000274"),
            )
        restored = await service.restore_course(
            organization_id=ORG_A,
            course_id=COURSE_ACTIVE,
            expected_revision=1,
            actor=METHOD_ACTOR,
            request_id=UUID("00000000-0000-7000-8000-000000000275"),
            trace_id=UUID("00000000-0000-7000-8000-000000000276"),
        )
        assert (restored.status, restored.revision) == ("active", 2)

    async with foundation_session_factory() as session:
        events = (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == ORG_A,
                    AuditEvent.entity_type == "course",
                    AuditEvent.entity_id == COURSE_ACTIVE,
                )
                .order_by(AuditEvent.id)
            )
        ).scalars().all()
        assert [event.action for event in events] == ["archive_course", "restore_course"]
        assert [(event.before_revision, event.after_revision) for event in events] == [
            (0, 1),
            (1, 2),
        ]
        assert events[0].actor_user_id == METHOD
        assert events[0].sanitized_details == {
            "from_status": "active",
            "to_status": "archived",
            "reason": "course complete",
        }
        assert events[1].sanitized_details == {
            "from_status": "archived",
            "to_status": "active",
            "reason": None,
        }


async def test_two_archive_transactions_have_one_cas_winner(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_world(foundation_session_factory)
    ready = anyio.Event()
    attempts = 0
    outcomes: list[str] = []

    async def archive(request_suffix: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            ready.set()
        await ready.wait()
        try:
            async with session_scope(foundation_session_factory) as session:
                await _service(session, foundation_session_factory).archive_course(
                    organization_id=ORG_A,
                    course_id=COURSE_ACTIVE,
                    expected_revision=0,
                    actor=METHOD_ACTOR,
                    request_id=UUID(
                        f"00000000-0000-7000-8000-{request_suffix:012d}"
                    ),
                    trace_id=UUID(
                        f"00000000-0000-7000-8000-{request_suffix + 10:012d}"
                    ),
                    reason="concurrent archive",
                )
            outcomes.append("succeeded")
        except CourseRevisionConflict:
            outcomes.append("conflict")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(archive, 301)
        tasks.start_soon(archive, 302)

    assert sorted(outcomes) == ["conflict", "succeeded"]
    async with foundation_session_factory() as session:
        course = await session.get(Course, COURSE_ACTIVE)
        assert course is not None
        assert (course.status, course.revision) == ("archived", 1)
        event_count = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.organization_id == ORG_A,
                AuditEvent.entity_id == COURSE_ACTIVE,
                AuditEvent.action == "archive_course",
            )
        )
        assert event_count == 1


async def test_archived_parent_or_child_blocks_only_new_work_and_restore_reenables_it(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_world(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        session.add(
            OutboxMessage(
                organization_id=ORG_A,
                message_id=MESSAGE,
                aggregate_type="review_publication",
                aggregate_id=RUN_ACTIVE,
                event_type="ExternalDeliveryRequested",
                payload_version="1.1.0",
                payload={"course_run_id": str(RUN_ACTIVE)},
                available_at=NOW,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                enqueue_state="pending",
                completed_at=None,
                attempts=0,
                max_attempts=5,
                error_code=None,
                sanitized_error=None,
            )
        )

    audit_counter = 400

    def audit_id() -> UUID:
        nonlocal audit_counter
        audit_counter += 1
        return UUID(f"00000000-0000-7000-8000-{audit_counter:012d}")

    async with session_scope(foundation_session_factory) as session:
        service = _service(session, foundation_session_factory, id_factory=audit_id)
        for action in ("recommendation", "open_review", "publication"):
            await service.archived_state_guard.require_active(
                organization_id=ORG_A,
                course_run_id=RUN_ACTIVE,
                action=action,
            )
        await service.archive_course(
            organization_id=ORG_A,
            course_id=COURSE_ACTIVE,
            expected_revision=0,
            actor=METHOD_ACTOR,
            request_id=UUID("00000000-0000-7000-8000-000000000411"),
            trace_id=UUID("00000000-0000-7000-8000-000000000412"),
            reason="archive parent",
        )
        for action in ("recommendation", "open_review", "publication"):
            with pytest.raises(ArchivedCourseActionDenied, match="archived"):
                await service.archived_state_guard.require_active(
                    organization_id=ORG_A,
                    course_run_id=RUN_ACTIVE,
                    action=action,
                )
        await service.restore_course(
            organization_id=ORG_A,
            course_id=COURSE_ACTIVE,
            expected_revision=1,
            actor=METHOD_ACTOR,
            request_id=UUID("00000000-0000-7000-8000-000000000413"),
            trace_id=UUID("00000000-0000-7000-8000-000000000414"),
        )
        await service.archived_state_guard.require_active(
            organization_id=ORG_A,
            course_run_id=RUN_ACTIVE,
            action="open_review",
        )
        await service.archive_course_run(
            organization_id=ORG_A,
            course_run_id=RUN_ACTIVE,
            expected_revision=0,
            actor=METHOD_ACTOR,
            request_id=UUID("00000000-0000-7000-8000-000000000415"),
            trace_id=UUID("00000000-0000-7000-8000-000000000416"),
            reason="archive child",
        )
        with pytest.raises(ArchivedCourseActionDenied, match="archived"):
            await service.archived_state_guard.require_active(
                organization_id=ORG_A,
                course_run_id=RUN_ACTIVE,
                action="publication",
            )
        await service.restore_course_run(
            organization_id=ORG_A,
            course_run_id=RUN_ACTIVE,
            expected_revision=1,
            actor=METHOD_ACTOR,
            request_id=UUID("00000000-0000-7000-8000-000000000417"),
            trace_id=UUID("00000000-0000-7000-8000-000000000418"),
        )
        await service.archived_state_guard.require_active(
            organization_id=ORG_A,
            course_run_id=RUN_ACTIVE,
            action="publication",
        )

    async with foundation_session_factory() as session:
        intent = await session.get(OutboxMessage, MESSAGE)
        assert intent is not None
        assert (intent.enqueue_state, intent.attempts, intent.completed_at) == (
            "pending",
            0,
            None,
        )
        events = (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == ORG_A,
                    AuditEvent.action.in_(
                        (
                            "archive_course",
                            "restore_course",
                            "archive_course_run",
                            "restore_course_run",
                        )
                    ),
                )
                .order_by(AuditEvent.id)
            )
        ).scalars().all()
        assert [event.action for event in events] == [
            "archive_course",
            "restore_course",
            "archive_course_run",
            "restore_course_run",
        ]


async def test_archived_guard_never_falls_back_to_foreign_tenant_identity(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_world(foundation_session_factory)
    async with foundation_session_factory() as session:
        service = _service(session, foundation_session_factory)
        with pytest.raises(CourseRunNotFound):
            await service.archived_state_guard.require_active(
                organization_id=ORG_B,
                course_run_id=RUN_ACTIVE,
                action="recommendation",
            )
        await session.rollback()
