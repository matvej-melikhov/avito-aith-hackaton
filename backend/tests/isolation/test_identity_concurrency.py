"""Executable US1 identity concurrency and revocation specifications.

The current Foundation app deliberately has no identity routes yet.  Each test
therefore checks the canonical route before touching future identity tables, so
the RED reason is missing US1 behavior rather than an import or fixture error.
Once US1 is composed, state is seeded through SQLAlchemy metadata and all calls
still cross the real HTTP/transaction boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import anyio
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from starlette.types import Receive, Scope, Send
from taskiq import TaskiqMessage

from review_platform.application.authorization import AuthorizationDenied
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.request_context import RequestActor, Role
from review_platform.application.services.invitations import InvitationEmailIntent
from review_platform.contracts.registry import ContractRegistry
from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    Invitation,
    OAuthState,
    OrganizationMembership,
    Session,
    User,
)
from review_platform.infrastructure.db.models.operations import CommandReceipt
from review_platform.infrastructure.db.session import AsyncSessionFactory
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
METHOD_A = UUID("00000000-0000-7000-8000-000000000101")
METHOD_B = UUID("00000000-0000-7000-8000-000000000102")
REVIEWER = UUID("00000000-0000-7000-8000-000000000103")
MEMBERSHIP_A = UUID("00000000-0000-7000-8000-000000000111")
MEMBERSHIP_B = UUID("00000000-0000-7000-8000-000000000112")
REVIEWER_MEMBERSHIP = UUID("00000000-0000-7000-8000-000000000113")
INVITATION = UUID("00000000-0000-7000-8000-000000000121")
OAUTH_STATE = UUID("00000000-0000-7000-8000-000000000122")
EMAIL_CREDENTIAL = UUID("00000000-0000-7000-8000-000000000124")
QUEUED_RECEIPT = UUID("00000000-0000-7000-8000-000000000131")
CLAIMED_RECEIPT = UUID("00000000-0000-7000-8000-000000000132")
REVIEWER_SESSION = UUID("00000000-0000-7000-8000-000000000133")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
INVITATION_TOKEN = "offline-invitation-token-0000000000000001"
REVIEWER_SESSION_SECRET = "offline-reviewer-session-secret-000000000001"

MEMBERSHIP_ROLES_PATH = "/api/v1/memberships/{membershipId}/roles"
MAGIC_LINK_PATH = "/api/v1/auth/reviewer/magic-link"
REVOKE_INVITATION_PATH = "/api/v1/invitations/{invitationId}/revoke"
SESSION_PATH = "/api/v1/session"

_ASSERTION: ContextVar[Mapping[str, object] | None] = ContextVar(
    "identity_concurrency_assertion",
    default=None,
)


class _StateInjectedApp:
    """Inject server-owned dependencies into ASGI scope, never HTTP headers."""

    def __init__(self, app: FastAPI, state: Mapping[str, object]) -> None:
        self._app = app
        self._state = state

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "http":
            scope = dict(scope)
            request_state = dict(scope.get("state", {}))
            request_state.update(self._state)
            scope["state"] = request_state
        assertion = self._state.get("identity_assertion")
        token = _ASSERTION.set(cast(Mapping[str, object], assertion)) if assertion else None
        try:
            await self._app(scope, receive, send)
        finally:
            if token is not None:
                _ASSERTION.reset(token)


class _DynamicIdentityProvider:
    contract_version = "1.1.0"
    schema_name = "identity-provider.schema.json"

    def __init__(self) -> None:
        self._registry = ContractRegistry()

    async def verify_identity(self, request: ProviderPayload) -> ProviderPayload:
        self._registry.validate(
            request,
            self.schema_name,
            definition="verification_request",
        )
        injected = _ASSERTION.get()
        if injected is None:
            raise RuntimeError("test request did not inject an identity assertion")
        assertion = dict(injected)
        assertion["provider"] = request["provider"]
        assertion["state_id"] = request["state_id"]
        self._registry.validate(
            assertion,
            self.schema_name,
            definition="identity_assertion",
        )
        return cast(ProviderPayload, assertion)


class _UnusedEmailIntents:
    async def enqueue(self, intent: InvitationEmailIntent, *, transaction: object) -> None:
        del intent, transaction
        raise AssertionError("revoke path must not enqueue another invitation email")


class _TestSecretProtector:
    def seal(self, magic_link: str) -> str:
        return "sealed:" + sha256_digest(magic_link)


@dataclass(frozen=True, slots=True)
class _StaticIdentityAssertion:
    verified_email: str

    @property
    def payload(self) -> dict[str, object]:
        return {
            "contract_version": "1.1.0",
            "provider": "email_magic_link",
            "issuer": "https://review.example.test",
            "subject": "reviewer-fixture",
            "verified_email": self.verified_email,
            "issued_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "state_id": str(OAUTH_STATE),
        }


def _actor(
    *,
    user_id: UUID,
    roles: set[Role],
    membership_revision: int = 0,
    auth_epoch: int = 0,
) -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=user_id,
        roles=roles,
        membership_revision=membership_revision,
        auth_epoch=auth_epoch,
    )


def _app(foundation_runtime: FoundationRuntime) -> FastAPI:
    app = create_app(runtime=foundation_runtime)
    app.state.installation_organization_id = ORG
    app.state.identity_provider = _DynamicIdentityProvider()
    app.state.invitation_email_intents = _UnusedEmailIntents()
    app.state.invitation_secret_protector = _TestSecretProtector()
    app.state.reviewer_magic_link_base_url = "https://review.example.test/invite"
    return app


def _require_route(app: FastAPI, *, path: str, method: str) -> None:
    available = {
        (route.path, candidate)
        for route in app.routes
        for candidate in (getattr(route, "methods", None) or set())
    }
    assert (path, method.upper()) in available, (
        f"missing required identity route: {method.upper()} {path}"
    )


@asynccontextmanager
async def _client(
    app: FastAPI,
    *,
    actor: RequestActor | None = None,
    identity_assertion: Mapping[str, object] | None = None,
) -> AsyncIterator[AsyncClient]:
    state: dict[str, object] = {}
    if actor is not None:
        state["request_actor"] = actor
    if identity_assertion is not None:
        state["identity_assertion"] = dict(identity_assertion)
        state["identity_provider_result"] = dict(identity_assertion)
    transport = ASGITransport(app=_StateInjectedApp(app, state))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _seed_users_and_memberships(
    factory: AsyncSessionFactory,
    *,
    include_reviewer: bool = False,
) -> None:
    async with factory.begin() as session:
        for user_id, name in ((METHOD_A, "Method A"), (METHOD_B, "Method B")):
            session.add(
                User(
                    id=user_id,
                    display_name=name,
                    status="active",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.flush()
        for membership_id, user_id in (
            (MEMBERSHIP_A, METHOD_A),
            (MEMBERSHIP_B, METHOD_B),
        ):
            session.add(
                OrganizationMembership(
                    id=membership_id,
                    user_id=user_id,
                    roles=["methodologist"],
                    auth_epoch=0,
                    **_membership_values(),
                )
            )
        if include_reviewer:
            session.add(
                User(
                    id=REVIEWER,
                    display_name="Reviewer",
                    status="active",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await session.flush()
            session.add(
                OrganizationMembership(
                    id=REVIEWER_MEMBERSHIP,
                    user_id=REVIEWER,
                    roles=["reviewer"],
                    auth_epoch=7,
                    **_membership_values(),
                )
            )
            session.add(
                Session(
                    id=REVIEWER_SESSION,
                    organization_id=ORG,
                    user_id=REVIEWER,
                    membership_id=REVIEWER_MEMBERSHIP,
                    membership_revision=0,
                    auth_epoch=7,
                    token_digest=sha256_digest(REVIEWER_SESSION_SECRET),
                    expires_at=NOW + timedelta(hours=1),
                    revoked_at=None,
                    status="active",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )


def _membership_values() -> dict[str, object]:
    return {
        "organization_id": ORG,
        "status": "active",
        "revision": 0,
        "revoked_at": None,
        "revoked_by": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


async def _seed_invitation(factory: AsyncSessionFactory, *, email: str) -> None:
    async with factory.begin() as session:
        session.add(
            ExternalCredential(
                id=EMAIL_CREDENTIAL,
                organization_id=ORG,
                provider="email_magic_link",
                binding_version=1,
                ciphertext="offline-encrypted-fixture",
                key_id="offline-test-key",
                status="active",
                created_at=NOW,
                updated_at=NOW,
                rotated_at=None,
                revoked_at=None,
            )
        )
        await session.flush()
        session.add(
            Invitation(
                id=INVITATION,
                organization_id=ORG,
                role="reviewer",
                normalized_email=email.casefold(),
                token_digest=sha256_digest(INVITATION_TOKEN),
                expires_at=NOW + timedelta(hours=1),
                issued_by=METHOD_A,
                consumed_by=None,
                consumed_at=None,
                revoked_at=None,
                status="active",
                revision=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            OAuthState(
                state_id=OAUTH_STATE,
                organization_id=ORG,
                state_digest=sha256_digest(str(OAUTH_STATE)),
                provider="email_magic_link",
                credential_binding_id=EMAIL_CREDENTIAL,
                credential_binding_version=1,
                redirect_uri="https://review.example.test/auth/callback",
                pkce_verifier_ciphertext="offline-fixture-verifier",
                expires_at=NOW + timedelta(minutes=10),
                consumed_at=None,
                resulting_session_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def _change_roles_command(
    *, membership_id: UUID, roles: list[str], request: int
) -> dict[str, object]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request:012d}",
        "idempotency_key": f"membership-change-{request:08d}",
        "command_name": "change_membership_roles",
        "revision_target": "membership",
        "target_id": str(membership_id),
        "expected_revision": 0,
        "payload": {"roles": roles},
    }


def _revoke_invitation_command(*, request: int) -> dict[str, object]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request:012d}",
        "idempotency_key": f"invitation-revoke-{request:08d}",
        "command_name": "revoke_invitation",
        "revision_target": "invitation",
        "target_id": str(INVITATION),
        "expected_revision": 0,
        "payload": {"reason": "access no longer required"},
    }


async def _membership_rows(factory: AsyncSessionFactory) -> list[Mapping[str, object]]:
    async with factory() as session:
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == ORG
            )
        )
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "roles": list(row.roles),
                "status": row.status,
                "revision": row.revision,
                "auth_epoch": row.auth_epoch,
            }
            for row in result.scalars()
        ]


async def _invitation_row(factory: AsyncSessionFactory) -> Mapping[str, object]:
    async with factory() as session:
        row = await session.scalar(
            select(Invitation).where(
                Invitation.organization_id == ORG,
                Invitation.id == INVITATION,
            )
        )
        assert row is not None
        return {
            "id": row.id,
            "status": row.status,
            "revision": row.revision,
            "consumed_by": row.consumed_by,
        }


async def _session_count(factory: AsyncSessionFactory) -> int:
    async with factory() as session:
        result = await session.scalars(select(Session).where(Session.organization_id == ORG))
        return len(result.all())


async def _reviewer_session_status(factory: AsyncSessionFactory) -> str:
    async with factory() as session:
        row = await session.get(Session, REVIEWER_SESSION)
        assert row is not None
        return row.status


async def test_concurrent_role_removals_cannot_remove_the_last_methodologist(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    app = _app(foundation_runtime)
    _require_route(app, path=MEMBERSHIP_ROLES_PATH, method="PUT")
    await _seed_users_and_memberships(foundation_session_factory)

    responses: list[Response] = []

    async def remove_other(actor: RequestActor, target: UUID, request: int) -> None:
        async with _client(app, actor=actor) as client:
            responses.append(
                await client.put(
                    MEMBERSHIP_ROLES_PATH.replace("{membershipId}", str(target)),
                    json=_change_roles_command(
                        membership_id=target,
                        roles=[],
                        request=request,
                    ),
                )
            )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(
            remove_other,
            _actor(user_id=METHOD_A, roles={"methodologist"}),
            MEMBERSHIP_B,
            201,
        )
        tasks.start_soon(
            remove_other,
            _actor(user_id=METHOD_B, roles={"methodologist"}),
            MEMBERSHIP_A,
            202,
        )

    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in {200, 401, 403, 409} for response in responses)
    memberships = await _membership_rows(foundation_session_factory)
    active_methodologists = [
        row
        for row in memberships
        if row["status"] == "active" and "methodologist" in (row["roles"] or [])
    ]
    assert len(active_methodologists) == 1


async def test_magic_link_rejects_mismatched_verified_email_without_granting_role(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    app = _app(foundation_runtime)
    _require_route(app, path=MAGIC_LINK_PATH, method="POST")
    await _seed_users_and_memberships(foundation_session_factory)
    await _seed_invitation(foundation_session_factory, email="invited@example.com")

    assertion = _StaticIdentityAssertion("different@example.com")
    async with _client(app, identity_assertion=assertion.payload) as client:
        response = await client.post(
            MAGIC_LINK_PATH,
            json={"token": INVITATION_TOKEN, "state_id": str(OAUTH_STATE)},
        )

    assert response.status_code == 401
    assert (await _invitation_row(foundation_session_factory))["status"] == "active"
    assert len(await _membership_rows(foundation_session_factory)) == 2
    assert await _session_count(foundation_session_factory) == 0


async def test_invitation_token_and_state_double_consume_create_one_membership_and_session(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    app = _app(foundation_runtime)
    _require_route(app, path=MAGIC_LINK_PATH, method="POST")
    await _seed_users_and_memberships(foundation_session_factory)
    await _seed_invitation(foundation_session_factory, email="reviewer@example.com")
    assertion = _StaticIdentityAssertion("reviewer@example.com")
    responses: list[Response] = []

    async def consume() -> None:
        async with _client(app, identity_assertion=assertion.payload) as client:
            responses.append(
                await client.post(
                    MAGIC_LINK_PATH,
                    json={"token": INVITATION_TOKEN, "state_id": str(OAUTH_STATE)},
                )
            )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        tasks.start_soon(consume)

    assert all(response.status_code in {204, 401} for response in responses)
    assert any(response.status_code == 204 for response in responses)
    assert (await _invitation_row(foundation_session_factory))["status"] == "consumed"
    reviewer_memberships = [
        row
        for row in await _membership_rows(foundation_session_factory)
        if row["user_id"] not in {METHOD_A, METHOD_B}
    ]
    assert len(reviewer_memberships) == 1
    assert await _session_count(foundation_session_factory) == 1


async def test_invitation_consume_vs_revoke_has_one_linearizable_winner(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    app = _app(foundation_runtime)
    _require_route(app, path=MAGIC_LINK_PATH, method="POST")
    _require_route(app, path=REVOKE_INVITATION_PATH, method="POST")
    await _seed_users_and_memberships(foundation_session_factory)
    await _seed_invitation(foundation_session_factory, email="reviewer@example.com")
    assertion = _StaticIdentityAssertion("reviewer@example.com")
    results: dict[str, Response] = {}

    async def consume() -> None:
        async with _client(app, identity_assertion=assertion.payload) as client:
            results["consume"] = await client.post(
                MAGIC_LINK_PATH,
                json={"token": INVITATION_TOKEN, "state_id": str(OAUTH_STATE)},
            )

    async def revoke() -> None:
        async with _client(
            app,
            actor=_actor(user_id=METHOD_A, roles={"methodologist"}),
        ) as client:
            results["revoke"] = await client.post(
                REVOKE_INVITATION_PATH.replace("{invitationId}", str(INVITATION)),
                json=_revoke_invitation_command(request=301),
            )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        tasks.start_soon(revoke)

    pair = (results["consume"].status_code, results["revoke"].status_code)
    assert pair in {(204, 409), (401, 204)}
    invitation = await _invitation_row(foundation_session_factory)
    assert invitation["status"] in {"consumed", "revoked"}
    created = [
        row
        for row in await _membership_rows(foundation_session_factory)
        if row["user_id"] not in {METHOD_A, METHOD_B}
    ]
    assert (len(created), await _session_count(foundation_session_factory)) == (
        (1, 1) if invitation["status"] == "consumed" else (0, 0)
    )


async def _seed_revocation_receipts(factory: AsyncSessionFactory) -> None:
    actor_snapshot = {
        "type": "user",
        "user_id": str(REVIEWER),
        "membership_revision": 0,
        "auth_epoch": 7,
        "roles": ["reviewer"],
    }
    async with factory.begin() as session:
        for receipt_id, status, suffix in (
            (QUEUED_RECEIPT, "reserved", 401),
            (CLAIMED_RECEIPT, "processing", 402),
        ):
            session.add(
                CommandReceipt(
                    id=receipt_id,
                    organization_id=ORG,
                    idempotency_key=f"revocation-race-{suffix:08d}",
                    request_id=UUID(f"00000000-0000-7000-8000-{suffix:012d}"),
                    command_name="set_reviewer_availability",
                    target_id=REVIEWER_MEMBERSHIP,
                    expected_revision=0,
                    payload_digest="sha256:" + "4" * 64,
                    actor_snapshot=actor_snapshot,
                    status=status,
                    result_reference=None,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )


async def _receipt_statuses(factory: AsyncSessionFactory) -> dict[UUID, str]:
    async with factory() as session:
        result = await session.scalars(
            select(CommandReceipt).where(
                CommandReceipt.organization_id == ORG,
                CommandReceipt.id.in_((QUEUED_RECEIPT, CLAIMED_RECEIPT)),
            )
        )
        return {row.id: row.status for row in result}


async def test_membership_revocation_invalidates_rest_queued_and_claimed_work(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    app = _app(foundation_runtime)
    _require_route(app, path=MEMBERSHIP_ROLES_PATH, method="PUT")
    _require_route(app, path=SESSION_PATH, method="GET")
    revalidator = getattr(app.state, "worker_auth_revalidator", None) or getattr(
        foundation_runtime, "worker_auth_revalidator", None
    )
    assert revalidator is not None, "missing worker auth_epoch revalidation hook"

    await _seed_users_and_memberships(foundation_session_factory, include_reviewer=True)
    await _seed_revocation_receipts(foundation_session_factory)
    stale_reviewer = _actor(
        user_id=REVIEWER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=7,
    )
    claimed_message = TaskiqMessage(
        task_id=str(CLAIMED_RECEIPT),
        task_name="fixture.claimed_review_job",
        labels={
            "organization_id": str(ORG),
            "message_id": str(CLAIMED_RECEIPT),
            "task_kind": "course_import",
            "requires_auth_revalidation": True,
            "actor": {
                "type": "user",
                "user_id": str(REVIEWER),
                "membership_revision": 0,
                "auth_epoch": 7,
                "roles": ["reviewer"],
            },
        },
        args=[],
        kwargs={},
    )
    claimed = anyio.Event()
    revoked = anyio.Event()
    commit_outcome: list[str] = []
    revoke_response: list[Response] = []

    async def claimed_job() -> None:
        await revalidator.revalidate(claimed_message)
        claimed.set()
        await revoked.wait()
        try:
            await revalidator.revalidate(claimed_message)
        except AuthorizationDenied:
            commit_outcome.append("rejected_stale_auth_epoch")
        else:
            commit_outcome.append("committed")

    async def revoke_membership() -> None:
        await claimed.wait()
        async with _client(
            app,
            actor=_actor(user_id=METHOD_A, roles={"methodologist"}),
        ) as client:
            revoke_response.append(
                await client.put(
                    MEMBERSHIP_ROLES_PATH.replace(
                        "{membershipId}", str(REVIEWER_MEMBERSHIP)
                    ),
                    json=_change_roles_command(
                        membership_id=REVIEWER_MEMBERSHIP,
                        roles=[],
                        request=403,
                    ),
                )
            )
        revoked.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(claimed_job)
        tasks.start_soon(revoke_membership)

    assert revoke_response[0].status_code == 200
    assert commit_outcome == ["rejected_stale_auth_epoch"]
    assert await _reviewer_session_status(foundation_session_factory) == "revoked"

    async with _client(app, actor=stale_reviewer) as client:
        client.cookies.set("review_session", REVIEWER_SESSION_SECRET, path="/api")
        rest_after_revoke = await client.get(SESSION_PATH)
    assert rest_after_revoke.status_code in {401, 403}

    statuses = await _receipt_statuses(foundation_session_factory)
    assert statuses[QUEUED_RECEIPT] == "invalidated"
    assert statuses[CLAIMED_RECEIPT] != "succeeded"
