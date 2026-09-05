from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.publication_requests import (
    PublicationRequestConflict,
    PublicationRequestContext,
    PublicationRequestRecord,
    PublicationRequestService,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000002001")
ITERATION = UUID("00000000-0000-7000-8000-000000002002")
REVISION = UUID("00000000-0000-7000-8000-000000002003")
OTHER_REVISION = UUID("00000000-0000-7000-8000-000000002004")
AGENT = UUID("00000000-0000-7000-8000-000000002005")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000002006")
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
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            roles=actor.roles,
            membership_revision=actor.membership_revision,
            auth_epoch=actor.auth_epoch,
            active=True,
            agent_authorization_id=actor.agent_authorization_id,
            agent_authorization_revision=actor.agent_authorization_revision,
            agent_scopes=actor.scopes,
            agent_expires_at=actor.expires_at,
            agent_active=True,
        )


class Audits:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, event: object, *, transaction: object) -> None:
        self.events.append(event)


class Repository:
    def __init__(self) -> None:
        self.context = PublicationRequestContext(ORG, ITERATION, 3, REVISION)
        self.requests: dict[str, PublicationRequestRecord] = {}

    async def lock_iteration_context(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> PublicationRequestContext | None:
        if (organization_id, review_iteration_id, expected_revision) != (ORG, ITERATION, 3):
            return None
        return self.context

    async def reserve(
        self, candidate: PublicationRequestRecord, *, transaction: object
    ) -> tuple[PublicationRequestRecord, bool]:
        stored = self.requests.get(candidate.idempotency_key)
        if stored is not None:
            return stored, False
        self.requests[candidate.idempotency_key] = candidate
        return candidate, True


def _agent() -> RequestActor:
    return RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=1,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=4,
        scopes={"publication_requests:write"},
        expires_at=NOW + timedelta(hours=1),
    )


def _human() -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"methodologist"},
        membership_revision=1,
        auth_epoch=2,
    )


def _service(repository: Repository, audits: Audits) -> PublicationRequestService:
    ids = iter(UUID(int=value) for value in range(100, 1000))
    return PublicationRequestService(
        repository=repository,
        authorizer=Authorizer(Guard(), clock=lambda: NOW),
        audit=AuditRecorder(audits, event_id_factory=lambda: next(ids), clock=lambda: NOW),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
    )


async def _request(
    service: PublicationRequestService,
    actor: RequestActor,
    *,
    review_revision_id: UUID = REVISION,
    key: str = "publication-request-fixture-0001",
):
    return await service.request(
        transaction=object(),
        organization_id=ORG,
        review_iteration_id=ITERATION,
        expected_iteration_revision=3,
        review_revision_id=review_revision_id,
        expires_at=NOW + timedelta(minutes=30),
        idempotency_key=key,
        actor=actor,
        request_id=UUID(int=1),
        trace_id=UUID(int=2),
    )


@pytest.mark.anyio
async def test_agent_request_is_idempotent_harmless_and_preserves_provenance() -> None:
    repository, audits = Repository(), Audits()
    service = _service(repository, audits)
    first = await _request(service, _agent())
    replay = await _request(service, _agent())

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.request == first.request
    assert first.request.agent_id == AGENT
    assert first.request.agent_authorization_id == AUTHORIZATION
    assert first.request.status == "pending"
    assert len(repository.requests) == 1
    assert len(audits.events) == 1
    for forbidden in ("publications", "deliveries", "operations", "outbox"):
        assert not hasattr(repository, forbidden)


@pytest.mark.anyio
async def test_same_key_different_exact_revision_is_collision() -> None:
    repository, audits = Repository(), Audits()
    service = _service(repository, audits)
    await _request(service, _agent())
    repository.context = PublicationRequestContext(ORG, ITERATION, 3, OTHER_REVISION)

    with pytest.raises(PublicationRequestConflict, match="different"):
        await _request(service, _agent(), review_revision_id=OTHER_REVISION)
    assert len(repository.requests) == 1
    assert len(audits.events) == 1


@pytest.mark.anyio
async def test_interactive_human_can_create_same_harmless_request_without_agent_provenance() -> (
    None
):
    repository, audits = Repository(), Audits()
    result = await _request(
        _service(repository, audits),
        _human(),
        key="human-publication-request-0001",
    )

    assert result.request.agent_id is None
    assert result.request.agent_authorization_id is None
    assert result.request.requested_by_user_id == USER
