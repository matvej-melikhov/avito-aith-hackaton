"""Offline immutable preparation and audience/revocation boundaries."""

from __future__ import annotations

import base64
from uuid import UUID

import pytest
from sqlalchemy import select
from tests.workspace.conftest import IDS
from tests.workspace.test_http import assert_ok, client_for, command

from review_platform.application.workspace.preparation import PreparationWorker
from review_platform.application.workspace.worker import FixtureSelfReviewProvider, SelfReviewWorker
from review_platform.infrastructure.db.models import (
    ArtifactVersion,
    OrganizationMembership,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import (
    ArtifactPreparation,
    SelfReviewQuota,
    SelfReviewRun,
    WorkspaceArtifact,
)

pytestmark = [pytest.mark.anyio, pytest.mark.infrastructure]


async def save_url(client, url, revision=0):
    return assert_ok(
        await client.post(
            f"/api/v2/course-run-homeworks/{IDS['publication']}/draft",
            json=command("save_work_draft", IDS["publication"], {"artifact_url": url}, revision),
        )
    )


async def prepare_url(runtime, client, draft):
    pending = assert_ok(
        await client.post(
            f"/api/v2/work-drafts/{draft['id']}/prepare",
            json=command("prepare_work_draft", draft["id"], {}, draft["revision"]),
        )
    )
    assert pending["status"] == "pending"
    assert await PreparationWorker(
        runtime, FixtureSelfReviewProvider(), runtime.object_storage
    ).tick()
    prepared = assert_ok(await client.get(f"/api/v2/preparations/{pending['id']}"))
    assert prepared["status"] == "succeeded"
    return prepared


@pytest.mark.parametrize("self_review", [False, True])
async def test_prepared_url_submits_identical_snapshot_with_optional_self_review(
    workspace_runtime, self_review
):
    runtime = workspace_runtime
    runtime._settings = runtime.settings.model_copy(
        update={"workspace_enabled": True, "workspace_fixtures": True}
    )
    async with await client_for(runtime, "student") as client:
        draft = await save_url(client, "https://github.com/example/first")
        prepared = await prepare_url(runtime, client, draft)
        if self_review:
            run = assert_ok(
                await client.post(
                    f"/api/v2/work-drafts/{draft['id']}/self-reviews",
                    json=command("start_self_review", draft["id"], {}, draft["revision"]),
                )
            )
            assert await SelfReviewWorker(
                runtime, FixtureSelfReviewProvider(), runtime.object_storage
            ).tick()
            result = assert_ok(await client.get(f"/api/v2/self-reviews/{run['id']}"))
            assert result["disposition"] == "consumed"
            async with runtime.transaction() as session:
                stored_run = await session.get(SelfReviewRun, UUID(run["id"]))
                assert stored_run.artifact_id == UUID(prepared["artifact_id"])
        submitted = assert_ok(
            await client.post(
                f"/api/v2/work-drafts/{draft['id']}/submit",
                json=command("submit_work_draft", draft["id"], {}, draft["revision"]),
            )
        )
        async with runtime.transaction() as session:
            version = await session.scalar(
                select(SubmissionVersion).where(
                    SubmissionVersion.submission_id == UUID(submitted["id"])
                )
            )
            snapshot = await session.get(WorkspaceArtifact, UUID(prepared["artifact_id"]))
            artifact = await session.get(ArtifactVersion, version.artifact_version_id)
            assert artifact.id == snapshot.id
            assert artifact.content_digest == snapshot.digest
            assert artifact.object_key == snapshot.object_key
            quota = await session.scalar(
                select(SelfReviewQuota).where(SelfReviewQuota.draft_id == UUID(draft["id"]))
            )
            assert quota.used == int(self_review) and quota.reserved == 0


async def test_changed_url_requires_new_snapshot_and_preserves_quota(workspace_runtime):
    runtime = workspace_runtime
    runtime._settings = runtime.settings.model_copy(
        update={"workspace_enabled": True, "workspace_fixtures": True}
    )
    async with await client_for(runtime, "student") as client:
        draft = await save_url(client, "https://github.com/example/first")
        first = await prepare_url(runtime, client, draft)
        run = assert_ok(
            await client.post(
                f"/api/v2/work-drafts/{draft['id']}/self-reviews",
                json=command("start_self_review", draft["id"], {}, draft["revision"]),
            )
        )
        updated = await save_url(client, "https://github.com/example/second", draft["revision"])
        assert await SelfReviewWorker(
            runtime, FixtureSelfReviewProvider(), runtime.object_storage
        ).tick()
        assert updated["id"] == draft["id"] and updated["revision"] > draft["revision"]
        old = assert_ok(await client.get(f"/api/v2/self-reviews/{run['id']}"))
        assert old["draft_revision"] == draft["revision"]
        assert old["artifact_id"] == first["artifact_id"]
        assert old["quota"]["used"] == 1 and old["quota"]["remaining"] == 0
        response = await client.post(
            f"/api/v2/work-drafts/{updated['id']}/submit",
            json=command("submit_work_draft", updated["id"], {}, updated["revision"]),
        )
        assert response.status_code == 409 and response.json()["code"] == "artifact_not_prepared"
        second = await prepare_url(runtime, client, updated)
        assert second["artifact_id"] != first["artifact_id"]
        assert_ok(
            await client.post(
                f"/api/v2/work-drafts/{updated['id']}/submit",
                json=command("submit_work_draft", updated["id"], {}, updated["revision"]),
            )
        )
        async with runtime.transaction() as session:
            version = await session.scalar(select(SubmissionVersion))
            assert str(version.artifact_version_id) == second["artifact_id"]
            quota = await session.scalar(select(SelfReviewQuota))
            assert quota.used == 1 and quota.reserved == 0


async def test_private_reference_denied_to_student_and_readable_by_scoped_reviewer(
    workspace_runtime,
):
    async with await client_for(workspace_runtime, "methodologist") as client:
        uploaded = []
        for private in (False, True):
            uploaded.append(
                assert_ok(
                    await client.post(
                        "/api/v2/uploads",
                        json=command(
                            "upload_artifact",
                            IDS["methodologist"],
                            {
                                "filename": "reference.md",
                                "media_type": "text/markdown",
                                "content_base64": base64.b64encode(b"Material").decode(),
                                "private": private,
                            },
                        ),
                    )
                )
            )
        public, reference = uploaded
        assert_ok(
            await client.post(
                f"/api/v2/homework-versions/{IDS['version']}/private-details",
                json=command(
                    "save_private_homework",
                    IDS["version"],
                    {
                        "reviewer_guidance": "Private grading guide",
                        "reference_upload_id": reference["id"],
                        "material_upload_ids": [public["id"]],
                        "criterion_classes": {},
                    },
                ),
            )
        )
    async with await client_for(workspace_runtime, "student") as client:
        assert (await client.get(f"/api/v2/artifacts/{reference['id']}/download")).status_code in (
            403,
            404,
        )
        assert_ok(await client.get(f"/api/v2/artifacts/{public['id']}/download"))
        context = assert_ok(
            await client.get(f"/api/v2/course-run-homeworks/{IDS['publication']}/student-context")
        )
        assert context["material_upload_ids"] == [public["id"]]
        assert reference["id"] not in str(context)
        assert "Private grading guide" not in str(context)
    async with await client_for(workspace_runtime, "reviewer") as client:
        assert_ok(await client.get(f"/api/v2/artifacts/{reference['id']}/download"))


async def test_prepare_rechecks_auth_epoch_after_provider_returns(workspace_runtime):
    runtime = workspace_runtime
    runtime._settings = runtime.settings.model_copy(update={"workspace_enabled": True})

    class RevokeDuringPreparation(FixtureSelfReviewProvider):
        async def prepare(self, artifact_url):
            async with runtime.transaction() as session:
                member = await session.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.user_id == IDS["student"]
                    )
                )
                member.auth_epoch += 1
            return await super().prepare(artifact_url)

    async with await client_for(runtime, "student") as client:
        draft = await save_url(client, "https://github.com/example/first")
        pending = assert_ok(
            await client.post(
                f"/api/v2/work-drafts/{draft['id']}/prepare",
                json=command("prepare_work_draft", draft["id"], {}, draft["revision"]),
            )
        )
    assert await PreparationWorker(
        runtime, RevokeDuringPreparation(), runtime.object_storage
    ).tick()
    async with runtime.transaction() as session:
        prepared = await session.get(ArtifactPreparation, UUID(pending["id"]))
        assert prepared.status == "failed" and prepared.error_code == "access_revoked"
        assert prepared.artifact_id is None


async def test_self_review_rechecks_auth_epoch_when_result_arrives(workspace_runtime):
    runtime = workspace_runtime
    runtime._settings = runtime.settings.model_copy(update={"workspace_enabled": True})

    class RevokeDuringSelfReview(FixtureSelfReviewProvider):
        async def submit(self, request):
            result = await super().submit(request)
            async with runtime.transaction() as session:
                member = await session.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.user_id == IDS["student"]
                    )
                )
                member.auth_epoch += 1
            return result

    async with await client_for(runtime, "student") as client:
        draft = await save_url(client, "https://github.com/example/first")
        await prepare_url(runtime, client, draft)
        started = assert_ok(
            await client.post(
                f"/api/v2/work-drafts/{draft['id']}/self-reviews",
                json=command("start_self_review", draft["id"], {}, draft["revision"]),
            )
        )
    assert await SelfReviewWorker(runtime, RevokeDuringSelfReview(), runtime.object_storage).tick()
    async with runtime.transaction() as session:
        run = await session.get(SelfReviewRun, UUID(started["id"]))
        assert run.status == "failed" and run.error_code == "access_revoked"
        assert run.disposition == "released" and run.result is None
        quota = await session.scalar(select(SelfReviewQuota))
        assert quota.used == 0 and quota.reserved == 0
