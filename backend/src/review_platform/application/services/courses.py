"""Tenant-scoped course reads, lifecycle changes, and archived-state guard."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import (
    AuthorizationGrant,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models.learning import Course, CourseMembership, CourseRun
from review_platform.infrastructure.db.repositories.learning import (
    ArchiveStatus,
    CourseMembershipKind,
    CourseMembershipRepository,
    CourseMembershipStatus,
    CourseRepository,
    CourseRunRepository,
    CourseRunStatus,
)

type GuardedCourseAction = Literal["recommendation", "open_review", "publication"]

_ALL_MEMBERSHIP_STATUSES: frozenset[CourseMembershipStatus] = frozenset(
    {"active", "removed", "archived"}
)
_ACTIVE_MEMBERSHIP_STATUS: frozenset[CourseMembershipStatus] = frozenset({"active"})
_ALL_MEMBERSHIP_KINDS: frozenset[CourseMembershipKind] = frozenset(
    {"student", "reviewer"}
)
_VISIBLE_COURSE_POLICY = AuthorizationPolicy(
    required_roles=frozenset({"methodologist", "reviewer", "student"})
)
_METHODOLOGIST_POLICY = AuthorizationPolicy(required_roles=frozenset({"methodologist"}))


class CourseServiceError(RuntimeError):
    """Base application error for course lifecycle and visibility."""


class CourseNotFound(CourseServiceError):
    """The course is absent in the actor's organization."""


class CourseRunNotFound(CourseServiceError):
    """The CourseRun is absent in the actor's organization."""


class CourseRevisionConflict(CourseServiceError):
    """The exact expected revision is no longer current."""


class CourseStateConflict(CourseServiceError):
    """The requested archive/restore transition is already applied or invalid."""


class ArchivedCourseActionDenied(CourseServiceError):
    """A new action cannot begin under an archived Course or CourseRun."""


@dataclass(frozen=True, slots=True)
class CourseLifecycleResult:
    entity_id: UUID
    revision: int
    status: str


class CourseArchivedStateGuard:
    """Lock parent and child lifecycle state before creating new work.

    This guard intentionally has no cancellation method.  Publication and
    delivery intents that were durable before archival continue through their
    own recovery state machines; only creation of new work is rejected.
    """

    def __init__(
        self,
        *,
        courses: CourseRepository,
        course_runs: CourseRunRepository,
    ) -> None:
        self._courses = courses
        self._course_runs = course_runs

    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        action: GuardedCourseAction,
    ) -> tuple[Course, CourseRun]:
        if action not in {"recommendation", "open_review", "publication"}:
            raise ValueError(f"unknown guarded course action: {action!r}")

        # Resolve the parent ID tenant-locally, then lock parent before child.
        # Re-reading the child under lock closes an archive race between the
        # initial lookup and the final decision.
        discovered_run = await self._course_runs.get(organization_id, course_run_id)
        if discovered_run is None:
            raise CourseRunNotFound("tenant-scoped CourseRun not found")
        course = await self._courses.get(
            organization_id,
            discovered_run.course_id,
            for_update=True,
        )
        if course is None:
            raise CourseNotFound("tenant-scoped parent Course not found")
        course_run = await self._course_runs.get(
            organization_id,
            course_run_id,
            for_update=True,
        )
        if course_run is None or course_run.course_id != course.id:
            raise CourseRunNotFound("tenant-scoped CourseRun changed during guard evaluation")
        if course.status == "archived" or course_run.status == "archived":
            raise ArchivedCourseActionDenied(
                f"cannot start {action}: Course or CourseRun is archived"
            )
        return course, course_run


class CourseService:
    """Application service over caller-owned SQL repositories/session."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        authorizer: Authorizer,
        audit: AuditRecorder,
    ) -> None:
        self._session = session
        self._authorizer = authorizer
        self._audit = audit
        self._courses = CourseRepository(session)
        self._course_runs = CourseRunRepository(session)
        self._memberships = CourseMembershipRepository(session)
        self.archived_state_guard = CourseArchivedStateGuard(
            courses=self._courses,
            course_runs=self._course_runs,
        )

    async def list_courses(
        self,
        *,
        organization_id: UUID,
        actor: RequestActor,
        statuses: Collection[ArchiveStatus],
    ) -> Sequence[Course]:
        await self._authorize(actor=actor, organization_id=organization_id)
        candidates = await self._courses.list_for_organization(
            organization_id,
            statuses=statuses,
        )
        if "methodologist" in actor.roles:
            return candidates
        visible_course_ids = await self._visible_course_ids(
            organization_id=organization_id,
            actor=actor,
            include_history="archived" in statuses,
        )
        return tuple(course for course in candidates if course.id in visible_course_ids)

    async def list_course_runs(
        self,
        *,
        organization_id: UUID,
        actor: RequestActor,
        course_id: UUID,
        statuses: Collection[CourseRunStatus],
    ) -> Sequence[CourseRun]:
        await self._authorize(actor=actor, organization_id=organization_id)
        course = await self._courses.get(organization_id, course_id)
        if course is None:
            raise CourseNotFound("tenant-scoped Course not found")
        candidates = await self._course_runs.list_for_course(
            organization_id,
            course_id,
            statuses=statuses,
        )
        if "methodologist" in actor.roles:
            return candidates
        visible_run_ids = await self._visible_run_ids(
            organization_id=organization_id,
            actor=actor,
            include_history="archived" in statuses,
        )
        return tuple(course_run for course_run in candidates if course_run.id in visible_run_ids)

    async def read_roster(
        self,
        *,
        organization_id: UUID,
        actor: RequestActor,
        course_run_id: UUID,
        include_history: bool,
    ) -> Sequence[CourseMembership]:
        await self._authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_METHODOLOGIST_POLICY,
        )
        if await self._course_runs.get(organization_id, course_run_id) is None:
            raise CourseRunNotFound("tenant-scoped CourseRun not found")
        statuses = (
            _ALL_MEMBERSHIP_STATUSES if include_history else _ACTIVE_MEMBERSHIP_STATUS
        )
        return await self._memberships.list_for_course_run(
            organization_id,
            course_run_id,
            statuses=statuses,
            kinds=_ALL_MEMBERSHIP_KINDS,
        )

    async def archive_course(
        self,
        *,
        organization_id: UUID,
        course_id: UUID,
        expected_revision: int,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
        reason: str,
    ) -> CourseLifecycleResult:
        if not reason:
            raise ValueError("course archive reason is required")
        return await self._change_course_status(
            organization_id=organization_id,
            course_id=course_id,
            expected_revision=expected_revision,
            actor=actor,
            request_id=request_id,
            trace_id=trace_id,
            new_status="archived",
            action="archive_course",
            reason=reason,
        )

    async def restore_course(
        self,
        *,
        organization_id: UUID,
        course_id: UUID,
        expected_revision: int,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> CourseLifecycleResult:
        return await self._change_course_status(
            organization_id=organization_id,
            course_id=course_id,
            expected_revision=expected_revision,
            actor=actor,
            request_id=request_id,
            trace_id=trace_id,
            new_status="active",
            action="restore_course",
            reason=None,
        )

    async def archive_course_run(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        expected_revision: int,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
        reason: str,
    ) -> CourseLifecycleResult:
        if not reason:
            raise ValueError("CourseRun archive reason is required")
        return await self._change_course_run_status(
            organization_id=organization_id,
            course_run_id=course_run_id,
            expected_revision=expected_revision,
            actor=actor,
            request_id=request_id,
            trace_id=trace_id,
            new_status="archived",
            action="archive_course_run",
            reason=reason,
        )

    async def restore_course_run(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        expected_revision: int,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> CourseLifecycleResult:
        return await self._change_course_run_status(
            organization_id=organization_id,
            course_run_id=course_run_id,
            expected_revision=expected_revision,
            actor=actor,
            request_id=request_id,
            trace_id=trace_id,
            new_status="active",
            action="restore_course_run",
            reason=None,
        )

    async def _change_course_status(
        self,
        *,
        organization_id: UUID,
        course_id: UUID,
        expected_revision: int,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
        new_status: ArchiveStatus,
        action: str,
        reason: str | None,
    ) -> CourseLifecycleResult:
        grant = await self._authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_METHODOLOGIST_POLICY,
        )
        course = await self._courses.get(organization_id, course_id, for_update=True)
        if course is None:
            raise CourseNotFound("tenant-scoped Course not found")
        self._validate_transition(
            current_status=course.status,
            current_revision=course.revision,
            expected_revision=expected_revision,
            new_status=new_status,
        )
        before_status = course.status
        updated = await self._courses.compare_and_set_status(
            organization_id,
            course_id,
            expected_revision=expected_revision,
            new_status=new_status,
        )
        if not updated:
            raise CourseRevisionConflict("Course revision changed during compare-and-set")
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action=action,
                entity_type="course",
                entity_id=course_id,
                before_revision=expected_revision,
                after_revision=expected_revision + 1,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "from_status": before_status,
                    "to_status": new_status,
                    "reason": reason,
                },
            ),
            transaction=self._session,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=self._session)
        return CourseLifecycleResult(course_id, expected_revision + 1, new_status)

    async def _change_course_run_status(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        expected_revision: int,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
        new_status: CourseRunStatus,
        action: str,
        reason: str | None,
    ) -> CourseLifecycleResult:
        grant = await self._authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_METHODOLOGIST_POLICY,
        )
        course_run = await self._course_runs.get(
            organization_id,
            course_run_id,
            for_update=True,
        )
        if course_run is None:
            raise CourseRunNotFound("tenant-scoped CourseRun not found")
        self._validate_transition(
            current_status=course_run.status,
            current_revision=course_run.revision,
            expected_revision=expected_revision,
            new_status=new_status,
        )
        before_status = course_run.status
        updated = await self._course_runs.compare_and_set_status(
            organization_id,
            course_run_id,
            expected_revision=expected_revision,
            new_status=new_status,
        )
        if not updated:
            raise CourseRevisionConflict("CourseRun revision changed during compare-and-set")
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action=action,
                entity_type="course_run",
                entity_id=course_run_id,
                before_revision=expected_revision,
                after_revision=expected_revision + 1,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "from_status": before_status,
                    "to_status": new_status,
                    "reason": reason,
                },
            ),
            transaction=self._session,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=self._session)
        return CourseLifecycleResult(course_run_id, expected_revision + 1, new_status)

    async def _authorize(
        self,
        *,
        actor: RequestActor,
        organization_id: UUID,
        policy: AuthorizationPolicy = _VISIBLE_COURSE_POLICY,
    ) -> AuthorizationGrant:
        return await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=policy,
        )

    async def _visible_course_ids(
        self,
        *,
        organization_id: UUID,
        actor: RequestActor,
        include_history: bool,
    ) -> frozenset[UUID]:
        run_ids = await self._visible_run_ids(
            organization_id=organization_id,
            actor=actor,
            include_history=include_history,
        )
        course_ids: set[UUID] = set()
        for run_id in run_ids:
            course_run = await self._course_runs.get(organization_id, run_id)
            if course_run is not None:
                course_ids.add(course_run.course_id)
        return frozenset(course_ids)

    async def _visible_run_ids(
        self,
        *,
        organization_id: UUID,
        actor: RequestActor,
        include_history: bool,
    ) -> frozenset[UUID]:
        if actor.user_id is None:
            return frozenset()
        kinds: set[CourseMembershipKind] = set()
        if "reviewer" in actor.roles:
            kinds.add("reviewer")
        if "student" in actor.roles:
            kinds.add("student")
        if not kinds:
            return frozenset()
        statuses = _ALL_MEMBERSHIP_STATUSES if include_history else _ACTIVE_MEMBERSHIP_STATUS
        memberships = await self._memberships.list_for_user(
            organization_id,
            actor.user_id,
            statuses=statuses,
            kinds=kinds,
        )
        return frozenset(membership.course_run_id for membership in memberships)

    @staticmethod
    def _validate_transition(
        *,
        current_status: str,
        current_revision: int,
        expected_revision: int,
        new_status: str,
    ) -> None:
        if isinstance(expected_revision, bool) or expected_revision < 0:
            raise CourseRevisionConflict("expected revision must be nonnegative")
        if current_revision != expected_revision:
            raise CourseRevisionConflict(
                f"expected revision {expected_revision}, current revision is {current_revision}"
            )
        if current_status == new_status:
            raise CourseStateConflict(f"entity is already {new_status}")
        expected_current = "active" if new_status == "archived" else "archived"
        if current_status != expected_current:
            raise CourseStateConflict(
                f"cannot transition lifecycle state from {current_status} to {new_status}"
            )


# Compatibility-friendly name for downstream story services.
ArchivedStateGuard = CourseArchivedStateGuard


__all__ = [
    "ArchivedCourseActionDenied",
    "ArchivedStateGuard",
    "CourseArchivedStateGuard",
    "CourseLifecycleResult",
    "CourseNotFound",
    "CourseRevisionConflict",
    "CourseRunNotFound",
    "CourseService",
    "CourseServiceError",
    "CourseStateConflict",
    "GuardedCourseAction",
]
