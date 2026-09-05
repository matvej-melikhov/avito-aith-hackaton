"""Durable SQL self-review dispatch with immutable snapshots and safe replay.

The accepted run row is the outbox intent: it is committed together with quota
reservation. A worker claims it with SKIP LOCKED and a renewable lease. An
uncertain dispatch is looked up by stable run ID before a repeated submission.
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import httpx
from pydantic import JsonValue
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.workspace.common import WorkspaceFailure, digest, row
from review_platform.application.workspace.ports import (
    DefinitivePreparationFailure,
    PreparedBytes,
    StudentAIProvider,
)
from review_platform.application.workspace.self_review import SelfReviewService
from review_platform.contracts.workspace import (
    PublicCriterion,
    SelfReviewEvent,
    SelfReviewFinding,
    SelfReviewRequest,
    SelfReviewResult,
)
from review_platform.infrastructure.db.models import (
    CourseMembership,
    CourseRunHomework,
    OrganizationMembership,
)
from review_platform.infrastructure.db.models.workspace import (
    SelfReviewQuota,
    SelfReviewRun,
    WorkDraft,
    WorkspaceArtifact,
)
from review_platform.infrastructure.object_storage.s3 import S3ObjectStorage


class SelfReviewWorker:
    def __init__(
        self, runtime: FoundationRuntime, provider: StudentAIProvider, storage: S3ObjectStorage
    ):
        self.runtime, self.provider, self.storage = runtime, provider, storage

    async def tick(self) -> bool:
        now = self.runtime.clock()
        token = self.runtime.id_factory()
        async with self.runtime.transaction() as session:
            run = await session.scalar(
                select(SelfReviewRun)
                .where(
                    SelfReviewRun.disposition == "reserved",
                    or_(SelfReviewRun.lease_until.is_(None), SelfReviewRun.lease_until <= now),
                )
                .order_by(SelfReviewRun.created_at, SelfReviewRun.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if run is None:
                return False
            run_id, org = run.id, run.organization_id
            previously_dispatched = run.status in {"pending", "running", "unknown_outcome"}
            run.lease_token, run.lease_until = token, now + timedelta(seconds=60)
            if run.attempt == 0:
                run.attempt = 1
            if not previously_dispatched:
                run.status = "capturing"
            draft = await row(session, WorkDraft, org, run.draft_id)
            publication = await row(session, CourseRunHomework, org, draft.publication_id)
            membership = await session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == org,
                    OrganizationMembership.user_id == draft.student_id,
                    OrganizationMembership.status == "active",
                )
            )
            enrolled = await session.scalar(
                select(CourseMembership.id).where(
                    CourseMembership.organization_id == org,
                    CourseMembership.user_id == draft.student_id,
                    CourseMembership.course_run_id == publication.course_run_id,
                    CourseMembership.kind == "student",
                    CourseMembership.status == "active",
                )
            )
            if (
                membership is None
                or "student" not in membership.roles
                or enrolled is None
                or membership.revision != run.membership_revision
                or membership.auth_epoch != run.auth_epoch
            ):
                await self._release(session, run, "access_revoked")
                return True
            artifact_id, owner = run.artifact_id, draft.student_id
        try:
            if artifact_id is None:
                raise DefinitivePreparationFailure("Snapshot is missing")
            async with self.runtime.transaction() as session:
                current = await row(session, SelfReviewRun, org, run_id, lock=True)
                if current.lease_token != token or current.disposition != "reserved":
                    return True
                artifact = await row(session, WorkspaceArtifact, org, artifact_id)
                fingerprint = digest(
                    cast(
                        JsonValue,
                        {
                            "purpose": "student_self_review",
                            "org": str(org),
                            "owner": str(owner),
                            "draft": str(current.draft_id),
                            "draft_revision": current.draft_revision,
                            "homework_version": str(current.homework_version_id),
                            "artifact_digest": artifact.digest,
                            "criteria": current.criteria,
                            "contract_version": "2.0.0",
                        },
                    )
                )
                current.input_fingerprint = fingerprint
                request = SelfReviewRequest(
                    run_id=current.id,
                    attempt=current.attempt,
                    input_fingerprint=fingerprint,
                    artifact_id=artifact.id,
                    artifact_url=self.storage.sign_read(
                        key=artifact.object_key,
                        organization_id=str(org),
                        artifact_version_id=str(artifact.id),
                        requested_by_organization_id=str(org),
                        expires_in_seconds=900,
                    ),
                    artifact_digest=artifact.digest,
                    media_type=artifact.media_type,
                    student_text=current.student_text,
                    criteria=[PublicCriterion.model_validate(c) for c in current.criteria],
                )
                current.status = "pending"
            # An adapter MUST implement idempotent submit and lookup for stable run ID.
            event = (
                await self.provider.lookup(request)
                if previously_dispatched
                else await self.provider.submit(request)
            )
            if event:
                async with self.runtime.transaction() as session:
                    await SelfReviewService(self.runtime, session).accept(org, event)
            else:
                async with self.runtime.transaction() as session:
                    current = await row(session, SelfReviewRun, org, run_id, lock=True)
                    if current.lease_token == token and current.disposition == "reserved":
                        current.status = "running"
                        current.lease_until = self.runtime.clock() + timedelta(seconds=5)
        except DefinitivePreparationFailure as error:
            async with self.runtime.transaction() as session:
                current = await row(session, SelfReviewRun, org, run_id, lock=True)
                if current.lease_token == token and current.disposition == "reserved":
                    await self._release(session, current, error.code)
        except (OSError, TimeoutError, WorkspaceFailure, httpx.HTTPError):
            async with self.runtime.transaction() as session:
                current = await row(session, SelfReviewRun, org, run_id, lock=True)
                if current.lease_token == token and current.disposition == "reserved":
                    current.status = (
                        "unknown_outcome"
                        if previously_dispatched or current.input_fingerprint
                        else "capturing"
                    )
                    current.lease_until = self.runtime.clock() + timedelta(seconds=15)
        return True

    async def _release(self, session: AsyncSession, run: SelfReviewRun, code: str) -> None:
        quota = await row(session, SelfReviewQuota, run.organization_id, run.quota_id, lock=True)
        quota.reserved -= 1
        run.status = "failed"
        run.disposition = "released"
        run.error_code = code
        run.finished_at = self.runtime.clock()
        run.lease_until = None
        SelfReviewService(self.runtime, session).record(run, "release")


class FixtureSelfReviewProvider:
    """Explicit local adapter, never an implicit fallback for a live provider."""

    async def prepare(self, artifact_url: str) -> PreparedBytes:
        return PreparedBytes(
            b"# Local fixture\nThis is an explicitly simulated work snapshot.\n",
            "text/markdown",
            "fixture.md",
        )

    async def submit(self, request: SelfReviewRequest) -> SelfReviewEvent:
        from uuid import uuid5

        return SelfReviewEvent(
            contract_version="2.0.0",
            event_id=uuid5(request.run_id, "fixture-final"),
            run_id=request.run_id,
            attempt=request.attempt,
            sequence=1,
            input_fingerprint=request.input_fingerprint,
            status="succeeded",
            result=SelfReviewResult(
                findings=[
                    SelfReviewFinding(
                        criterion_id=c.id,
                        status="needs_attention",
                        feedback=(
                            f"Демо: проверьте выполнение требования «{c.title}». "
                            "Реальная модель не вызывалась."
                        ),
                    )
                    for c in request.criteria
                ]
            ),
        )

    async def lookup(self, request: SelfReviewRequest) -> SelfReviewEvent:
        return await self.submit(request)
