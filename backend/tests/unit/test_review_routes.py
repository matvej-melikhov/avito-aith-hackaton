from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import WireCommand
from review_platform.infrastructure.db.models import OrganizationMembership, User
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

ITERATION = UUID("00000000-0000-7000-8000-000000002101")
USER = UUID("00000000-0000-7000-8000-000000002201")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000002202")


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
async def test_mutation_receipt_replays_injected_handler_result_once(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    async with session_scope(foundation_session_factory) as session:
        session.add(User(id=USER, display_name="Route Reviewer", status="active"))
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP,
                organization_id=UUID("00000000-0000-7000-8000-000000000001"),
                user_id=USER,
                roles=["reviewer"],
                status="active",
                revision=0,
                auth_epoch=0,
            )
        )
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        user_id=USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )
    handler = AvailabilityHandler()
    app.state.review_route_handlers = {"set_reviewer_availability": handler}

    @app.middleware("http")
    async def inject_reviewer(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
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

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"id": str(MEMBERSHIP), "revision": 1}
    assert handler.calls == 1
