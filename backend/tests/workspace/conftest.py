"""Isolated MySQL fixtures for additive workspace boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from testcontainers.mysql import MySqlContainer

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
    Organization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.workspace import PublicationPolicy
from review_platform.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client, S3ObjectStorage
from review_platform.settings import Settings

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
IDS = {
    name: UUID(f"00000000-0000-7000-8000-{i:012d}")
    for i, name in enumerate(
        (
            "org",
            "student",
            "reviewer",
            "methodologist",
            "course",
            "run",
            "homework",
            "version",
            "set",
            "criterion",
            "publication",
            "pubhistory",
        ),
        1,
    )
}


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: object) -> dict[str, object]:
        from typing import BinaryIO

        body = kwargs["Body"]
        data = body if isinstance(body, bytes) else cast(BinaryIO, body).read()
        self.objects[str(kwargs["Key"])] = data
        return {"ETag": "memory"}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        from io import BytesIO

        data = self.objects[str(kwargs["Key"])]
        return {"Body": BytesIO(data), "ContentLength": len(data), "ContentType": "text/markdown"}

    def generate_presigned_url(self, client_method: str, *, Params: object, ExpiresIn: int) -> str:
        return "https://fixture.invalid/artifact"

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.objects.pop(str(kwargs["Key"]), None)
        return {}


@pytest.fixture
async def workspace_runtime(mysql_container: MySqlContainer) -> AsyncIterator[FoundationRuntime]:
    engine = create_database_engine(mysql_container.get_connection_url())
    factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with session_scope(factory) as s:
        s.add(Organization(id=IDS["org"], slug="workspace", name="Workspace"))
        for role in ("student", "reviewer", "methodologist"):
            s.add(User(id=IDS[role], display_name=role))
        await s.flush()
        for role in ("student", "reviewer", "methodologist"):
            s.add(
                OrganizationMembership(
                    id=UUID(int=IDS[role].int + 100),
                    organization_id=IDS["org"],
                    user_id=IDS[role],
                    roles=[role],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                )
            )
        s.add(
            Course(
                id=IDS["course"],
                organization_id=IDS["org"],
                title="Course",
                description="",
                source_kind="standalone",
                status="active",
                revision=0,
            )
        )
        await s.flush()
        s.add(
            CourseRun(
                id=IDS["run"],
                organization_id=IDS["org"],
                course_id=IDS["course"],
                title="Run",
                timezone="UTC",
                status="active",
                revision=0,
            )
        )
        s.add(
            Homework(
                id=IDS["homework"],
                organization_id=IDS["org"],
                course_id=IDS["course"],
                title="Task",
                revision=0,
            )
        )
        await s.flush()
        for role in ("student", "reviewer"):
            s.add(
                CourseMembership(
                    id=UUID(int=IDS[role].int + 200),
                    organization_id=IDS["org"],
                    course_run_id=IDS["run"],
                    user_id=IDS[role],
                    kind=role,
                    status="active",
                    source="invitation",
                    joined_at=NOW,
                )
            )
        s.add(
            HomeworkVersion(
                id=IDS["version"],
                organization_id=IDS["org"],
                homework_id=IDS["homework"],
                version_number=1,
                revision=0,
                student_text="Public instructions",
                max_score=Decimal("10"),
                artifact_kinds=["github", "google_docs"],
                estimated_review_minutes=30,
            )
        )
        s.add(
            CourseRunHomework(
                id=IDS["publication"],
                organization_id=IDS["org"],
                course_run_id=IDS["run"],
                homework_id=IDS["homework"],
                status="active",
                revision=0,
            )
        )
        await s.flush()
        s.add(
            CriterionSet(
                id=IDS["set"], organization_id=IDS["org"], homework_version_id=IDS["version"]
            )
        )
        s.add(
            CourseRunHomeworkPublication(
                id=IDS["pubhistory"],
                organization_id=IDS["org"],
                course_run_homework_id=IDS["publication"],
                homework_id=IDS["homework"],
                homework_version_id=IDS["version"],
                publication_sequence=1,
                submission_deadline=NOW + timedelta(days=7),
                review_deadline=NOW + timedelta(days=10),
                published_at=NOW,
            )
        )
        await s.flush()
        publication = await s.get(CourseRunHomework, IDS["publication"])
        assert publication
        publication.current_publication_id = IDS["pubhistory"]
        s.add(
            Criterion(
                id=IDS["criterion"],
                organization_id=IDS["org"],
                criterion_set_id=IDS["set"],
                stable_key="api",
                position=1,
                title="API correctness",
                description="PRIVATE rubric",
                max_points=Decimal("10"),
                active=True,
            )
        )
        s.add(
            PublicationPolicy(
                id=IDS["publication"],
                organization_id=IDS["org"],
                revision=0,
                self_review_limit=1,
                policy={
                    "self_review_limit": 1,
                    "pass_score": 5,
                    "revision_days": 7,
                    "penalty_per_day": 0,
                    "max_resubmissions": 3,
                },
            )
        )
    runtime = FoundationRuntime(
        session_factory=factory,
        object_storage=S3ObjectStorage(
            client=cast(S3Client, MemoryStorage()), bucket="workspace", max_object_bytes=10_000_000
        ),
        settings=Settings(
            database_url=mysql_container.get_connection_url(),
            workspace_enabled=True,
            workspace_fixtures=True,
        ),
        engine=engine,
        clock=lambda: NOW,
    )
    yield runtime
    await runtime.close()


@pytest.fixture
def student() -> RequestActor:
    return RequestActor(
        organization_id=IDS["org"],
        actor_type="user",
        user_id=IDS["student"],
        roles=frozenset({"student"}),
        membership_revision=0,
        auth_epoch=0,
    )
