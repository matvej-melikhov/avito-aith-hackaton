"""Measured coordinator insights and staff-only chronology over immutable review records."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Exists

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import course_scope, require_roles
from review_platform.contracts.workspace import (
    ReviewAssistResult,
    ReviewDecisionEvent,
    TypicalCriterionFailure,
    WorkspaceInsightsView,
    WorkspacePoolMetrics,
    WorkspaceStatusCounts,
)
from review_platform.infrastructure.db.models import (
    AuditEvent,
    CourseMembership,
    Criterion,
    OrganizationMembership,
    ReviewCriterionDecision,
    ReviewIteration,
    ReviewPublication,
    ReviewResponsibility,
    ReviewRevision,
    User,
)
from review_platform.infrastructure.db.models.workspace import ReviewAIChoice, ReviewAssistRun


def has_active_participant(
    organization_id: UUID,
    iteration_id: ColumnElement[UUID] | InstrumentedAttribute[UUID],
) -> Exists:
    latest = (
        select(
            ReviewResponsibility.review_iteration_id,
            ReviewResponsibility.action,
            func.row_number()
            .over(
                partition_by=(
                    ReviewResponsibility.review_iteration_id,
                    ReviewResponsibility.reviewer_id,
                ),
                order_by=(ReviewResponsibility.occurred_at.desc(), ReviewResponsibility.id.desc()),
            )
            .label("rank"),
        )
        .where(ReviewResponsibility.organization_id == organization_id)
        .subquery()
    )
    return (
        select(latest.c.action)
        .where(
            latest.c.review_iteration_id == iteration_id,
            latest.c.rank == 1,
            latest.c.action.in_(["started", "joined"]),
        )
        .exists()
    )


def review_deadline(first_participation: datetime, timezone: str) -> datetime:
    """Two Monday-Friday days at the same local time; no unconfigured holiday calendar."""
    local = first_participation.astimezone(ZoneInfo(timezone))
    remaining = 2
    while remaining:
        local += timedelta(days=1)
        if local.weekday() < 5:
            remaining -= 1
    return local.astimezone(UTC)


async def decision_history(
    session: AsyncSession,
    organization_id: UUID,
    iteration: ReviewIteration,
) -> list[ReviewDecisionEvent]:
    # Include corrections and added requirements for this exact submitted artifact only.
    identities = (
        await session.scalars(
            select(ReviewIteration.id).where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.review_case_id == iteration.review_case_id,
                ReviewIteration.artifact_version_id == iteration.artifact_version_id,
                ReviewIteration.iteration_number <= iteration.iteration_number,
            )
        )
    ).all()
    revisions = (
        await session.scalars(
            select(ReviewRevision)
            .where(
                ReviewRevision.organization_id == organization_id,
                ReviewRevision.review_iteration_id.in_(identities),
            )
            .order_by(ReviewRevision.created_at, ReviewRevision.revision_number, ReviewRevision.id)
        )
    ).all()
    revision_ids = [revision.id for revision in revisions]
    decisions = (
        await session.execute(
            select(ReviewCriterionDecision, Criterion)
            .join(
                Criterion,
                and_(
                    Criterion.id == ReviewCriterionDecision.criterion_id,
                    Criterion.organization_id == ReviewCriterionDecision.organization_id,
                ),
            )
            .where(
                ReviewCriterionDecision.organization_id == organization_id,
                ReviewCriterionDecision.review_revision_id.in_(revision_ids),
            )
        )
    ).all()
    runs = (
        await session.scalars(
            select(ReviewAssistRun).where(
                ReviewAssistRun.organization_id == organization_id,
                ReviewAssistRun.iteration_id.in_(identities),
                ReviewAssistRun.status == "succeeded",
            )
        )
    ).all()
    choices = (
        await session.scalars(
            select(ReviewAIChoice).where(
                ReviewAIChoice.organization_id == organization_id,
                ReviewAIChoice.id.in_(revision_ids),
            )
        )
    ).all()
    publications = (
        await session.scalars(
            select(ReviewPublication).where(
                ReviewPublication.organization_id == organization_id,
                ReviewPublication.review_iteration_id.in_(identities),
            )
        )
    ).all()
    additions = (
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.entity_id.in_(identities),
                AuditEvent.action == "add_review_requirement",
                AuditEvent.outcome == "succeeded",
            )
        )
    ).all()
    users = {revision.author_user_id for revision in revisions}
    users.update(publication.published_by for publication in publications)
    users.update(event.actor_user_id for event in additions if event.actor_user_id)
    names = {
        person.id: person.display_name
        for person in (await session.scalars(select(User).where(User.id.in_(users)))).all()
    }
    by_revision: dict[UUID, dict[UUID, tuple[str, float]]] = defaultdict(dict)
    for decision, criterion in decisions:
        by_revision[decision.review_revision_id][criterion.id] = (
            criterion.title,
            float(decision.points),
        )
    proposals = {}
    history = []
    for run in runs:
        if run.result is None:
            continue
        result = ReviewAssistResult.model_validate(run.result)
        proposals[run.id] = {
            suggestion.criterion_id: float(suggestion.proposed_points)
            for suggestion in result.suggestions
            if suggestion.proposed_points is not None
        }
        history.append(
            ReviewDecisionEvent(
                timestamp=run.updated_at,
                text=f"Модель подготовила разбор по {len(result.suggestions)} требованиям.",
                actor=None,
            )
        )
    by_choice = {choice.id: choice.run_id for choice in choices}
    by_identity = {revision.id: revision for revision in revisions}
    for revision in revisions:
        previous = (
            by_revision.get(revision.base_revision_id, {}) if revision.base_revision_id else {}
        )
        baseline = {identity: points for identity, (_, points) in previous.items()}
        if not baseline and revision.id in by_choice:
            baseline = proposals.get(by_choice[revision.id], {})
        changes = []
        for criterion_id, (title, points) in by_revision[revision.id].items():
            before = baseline.get(criterion_id)
            if before is not None and before != points:
                changes.append(f"Изменена оценка «{title}»: {before:g} → {points:g}.")
        old = by_identity.get(revision.base_revision_id) if revision.base_revision_id else None
        if old and old.feedback != revision.feedback:
            changes.append("Обновлён ответ студенту.")
        if not changes:
            changes.append("Сохранён черновик проверки.")
        for text in changes:
            history.append(
                ReviewDecisionEvent(
                    timestamp=revision.created_at,
                    text=text,
                    actor=names.get(revision.author_user_id),
                )
            )
    for event in additions:
        history.append(
            ReviewDecisionEvent(
                timestamp=event.occurred_at,
                text="Добавлено требование к этой проверке.",
                actor=names.get(event.actor_user_id) if event.actor_user_id else None,
            )
        )
    for publication in publications:
        history.append(
            ReviewDecisionEvent(
                timestamp=publication.published_at,
                text="Результат проверки опубликован студенту.",
                actor=names.get(publication.published_by),
            )
        )
    return sorted(history, key=lambda event: event.timestamp)


async def workspace_insights(
    runtime: FoundationRuntime,
    session: AsyncSession,
    actor: RequestActor,
    run_id: UUID,
    homework_id: UUID | None = None,
) -> WorkspaceInsightsView:
    from review_platform.application.workspace.projections import WorkspaceQueries

    require_roles(actor, "methodologist")
    await course_scope(session, actor, run_id)
    items = []
    offset = 0
    # Scoped pages have no fixed truncation: every row contributes to the measured denominators.
    while True:
        page = await WorkspaceQueries(runtime, session).works(
            actor, run_id=run_id, homework_id=homework_id, offset=offset, limit=100
        )
        items.extend(page.items)
        offset += len(page.items)
        if offset >= page.total or not page.items:
            break
    counts = WorkspaceStatusCounts(all=len(items))
    for item in items:
        status = item.status
        if status in {"in_review", "ready_to_publish"}:
            status = "repeat_review" if item.attempt > 1 else "in_review"
        if status in type(counts).model_fields and status != "all":
            setattr(counts, status, getattr(counts, status) + 1)
    waiting = [
        item
        for item in items
        if item.status in {"pending_review", "in_review", "ready_to_publish"}
        and not item.participant_ids
    ]
    active = {
        identity
        for item in items
        if item.status in {"in_review", "ready_to_publish"}
        for identity in item.participant_ids
    }
    members = (
        await session.scalars(
            select(OrganizationMembership)
            .join(
                CourseMembership,
                and_(
                    CourseMembership.organization_id == OrganizationMembership.organization_id,
                    CourseMembership.user_id == OrganizationMembership.user_id,
                ),
            )
            .where(
                CourseMembership.organization_id == actor.organization_id,
                CourseMembership.course_run_id == run_id,
                CourseMembership.kind == "reviewer",
                CourseMembership.status == "active",
                OrganizationMembership.status == "active",
            )
        )
    ).all()
    reviewers = {
        member.user_id
        for member in members
        if {"reviewer", "methodologist"}.intersection(member.roles)
    }
    waits = [
        (item.taken_at - item.submitted_at).total_seconds() / 60
        for item in items
        if item.taken_at and item.submitted_at and item.taken_at >= item.submitted_at
    ]
    latest = (
        select(
            ReviewPublication.review_revision_id,
            func.row_number()
            .over(
                partition_by=ReviewIteration.review_case_id,
                order_by=ReviewIteration.iteration_number.desc(),
            )
            .label("rank"),
        )
        .join(
            ReviewIteration,
            and_(
                ReviewIteration.id == ReviewPublication.review_iteration_id,
                ReviewIteration.organization_id == ReviewPublication.organization_id,
            ),
        )
        .where(
            ReviewPublication.organization_id == actor.organization_id,
            ReviewIteration.course_run_id == run_id,
        )
    )
    if homework_id:
        latest = latest.where(ReviewIteration.homework_id == homework_id)
    ranked = latest.subquery()
    rows = (
        await session.execute(
            select(
                Criterion.id, Criterion.title, ReviewCriterionDecision.points, Criterion.max_points
            )
            .join(
                ReviewCriterionDecision,
                and_(
                    ReviewCriterionDecision.criterion_id == Criterion.id,
                    ReviewCriterionDecision.organization_id == Criterion.organization_id,
                ),
            )
            .where(
                Criterion.organization_id == actor.organization_id,
                ReviewCriterionDecision.review_revision_id.in_(
                    select(ranked.c.review_revision_id).where(ranked.c.rank == 1)
                ),
            )
        )
    ).all()
    failures: dict[UUID, tuple[str, int, int]] = {}
    for identity, title, points, maximum in rows:
        if maximum <= 0:
            continue
        _, failed, reviewed = failures.get(identity, (title, 0, 0))
        failures[identity] = (title, failed + int(points < maximum), reviewed + 1)
    return WorkspaceInsightsView(
        status_counts=counts,
        pool_metrics=WorkspacePoolMetrics(
            waiting=len(waiting),
            submitted=sum(item.submission_id is not None for item in items),
            stuck=sum(
                item.submitted_at is not None
                and item.submitted_at < runtime.clock() - timedelta(days=3)
                for item in waiting
            ),
            active_reviewers=len(active & reviewers),
            total_reviewers=len(reviewers),
            average_wait_minutes=sum(waits) / len(waits) if waits else None,
        ),
        typical_failures=[
            TypicalCriterionFailure(
                criterion_id=identity,
                title=title,
                failed=failed,
                reviewed=reviewed,
                ratio=failed / reviewed,
            )
            for identity, (title, failed, reviewed) in sorted(
                failures.items(), key=lambda entry: -entry[1][1] / entry[1][2]
            )
            if failed
        ],
    )
