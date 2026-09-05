from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.workspace.conftest import IDS

from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.catalog_editor import course_homeworks
from review_platform.application.workspace.common import WorkspaceFailure
from review_platform.contracts.workspace import CourseInput
from review_platform.infrastructure.db.models import Homework


def test_stepik_link_accepts_only_https_stepik_host():
    assert CourseInput(title="Course", stepik_url="https://stepik.org/course/12").stepik_url
    for url in (
        "http://stepik.org/course/12",
        "https://stepik.org.evil.test/12",
        "https://me@stepik.org/12",
    ):
        with pytest.raises(ValidationError):
            CourseInput(title="Course", stepik_url=url)


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_editor_catalog_keeps_unpublished_homework_and_enforces_role(workspace_runtime):
    actors = {
        role: RequestActor(
            organization_id=IDS["org"],
            actor_type="user",
            user_id=IDS[role],
            roles=frozenset({role}),
            membership_revision=0,
            auth_epoch=0,
        )
        for role in ("student", "methodologist")
    }
    identity = uuid4()
    async with workspace_runtime.transaction() as session:
        session.add(
            Homework(
                id=identity,
                organization_id=IDS["org"],
                course_id=IDS["course"],
                title="Unpublished draft",
                revision=0,
            )
        )
    async with workspace_runtime.transaction() as session:
        result = await course_homeworks(session, actors["methodologist"], IDS["course"])
        draft = next(item for item in result.items if item.id == identity)
        assert draft.latest_version_number is None
        assert draft.published_run_ids == []
        assert next(
            item for item in result.items if item.id == IDS["homework"]
        ).published_run_ids == [IDS["run"]]
        with pytest.raises(WorkspaceFailure) as denied:
            await course_homeworks(session, actors["student"], IDS["course"])
        assert denied.value.status == 403
        with pytest.raises(WorkspaceFailure) as missing:
            await course_homeworks(session, actors["methodologist"], uuid4())
        assert missing.value.status == 404


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_catalog_counts_only_active_reviewers_on_the_run(workspace_runtime):
    from sqlalchemy import select

    from review_platform.application.workspace.catalog import CatalogService
    from review_platform.infrastructure.db.models import CourseMembership

    actor = RequestActor(
        organization_id=IDS["org"],
        actor_type="user",
        user_id=IDS["methodologist"],
        roles=frozenset({"methodologist"}),
        membership_revision=0,
        auth_epoch=0,
    )
    async with workspace_runtime.transaction() as session:
        catalog = await CatalogService(workspace_runtime, session).catalog(actor)
        assert catalog.course_runs[0].reviewer_count == 1
        member = await session.scalar(
            select(CourseMembership).where(CourseMembership.user_id == IDS["reviewer"])
        )
        assert member is not None
        member.status = "removed"
    async with workspace_runtime.transaction() as session:
        catalog = await CatalogService(workspace_runtime, session).catalog(actor)
        assert catalog.course_runs[0].reviewer_count == 0
