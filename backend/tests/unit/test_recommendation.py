from __future__ import annotations

import itertools
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from review_platform.domain.recommendation import (
    RecommendationCandidate,
    RecommendationValidationError,
    ReviewerRecommendationContext,
    rank_recommendations,
    recommendation_sort_key,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
REVIEWER = UUID("00000000-0000-7000-8000-000000000001")
OTHER_REVIEWER = UUID("00000000-0000-7000-8000-000000000002")


def _candidate(
    suffix: int,
    *,
    deadline_hours: int = 2,
    submitted_hours: int = 1,
    estimate: int = 60,
    continuing: UUID | None = None,
    active_reviewers: int = 1,
) -> RecommendationCandidate:
    return RecommendationCandidate(
        candidate_id=UUID(f"00000000-0000-7000-8000-{suffix:012d}"),
        review_deadline=NOW + timedelta(hours=deadline_hours),
        continuing_reviewer_id=continuing,
        submitted_at=NOW - timedelta(hours=submitted_hours),
        estimated_review_minutes=estimate,
        active_reviewer_count=active_reviewers,
    )


def _context(
    *,
    planned: int = 30,
    assigned: int = 0,
) -> ReviewerRecommendationContext:
    return ReviewerRecommendationContext(
        reviewer_id=REVIEWER,
        planned_minutes=planned,
        assigned_minutes=assigned,
    )


def test_exact_priority_chain_matches_t109_behavioral_contract() -> None:
    earliest = _candidate(1, deadline_hours=1, estimate=120)
    continuation = _candidate(2, continuing=REVIEWER)
    older = _candidate(3, submitted_hours=4)
    fits_plan = _candidate(4, estimate=20)
    lower_load = _candidate(5, active_reviewers=0)
    higher_load = _candidate(6, active_reviewers=2)

    ranked = rank_recommendations(
        [higher_load, lower_load, fits_plan, older, continuation, earliest],
        context=_context(),
    )

    assert ranked == (
        earliest,
        continuation,
        older,
        fits_plan,
        lower_load,
        higher_load,
    )


@pytest.mark.parametrize(
    ("preferred", "other"),
    [
        (_candidate(11, deadline_hours=1), _candidate(12, deadline_hours=2)),
        (_candidate(13, continuing=REVIEWER), _candidate(14)),
        (_candidate(15, submitted_hours=2), _candidate(16, submitted_hours=1)),
        (_candidate(17, estimate=25), _candidate(18, estimate=40)),
        (_candidate(19, active_reviewers=0), _candidate(20, active_reviewers=2)),
        (_candidate(21), _candidate(22)),
    ],
)
def test_each_priority_is_a_stable_tiebreaker(
    preferred: RecommendationCandidate,
    other: RecommendationCandidate,
) -> None:
    assert rank_recommendations([other, preferred], context=_context())[0] is preferred


def test_deadline_age_and_continuity_outrank_advisory_signals() -> None:
    overloaded_early = _candidate(
        31,
        deadline_hours=1,
        submitted_hours=1,
        estimate=600,
        active_reviewers=100,
    )
    light_late = _candidate(
        32,
        deadline_hours=2,
        submitted_hours=20,
        estimate=5,
        active_reviewers=0,
    )
    other_reviewer = _candidate(33, continuing=OTHER_REVIEWER, estimate=5)
    same_reviewer = _candidate(34, continuing=REVIEWER, estimate=600)

    assert rank_recommendations([light_late, overloaded_early], context=_context())[0] is (
        overloaded_early
    )
    assert rank_recommendations([other_reviewer, same_reviewer], context=_context())[0] is (
        same_reviewer
    )


def test_zero_or_exhausted_plan_never_filters_actual_work() -> None:
    candidates = [_candidate(41, estimate=120), _candidate(42, estimate=10)]

    zero = rank_recommendations(candidates, context=_context(planned=0))
    exhausted = rank_recommendations(
        candidates,
        context=_context(planned=30, assigned=90),
    )

    assert {item.candidate_id for item in zero} == {
        item.candidate_id for item in candidates
    }
    assert {item.candidate_id for item in exhausted} == {
        item.candidate_id for item in candidates
    }
    assert zero[0].estimated_review_minutes == 10
    assert exhausted[0].estimated_review_minutes == 10


def test_plan_fit_prefers_closest_task_that_stays_within_remaining_minutes() -> None:
    exact = _candidate(51, estimate=30)
    smaller = _candidate(52, estimate=20)
    too_large = _candidate(53, estimate=31)

    ranked = rank_recommendations(
        [too_large, smaller, exact],
        context=_context(planned=45, assigned=15),
    )

    assert ranked == (exact, smaller, too_large)


def test_every_input_permutation_produces_the_same_uuid_stable_order() -> None:
    candidates = tuple(_candidate(suffix) for suffix in (63, 61, 62))
    expected = tuple(sorted(candidates, key=lambda item: item.candidate_id.int))

    for permutation in itertools.permutations(candidates):
        assert rank_recommendations(permutation, context=_context()) == expected


def test_ranking_does_not_mutate_input_or_replace_candidate_objects() -> None:
    first = _candidate(71, deadline_hours=2)
    second = _candidate(72, deadline_hours=1)
    source = [first, second]
    before = list(source)

    ranked = rank_recommendations(source, context=_context())

    assert source == before
    assert ranked == (second, first)
    assert ranked[0] is second and ranked[1] is first
    with pytest.raises(FrozenInstanceError):
        first.active_reviewer_count = 10  # type: ignore[misc]


@pytest.mark.parametrize(
    "candidate",
    [
        RecommendationCandidate(
            candidate_id=UUID("00000000-0000-7000-8000-000000000081"),
            review_deadline=NOW,
            continuing_reviewer_id=None,
            submitted_at=NOW,
            estimated_review_minutes=1,
            active_reviewer_count=0,
        ),
    ],
)
def test_sort_key_is_public_and_matches_rank(
    candidate: RecommendationCandidate,
) -> None:
    context = _context()
    assert recommendation_sort_key(candidate, context=context) == (
        NOW,
        1,
        NOW,
        0,
        29,
        0,
        candidate.candidate_id.int,
    )


def test_invalid_time_counts_and_duplicate_identity_fail_closed() -> None:
    with pytest.raises(RecommendationValidationError, match="timestamps"):
        RecommendationCandidate(
            candidate_id=UUID("00000000-0000-7000-8000-000000000091"),
            review_deadline=datetime(2026, 9, 5, 12, 0),
            continuing_reviewer_id=None,
            submitted_at=NOW,
            estimated_review_minutes=1,
            active_reviewer_count=0,
        )
    with pytest.raises(RecommendationValidationError, match="positive"):
        _candidate(92, estimate=0)
    with pytest.raises(RecommendationValidationError, match="nonnegative"):
        _candidate(93, active_reviewers=-1)
    with pytest.raises(RecommendationValidationError, match="nonnegative"):
        _context(planned=-1)

    duplicate = _candidate(94)
    with pytest.raises(RecommendationValidationError, match="unique"):
        rank_recommendations([duplicate, duplicate], context=_context())


def test_empty_input_is_a_valid_deterministic_result() -> None:
    assert rank_recommendations([], context=_context()) == ()
