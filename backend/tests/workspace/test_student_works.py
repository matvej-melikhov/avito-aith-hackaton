"""Student cabinet pagination includes own drafts and published grading continuity."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.workspace.conftest import IDS, NOW
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open

from review_platform.application.workspace.student_works import student_homeworks
from review_platform.infrastructure.db.models import (
    CourseMembership,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Homework,
    HomeworkVersion,
    OrganizationMembership,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.models.workspace import WorkDraft

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


async def add_assignment(session, title, student_id=None):
    homework, version, relation, history = uuid4(), uuid4(), uuid4(), uuid4()
    session.add(
        Homework(
            id=homework,
            organization_id=IDS["org"],
            course_id=IDS["course"],
            title=title,
            revision=0,
        )
    )
    await session.flush()
    source = await session.get(HomeworkVersion, IDS["version"])
    fields = {
        column.name: getattr(source, column.name) for column in HomeworkVersion.__table__.columns
    }
    fields.update(id=version, homework_id=homework)
    session.add(HomeworkVersion(**fields))
    session.add(
        CourseRunHomework(
            id=relation,
            organization_id=IDS["org"],
            course_run_id=IDS["run"],
            homework_id=homework,
            status="active",
            revision=0,
        )
    )
    await session.flush()
    session.add(
        CourseRunHomeworkPublication(
            id=history,
            organization_id=IDS["org"],
            course_run_homework_id=relation,
            homework_id=homework,
            homework_version_id=version,
            publication_sequence=1,
            submission_deadline=NOW + timedelta(days=8),
            review_deadline=NOW + timedelta(days=10),
            published_at=NOW,
        )
    )
    await session.flush()
    row = await session.get(CourseRunHomework, relation)
    row.current_publication_id = history
    if student_id:
        session.add(
            WorkDraft(
                id=uuid4(),
                organization_id=IDS["org"],
                publication_id=relation,
                student_id=student_id,
                artifact_url="https://github.com/example/draft",
                comment="",
                revision=1,
            )
        )
    return relation


async def test_own_drafts_and_submissions_share_counted_filtered_pages(workspace_runtime, student):
    runtime = workspace_runtime
    sub, opened = await upload_and_open(runtime)
    other = uuid4()
    async with runtime.transaction() as session:
        session.add(User(id=other, display_name="Other student"))
        await session.flush()
        session.add(
            OrganizationMembership(
                id=uuid4(),
                organization_id=IDS["org"],
                user_id=other,
                roles=["student"],
                status="active",
                revision=0,
                auth_epoch=0,
            )
        )
        session.add(
            CourseMembership(
                id=uuid4(),
                organization_id=IDS["org"],
                course_run_id=IDS["run"],
                user_id=other,
                kind="student",
                status="active",
                source="invitation",
                joined_at=NOW,
            )
        )
        draft_relation = await add_assignment(session, "Own draft", IDS["student"])
        await add_assignment(session, "Untouched assignment")
        await add_assignment(session, "Foreign draft", other)
    async with runtime.transaction() as session:
        mixed = replace(student, roles=frozenset({"student", "methodologist"}))
        first = await student_homeworks(session, mixed, limit=1)
        second = await student_homeworks(session, mixed, offset=1, limit=1)
        assert first.total == second.total == 2
        assert first.items[0].submission_id == UUID(sub["id"])
        assert second.items[0].publication_id == draft_relation
        assert second.items[0].draft_id and second.items[0].status == "draft"
        assert (await student_homeworks(session, mixed, state="in_progress")).total == 2
        assert (await student_homeworks(session, mixed, state="completed")).total == 0
        foreign = await student_homeworks(session, replace(student, user_id=other))
        assert foreign.total == 1 and foreign.items[0].title == "Foreign draft"
    async with await client_for(runtime, "reviewer") as client:
        saved = assert_ok(
            await client.post(
                f"/api/v1/review-iterations/{opened['id']}/revisions",
                json={
                    **command(
                        "save_review_revision",
                        opened["id"],
                        {
                            "feedback": "Published",
                            "criterion_decisions": [
                                {
                                    "criterion_id": str(IDS["criterion"]),
                                    "points": 8,
                                    "decision": "manual",
                                    "reason": "Checked",
                                }
                            ],
                            "review_notes": [],
                        },
                        opened["revision"],
                    ),
                    "revision_target": "review_iteration",
                },
            )
        )
        assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/publish",
                json=command(
                    "publish_workspace_review",
                    opened["id"],
                    {"review_revision_id": saved["review_revision_id"], "apply_penalty": False},
                    saved["review_iteration_revision"],
                ),
            )
        )
    async with runtime.transaction() as session:
        completed = await student_homeworks(session, student, state="completed", limit=1)
        assert completed.total == 1 and completed.items[0].score == 8
        assert (await student_homeworks(session, student, state="in_progress")).total == 1
        previous = await session.scalar(
            select(SubmissionVersion).where(SubmissionVersion.submission_id == UUID(sub["id"]))
        )
        fields = {
            column.name: getattr(previous, column.name)
            for column in SubmissionVersion.__table__.columns
        }
        fields.update(id=uuid4(), sequence=2)
        session.add(SubmissionVersion(**fields))
    async with runtime.transaction() as session:
        listed = await student_homeworks(session, student)
        submitted = next(item for item in listed.items if item.submission_id)
        assert (
            submitted.status == "pending_review" and submitted.score == 8 and submitted.attempt == 2
        )
        assert (await student_homeworks(session, student, state="completed")).total == 0
        member = await session.scalar(
            select(CourseMembership).where(CourseMembership.user_id == IDS["student"])
        )
        member.status = "removed"
    async with runtime.transaction() as session:
        assert (await student_homeworks(session, student)).total == 0
