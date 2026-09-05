"""T115 RED acceptance: a human publishes while AI is entirely unavailable."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import Request, Response
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import RequestResponseEndpoint
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

from review_platform.application.foundation_runtime import build_foundation_runtime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import Organization, OrganizationMembership, User
from review_platform.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000001201")
USER = UUID("00000000-0000-7000-8000-000000001202")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000001203")
ITERATION = UUID("00000000-0000-7000-8000-000000001204")
CRITERION = UUID("00000000-0000-7000-8000-000000001205")


@pytest.fixture
async def human_review_client(
    mysql_container: MySqlContainer,
    redis_container: DockerContainer,
    minio_container: MinioContainer,
    uuid7_factory: Callable[[], UUID],
    fixed_clock: Callable[[], datetime],
) -> AsyncIterator[AsyncClient]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORG, slug="human-without-ai", name="Human Without AI"),
                User(id=USER, display_name="Human Reviewer"),
            ]
        )
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP,
                organization_id=ORG,
                user_id=USER,
                roles=["reviewer"],
                revision=0,
                auth_epoch=0,
            )
        )
    settings = Settings(
        environment="test",
        database_url=mysql_container.get_connection_url(),
        redis_url=(
            f"redis://{redis_container.get_container_host_ip()}:"
            f"{redis_container.get_exposed_port(6379)}/0"
        ),
    )
    runtime = build_foundation_runtime(
        settings,
        session_factory=factory,
        s3_client=cast(S3Client, minio_container.get_client()),
        id_factory=uuid7_factory,
        clock=fixed_clock,
    )
    app = create_app(settings, runtime=runtime)
    # Deliberately do not configure an AI provider, component credential, run,
    # or fallback. Human editing/publication must remain independent.
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles=["reviewer"],
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def inject_human(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield client
    await runtime.close()
    await engine.dispose()


def _command(
    *,
    name: str,
    expected_revision: int,
    payload: Mapping[str, Any],
    suffix: int,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{suffix:012d}",
        "idempotency_key": f"human-without-ai-{suffix:04d}",
        "command_name": name,
        "revision_target": "review_iteration",
        "target_id": str(ITERATION),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


async def test_human_can_save_and_publish_when_ai_component_is_unavailable(
    human_review_client: AsyncClient,
) -> None:
    saved = await human_review_client.post(
        f"/api/v1/review-iterations/{ITERATION}/revisions",
        json=_command(
            name="save_review_revision",
            expected_revision=0,
            payload={
                "feedback": "Human-only review",
                "criterion_decisions": [
                    {
                        "criterion_id": str(CRITERION),
                        "points": 5,
                        "decision": "manual",
                        "reason": "Reviewed directly by a human",
                        "evidence_ids": [],
                    }
                ],
                "review_notes": [],
            },
            suffix=1210,
        ),
    )
    assert saved.status_code == 201, (
        "US5 human save route is not implemented independently of AI; "
        f"received HTTP {saved.status_code}: {saved.text}"
    )
    revision_id = cast(str, saved.json()["review_revision_id"])

    published = await human_review_client.post(
        f"/api/v1/review-iterations/{ITERATION}/publish",
        json=_command(
            name="publish_review",
            expected_revision=saved.json()["review_iteration_revision"],
            payload={"review_revision_id": revision_id, "publication_request_id": None},
            suffix=1211,
        ),
    )
    assert published.status_code == 202, (
        "US5 human publication must not depend on AI availability; "
        f"received HTTP {published.status_code}: {published.text}"
    )
    assert published.json()["review_revision_id"] == revision_id
