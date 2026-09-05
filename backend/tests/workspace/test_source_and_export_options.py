from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.workspace.conftest import IDS
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open

from review_platform.application.workspace.exports import (
    csv_bytes,
    export_details,
    export_rows,
    xlsx_bytes,
)
from review_platform.contracts.workspace import ExportInput, WorkItem
from review_platform.infrastructure.db.models import SubmissionVersion
from review_platform.infrastructure.db.models.workspace import HomeworkPrivateDetails, WorkDraft

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


async def test_title_and_source_policy_are_server_enforced(workspace_runtime):
    runtime = workspace_runtime
    async with await client_for(runtime, "methodologist") as client:
        changed = assert_ok(
            await client.post(
                f"/api/v2/homeworks/{IDS['homework']}/title",
                json=command(
                    "update_workspace_homework", IDS["homework"], {"title": "Новое название"}, 0
                ),
            )
        )
        assert changed["revision"] == 1
        draft = assert_ok(
            await client.get(
                f"/api/v2/homeworks/{IDS['homework']}/editor-draft",
                params={"course_run_id": str(IDS["run"])},
            )
        )
        assert draft["homework_title"] == "Новое название" and draft["homework_revision"] == 1
        stale = await client.post(
            f"/api/v2/homeworks/{IDS['homework']}/title",
            json=command("update_workspace_homework", IDS["homework"], {"title": "Stale"}, 0),
        )
        assert stale.status_code == 409
        frozen = await client.post(
            f"/api/v2/homework-versions/{IDS['version']}/private-details",
            json=command(
                "save_private_homework", IDS["version"], {"allowed_sources": ["upload"]}, 0
            ),
        )
        assert frozen.status_code == 409, frozen.text
    async with runtime.transaction() as session:
        session.add(
            HomeworkPrivateDetails(
                id=IDS["version"],
                organization_id=IDS["org"],
                revision=0,
                allowed_sources=["upload"],
            )
        )
    blocked_draft = uuid4()
    async with runtime.transaction() as session:
        session.add(
            WorkDraft(
                id=blocked_draft,
                organization_id=IDS["org"],
                publication_id=IDS["publication"],
                student_id=IDS["student"],
                revision=0,
                artifact_url="https://github.com/example/repository",
                comment="",
            )
        )
    async with await client_for(runtime, "student") as client:
        denied = await client.post(
            f"/api/v2/course-run-homeworks/{IDS['publication']}/draft",
            json=command(
                "save_work_draft",
                IDS["publication"],
                {"artifact_url": "https://github.com/example/repository"},
                0,
            ),
        )
        assert denied.status_code == 422, denied.text
        assert denied.json()["code"] == "source_not_allowed"
        for suffix, name in [("prepare", "prepare_work_draft"), ("submit", "submit_work_draft")]:
            blocked = await client.post(
                f"/api/v2/work-drafts/{blocked_draft}/{suffix}",
                json=command(name, blocked_draft, {}, 0),
            )
            assert blocked.status_code == 422, blocked.text
            assert blocked.json()["code"] == "source_not_allowed"

        rename = await client.post(
            f"/api/v2/homeworks/{IDS['homework']}/title",
            json=command("update_workspace_homework", IDS["homework"], {"title": "Forbidden"}, 1),
        )
        assert rename.status_code == 403
    from review_platform.infrastructure.db.repositories.submissions import (
        SqlArtifactPreflightRepository,
    )

    async with runtime.transaction() as session:
        context = await SqlArtifactPreflightRepository().lock_context(
            IDS["org"], IDS["publication"], IDS["student"], expected_revision=0, transaction=session
        )
        assert context.allowed_artifact_kinds == ()


async def test_export_criteria_and_snapshot_match_published_attempt(workspace_runtime):
    runtime = workspace_runtime
    sub, opened = await upload_and_open(runtime)
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
        original = await session.scalar(
            select(SubmissionVersion).where(SubmissionVersion.submission_id == UUID(sub["id"]))
        )
        artifact_id = original.artifact_version_id
        new_id = uuid4()
        values = {
            column.name: getattr(original, column.name)
            for column in SubmissionVersion.__table__.columns
            if column.name not in {"id", "sequence", "created_at", "updated_at"}
        }
        session.add(SubmissionVersion(**values, id=new_id, sequence=2))
    async with await client_for(runtime, "methodologist") as client:
        data = assert_ok(
            await client.get("/api/v2/works", params={"homework_id": str(IDS["homework"])})
        )
    item = WorkItem.model_validate(data["items"][0])
    assert item.submission_version_id == new_id and item.attempt == 2
    async with runtime.transaction() as session:
        details = await export_details(
            session, IDS["org"], [item], "https://workspace.example.invalid"
        )
    options = ExportInput(
        course_run_id=IDS["run"],
        homework_id=IDS["homework"],
        audience="team",
        columns=["attempt", "criterion_points", "artifact_url"],
        format="csv",
    )
    rows = export_rows(options, [item], details)
    assert rows[1] == [
        1,
        8.0,
        f"https://workspace.example.invalid/api/v2/artifacts/{artifact_id}/open",
    ]
    assert "API correctness" in rows[0][1]
    assert "8.0" in csv_bytes(rows).decode("utf-8-sig")
    from io import BytesIO
    from zipfile import ZipFile

    with ZipFile(BytesIO(xlsx_bytes(rows))) as book:
        assert "<v>8.0</v>" in book.read("xl/worksheets/sheet1.xml").decode()


async def test_export_links_need_public_base_and_remain_team_only(workspace_runtime):
    from review_platform.application.workspace.exports import ExportWorker

    async with await client_for(workspace_runtime, "methodologist") as client:
        denied = await client.post(
            "/api/v2/exports",
            json=command(
                "create_export",
                IDS["run"],
                {
                    "course_run_id": str(IDS["run"]),
                    "audience": "students",
                    "columns": ["artifact_url"],
                    "format": "csv",
                },
            ),
        )
        assert denied.status_code == 422
        job = assert_ok(
            await client.post(
                "/api/v2/exports",
                json=command(
                    "create_export",
                    IDS["run"],
                    {
                        "course_run_id": str(IDS["run"]),
                        "audience": "team",
                        "columns": ["artifact_url"],
                        "format": "csv",
                    },
                ),
            )
        )
        assert await ExportWorker(workspace_runtime).tick()
        result = assert_ok(await client.get(f"/api/v2/exports/{job['id']}"))
        assert result["status"] == "failed" and result["error"] == "configuration_required"
