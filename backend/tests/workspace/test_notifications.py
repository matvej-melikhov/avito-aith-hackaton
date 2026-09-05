"""In-app reminders follow participation and opt-ins, with durable deduplication."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.workspace.conftest import IDS, NOW
from tests.workspace.test_http import assert_ok, client_for, upload_and_open

from review_platform.application.workspace.notifications import NotificationWorker
from review_platform.infrastructure.db.models import (
    CourseMembership,
    CourseRunHomeworkPublication,
    ReviewIteration,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import (
    StudentReviewerAssignment,
    WorkspaceNotification,
    WorkspacePreferences,
)

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


def preferences(**changes):
    return {
        "course_run_ids": [str(IDS["run"])],
        "planned_minutes": 60,
        "until_at": (NOW + timedelta(days=7)).isoformat(),
        "show_pool": True,
        "notifications": {"deadline": True, "revision": True, "pool": False},
        **changes,
    }


async def test_deadline_and_revision_follow_participants_and_deduplicate(workspace_runtime):
    runtime = workspace_runtime
    sub, opened = await upload_and_open(runtime)
    async with runtime.transaction() as session:
        publication = await session.get(CourseRunHomeworkPublication, IDS["pubhistory"])
        publication.submission_deadline = NOW + timedelta(hours=6)
        publication.review_deadline = NOW + timedelta(hours=12)
        iteration = await session.get(ReviewIteration, UUID(opened["id"]))
        iteration.responsible_reviewer_id = IDS["methodologist"]
        session.add(
            StudentReviewerAssignment(
                id=uuid4(),
                organization_id=IDS["org"],
                course_run_id=IDS["run"],
                student_id=IDS["student"],
                reviewer_id=IDS["methodologist"],
                revision=0,
            )
        )
        session.add(
            CourseMembership(
                id=uuid4(),
                organization_id=IDS["org"],
                course_run_id=IDS["run"],
                user_id=IDS["methodologist"],
                kind="reviewer",
                status="active",
                source="invitation",
                joined_at=NOW,
            )
        )
        session.add(
            WorkspacePreferences(
                id=uuid4(),
                organization_id=IDS["org"],
                user_id=IDS["reviewer"],
                revision=0,
                settings=preferences(
                    show_pool=False,
                    absent_from=(NOW - timedelta(days=1)).isoformat(),
                    absent_until=(NOW + timedelta(days=1)).isoformat(),
                    notifications={"deadline": False, "revision": True, "pool": True},
                ),
            )
        )
    assert not await NotificationWorker(runtime).tick()
    async with await client_for(runtime, "reviewer") as client:
        assert assert_ok(await client.get("/api/v2/works?view=pool"))["total"] == 0
        assert assert_ok(await client.get("/api/v2/works?view=active"))["total"] == 1
    async with runtime.transaction() as session:
        saved = await session.scalar(select(WorkspacePreferences))
        saved.settings = {
            **saved.settings,
            "notifications": {"deadline": True, "revision": True, "pool": True},
        }
    worker = NotificationWorker(runtime)
    assert await worker.tick()
    assert not await worker.tick()  # per-instance 60-second throttle
    async with runtime.transaction() as session:
        notices = (await session.scalars(select(WorkspaceNotification))).all()
        assert len(notices) == 1 and notices[0].recipient_id == IDS["reviewer"]
        notices[0].read_at = NOW
        original_id = notices[0].id
    assert all(
        await asyncio.gather(NotificationWorker(runtime).tick(), NotificationWorker(runtime).tick())
    )  # overlapping restarted workers deduplicate in SQL
    async with runtime.transaction() as session:
        notices = (await session.scalars(select(WorkspaceNotification))).all()
        assert len(notices) == 1 and notices[0].id == original_id and notices[0].read_at == NOW
        previous = await session.scalar(
            select(SubmissionVersion).where(SubmissionVersion.submission_id == UUID(sub["id"]))
        )
        fields = {
            column.name: getattr(previous, column.name)
            for column in SubmissionVersion.__table__.columns
        }
        fields.update(id=uuid4(), sequence=2, submitted_at=NOW)
        session.add(SubmissionVersion(**fields))
    assert await NotificationWorker(runtime).tick()
    async with runtime.transaction() as session:
        notices = (await session.scalars(select(WorkspaceNotification))).all()
        assert len(notices) == 2
        assert {notice.recipient_id for notice in notices} == {IDS["reviewer"]}
        assert sum("новую версию" in notice.text for notice in notices) == 1


async def test_new_pool_is_opt_in_selected_available_and_recent(workspace_runtime):
    runtime = workspace_runtime
    await upload_and_open(runtime)
    async with runtime.transaction() as session:
        session.add(
            CourseMembership(
                id=uuid4(),
                organization_id=IDS["org"],
                course_run_id=IDS["run"],
                user_id=IDS["methodologist"],
                kind="reviewer",
                status="active",
                source="invitation",
                joined_at=NOW,
            )
        )
        session.add(
            WorkspacePreferences(
                id=uuid4(),
                organization_id=IDS["org"],
                user_id=IDS["methodologist"],
                revision=0,
                settings=preferences(),
            )
        )
    for settings in (
        preferences(),
        preferences(
            notifications={"deadline": False, "revision": False, "pool": True}, course_run_ids=[]
        ),
        preferences(
            notifications={"deadline": False, "revision": False, "pool": True}, show_pool=False
        ),
        preferences(
            notifications={"deadline": False, "revision": False, "pool": True},
            absent_from=(NOW - timedelta(days=1)).isoformat(),
            absent_until=(NOW + timedelta(days=1)).isoformat(),
        ),
    ):
        async with runtime.transaction() as session:
            saved = await session.scalar(select(WorkspacePreferences))
            saved.settings = settings
        assert not await NotificationWorker(runtime).tick()
    async with runtime.transaction() as session:
        saved = await session.scalar(select(WorkspacePreferences))
        saved.settings = preferences(
            notifications={"deadline": False, "revision": False, "pool": True}
        )
        version = await session.scalar(select(SubmissionVersion))
        version.submitted_at = NOW - timedelta(hours=25)
    assert not await NotificationWorker(runtime).tick()
    async with runtime.transaction() as session:
        version = await session.scalar(select(SubmissionVersion))
        version.submitted_at = NOW
    assert await NotificationWorker(runtime).tick()
    async with runtime.transaction() as session:
        notices = (await session.scalars(select(WorkspaceNotification))).all()
        assert len(notices) == 1 and notices[0].recipient_id == IDS["methodologist"]
        assert "пуле" in notices[0].text
