from __future__ import annotations

import base64
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tests.workspace.conftest import IDS, NOW

from review_platform.application.workspace.worker import FixtureSelfReviewProvider, SelfReviewWorker
from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.models import Session
from review_platform.main import create_app

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


async def client_for(runtime, role):
    secret = ("workspace-" + role + "-") * 4
    async with runtime.transaction() as s:
        existing = await s.scalar(
            select(Session).where(Session.token_digest == sha256_digest(secret))
        )
        if not existing:
            s.add(
                Session(
                    id=uuid4(),
                    organization_id=IDS["org"],
                    user_id=IDS[role],
                    membership_id=UUID(int=IDS[role].int + 100),
                    membership_revision=0,
                    auth_epoch=0,
                    token_digest=sha256_digest(secret),
                    expires_at=NOW + timedelta(hours=1),
                    status="active",
                )
            )
    app = create_app(runtime=runtime)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")
    client.cookies.set("review_session", secret)
    return client


def command(name, target, payload, revision=0):
    return {
        "request_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "command_name": name,
        "target_id": str(target),
        "expected_revision": revision,
        "payload": payload,
    }


def assert_ok(response):
    assert response.status_code < 300, response.text
    return response.json()


async def test_cookie_actor_and_typed_student_context(workspace_runtime):
    async with await client_for(workspace_runtime, "student") as c:
        current = assert_ok(await c.get("/api/v1/session"))
        assert current["user_id"] == str(IDS["student"])
        context = assert_ok(
            await c.get(f"/api/v2/course-run-homeworks/{IDS['publication']}/student-context")
        )
        assert context["quota"]["remaining"] == 1
        assert "PRIVATE" not in str(context)
        assert (await c.get("/api/v2/directory")).status_code == 403
        assert (
            await c.post(
                "/api/v2/courses", json=command("create_course", IDS["org"], {"title": "bad"})
            )
        ).status_code == 403


async def test_full_upload_self_review_then_human_open(workspace_runtime):
    async with await client_for(workspace_runtime, "student") as c:
        uploaded = assert_ok(
            await c.post(
                "/api/v2/uploads",
                json=command(
                    "upload_artifact",
                    IDS["student"],
                    {
                        "filename": "work.md",
                        "media_type": "text/markdown",
                        "content_base64": base64.b64encode(b"# Work\nPublic solution").decode(),
                        "private": False,
                    },
                ),
            )
        )
        draft = assert_ok(
            await c.post(
                f"/api/v2/course-run-homeworks/{IDS['publication']}/draft",
                json=command(
                    "save_work_draft",
                    IDS["publication"],
                    {"artifact_url": "", "upload_id": uploaded["id"], "comment": "My work"},
                ),
            )
        )
        start = command("start_self_review", draft["id"], {}, draft["revision"])
        run = assert_ok(await c.post(f"/api/v2/work-drafts/{draft['id']}/self-reviews", json=start))
        replayed = assert_ok(
            await c.post(f"/api/v2/work-drafts/{draft['id']}/self-reviews", json=start)
        )
        assert run["id"] == replayed["id"]
        assert await SelfReviewWorker(
            workspace_runtime, FixtureSelfReviewProvider(), workspace_runtime.object_storage
        ).tick()
        result = assert_ok(await c.get(f"/api/v2/self-reviews/{run['id']}"))
        assert result["disposition"] == "consumed"
        assert result["quota"]["remaining"] == 0
        submitted = assert_ok(
            await c.post(
                f"/api/v2/work-drafts/{draft['id']}/submit-upload",
                json=command("submit_uploaded_draft", draft["id"], {}, draft["revision"]),
            )
        )
        detail = assert_ok(await c.get(f"/api/v2/submissions/{submitted['id']}"))
        assert len(detail["attempts"]) == 1 and not detail["reviews"]
    async with await client_for(workspace_runtime, "reviewer") as c:
        works = assert_ok(await c.get("/api/v2/works"))
        assert works["total"] == 1
        item = works["items"][0]
        opened = assert_ok(
            await c.post(
                f"/api/v2/submissions/{submitted['id']}/open-review",
                json=command(
                    "open_work",
                    submitted["id"],
                    {"submission_version_id": item["submission_version_id"]},
                    item["submission_revision"],
                ),
            )
        )
        context = assert_ok(await c.get(f"/api/v2/reviews/{opened['id']}/context"))
        assert len(context["self_reviews"]) == 1
        assert context["criteria"][0]["description"] == "PRIVATE rubric"


async def test_coordinator_creates_course_and_run(workspace_runtime):
    async with await client_for(workspace_runtime, "methodologist") as c:
        course = assert_ok(
            await c.post(
                "/api/v2/courses",
                json=command(
                    "create_course",
                    IDS["org"],
                    {
                        "title": "New course",
                        "description": "Description",
                        "owner_id": str(IDS["methodologist"]),
                    },
                ),
            )
        )
        run = assert_ok(
            await c.post(
                f"/api/v2/courses/{course['id']}/course-runs",
                json=command(
                    "create_course_run",
                    course["id"],
                    {
                        "title": "Autumn",
                        "starts_at": NOW.isoformat(),
                        "ends_at": (NOW + timedelta(days=90)).isoformat(),
                        "timezone": "UTC",
                    },
                ),
            )
        )
        catalog = assert_ok(await c.get("/api/v2/catalog"))
        assert any(
            v["id"] == course["id"] and v["owner_id"] == str(IDS["methodologist"])
            for v in catalog["courses"]
        )
        assert any(v["id"] == run["id"] for v in catalog["course_runs"])


async def test_mutation_rejects_cross_origin(workspace_runtime):
    async with await client_for(workspace_runtime, "student") as c:
        response = await c.post(
            f"/api/v2/course-run-homeworks/{IDS['publication']}/draft",
            headers={"Origin": "https://evil.invalid"},
            json=command(
                "save_work_draft",
                IDS["publication"],
                {"artifact_url": "https://github.com/example/demo"},
            ),
        )
        assert response.status_code == 403


async def upload_and_open(runtime, *, late=False):
    from review_platform.infrastructure.db.models import CourseRunHomeworkPublication
    from review_platform.infrastructure.db.models.workspace import PublicationPolicy

    if late:
        async with runtime.transaction() as s:
            pub = await s.get(CourseRunHomeworkPublication, IDS["pubhistory"])
            pub.submission_deadline = NOW - timedelta(days=2)
            policy = await s.get(PublicationPolicy, IDS["publication"])
            policy.policy = {**policy.policy, "penalty_per_day": 2}
    async with await client_for(runtime, "student") as c:
        uploaded = assert_ok(
            await c.post(
                "/api/v2/uploads",
                json=command(
                    "upload_artifact",
                    IDS["student"],
                    {
                        "filename": "grade.md",
                        "media_type": "text/markdown",
                        "content_base64": base64.b64encode(b"# Graded work").decode(),
                        "private": False,
                    },
                ),
            )
        )
        d = assert_ok(
            await c.post(
                f"/api/v2/course-run-homeworks/{IDS['publication']}/draft",
                json=command(
                    "save_work_draft",
                    IDS["publication"],
                    {
                        "artifact_url": "",
                        "upload_id": uploaded["id"],
                        "comment": "Keep this comment",
                    },
                ),
            )
        )
        sub = assert_ok(
            await c.post(
                f"/api/v2/work-drafts/{d['id']}/submit-upload",
                json=command("submit_uploaded_draft", d["id"], {}, d["revision"]),
            )
        )
    async with await client_for(runtime, "reviewer") as c:
        work = assert_ok(await c.get("/api/v2/works"))["items"][0]
        opened = assert_ok(
            await c.post(
                f"/api/v2/submissions/{sub['id']}/open-review",
                json=command(
                    "open_work",
                    sub["id"],
                    {"submission_version_id": work["submission_version_id"]},
                    work["submission_revision"],
                ),
            )
        )
    return sub, opened


async def test_published_grade_is_shared_by_student_registry_and_export(workspace_runtime):
    from review_platform.application.workspace.exports import export_rows
    from review_platform.contracts.workspace import ExportInput, WorkItem

    sub, opened = await upload_and_open(workspace_runtime, late=True)
    async with await client_for(workspace_runtime, "reviewer") as c:
        saved = assert_ok(
            await c.post(
                f"/api/v1/review-iterations/{opened['id']}/revisions",
                json={
                    **command(
                        "save_review_revision",
                        opened["id"],
                        {
                            "feedback": "Final feedback",
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
        preview = assert_ok(await c.get(f"/api/v2/reviews/{opened['id']}/grade-preview"))
        assert preview["raw_score"] == 8 and preview["final_score"] == 4
        publication = assert_ok(
            await c.post(
                f"/api/v2/reviews/{opened['id']}/publish",
                json=command(
                    "publish_workspace_review",
                    opened["id"],
                    {"review_revision_id": saved["review_revision_id"], "apply_penalty": True},
                    saved["review_iteration_revision"],
                ),
            )
        )
        assert publication["grade"]["final_score"] == 4
        listed = assert_ok(await c.get("/api/v2/works"))["items"][0]
        assert listed["score"] == 4
        exported = export_rows(
            ExportInput(course_run_id=IDS["run"], audience="team", columns=["score"], format="csv"),
            [WorkItem.model_validate(listed)],
        )
        assert exported[1] == [4]
    async with await client_for(workspace_runtime, "student") as c:
        detail = assert_ok(await c.get(f"/api/v2/submissions/{sub['id']}"))
        assert detail["reviews"][0]["score"] == 4
        assert detail["reviews"][0]["grade"]["penalty"] == 4
        assert detail["attempts"][0]["comment"] == "Keep this comment"


async def test_extra_requirement_is_local_to_one_review(workspace_runtime):
    sub, opened = await upload_and_open(workspace_runtime)
    async with await client_for(workspace_runtime, "reviewer") as c:
        extra = assert_ok(
            await c.post(
                f"/api/v2/reviews/{opened['id']}/requirements",
                json=command(
                    "add_review_requirement",
                    opened["id"],
                    {
                        "title": "Explain tradeoffs",
                        "description": "Review-specific requirement",
                        "max_points": 2,
                    },
                    opened["revision"],
                ),
            )
        )
        context = assert_ok(await c.get(f"/api/v2/reviews/{extra['id']}/context"))
        assert len(context["criteria"]) == 2 and context["max_score"] == 12
        history = assert_ok(await c.get(f"/api/v1/homeworks/{IDS['homework']}"))
        assert len(history["versions"]) == 1
    async with await client_for(workspace_runtime, "student") as c:
        context = assert_ok(
            await c.get(f"/api/v2/course-run-homeworks/{IDS['publication']}/student-context")
        )
        assert len(context["criteria"]) == 1


async def test_editor_reads_saved_draft_without_changing_published_only_v1(workspace_runtime):
    _, opened = await upload_and_open(workspace_runtime)
    async with await client_for(workspace_runtime, "reviewer") as c:
        saved = assert_ok(
            await c.post(
                f"/api/v2/reviews/{opened['id']}/draft",
                json=command(
                    "save_workspace_review",
                    opened["id"],
                    {
                        "draft": {
                            "feedback": "Saved human feedback",
                            "criterion_decisions": [
                                {
                                    "criterion_id": str(IDS["criterion"]),
                                    "points": 8,
                                    "decision": "manual",
                                    "reason": "Checked implementation",
                                }
                            ],
                            "review_notes": [],
                        }
                    },
                    opened["revision"],
                ),
            )
        )
        draft = assert_ok(await c.get(f"/api/v2/reviews/{opened['id']}/draft"))
        assert draft["current_review_revision_id"] == saved["id"]
        assert draft["current_review_revision"]["feedback"] == "Saved human feedback"
        assert draft["criterion_decisions"][0]["points"] == 8
        assert draft["revision"] == saved["revision"]
        published = assert_ok(await c.get(f"/api/v1/review-iterations/{opened['id']}"))
        assert published["current_review_revision_id"] is None
    async with await client_for(workspace_runtime, "student") as c:
        assert (await c.get(f"/api/v2/reviews/{opened['id']}/draft")).status_code == 403
