"""RED boundary contract: agents request publication; interactive humans publish."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import (
    AgentScope,
    AuthVersionSnapshot,
    RequestActor,
    Role,
)
from review_platform.contracts.commands import ApplicationCommand
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    Organization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.repositories.identity import (
    AgentAuthorizationRepository,
    OrganizationMembershipRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
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


class _AgentAuthorityGuard:
    """T153-shaped test guard over real tenant authority rows."""

    def __init__(self, factory: AsyncSessionFactory) -> None:
        self._factory = factory

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        async with self._factory() as session:
            organization = await session.scalar(
                select(Organization).where(Organization.id == actor.organization_id)
            )
            membership = await OrganizationMembershipRepository(session).get_by_user(
                actor.organization_id,
                cast(UUID, actor.user_id),
            )
            authorization = await AgentAuthorizationRepository(session).get(
                actor.organization_id,
                cast(UUID, actor.agent_authorization_id),
            )
            return self._snapshot(actor, organization, membership, authorization)

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        assert isinstance(transaction, AsyncSession)
        memberships = OrganizationMembershipRepository(transaction)
        organization = await memberships.lock_organization(actor.organization_id)
        membership = await memberships.get_by_user(
            actor.organization_id,
            cast(UUID, actor.user_id),
            for_update=True,
        )
        authorization = await AgentAuthorizationRepository(transaction).get(
            actor.organization_id,
            cast(UUID, actor.agent_authorization_id),
            for_update=True,
        )
        return self._snapshot(actor, organization, membership, authorization)

    @staticmethod
    def _snapshot(
        actor: RequestActor,
        organization: Organization | None,
        membership: OrganizationMembership | None,
        authorization: AgentAuthorization | None,
    ) -> AuthVersionSnapshot:
        assert actor.actor_type == "agent"
        assert organization is not None and organization.status == "active"
        assert membership is not None and membership.user_id == actor.user_id
        assert authorization is not None
        assert authorization.user_id == actor.user_id
        assert authorization.agent_id == actor.agent_id
        return AuthVersionSnapshot(
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            roles=cast(frozenset[Role], frozenset(membership.roles)),
            membership_revision=membership.revision,
            auth_epoch=membership.auth_epoch,
            active=membership.status == "active",
            agent_authorization_id=authorization.id,
            agent_authorization_revision=authorization.revision,
            agent_scopes=cast(frozenset[AgentScope], frozenset(authorization.scopes)),
            agent_expires_at=authorization.expires_at,
            agent_active=authorization.status == "active",
        )


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
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[httpx.AsyncClient]:
    async with session_scope(foundation_session_factory) as session:
        session.add(User(id=USER, display_name="Represented reviewer", status="active"))
        await session.flush()
        session.add(
            OrganizationMembership(
                id=UUID("00000000-0000-7000-8000-000000001608"),
                organization_id=ORG,
                user_id=USER,
                roles=["reviewer"],
                status="active",
                revision=0,
                auth_epoch=0,
            )
        )
        await session.flush()
        session.add(
            AgentAuthorization(
                id=AUTHORIZATION,
                organization_id=ORG,
                user_id=USER,
                agent_id=AGENT,
                scopes=["publication_requests:write"],
                membership_revision=0,
                auth_epoch=0,
                token_digest="sha256:" + "6" * 64,
                status="active",
                expires_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
                revision=0,
            )
        )
    app = create_app(Settings(), runtime=foundation_runtime)
    cast(Any, foundation_runtime)._membership_guard = _AgentAuthorityGuard(
        foundation_session_factory
    )

    async def harmless_publication_request(
        *,
        actor: RequestActor,
        command: object,
        transaction: object,
    ) -> Mapping[str, Any]:
        del command, transaction
        assert actor.agent_authorization_id == AUTHORIZATION
        return {
            "id": "00000000-0000-7000-8000-000000001609",
            "review_revision_id": str(REVISION),
            "status": "pending",
            "expires_at": "2026-09-06T12:00:00+00:00",
            "revision": 0,
        }

    app.state.review_route_handlers = {
        "request_review_publication": harmless_publication_request,
    }
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

    assert requested.status_code == 201, requested.text
    assert published.status_code in {401, 403}
