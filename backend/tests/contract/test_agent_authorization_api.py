"""US7 RED contract for interactive, revocable agent authorization grants."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from jsonschema import ValidationError
from sqlalchemy import func, select
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.contracts.registry import ContractRegistry
from review_platform.domain.primitives import sha256_digest, utc_now
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.operations import AuditEvent, CommandReceipt
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000180001")
USER = UUID("00000000-0000-7000-8000-000000180002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000180003")
AGENT = UUID("00000000-0000-7000-8000-000000180004")
SCOPES = ["courses:read", "reviews:read", "reviews:write"]
GRANT_PATH = f"/api/v1/memberships/{MEMBERSHIP}/agent-authorizations"


@pytest.fixture
async def agent_authorization_client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, AsyncSessionFactory]]:
    async with session_scope(foundation_session_factory) as session:
        session.add_all(
            [
                Organization(id=ORG, slug="agent-authorization", name="Agent Authorization"),
                User(id=USER, display_name="Agent owner", status="active"),
            ]
        )
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP,
                organization_id=ORG,
                user_id=USER,
                roles=["reviewer"],
                status="active",
                revision=3,
                auth_epoch=2,
            )
        )
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
    )

    @app.middleware("http")
    async def inject_authenticated_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent-authorization.test",
    ) as client:
        yield client, foundation_session_factory


def _command(
    *,
    name: str,
    target: UUID,
    revision_target: str,
    expected_revision: int,
    payload: Mapping[str, object],
    request_number: int,
) -> dict[str, object]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request_number:012d}",
        "idempotency_key": f"agent-authorization-{request_number:08d}",
        "command_name": name,
        "revision_target": revision_target,
        "target_id": str(target),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


def _grant_command(*, expires_at: datetime, request_number: int = 1) -> dict[str, object]:
    return _command(
        name="grant_agent_authorization",
        target=MEMBERSHIP,
        revision_target="membership",
        expected_revision=3,
        payload={
            "agent_id": str(AGENT),
            "scopes": SCOPES,
            "expires_at": expires_at.isoformat(),
        },
        request_number=request_number,
    )


def _validate_component(name: str, value: object) -> None:
    openapi = load_openapi()
    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": openapi["components"],
        }
    ).validate(value)


async def test_interactive_owner_grant_returns_secret_once_and_stores_only_digest(
    agent_authorization_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, factory = agent_authorization_client
    expires_at = utc_now() + timedelta(hours=1)
    command = _grant_command(expires_at=expires_at)
    ContractRegistry().validate(
        command,
        "command.schema.json",
        definition="grant_agent_authorization",
    )

    response = await client.post(GRANT_PATH, json=command)

    assert response.status_code == 201, response.text
    created = response.json()
    _validate_component("AgentAuthorizationCreated", created)
    assert created["agent_id"] == str(AGENT)
    assert created["scopes"] == SCOPES
    assert created["expires_at"] == expires_at.isoformat()
    secret = created["access_token"]
    assert isinstance(secret, str) and len(secret) >= 32

    authorization_id = UUID(created["id"])
    async with factory() as session:
        stored = await session.scalar(
            select(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.id == authorization_id,
            )
        )
        receipts = (
            await session.scalars(
                select(CommandReceipt).where(CommandReceipt.organization_id == ORG)
            )
        ).all()
        audit_events = (
            await session.scalars(select(AuditEvent).where(AuditEvent.organization_id == ORG))
        ).all()
    assert stored is not None
    assert stored.user_id == USER
    assert stored.agent_id == AGENT
    assert stored.scopes == SCOPES
    assert stored.membership_revision == 3
    assert stored.auth_epoch == 2
    assert stored.status == "active"
    assert stored.token_digest == sha256_digest(secret)
    assert secret not in stored.token_digest
    persisted_metadata = json.dumps(
        {
            "receipts": [
                {
                    "actor": item.actor_snapshot,
                    "result": item.result_reference,
                }
                for item in receipts
            ],
            "audit": [item.sanitized_details for item in audit_events],
        },
        default=str,
        sort_keys=True,
    )
    assert secret not in persisted_metadata

    replay = await client.post(GRANT_PATH, json=command)
    assert replay.status_code == 409, replay.text
    _validate_component("ErrorObject", replay.json())
    assert secret not in replay.text
    async with factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.user_id == USER,
                AgentAuthorization.agent_id == AGENT,
            )
        )
    assert count == 1


async def test_grant_rejects_unknown_scope_and_nonfuture_expiry_without_persistence(
    agent_authorization_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, factory = agent_authorization_client
    invalid_scope = _grant_command(expires_at=utc_now() + timedelta(hours=1))
    payload = invalid_scope["payload"]
    assert isinstance(payload, dict)
    payload["scopes"] = ["reviews:read", "admin:everything"]
    with pytest.raises(ValidationError):
        ContractRegistry().validate(
            invalid_scope,
            "command.schema.json",
            definition="grant_agent_authorization",
        )

    scope_response = await client.post(GRANT_PATH, json=invalid_scope)
    assert scope_response.status_code == 409, scope_response.text
    _validate_component("ErrorObject", scope_response.json())

    expired = _grant_command(
        expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        request_number=2,
    )
    ContractRegistry().validate(
        expired,
        "command.schema.json",
        definition="grant_agent_authorization",
    )
    expiry_response = await client.post(GRANT_PATH, json=expired)
    assert expiry_response.status_code == 409, expiry_response.text
    _validate_component("ErrorObject", expiry_response.json())

    async with factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG
            )
        )
    assert count == 0


async def test_interactive_owner_can_revoke_and_token_state_is_invalidated(
    agent_authorization_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, factory = agent_authorization_client
    grant = await client.post(
        GRANT_PATH,
        json=_grant_command(expires_at=utc_now() + timedelta(hours=1), request_number=3),
    )
    assert grant.status_code == 201, grant.text
    authorization_id = UUID(grant.json()["id"])
    revoke = _command(
        name="revoke_agent_authorization",
        target=authorization_id,
        revision_target="agent_authorization",
        expected_revision=0,
        payload={"reason": "agent access is no longer needed"},
        request_number=4,
    )
    ContractRegistry().validate(
        revoke,
        "command.schema.json",
        definition="revoke_agent_authorization",
    )

    response = await client.post(
        f"/api/v1/agent-authorizations/{authorization_id}/revoke",
        json=revoke,
    )

    assert response.status_code == 204, response.text
    async with factory() as session:
        stored = await session.scalar(
            select(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.id == authorization_id,
            )
        )
    assert stored is not None
    assert stored.status == "revoked"
    assert stored.revoked_at is not None
    assert stored.revision == 1


async def test_agent_actor_cannot_grant_another_agent_authorization(
    foundation_runtime: FoundationRuntime,
) -> None:
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=UUID("00000000-0000-7000-8000-000000180099"),
        agent_authorization_revision=0,
        scopes={"reviews:write"},
    )

    @app.middleware("http")
    async def inject_agent_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent-authorization.test",
    ) as client:
        response = await client.post(
            GRANT_PATH,
            json=_grant_command(expires_at=utc_now() + timedelta(hours=1), request_number=5),
        )

    assert response.status_code == 403, response.text
    _validate_component("ErrorObject", response.json())
