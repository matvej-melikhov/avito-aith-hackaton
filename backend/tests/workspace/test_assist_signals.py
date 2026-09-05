"""Signals remain human metadata and cannot silently alter criterion scores."""

from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.workspace.conftest import IDS
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open

from review_platform.application.workspace.ai_adapter import FixtureWorkspaceAI
from review_platform.application.workspace.review_assist import ReviewAssistWorker
from review_platform.contracts.workspace import AssistEvidence, AuthorshipSignal
from review_platform.infrastructure.db.models import ReviewRevision
from review_platform.infrastructure.db.models.workspace import ReviewAIChoice


def test_provider_cannot_self_assert_verified_evidence():
    with pytest.raises(ValidationError):
        AssistEvidence(quote="Claimed quote", verified=True)
    with pytest.raises(ValidationError):
        AssistEvidence(quote="Claimed quote", line_start=5, line_end=2)
    assert AssistEvidence(quote="Claimed quote", path="work.md", line_start=1).verified is False
    with pytest.raises(ValidationError):
        AuthorshipSignal(id="signal", explanation="Claim", probability=1.1)


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_signal_decision_roundtrip_preserves_human_score(workspace_runtime):
    runtime = workspace_runtime
    runtime._settings = runtime.settings.model_copy(
        update={
            "workspace_enabled": True,
            "workspace_fixtures": True,
        }
    )
    _, opened = await upload_and_open(runtime)

    class SignalFixture(FixtureWorkspaceAI):
        async def submit_assist(self, request):
            event = await super().submit_assist(request)
            event.result.authorship_signal = AuthorshipSignal(
                id="fixture-authorship",
                probability=None,
                explanation="Тестовый сигнал, авторство не определялось.",
                evidence=[AssistEvidence(quote="Graded work", path="grade.md", line_start=1)],
            )
            event.result.feedback_draft = "Демонстрационный черновик ответа."
            return event

    async with await client_for(runtime, "reviewer") as client:
        started = assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/assist",
                json=command("start_review_assist", opened["id"], {}, opened["revision"]),
            )
        )
        assert await ReviewAssistWorker(runtime, SignalFixture()).tick()
        received = assert_ok(await client.get(f"/api/v2/review-assists/{started['id']}"))
        assert received["result"]["authorship_signal"]["probability"] is None
        assert received["result"]["authorship_signal"]["evidence"][0]["verified"] is False
        assert received["result"]["feedback_draft"] == "Демонстрационный черновик ответа."
        draft = {
            "feedback": "Human feedback",
            "criterion_decisions": [
                {
                    "criterion_id": str(IDS["criterion"]),
                    "points": 8,
                    "decision": "manual",
                    "reason": "Checked by a human",
                }
            ],
            "review_notes": [],
        }
        invalid = await client.post(
            f"/api/v2/reviews/{opened['id']}/draft",
            json=command(
                "save_workspace_review",
                opened["id"],
                {
                    "draft": draft,
                    "ai_run_id": started["id"],
                    "signal_decisions": {"invented-id": "confirm"},
                },
                opened["revision"],
            ),
        )
        assert invalid.status_code == 422 and invalid.json()["code"] == "unknown_signal"
        no_source = await client.post(
            f"/api/v2/reviews/{opened['id']}/draft",
            json=command(
                "save_workspace_review",
                opened["id"],
                {"draft": draft, "signal_decisions": {"fixture-authorship": "confirm"}},
                opened["revision"],
            ),
        )
        assert no_source.status_code == 422 and no_source.json()["code"] == "signal_source_required"
        revision = opened["revision"]
        for decision in ("confirm", "reject"):
            saved = assert_ok(
                await client.post(
                    f"/api/v2/reviews/{opened['id']}/draft",
                    json=command(
                        "save_workspace_review",
                        opened["id"],
                        {
                            "draft": draft,
                            "ai_run_id": started["id"],
                            "signal_decisions": {"fixture-authorship": decision},
                        },
                        revision,
                    ),
                )
            )
            revision = saved["revision"]
            context = assert_ok(await client.get(f"/api/v2/reviews/{opened['id']}/context"))
            assert context["signal_decisions"] == {"fixture-authorship": decision}
            assert context["ai_run_id"] == started["id"]
            async with runtime.transaction() as session:
                choice = await session.get(ReviewAIChoice, UUID(saved["id"]))
                review = await session.get(ReviewRevision, UUID(saved["id"]))
                assert choice.signal_decisions == {"fixture-authorship": decision}
                assert choice.run_id == UUID(started["id"])
                assert review.total_score == 8 and review.feedback == "Human feedback"
