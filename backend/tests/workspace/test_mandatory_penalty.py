import pytest
from tests.workspace.conftest import IDS
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


@pytest.mark.parametrize("legacy", [False, True])
async def test_policy_penalty_cannot_be_waived_and_conditions_are_postpublication(
    workspace_runtime, legacy
):
    runtime = workspace_runtime
    sub, opened = await upload_and_open(runtime, late=True)
    async with await client_for(runtime, "student") as client:
        # A submission exists, but there is no human publication yet.
        before = assert_ok(await client.get(f"/api/v2/submissions/{sub['id']}"))
        assert before["reviews"] == []
    async with await client_for(runtime, "reviewer") as client:
        saved = assert_ok(
            await client.post(
                f"/api/v1/review-iterations/{opened['id']}/revisions",
                json={
                    **command(
                        "save_review_revision",
                        opened["id"],
                        {
                            "feedback": "Checked",
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
        if legacy:
            published = await client.post(
                f"/api/v1/review-iterations/{opened['id']}/publish",
                json={
                    **command(
                        "publish_review",
                        opened["id"],
                        {"review_revision_id": saved["review_revision_id"]},
                        saved["review_iteration_revision"],
                    ),
                    "revision_target": "review_iteration",
                },
            )
            assert published.status_code == 202, published.text
        else:
            published = await client.post(
                f"/api/v2/reviews/{opened['id']}/publish",
                json=command(
                    "publish_workspace_review",
                    opened["id"],
                    {"review_revision_id": saved["review_revision_id"], "apply_penalty": False},
                    saved["review_iteration_revision"],
                ),
            )
            assert published.status_code == 200, published.text
            assert published.json()["grade"]["penalty"] == 4
        listed = assert_ok(await client.get("/api/v2/works"))["items"][0]
        assert listed["score"] == 4
    async with await client_for(runtime, "student") as client:
        after = assert_ok(await client.get(f"/api/v2/submissions/{sub['id']}"))
        review = after["reviews"][0]
        assert review["grade"]["raw_score"] == 8 and review["grade"]["final_score"] == 4
        assert review["criteria"][0]["description"] == "PRIVATE rubric"
        # PublicCriterion remains structurally incapable of exposing this field.
        from review_platform.contracts.workspace import PublicCriterion

        assert "description" not in PublicCriterion.model_fields
