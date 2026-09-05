"""Private check metadata is version-bound and quality scoring needs a known gate."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from tests.workspace.conftest import IDS
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open

from review_platform.application.workspace.common import WorkspaceFailure
from review_platform.application.workspace.review_assist import ReviewAssistService
from review_platform.contracts.workspace import (
    CriterionSettings,
    EditorCriterion,
    ReviewAssistEvent,
    ReviewAssistResult,
    ReviewerSuggestion,
)
from review_platform.infrastructure.db.models.workspace import ReviewAssistRun


def test_score_increment_is_positive_without_forcing_binary_or_maximum():
    assert EditorCriterion(key="x", max_points=0, score_step=0.25).score_step == 0.25
    assert CriterionSettings().score_step == 0.5
    assert not CriterionSettings().evaluate_quality
    with pytest.raises(ValidationError):
        CriterionSettings(score_step=0)
    with pytest.raises(ValidationError):
        CriterionSettings(score_step=float("inf"))


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_metadata_roundtrip_private_projection_and_quality_gate(workspace_runtime):
    runtime = workspace_runtime
    runtime._settings = runtime.settings.model_copy(
        update={"workspace_enabled": True, "workspace_fixtures": True}
    )
    _, opened = await upload_and_open(runtime)
    payload = {"criterion_settings": {"api": {"score_step": 0.25, "evaluate_quality": True}}}
    async with await client_for(runtime, "methodologist") as client:
        invalid = await client.post(
            f"/api/v2/homework-versions/{IDS['version']}/private-details",
            json=command(
                "save_private_homework", IDS["version"], {"criterion_settings": {"missing": {}}}
            ),
        )
        assert invalid.status_code == 422 and invalid.json()["code"] == "unknown_criterion"
        saved = assert_ok(
            await client.post(
                f"/api/v2/homework-versions/{IDS['version']}/private-details",
                json=command("save_private_homework", IDS["version"], payload),
            )
        )
        assert saved["criterion_settings"] == payload["criterion_settings"]
        loaded = assert_ok(
            await client.get(f"/api/v2/homework-versions/{IDS['version']}/private-details")
        )
        assert loaded["criterion_settings"] == payload["criterion_settings"]
    async with await client_for(runtime, "student") as client:
        public = assert_ok(
            await client.get(f"/api/v2/course-run-homeworks/{IDS['publication']}/student-context")
        )
        assert "evaluate_quality" not in str(public) and "score_step" not in str(public)
    async with await client_for(runtime, "reviewer") as client:
        context = assert_ok(await client.get(f"/api/v2/reviews/{opened['id']}/context"))
        assert context["criteria"][0]["score_step"] == 0.25
        assert context["criteria"][0]["evaluate_quality"] is True
        started = assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/assist",
                json=command("start_review_assist", opened["id"], {}, opened["revision"]),
            )
        )
    async with runtime.transaction() as session:
        run = await session.get(ReviewAssistRun, UUID(started["id"]))
        assert run.inputs["criteria"][0]["score_step"] == 0.25
        assert run.inputs["criteria"][0]["evaluate_quality"] is True
        fingerprint, attempt = run.input_fingerprint, run.attempt
    for requirement_met, points in ((None, 5), (False, 5), (False, 0)):
        event = ReviewAssistEvent(
            contract_version="2.0.0",
            event_id=uuid4(),
            run_id=UUID(started["id"]),
            attempt=attempt,
            sequence=1,
            input_fingerprint=fingerprint,
            status="succeeded",
            result=ReviewAssistResult(
                suggestions=[
                    ReviewerSuggestion(
                        criterion_id=IDS["criterion"],
                        status="suggested",
                        proposed_points=points,
                        requirement_met=requirement_met,
                        reason="Fixture gate result",
                    )
                ]
            ),
        )
        async with runtime.transaction() as session:
            service = ReviewAssistService(runtime, session)
            if points:
                with pytest.raises(WorkspaceFailure, match="критериям"):
                    await service.accept(IDS["org"], event)
            else:
                accepted = await service.accept(IDS["org"], event)
                assert accepted.result.suggestions[0].proposed_points == 0
    async with await client_for(runtime, "reviewer") as client:
        changed = assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/requirements",
                json=command(
                    "add_review_requirement",
                    opened["id"],
                    {
                        "title": "Extra review requirement",
                        "description": "Local only",
                        "max_points": 2,
                    },
                    opened["revision"],
                ),
            )
        )
        copied = assert_ok(await client.get(f"/api/v2/reviews/{changed['id']}/context"))
        original = next(item for item in copied["criteria"] if item["key"] == "api")
        extra = next(item for item in copied["criteria"] if item["key"] != "api")
        assert original["score_step"] == 0.25 and original["evaluate_quality"] is True
        assert extra["score_step"] == 0.5 and extra["evaluate_quality"] is False

