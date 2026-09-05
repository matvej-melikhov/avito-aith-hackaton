from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from review_platform.application.workspace.ai_adapter import FixtureWorkspaceAI
from review_platform.application.workspace.common import WorkspaceFailure
from review_platform.application.workspace.local_fixture import fixture_id, seed
from review_platform.application.workspace.review_assist import (
    ReviewAssistService,
    ReviewAssistWorker,
)
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import ReviewIteration, Submission, SubmissionVersion
from review_platform.infrastructure.db.models.workspace import (
    ReviewAIChoice,
    ReviewAssistRun,
    SelfReviewQuota,
)
from review_platform.main import create_app
from review_platform.settings import Settings


def wire(name, target, payload, revision=0):
    return {
        "request_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "command_name": name,
        "target_id": str(target),
        "expected_revision": revision,
        "payload": payload,
    }


@pytest.mark.anyio
async def test_seed_refuses_existing_database(workspace_runtime):
    workspace_runtime._settings = Settings(
        environment="local", workspace_fixtures=True, workspace_enabled=True
    )
    with pytest.raises(RuntimeError, match="unrelated organizations"):
        await seed(workspace_runtime)


@pytest.mark.anyio
async def test_real_cookie_seed_and_reviewer_assist(workspace_runtime):
    runtime = workspace_runtime
    runtime._settings = Settings(
        environment="local", workspace_fixtures=True, workspace_enabled=True
    )
    async with runtime._engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    assert await seed(runtime)
    assert not await seed(runtime)
    async with runtime.transaction() as session:
        quotas = (await session.scalars(select(SelfReviewQuota))).all()
        assert len(quotas) == 4
        quotas[0].used = 1
        quota_id = quotas[0].id
    assert not await seed(runtime)
    async with runtime.transaction() as session:
        quota = await session.get(SelfReviewQuota, quota_id)
        assert quota is not None and quota.used == 1
    app = create_app(runtime.settings, runtime=runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post("/api/v1/auth/local/login", json={"identity": "reviewer-1"})
        assert login.status_code == 200, login.text
        assert "HttpOnly" in login.headers["set-cookie"]
        listing = await client.get("/api/v2/work")
        # Login is a genuine session resolved by SessionActorMiddleware.
        assert listing.status_code != 401
        async with runtime.transaction() as session:
            version = await session.scalar(select(SubmissionVersion))
            version_id = version.id
            submission = await session.get(Submission, version.submission_id)
            submission_id, submission_revision = submission.id, submission.revision
        # Open the real submitted work through the existing v2 route.
        opened = await client.post(
            f"/api/v2/submissions/{submission_id}/open-review",
            json=wire(
                "open_work",
                submission_id,
                {"submission_version_id": str(version_id)},
                submission_revision,
            ),
        )
        if opened.status_code == 404:
            pytest.fail("Review open route was not found")
        assert opened.status_code == 200, opened.text
        iteration_id = UUID(opened.json()["id"])
        async with runtime.transaction() as session:
            iteration = await session.get(ReviewIteration, iteration_id)
            current_revision = iteration.revision
        started = await client.post(
            f"/api/v2/reviews/{iteration_id}/assist",
            json=wire("start_review_assist", iteration_id, {}, current_revision),
        )
        assert started.status_code == 200, started.text
        run_id = UUID(started.json()["id"])

        class UncertainProvider(FixtureWorkspaceAI):
            submissions = 0
            lookups = 0
            event = None

            async def submit_assist(self, request):
                self.submissions += 1
                raise TimeoutError("response lost")

            async def lookup_assist(self, request):
                self.lookups += 1
                self.event = await FixtureWorkspaceAI.submit_assist(self, request)
                return self.event

        provider = UncertainProvider()
        assert await ReviewAssistWorker(runtime, provider).tick()
        async with runtime.transaction() as session:
            run = await session.get(ReviewAssistRun, run_id)
            assert run.status == "unknown_outcome"
            run.lease_until = None
        # A new worker instance recovers durable intent by lookup without resubmission.
        assert await ReviewAssistWorker(runtime, provider).tick()
        assert (provider.submissions, provider.lookups) == (1, 1)
        result = await client.get(f"/api/v2/review-assists/{run_id}")
        assert result.status_code == 200 and result.json()["status"] == "succeeded", result.text
        async with runtime.transaction() as session:
            service = ReviewAssistService(runtime, session)
            accepted = await service.accept(fixture_id("org"), provider.event)
            assert accepted.status == "succeeded"
            conflict = provider.event.model_copy(update={"sequence": 99})
            with pytest.raises(WorkspaceFailure, match="Содержимое"):
                await service.accept(fixture_id("org"), conflict)
        suggestions = result.json()["result"]["suggestions"]
        saved = await client.post(
            f"/api/v2/reviews/{iteration_id}/draft",
            json=wire(
                "save_workspace_review",
                iteration_id,
                {
                    "ai_run_id": str(run_id),
                    "draft": {
                        "feedback": "Проверено человеком.",
                        "criterion_decisions": [
                            {
                                "criterion_id": item["criterion_id"],
                                "points": item["proposed_points"],
                                "decision": "manual",
                                "reason": "Проверено",
                            }
                            for item in suggestions
                        ],
                        "review_notes": [],
                    },
                },
                current_revision,
            ),
        )
        assert saved.status_code == 200, saved.text
        async with runtime.transaction() as session:
            choice = await session.get(ReviewAIChoice, UUID(saved.json()["id"]))
            assert choice.run_id == run_id
        await client.post("/api/v1/auth/local/login", json={"identity": "student-2"})
        denied = await client.get(f"/api/v2/review-assists/{run_id}")
        assert denied.status_code == 403, denied.text
