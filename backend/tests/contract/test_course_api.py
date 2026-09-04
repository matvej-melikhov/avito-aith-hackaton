"""Executable REST contract for organization, course, and CourseRun boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.services.courses import (
    ArchivedCourseActionDenied,
    CourseArchivedStateGuard,
)
from review_platform.application.services.invitations import InvitationView
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    Invitation,
    Operation,
    OperationAttempt,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.repositories.learning import (
    CourseRepository,
    CourseRunRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000001")
COURSE_ID = UUID("00000000-0000-7000-8000-000000000702")
COURSE_RUN_ID = UUID("00000000-0000-7000-8000-000000000703")
INVITATION_ID = UUID("00000000-0000-7000-8000-000000000704")
OPERATION_ID = UUID("00000000-0000-7000-8000-000000000705")
USER_ID = UUID("00000000-0000-7000-8000-000000000706")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000707")
OTHER_TARGET_ID = UUID("00000000-0000-7000-8000-000000000799")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class StubInvitationService:
    async def issue(self, **_: object) -> InvitationView:
        return InvitationView(
            invitation_id=UUID("00000000-0000-7000-8000-000000000708"),
            organization_id=ORGANIZATION_ID,
            normalized_email="new-reviewer@example.test",
            role="reviewer",
            status="active",
            revision=0,
            expires_at=NOW + timedelta(hours=1),
        )

    async def revoke(self, **_: object) -> None:
        return None


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(User(id=USER_ID, display_name="Course Contract User", status="active"))
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP_ID,
                    organization_id=ORGANIZATION_ID,
                    user_id=USER_ID,
                    roles=["methodologist", "reviewer"],
                    status="active",
                    revision=3,
                    auth_epoch=2,
                ),
                Course(
                    id=COURSE_ID,
                    organization_id=ORGANIZATION_ID,
                    title="Contract Course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=4,
                ),
                Invitation(
                    id=INVITATION_ID,
                    organization_id=ORGANIZATION_ID,
                    role="reviewer",
                    normalized_email="reviewer@example.test",
                    token_digest="sha256:" + "1" * 64,
                    expires_at=NOW + timedelta(hours=1),
                    issued_by=USER_ID,
                    status="active",
                    revision=2,
                ),
                Operation(
                    id=OPERATION_ID,
                    organization_id=ORGANIZATION_ID,
                    kind="course_import",
                    input_version="stepik:course:provider-version-7",
                    state="succeeded",
                    revision=2,
                    created_at=NOW - timedelta(minutes=2),
                    updated_at=NOW,
                    finished_at=NOW,
                    error_code=None,
                    sanitized_error=None,
                ),
            ]
        )
        await session.flush()
        session.add(
            CourseRun(
                id=COURSE_RUN_ID,
                organization_id=ORGANIZATION_ID,
                course_id=COURSE_ID,
                external_run_id="contract-run",
                title="Contract Run",
                starts_at=None,
                ends_at=None,
                timezone="Europe/Moscow",
                status="archived",
                revision=5,
            )
        )
        session.add_all(
            [
                OperationAttempt(
                    id=UUID("00000000-0000-7000-8000-000000000709"),
                    organization_id=ORGANIZATION_ID,
                    operation_id=OPERATION_ID,
                    attempt_number=1,
                    worker_identity="course-import-worker",
                    started_at=NOW - timedelta(minutes=2),
                    finished_at=NOW - timedelta(minutes=1),
                    outcome="retryable_failed",
                    error_code="provider_timeout",
                    sanitized_error={
                        "code": "provider_timeout",
                        "message": "Provider timed out",
                        "action": "retry",
                    },
                ),
                OperationAttempt(
                    id=UUID("00000000-0000-7000-8000-000000000710"),
                    organization_id=ORGANIZATION_ID,
                    operation_id=OPERATION_ID,
                    attempt_number=2,
                    worker_identity="course-import-worker",
                    started_at=NOW - timedelta(minutes=1),
                    finished_at=NOW,
                    outcome="succeeded",
                    error_code=None,
                    sanitized_error=None,
                ),
            ]
        )
        await session.flush()
        session.add(
            CourseMembership(
                id=UUID("00000000-0000-7000-8000-000000000711"),
                organization_id=ORGANIZATION_ID,
                course_run_id=COURSE_RUN_ID,
                user_id=USER_ID,
                kind="reviewer",
                source="invitation",
                status="active",
                external_version=None,
                joined_at=NOW,
                removed_at=None,
            )
        )


@pytest.fixture
async def course_client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[httpx.AsyncClient]:
    await _seed(foundation_session_factory)
    app = create_app(Settings(), runtime=foundation_runtime)
    app.state.course_import_credential_binding_id = UUID(
        "00000000-0000-7000-8000-000000000712"
    )
    app.state.course_import_credential_binding_version = 1
    app.state.invitation_service_factory = lambda _session: StubInvitationService()
    actor = RequestActor.user(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        roles={"methodologist", "reviewer"},
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
        base_url="http://test",
    ) as client:
        yield client


def _wire_command(
    *,
    command_name: str,
    revision_target: str,
    target_id: UUID,
    payload: Mapping[str, Any],
    expected_revision: int = 0,
) -> dict[str, Any]:
    return {
        "request_id": "00000000-0000-7000-8000-000000000713",
        "idempotency_key": f"contract-{command_name}-0001",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


def _validate_component(component_name: str, value: object) -> None:
    openapi = load_openapi()
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{component_name}",
        "components": openapi["components"],
    }
    validator_for(wrapper).validate(value)


def _assert_error_response(response: httpx.Response) -> None:
    assert response.status_code == 409, response.text
    _validate_component("ErrorObject", response.json())


async def test_course_and_run_lists_expose_stable_identity_and_revision(
    course_client: httpx.AsyncClient,
) -> None:
    courses = await course_client.get("/api/v1/courses")
    runs = await course_client.get(
        "/api/v1/course-runs",
        params={"course_id": str(COURSE_ID)},
    )

    assert courses.status_code == runs.status_code == 200
    _validate_component("CourseList", courses.json())
    _validate_component("CourseRunList", runs.json())
    assert courses.json()["items"][0]["id"] == str(COURSE_ID)
    assert courses.json()["items"][0]["revision"] == 4
    assert runs.json()["items"][0]["id"] == str(COURSE_RUN_ID)
    assert runs.json()["items"][0]["revision"] == 5


@pytest.mark.parametrize(
    ("path", "component"),
    [
        ("/api/v1/organization/memberships", "OrganizationMembershipList"),
        ("/api/v1/invitations", "InvitationList"),
        (f"/api/v1/course-runs/{COURSE_RUN_ID}/memberships", "CourseMembershipList"),
    ],
)
async def test_membership_invitation_and_roster_reads_are_typed(
    course_client: httpx.AsyncClient,
    path: str,
    component: str,
) -> None:
    response = await course_client.get(path)

    assert response.status_code == 200, response.text
    _validate_component(component, response.json())
    assert response.json()["items"]


async def test_create_invitation_and_course_import_return_frozen_types(
    course_client: httpx.AsyncClient,
) -> None:
    invitation = await course_client.post(
        "/api/v1/invitations",
        json=_wire_command(
            command_name="create_invitation",
            revision_target="organization",
            target_id=ORGANIZATION_ID,
            payload={
                "email": "new-reviewer@example.test",
                "role": "reviewer",
                "expires_at": "2026-09-05T12:00:00Z",
            },
        ),
    )
    course_import = await course_client.post(
        "/api/v1/courses/imports",
        json=_wire_command(
            command_name="start_course_import",
            revision_target="organization",
            target_id=ORGANIZATION_ID,
            payload={"provider": "stepik", "external_url": "https://stepik.org/course/123"},
        ),
    )

    assert invitation.status_code == 201, invitation.text
    assert course_import.status_code == 202, course_import.text
    _validate_component("CreatedResource", invitation.json())
    _validate_component("Operation", course_import.json())
    assert course_import.json()["state"] == "pending"


@pytest.mark.parametrize(
    ("path", "command_name", "revision_target", "payload"),
    [
        (
            f"/api/v1/courses/{COURSE_ID}/archive",
            "archive_course",
            "course",
            {"reason": "finished"},
        ),
        (f"/api/v1/courses/{COURSE_ID}/restore", "restore_course", "course", {}),
        (
            f"/api/v1/course-runs/{COURSE_RUN_ID}/archive",
            "archive_course_run",
            "course_run",
            {"reason": "finished"},
        ),
        (
            f"/api/v1/course-runs/{COURSE_RUN_ID}/restore",
            "restore_course_run",
            "course_run",
            {},
        ),
    ],
)
async def test_archive_restore_reject_path_target_mismatch(
    course_client: httpx.AsyncClient,
    path: str,
    command_name: str,
    revision_target: str,
    payload: Mapping[str, Any],
) -> None:
    response = await course_client.post(
        path,
        json=_wire_command(
            command_name=command_name,
            revision_target=revision_target,
            target_id=OTHER_TARGET_ID,
            payload=payload,
        ),
    )

    _assert_error_response(response)


async def test_archive_route_rejects_different_valid_command_variant(
    course_client: httpx.AsyncClient,
) -> None:
    response = await course_client.post(
        f"/api/v1/courses/{COURSE_ID}/archive",
        json=_wire_command(
            command_name="restore_course",
            revision_target="course",
            target_id=COURSE_ID,
            payload={},
        ),
    )

    _assert_error_response(response)


async def test_archived_guard_blocks_all_three_new_action_kinds(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    async with foundation_runtime.transaction() as transaction:
        guard = CourseArchivedStateGuard(
            courses=CourseRepository(transaction),
            course_runs=CourseRunRepository(transaction),
        )
        with pytest.raises(ArchivedCourseActionDenied, match="recommendation"):
            await guard.require_active(
                organization_id=ORGANIZATION_ID,
                course_run_id=COURSE_RUN_ID,
                action="recommendation",
            )
        with pytest.raises(ArchivedCourseActionDenied, match="open_review"):
            await guard.require_active(
                organization_id=ORGANIZATION_ID,
                course_run_id=COURSE_RUN_ID,
                action="open_review",
            )
        with pytest.raises(ArchivedCourseActionDenied, match="publication"):
            await guard.require_active(
                organization_id=ORGANIZATION_ID,
                course_run_id=COURSE_RUN_ID,
                action="publication",
            )


async def test_operation_read_returns_full_ordered_attempt_history_shape(
    course_client: httpx.AsyncClient,
) -> None:
    response = await course_client.get(f"/api/v1/operations/{OPERATION_ID}")

    assert response.status_code == 200, response.text
    payload = response.json()
    _validate_component("Operation", payload)
    assert payload["id"] == str(OPERATION_ID)
    assert payload["kind"] == "course_import"
    assert payload["input_version"] == "stepik:course:provider-version-7"
    assert [attempt["attempt_number"] for attempt in payload["attempts"]] == [1, 2]
