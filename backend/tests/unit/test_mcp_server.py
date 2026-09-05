from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.auth.agent_tokens import issue_agent_token
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.mcp.server import MCPToolHandler, create_mcp_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000182001")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000182002")
AGENT = UUID("00000000-0000-7000-8000-000000182003")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000182004")
COURSE = UUID("00000000-0000-7000-8000-000000182005")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _token() -> tuple[str, str]:
    issued = issue_agent_token(random_bytes=lambda size: b"m" * size)
    return issued.access_token.reveal(), issued.token_digest


async def _seed(factory: AsyncSessionFactory, token_digest: str) -> None:
    async with session_scope(factory) as session:
        session.add(User(id=USER, display_name="MCP server reviewer", status="active"))
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=USER,
                    roles=["reviewer"],
                    status="active",
                    revision=2,
                    auth_epoch=1,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="MCP server course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add(
            AgentAuthorization(
                id=AUTHORIZATION,
                organization_id=ORG,
                user_id=USER,
                agent_id=AGENT,
                scopes=["courses:read"],
                membership_revision=2,
                auth_epoch=1,
                token_digest=token_digest,
                status="active",
                expires_at=NOW + timedelta(hours=1),
                revoked_at=None,
                revision=3,
            )
        )


def _headers(token: str, name: str = "list_courses") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
    }


async def test_valid_strict_bearer_executes_stateless_default_tool(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    token, digest = _token()
    await _seed(foundation_session_factory, digest)
    app = create_mcp_app(runtime=foundation_runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp.test",
    ) as client:
        first = await client.post("/mcp", headers=_headers(token), json={})
        second = await client.post("/mcp", headers=_headers(token), json={})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {
        "items": [
            {
                "id": str(COURSE),
                "title": "MCP server course",
                "status": "active",
                "revision": 0,
            }
        ],
        "course_runs": [],
    }
    assert first.headers["mcp-protocol-version"] == "2026-07-28"
    assert "mcp-session-id" not in first.headers

    limited = create_mcp_app(
        Settings(command_body_limit_bytes=256),
        runtime=foundation_runtime,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=limited),
        base_url="http://mcp.test",
    ) as client:
        unknown = await client.post(
            "/mcp",
            headers=_headers(token),
            json={"organization_id": str(ORG)},
        )
        oversized = await client.post(
            "/mcp",
            headers=_headers(token),
            content=b'{"padding":"' + b"sensitive-provider-body-" * 32 + b'"}',
        )
    assert unknown.status_code == 400
    assert oversized.status_code == 413
    assert "sensitive-provider-body" not in oversized.text


class _InvalidateAuthorityDuringCall:
    async def __call__(
        self,
        *,
        actor: RequestActor,
        arguments: Mapping[str, Any],
        transaction: object,
    ) -> Mapping[str, Any]:
        del arguments
        assert isinstance(transaction, AsyncSession)
        row = await transaction.scalar(
            select(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == actor.organization_id,
                AgentAuthorization.id == actor.agent_authorization_id,
            )
            .with_for_update()
        )
        assert row is not None
        row.status = "revoked"
        row.revoked_at = NOW
        row.revision += 1
        await transaction.flush([row])
        return {"ok": True}


async def test_final_agent_revalidation_rejects_and_rolls_back_changed_authority(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    token, digest = _token()
    await _seed(foundation_session_factory, digest)
    handler: MCPToolHandler = _InvalidateAuthorityDuringCall()
    app = create_mcp_app(
        runtime=foundation_runtime,
        tool_registry={"test_authority_change": handler},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp.test",
    ) as client:
        response = await client.post(
            "/mcp",
            headers=_headers(token, "test_authority_change"),
            json={},
        )

    assert response.status_code == 401
    assert response.json() == {
        "code": "agent_authority_changed",
        "message": "agent authorization is no longer active",
        "action": "reauthorize_agent",
    }
    async with foundation_session_factory() as session:
        stored = await session.scalar(
            select(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.id == AUTHORIZATION,
            )
        )
    assert stored is not None
    assert stored.status == "active"
    assert stored.revision == 3
