"""Executable RED contract for versioned homework HTTP boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models import (
    Course,
    CourseRun,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000001")
USER_ID = UUID("00000000-0000-7000-8000-000000000801")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000802")
COURSE_ID = UUID("00000000-0000-7000-8000-000000000803")
COURSE_RUN_A = UUID("00000000-0000-7000-8000-000000000804")
COURSE_RUN_B = UUID("00000000-0000-7000-8000-000000000805")
OTHER_TARGET_ID = UUID("00000000-0000-7000-8000-000000000899")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(User(id=USER_ID, display_name="Homework Contract User", status="active"))
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
                    title="Homework Contract Course",
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
                CourseRun(
                    id=COURSE_RUN_A,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    external_run_id="homework-contract-a",
                    title="Homework Contract Run A",
                    starts_at=None,
                    ends_at=None,
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=COURSE_RUN_B,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    external_run_id="homework-contract-b",
                    title="Homework Contract Run B",
                    starts_at=None,
                    ends_at=None,
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
            ]
        )


@pytest.fixture
async def homework_client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[httpx.AsyncClient]:
    await _seed(foundation_session_factory)
    app = create_app(Settings(), runtime=foundation_runtime)
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
    expected_revision: int,
    payload: Mapping[str, Any],
    request_number: int,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request_number:012d}",
        "idempotency_key": f"homework-{command_name}-{request_number:08d}",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


def _version_payload(*, text: str) -> dict[str, Any]:
    return {
        "student_text": text,
        "max_score": 10,
        "artifact_kinds": ["github"],
        "estimated_review_minutes": 30,
        "criteria": [
            {
                "key": "correctness",
                "title": "Correctness",
                "description": "The solution satisfies the requirements",
                "max_points": 10,
            }
        ],
    }


def _publication_payload(course_run_id: UUID) -> dict[str, Any]:
    return {
        "course_run_id": str(course_run_id),
        "submission_deadline": "2026-09-10T12:00:00Z",
        "review_deadline": "2026-09-12T12:00:00Z",
    }


def _validate_component(component_name: str, value: object) -> None:
    openapi = load_openapi()
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{component_name}",
        "components": openapi["components"],
    }
    validator_for(wrapper).validate(value)


def _assert_conflict(response: httpx.Response) -> None:
    assert response.status_code == 409, response.text
    _validate_component("ErrorObject", response.json())


async def _create_homework(
    client: httpx.AsyncClient,
    *,
    course_run_id: UUID = COURSE_RUN_A,
    expected_revision: int = 0,
    request_number: int,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/course-runs/{course_run_id}/homeworks",
        json=_wire_command(
            command_name="create_homework",
            revision_target="course_run",
            target_id=course_run_id,
            expected_revision=expected_revision,
            payload={"title": "Contract Homework"},
            request_number=request_number,
        ),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    _validate_component("CreatedResource", payload)
    return payload


async def _create_version(
    client: httpx.AsyncClient,
    *,
    homework_id: UUID,
    expected_revision: int,
    text: str,
    request_number: int,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/homeworks/{homework_id}/versions",
        json=_wire_command(
            command_name="create_homework_version",
            revision_target="homework",
            target_id=homework_id,
            expected_revision=expected_revision,
            payload=_version_payload(text=text),
            request_number=request_number,
        ),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    _validate_component("CreatedResource", payload)
    return payload


async def _publish_version(
    client: httpx.AsyncClient,
    *,
    version_id: UUID,
    course_run_id: UUID,
    expected_revision: int,
    request_number: int,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/homework-versions/{version_id}/publish",
        json=_wire_command(
            command_name="publish_homework_version",
            revision_target="homework_version",
            target_id=version_id,
            expected_revision=expected_revision,
            payload=_publication_payload(course_run_id),
            request_number=request_number,
        ),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    _validate_component("CreatedResource", payload)
    return payload


def test_frozen_openapi_binds_exact_homework_commands_and_result_types() -> None:
    openapi = load_openapi()
    expected = {
        ("/v1/course-runs/{courseRunId}/homeworks", "post"): (
            "create_homework",
            "create_homework",
            "CreatedResource",
        ),
        ("/v1/homeworks/{homeworkId}/versions", "post"): (
            "create_homework_version",
            "create_homework_version",
            "CreatedResource",
        ),
        ("/v1/homework-versions/{homeworkVersionId}/publish", "post"): (
            "publish_homework_version",
            "publish_homework_version",
            "CreatedResource",
        ),
    }
    for (path, method), (command_name, definition, response_name) in expected.items():
        operation = openapi["paths"][path][method]
        assert operation["x-command-name"] == command_name
        assert operation["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": f"command.schema.json#/$defs/{definition}"
        }
        success = operation["responses"]["201"]["content"]["application/json"]["schema"]
        assert success == {"$ref": f"#/components/schemas/{response_name}"}

    assert openapi["paths"]["/v1/course-runs/{courseRunId}/homeworks"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HomeworkList"
    }
    assert openapi["paths"]["/v1/homeworks/{homeworkId}"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HomeworkHistory"
    }


async def test_draft_has_no_publication_or_current_version(
    homework_client: httpx.AsyncClient,
) -> None:
    created = await _create_homework(homework_client, request_number=801)
    homework_id = UUID(created["id"])

    published = await homework_client.get(
        f"/api/v1/course-runs/{COURSE_RUN_A}/homeworks"
    )
    history = await homework_client.get(f"/api/v1/homeworks/{homework_id}")
    assert published.status_code == history.status_code == 200
    _validate_component("HomeworkList", published.json())
    _validate_component("HomeworkHistory", history.json())
    assert published.json()["items"] == []
    assert history.json()["versions"] == []
    assert history.json()["course_run_publications"] == []


async def test_create_version_publish_and_reads_use_frozen_typed_results(
    homework_client: httpx.AsyncClient,
) -> None:
    created = await _create_homework(homework_client, request_number=811)
    homework_id = UUID(created["id"])
    version = await _create_version(
        homework_client,
        homework_id=homework_id,
        expected_revision=created["revision"],
        text="Version one",
        request_number=812,
    )
    version_id = UUID(version["id"])
    await _publish_version(
        homework_client,
        version_id=version_id,
        course_run_id=COURSE_RUN_A,
        expected_revision=version["revision"],
        request_number=813,
    )

    published = await homework_client.get(
        f"/api/v1/course-runs/{COURSE_RUN_A}/homeworks"
    )
    history = await homework_client.get(f"/api/v1/homeworks/{homework_id}")
    assert published.status_code == history.status_code == 200
    _validate_component("HomeworkList", published.json())
    _validate_component("HomeworkHistory", history.json())
    assert published.json()["items"][0]["current_version_id"] == str(version_id)
    assert history.json()["versions"][0]["id"] == str(version_id)
    assert history.json()["course_run_publications"][0]["is_current"] is True


async def test_current_publication_marker_is_independent_per_course_run(
    homework_client: httpx.AsyncClient,
) -> None:
    created = await _create_homework(homework_client, request_number=821)
    homework_id = UUID(created["id"])
    first = await _create_version(
        homework_client,
        homework_id=homework_id,
        expected_revision=created["revision"],
        text="Version one",
        request_number=822,
    )
    second = await _create_version(
        homework_client,
        homework_id=homework_id,
        expected_revision=created["revision"] + 1,
        text="Version two",
        request_number=823,
    )
    first_id = UUID(first["id"])
    second_id = UUID(second["id"])
    await _publish_version(
        homework_client,
        version_id=first_id,
        course_run_id=COURSE_RUN_A,
        expected_revision=first["revision"],
        request_number=824,
    )
    await _publish_version(
        homework_client,
        version_id=second_id,
        course_run_id=COURSE_RUN_B,
        expected_revision=second["revision"],
        request_number=825,
    )
    await _publish_version(
        homework_client,
        version_id=second_id,
        course_run_id=COURSE_RUN_A,
        expected_revision=second["revision"],
        request_number=826,
    )

    history = await homework_client.get(f"/api/v1/homeworks/{homework_id}")
    assert history.status_code == 200, history.text
    _validate_component("HomeworkHistory", history.json())
    publications = history.json()["course_run_publications"]
    current_by_run = {
        item["course_run_id"]: item["homework_version_id"]
        for item in publications
        if item["is_current"]
    }
    assert current_by_run == {
        str(COURSE_RUN_A): str(second_id),
        str(COURSE_RUN_B): str(second_id),
    }
    first_a = [
        item
        for item in publications
        if item["course_run_id"] == str(COURSE_RUN_A)
        and item["homework_version_id"] == str(first_id)
    ]
    assert len(first_a) == 1 and first_a[0]["is_current"] is False


async def test_routes_reject_wrong_command_and_path_target(
    homework_client: httpx.AsyncClient,
) -> None:
    wrong_command = await homework_client.post(
        f"/api/v1/course-runs/{COURSE_RUN_A}/homeworks",
        json=_wire_command(
            command_name="create_homework_version",
            revision_target="homework",
            target_id=OTHER_TARGET_ID,
            expected_revision=0,
            payload=_version_payload(text="Wrong route"),
            request_number=831,
        ),
    )
    _assert_conflict(wrong_command)

    wrong_target = await homework_client.post(
        f"/api/v1/course-runs/{COURSE_RUN_A}/homeworks",
        json=_wire_command(
            command_name="create_homework",
            revision_target="course_run",
            target_id=COURSE_RUN_B,
            expected_revision=0,
            payload={"title": "Wrong target"},
            request_number=832,
        ),
    )
    _assert_conflict(wrong_target)

    for path, command_name, revision_target, payload, request_number in (
        (
            f"/api/v1/homeworks/{COURSE_ID}/versions",
            "create_homework_version",
            "homework",
            _version_payload(text="Wrong homework target"),
            833,
        ),
        (
            f"/api/v1/homework-versions/{COURSE_ID}/publish",
            "publish_homework_version",
            "homework_version",
            _publication_payload(COURSE_RUN_A),
            834,
        ),
    ):
        response = await homework_client.post(
            path,
            json=_wire_command(
                command_name=command_name,
                revision_target=revision_target,
                target_id=OTHER_TARGET_ID,
                expected_revision=0,
                payload=payload,
                request_number=request_number,
            ),
        )
        _assert_conflict(response)


async def test_stale_homework_revision_is_rejected_without_new_version(
    homework_client: httpx.AsyncClient,
) -> None:
    created = await _create_homework(homework_client, request_number=841)
    homework_id = UUID(created["id"])
    first = await _create_version(
        homework_client,
        homework_id=homework_id,
        expected_revision=created["revision"],
        text="Version one",
        request_number=842,
    )
    stale = await homework_client.post(
        f"/api/v1/homeworks/{homework_id}/versions",
        json=_wire_command(
            command_name="create_homework_version",
            revision_target="homework",
            target_id=homework_id,
            expected_revision=created["revision"],
            payload=_version_payload(text="Stale version"),
            request_number=843,
        ),
    )
    _assert_conflict(stale)

    history = await homework_client.get(f"/api/v1/homeworks/{homework_id}")
    assert history.status_code == 200
    _validate_component("HomeworkHistory", history.json())
    assert [item["id"] for item in history.json()["versions"]] == [first["id"]]
