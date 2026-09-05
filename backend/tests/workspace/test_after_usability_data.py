"""Regressions for observed UTF-8 artifact reading and active return deadlines."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.workspace.conftest import IDS, NOW
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open

from review_platform.application.workspace.student_works import student_homeworks
from review_platform.infrastructure.db.models import SubmissionVersion
from review_platform.infrastructure.db.models.workspace import ReviewOutcome, WorkspaceArtifact
from review_platform.infrastructure.object_storage.s3 import TenantObjectBoundaryError

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


@pytest.mark.parametrize("role", ["student", "reviewer", "methodologist"])
async def test_public_submission_sources_do_not_require_student_context(workspace_runtime, role):
    async with await client_for(workspace_runtime, role) as client:
        view = assert_ok(
            await client.get(f"/api/v2/course-run-homeworks/{IDS['publication']}/sources")
        )
        assert set(view) == {"allowed_sources"}
        assert set(view["allowed_sources"]) == {"upload", "github", "google_docs"}


class RecordingSigner:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, client_method, *, Params, ExpiresIn):
        self.calls.append((client_method, dict(Params), ExpiresIn))
        return "https://fixture.invalid/read-only-artifact"


@pytest.mark.parametrize("media_type", ["text/markdown", "text/plain", "application/pdf"])
async def test_v2_download_selects_utf8_header_from_stored_type(
    workspace_runtime, monkeypatch, media_type
):
    runtime = workspace_runtime
    storage = runtime.object_storage
    signer = RecordingSigner()
    monkeypatch.setattr(storage, "_signing_client", signer)
    identity = uuid4()
    content = (
        "Проверка обработки ошибок".encode() if media_type.startswith("text/") else b"%PDF-1.4\n"
    )
    stored = storage.upload(
        organization_id=str(IDS["org"]),
        artifact_version_id=str(identity),
        source=[content],
        media_type=media_type,
    )
    async with runtime.transaction() as session:
        session.add(
            WorkspaceArtifact(
                id=identity,
                organization_id=IDS["org"],
                owner_id=IDS["student"],
                filename="work.md" if media_type.startswith("text/") else "work.pdf",
                media_type=media_type,
                object_key=stored.key,
                digest=stored.content_digest,
                byte_size=stored.byte_size,
                private=False,
            )
        )
    before = dict(storage._client.objects)
    async with await client_for(runtime, "student") as client:
        assert_ok(await client.get(f"/api/v2/artifacts/{identity}/download"))
    method, params, ttl = signer.calls[-1]
    assert method == "get_object" and ttl == 900
    assert params["Key"] == stored.key
    if media_type.startswith("text/"):
        assert params["ResponseContentType"] == "text/plain; charset=utf-8"
    else:
        assert "ResponseContentType" not in params
    assert storage._client.objects == before
    calls = len(signer.calls)
    with pytest.raises(TenantObjectBoundaryError):
        storage.sign_read(
            organization_id=str(IDS["org"]),
            artifact_version_id=str(identity),
            requested_by_organization_id=str(uuid4()),
            key=stored.key,
            expires_in_seconds=900,
            response_content_type="text/plain; charset=utf-8",
        )
    assert len(signer.calls) == calls


async def test_return_deadline_only_for_published_latest_attempt(workspace_runtime, student):
    runtime = workspace_runtime
    sub, opened = await upload_and_open(runtime)
    due = NOW + timedelta(days=3)
    async with await client_for(runtime, "reviewer") as client:
        saved = assert_ok(
            await client.post(
                f"/api/v1/review-iterations/{opened['id']}/revisions",
                json={
                    **command(
                        "save_review_revision",
                        opened["id"],
                        {
                            "feedback": "Исправьте обработку ошибок.",
                            "criterion_decisions": [
                                {
                                    "criterion_id": str(IDS["criterion"]),
                                    "points": 4,
                                    "decision": "manual",
                                    "reason": "Нет проверки ошибок.",
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
        async with runtime.transaction() as session:
            session.add(
                ReviewOutcome(
                    id=UUID(opened["id"]),
                    organization_id=IDS["org"],
                    revision=0,
                    decision="needs_changes",
                    revision_deadline=due,
                    reason="Нужны исправления.",
                )
            )
        async with runtime.transaction() as session:
            pending = (await student_homeworks(session, student)).items[0]
            assert pending.revision_deadline is None
        assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/publish",
                json=command(
                    "publish_workspace_review",
                    opened["id"],
                    {"review_revision_id": saved["review_revision_id"], "apply_penalty": True},
                    saved["review_iteration_revision"],
                ),
            )
        )
    async with runtime.transaction() as session:
        returned = (await student_homeworks(session, student)).items[0]
        assert returned.status == "needs_changes" and returned.revision_deadline == due
        assert returned.submission_deadline == NOW + timedelta(days=7)
        original = await session.scalar(
            select(SubmissionVersion).where(SubmissionVersion.submission_id == UUID(sub["id"]))
        )
        values = {
            column.name: getattr(original, column.name)
            for column in SubmissionVersion.__table__.columns
        }
        values.update(id=uuid4(), sequence=2)
        session.add(SubmissionVersion(**values))
    async with runtime.transaction() as session:
        resubmitted = (await student_homeworks(session, student)).items[0]
        assert resubmitted.status == "pending_review" and resubmitted.attempt == 2
        assert resubmitted.revision_deadline is None
        assert resubmitted.submission_deadline == returned.submission_deadline
