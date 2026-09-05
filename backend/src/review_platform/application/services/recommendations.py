"""Tenant-scoped recommendation and exact recommend-to-open continuity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc
from review_platform.domain.recommendation import (
    ReviewerRecommendationContext,
    rank_recommendations,
)

type CourseState = Literal["active", "archived"]
type CourseRunState = Literal["draft", "active", "archived"]

_RECOMMEND = AuthorizationPolicy(required_roles=frozenset({"reviewer"}))


class RecommendationServiceError(RuntimeError):
    """Base typed recommendation boundary failure."""


class RecommendationScopeViolation(RecommendationServiceError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewQueueCandidate:
    organization_id: UUID
    course_run_id: UUID
    candidate_id: UUID
    review_case_id: UUID
    review_case_revision: int
    submission_version_id: UUID
    review_deadline: datetime
    continuing_reviewer_id: UUID | None
    submitted_at: datetime
    estimated_review_minutes: int
    active_reviewer_count: int

    def __post_init__(self) -> None:
        if self.candidate_id != self.review_case_id:
            raise ValueError("recommendation candidate identity must equal ReviewCase identity")
        if self.review_case_revision < 0:
            raise ValueError("ReviewCase revision must be nonnegative")


@dataclass(frozen=True, slots=True)
class RecommendationQueueSnapshot:
    organization_id: UUID
    reviewer_id: UUID
    course_run_id: UUID
    course_status: CourseState
    course_run_status: CourseRunState
    selected: bool
    planned_minutes: int
    assigned_minutes: int
    candidates: tuple[ReviewQueueCandidate, ...]


@dataclass(frozen=True, slots=True)
class RecommendedOpenTarget:
    """The exact identifiers consumed by the existing T085 open command."""

    review_case_id: UUID
    review_case_revision: int
    submission_version_id: UUID


@dataclass(frozen=True, slots=True)
class ReviewRecommendation:
    """Tenant-bound result with the frozen route fields and exact open target."""

    organization_id: UUID
    reviewer_id: UUID
    course_run_id: UUID
    review_case_id: UUID
    review_case_revision: int
    submission_version_id: UUID
    reason: tuple[str, ...]


    @property
    def open_target(self) -> RecommendedOpenTarget:
        return RecommendedOpenTarget(
            review_case_id=self.review_case_id,
            review_case_revision=self.review_case_revision,
            submission_version_id=self.submission_version_id,
        )


class RecommendationRepository(Protocol):
    """T128 port. ``load_queue`` must use non-locking tenant predicates."""

    async def load_queue(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        course_run_id: UUID,
        *,
        now: datetime,
        transaction: object,
    ) -> RecommendationQueueSnapshot | None: ...

class RecommendationService:
    def __init__(
        self,
        *,
        repository: RecommendationRepository,
        authorizer: Authorizer,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer

    async def recommend_next(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        actor: RequestActor,
        now: datetime,
        transaction: object,
    ) -> ReviewRecommendation | None:
        """Recommend without claiming, locking, or creating responsibility state."""

        await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_RECOMMEND,
        )
        if actor.user_id is None:
            raise RecommendationScopeViolation("reviewer identity is required")
        selected_at = require_utc(now)
        snapshot = await self._repository.load_queue(
            organization_id,
            actor.user_id,
            course_run_id,
            now=selected_at,
            transaction=transaction,
        )
        if snapshot is None:
            return None
        self._validate_queue_scope(
            snapshot,
            organization_id=organization_id,
            reviewer_id=actor.user_id,
            course_run_id=course_run_id,
        )
        if (
            not snapshot.selected
            or snapshot.course_status != "active"
            or snapshot.course_run_status != "active"
        ):
            return None
        ranked = rank_recommendations(
            snapshot.candidates,
            context=ReviewerRecommendationContext(
                reviewer_id=actor.user_id,
                planned_minutes=snapshot.planned_minutes,
                assigned_minutes=snapshot.assigned_minutes,
            ),
        )
        if not ranked:
            return None
        candidate = ranked[0]
        return ReviewRecommendation(
            organization_id=organization_id,
            reviewer_id=actor.user_id,
            course_run_id=course_run_id,
            review_case_id=candidate.review_case_id,
            review_case_revision=candidate.review_case_revision,
            submission_version_id=candidate.submission_version_id,
            reason=self._reason(candidate, snapshot),
        )

    @staticmethod
    def _validate_queue_scope(
        snapshot: RecommendationQueueSnapshot,
        *,
        organization_id: UUID,
        reviewer_id: UUID,
        course_run_id: UUID,
    ) -> None:
        if (
            snapshot.organization_id != organization_id
            or snapshot.reviewer_id != reviewer_id
            or snapshot.course_run_id != course_run_id
        ):
            raise RecommendationScopeViolation(
                "recommendation repository returned another tenant, reviewer, or CourseRun"
            )
        if (
            not isinstance(snapshot.selected, bool)
            or not isinstance(snapshot.planned_minutes, int)
            or isinstance(snapshot.planned_minutes, bool)
            or snapshot.planned_minutes < 0
            or not isinstance(snapshot.assigned_minutes, int)
            or isinstance(snapshot.assigned_minutes, bool)
            or snapshot.assigned_minutes < 0
        ):
            raise RecommendationScopeViolation("recommendation advisory minutes are invalid")
        if snapshot.course_status not in {"active", "archived"} or (
            snapshot.course_run_status not in {"draft", "active", "archived"}
        ):
            raise RecommendationScopeViolation("recommendation archive state is invalid")
        for candidate in snapshot.candidates:
            if (
                candidate.organization_id != organization_id
                or candidate.course_run_id != course_run_id
            ):
                raise RecommendationScopeViolation(
                    "recommendation repository returned a cross-scope candidate"
                )

    @staticmethod
    def _reason(
        candidate: ReviewQueueCandidate,
        snapshot: RecommendationQueueSnapshot,
    ) -> tuple[str, ...]:
        reason = [
            f"review_deadline:{require_utc(candidate.review_deadline).isoformat()}",
        ]
        if candidate.continuing_reviewer_id == snapshot.reviewer_id:
            reason.append("same_reviewer_continuation")
        reason.extend(
            [
                f"submitted_at:{require_utc(candidate.submitted_at).isoformat()}",
                f"planned_minutes:{snapshot.planned_minutes}",
                f"assigned_minutes:{snapshot.assigned_minutes}",
                f"active_reviewer_count:{candidate.active_reviewer_count}",
            ]
        )
        return tuple(reason)


__all__ = [
    "RecommendationQueueSnapshot",
    "RecommendationRepository",
    "RecommendationScopeViolation",
    "RecommendationService",
    "RecommendationServiceError",
    "RecommendedOpenTarget",
    "ReviewQueueCandidate",
    "ReviewRecommendation",
]
