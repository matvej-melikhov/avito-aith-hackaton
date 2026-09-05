"""Read-model regressions for first participation deadlines, registry totals and appeals."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from tests.workspace.conftest import IDS, NOW
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open
from tests.workspace.test_student_works import add_assignment

from review_platform.application.workspace.ai_adapter import FixtureWorkspaceAI
from review_platform.application.workspace.insights import review_deadline, workspace_insights
from review_platform.application.workspace.projections import WorkspaceQueries
from review_platform.application.workspace.review_assist import ReviewAssistWorker
from review_platform.infrastructure.db.models import (
    ReviewIteration,
    ReviewResponsibility,
)


def test_two_weekdays_keep_local_time_across_dst():
    friday = datetime(2026, 3, 27, 16, tzinfo=UTC)
    assert review_deadline(friday, "Europe/Berlin") == datetime(2026, 3, 31, 15, tzinfo=UTC)
    assert review_deadline(datetime(2026, 9, 5, 12, tzinfo=UTC), "UTC") == datetime(
        2026, 9, 8, 12, tzinfo=UTC
    )


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_registry_union_counts_and_first_participation_clock(workspace_runtime, student):
    runtime = workspace_runtime
    sub, opened = await upload_and_open(runtime)
    coordinator = replace(student, user_id=IDS["methodologist"], roles=frozenset({"methodologist"}))
    async with runtime.transaction() as session:
        await add_assignment(session, "Draft only", IDS["student"])
        iteration = await session.get(ReviewIteration, UUID(opened["id"]))
        for reviewer, time in (
            (IDS["reviewer"], NOW),
            (IDS["methodologist"], NOW + timedelta(days=2)),
        ):
            session.add(
                ReviewResponsibility(
                    id=uuid4(),
                    organization_id=IDS["org"],
                    review_case_id=iteration.review_case_id,
                    review_iteration_id=iteration.id,
                    reviewer_id=reviewer,
                    actor_id=reviewer,
                    action="joined",
                    occurred_at=time,
                )
            )
    async with runtime.transaction() as session:
        query = WorkspaceQueries(runtime, session)
        first = await query.works(coordinator, limit=1)
        second = await query.works(coordinator, limit=1, offset=1)
        assert first.total == second.total == 2
        rows = first.items + second.items
        draft = next(item for item in rows if item.submission_id is None)
        work = next(item for item in rows if item.submission_id)
        assert draft.draft_id is not None and draft.submitted_at is None and draft.status == "draft"
        assert work.taken_at == NOW
        assert work.review_deadline == datetime(2026, 9, 8, 12, tzinfo=UTC)
        assert work.reviewer_name == "reviewer" and work.updated_at is not None
        assert (await query.works(coordinator, state="draft")).total == 1
        insights = await workspace_insights(runtime, session, coordinator, IDS["run"])
        assert insights.status_counts.all == 2 and insights.status_counts.draft == 1
        assert insights.status_counts.in_review == 1
        assert insights.pool_metrics.submitted == 1 and insights.pool_metrics.waiting == 0
        assert insights.pool_metrics.active_reviewers == 1
        reviewer = replace(student, user_id=IDS["reviewer"], roles=frozenset({"reviewer"}))
        active = await query.works(reviewer, view="active", limit=1)
        assert active.items[0].review_deadline == work.review_deadline
        assert (await query.works(coordinator, state="reviewing")).total == 1
        assert (
            await query.works(coordinator, search="Draft only", search_student_only=True)
        ).total == 0
        assert (
            await query.works(
                coordinator, search=str(IDS["student"])[-12:], search_student_only=True
            )
        ).total == 2
    runtime._clock = lambda: NOW + timedelta(days=5)
    async with runtime.transaction() as session:
        iteration = await session.get(ReviewIteration, UUID(opened["id"]))
        for reviewer_id in (IDS["reviewer"], IDS["methodologist"]):
            session.add(
                ReviewResponsibility(
                    id=uuid4(),
                    organization_id=IDS["org"],
                    review_case_id=iteration.review_case_id,
                    review_iteration_id=iteration.id,
                    reviewer_id=reviewer_id,
                    actor_id=reviewer_id,
                    action="released",
                    occurred_at=runtime.clock(),
                )
            )
    async with runtime.transaction() as session:
        stuck = await WorkspaceQueries(runtime, session).works(coordinator, view="pool", stuck=True)
        insight = await workspace_insights(runtime, session, coordinator, IDS["run"])
        assert stuck.total == insight.pool_metrics.stuck == insight.pool_metrics.waiting == 1


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_journal_uses_persisted_ai_human_requirement_and_publication_events(
    workspace_runtime, student
):
    runtime = workspace_runtime
    runtime._settings = runtime.settings.model_copy(
        update={"workspace_enabled": True, "workspace_fixtures": True}
    )
    _, opened = await upload_and_open(runtime)
    async with await client_for(runtime, "reviewer") as client:
        started = assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/assist",
                json=command("start_review_assist", opened["id"], {}, opened["revision"]),
            )
        )
        assert await ReviewAssistWorker(runtime, FixtureWorkspaceAI()).tick()
        saved = assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/draft",
                json=command(
                    "save_workspace_review",
                    opened["id"],
                    {
                        "ai_run_id": started["id"],
                        "draft": {
                            "feedback": "Human feedback",
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
                    },
                    opened["revision"],
                ),
            )
        )
        extra = assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/requirements",
                json=command(
                    "add_review_requirement",
                    opened["id"],
                    {"title": "Extra", "description": "Local", "max_points": 0},
                    saved["revision"],
                ),
            )
        )
        assert_ok(
            await client.post(
                f"/api/v2/reviews/{extra['id']}/outcome",
                json=command(
                    "save_review_outcome",
                    extra["id"],
                    {"decision": "passed", "reason": "Accepted"},
                    0,
                ),
            )
        )
        async with runtime.transaction() as session:
            current = await session.get(ReviewIteration, UUID(extra["id"]))
            revision_id, revision = current.current_revision_id, current.revision
        current_context = assert_ok(await client.get(f"/api/v2/reviews/{extra['id']}/context"))
        completed = assert_ok(
            await client.post(
                f"/api/v2/reviews/{extra['id']}/draft",
                json=command(
                    "save_workspace_review",
                    extra["id"],
                    {
                        "draft": {
                            "feedback": "Human feedback",
                            "criterion_decisions": [
                                {
                                    "criterion_id": criterion["id"],
                                    "points": 8 if criterion["key"] == "api" else 0,
                                    "decision": "manual",
                                    "reason": "Confirmed after adding requirement",
                                }
                                for criterion in current_context["criteria"]
                            ],
                            "review_notes": [],
                        }
                    },
                    revision,
                ),
            )
        )
        revision_id, revision = completed["id"], completed["revision"]
        assert_ok(
            await client.post(
                f"/api/v2/reviews/{extra['id']}/publish",
                json=command(
                    "publish_workspace_review",
                    extra["id"],
                    {"review_revision_id": str(revision_id), "apply_penalty": True},
                    revision,
                ),
            )
        )
        context = assert_ok(await client.get(f"/api/v2/reviews/{extra['id']}/context"))
        journal = context["decision_history"]
        assert any("Модель подготовила" in event["text"] for event in journal)
        assert any("5 → 8" in event["text"] and event["actor"] == "reviewer" for event in journal)
        assert any("Добавлено требование" in event["text"] for event in journal)
        assert any("опубликован студенту" in event["text"] for event in journal)
        assert [event["timestamp"] for event in journal] == sorted(
            event["timestamp"] for event in journal
        )
    coordinator = replace(student, user_id=IDS["methodologist"], roles=frozenset({"methodologist"}))
    async with runtime.transaction() as session:
        insights = await workspace_insights(runtime, session, coordinator, IDS["run"])
        assert insights.status_counts.passed == 1
        assert insights.typical_failures[0].failed == insights.typical_failures[0].reviewed == 1
        assert insights.typical_failures[0].ratio == 1
