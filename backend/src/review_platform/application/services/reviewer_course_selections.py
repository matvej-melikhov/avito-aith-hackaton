"""Exact reviewer CourseRun selection replacement."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor

_REVIEWER = AuthorizationPolicy(required_roles=frozenset({"reviewer"}))


class ReviewerSelectionError(RuntimeError):
    pass


class ReviewerSelectionConflict(ReviewerSelectionError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewerCourseSelectionResult:
    organization_id: UUID
    membership_id: UUID
    reviewer_id: UUID
    membership_revision: int
    selected_course_run_ids: tuple[UUID, ...]


class ReviewerCourseSelectionRepository(Protocol):
    async def lock_active_reviewer_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        reviewer_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> bool: ...

    async def replace_exact_active_runs(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        course_run_ids: tuple[UUID, ...],
        *,
        transaction: object,
    ) -> tuple[UUID, ...]: ...


class ReviewerCourseSelectionService:
    def __init__(
        self,
        *,
        repository: ReviewerCourseSelectionRepository,
        authorizer: Authorizer,
        audit: AuditRecorder,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer
        self._audit = audit

    async def replace(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        membership_id: UUID,
        expected_membership_revision: int,
        course_run_ids: Sequence[UUID],
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> ReviewerCourseSelectionResult:
        grant = await self._authorizer.authorize(
            actor=actor, organization_id=organization_id, policy=_REVIEWER
        )
        if actor.user_id is None:
            raise ReviewerSelectionError("reviewer identity is required")
        selected = tuple(course_run_ids)
        if len(selected) > 1000:
            raise ReviewerSelectionError("at most 1000 CourseRuns may be selected")
        if len(set(selected)) != len(selected):
            raise ReviewerSelectionError("CourseRun selection must be unique")
        if not await self._repository.lock_active_reviewer_membership(
            organization_id,
            membership_id,
            actor.user_id,
            expected_revision=expected_membership_revision,
            transaction=transaction,
        ):
            raise ReviewerSelectionConflict("reviewer membership is missing, inactive, or stale")
        stored = await self._repository.replace_exact_active_runs(
            organization_id,
            actor.user_id,
            tuple(sorted(selected, key=str)),
            transaction=transaction,
        )
        if set(stored) != set(selected) or len(stored) != len(selected):
            raise ReviewerSelectionConflict("repository returned a different exact selection")
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="set_reviewer_course_selection",
                entity_type="organization_membership",
                entity_id=membership_id,
                before_revision=expected_membership_revision,
                after_revision=expected_membership_revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={"course_run_ids": [str(value) for value in stored]},
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return ReviewerCourseSelectionResult(
            organization_id,
            membership_id,
            actor.user_id,
            expected_membership_revision,
            tuple(stored),
        )


__all__ = [
    "ReviewerCourseSelectionRepository",
    "ReviewerCourseSelectionResult",
    "ReviewerCourseSelectionService",
    "ReviewerSelectionConflict",
    "ReviewerSelectionError",
]
