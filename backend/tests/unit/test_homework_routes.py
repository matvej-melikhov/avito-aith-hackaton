from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from sqlalchemy import func, select
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models import (
    CommandReceipt,
    Course,
    CourseRun,
    Homework,
    OrganizationMembership,
    OutboxMessage,
    User,
)
from review_platform.infrastructure.db.models.homework import CourseRunHomeworkPublication
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000000951")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000000952")
COURSE = UUID("00000000-0000-7000-8000-000000000953")
RUN_A = UUID("00000000-0000-7000-8000-000000000954")
RUN_B = UUID("00000000-0000-7000-8000-000000000955")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _command(
    name: str,
    target: UUID,
    revision_target: str,
    expected_revision: int,
    payload: dict[str, object],
    *,
    suffix: int,
    key: str,
) -> dict[str, object]:
    return {
        "request_id": f"00000000-0000-7000-8000-{suffix:012d}",
        "idempotency_key": key,
        "command_name": name,
        "revision_target": revision_target,
        "target_id": str(target),
        "expected_revision": expected_revision,
        "payload": payload,
    }


def _validate(name: str, value: object) -> None:
    openapi = load_openapi()
    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": openapi["components"],
        }
    ).validate(value)


@pytest.fixture
async def client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[httpx.AsyncClient]:
    async with session_scope(foundation_session_factory) as session:
        session.add(User(id=USER, display_name="Homework Route User", status="active"))
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=USER,
                    roles=["methodologist"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Course",
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
                    id=RUN_A,
                    organization_id=ORG,
                    course_id=COURSE,
                    external_run_id="run-a",
                    title="Run A",
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=RUN_B,
                    organization_id=ORG,
                    course_id=COURSE,
                    external_run_id="run-b",
                    title="Run B",
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
            ]
        )
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"methodologist"},
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


async def test_full_route_round_trip_replay_two_runs_events_and_conflicts(
    client: httpx.AsyncClient,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    create = _command(
        "create_homework",
        RUN_A,
        "course_run",
        0,
        {"title": "Homework"},
        suffix=960,
        key="homework-create-route-0001",
    )
    first_create = await client.post(f"/api/v1/course-runs/{RUN_A}/homeworks", json=create)
    replay_create = await client.post(f"/api/v1/course-runs/{RUN_A}/homeworks", json=create)
    assert first_create.status_code == replay_create.status_code == 201
    assert first_create.json() == replay_create.json()
    _validate("CreatedResource", first_create.json())
    homework_id = UUID(first_create.json()["id"])

    def version_command(text: str, revision: int, suffix: int, key: str) -> dict[str, object]:
        return _command(
            "create_homework_version",
            homework_id,
            "homework",
            revision,
            {
                "student_text": text,
                "max_score": 10,
                "artifact_kinds": ["github"],
                "estimated_review_minutes": 20,
                "criteria": [
                    {
                        "key": "correctness",
                        "title": "Correctness",
                        "description": "Works",
                        "max_points": 10,
                    }
                ],
            },
            suffix=suffix,
            key=key,
        )

    v1_command = version_command("Version one", 0, 961, "homework-version-route-0001")
    v1 = await client.post(f"/api/v1/homeworks/{homework_id}/versions", json=v1_command)
    v1_replay = await client.post(f"/api/v1/homeworks/{homework_id}/versions", json=v1_command)
    v2 = await client.post(
        f"/api/v1/homeworks/{homework_id}/versions",
        json=version_command("Version two", 1, 962, "homework-version-route-0002"),
    )
    assert v1.status_code == v1_replay.status_code == v2.status_code == 201
    assert v1.json() == v1_replay.json()
    version_1, version_2 = UUID(v1.json()["id"]), UUID(v2.json()["id"])

    async def publish(version: UUID, run: UUID, suffix: int, key: str) -> httpx.Response:
        return await client.post(
            f"/api/v1/homework-versions/{version}/publish",
            json=_command(
                "publish_homework_version",
                version,
                "homework_version",
                0,
                {
                    "course_run_id": str(run),
                    "submission_deadline": "2026-09-10T12:00:00Z",
                    "review_deadline": "2026-09-12T12:00:00Z",
                },
                suffix=suffix,
                key=key,
            ),
        )

    p_a1 = await publish(version_1, RUN_A, 963, "homework-publish-a1-0001")
    p_a1_replay = await publish(version_1, RUN_A, 963, "homework-publish-a1-0001")
    p_b1 = await publish(version_1, RUN_B, 964, "homework-publish-b1-0001")
    p_a2 = await publish(version_2, RUN_A, 965, "homework-publish-a2-0001")
    assert {p_a1.status_code, p_a1_replay.status_code, p_b1.status_code, p_a2.status_code} == {201}
    assert p_a1.json() == p_a1_replay.json()

    history = await client.get(f"/api/v1/homeworks/{homework_id}")
    run_a = await client.get(f"/api/v1/course-runs/{RUN_A}/homeworks")
    run_b = await client.get(f"/api/v1/course-runs/{RUN_B}/homeworks")
    assert history.status_code == run_a.status_code == run_b.status_code == 200
    _validate("HomeworkHistory", history.json())
    _validate("HomeworkList", run_a.json())
    _validate("HomeworkList", run_b.json())
    assert run_a.json()["items"][0]["current_version_id"] == str(version_2)
    assert run_b.json()["items"][0]["current_version_id"] == str(version_1)
    assert sum(item["is_current"] for item in history.json()["course_run_publications"]) == 2

    wrong = await client.post(
        f"/api/v1/homeworks/{homework_id}/versions",
        json={**v1_command, "target_id": str(RUN_A)},
    )
    stale = await client.post(
        f"/api/v1/homeworks/{homework_id}/versions",
        json=version_command("stale", 0, 966, "homework-version-stale-0001"),
    )
    assert wrong.status_code == stale.status_code == 409
    _validate("ErrorObject", wrong.json())
    _validate("ErrorObject", stale.json())

    async with foundation_session_factory() as session:
        counts = {
            "homeworks": await session.scalar(select(func.count()).select_from(Homework)),
            "publications": await session.scalar(
                select(func.count()).select_from(CourseRunHomeworkPublication)
            ),
            "events": await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.event_type == "HomeworkRequirementsChanged")
            ),
            "receipts": await session.scalar(select(func.count()).select_from(CommandReceipt)),
        }
        events = (
            (
                await session.execute(
                    select(OutboxMessage)
                    .where(OutboxMessage.event_type == "HomeworkRequirementsChanged")
                    .order_by(OutboxMessage.created_at, OutboxMessage.message_id)
                )
            )
            .scalars()
            .all()
        )
    assert counts == {"homeworks": 1, "publications": 3, "events": 3, "receipts": 6}
    assert events[0].payload["previous_homework_version_id"] is None
    replacement = next(
        event for event in events if event.payload["current_homework_version_id"] == str(version_2)
    )
    assert replacement.payload["previous_homework_version_id"] == str(version_1)
    assert replacement.payload["course_run_id"] == str(RUN_A)
