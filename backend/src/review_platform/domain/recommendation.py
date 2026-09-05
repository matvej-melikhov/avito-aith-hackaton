"""Deterministic, advisory-only ordering for eligible review work."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from review_platform.domain.primitives import require_utc


class RecommendationValidationError(ValueError):
    """A candidate or reviewer context cannot produce a safe stable order."""


class RecommendationCandidateLike(Protocol):
    @property
    def candidate_id(self) -> UUID: ...

    @property
    def review_deadline(self) -> datetime: ...

    @property
    def continuing_reviewer_id(self) -> UUID | None: ...

    @property
    def submitted_at(self) -> datetime: ...

    @property
    def estimated_review_minutes(self) -> int: ...

    @property
    def active_reviewer_count(self) -> int: ...


class ReviewerRecommendationContextLike(Protocol):
    @property
    def reviewer_id(self) -> UUID: ...

    @property
    def planned_minutes(self) -> int: ...

    @property
    def assigned_minutes(self) -> int: ...


@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    candidate_id: UUID
    review_deadline: datetime
    continuing_reviewer_id: UUID | None
    submitted_at: datetime
    estimated_review_minutes: int
    active_reviewer_count: int

    def __post_init__(self) -> None:
        _validate_candidate(self)


@dataclass(frozen=True, slots=True)
class ReviewerRecommendationContext:
    reviewer_id: UUID
    planned_minutes: int
    assigned_minutes: int

    def __post_init__(self) -> None:
        _validate_context(self)


def rank_recommendations[CandidateT: RecommendationCandidateLike](
    candidates: Sequence[CandidateT],
    *,
    context: ReviewerRecommendationContextLike,
) -> tuple[CandidateT, ...]:
    """Return every eligible candidate in one stable advisory order.

    Eligibility and tenant/archive checks belong to the application/repository
    boundary.  This function deliberately never filters by planned time: zero
    or exhausted availability changes only the advisory ordering.
    """

    _validate_context(context)
    candidate_values = tuple(candidates)
    seen_ids: set[UUID] = set()
    for candidate in candidate_values:
        _validate_candidate(candidate)
        if candidate.candidate_id in seen_ids:
            raise RecommendationValidationError("candidate IDs must be unique")
        seen_ids.add(candidate.candidate_id)
    return tuple(
        sorted(
            candidate_values,
            key=lambda candidate: recommendation_sort_key(candidate, context=context),
        )
    )


def recommendation_sort_key(
    candidate: RecommendationCandidateLike,
    *,
    context: ReviewerRecommendationContextLike,
) -> tuple[datetime, int, datetime, int, int, int, int]:
    """Expose the documented stable ordering key for repository-side parity."""

    _validate_context(context)
    _validate_candidate(candidate)
    fit_bucket, fit_distance = _planned_fit(
        candidate.estimated_review_minutes,
        planned_minutes=context.planned_minutes,
        assigned_minutes=context.assigned_minutes,
    )
    return (
        require_utc(candidate.review_deadline),
        0 if candidate.continuing_reviewer_id == context.reviewer_id else 1,
        require_utc(candidate.submitted_at),
        fit_bucket,
        fit_distance,
        candidate.active_reviewer_count,
        candidate.candidate_id.int,
    )


def _planned_fit(
    estimate: int,
    *,
    planned_minutes: int,
    assigned_minutes: int,
) -> tuple[int, int]:
    remaining = max(planned_minutes - assigned_minutes, 0)
    if remaining > 0 and estimate <= remaining:
        # Prefer a task that fits, then the task that uses the remaining plan
        # most closely.  This remains advisory and never drops larger work.
        return 0, remaining - estimate
    return 1, estimate - remaining


def _validate_context(context: ReviewerRecommendationContextLike) -> None:
    if not isinstance(context.reviewer_id, UUID):
        raise RecommendationValidationError("reviewer_id must be a UUID")
    _nonnegative_integer(context.planned_minutes, field="planned_minutes")
    _nonnegative_integer(context.assigned_minutes, field="assigned_minutes")


def _validate_candidate(candidate: RecommendationCandidateLike) -> None:
    if not isinstance(candidate.candidate_id, UUID):
        raise RecommendationValidationError("candidate_id must be a UUID")
    if candidate.continuing_reviewer_id is not None and not isinstance(
        candidate.continuing_reviewer_id,
        UUID,
    ):
        raise RecommendationValidationError("continuing_reviewer_id must be a UUID or None")
    try:
        require_utc(candidate.review_deadline)
        require_utc(candidate.submitted_at)
    except (AttributeError, TypeError, ValueError) as error:
        raise RecommendationValidationError(
            "candidate timestamps must be timezone-aware UTC"
        ) from error
    _positive_integer(
        candidate.estimated_review_minutes,
        field="estimated_review_minutes",
    )
    _nonnegative_integer(
        candidate.active_reviewer_count,
        field="active_reviewer_count",
    )


def _positive_integer(value: int, *, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RecommendationValidationError(f"{field} must be a positive integer")


def _nonnegative_integer(value: int, *, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RecommendationValidationError(f"{field} must be a nonnegative integer")


__all__ = [
    "RecommendationCandidate",
    "RecommendationCandidateLike",
    "RecommendationValidationError",
    "ReviewerRecommendationContext",
    "ReviewerRecommendationContextLike",
    "rank_recommendations",
    "recommendation_sort_key",
]
