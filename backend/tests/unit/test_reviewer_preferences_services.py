from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.reviewer_availability import (
    ReviewerAvailabilityConflict,
    ReviewerAvailabilityError,
    ReviewerAvailabilityService,
)
from review_platform.application.services.reviewer_course_selections import (
    ReviewerCourseSelectionService,
    ReviewerSelectionConflict,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000001701")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000001702")
RUN_A = UUID("00000000-0000-7000-8000-000000001703")
RUN_B = UUID("00000000-0000-7000-8000-000000001704")
PLAN = UUID("00000000-0000-7000-8000-000000001705")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class Guard:
    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        return self.snapshot(actor)

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        return self.snapshot(actor)

    @staticmethod
    def snapshot(actor: RequestActor) -> AuthVersionSnapshot:
        assert actor.user_id is not None
        assert actor.membership_revision is not None
        assert actor.auth_epoch is not None
        return AuthVersionSnapshot(
            actor.organization_id,
            actor.user_id,
            actor.roles,
            actor.membership_revision,
            actor.auth_epoch,
            True,
        )


class Audits:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, event: object, *, transaction: object) -> None:
        self.events.append(event)


class Repository:
    def __init__(self) -> None:
        self.membership_revision = 4
        self.active_runs = {RUN_A, RUN_B}
        self.selected: tuple[UUID, ...] = ()
        self.plan_revision = 0
        self.plan: tuple[int, datetime] | None = None

    async def lock_active_reviewer_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        reviewer_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> bool:
        return (
            organization_id == ORG
            and membership_id == MEMBERSHIP
            and reviewer_id == USER
            and expected_revision == self.membership_revision
        )

    async def replace_exact_active_runs(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        course_run_ids: tuple[UUID, ...],
        *,
        transaction: object,
    ) -> tuple[UUID, ...]:
        if not set(course_run_ids) <= self.active_runs:
            raise ReviewerSelectionConflict("inactive or foreign CourseRun")
        self.selected = course_run_ids
        return self.selected

    async def upsert_plan(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        *,
        planned_minutes: int,
        until_at: datetime,
        transaction: object,
    ) -> tuple[UUID, int]:
        self.plan_revision += 1
        self.plan = planned_minutes, until_at
        return PLAN, self.plan_revision


def _actor() -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=4,
        auth_epoch=2,
    )


def _dependencies() -> tuple[Repository, Authorizer, AuditRecorder]:
    repository = Repository()
    audit = AuditRecorder(Audits(), event_id_factory=lambda: UUID(int=10), clock=lambda: NOW)
    return repository, Authorizer(Guard(), clock=lambda: NOW), audit


@pytest.mark.anyio
async def test_selection_replaces_exact_active_course_run_set() -> None:
    repository, authorizer, audit = _dependencies()
    service = ReviewerCourseSelectionService(
        repository=repository, authorizer=authorizer, audit=audit
    )
    first = await service.replace(
        transaction=object(),
        organization_id=ORG,
        membership_id=MEMBERSHIP,
        expected_membership_revision=4,
        course_run_ids=(RUN_B, RUN_A),
        actor=_actor(),
        request_id=UUID(int=1),
        trace_id=UUID(int=2),
    )
    cleared = await service.replace(
        transaction=object(),
        organization_id=ORG,
        membership_id=MEMBERSHIP,
        expected_membership_revision=4,
        course_run_ids=(),
        actor=_actor(),
        request_id=UUID(int=3),
        trace_id=UUID(int=4),
    )

    assert first.selected_course_run_ids == tuple(sorted((RUN_A, RUN_B), key=str))
    assert cleared.selected_course_run_ids == ()
    assert repository.selected == ()
    with pytest.raises(ReviewerSelectionConflict):
        await service.replace(
            transaction=object(),
            organization_id=ORG,
            membership_id=MEMBERSHIP,
            expected_membership_revision=3,
            course_run_ids=(RUN_A,),
            actor=_actor(),
            request_id=UUID(int=5),
            trace_id=UUID(int=6),
        )


@pytest.mark.anyio
async def test_availability_is_advisory_free_form_without_hard_cap() -> None:
    repository, authorizer, audit = _dependencies()
    service = ReviewerAvailabilityService(repository=repository, authorizer=authorizer, audit=audit)
    result = await service.update(
        transaction=object(),
        organization_id=ORG,
        membership_id=MEMBERSHIP,
        expected_membership_revision=4,
        planned_minutes=10_000_000,
        until_at=NOW,
        actor=_actor(),
        request_id=UUID(int=7),
        trace_id=UUID(int=8),
    )

    assert result.planned_minutes == 10_000_000
    assert repository.plan == (10_000_000, NOW)
    with pytest.raises(ReviewerAvailabilityError, match="non-negative"):
        await service.update(
            transaction=object(),
            organization_id=ORG,
            membership_id=MEMBERSHIP,
            expected_membership_revision=4,
            planned_minutes=-1,
            until_at=NOW,
            actor=_actor(),
            request_id=UUID(int=9),
            trace_id=UUID(int=10),
        )
    with pytest.raises(ReviewerAvailabilityConflict):
        await service.update(
            transaction=object(),
            organization_id=ORG,
            membership_id=MEMBERSHIP,
            expected_membership_revision=99,
            planned_minutes=60,
            until_at=NOW,
            actor=_actor(),
            request_id=UUID(int=11),
            trace_id=UUID(int=12),
        )
