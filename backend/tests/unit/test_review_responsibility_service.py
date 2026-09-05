from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import anyio
import pytest

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.review_responsibility import (
    ResponsibilityContext,
    ReviewResponsibilityConflict,
    ReviewResponsibilityEvent,
    ReviewResponsibilityService,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
CASE = UUID("00000000-0000-7000-8000-000000001801")
ITERATION = UUID("00000000-0000-7000-8000-000000001802")
REVIEWER_A = UUID("00000000-0000-7000-8000-000000001803")
REVIEWER_B = UUID("00000000-0000-7000-8000-000000001804")
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
    async def append(self, event: object, *, transaction: object) -> None:
        return None


class Repository:
    def __init__(self) -> None:
        self.events: list[ReviewResponsibilityEvent] = []

    async def resolve_context(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        review_iteration_id: UUID | None,
        *,
        transaction: object,
    ) -> ResponsibilityContext | None:
        if organization_id != ORG or review_case_id != CASE:
            return None
        if review_iteration_id not in {None, ITERATION}:
            return None
        return ResponsibilityContext(organization_id, review_case_id, review_iteration_id)

    async def append(self, event: ReviewResponsibilityEvent, *, transaction: object) -> None:
        # Append deliberately has no uniqueness/owner check.
        await anyio.sleep(0)
        self.events.append(event)


def _actor(user_id: UUID, *, methodologist: bool = False) -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=user_id,
        roles={"methodologist" if methodologist else "reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )


def _service(repository: Repository, start: int) -> ReviewResponsibilityService:
    ids = iter(UUID(int=value) for value in range(start, start + 100))
    return ReviewResponsibilityService(
        repository=repository,
        authorizer=Authorizer(Guard(), clock=lambda: NOW),
        audit=AuditRecorder(Audits(), event_id_factory=lambda: next(ids), clock=lambda: NOW),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
    )


@pytest.mark.anyio
async def test_all_actions_are_distinct_append_only_events_for_own_identity() -> None:
    repository = Repository()
    service = _service(repository, 100)
    for index, action in enumerate(("started", "joined", "released", "completed")):
        result = await service.record(
            transaction=object(),
            organization_id=ORG,
            review_case_id=CASE,
            review_iteration_id=ITERATION,
            action=action,
            actor=_actor(REVIEWER_A),
            request_id=UUID(int=1000 + index),
            trace_id=UUID(int=2000 + index),
        )
        assert result.reviewer_id == REVIEWER_A

    assert [event.action for event in repository.events] == [
        "started",
        "joined",
        "released",
        "completed",
    ]
    assert len({event.event_id for event in repository.events}) == 4


@pytest.mark.anyio
async def test_concurrent_distinct_participants_are_nonexclusive() -> None:
    repository = Repository()

    async def record(user_id: UUID, methodologist: bool, start: int) -> None:
        await _service(repository, start).record(
            transaction=object(),
            organization_id=ORG,
            review_case_id=CASE,
            review_iteration_id=ITERATION,
            action="joined",
            actor=_actor(user_id, methodologist=methodologist),
            request_id=UUID(int=start + 50),
            trace_id=UUID(int=start + 60),
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(record, REVIEWER_A, False, 300)
        tasks.start_soon(record, REVIEWER_B, True, 500)

    assert {event.reviewer_id for event in repository.events} == {REVIEWER_A, REVIEWER_B}
    assert len(repository.events) == 2


@pytest.mark.anyio
async def test_case_iteration_affinity_fails_closed() -> None:
    repository = Repository()
    with pytest.raises(ReviewResponsibilityConflict):
        service = _service(repository, 700)
        original = repository.resolve_context

        async def wrong_context(*args: object, **kwargs: object) -> ResponsibilityContext:
            await original(ORG, CASE, ITERATION, transaction=object())
            return ResponsibilityContext(ORG, CASE, UUID(int=999))

        repository.resolve_context = wrong_context  # type: ignore[method-assign]
        await service.record(
            transaction=object(),
            organization_id=ORG,
            review_case_id=CASE,
            review_iteration_id=ITERATION,
            action="started",
            actor=_actor(REVIEWER_A),
            request_id=UUID(int=1),
            trace_id=UUID(int=2),
        )
