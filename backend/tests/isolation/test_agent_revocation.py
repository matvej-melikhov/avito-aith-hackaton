"""US7 RED isolation contract for AgentAuthorization revoke-versus-commit races."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import anyio
import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import TaskiqMessage

from review_platform.application.authorization import (
    AuthorizationDenied,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    AuditEvent,
    AvailabilityPlan,
    CommandReceipt,
    Course,
    CourseRun,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.tasks.broker import MembershipWorkerAuthRevalidator
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
USER = UUID("00000000-0000-7000-8000-000000148001")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000148002")
AGENT = UUID("00000000-0000-7000-8000-000000148003")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000148004")
COURSE = UUID("00000000-0000-7000-8000-000000148005")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000148006")
PLAN = UUID("00000000-0000-7000-8000-000000148007")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


@dataclass(slots=True)
class AgentRaceHarness:
    app: FastAPI
    client: httpx.AsyncClient
    runtime: FoundationRuntime
    factory: AsyncSessionFactory
    actor: RequestActor


@pytest.fixture
async def agent_race_harness(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[AgentRaceHarness]:
    async with session_scope(foundation_session_factory) as session:
        session.add(User(id=USER, display_name="Agent represented reviewer", status="active"))
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=USER,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Agent race course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                AgentAuthorization(
                    id=AUTHORIZATION,
                    organization_id=ORG,
                    user_id=USER,
                    agent_id=AGENT,
                    scopes=[
                        "courses:read",
                        "review_preferences:write",
                        "review_queue:read",
                        "reviews:read",
                        "reviews:write",
                    ],
                    membership_revision=0,
                    auth_epoch=0,
                    token_digest="sha256:" + "8" * 64,
                    status="active",
                    expires_at=NOW + timedelta(days=1),
                    revision=0,
                ),
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Agent race run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
            ]
        )
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=0,
        scopes={
            "courses:read",
            "review_preferences:write",
            "review_queue:read",
            "reviews:read",
            "reviews:write",
        },
        expires_at=NOW + timedelta(days=1),
    )
    app = create_app(runtime=foundation_runtime)

    @app.middleware("http")
    async def inject_agent(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield AgentRaceHarness(
            app,
            client,
            foundation_runtime,
            foundation_session_factory,
            actor,
        )


async def _revoke(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        result = await session.execute(
            update(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.id == AUTHORIZATION,
                AgentAuthorization.revision == 0,
                AgentAuthorization.status == "active",
            )
            .values(status="revoked", revision=1, revoked_at=NOW)
        )
        assert result.rowcount == 1


async def _wait_then_revoke(
    started: anyio.Event,
    revoked: anyio.Event,
    factory: AsyncSessionFactory,
) -> None:
    with anyio.move_on_after(1):
        await started.wait()
    await _revoke(factory)
    revoked.set()


async def _effect_counts(factory: AsyncSessionFactory) -> tuple[int, int, int]:
    async with factory() as session:
        plan_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AvailabilityPlan)
                .where(AvailabilityPlan.organization_id == ORG)
            )
            or 0
        )
        audit_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.organization_id == ORG)
            )
            or 0
        )
        receipt_count = int(
            await session.scalar(
                select(func.count())
                .select_from(CommandReceipt)
                .where(CommandReceipt.organization_id == ORG)
            )
            or 0
        )
        return plan_count, audit_count, receipt_count


def _availability_command() -> dict[str, Any]:
    return {
        "request_id": "00000000-0000-7000-8000-000000148010",
        "idempotency_key": "agent-revocation-rest-write-0001",
        "command_name": "set_reviewer_availability",
        "revision_target": "membership",
        "target_id": str(MEMBERSHIP),
        "expected_revision": 0,
        "payload": {
            "planned_minutes": 60,
            "until_at": (NOW + timedelta(days=1)).isoformat(),
        },
    }


async def test_rest_write_revoked_before_commit_rolls_back_domain_audit_and_receipt(
    agent_race_harness: AgentRaceHarness,
) -> None:
    started = anyio.Event()
    revoked = anyio.Event()

    async def write_handler(
        *,
        actor: RequestActor,
        command: object,
        transaction: object,
    ) -> Mapping[str, object]:
        del command
        assert isinstance(transaction, AsyncSession)
        transaction.add(
            AvailabilityPlan(
                id=PLAN,
                organization_id=actor.organization_id,
                reviewer_id=cast(UUID, actor.user_id),
                planned_minutes=60,
                until_at=NOW + timedelta(days=1),
                revision=0,
            )
        )
        transaction.add(
            _audit(
                UUID("00000000-0000-7000-8000-000000148011"),
                "agent_rest_write",
            )
        )
        await transaction.flush()
        started.set()
        await revoked.wait()
        return {"id": str(PLAN), "revision": 0}

    agent_race_harness.app.state.review_route_handlers = {
        "set_reviewer_availability": write_handler,
    }
    responses: list[httpx.Response] = []
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(
            _put,
            agent_race_harness.client,
            "/api/v1/reviewer/availability",
            _availability_command(),
            responses,
        )
        tasks.start_soon(
            _wait_then_revoke,
            started,
            revoked,
            agent_race_harness.factory,
        )

    assert len(responses) == 1
    assert responses[0].status_code in {401, 403}
    assert await _effect_counts(agent_race_harness.factory) == (0, 0, 0)
    assert started.is_set(), (
        "T153 combined guard rejected the active agent before the revoke-vs-commit race"
    )


async def test_rest_read_revalidates_after_concurrent_agent_revocation(
    agent_race_harness: AgentRaceHarness,
) -> None:
    started = anyio.Event()
    revoked = anyio.Event()

    async def read_handler(
        *,
        actor: RequestActor,
        identity: UUID | None,
        transaction: object,
    ) -> Mapping[str, object]:
        del transaction
        assert actor.agent_authorization_id == AUTHORIZATION
        assert identity == COURSE_RUN
        started.set()
        await revoked.wait()
        return {
            "review_case_id": "00000000-0000-7000-8000-000000148012",
            "review_case_revision": 0,
            "submission_version_id": "00000000-0000-7000-8000-000000148013",
            "reason": ["should-not-leak-after-revoke"],
        }

    agent_race_harness.app.state.review_read_handlers = {
        "recommend_next_review": read_handler,
    }
    responses: list[httpx.Response] = []
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(
            _get,
            agent_race_harness.client,
            f"/api/v1/review-queue/next?course_run_id={COURSE_RUN}",
            responses,
        )
        tasks.start_soon(
            _wait_then_revoke,
            started,
            revoked,
            agent_race_harness.factory,
        )

    assert started.is_set(), "T153 active agent read did not reach the application boundary"
    assert len(responses) == 1
    assert responses[0].status_code in {401, 403}
    assert "should-not-leak-after-revoke" not in responses[0].text


@pytest.mark.parametrize("mode", ["read", "write"])
async def test_mcp_application_boundary_rechecks_combined_authority(
    agent_race_harness: AgentRaceHarness,
    mode: str,
) -> None:
    started = anyio.Event()
    revoked = anyio.Event()
    outcomes: list[str] = []
    policy = AuthorizationPolicy(
        required_roles=frozenset({"reviewer"}),
        required_scopes=frozenset({"reviews:read" if mode == "read" else "reviews:write"}),
    )

    async def mcp_call() -> None:
        authorizer = Authorizer(
            agent_race_harness.runtime.user_auth_guard,
            clock=lambda: NOW,
        )
        try:
            grant = await authorizer.authorize(
                actor=agent_race_harness.actor,
                organization_id=ORG,
                policy=policy,
            )
            started.set()
            await revoked.wait()
            if mode == "read":
                await agent_race_harness.runtime.user_auth_guard.revalidate(
                    actor=agent_race_harness.actor
                )
            else:
                async with agent_race_harness.runtime.transaction() as transaction:
                    transaction.add(
                        _audit(
                            UUID("00000000-0000-7000-8000-000000148014"),
                            "agent_mcp_write",
                        )
                    )
                    await transaction.flush()
                    await authorizer.revalidate_for_commit(
                        grant,
                        transaction=transaction,
                    )
        except AuthorizationDenied:
            outcomes.append("rejected")
        else:
            outcomes.append("committed")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(mcp_call)
        tasks.start_soon(
            _wait_then_revoke,
            started,
            revoked,
            agent_race_harness.factory,
        )

    assert await _effect_counts(agent_race_harness.factory) == (0, 0, 0)
    assert started.is_set(), f"T153 active agent MCP {mode} was rejected before the race"
    assert outcomes == ["rejected"]


async def test_queued_and_claimed_agent_jobs_revalidate_authorization_epoch(
    agent_race_harness: AgentRaceHarness,
) -> None:
    revalidator = cast(
        MembershipWorkerAuthRevalidator,
        agent_race_harness.runtime.worker_auth_revalidator,
    )
    queued = _agent_message("queued", UUID("00000000-0000-7000-8000-000000148020"))
    claimed = _agent_message(
        "claimed",
        UUID("00000000-0000-7000-8000-000000148021"),
    )
    active_checks: list[str] = []
    for name, message in (("queued", queued), ("claimed", claimed)):
        try:
            await revalidator.revalidate(message)
        except AuthorizationDenied:
            active_checks.append(f"{name}:rejected")
        else:
            active_checks.append(f"{name}:active")

    await _revoke(agent_race_harness.factory)
    stale_checks: list[str] = []
    for name, message in (("queued", queued), ("claimed", claimed)):
        try:
            await revalidator.revalidate(message)
        except AuthorizationDenied:
            stale_checks.append(f"{name}:rejected")
        else:
            stale_checks.append(f"{name}:committed")

    assert await _effect_counts(agent_race_harness.factory) == (0, 0, 0)
    assert active_checks == ["queued:active", "claimed:active"], (
        "T153 worker revalidation cannot resolve an active agent snapshot"
    )
    assert stale_checks == ["queued:rejected", "claimed:rejected"]


async def test_agent_authorization_never_crosses_tenant_boundary(
    agent_race_harness: AgentRaceHarness,
) -> None:
    other_tenant_actor = RequestActor.agent(
        organization_id=OTHER_ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=0,
        scopes={"reviews:read"},
        expires_at=NOW + timedelta(days=1),
    )
    with pytest.raises(AuthorizationDenied):
        await agent_race_harness.runtime.user_auth_guard.revalidate(actor=other_tenant_actor)
    assert await _effect_counts(agent_race_harness.factory) == (0, 0, 0)


def _audit(identity: UUID, action: str) -> AuditEvent:
    return AuditEvent(
        id=identity,
        organization_id=ORG,
        actor_type="agent",
        actor_user_id=USER,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        action=action,
        entity_type="availability_plan",
        entity_id=PLAN,
        request_id=UUID("00000000-0000-7000-8000-000000148030"),
        trace_id=UUID("00000000-0000-7000-8000-000000148031"),
        outcome="succeeded",
        sanitized_details={},
        occurred_at=NOW,
    )


def _agent_message(state: str, identity: UUID) -> TaskiqMessage:
    return TaskiqMessage(
        task_id=str(identity),
        task_name=f"fixture.agent_{state}_job",
        labels={
            "organization_id": str(ORG),
            "message_id": str(identity),
            "task_kind": "ai_review",
            "requires_auth_revalidation": True,
            "actor": {
                "type": "agent",
                "user_id": str(USER),
                "membership_revision": 0,
                "auth_epoch": 0,
                "roles": ["reviewer"],
                "agent_id": str(AGENT),
                "agent_authorization_id": str(AUTHORIZATION),
                "agent_authorization_revision": 0,
                "scopes": ["reviews:write"],
                "expires_at": (NOW + timedelta(days=1)).isoformat(),
            },
        },
        args=[],
        kwargs={},
    )


async def _put(
    client: httpx.AsyncClient,
    path: str,
    body: Mapping[str, Any],
    responses: list[httpx.Response],
) -> None:
    responses.append(await client.put(path, json=dict(body)))


async def _get(
    client: httpx.AsyncClient,
    path: str,
    responses: list[httpx.Response],
) -> None:
    responses.append(await client.get(path))
