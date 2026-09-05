"""US7 RED contract for the frozen stateless MCP 2026-07-28 transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from importlib import import_module
from importlib.util import find_spec
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.domain.primitives import utc_now
from review_platform.infrastructure.auth.agent_tokens import issue_agent_token
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app as create_api_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

PROTOCOL_VERSION = "2026-07-28"
MCP_PATH = "/mcp"
ORG = UUID("00000000-0000-7000-8000-000000181001")
USER = UUID("00000000-0000-7000-8000-000000181002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000181003")
AGENT = UUID("00000000-0000-7000-8000-000000181004")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000181005")
COURSE = UUID("00000000-0000-7000-8000-000000181006")
_ISSUED_TOKEN = issue_agent_token(random_bytes=lambda size: b"m" * size)
TOKEN = _ISSUED_TOKEN.access_token.reveal()


@pytest.fixture
async def mcp_client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, AsyncSessionFactory]]:
    async with session_scope(foundation_session_factory) as session:
        session.add_all(
            [
                Organization(id=ORG, slug="mcp-protocol", name="MCP Protocol"),
                User(id=USER, display_name="MCP represented user", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=USER,
                    roles=["reviewer"],
                    status="active",
                    revision=3,
                    auth_epoch=2,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="MCP visible course",
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
                membership_revision=3,
                auth_epoch=2,
                token_digest=_ISSUED_TOKEN.token_digest,
                status="active",
                expires_at=utc_now() + timedelta(hours=1),
                revoked_at=None,
                revision=4,
            )
        )
    app = _create_mcp_app(Settings(), runtime=foundation_runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp.test",
    ) as client:
        yield client, foundation_session_factory


def _create_mcp_app(settings: Settings, *, runtime: FoundationRuntime) -> Any:
    """Use the T155 factory when present; current RED falls through to HTTP 404."""

    try:
        specification = find_spec("review_platform.mcp.server")
    except ModuleNotFoundError:
        specification = None
    if specification is None:
        return create_api_app(settings, runtime=runtime)
    module = import_module("review_platform.mcp.server")
    factory = getattr(module, "create_mcp_app", None)
    if callable(factory):
        return factory(settings, runtime=runtime)
    builder = getattr(module, "build_mcp_server", None)
    if not callable(builder):
        return create_api_app(settings, runtime=runtime)
    server = builder(settings, runtime=runtime)
    asgi_factory = getattr(server, "streamable_http_app", None)
    return asgi_factory() if callable(asgi_factory) else server


def _headers(
    *,
    token: str = TOKEN,
    protocol_version: str = PROTOCOL_VERSION,
    method: str = "tools/call",
    name: str = "list_courses",
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": protocol_version,
        "Mcp-Method": method,
        "Mcp-Name": name,
    }


def _validate_component(name: str, value: object) -> None:
    openapi = load_openapi()
    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": openapi["components"],
        }
    ).validate(value)


def _assert_error(response: httpx.Response, status: int) -> None:
    assert response.status_code == status, response.text
    _validate_component("ErrorObject", response.json())
    assert len(response.content) <= 4096


async def test_mcp_requires_exact_protocol_method_and_name_headers(
    mcp_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, _factory = mcp_client
    headers = _headers()
    for required in ("MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"):
        incomplete = dict(headers)
        incomplete.pop(required)
        response = await client.post(MCP_PATH, headers=incomplete, json={})
        _assert_error(response, 400)


async def test_mcp_bearer_is_required_and_resolves_current_authorization(
    mcp_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, factory = mcp_client
    missing = _headers()
    missing.pop("Authorization")
    _assert_error(await client.post(MCP_PATH, headers=missing, json={}), 401)
    _assert_error(
        await client.post(MCP_PATH, headers=_headers(token="x" * 40), json={}),
        401,
    )

    async with session_scope(factory) as session:
        authorization = await session.scalar(
            select(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.id == AUTHORIZATION,
            )
        )
        assert authorization is not None
        authorization.status = "revoked"
        authorization.revoked_at = utc_now()
        authorization.revision += 1
    _assert_error(await client.post(MCP_PATH, headers=_headers(), json={}), 401)


async def test_mcp_calls_are_stateless_and_return_frozen_typed_output(
    mcp_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, _factory = mcp_client

    first = await client.post(MCP_PATH, headers=_headers(), json={})
    second = await client.post(MCP_PATH, headers=_headers(), json={})

    assert first.status_code == second.status_code == 200
    assert first.headers["mcp-protocol-version"] == PROTOCOL_VERSION
    assert second.headers["mcp-protocol-version"] == PROTOCOL_VERSION
    assert "mcp-session-id" not in first.headers
    assert "mcp-session-id" not in second.headers
    _validate_component("CourseList", first.json())
    _validate_component("CourseList", second.json())
    assert first.json() == second.json()
    assert first.json()["items"] == [
        {
            "id": str(COURSE),
            "title": "MCP visible course",
            "status": "active",
            "revision": 0,
        }
    ]
    assert first.json()["course_runs"] == []


async def test_obsolete_initialize_handshake_and_protocol_are_rejected(
    mcp_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, _factory = mcp_client
    obsolete = await client.post(
        MCP_PATH,
        headers=_headers(
            protocol_version="2025-06-18",
            method="initialize",
            name="initialize",
        ),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
    )

    _assert_error(obsolete, 400)


async def test_mcp_rejects_unknown_input_and_oversized_body_with_typed_errors(
    mcp_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
    foundation_runtime: FoundationRuntime,
) -> None:
    client, _factory = mcp_client
    unknown = await client.post(
        MCP_PATH,
        headers=_headers(),
        json={"organization_id": str(ORG)},
    )
    _assert_error(unknown, 400)

    limited_app = _create_mcp_app(
        Settings(command_body_limit_bytes=256),
        runtime=foundation_runtime,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=limited_app),
        base_url="http://mcp.test",
    ) as limited_client:
        oversized = await limited_client.post(
            MCP_PATH,
            headers=_headers(),
            content=b'{"padding":"' + b"sensitive-provider-body-" * 32 + b'"}',
        )
    _assert_error(oversized, 413)
    assert "sensitive-provider-body" not in oversized.text
