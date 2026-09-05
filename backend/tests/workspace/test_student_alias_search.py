"""Coordinator registries identify students without exposing personal names."""

from dataclasses import replace
from uuid import uuid4

import pytest
from tests.workspace.conftest import IDS
from tests.workspace.test_http import assert_ok, client_for, upload_and_open

from review_platform.application.workspace.search import workspace_search
from review_platform.infrastructure.db.models import (
    Course,
    ExternalIdentity,
    Homework,
    Organization,
    User,
)

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


async def test_identifiers_replace_names_and_search_stays_in_organization(
    workspace_runtime, student
):
    runtime = workspace_runtime
    _, opened = await upload_and_open(runtime)
    async with await client_for(runtime, "methodologist") as client:
        first = assert_ok(await client.get("/api/v2/works"))["items"][0]
        assert first["student_name"] == f"Студент {str(IDS['student'])[-12:]}"
        assert (
            assert_ok(await client.get("/api/v2/works", params={"q": str(IDS["student"])[-12:]}))[
                "total"
            ]
            == 1
        )
    other_org, other_course, other_homework = uuid4(), uuid4(), uuid4()
    async with runtime.transaction() as session:
        user = await session.get(User, IDS["student"])
        user.display_name = "Секретное Полное Имя"
        session.add(
            ExternalIdentity(
                id=uuid4(),
                user_id=user.id,
                provider="stepik",
                issuer="https://stepik.org",
                subject="987654321",
                status="active",
            )
        )
        session.add(Organization(id=other_org, slug="other-private", name="Other"))
        await session.flush()
        session.add(
            Course(
                id=other_course,
                organization_id=other_org,
                title="Other",
                description="",
                source_kind="standalone",
                status="active",
                revision=0,
            )
        )
        await session.flush()
        session.add(
            Homework(
                id=other_homework,
                organization_id=other_org,
                course_id=other_course,
                title="Task foreign",
                revision=0,
            )
        )
    async with await client_for(runtime, "methodologist") as client:
        hidden = assert_ok(await client.get("/api/v2/works", params={"q": "Секретное"}))
        assert hidden["total"] == 0
        for query in ("987654321", str(IDS["student"])):
            found = assert_ok(await client.get("/api/v2/works", params={"q": query}))
            assert found["total"] == 1
            assert found["items"][0]["student_name"] == "Студент 987654321"
            assert "Секретное" not in str(found)
        context = assert_ok(await client.get(f"/api/v2/reviews/{opened['id']}/context"))
        assert context["student_name"] == "Студент 987654321"
        assignments = assert_ok(await client.get(f"/api/v2/course-runs/{IDS['run']}/assignments"))
        assert assignments["items"][0]["student_name"] == "Студент 987654321"
        directory = assert_ok(await client.get("/api/v2/directory"))
        assert (
            next(member for member in directory["items"] if member["id"] == str(IDS["student"]))[
                "display_name"
            ]
            == "Студент 987654321"
        )
    coordinator = replace(student, user_id=IDS["methodologist"], roles=frozenset({"methodologist"}))
    async with runtime.transaction() as session:
        hidden = await workspace_search(runtime, session, coordinator, "Секретное")
        assert not hidden.students and not hidden.homeworks
        found = await workspace_search(runtime, session, coordinator, "987654321")
        assert len(found.students) == 1
        tasks = await workspace_search(runtime, session, coordinator, "Task")
        assert {homework.id for homework in tasks.homeworks} == {IDS["homework"]}
        foreign = await workspace_search(
            runtime, session, replace(coordinator, organization_id=other_org), "987654321"
        )
        assert not foreign.students and not foreign.homeworks
