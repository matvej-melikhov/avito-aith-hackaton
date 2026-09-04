"""Executable REST contract for organization, course, and CourseRun boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime, OperationView
from review_platform.application.request_context import RequestActor
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.anyio]

ORGANIZATION_ID = "00000000-0000-7000-8000-000000000001"
COURSE_ID = "00000000-0000-7000-8000-000000000002"
COURSE_RUN_ID = "00000000-0000-7000-8000-000000000003"
REVIEW_CASE_ID = "00000000-0000-7000-8000-000000000006"
REVIEW_ITERATION_ID = "00000000-0000-7000-8000-000000000007"
REVIEW_REVISION_ID = "00000000-0000-7000-8000-000000000008"
OPERATION_ID = "00000000-0000-7000-8000-000000000009"
USER_ID = "00000000-0000-7000-8000-000000000010"
SUBMISSION_VERSION_ID = "00000000-0000-7000-8000-000000000012"
OTHER_TARGET_ID = "00000000-0000-7000-8000-000000000099"


class CourseContractProbeRuntime(FoundationRuntime):
    """Only supplies the already-existing Operation read during the RED wave."""

    def __init__(self) -> None:
        # No infrastructure is needed to probe route/response contracts.
        pass

    async def get_operation(
        self, *, organization_id: str, operation_id: str
    ) -> OperationView | None:
        if organization_id != ORGANIZATION_ID or operation_id != OPERATION_ID:
            return None
        return OperationView(
            operation_id=OPERATION_ID,
            organization_id=ORGANIZATION_ID,
            kind="course_import",
            input_version="stepik:course:provider-version-7",
            state="succeeded",
            attempts=(
                {
                    "attempt_number": 1,
                    "state": "retryable_failed",
                    "started_at": "2026-09-04T11:58:00Z",
                    "finished_at": "2026-09-04T11:58:05Z",
                    "error": {
                        "code": "provider_timeout",
                        "message": "Provider timed out",
                        "action": "retry",
                    },
                },
                {
                    "attempt_number": 2,
                    "state": "succeeded",
                    "started_at": "2026-09-04T11:59:00Z",
                    "finished_at": "2026-09-04T11:59:04Z",
                    "error": None,
                },
            ),
            error=None,
        )

    async def close(self) -> None:
        return None


@pytest.fixture
async def course_client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), runtime=CourseContractProbeRuntime())
    actor = RequestActor.user(
        organization_id=UUID(ORGANIZATION_ID),
        user_id=UUID(USER_ID),
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
    target_id: str,
    payload: Mapping[str, Any],
    expected_revision: int = 0,
) -> dict[str, Any]:
    return {
        "request_id": "00000000-0000-7000-8000-000000000011",
        "idempotency_key": f"contract-{command_name}-0001",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": target_id,
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


def _assert_error_response(response: httpx.Response, *, status_code: int = 409) -> None:
    assert response.status_code == status_code, response.text
    _validate_component("ErrorObject", response.json())


async def test_course_list_exposes_stable_identity_status_and_revision(
    course_client: httpx.AsyncClient,
) -> None:
    response = await course_client.get("/api/v1/courses")

    assert response.status_code == 200, response.text
    payload = response.json()
    _validate_component("CourseList", payload)
    course_schema = load_openapi()["components"]["schemas"]["Course"]
    assert set(course_schema["required"]) == {"id", "title", "status", "revision"}


async def test_course_run_list_exposes_course_identity_timezone_status_and_revision(
    course_client: httpx.AsyncClient,
) -> None:
    response = await course_client.get(
        "/api/v1/course-runs",
        params={"course_id": COURSE_ID},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    _validate_component("CourseRunList", payload)
    run_schema = load_openapi()["components"]["schemas"]["CourseRun"]
    assert set(run_schema["required"]) == {
        "id",
        "course_id",
        "title",
        "timezone",
        "status",
        "revision",
    }


@pytest.mark.parametrize(
    ("path", "component"),
    [
        ("/api/v1/organization/memberships", "OrganizationMembershipList"),
        ("/api/v1/invitations", "InvitationList"),
        (
            f"/api/v1/course-runs/{COURSE_RUN_ID}/memberships",
            "CourseMembershipList",
        ),
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


async def test_create_invitation_returns_typed_resource_identity_and_revision(
    course_client: httpx.AsyncClient,
) -> None:
    command = _wire_command(
        command_name="create_invitation",
        revision_target="organization",
        target_id=ORGANIZATION_ID,
        payload={
            "email": "reviewer@example.test",
            "role": "reviewer",
            "expires_at": "2026-09-05T12:00:00Z",
        },
    )
    response = await course_client.post("/api/v1/invitations", json=command)

    assert response.status_code == 201, response.text
    _validate_component("CreatedResource", response.json())


async def test_start_course_import_returns_full_typed_operation(
    course_client: httpx.AsyncClient,
) -> None:
    command = _wire_command(
        command_name="start_course_import",
        revision_target="organization",
        target_id=ORGANIZATION_ID,
        payload={"provider": "stepik", "external_url": "https://stepik.org/course/123"},
    )
    response = await course_client.post("/api/v1/courses/imports", json=command)

    assert response.status_code == 202, response.text
    _validate_component("Operation", response.json())


@pytest.mark.parametrize(
    ("path", "command_name", "revision_target", "payload"),
    [
        (
            f"/api/v1/courses/{COURSE_ID}/archive",
            "archive_course",
            "course",
            {"reason": "finished"},
        ),
        (
            f"/api/v1/courses/{COURSE_ID}/restore",
            "restore_course",
            "course",
            {},
        ),
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
    command = _wire_command(
        command_name=command_name,
        revision_target=revision_target,
        target_id=OTHER_TARGET_ID,
        payload=payload,
    )
    response = await course_client.post(path, json=command)

    _assert_error_response(response)


async def test_archive_route_rejects_different_valid_command_variant(
    course_client: httpx.AsyncClient,
) -> None:
    command = _wire_command(
        command_name="restore_course",
        revision_target="course",
        target_id=COURSE_ID,
        payload={},
    )
    response = await course_client.post(
        f"/api/v1/courses/{COURSE_ID}/archive",
        json=command,
    )

    _assert_error_response(response)


@pytest.mark.parametrize(
    ("method", "path", "command"),
    [
        ("GET", f"/api/v1/review-queue/next?course_run_id={COURSE_RUN_ID}", None),
        (
            "POST",
            f"/api/v1/review-cases/{REVIEW_CASE_ID}/iterations",
            _wire_command(
                command_name="open_review_iteration",
                revision_target="review_case",
                target_id=REVIEW_CASE_ID,
                payload={"submission_version_id": SUBMISSION_VERSION_ID},
            ),
        ),
        (
            "POST",
            f"/api/v1/review-iterations/{REVIEW_ITERATION_ID}/publish",
            _wire_command(
                command_name="publish_review",
                revision_target="review_iteration",
                target_id=REVIEW_ITERATION_ID,
                payload={"review_revision_id": REVIEW_REVISION_ID},
            ),
        ),
    ],
)
async def test_archived_course_run_blocks_new_recommendation_review_and_publication(
    course_client: httpx.AsyncClient,
    method: str,
    path: str,
    command: Mapping[str, Any] | None,
) -> None:
    archive_command = _wire_command(
        command_name="archive_course_run",
        revision_target="course_run",
        target_id=COURSE_RUN_ID,
        payload={"reason": "contract precondition"},
    )
    archive_response = await course_client.post(
        f"/api/v1/course-runs/{COURSE_RUN_ID}/archive",
        json=archive_command,
    )
    response = await course_client.request(method, path, json=command)

    assert archive_response.status_code == 204, archive_response.text
    _assert_error_response(response)


async def test_operation_read_returns_full_ordered_attempt_history_shape(
    course_client: httpx.AsyncClient,
) -> None:
    response = await course_client.get(f"/api/v1/operations/{OPERATION_ID}")

    assert response.status_code == 200, response.text
    payload = response.json()
    _validate_component("Operation", payload)
    assert payload["id"] == OPERATION_ID
    assert payload["kind"] == "course_import"
    assert payload["input_version"] == "stepik:course:provider-version-7"
    assert [attempt["attempt_number"] for attempt in payload["attempts"]] == [1, 2]
