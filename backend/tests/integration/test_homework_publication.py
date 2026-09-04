"""T067 RED integration contract for per-CourseRun homework publication history."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.base import RequestResponseEndpoint
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

from review_platform.application.foundation_runtime import build_foundation_runtime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    Course,
    CourseRun,
    Organization,
    OrganizationMembership,
    OutboxMessage,
    User,
)
from review_platform.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000701")
USER_ID = UUID("00000000-0000-7000-8000-000000000702")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000703")
COURSE_ID = UUID("00000000-0000-7000-8000-000000000704")
RUN_A_ID = UUID("00000000-0000-7000-8000-000000000705")
RUN_B_ID = UUID("00000000-0000-7000-8000-000000000706")


@dataclass(slots=True)
class HomeworkHarness:
    client: AsyncClient
    session_factory: Any


@pytest.fixture
async def homework_harness(
    mysql_container: MySqlContainer,
    redis_container: DockerContainer,
    minio_container: MinioContainer,
    uuid7_factory: Callable[[], UUID],
    fixed_clock: Callable[[], datetime],
) -> AsyncIterator[HomeworkHarness]:
    engine = create_database_engine(mysql_container.get_connection_url())
    await _reset_database(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        session.add_all(
            [
                Organization(
                    id=ORGANIZATION_ID,
                    slug="homework-publication-org",
                    name="Homework Publication Organization",
                ),
                User(id=USER_ID, display_name="Homework Methodologist"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP_ID,
                    organization_id=ORGANIZATION_ID,
                    user_id=USER_ID,
                    roles=["methodologist"],
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE_ID,
                    organization_id=ORGANIZATION_ID,
                    title="Shared Course",
                    description="Two independent CourseRuns",
                    source_kind="standalone",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=RUN_A_ID,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    external_run_id=None,
                    title="Run A",
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=RUN_B_ID,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    external_run_id=None,
                    title="Run B",
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
            ]
        )

    actor = RequestActor.user(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        roles=["methodologist"],
        membership_revision=0,
        auth_epoch=0,
    )
    settings = Settings(
        environment="test",
        database_url=mysql_container.get_connection_url(),
        redis_url=(
            f"redis://{redis_container.get_container_host_ip()}:"
            f"{redis_container.get_exposed_port(6379)}/0"
        ),
        s3_endpoint_url=(
            f"http://{minio_container.get_container_host_ip()}:"
            f"{minio_container.get_exposed_port(9000)}"
        ),
    )
    runtime = build_foundation_runtime(
        settings,
        session_factory=session_factory,
        s3_client=cast(S3Client, minio_container.get_client()),
        id_factory=uuid7_factory,
        clock=fixed_clock,
    )
    app = create_app(settings, runtime=runtime)

    @app.middleware("http")
    async def inject_methodologist(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield HomeworkHarness(client=client, session_factory=session_factory)
    await runtime.close()
    await _reset_database(engine)
    await engine.dispose()


async def _reset_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)


def _command(
    *,
    command_name: str,
    revision_target: str,
    target_id: UUID,
    expected_revision: int,
    payload: Mapping[str, Any],
    request_suffix: int,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request_suffix:012d}",
        "idempotency_key": f"homework-publication-{request_suffix:04d}",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


async def _post_created(
    client: AsyncClient,
    path: str,
    command: Mapping[str, Any],
    capability: str,
) -> dict[str, Any]:
    response = await client.post(path, json=command)
    assert response.status_code == 201, (
        f"US2 capability is not implemented: {capability}; "
        f"received HTTP {response.status_code}: {response.text}"
    )
    return cast(dict[str, Any], response.json())


async def test_two_course_runs_keep_independent_current_versions_and_append_only_history(
    homework_harness: HomeworkHarness,
) -> None:
    client = homework_harness.client
    homework = await _post_created(
        client,
        f"/api/v1/course-runs/{RUN_A_ID}/homeworks",
        _command(
            command_name="create_homework",
            revision_target="course_run",
            target_id=RUN_A_ID,
            expected_revision=0,
            payload={"title": "Independent publication fixture"},
            request_suffix=710,
        ),
        "create Homework in an exact CourseRun",
    )
    homework_id = UUID(homework["id"])

    version_ids: list[UUID] = []
    for number in range(1, 4):
        version = await _post_created(
            client,
            f"/api/v1/homeworks/{homework_id}/versions",
            _command(
                command_name="create_homework_version",
                revision_target="homework",
                target_id=homework_id,
                expected_revision=number - 1,
                payload={
                    "student_text": f"Requirements v{number}",
                    "max_score": 10,
                    "artifact_kinds": ["github"],
                    "estimated_review_minutes": 30,
                    "criteria": [
                        {
                            "key": "correctness",
                            "title": "Correctness",
                            "description": "Solution is correct",
                            "max_points": 10,
                        }
                    ],
                },
                request_suffix=710 + number,
            ),
            f"create immutable HomeworkVersion {number}",
        )
        version_ids.append(UUID(version["id"]))

    async def publish(
        version_id: UUID,
        course_run_id: UUID,
        request_suffix: int,
    ) -> dict[str, Any]:
        return await _post_created(
            client,
            f"/api/v1/homework-versions/{version_id}/publish",
            _command(
                command_name="publish_homework_version",
                revision_target="homework_version",
                target_id=version_id,
                expected_revision=0,
                payload={
                    "course_run_id": str(course_run_id),
                    "submission_deadline": "2026-09-10T12:00:00Z",
                    "review_deadline": "2026-09-12T12:00:00Z",
                },
                request_suffix=request_suffix,
            ),
            "publish an exact version inside one CourseRun",
        )

    await publish(version_ids[0], RUN_A_ID, 720)
    await publish(version_ids[1], RUN_B_ID, 721)

    initial_history = await client.get(f"/api/v1/homeworks/{homework_id}")
    assert initial_history.status_code == 200, initial_history.text
    initial_publications = initial_history.json()["course_run_publications"]
    initial_current = {
        UUID(publication["course_run_id"]): UUID(publication["homework_version_id"])
        for publication in initial_publications
        if publication["is_current"]
    }
    assert initial_current == {
        RUN_A_ID: version_ids[0],
        RUN_B_ID: version_ids[1],
    }

    await publish(version_ids[2], RUN_A_ID, 722)
    history = await client.get(f"/api/v1/homeworks/{homework_id}")
    assert history.status_code == 200, history.text
    body = history.json()
    assert [UUID(version["id"]) for version in body["versions"]] == version_ids
    publications = body["course_run_publications"]
    by_run = {
        run_id: [
            publication
            for publication in publications
            if UUID(publication["course_run_id"]) == run_id
        ]
        for run_id in (RUN_A_ID, RUN_B_ID)
    }
    assert [item["publication_sequence"] for item in by_run[RUN_A_ID]] == [1, 2]
    assert [item["publication_sequence"] for item in by_run[RUN_B_ID]] == [1]
    assert [item["is_current"] for item in by_run[RUN_A_ID]] == [False, True]
    assert [item["is_current"] for item in by_run[RUN_B_ID]] == [True]
    assert UUID(by_run[RUN_A_ID][-1]["homework_version_id"]) == version_ids[2]
    assert UUID(by_run[RUN_B_ID][-1]["homework_version_id"]) == version_ids[1]

    for run_id, expected_version_id in (
        (RUN_A_ID, version_ids[2]),
        (RUN_B_ID, version_ids[1]),
    ):
        listing = await client.get(f"/api/v1/course-runs/{run_id}/homeworks")
        assert listing.status_code == 200, listing.text
        assert UUID(listing.json()["items"][0]["current_version_id"]) == expected_version_id

    async with session_scope(homework_harness.session_factory) as session:
        events = (
            await session.execute(
                select(OutboxMessage)
                .where(
                    OutboxMessage.organization_id == ORGANIZATION_ID,
                    OutboxMessage.event_type == "HomeworkRequirementsChanged",
                )
                .order_by(OutboxMessage.created_at, OutboxMessage.message_id)
            )
        ).scalars().all()
        changed = [
            event
            for event in events
            if event.payload.get("course_run_id") == str(RUN_A_ID)
            and event.payload.get("previous_version_id") == str(version_ids[0])
            and event.payload.get("current_version_id") == str(version_ids[2])
        ]
        assert len(changed) == 1
        assert changed[0].payload["organization_id"] == str(ORGANIZATION_ID)
        assert changed[0].payload["homework_id"] == str(homework_id)
        assert changed[0].aggregate_id == homework_id
