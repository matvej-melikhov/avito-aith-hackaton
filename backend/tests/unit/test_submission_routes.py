from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from tests.support.contracts import load_openapi, validator_for

from review_platform.api.routes.submissions import _artifact_error_object
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000001201")
TARGET = UUID("00000000-0000-7000-8000-000000001202")
OTHER = UUID("00000000-0000-7000-8000-000000001203")


@pytest.fixture
async def client(
    foundation_runtime: FoundationRuntime,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"student"},
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def actor_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


async def test_exact_submission_routes_are_registered(
    foundation_runtime: FoundationRuntime,
) -> None:
    app = create_app(Settings(), runtime=foundation_runtime)
    operations = {
        (route.path, method): route.operation_id
        for route in app.routes
        if hasattr(route, "operation_id") and hasattr(route, "methods")
        for method in route.methods
    }
    assert {
        (
            "/api/v1/course-run-homeworks/{courseRunHomeworkId}/submissions/preflight",
            "POST",
        ): "preflightSubmission",
        ("/api/v1/submissions/{submissionId}/versions", "POST"): "submitWork",
        ("/api/v1/submissions/{submissionId}", "GET"): "getSubmissionHistory",
        ("/api/v1/review-cases/{reviewCaseId}/iterations", "POST"): ("openReviewIteration"),
    }.items() <= operations.items()


@pytest.mark.parametrize(
    ("path", "command"),
    [
        (
            f"/api/v1/course-run-homeworks/{TARGET}/submissions/preflight",
            {
                "command_name": "preflight_submission",
                "revision_target": "course_run_homework",
                "payload": {"artifact_url": "https://github.com/acme/repo"},
            },
        ),
        (
            f"/api/v1/submissions/{TARGET}/versions",
            {
                "command_name": "submit_work",
                "revision_target": "submission",
                "payload": {"artifact_reference_id": str(OTHER)},
            },
        ),
        (
            f"/api/v1/review-cases/{TARGET}/iterations",
            {
                "command_name": "open_review_iteration",
                "revision_target": "review_case",
                "payload": {"submission_version_id": str(OTHER)},
            },
        ),
    ],
)
async def test_mutations_reject_path_target_mismatch_before_provider_or_db(
    client: httpx.AsyncClient,
    path: str,
    command: dict[str, object],
) -> None:
    body = {
        "request_id": "00000000-0000-7000-8000-000000001204",
        "idempotency_key": "submission-route-mismatch-0001",
        "target_id": str(OTHER),
        "expected_revision": 0,
        **command,
    }
    response = await client.post(path, json=body)

    assert response.status_code == 409
    assert set(response.json()) == {"code", "message", "action"}


def test_unavailable_preflight_error_is_closed_frozen_artifact_capability() -> None:
    projected = _artifact_error_object(
        {
            "code": "provider_access_denied",
            "message": "Grant repository access",
            "action": "grant_access",
            "retryable": False,
            "provider_body": {"private": "must-not-leak"},
        }
    )
    capability = {
        "provider": "github",
        "read_capability": "requires_action",
        "feedback_capability": "requires_action",
        "submission_id": str(TARGET),
        "submission_revision": 0,
        "artifact_reference_id": None,
        "error": projected,
    }
    openapi = load_openapi()
    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "#/components/schemas/ArtifactCapability",
            "components": openapi["components"],
        }
    ).validate(capability)

    assert projected == {
        "code": "provider_access_denied",
        "message": "Grant repository access",
        "action": "grant_access",
    }
    assert set(projected) == {"code", "message", "action"}
