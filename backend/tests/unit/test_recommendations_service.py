from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from review_platform.application.authorization import AuthorizationDenied, Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.recommendations import (
    RecommendationQueueSnapshot,
    RecommendationScopeViolation,
    RecommendationService,
    RecommendedOpenTarget,
    ReviewQueueCandidate,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
REVIEWER = UUID("00000000-0000-7000-8000-000000040001")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000040003")
TRANSACTION = object()


class FakeRepository:
    def __init__(self, snapshot: RecommendationQueueSnapshot | None) -> None:
        self.snapshot = snapshot
        self.read_calls = 0

    async def load_queue(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        course_run_id: UUID,
        *,
        now: datetime,
        transaction: object,
    ) -> RecommendationQueueSnapshot | None:
        assert (organization_id, reviewer_id, course_run_id) == (
            ORG,
            REVIEWER,
            COURSE_RUN,
        )
        assert now == NOW
        assert transaction is TRANSACTION
        self.read_calls += 1
        return self.snapshot

class FakeAuthGuard:
    def __init__(self) -> None:
        self.early = 0
        self.final = 0

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        self.early += 1
        return _auth_snapshot(actor)

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        assert transaction is TRANSACTION
        self.final += 1
        return _auth_snapshot(actor)


def _auth_snapshot(actor: RequestActor) -> AuthVersionSnapshot:
    assert actor.user_id is not None
    assert actor.membership_revision is not None
    assert actor.auth_epoch is not None
    return AuthVersionSnapshot(
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        roles=actor.roles,
        membership_revision=actor.membership_revision,
        auth_epoch=actor.auth_epoch,
        active=True,
    )


def _actor(role: str = "reviewer", *, user_id: UUID = REVIEWER) -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=user_id,
        roles=[role],  # type: ignore[list-item]
        membership_revision=3,
        auth_epoch=2,
    )


def _candidate(
    suffix: int,
    *,
    deadline_hours: int = 2,
    continuing: UUID | None = None,
    organization_id: UUID = ORG,
) -> ReviewQueueCandidate:
    review_case_id = UUID(f"00000000-0000-7000-8000-{suffix:012d}")
    return ReviewQueueCandidate(
        organization_id=organization_id,
        course_run_id=COURSE_RUN,
        candidate_id=review_case_id,
        review_case_id=review_case_id,
        review_case_revision=4,
        submission_version_id=UUID(
            f"00000000-0000-7000-8001-{suffix:012d}"
        ),
        review_deadline=NOW + timedelta(hours=deadline_hours),
        continuing_reviewer_id=continuing,
        submitted_at=NOW - timedelta(hours=1),
        estimated_review_minutes=30,
        active_reviewer_count=1,
    )


def _snapshot(
    *candidates: ReviewQueueCandidate,
    selected: bool = True,
    course_status: str = "active",
    course_run_status: str = "active",
    planned_minutes: int = 60,
) -> RecommendationQueueSnapshot:
    return RecommendationQueueSnapshot(
        organization_id=ORG,
        reviewer_id=REVIEWER,
        course_run_id=COURSE_RUN,
        course_status=course_status,  # type: ignore[arg-type]
        course_run_status=course_run_status,  # type: ignore[arg-type]
        selected=selected,
        planned_minutes=planned_minutes,
        assigned_minutes=0,
        candidates=tuple(candidates),
    )


def _service(
    snapshot: RecommendationQueueSnapshot | None,
) -> tuple[RecommendationService, FakeRepository, FakeAuthGuard]:
    repository = FakeRepository(snapshot)
    guard = FakeAuthGuard()
    return (
        RecommendationService(
            repository=repository,
            authorizer=Authorizer(guard, clock=lambda: NOW),
        ),
        repository,
        guard,
    )


@pytest.mark.anyio
async def test_read_returns_deterministic_exact_open_target_without_claiming() -> None:
    later_continuation = _candidate(2, deadline_hours=2, continuing=REVIEWER)
    earliest = _candidate(1, deadline_hours=1)
    service, repository, guard = _service(
        _snapshot(later_continuation, earliest)
    )

    result = await service.recommend_next(
        organization_id=ORG,
        course_run_id=COURSE_RUN,
        actor=_actor(),
        now=NOW,
        transaction=TRANSACTION,
    )

    assert result is not None
    assert result.review_case_id == earliest.review_case_id
    assert result.review_case_revision == earliest.review_case_revision
    assert result.submission_version_id == earliest.submission_version_id
    assert result.open_target == RecommendedOpenTarget(
        review_case_id=earliest.review_case_id,
        review_case_revision=earliest.review_case_revision,
        submission_version_id=earliest.submission_version_id,
    )
    assert result.reason == (
        f"review_deadline:{earliest.review_deadline.isoformat()}",
        f"submitted_at:{earliest.submitted_at.isoformat()}",
        "planned_minutes:60",
        "assigned_minutes:0",
        "active_reviewer_count:1",
    )
    assert repository.read_calls == 1
    assert guard.early == 1
    assert guard.final == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("selected", "course_status", "course_run_status"),
    [
        (False, "active", "active"),
        (True, "archived", "active"),
        (True, "active", "archived"),
        (True, "active", "draft"),
    ],
)
async def test_unselected_or_nonactive_course_run_has_no_recommendation(
    selected: bool,
    course_status: str,
    course_run_status: str,
) -> None:
    service, repository, _ = _service(
        _snapshot(
            _candidate(10),
            selected=selected,
            course_status=course_status,
            course_run_status=course_run_status,
        )
    )

    result = await service.recommend_next(
        organization_id=ORG,
        course_run_id=COURSE_RUN,
        actor=_actor(),
        now=NOW,
        transaction=TRANSACTION,
    )

    assert result is None


@pytest.mark.anyio
async def test_zero_plan_is_advisory_and_does_not_hide_eligible_work() -> None:
    candidate = _candidate(20)
    service, _, _ = _service(_snapshot(candidate, planned_minutes=0))

    result = await service.recommend_next(
        organization_id=ORG,
        course_run_id=COURSE_RUN,
        actor=_actor(),
        now=NOW,
        transaction=TRANSACTION,
    )

    assert result is not None and result.review_case_id == candidate.review_case_id


@pytest.mark.anyio
async def test_same_reviewer_continuation_is_preserved_in_exact_open_target() -> None:
    continuation = _candidate(21, continuing=REVIEWER)
    older_other = replace(
        _candidate(22),
        submitted_at=NOW - timedelta(days=1),
    )
    service, _, guard = _service(_snapshot(older_other, continuation))

    result = await service.recommend_next(
        organization_id=ORG,
        course_run_id=COURSE_RUN,
        actor=_actor(),
        now=NOW,
        transaction=TRANSACTION,
    )

    assert result is not None
    assert result.review_case_id == continuation.review_case_id
    assert result.open_target.submission_version_id == continuation.submission_version_id
    assert "same_reviewer_continuation" in result.reason
    assert guard.final == 0


@pytest.mark.anyio
async def test_cross_tenant_candidate_is_rejected_instead_of_ranked() -> None:
    service, _, _ = _service(_snapshot(_candidate(30, organization_id=OTHER_ORG)))

    with pytest.raises(RecommendationScopeViolation, match="cross-scope"):
        await service.recommend_next(
            organization_id=ORG,
            course_run_id=COURSE_RUN,
            actor=_actor(),
            now=NOW,
            transaction=TRANSACTION,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["student", "methodologist"])
async def test_nonreviewer_cannot_read_recommendation(role: str) -> None:
    candidate = _candidate(80)
    service, _, _ = _service(_snapshot(candidate))
    with pytest.raises(AuthorizationDenied):
        await service.recommend_next(
            organization_id=ORG,
            course_run_id=COURSE_RUN,
            actor=_actor(role),
            now=NOW,
            transaction=TRANSACTION,
        )
