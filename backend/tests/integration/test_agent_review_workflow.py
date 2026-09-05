"""US7 RED acceptance: scoped MCP review work and human-only publication."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import WireCommand
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    ArtifactReference,
    ArtifactVersion,
    AuditEvent,
    AvailabilityPlan,
    Course,
    CourseMembership,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    ExternalCredential,
    ExternalDelivery,
    Homework,
    HomeworkVersion,
    OrganizationMembership,
    PublicationRequest,
    ReviewCase,
    ReviewerCourseSelection,
    ReviewPublication,
    ReviewRevision,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
REVIEWER = UUID("00000000-0000-7000-8000-000000019001")
REVIEWER_MEMBERSHIP = UUID("00000000-0000-7000-8000-000000019002")
STUDENT = UUID("00000000-0000-7000-8000-000000019003")
STUDENT_MEMBERSHIP = UUID("00000000-0000-7000-8000-000000019004")
AGENT = UUID("00000000-0000-7000-8000-000000019005")
COURSE = UUID("00000000-0000-7000-8000-000000019006")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000019007")
HOMEWORK = UUID("00000000-0000-7000-8000-000000019008")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000019009")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000019010")
CRITERION = UUID("00000000-0000-7000-8000-000000019011")
RUN_HOMEWORK = UUID("00000000-0000-7000-8000-000000019012")
HOMEWORK_PUBLICATION = UUID("00000000-0000-7000-8000-000000019013")
GITHUB_CREDENTIAL = UUID("00000000-0000-7000-8000-000000019014")
AI_CREDENTIAL = UUID("00000000-0000-7000-8000-000000019015")
ARTIFACT_REFERENCE = UUID("00000000-0000-7000-8000-000000019016")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000019017")
SUBMISSION = UUID("00000000-0000-7000-8000-000000019018")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000019019")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000019020")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

ALL_AGENT_SCOPES = [
    "courses:read",
    "review_preferences:write",
    "review_queue:read",
    "reviews:read",
    "reviews:write",
    "ai_reviews:start",
    "operations:read",
    "publication_requests:write",
]


@dataclass(slots=True)
class AgentWorkflowHarness:
    app: FastAPI
    client: AsyncClient
    session_factory: AsyncSessionFactory


@pytest.fixture
async def agent_workflow(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[AgentWorkflowHarness]:
    await _seed_review_candidate(foundation_session_factory)
    app = create_app(runtime=foundation_runtime)
    app.state.ai_review_credential_binding_id = AI_CREDENTIAL
    app.state.ai_review_credential_binding_version = 1
    human = RequestActor.user(
        organization_id=ORG,
        user_id=REVIEWER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def inject_interactive_human(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.headers.get("x-test-interactive-actor") == "reviewer":
            request.state.request_actor = human
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield AgentWorkflowHarness(app, client, foundation_session_factory)


async def test_agent_reviews_over_mcp_requests_publication_then_human_publishes(
    agent_workflow: AgentWorkflowHarness,
) -> None:
    token, authorization_id = await _grant(agent_workflow.client)
    replay = await agent_workflow.client.post(
        f"/api/v1/memberships/{REVIEWER_MEMBERSHIP}/agent-authorizations",
        headers=_human_headers(),
        json=_grant_command(),
    )
    assert replay.status_code in {200, 201, 409}, replay.text
    if replay.status_code < 400:
        assert "access_token" not in replay.json(), (
            "agent bearer secret must be returned exactly once"
        )

    courses = await _mcp(agent_workflow.client, token, "list_courses", {})
    assert any(item["id"] == str(COURSE) for item in courses["items"])
    assert any(item["id"] == str(COURSE_RUN) for item in courses["course_runs"])

    recommendation = await _mcp(
        agent_workflow.client,
        token,
        "recommend_next_review",
        {"course_run_id": str(COURSE_RUN)},
    )
    assert recommendation["review_case_id"] == str(REVIEW_CASE)
    assert recommendation["submission_version_id"] == str(SUBMISSION_VERSION)

    opened = await _mcp(
        agent_workflow.client,
        token,
        "open_review_iteration",
        _command(
            request=21,
            idempotency="agent-open-review-0001",
            name="open_review_iteration",
            revision_target="review_case",
            target=REVIEW_CASE,
            expected_revision=int(recommendation["review_case_revision"]),
            payload={"submission_version_id": str(SUBMISSION_VERSION)},
        ),
    )
    iteration_id = UUID(str(opened["review_iteration_id"]))

    detail = await _mcp(
        agent_workflow.client,
        token,
        "get_review_iteration",
        {"review_iteration_id": str(iteration_id)},
    )
    assert detail["immutable_inputs"]["submission_version_id"] == str(
        SUBMISSION_VERSION
    )

    responsibility = await _mcp(
        agent_workflow.client,
        token,
        "record_review_responsibility",
        _command(
            request=22,
            idempotency="agent-responsibility-0001",
            name="record_review_responsibility",
            revision_target="review_iteration",
            target=iteration_id,
            expected_revision=int(detail["revision"]),
            payload={"action": "started"},
        ),
    )
    assert responsibility["ok"] is True

    saved = await _mcp(
        agent_workflow.client,
        token,
        "save_review_revision",
        _command(
            request=23,
            idempotency="agent-save-review-0001",
            name="save_review_revision",
            revision_target="review_iteration",
            target=iteration_id,
            expected_revision=int(detail["revision"]),
            payload={
                "feedback": "Agent-prepared draft; publication remains a human decision.",
                "criterion_decisions": [
                    {
                        "criterion_id": str(CRITERION),
                        "points": 5,
                        "decision": "accepted",
                        "reason": "The captured artifact satisfies the exact criterion.",
                        "evidence_ids": [],
                    }
                ],
                "review_notes": [
                    {"criterion_id": None, "text": "Prepared through scoped MCP."}
                ],
            },
        ),
    )
    review_revision_id = UUID(str(saved["review_revision_id"]))
    iteration_revision = int(saved["review_iteration_revision"])

    ai_operation = await _mcp(
        agent_workflow.client,
        token,
        "start_ai_review",
        _command(
            request=24,
            idempotency="agent-start-ai-review-0001",
            name="start_ai_review",
            revision_target="review_iteration",
            target=iteration_id,
            expected_revision=iteration_revision,
            payload={},
        ),
    )
    assert ai_operation["kind"] == "ai_review"
    assert ai_operation["state"] in {"pending", "processing", "succeeded"}

    publication_request = await _mcp(
        agent_workflow.client,
        token,
        "request_review_publication",
        _command(
            request=25,
            idempotency="agent-publication-request-0001",
            name="request_review_publication",
            revision_target="review_iteration",
            target=iteration_id,
            expected_revision=iteration_revision,
            payload={
                "review_revision_id": str(review_revision_id),
                "expires_at": (NOW + timedelta(days=1)).isoformat(),
            },
        ),
    )
    publication_request_id = UUID(str(publication_request["id"]))
    assert publication_request["status"] == "pending"
    await _assert_not_published(
        agent_workflow.session_factory,
        iteration_id,
        publication_request_id,
    )

    direct = await _raw_mcp(
        agent_workflow.client,
        token,
        "publish_review",
        _command(
            request=26,
            idempotency="agent-direct-publication-0001",
            name="publish_review",
            revision_target="review_iteration",
            target=iteration_id,
            expected_revision=iteration_revision,
            payload={
                "review_revision_id": str(review_revision_id),
                "publication_request_id": str(publication_request_id),
            },
        ),
    )
    assert direct.status_code in {400, 403, 404}, direct.text
    await _assert_not_published(
        agent_workflow.session_factory,
        iteration_id,
        publication_request_id,
    )

    published = await agent_workflow.client.post(
        f"/api/v1/review-iterations/{iteration_id}/publish",
        headers=_human_headers(),
        json=_command(
            request=27,
            idempotency="human-confirms-agent-request-0001",
            name="publish_review",
            revision_target="review_iteration",
            target=iteration_id,
            expected_revision=iteration_revision,
            payload={
                "review_revision_id": str(review_revision_id),
                "publication_request_id": str(publication_request_id),
            },
        ),
    )
    assert published.status_code == 202, published.text
    await _assert_human_publication(
        agent_workflow.session_factory,
        iteration_id=iteration_id,
        review_revision_id=review_revision_id,
        publication_request_id=publication_request_id,
        authorization_id=authorization_id,
    )

    revoked = await agent_workflow.client.post(
        f"/api/v1/agent-authorizations/{authorization_id}/revoke",
        headers=_human_headers(),
        json=_command(
            request=28,
            idempotency="human-revokes-agent-0001",
            name="revoke_agent_authorization",
            revision_target="agent_authorization",
            target=authorization_id,
            expected_revision=0,
            payload={"reason": "Agent workflow completed"},
        ),
    )
    assert revoked.status_code == 204, revoked.text

    revision_count = await _revision_count(agent_workflow.session_factory, iteration_id)
    denied_read = await _raw_mcp(
        agent_workflow.client,
        token,
        "get_review_iteration",
        {"review_iteration_id": str(iteration_id)},
    )
    denied_write = await _raw_mcp(
        agent_workflow.client,
        token,
        "record_review_responsibility",
        _command(
            request=29,
            idempotency="revoked-agent-write-0001",
            name="record_review_responsibility",
            revision_target="review_iteration",
            target=iteration_id,
            expected_revision=iteration_revision + 1,
            payload={"action": "completed"},
        ),
    )
    assert denied_read.status_code in {401, 403}, denied_read.text
    assert denied_write.status_code in {401, 403}, denied_write.text
    assert await _revision_count(agent_workflow.session_factory, iteration_id) == revision_count
    async with agent_workflow.session_factory() as session:
        authorization = await session.scalar(
            select(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.id == authorization_id,
            )
        )
    assert authorization is not None and authorization.status == "revoked"


async def _grant(client: AsyncClient) -> tuple[str, UUID]:
    response = await client.post(
        f"/api/v1/memberships/{REVIEWER_MEMBERSHIP}/agent-authorizations",
        headers=_human_headers(),
        json=_grant_command(),
    )
    assert response.status_code == 201, (
        "US7 interactive agent authorization grant is not implemented: "
        f"{response.status_code} {response.text}"
    )
    body = response.json()
    assert set(body) == {
        "id",
        "agent_id",
        "scopes",
        "expires_at",
        "revision",
        "access_token",
    }
    assert body["agent_id"] == str(AGENT)
    assert set(body["scopes"]) == set(ALL_AGENT_SCOPES)
    assert len(body["access_token"]) >= 32
    return str(body["access_token"]), UUID(str(body["id"]))


def _grant_command() -> dict[str, Any]:
    return _command(
        request=20,
        idempotency="interactive-agent-grant-0001",
        name="grant_agent_authorization",
        revision_target="membership",
        target=REVIEWER_MEMBERSHIP,
        expected_revision=0,
        payload={
            "agent_id": str(AGENT),
            "scopes": ALL_AGENT_SCOPES,
            "expires_at": (NOW + timedelta(days=2)).isoformat(),
        },
    )


def _command(
    *,
    request: int,
    idempotency: str,
    name: str,
    revision_target: str,
    target: UUID,
    expected_revision: int,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "request_id": f"00000000-0000-7000-8000-{request:012d}",
        "idempotency_key": idempotency,
        "command_name": name,
        "revision_target": revision_target,
        "target_id": str(target),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }
    WireCommand.model_validate(body)
    return body


def _human_headers() -> dict[str, str]:
    return {"X-Test-Interactive-Actor": "reviewer"}


async def _mcp(
    client: AsyncClient,
    token: str,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    response = await _raw_mcp(client, token, tool_name, arguments)
    assert response.status_code in {200, 201, 202}, (
        f"US7 MCP tool {tool_name!r} is not implemented: "
        f"{response.status_code} {response.text}"
    )
    value = response.json()
    assert isinstance(value, dict), f"MCP tool {tool_name!r} returned an untyped result"
    return cast(dict[str, Any], value)


async def _raw_mcp(
    client: AsyncClient,
    token: str,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> Response:
    return await client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": tool_name,
        },
        json=dict(arguments),
    )


async def _assert_not_published(
    factory: AsyncSessionFactory,
    iteration_id: UUID,
    request_id: UUID,
) -> None:
    async with factory() as session:
        request = await session.scalar(
            select(PublicationRequest).where(
                PublicationRequest.organization_id == ORG,
                PublicationRequest.id == request_id,
                PublicationRequest.review_iteration_id == iteration_id,
            )
        )
        publications = await session.scalar(
            select(func.count())
            .select_from(ReviewPublication)
            .where(ReviewPublication.organization_id == ORG)
        )
        deliveries = await session.scalar(
            select(func.count())
            .select_from(ExternalDelivery)
            .where(ExternalDelivery.organization_id == ORG)
        )
    assert request is not None and request.status == "pending"
    assert publications == 0
    assert deliveries == 0


async def _assert_human_publication(
    factory: AsyncSessionFactory,
    *,
    iteration_id: UUID,
    review_revision_id: UUID,
    publication_request_id: UUID,
    authorization_id: UUID,
) -> None:
    async with factory() as session:
        publication = await session.scalar(
            select(ReviewPublication).where(
                ReviewPublication.organization_id == ORG,
                ReviewPublication.review_iteration_id == iteration_id,
                ReviewPublication.review_revision_id == review_revision_id,
            )
        )
        request = await session.scalar(
            select(PublicationRequest).where(
                PublicationRequest.organization_id == ORG,
                PublicationRequest.id == publication_request_id,
            )
        )
        authorization = await session.scalar(
            select(AgentAuthorization).where(
                AgentAuthorization.organization_id == ORG,
                AgentAuthorization.id == authorization_id,
            )
        )
        agent_audits = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == ORG,
                    AuditEvent.agent_id == AGENT,
                    AuditEvent.agent_authorization_id == authorization_id,
                )
            )
        ).all()
    assert publication is not None
    assert publication.publication_request_id == publication_request_id
    assert publication.published_by == REVIEWER
    assert request is not None and request.status == "confirmed"
    assert authorization is not None and authorization.user_id == REVIEWER
    assert {event.action for event in agent_audits} >= {
        "record_review_responsibility",
        "save_review_revision",
        "request_review_publication",
    }


async def _revision_count(factory: AsyncSessionFactory, iteration_id: UUID) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ReviewRevision)
                .where(
                    ReviewRevision.organization_id == ORG,
                    ReviewRevision.review_iteration_id == iteration_id,
                )
            )
            or 0
        )


async def _seed_review_candidate(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=REVIEWER, display_name="Represented reviewer", status="active"),
                User(id=STUDENT, display_name="Agent workflow student", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=REVIEWER_MEMBERSHIP,
                    organization_id=ORG,
                    user_id=REVIEWER,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                OrganizationMembership(
                    id=STUDENT_MEMBERSHIP,
                    organization_id=ORG,
                    user_id=STUDENT,
                    roles=["student"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Agent workflow course",
                    description="Offline US7 acceptance fixture",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                ExternalCredential(
                    id=GITHUB_CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="offline-github-credential",
                    key_id="test-key",
                    status="active",
                ),
                ExternalCredential(
                    id=AI_CREDENTIAL,
                    organization_id=ORG,
                    provider="ai_review",
                    binding_version=1,
                    ciphertext="offline-ai-credential",
                    key_id="test-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Agent workflow run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Agent workflow homework",
                    revision=0,
                ),
                ArtifactReference(
                    id=ARTIFACT_REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=GITHUB_CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/agent-workflow",
                    locator={"external_id": "example/agent-workflow"},
                    read_capability="available",
                    feedback_capability="available",
                    last_checked_at=NOW,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000019031"),
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    user_id=REVIEWER,
                    kind="reviewer",
                    source="self_selected",
                    status="active",
                    joined_at=NOW,
                ),
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000019032"),
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    user_id=STUDENT,
                    kind="student",
                    source="imported",
                    status="active",
                    joined_at=NOW,
                ),
                ReviewerCourseSelection(
                    id=UUID("00000000-0000-7000-8000-000000019033"),
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    reviewer_id=REVIEWER,
                    active=True,
                ),
                AvailabilityPlan(
                    id=UUID("00000000-0000-7000-8000-000000019034"),
                    organization_id=ORG,
                    reviewer_id=REVIEWER,
                    planned_minutes=120,
                    until_at=NOW + timedelta(days=1),
                    revision=0,
                ),
                HomeworkVersion(
                    id=HOMEWORK_VERSION,
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=1,
                    student_text="Submit one captured repository artifact.",
                    max_score=Decimal("5"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                CourseRunHomework(
                    id=RUN_HOMEWORK,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    current_publication_id=None,
                    status="active",
                    revision=0,
                ),
                ArtifactVersion(
                    id=ARTIFACT_VERSION,
                    organization_id=ORG,
                    artifact_reference_id=ARTIFACT_REFERENCE,
                    provider_version="commit:agent-workflow",
                    content_digest="sha256:" + "a" * 64,
                    object_key=f"{ORG}/{ARTIFACT_VERSION}/artifact.zip",
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW,
                    artifact_metadata={"fixture": "us7"},
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CriterionSet(
                    id=CRITERION_SET,
                    organization_id=ORG,
                    homework_version_id=HOMEWORK_VERSION,
                ),
                CourseRunHomeworkPublication(
                    id=HOMEWORK_PUBLICATION,
                    organization_id=ORG,
                    course_run_homework_id=RUN_HOMEWORK,
                    homework_id=HOMEWORK,
                    homework_version_id=HOMEWORK_VERSION,
                    publication_sequence=1,
                    submission_deadline=NOW + timedelta(hours=1),
                    review_deadline=NOW + timedelta(days=1),
                    published_at=NOW - timedelta(days=1),
                ),
                Submission(
                    id=SUBMISSION,
                    organization_id=ORG,
                    course_run_homework_id=RUN_HOMEWORK,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    current_predeadline_version_id=None,
                    revision=0,
                ),
                ReviewCase(
                    id=REVIEW_CASE,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    current_iteration_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Criterion(
                    id=CRITERION,
                    organization_id=ORG,
                    criterion_set_id=CRITERION_SET,
                    stable_key="agent-workflow",
                    position=0,
                    title="Agent workflow criterion",
                    description="The captured artifact satisfies the requirements.",
                    max_points=Decimal("5"),
                    active=True,
                ),
                SubmissionVersion(
                    id=SUBMISSION_VERSION,
                    organization_id=ORG,
                    submission_id=SUBMISSION,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    sequence=1,
                    homework_version_id=HOMEWORK_VERSION,
                    artifact_reference_id=ARTIFACT_REFERENCE,
                    artifact_version_id=ARTIFACT_VERSION,
                    submitted_at=NOW,
                    effective_deadline=NOW + timedelta(hours=1),
                    phase="before_deadline",
                    status="ready",
                    capture_operation_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        relation = await session.get(CourseRunHomework, RUN_HOMEWORK)
        submission = await session.get(Submission, SUBMISSION)
        assert relation is not None and submission is not None
        relation.current_publication_id = HOMEWORK_PUBLICATION
        submission.current_predeadline_version_id = SUBMISSION_VERSION
