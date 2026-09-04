"""RED contract specifications for the US1 identity and session boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from jsonschema import Draft202012Validator, FormatChecker
from tests.support.contracts import load_openapi

from review_platform.application.ports.providers import ProviderPayload
from review_platform.contracts.commands import ApplicationCommand
from review_platform.contracts.registry import ContractRegistry
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.anyio]

ORGANIZATION_ID = "00000000-0000-7000-8000-000000000001"
USER_ID = "00000000-0000-7000-8000-000000000002"
MEMBERSHIP_ID = "00000000-0000-7000-8000-000000000003"
REQUEST_ID = "00000000-0000-7000-8000-000000000004"
TRACE_ID = "00000000-0000-7000-8000-000000000005"
STATE_ID = "00000000-0000-7000-8000-000000000006"
CREDENTIAL_BINDING_ID = "00000000-0000-7000-8000-000000000007"
SESSION_COOKIE = "review_session"
SESSION_SECRET = "offline-session-secret-with-at-least-32-bytes"
MAGIC_LINK_TOKEN = "offline-magic-link-token-with-at-least-32-bytes"
API_PREFIX = "/api"


@dataclass(slots=True)
class RecordingIdentityProvider:
    """Local provider boundary; it validates both sides and never uses the network."""

    registry: ContractRegistry = field(default_factory=ContractRegistry)
    requests: list[ProviderPayload] = field(default_factory=list)

    @property
    def contract_version(self) -> str:
        return "1.1.0"

    @property
    def schema_name(self) -> str:
        return "identity-provider.schema.json"

    async def verify_identity(self, request: ProviderPayload) -> ProviderPayload:
        self.registry.validate(request, self.schema_name, definition="verification_request")
        self.requests.append(request)
        provider = request["provider"]
        state_id = request["state_id"]
        assert isinstance(provider, str)
        assert isinstance(state_id, str)
        assertion: ProviderPayload = {
            "contract_version": self.contract_version,
            "provider": provider,
            "issuer": "https://identity.example.test",
            "subject": "fixture-user",
            "verified_email": "reviewer@example.test",
            "issued_at": "2026-09-04T12:00:00Z",
            "expires_at": "2026-09-04T12:05:00Z",
            "state_id": state_id,
        }
        self.registry.validate(assertion, self.schema_name, definition="identity_assertion")
        return assertion


@pytest.fixture
def identity_app() -> FastAPI:
    app = create_app(Settings())
    app.state.identity_provider = RecordingIdentityProvider()
    return app


@pytest.fixture
async def identity_client(identity_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=identity_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://identity.test",
        follow_redirects=False,
        cookies={SESSION_COOKIE: SESSION_SECRET},
    ) as client:
        yield client


def _component_validator(name: str) -> Draft202012Validator:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **load_openapi()["components"]["schemas"][name],
    }
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _assert_error(response: Response, expected_status: int) -> None:
    assert response.status_code == expected_status
    payload = response.json()
    _component_validator("ErrorObject").validate(payload)


def _oauth_state(response: Response) -> str:
    assert response.status_code == 302
    location = response.headers["location"]
    state_values = parse_qs(urlparse(location).query).get("state", [])
    assert len(state_values) == 1 and state_values[0]
    return state_values[0]


def test_bootstrap_and_recovery_are_operator_only_and_have_no_public_routes(
    identity_app: FastAPI,
) -> None:
    registry = ContractRegistry()
    commands = (
        {
            "request_id": REQUEST_ID,
            "idempotency_key": "activate-bootstrap-fixture-0001",
            "command_name": "activate_bootstrap",
            "revision_target": "organization",
            "target_id": ORGANIZATION_ID,
            "expected_revision": 0,
            "payload": {
                "external_identity": {
                    "provider": "stepik",
                    "issuer": "https://stepik.org",
                    "subject": "fixture-methodologist",
                }
            },
        },
        {
            "request_id": REQUEST_ID,
            "idempotency_key": "recover-methodologist-fixture-0001",
            "command_name": "recover_methodologist",
            "revision_target": "organization",
            "target_id": ORGANIZATION_ID,
            "expected_revision": 1,
            "payload": {
                "organization_id": ORGANIZATION_ID,
                "provider": "stepik",
                "issuer": "https://stepik.org",
                "subject": "fixture-methodologist",
                "reason": "recover installation access",
            },
        },
    )
    for command in commands:
        application = ApplicationCommand.model_validate(
            {
                **command,
                "organization_id": ORGANIZATION_ID,
                "actor": {
                    "type": "installation_operator",
                    "installation_operator_id": "local-operator",
                    "reason": "authorized local operation",
                },
                "transport": "operator",
                "trace_id": TRACE_ID,
            }
        )
        registry.validate_pydantic(
            application,
            "command.schema.json",
            definition="application",
        )

    public_paths = {route.path for route in identity_app.routes}
    assert not any(
        marker in path.casefold()
        for path in public_paths
        for marker in ("bootstrap", "recover", "operator")
    )


async def test_stepik_oauth_start_issues_fresh_server_side_state(
    identity_client: AsyncClient,
) -> None:
    first = await identity_client.get(f"{API_PREFIX}/v1/auth/stepik/start")
    second = await identity_client.get(f"{API_PREFIX}/v1/auth/stepik/start")

    first_state = _oauth_state(first)
    second_state = _oauth_state(second)
    assert first_state != second_state


async def test_stepik_callback_rejects_unknown_state_with_typed_error(
    identity_client: AsyncClient,
) -> None:
    response = await identity_client.post(
        f"{API_PREFIX}/v1/auth/stepik/callback",
        params={"state": "unknown-state", "code": "fixture-code"},
    )

    _assert_error(response, 401)


async def test_stepik_callback_replay_is_an_idempotent_session_noop(
    identity_client: AsyncClient,
) -> None:
    start = await identity_client.get(f"{API_PREFIX}/v1/auth/stepik/start")
    state = _oauth_state(start)
    callback = {"state": state, "code": "fixture-code"}

    first = await identity_client.post(
        f"{API_PREFIX}/v1/auth/stepik/callback",
        params=callback,
    )
    assert first.status_code == 204
    first_session = identity_client.cookies.get(SESSION_COOKIE)
    assert first_session

    replay = await identity_client.post(
        f"{API_PREFIX}/v1/auth/stepik/callback",
        params=callback,
    )
    assert replay.status_code == 204
    assert identity_client.cookies.get(SESSION_COOKIE) == first_session


async def test_magic_link_uses_typed_identity_assertion_and_rejects_replay(
    identity_app: FastAPI,
) -> None:
    provider = identity_app.state.identity_provider
    assert isinstance(provider, RecordingIdentityProvider)
    request: ProviderPayload = {
        "contract_version": "1.1.0",
        "provider": "email_magic_link",
        "state_id": STATE_ID,
        "credential_binding_id": CREDENTIAL_BINDING_ID,
        "credential_binding_version": 1,
        "authorization_response": MAGIC_LINK_TOKEN,
    }
    assertion = await provider.verify_identity(request)
    assert assertion["provider"] == "email_magic_link"
    assert assertion["state_id"] == STATE_ID
    assert assertion["verified_email"] == "reviewer@example.test"

    transport = ASGITransport(app=identity_app)
    async with AsyncClient(transport=transport, base_url="http://identity.test") as client:
        body = {"token": MAGIC_LINK_TOKEN, "state_id": STATE_ID}
        first = await client.post(f"{API_PREFIX}/v1/auth/reviewer/magic-link", json=body)
        assert first.status_code == 204
        assert client.cookies.get(SESSION_COOKIE)

        replay = await client.post(f"{API_PREFIX}/v1/auth/reviewer/magic-link", json=body)
        _assert_error(replay, 401)


async def test_current_session_response_is_closed_and_typed(
    identity_client: AsyncClient,
) -> None:
    response = await identity_client.get(f"{API_PREFIX}/v1/session")

    assert response.status_code == 200
    payload = response.json()
    _component_validator("Session").validate(payload)
    assert payload["actor_type"] == "user"
    assert payload.get("agent_id") is None
    assert set(payload) <= {
        "user_id",
        "organization_id",
        "membership_id",
        "roles",
        "membership_revision",
        "auth_epoch",
        "actor_type",
        "agent_id",
    }


async def test_logout_is_idempotently_bound_to_the_current_session(
    identity_client: AsyncClient,
) -> None:
    first = await identity_client.delete(f"{API_PREFIX}/v1/session")
    replay = await identity_client.delete(f"{API_PREFIX}/v1/session")

    assert first.status_code == replay.status_code == 204
    assert first.content == replay.content == b""


def test_session_fixture_values_are_valid_contract_identities() -> None:
    # Keep constants used by future server-side session fixtures contract-shaped;
    # no client-supplied organization or actor context is accepted by these tests.
    assert UUID(ORGANIZATION_ID)
    assert UUID(USER_ID)
    assert UUID(MEMBERSHIP_ID)
