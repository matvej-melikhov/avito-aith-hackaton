"""Transactional student drafts, finite quota and exactly-once result accounting."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    digest,
    require_roles,
    revision,
    row,
    student_epoch_valid,
)
from review_platform.application.workspace.preparation import prepared_artifact
from review_platform.contracts.workspace import (
    DraftInput,
    DraftView,
    PublicationPolicyInput,
    QuotaView,
    SelfReviewEvent,
    SelfReviewResult,
    SelfReviewView,
)
from review_platform.infrastructure.db.models import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.workspace import (
    PublicationPolicy,
    SelfReviewEventReceipt,
    SelfReviewQuota,
    SelfReviewQuotaEvent,
    SelfReviewRun,
    WorkDraft,
    WorkspaceArtifact,
)


class SelfReviewService:
    def __init__(self, runtime: FoundationRuntime, session: AsyncSession):
        self.runtime, self.session = runtime, session

    async def policy(
        self, org: UUID, publication_id: UUID, *, lock: bool = False
    ) -> PublicationPolicy:
        query = select(PublicationPolicy).where(
            PublicationPolicy.organization_id == org, PublicationPolicy.id == publication_id
        )
        if lock:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise WorkspaceFailure(
                "self_review_policy_required",
                "Координатор ещё не настроил лимит самопроверок.",
                409,
            )
        return value

    async def save_draft(
        self, actor: RequestActor, publication_id: UUID, expected: int, payload: DraftInput
    ) -> DraftView:
        user_id = require_roles(actor, "student")
        publication = await row(
            self.session, CourseRunHomework, actor.organization_id, publication_id, lock=True
        )
        await course_scope(self.session, actor, publication.course_run_id, write=True)
        draft = await self.session.scalar(
            select(WorkDraft)
            .where(
                WorkDraft.organization_id == actor.organization_id,
                WorkDraft.publication_id == publication_id,
                WorkDraft.student_id == user_id,
            )
            .with_for_update()
        )
        revision(draft.revision if draft else 0, expected)
        if payload.upload_id:
            artifact = await row(
                self.session, WorkspaceArtifact, actor.organization_id, payload.upload_id
            )
            if artifact.owner_id != user_id or artifact.private:
                raise WorkspaceFailure("artifact_forbidden", "Файл недоступен для этой сдачи.", 403)
        if draft is None:
            draft = WorkDraft(
                id=self.runtime.id_factory(),
                organization_id=actor.organization_id,
                publication_id=publication_id,
                student_id=user_id,
                revision=0,
            )
            self.session.add(draft)
        draft.artifact_url, draft.upload_id, draft.comment = (
            payload.artifact_url,
            payload.upload_id,
            payload.comment,
        )
        draft.revision += 1
        await self.session.flush()
        quota = await self.session.scalar(
            select(SelfReviewQuota).where(
                SelfReviewQuota.organization_id == actor.organization_id,
                SelfReviewQuota.draft_id == draft.id,
            )
        )
        if quota is None:
            self.session.add(
                SelfReviewQuota(
                    id=self.runtime.id_factory(),
                    organization_id=actor.organization_id,
                    draft_id=draft.id,
                    used=0,
                    reserved=0,
                )
            )
            await self.session.flush()
        return draft_view(draft)

    async def get_quota(
        self, draft: WorkDraft, policy: PublicationPolicy, *, lock: bool = False
    ) -> tuple[SelfReviewQuota, QuotaView]:
        query = select(SelfReviewQuota).where(
            SelfReviewQuota.organization_id == draft.organization_id,
            SelfReviewQuota.draft_id == draft.id,
        )
        if lock:
            query = query.with_for_update()
        quota = await self.session.scalar(query)
        if quota is None:
            raise WorkspaceFailure("quota_missing", "Учёт попыток недоступен.", 503)
        active = await self.session.scalar(
            select(SelfReviewRun.id).where(
                SelfReviewRun.organization_id == draft.organization_id,
                SelfReviewRun.draft_id == draft.id,
                SelfReviewRun.disposition == "reserved",
            )
        )
        return quota, QuotaView(
            limit=policy.self_review_limit,
            used=quota.used,
            reserved=quota.reserved,
            remaining=max(0, policy.self_review_limit - quota.used - quota.reserved),
            policy_revision=policy.revision,
            active_run_id=active,
        )

    async def start(self, actor: RequestActor, draft_id: UUID, expected: int) -> SelfReviewView:
        user_id = require_roles(actor, "student")
        draft = await row(self.session, WorkDraft, actor.organization_id, draft_id)
        if draft.student_id != user_id:
            raise WorkspaceFailure("not_found", "Черновик недоступен.", 404)
        publication = await row(
            self.session, CourseRunHomework, actor.organization_id, draft.publication_id, lock=True
        )
        draft = await row(self.session, WorkDraft, actor.organization_id, draft_id, lock=True)
        await course_scope(self.session, actor, publication.course_run_id, write=True)
        revision(draft.revision, expected)
        if not self.runtime.settings.workspace_enabled:
            raise WorkspaceFailure("configuration_required", "Самопроверка ещё не настроена.", 503)
        policy = await self.policy(actor.organization_id, publication.id, lock=True)
        quota, view = await self.get_quota(draft, policy, lock=True)
        if quota.reserved:
            raise WorkspaceFailure(
                "self_review_in_progress", "Самопроверка уже выполняется.", quota=view
            )
        if view.remaining <= 0:
            raise WorkspaceFailure(
                "self_review_limit_exhausted", "Лимит самопроверок исчерпан.", quota=view
            )
        artifact_id = await prepared_artifact(self.session, draft)
        if publication.current_publication_id is None:
            raise WorkspaceFailure("not_published", "Задание ещё не опубликовано.")
        published = await row(
            self.session,
            CourseRunHomeworkPublication,
            actor.organization_id,
            publication.current_publication_id,
        )
        homework = await row(
            self.session, HomeworkVersion, actor.organization_id, published.homework_version_id
        )
        criterion_set = await self.session.scalar(
            select(CriterionSet).where(
                CriterionSet.organization_id == actor.organization_id,
                CriterionSet.homework_version_id == homework.id,
            )
        )
        if criterion_set is None:
            raise WorkspaceFailure("criteria_missing", "Критерии недоступны.", 409)
        criteria = (
            await self.session.scalars(
                select(Criterion)
                .where(
                    Criterion.organization_id == actor.organization_id,
                    Criterion.criterion_set_id == criterion_set.id,
                )
                .order_by(Criterion.position)
            )
        ).all()
        run = SelfReviewRun(
            id=self.runtime.id_factory(),
            created_at=self.runtime.clock(),
            updated_at=self.runtime.clock(),
            organization_id=actor.organization_id,
            quota_id=quota.id,
            draft_id=draft.id,
            draft_revision=draft.revision,
            homework_version_id=homework.id,
            artifact_id=artifact_id,
            membership_revision=actor.membership_revision or 0,
            auth_epoch=actor.auth_epoch or 0,
            artifact_url=draft.artifact_url,
            student_text=homework.student_text,
            criteria=[
                {
                    "id": str(c.id),
                    "key": c.stable_key,
                    "title": c.title,
                    "max_points": float(c.max_points),
                }
                for c in criteria
            ],
            status="queued",
            disposition="reserved",
            attempt=0,
            sequence=-1,
        )
        quota.reserved += 1
        self.session.add(run)
        await self.session.flush()
        self.record(run, "reserve")
        return await self.view(run, draft=draft)

    def record(self, run: SelfReviewRun, action: str) -> None:
        self.session.add(
            SelfReviewQuotaEvent(
                id=self.runtime.id_factory(),
                organization_id=run.organization_id,
                run_id=run.id,
                action=action,
                occurred_at=self.runtime.clock(),
            )
        )

    async def view(self, run: SelfReviewRun, *, draft: WorkDraft | None = None) -> SelfReviewView:
        draft = draft or await row(self.session, WorkDraft, run.organization_id, run.draft_id)
        policy = await self.policy(run.organization_id, draft.publication_id)
        _, quota = await self.get_quota(draft, policy)
        return SelfReviewView(
            id=run.id,
            draft_revision=run.draft_revision,
            status=cast(
                Literal[
                    "queued",
                    "capturing",
                    "pending",
                    "running",
                    "unknown_outcome",
                    "succeeded",
                    "failed",
                ],
                run.status,
            ),
            disposition=cast(Literal["reserved", "consumed", "released"], run.disposition),
            artifact_id=run.artifact_id,
            created_at=run.created_at,
            result=SelfReviewResult.model_validate(run.result) if run.result else None,
            error_code=run.error_code,
            quota=quota,
        )

    async def get(self, actor: RequestActor, identity: UUID) -> SelfReviewView:
        run = await row(self.session, SelfReviewRun, actor.organization_id, identity)
        draft = await row(self.session, WorkDraft, actor.organization_id, run.draft_id)
        publication = await row(
            self.session, CourseRunHomework, actor.organization_id, draft.publication_id
        )
        await course_scope(self.session, actor, publication.course_run_id)
        if (
            "student" in actor.roles
            and not actor.roles.intersection({"reviewer", "methodologist"})
            and draft.student_id != actor.user_id
        ):
            raise WorkspaceFailure("not_found", "Самопроверка недоступна.", 404)
        return await self.view(run, draft=draft)

    async def accept(self, org: UUID, event: SelfReviewEvent) -> SelfReviewView:
        run = await row(self.session, SelfReviewRun, org, event.run_id, lock=True)
        receipt = await self.session.scalar(
            select(SelfReviewEventReceipt).where(
                SelfReviewEventReceipt.organization_id == org,
                SelfReviewEventReceipt.id == event.event_id,
            )
        )
        event_digest = digest(cast(JsonValue, event.model_dump(mode="json")))
        if receipt:
            if receipt.run_id != run.id or receipt.digest != event_digest:
                raise WorkspaceFailure("event_conflict", "Event identity was reused.")
            return await self.view(run)
        if event.attempt != run.attempt or event.input_fingerprint != run.input_fingerprint:
            raise WorkspaceFailure("stale_attempt", "AI event does not match the current attempt.")
        if run.disposition != "reserved" or event.sequence <= run.sequence:
            raise WorkspaceFailure("stale_event", "AI event is no longer current.")
        if event.result:
            expected = {str(c["id"]) for c in run.criteria}
            received = [str(f.criterion_id) for f in event.result.findings]
            if set(received) != expected or len(received) != len(expected):
                raise WorkspaceFailure(
                    "invalid_coverage", "Result criterion coverage is invalid.", 422
                )
        draft = await row(self.session, WorkDraft, org, run.draft_id)
        publication = await row(self.session, CourseRunHomework, org, draft.publication_id)
        access_valid = await student_epoch_valid(
            self.session,
            org,
            draft.student_id,
            publication.course_run_id,
            run.membership_revision,
            run.auth_epoch,
        )
        quota = await row(self.session, SelfReviewQuota, org, run.quota_id, lock=True)
        run.sequence = event.sequence
        if event.status == "running" and access_valid:
            run.status = "running"
        else:
            valid = (
                access_valid
                and event.result is not None
                and any(f.status != "not_checked" for f in event.result.findings)
            )
            if valid:
                assert event.result is not None
                run.result = event.result.model_dump(mode="json")
                run.status, run.disposition = "succeeded", "consumed"
                quota.used += 1
                self.record(run, "consume")
            else:
                run.status, run.disposition = "failed", "released"
                run.error_code = (
                    "access_revoked" if not access_valid else event.error_code or "invalid_result"
                )
                self.record(run, "release")
            quota.reserved -= 1
            run.finished_at = self.runtime.clock()
            run.lease_until = None
        self.session.add(
            SelfReviewEventReceipt(
                id=event.event_id, organization_id=org, run_id=run.id, digest=event_digest
            )
        )
        await self.session.flush()
        return await self.view(run)

    async def set_policy(
        self,
        actor: RequestActor,
        publication_id: UUID,
        expected: int,
        payload: PublicationPolicyInput,
    ) -> PublicationPolicy:
        require_roles(actor, "methodologist")
        publication = await row(
            self.session, CourseRunHomework, actor.organization_id, publication_id, lock=True
        )
        await course_scope(self.session, actor, publication.course_run_id, write=True)
        current = await self.session.scalar(
            select(PublicationPolicy)
            .where(
                PublicationPolicy.organization_id == actor.organization_id,
                PublicationPolicy.id == publication_id,
            )
            .with_for_update()
        )
        revision(current.revision if current else 0, expected)
        if current is None:
            current = PublicationPolicy(
                id=publication_id, organization_id=actor.organization_id, revision=0
            )
            self.session.add(current)
        current.self_review_limit, current.policy = (
            payload.self_review_limit,
            payload.model_dump(mode="json"),
        )
        current.revision += 1
        await self.session.flush()
        return current


def draft_view(draft: WorkDraft) -> DraftView:
    return DraftView(
        id=draft.id,
        revision=draft.revision,
        publication_id=draft.publication_id,
        artifact_url=draft.artifact_url,
        upload_id=draft.upload_id,
        comment=draft.comment,
    )
