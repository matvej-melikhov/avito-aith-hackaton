from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc
from review_platform.infrastructure.auth.agent_tokens import issue_agent_token
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course
from review_platform.infrastructure.db.models.operations import AuditEvent, Operation
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.mcp.server import MCPServerError, create_mcp_app
from review_platform.mcp.tools.read import (
    READ_TOOL_NAMES,
    build_read_tool_registry,
    get_homework_history,
    get_operation,
    get_review_iteration,
    list_courses,
    recommend_next_review,
)

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000183001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000183002")
USER = UUID("00000000-0000-7000-8000-000000183003")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000183004")
AGENT = UUID("00000000-0000-7000-8000-000000183005")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000183006")
COURSE = UUID("00000000-0000-7000-8000-000000183007")
OTHER_COURSE = UUID("00000000-0000-7000-8000-000000183008")
OPERATION = UUID("00000000-0000-7000-8000-000000183009")
OTHER_OPERATION = UUID("00000000-0000-7000-8000-000000183010")
READ_SCOPES = {
    "courses:read",
    "review_queue:read",
    "reviews:read",
    "operations:read",
}


@dataclass(frozen=True, slots=True)
class ReadHarness:
    runtime: FoundationRuntime
    factory: AsyncSessionFactory
    actor: RequestActor
    bearer: str


@pytest.fixture
async def read_harness(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[ReadHarness]:
    expires_at = require_utc(foundation_runtime.clock()) + timedelta(hours=1)
    issued = issue_agent_token(random_bytes=lambda size: b"g" * size)
    async with session_scope(foundation_session_factory) as session:
        session.add_all(
            [
                Organization(id=ORG, slug="mcp-read", name="MCP Read"),
                Organization(id=OTHER_ORG, slug="mcp-read-other", name="MCP Read Other"),
                User(id=USER, display_name="MCP read agent owner", status="active"),
            ]
        )
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP,
                organization_id=ORG,
                user_id=USER,
                roles=["methodologist", "reviewer"],
                status="active",
                revision=3,
                auth_epoch=2,
            )
        )
        await session.flush()
        session.add_all(
            [
                AgentAuthorization(
                    id=AUTHORIZATION,
                    organization_id=ORG,
                    user_id=USER,
                    agent_id=AGENT,
                    scopes=sorted(READ_SCOPES),
                    membership_revision=3,
                    auth_epoch=2,
                    token_digest=issued.token_digest,
                    status="active",
                    expires_at=expires_at,
                    revoked_at=None,
                    revision=4,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Visible course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=1,
                ),
                Course(
                    id=OTHER_COURSE,
                    organization_id=OTHER_ORG,
                    title="Cross-tenant course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                Operation(
                    id=OPERATION,
                    organization_id=ORG,
                    kind="course_import",
                    input_version="course-import:1.1.0",
                    state="succeeded",
                    revision=1,
                    created_at=foundation_runtime.clock(),
                    updated_at=foundation_runtime.clock(),
                    finished_at=foundation_runtime.clock(),
                    error_code=None,
                    sanitized_error=None,
                ),
                Operation(
                    id=OTHER_OPERATION,
                    organization_id=OTHER_ORG,
                    kind="course_import",
                    input_version="course-import:1.1.0",
                    state="succeeded",
                    revision=1,
                    created_at=foundation_runtime.clock(),
                    updated_at=foundation_runtime.clock(),
                    finished_at=foundation_runtime.clock(),
                    error_code=None,
                    sanitized_error=None,
                ),
            ]
        )
    yield ReadHarness(
        runtime=foundation_runtime,
        factory=foundation_session_factory,
        actor=RequestActor.agent(
            organization_id=ORG,
            user_id=USER,
            roles={"methodologist", "reviewer"},
            membership_revision=3,
            auth_epoch=2,
            agent_id=AGENT,
            agent_authorization_id=AUTHORIZATION,
            agent_authorization_revision=4,
            scopes=READ_SCOPES,
            expires_at=expires_at,
        ),
        bearer=issued.access_token.reveal(),
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


async def test_read_registry_is_exact_and_named_handlers_are_concrete() -> None:
    assert {
        "list_courses",
        "recommend_next_review",
        "get_review_iteration",
        "get_operation",
        "get_homework_history",
    } == READ_TOOL_NAMES
    assert all(
        callable(handler)
        for handler in (
            list_courses,
            recommend_next_review,
            get_review_iteration,
            get_operation,
            get_homework_history,
        )
    )


async def test_list_and_operation_reads_are_tenant_exact_typed_and_audited(
    read_harness: ReadHarness,
) -> None:
    monotonic_values = iter((10.0, 10.125, 20.0, 20.005))
    registry = build_read_tool_registry(
        read_harness.runtime,
        monotonic=monotonic_values.__next__,
    )
    assert set(registry) == READ_TOOL_NAMES
    async with session_scope(read_harness.factory) as session:
        courses = await registry["list_courses"](
            actor=read_harness.actor,
            arguments={},
            transaction=session,
        )
        operation = await registry["get_operation"](
            actor=read_harness.actor,
            arguments={"operation_id": str(OPERATION)},
            transaction=session,
        )

    _validate_component("CourseList", courses)
    _validate_component("Operation", operation)
    assert [item["id"] for item in courses["items"]] == [str(COURSE)]
    assert courses["course_runs"] == []
    assert operation["id"] == str(OPERATION)
    async with read_harness.factory() as session:
        events = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == ORG)
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
        ).all()
    assert [event.action for event in events] == ["mcp_tool_call", "mcp_tool_call"]
    assert {event.actor_user_id for event in events} == {USER}
    assert {event.agent_id for event in events} == {AGENT}
    assert {event.agent_authorization_id for event in events} == {AUTHORIZATION}
    assert [event.sanitized_details["tool_name"] for event in events] == [
        "list_courses",
        "get_operation",
    ]
    assert [event.sanitized_details["duration_ms"] for event in events] == [125, 5]
    assert {event.sanitized_details["idempotency_disposition"] for event in events} == {
        "read"
    }
    assert all(event.request_id != event.trace_id for event in events)


async def test_registry_handlers_run_through_t155_and_final_t153_boundary(
    read_harness: ReadHarness,
) -> None:
    app = create_mcp_app(
        runtime=read_harness.runtime,
        tool_registry=build_read_tool_registry(read_harness.runtime),
    )
    headers = {
        "Authorization": f"Bearer {read_harness.bearer}",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "list_courses",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp-read.test",
    ) as client:
        response = await client.post("/mcp", headers=headers, json={})

    assert response.status_code == 200, response.text
    _validate_component("CourseList", response.json())
    assert [item["id"] for item in response.json()["items"]] == [str(COURSE)]


async def test_cross_tenant_ids_and_client_tenant_argument_never_escape_scope(
    read_harness: ReadHarness,
) -> None:
    registry = build_read_tool_registry(read_harness.runtime)
    async with read_harness.factory() as session:
        with pytest.raises(MCPServerError) as missing:
            await registry["get_operation"](
                actor=read_harness.actor,
                arguments={"operation_id": str(OTHER_OPERATION)},
                transaction=session,
            )
        with pytest.raises(MCPServerError) as injected:
            await registry["list_courses"](
                actor=read_harness.actor,
                arguments={"organization_id": str(OTHER_ORG)},
                transaction=session,
            )

    assert (missing.value.status_code, missing.value.code) == (
        404,
        "mcp_resource_not_found",
    )
    assert (injected.value.status_code, injected.value.code) == (
        400,
        "invalid_mcp_arguments",
    )


async def test_each_read_tool_enforces_its_frozen_role_and_scope_before_query(
    read_harness: ReadHarness,
) -> None:
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"methodologist", "reviewer"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=4,
        scopes={"courses:read"},
        expires_at=read_harness.actor.expires_at,
    )
    registry = build_read_tool_registry(read_harness.runtime)
    async with read_harness.factory() as session:
        with pytest.raises(MCPServerError) as denied:
            await registry["get_operation"](
                actor=actor,
                arguments={"operation_id": str(OPERATION)},
                transaction=session,
            )

    assert (denied.value.status_code, denied.value.code, denied.value.action) == (
        403,
        "mcp_scope_denied",
        "request_scope",
    )
