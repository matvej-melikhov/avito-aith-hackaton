"""RED boundary contract: agents request publication; interactive humans publish."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from sqlalchemy import Table

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import ApplicationCommand
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.session import AsyncSessionFactory
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000001601")
ITERATION = UUID("00000000-0000-7000-8000-000000001602")
REVISION = UUID("00000000-0000-7000-8000-000000001603")
AGENT = UUID("00000000-0000-7000-8000-000000001604")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000001605")


def _table(name: str) -> Table:
    table = Base.metadata.tables.get(name)
    assert table is not None, f"T117 publication authority table is missing: {name}"
    return table


async def test_publication_request_carries_agent_provenance_without_publication_side_effects(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    request = _table("publication_request")
    publication = _table("review_publication")
    delivery = _table("external_delivery")
    assert {
        "organization_id",
        "id",
        "review_iteration_id",
        "review_revision_id",
        "requested_by_user_id",
        "agent_id",
        "agent_authorization_id",
        "idempotency_key",
        "status",
        "expires_at",
        "revision",
    } <= set(request.c.keys())
    assert "publication_request_id" in set(publication.c.keys())
    assert "publication_id" in set(delivery.c.keys())
    assert "delivery_id" not in set(request.c.keys())
    assert "published_at" not in set(request.c.keys())


async def test_review_publication_requires_human_identity_and_separate_delivery_intents(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    publication = _table("review_publication")
    delivery = _table("external_delivery")
    assert {
        "organization_id",
        "id",
        "review_iteration_id",
        "review_revision_id",
        "publication_request_id",
        "publication_version",
        "published_by",
        "published_at",
        "status",
        "revision",
    } <= set(publication.c.keys())
    assert not publication.c.published_by.nullable
    assert {
        "organization_id",
        "publication_id",
        "operation_id",
        "destination_binding_id",
        "binding_version",
        "state",
    } <= set(delivery.c.keys())


@pytest.fixture
async def authority_client(
    foundation_runtime: FoundationRuntime,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=0,
        scopes={"publication_requests:write"},
    )

    @app.middleware("http")
    async def inject_agent(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_agent_can_request_but_cannot_execute_interactive_publication(
    authority_client: httpx.AsyncClient,
) -> None:
    request_command = {
        "request_id": "00000000-0000-7000-8000-000000001606",
        "idempotency_key": "agent-publication-request-0001",
        "command_name": "request_review_publication",
        "revision_target": "review_iteration",
        "target_id": str(ITERATION),
        "expected_revision": 0,
        "payload": {
            "review_revision_id": str(REVISION),
            "expires_at": "2026-09-06T12:00:00Z",
        },
    }
    requested = await authority_client.post(
        f"/api/v1/review-iterations/{ITERATION}/publication-requests",
        json=request_command,
    )
    publish_command = {
        **request_command,
        "idempotency_key": "agent-direct-publish-0001",
        "command_name": "publish_review",
        "payload": {"review_revision_id": str(REVISION)},
    }
    # Frozen application validation independently guarantees the actor/transport
    # boundary even before the US5 route exists.
    with pytest.raises(ValueError, match="human-only"):
        ApplicationCommand.model_validate(
            {
                **publish_command,
                "organization_id": str(ORG),
                "actor": {
                    "type": "agent",
                    "user_id": str(USER),
                    "membership_revision": 0,
                    "auth_epoch": 0,
                    "agent_id": str(AGENT),
                    "agent_authorization_id": str(AUTHORIZATION),
                },
                "transport": "mcp",
                "trace_id": "00000000-0000-7000-8000-000000001607",
            }
        )
    published = await authority_client.post(
        f"/api/v1/review-iterations/{ITERATION}/publish",
        json=publish_command,
    )

    assert requested.status_code == 201
    assert published.status_code in {401, 403}
