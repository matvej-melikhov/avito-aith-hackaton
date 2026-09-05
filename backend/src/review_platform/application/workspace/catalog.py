"""Course, preferences and nonexclusive primary reviewer assignment commands."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    require_roles,
    revision,
    row,
)
from review_platform.application.workspace.student_alias import student_labels
from review_platform.contracts.workspace import (
    AssignmentInput,
    AssignmentsView,
    AssignmentView,
    CatalogView,
    CourseInput,
    CourseRunInput,
    CourseRunView,
    CourseView,
    DirectoryMember,
    DirectoryView,
    MembershipInput,
    PreferencesInput,
    PreferencesView,
    ResourceResult,
)
from review_platform.infrastructure.db.models import (
    AvailabilityPlan,
    Course,
    CourseMembership,
    CourseRun,
    OrganizationMembership,
    ReviewerCourseSelection,
    User,
)
from review_platform.infrastructure.db.models.workspace import (
    StudentReviewerAssignment,
    WorkspaceCourseDetails,
    WorkspacePreferences,
    WorkspaceRunSettings,
)


class CatalogService:
    def __init__(self, runtime: FoundationRuntime, session: AsyncSession):
        self.runtime, self.session = runtime, session

    async def directory(self, actor: RequestActor) -> DirectoryView:
        require_roles(actor, "methodologist")
        entries = (
            await self.session.execute(
                select(User, OrganizationMembership)
                .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
                .where(
                    OrganizationMembership.organization_id == actor.organization_id,
                    OrganizationMembership.status == "active",
                )
                .order_by(User.id)
            )
        ).all()
        aliases = await student_labels(
            self.session, actor.organization_id, [u.id for u, _ in entries]
        )
        return DirectoryView(
            items=[
                DirectoryMember(
                    id=u.id,
                    display_name=aliases[u.id] if set(m.roles) == {"student"} else u.display_name,
                    roles=m.roles,
                )
                for u, m in entries
            ]
        )

    async def catalog(self, actor: RequestActor) -> CatalogView:
        query = select(CourseRun).where(CourseRun.organization_id == actor.organization_id)
        if "methodologist" not in actor.roles:
            scope = select(CourseMembership.course_run_id).where(
                CourseMembership.organization_id == actor.organization_id,
                CourseMembership.user_id == actor.user_id,
                CourseMembership.status == "active",
            )
            if "reviewer" not in actor.roles:
                query = query.where(CourseRun.id.in_(scope))
        runs = (
            await self.session.scalars(query.order_by(CourseRun.created_at.desc(), CourseRun.id))
        ).all()
        courses_query = select(Course).where(Course.organization_id == actor.organization_id)
        if not actor.roles.intersection({"reviewer", "methodologist"}):
            courses_query = courses_query.where(Course.id.in_([r.course_id for r in runs]))
        courses = (
            await self.session.scalars(courses_query.order_by(Course.title, Course.id))
        ).all()
        details = (
            await self.session.scalars(
                select(WorkspaceCourseDetails).where(
                    WorkspaceCourseDetails.organization_id == actor.organization_id
                )
            )
        ).all()
        owners = {d.id: d.owner_id for d in details}
        stepik_links = {d.id: d.stepik_url for d in details}
        run_settings = (
            await self.session.scalars(
                select(WorkspaceRunSettings).where(
                    WorkspaceRunSettings.organization_id == actor.organization_id
                )
            )
        ).all()
        priorities = {d.id: d for d in run_settings}
        return CatalogView(
            courses=[
                CourseView(
                    id=c.id,
                    owner_id=owners.get(c.id),
                    stepik_url=stepik_links.get(c.id),
                    title=c.title,
                    description=c.description,
                    revision=c.revision,
                    status=c.status,
                )
                for c in courses
            ],
            course_runs=[
                CourseRunView(
                    id=r.id,
                    priority=cast(Literal["assigned", "deadline"], priorities[r.id].priority)
                    if r.id in priorities
                    else "assigned",
                    priority_revision=priorities[r.id].revision if r.id in priorities else 0,
                    course_id=r.course_id,
                    title=r.title,
                    starts_at=r.starts_at,
                    ends_at=r.ends_at,
                    timezone=r.timezone,
                    status=r.status,
                    revision=r.revision,
                )
                for r in runs
            ],
        )

    async def create_course(self, actor: RequestActor, payload: CourseInput) -> ResourceResult:
        require_roles(actor, "methodologist")
        if payload.owner_id:
            owner = await self.session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == actor.organization_id,
                    OrganizationMembership.user_id == payload.owner_id,
                    OrganizationMembership.status == "active",
                )
            )
            if owner is None or "methodologist" not in owner.roles:
                raise WorkspaceFailure(
                    "invalid_owner", "Ответственный должен быть координатором организации.", 422
                )
        course = Course(
            id=self.runtime.id_factory(),
            organization_id=actor.organization_id,
            title=payload.title,
            description=payload.description,
            source_kind="standalone",
            status="active",
            revision=0,
        )
        self.session.add(course)
        await self.session.flush()
        self.session.add(
            WorkspaceCourseDetails(
                id=course.id,
                organization_id=actor.organization_id,
                owner_id=payload.owner_id,
                stepik_url=payload.stepik_url,
            )
        )
        await self.session.flush()
        return ResourceResult(id=course.id, revision=0)

    async def create_run(
        self, actor: RequestActor, course_id: UUID, expected: int, payload: CourseRunInput
    ) -> ResourceResult:
        require_roles(actor, "methodologist")
        course = await row(self.session, Course, actor.organization_id, course_id, lock=True)
        revision(course.revision, expected)
        if course.status != "active":
            raise WorkspaceFailure("archived", "Этот курс архивирован.")
        run = CourseRun(
            id=self.runtime.id_factory(),
            organization_id=actor.organization_id,
            course_id=course_id,
            title=payload.title,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            timezone=payload.timezone,
            status="active",
            revision=0,
        )
        self.session.add(run)
        course.revision += 1
        await self.session.flush()
        self.session.add(
            WorkspaceRunSettings(
                id=run.id,
                organization_id=actor.organization_id,
                priority=payload.priority,
                revision=0,
            )
        )
        await self.session.flush()
        return ResourceResult(id=run.id, revision=0)

    async def preferences(self, actor: RequestActor) -> PreferencesView:
        require_roles(actor, "reviewer")
        current = await self.session.scalar(
            select(WorkspacePreferences).where(
                WorkspacePreferences.organization_id == actor.organization_id,
                WorkspacePreferences.user_id == actor.user_id,
            )
        )
        if current:
            return PreferencesView(
                revision=current.revision, value=PreferencesInput.model_validate(current.settings)
            )
        selections = (
            await self.session.scalars(
                select(ReviewerCourseSelection.course_run_id).where(
                    ReviewerCourseSelection.organization_id == actor.organization_id,
                    ReviewerCourseSelection.reviewer_id == actor.user_id,
                    ReviewerCourseSelection.active.is_(True),
                )
            )
        ).all()
        plan = await self.session.scalar(
            select(AvailabilityPlan).where(
                AvailabilityPlan.organization_id == actor.organization_id,
                AvailabilityPlan.reviewer_id == actor.user_id,
            )
        )
        value = (
            PreferencesInput(
                course_run_ids=list(selections),
                planned_minutes=plan.planned_minutes,
                until_at=plan.until_at,
            )
            if plan
            else None
        )
        return PreferencesView(revision=0, value=value)

    async def save_preferences(
        self, actor: RequestActor, expected: int, payload: PreferencesInput
    ) -> PreferencesView:
        user_id = require_roles(actor, "reviewer")
        # Membership lock serializes initial preference creation across browser tabs.
        membership = await self.session.scalar(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == actor.organization_id,
                OrganizationMembership.user_id == user_id,
            )
            .with_for_update()
        )
        if membership is None:
            raise WorkspaceFailure("forbidden", "Участник не найден.", 403)
        current = await self.session.scalar(
            select(WorkspacePreferences)
            .where(
                WorkspacePreferences.organization_id == actor.organization_id,
                WorkspacePreferences.user_id == user_id,
            )
            .with_for_update()
        )
        revision(current.revision if current else 0, expected)
        selected = set(payload.course_run_ids)
        for run_id in selected:
            run = await row(self.session, CourseRun, actor.organization_id, run_id)
            if run.status != "active":
                raise WorkspaceFailure("archived", "Выбранный поток не активен.")
            existing = await self.session.scalar(
                select(CourseMembership).where(
                    CourseMembership.organization_id == actor.organization_id,
                    CourseMembership.course_run_id == run_id,
                    CourseMembership.user_id == user_id,
                    CourseMembership.kind == "reviewer",
                )
            )
            if existing is None:
                self.session.add(
                    CourseMembership(
                        id=self.runtime.id_factory(),
                        organization_id=actor.organization_id,
                        course_run_id=run_id,
                        user_id=user_id,
                        kind="reviewer",
                        source="self_selected",
                        status="active",
                        joined_at=self.runtime.clock(),
                    )
                )
        selections = (
            await self.session.scalars(
                select(ReviewerCourseSelection).where(
                    ReviewerCourseSelection.organization_id == actor.organization_id,
                    ReviewerCourseSelection.reviewer_id == user_id,
                )
            )
        ).all()
        for item in selections:
            item.active = item.course_run_id in selected
        for run_id in selected - {item.course_run_id for item in selections}:
            self.session.add(
                ReviewerCourseSelection(
                    id=self.runtime.id_factory(),
                    organization_id=actor.organization_id,
                    course_run_id=run_id,
                    reviewer_id=user_id,
                    active=True,
                )
            )
        plan = await self.session.scalar(
            select(AvailabilityPlan)
            .where(
                AvailabilityPlan.organization_id == actor.organization_id,
                AvailabilityPlan.reviewer_id == user_id,
            )
            .with_for_update()
        )
        if plan is None:
            plan = AvailabilityPlan(
                id=self.runtime.id_factory(),
                organization_id=actor.organization_id,
                reviewer_id=user_id,
                revision=0,
            )
            self.session.add(plan)
        plan.planned_minutes, plan.until_at = payload.planned_minutes, payload.until_at
        plan.revision += 1
        if current is None:
            current = WorkspacePreferences(
                id=self.runtime.id_factory(),
                organization_id=actor.organization_id,
                user_id=user_id,
                revision=0,
            )
            self.session.add(current)
        current.settings = payload.model_dump(mode="json")
        current.revision += 1
        await self.session.flush()
        return PreferencesView(revision=current.revision, value=payload)

    async def assignments(self, actor: RequestActor, run_id: UUID) -> AssignmentsView:
        require_roles(actor, "reviewer", "methodologist")
        await course_scope(self.session, actor, run_id)
        students = (
            (
                await self.session.execute(
                    select(User)
                    .join(CourseMembership, CourseMembership.user_id == User.id)
                    .where(
                        CourseMembership.organization_id == actor.organization_id,
                        CourseMembership.course_run_id == run_id,
                        CourseMembership.kind == "student",
                        CourseMembership.status == "active",
                    )
                    .order_by(User.id)
                )
            )
            .scalars()
            .all()
        )
        assignments = (
            await self.session.scalars(
                select(StudentReviewerAssignment).where(
                    StudentReviewerAssignment.organization_id == actor.organization_id,
                    StudentReviewerAssignment.course_run_id == run_id,
                )
            )
        ).all()
        by_student = {a.student_id: a for a in assignments}
        reviewers = (
            await self.session.scalars(
                select(User).where(
                    User.id.in_([a.reviewer_id for a in assignments if a.reviewer_id])
                )
            )
        ).all()
        names: dict[UUID | None, str] = {u.id: u.display_name for u in reviewers}
        aliases = await student_labels(
            self.session, actor.organization_id, [u.id for u in students]
        )
        return AssignmentsView(
            items=[
                AssignmentView(
                    id=by_student[u.id].id if u.id in by_student else u.id,
                    student_id=u.id,
                    student_name=aliases[u.id],
                    reviewer_id=by_student[u.id].reviewer_id if u.id in by_student else None,
                    reviewer_name=names.get(by_student[u.id].reviewer_id)
                    if u.id in by_student
                    else None,
                    revision=by_student[u.id].revision if u.id in by_student else 0,
                )
                for u in students
            ]
        )

    async def assign(
        self, actor: RequestActor, run_id: UUID, expected: int, payload: AssignmentInput
    ) -> ResourceResult:
        require_roles(actor, "methodologist")
        run = await row(self.session, CourseRun, actor.organization_id, run_id, lock=True)
        await course_scope(self.session, actor, run.id, write=True)
        for user_id, kind in ((payload.student_id, "student"), (payload.reviewer_id, "reviewer")):
            if user_id is None:
                continue
            member = await self.session.scalar(
                select(CourseMembership.id).where(
                    CourseMembership.organization_id == actor.organization_id,
                    CourseMembership.course_run_id == run_id,
                    CourseMembership.user_id == user_id,
                    CourseMembership.kind == kind,
                    CourseMembership.status == "active",
                )
            )
            if member is None:
                raise WorkspaceFailure(
                    "invalid_assignment", "Участник не состоит в этом потоке.", 422
                )
        assignment = await self.session.scalar(
            select(StudentReviewerAssignment)
            .where(
                StudentReviewerAssignment.organization_id == actor.organization_id,
                StudentReviewerAssignment.course_run_id == run_id,
                StudentReviewerAssignment.student_id == payload.student_id,
            )
            .with_for_update()
        )
        revision(assignment.revision if assignment else 0, expected)
        if assignment is None:
            assignment = StudentReviewerAssignment(
                id=self.runtime.id_factory(),
                organization_id=actor.organization_id,
                course_run_id=run_id,
                student_id=payload.student_id,
                revision=0,
            )
            self.session.add(assignment)
        assignment.reviewer_id = payload.reviewer_id
        assignment.revision += 1
        await self.session.flush()
        return ResourceResult(id=assignment.id, revision=assignment.revision)

    async def membership(
        self, actor: RequestActor, run_id: UUID, expected: int, payload: MembershipInput
    ) -> ResourceResult:
        require_roles(actor, "methodologist")
        run = await row(self.session, CourseRun, actor.organization_id, run_id, lock=True)
        revision(run.revision, expected)
        org_member = await self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == actor.organization_id,
                OrganizationMembership.user_id == payload.user_id,
                OrganizationMembership.status == "active",
            )
        )
        if org_member is None or payload.kind not in org_member.roles:
            raise WorkspaceFailure(
                "invalid_member", "Нужна соответствующая роль в организации.", 422
            )
        existing = await self.session.scalar(
            select(CourseMembership).where(
                CourseMembership.organization_id == actor.organization_id,
                CourseMembership.course_run_id == run_id,
                CourseMembership.user_id == payload.user_id,
                CourseMembership.kind == payload.kind,
            )
        )
        if existing is None:
            existing = CourseMembership(
                id=self.runtime.id_factory(),
                organization_id=actor.organization_id,
                course_run_id=run_id,
                user_id=payload.user_id,
                kind=payload.kind,
                source="invitation",
                joined_at=self.runtime.clock(),
            )
            self.session.add(existing)
        existing.status = "active" if payload.active else "removed"
        run.revision += 1
        await self.session.flush()
        return ResourceResult(id=run.id, revision=run.revision)
