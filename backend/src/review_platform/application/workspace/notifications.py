"""In-app review reminders from durable events, without external delivery."""

from __future__ import annotations

from datetime import timedelta
from time import monotonic
from uuid import UUID, uuid5

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.mysql import insert

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.contracts.workspace import NotificationPreferences, PreferencesInput
from review_platform.infrastructure.db.models import (
    CourseMembership,
    CourseRun,
    CourseRunHomeworkPublication,
    OrganizationMembership,
    ReviewCase,
    ReviewIteration,
    ReviewResponsibility,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import (
    WorkspaceNotification,
    WorkspacePreferences,
)

NOTIFICATION_NAMESPACE = UUID("de376379-8d15-4d34-a1b5-2d5d8ec96f8e")


class NotificationWorker:
    def __init__(self, runtime: FoundationRuntime):
        self.runtime = runtime
        self._last_tick: float | None = None

    async def tick(self) -> bool:
        timer = monotonic()
        if self._last_tick is not None and timer - self._last_tick < 60:
            return False
        # Set before awaiting so one worker instance cannot overlap itself.
        self._last_tick = timer
        now = self.runtime.clock()
        async with self.runtime.transaction() as session:
            members = (
                await session.execute(
                    select(CourseMembership, OrganizationMembership)
                    .join(
                        OrganizationMembership,
                        and_(
                            OrganizationMembership.organization_id
                            == CourseMembership.organization_id,
                            OrganizationMembership.user_id == CourseMembership.user_id,
                        ),
                    )
                    .join(
                        CourseRun,
                        and_(
                            CourseRun.id == CourseMembership.course_run_id,
                            CourseRun.organization_id == CourseMembership.organization_id,
                        ),
                    )
                    .where(
                        CourseMembership.kind == "reviewer",
                        CourseMembership.status == "active",
                        OrganizationMembership.status == "active",
                        CourseRun.status == "active",
                    )
                )
            ).all()
            scopes = {
                (member.organization_id, member.course_run_id, member.user_id)
                for member, organization in members
                if {"reviewer", "methodologist"}.intersection(organization.roles)
            }
            if not scopes:
                return False
            organizations = {org for org, _, _ in scopes}
            run_ids = {run for _, run, _ in scopes}
            preferences = (
                await session.scalars(
                    select(WorkspacePreferences).where(
                        WorkspacePreferences.organization_id.in_(organizations)
                    )
                )
            ).all()
            settings = {
                (item.organization_id, item.user_id): PreferencesInput.model_validate(item.settings)
                for item in preferences
            }
            rows = (
                await session.execute(
                    select(
                        ReviewIteration,
                        SubmissionVersion,
                        Submission.course_run_homework_id,
                        ReviewCase.current_iteration_id,
                    )
                    .join(
                        SubmissionVersion,
                        and_(
                            SubmissionVersion.id == ReviewIteration.submission_version_id,
                            SubmissionVersion.organization_id == ReviewIteration.organization_id,
                        ),
                    )
                    .join(
                        Submission,
                        and_(
                            Submission.id == SubmissionVersion.submission_id,
                            Submission.organization_id == SubmissionVersion.organization_id,
                        ),
                    )
                    .join(
                        ReviewCase,
                        and_(
                            ReviewCase.id == ReviewIteration.review_case_id,
                            ReviewCase.organization_id == ReviewIteration.organization_id,
                        ),
                    )
                    .where(
                        ReviewIteration.organization_id.in_(organizations),
                        ReviewIteration.course_run_id.in_(run_ids),
                    )
                )
            ).all()
            iterations = {item.id: item for item, _, _, _ in rows}
            events = (
                await session.scalars(
                    select(ReviewResponsibility)
                    .where(
                        ReviewResponsibility.organization_id.in_(organizations),
                        ReviewResponsibility.review_iteration_id.in_(iterations),
                    )
                    .order_by(ReviewResponsibility.occurred_at, ReviewResponsibility.id)
                )
            ).all()
            latest: dict[tuple[UUID, UUID], str] = {}
            previous: dict[UUID, set[UUID]] = {}
            for event in events:
                if event.review_iteration_id is None:
                    continue
                latest[event.review_iteration_id, event.reviewer_id] = event.action
                if event.action in {"started", "joined"}:
                    previous.setdefault(event.review_iteration_id, set()).add(event.reviewer_id)
            histories = (
                await session.scalars(
                    select(CourseRunHomeworkPublication)
                    .where(
                        CourseRunHomeworkPublication.organization_id.in_(organizations),
                        CourseRunHomeworkPublication.course_run_homework_id.in_(
                            {p for _, _, p, _ in rows}
                        ),
                    )
                    .order_by(CourseRunHomeworkPublication.publication_sequence.desc())
                )
            ).all()
            sequence_rows = (
                await session.execute(
                    select(SubmissionVersion.submission_id, func.max(SubmissionVersion.sequence))
                    .where(
                        SubmissionVersion.organization_id.in_(organizations),
                        SubmissionVersion.course_run_id.in_(run_ids),
                    )
                    .group_by(SubmissionVersion.submission_id)
                )
            ).all()
            latest_sequences = {
                submission_id: sequence for submission_id, sequence in sequence_rows
            }
            notices: dict[UUID, tuple[UUID, UUID, UUID, str]] = {}

            def emit(
                org: UUID, run: UUID, recipient: UUID, kind: str, source: UUID, text: str
            ) -> None:
                if (org, run, recipient) in scopes:
                    key = uuid5(NOTIFICATION_NAMESPACE, f"{org}:{recipient}:{kind}:{source}")
                    notices[key] = (org, run, recipient, text)

            for iteration, version, publication_id, current_id in rows:
                if iteration.id != current_id or iteration.status not in {
                    "in_review",
                    "ready_to_publish",
                }:
                    continue
                # A submitted correction supersedes this iteration's impending deadline.
                if version.sequence != latest_sequences.get(version.submission_id):
                    continue
                deadline = next(
                    (
                        history.review_deadline
                        for history in histories
                        if history.organization_id == iteration.organization_id
                        and history.course_run_homework_id == publication_id
                        and history.published_at <= version.submitted_at
                    ),
                    None,
                )
                if deadline is None or not now <= deadline <= now + timedelta(hours=24):
                    continue
                for (iteration_id, reviewer), action in latest.items():
                    if iteration_id != iteration.id or action not in {"started", "joined"}:
                        continue
                    preference = settings.get((iteration.organization_id, reviewer))
                    toggles = preference.notifications if preference else NotificationPreferences()
                    if toggles.deadline:
                        emit(
                            iteration.organization_id,
                            iteration.course_run_id,
                            reviewer,
                            "deadline",
                            iteration.id,
                            "Работа, которую вы взяли, близка к сроку проверки (менее 24 часов).",
                        )
            arrivals = (
                await session.scalars(
                    select(SubmissionVersion)
                    .where(
                        SubmissionVersion.organization_id.in_(organizations),
                        SubmissionVersion.course_run_id.in_(run_ids),
                        SubmissionVersion.status == "ready",
                        SubmissionVersion.submitted_at >= now - timedelta(hours=24),
                        SubmissionVersion.submitted_at <= now,
                    )
                    .order_by(SubmissionVersion.sequence.desc())
                )
            ).all()
            newest: dict[UUID, SubmissionVersion] = {}
            for version in arrivals:
                newest.setdefault(version.submission_id, version)
            for version in newest.values():
                if version.sequence != latest_sequences.get(version.submission_id):
                    continue
                related = [
                    (iteration, old)
                    for iteration, old, _, _ in rows
                    if old.submission_id == version.submission_id
                ]
                if version.sequence > 1:
                    for iteration, old in related:
                        if old.sequence >= version.sequence:
                            continue
                        for reviewer in previous.get(iteration.id, set()):
                            preference = settings.get((version.organization_id, reviewer))
                            toggles = (
                                preference.notifications
                                if preference
                                else NotificationPreferences()
                            )
                            if toggles.revision:
                                emit(
                                    version.organization_id,
                                    version.course_run_id,
                                    reviewer,
                                    "revision",
                                    version.id,
                                    "Студент прислал новую версию работы, которую вы проверяли.",
                                )
                if any(
                    old.id == version.id and iteration.status == "published"
                    for iteration, old in related
                ):
                    continue
                for org, run, reviewer in scopes:
                    if org != version.organization_id or run != version.course_run_id:
                        continue
                    preference = settings.get((org, reviewer))
                    if (
                        preference is None
                        or not preference.notifications.pool
                        or not preference.show_pool
                        or run not in preference.course_run_ids
                        or (
                            preference.absent_from
                            and preference.absent_until
                            and preference.absent_from <= now < preference.absent_until
                        )
                    ):
                        continue
                    if any(
                        old.id == version.id
                        and latest.get((iteration.id, reviewer)) in {"started", "joined"}
                        for iteration, old in related
                    ):
                        continue
                    emit(
                        org,
                        run,
                        reviewer,
                        "pool",
                        version.id,
                        "Новая работа появилась в пуле выбранного потока.",
                    )
            for identity, (org, run, recipient, message) in sorted(notices.items()):
                statement = insert(WorkspaceNotification).values(
                    id=identity,
                    organization_id=org,
                    course_run_id=run,
                    recipient_id=recipient,
                    text=message,
                    created_at=now,
                    updated_at=now,
                    read_at=None,
                )
                await session.execute(statement.on_duplicate_key_update(id=identity))
        return bool(notices)
