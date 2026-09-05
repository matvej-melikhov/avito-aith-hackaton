from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from sqlalchemy import select

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import WireCommand
from review_platform.infrastructure.db.models import CommandReceipt, OrganizationMembership, User
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

ITERATION = UUID("00000000-0000-7000-8000-000000002101")
USER = UUID("00000000-0000-7000-8000-000000002201")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000002202")
OTHER_USER = UUID("00000000-0000-7000-8000-000000002204")
OTHER_MEMBERSHIP = UUID("00000000-0000-7000-8000-000000002205")


class AvailabilityHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self,
        *,
        actor: RequestActor,
        command: WireCommand,
        transaction: object,
    ) -> Mapping[str, Any]:
        del actor, command, transaction
        self.calls += 1
        return {"id": str(MEMBERSHIP), "revision": 1}


def test_exact_frozen_review_routes_are_registered() -> None:
    app = create_app(Settings())
    operation_ids = {route.operation_id for route in app.routes if hasattr(route, "operation_id")}
    assert {
        "setReviewerCourseSelection",
        "setReviewerAvailability",
        "recommendNextReview",
        "saveReviewRevision",
        "migrateReviewRequirements",
        "createReviewCorrection",
        "recordReviewResponsibility",
        "getReviewIteration",
        "requestReviewPublication",
        "publishReview",
    } <= operation_ids


@pytest.fixture
async def agent_client(
    foundation_runtime: FoundationRuntime,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.agent(
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        user_id=UUID("00000000-0000-7000-8000-000000002102"),
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
        agent_id=UUID("00000000-0000-7000-8000-000000002103"),
        agent_authorization_id=UUID("00000000-0000-7000-8000-000000002104"),
        agent_authorization_revision=0,
        scopes={"publication_requests:write"},
    )

    @app.middleware("http")
    async def inject(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.anyio
async def test_agent_publish_is_rejected_before_handler_or_database(
    agent_client: httpx.AsyncClient,
) -> None:
    response = await agent_client.post(
        f"/api/v1/review-iterations/{ITERATION}/publish",
        json={
            "request_id": "00000000-0000-7000-8000-000000002105",
            "idempotency_key": "agent-direct-publish-0001",
            "command_name": "publish_review",
            "revision_target": "review_iteration",
            "target_id": str(ITERATION),
            "expected_revision": 0,
            "payload": {"review_revision_id": "00000000-0000-7000-8000-000000002106"},
        },
    )

    assert response.status_code == 403
    assert set(response.json()) == {"code", "message", "action"}


@pytest.mark.anyio
async def test_mutation_receipt_replays_only_for_exact_original_actor_snapshot(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    async with session_scope(foundation_session_factory) as session:
        session.add_all(
            [
                User(id=USER, display_name="Route Reviewer", status="active"),
                User(id=OTHER_USER, display_name="Other Reviewer", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=UUID(
                        "00000000-0000-7000-8000-000000000001"
                    ),
                    user_id=USER,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                OrganizationMembership(
                    id=OTHER_MEMBERSHIP,
                    organization_id=UUID(
                        "00000000-0000-7000-8000-000000000001"
                    ),
                    user_id=OTHER_USER,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
            ]
        )
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        user_id=USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )
    other_actor = RequestActor.user(
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        user_id=OTHER_USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )
    current_actor = [actor]
    handler = AvailabilityHandler()
    app.state.review_route_handlers = {"set_reviewer_availability": handler}

    @app.middleware("http")
    async def inject_reviewer(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = current_actor[0]
        return await call_next(request)

    command = {
        "request_id": "00000000-0000-7000-8000-000000002203",
        "idempotency_key": "review-route-replay-0001",
        "command_name": "set_reviewer_availability",
        "revision_target": "membership",
        "target_id": str(MEMBERSHIP),
        "expected_revision": 0,
        "payload": {
            "planned_minutes": 120,
            "until_at": "2026-09-06T12:00:00Z",
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.put("/api/v1/reviewer/availability", json=command)
        replay = await client.put("/api/v1/reviewer/availability", json=command)
        current_actor[0] = other_actor
        cross_actor = await client.put(
            "/api/v1/reviewer/availability",
            json=command,
        )
        async with session_scope(foundation_session_factory) as session:
            membership = await session.get(OrganizationMembership, OTHER_MEMBERSHIP)
            assert membership is not None
            membership.roles = ["student"]
            membership.revision = 1
            membership.auth_epoch = 1
        current_actor[0] = RequestActor.user(
            organization_id=actor.organization_id,
            user_id=OTHER_USER,
            roles={"student"},
            membership_revision=1,
            auth_epoch=1,
        )
        role_denied = await client.put(
            "/api/v1/reviewer/availability",
            json=command,
        )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"id": str(MEMBERSHIP), "revision": 1}
    assert cross_actor.status_code == 409
    assert "actor or authority snapshot" in cross_actor.json()["message"]
    assert role_denied.status_code == 403
    assert "role" in role_denied.json()["message"]
    assert handler.calls == 1
    async with foundation_session_factory() as session:
        receipt = await session.scalar(
            select(CommandReceipt).where(
                CommandReceipt.organization_id == actor.organization_id,
                CommandReceipt.idempotency_key == command["idempotency_key"],
            )
        )
    assert receipt is not None
    assert receipt.actor_snapshot == {
        "type": "user",
        "organization_id": str(actor.organization_id),
        "user_id": str(USER),
        "roles": ["reviewer"],
        "membership_revision": 0,
        "auth_epoch": 0,
        "agent_id": None,
        "agent_authorization_id": None,
        "agent_authorization_revision": None,
        "scopes": [],
        "expires_at": None,
    }
