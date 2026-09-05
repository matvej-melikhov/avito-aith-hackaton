"""Durable reviewer-only AI suggestions with immutable inputs and receipt deduplication."""

from __future__ import annotations

from datetime import timedelta
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor, Role
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    digest,
    require_roles,
    revision,
    row,
)
from review_platform.application.workspace.ports import ReviewerAIProvider
from review_platform.application.workspace.reviews import lock_review_scope
from review_platform.contracts.workspace import (
    CriterionSettings,
    ReviewAssistEvent,
    ReviewAssistRequest,
    ReviewAssistView,
    ReviewCriterionView,
)
from review_platform.infrastructure.db.models import (
    ArtifactVersion,
    Criterion,
    HomeworkVersion,
    OrganizationMembership,
    ReviewIteration,
)
from review_platform.infrastructure.db.models.workspace import (
    HomeworkPrivateDetails,
    ReviewAssistEventReceipt,
    ReviewAssistRun,
    WorkspaceArtifact,
)


class AssistInputs(BaseModel):
    student_text: str
    criteria: list[ReviewCriterionView]
    reviewer_guidance: str
    reference_id: UUID | None
    reference_digest: str | None
    artifact_digest: str
    homework_version_id: UUID
    iteration_id: UUID
    organization_id: UUID
    contract_version: str


def assist_view(run: ReviewAssistRun) -> ReviewAssistView:
    return ReviewAssistView.model_validate(
        {
            "id": run.id,
            "status": run.status,
            "revision": run.revision,
            "result": run.result,
            "error_code": run.error_code,
            "created_at": run.created_at,
        }
    )


class ReviewAssistService:
    def __init__(self, runtime: FoundationRuntime, session: AsyncSession):
        self.runtime, self.session = runtime, session

    async def scope(self, actor: RequestActor, iteration_id: UUID) -> ReviewIteration:
        require_roles(actor, "reviewer", "methodologist")
        iteration = await row(self.session, ReviewIteration, actor.organization_id, iteration_id)
        await course_scope(self.session, actor, iteration.course_run_id)
        return iteration

    async def latest(self, actor: RequestActor, iteration_id: UUID) -> ReviewAssistView | None:
        await self.scope(actor, iteration_id)
        run = await self.session.scalar(
            select(ReviewAssistRun)
            .where(
                ReviewAssistRun.organization_id == actor.organization_id,
                ReviewAssistRun.iteration_id == iteration_id,
            )
            .order_by(ReviewAssistRun.created_at.desc(), ReviewAssistRun.id.desc())
            .limit(1)
        )
        return assist_view(run) if run else None

    async def get(self, actor: RequestActor, identity: UUID) -> ReviewAssistView:
        run = await row(self.session, ReviewAssistRun, actor.organization_id, identity)
        await self.scope(actor, run.iteration_id)
        return assist_view(run)

    async def start(self, actor: RequestActor, identity: UUID, expected: int) -> ReviewAssistView:
        owner = require_roles(actor, "reviewer", "methodologist")
        iteration = await lock_review_scope(self.session, actor, identity)
        revision(iteration.revision, expected)
        if iteration.status not in {"in_review", "ready_to_publish"}:
            raise WorkspaceFailure("review_closed", "AI доступен только в открытом ревью.")
        active = await self.session.scalar(
            select(ReviewAssistRun.id).where(
                ReviewAssistRun.organization_id == actor.organization_id,
                ReviewAssistRun.iteration_id == identity,
                ReviewAssistRun.status.in_(["queued", "running", "unknown_outcome"]),
            )
        )
        if active:
            raise WorkspaceFailure("assist_active", "Проверка уже выполняется.")
        homework = await row(
            self.session, HomeworkVersion, actor.organization_id, iteration.homework_version_id
        )
        artifact = await row(
            self.session, ArtifactVersion, actor.organization_id, iteration.artifact_version_id
        )
        criteria = (
            await self.session.scalars(
                select(Criterion)
                .where(
                    Criterion.organization_id == actor.organization_id,
                    Criterion.criterion_set_id == iteration.criterion_set_id,
                    Criterion.active.is_(True),
                )
                .order_by(Criterion.position)
            )
        ).all()
        if not criteria:
            raise WorkspaceFailure("missing_criteria", "Сначала добавьте критерии.")
        private = await self.session.scalar(
            select(HomeworkPrivateDetails).where(
                HomeworkPrivateDetails.organization_id == actor.organization_id,
                HomeworkPrivateDetails.id == homework.id,
            )
        )
        reference = (
            await row(
                self.session, WorkspaceArtifact, actor.organization_id, private.reference_upload_id
            )
            if private and private.reference_upload_id
            else None
        )
        inputs = {
            "student_text": homework.student_text,
            "criteria": [
                ReviewCriterionView(
                    **CriterionSettings.model_validate(
                        (private.criterion_settings or {}).get(c.stable_key, {}) if private else {}
                    ).model_dump(),
                    id=c.id,
                    key=c.stable_key,
                    title=c.title,
                    description=c.description,
                    max_points=float(c.max_points),
                    position=c.position,
                ).model_dump(mode="json")
                for c in criteria
            ],
            "reviewer_guidance": private.reviewer_guidance if private else "",
            "reference_id": str(reference.id) if reference else None,
            "reference_digest": reference.digest if reference else None,
            "artifact_digest": artifact.content_digest,
            "homework_version_id": str(homework.id),
            "iteration_id": str(identity),
            "organization_id": str(actor.organization_id),
            "contract_version": "2.0.0",
        }
        run = ReviewAssistRun(
            id=self.runtime.id_factory(),
            organization_id=actor.organization_id,
            iteration_id=identity,
            owner_id=owner,
            membership_revision=actor.membership_revision,
            auth_epoch=actor.auth_epoch,
            artifact_id=artifact.id,
            homework_version_id=homework.id,
            input_fingerprint=digest(cast(JsonValue, inputs)),
            inputs=inputs,
            status="queued",
            revision=0,
            attempt=1,
            sequence=-1,
            created_at=self.runtime.clock(),
            updated_at=self.runtime.clock(),
        )
        self.session.add(run)
        await self.session.flush()
        return assist_view(run)

    async def retry(self, actor: RequestActor, identity: UUID, expected: int) -> ReviewAssistView:
        original = await row(self.session, ReviewAssistRun, actor.organization_id, identity)
        await lock_review_scope(self.session, actor, original.iteration_id)
        run = await row(self.session, ReviewAssistRun, actor.organization_id, identity, lock=True)
        owner = require_roles(actor, "reviewer", "methodologist")
        revision(run.revision, expected)
        if run.status != "failed" or run.attempt >= self.runtime.settings.workspace_max_attempts:
            raise WorkspaceFailure(
                "retry_unavailable", "Повтор доступен только после подтверждённой ошибки."
            )
        assert actor.membership_revision is not None and actor.auth_epoch is not None
        iteration = await self.scope(actor, run.iteration_id)
        if (
            iteration.artifact_version_id != run.artifact_id
            or iteration.homework_version_id != run.homework_version_id
            or iteration.status not in {"in_review", "ready_to_publish"}
        ):
            raise WorkspaceFailure("ai_source_stale", "Входные данные ревью изменились.")
        active = await self.session.scalar(
            select(ReviewAssistRun.id).where(
                ReviewAssistRun.organization_id == actor.organization_id,
                ReviewAssistRun.iteration_id == run.iteration_id,
                ReviewAssistRun.status.in_(["queued", "running", "unknown_outcome"]),
            )
        )
        if active:
            raise WorkspaceFailure("assist_active", "Проверка уже выполняется.")
        run.owner_id = owner
        run.membership_revision, run.auth_epoch = actor.membership_revision, actor.auth_epoch
        run.status, run.error_code, run.result = "queued", None, None
        run.attempt += 1
        run.revision += 1
        run.sequence, run.lease_token, run.lease_until = -1, None, None
        return assist_view(run)

    async def authorized(self, run: ReviewAssistRun) -> bool:
        member = await self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == run.organization_id,
                OrganizationMembership.user_id == run.owner_id,
                OrganizationMembership.status == "active",
            )
        )
        if not member or (member.revision, member.auth_epoch) != (
            run.membership_revision,
            run.auth_epoch,
        ):
            return False
        actor = RequestActor(
            organization_id=run.organization_id,
            actor_type="user",
            user_id=run.owner_id,
            roles=frozenset(cast(list[Role], member.roles)),
            membership_revision=member.revision,
            auth_epoch=member.auth_epoch,
        )
        try:
            iteration = await self.scope(actor, run.iteration_id)
        except WorkspaceFailure:
            return False
        return (
            iteration.artifact_version_id == run.artifact_id
            and iteration.homework_version_id == run.homework_version_id
            and iteration.status in {"in_review", "ready_to_publish"}
        )

    async def accept(self, org: UUID, event: ReviewAssistEvent) -> ReviewAssistView:
        run = await row(self.session, ReviewAssistRun, org, event.run_id, lock=True)
        existing = await self.session.scalar(
            select(ReviewAssistEventReceipt).where(
                ReviewAssistEventReceipt.organization_id == org,
                ReviewAssistEventReceipt.id == event.event_id,
            )
        )
        event_digest = digest(event.model_dump(mode="json"))
        if existing:
            if existing.run_id != run.id or existing.digest != event_digest:
                raise WorkspaceFailure("event_conflict", "Содержимое принятого события отличается.")
            return assist_view(run)
        if event.attempt != run.attempt or event.input_fingerprint != run.input_fingerprint:
            raise WorkspaceFailure("stale_event", "Событие относится к другим входным данным.")
        if run.status in {"succeeded", "failed", "stale"} or event.sequence <= run.sequence:
            return assist_view(run)
        if not await self.authorized(run):
            run.status, run.error_code = "stale", "access_or_inputs_changed"
            run.revision += 1
            return assist_view(run)
        if event.result:
            criteria = {str(c.id): c for c in AssistInputs.model_validate(run.inputs).criteria}
            suggestions = event.result.suggestions
            ids = [str(s.criterion_id) for s in suggestions]
            if (
                set(ids) != set(criteria)
                or len(ids) != len(set(ids))
                or any(
                    s.proposed_points is not None
                    and s.proposed_points > criteria[str(s.criterion_id)].max_points
                    for s in suggestions
                )
                or any(
                    criteria[str(s.criterion_id)].evaluate_quality
                    and s.proposed_points is not None
                    and (
                        s.requirement_met is None
                        or (not s.requirement_met and s.proposed_points != 0)
                    )
                    for s in suggestions
                    if str(s.criterion_id) in criteria
                )
            ):
                raise WorkspaceFailure("invalid_result", "Ответ AI не соответствует критериям.")
        self.session.add(
            ReviewAssistEventReceipt(
                id=event.event_id, organization_id=org, run_id=run.id, digest=event_digest
            )
        )
        run.sequence, run.status = event.sequence, event.status
        run.result = event.result.model_dump(mode="json") if event.result else None
        run.error_code = event.error_code
        run.revision += 1
        run.lease_until = (
            self.runtime.clock() + timedelta(seconds=5) if event.status == "running" else None
        )
        return assist_view(run)


class ReviewAssistWorker:
    def __init__(self, runtime: FoundationRuntime, provider: ReviewerAIProvider):
        self.runtime, self.provider = runtime, provider

    async def tick(self) -> bool:
        now, token = self.runtime.clock(), self.runtime.id_factory()
        async with self.runtime.transaction() as session:
            run = await session.scalar(
                select(ReviewAssistRun)
                .where(
                    ReviewAssistRun.status.in_(["queued", "running", "unknown_outcome"]),
                    or_(ReviewAssistRun.lease_until.is_(None), ReviewAssistRun.lease_until <= now),
                )
                .order_by(ReviewAssistRun.created_at, ReviewAssistRun.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if run is None:
                return False
            org, identity = run.organization_id, run.id
            service = ReviewAssistService(self.runtime, session)
            if not await service.authorized(run):
                run.status, run.error_code = "stale", "access_or_inputs_changed"
                run.revision += 1
                return True
            lookup = run.status != "queued"
            run.lease_token, run.lease_until = token, now + timedelta(seconds=60)
            run.status = "unknown_outcome"
            artifact = await row(session, ArtifactVersion, org, run.artifact_id)
            inputs = AssistInputs.model_validate(run.inputs)
            reference = (
                await row(session, WorkspaceArtifact, org, inputs.reference_id)
                if inputs.reference_id
                else None
            )
            request = ReviewAssistRequest(
                run_id=identity,
                attempt=run.attempt,
                input_fingerprint=run.input_fingerprint,
                review_iteration_id=run.iteration_id,
                artifact_id=artifact.id,
                artifact_digest=artifact.content_digest,
                media_type=artifact.media_type,
                artifact_url=self.runtime.sign_artifact_read(
                    organization_id=str(org),
                    artifact_version_id=str(artifact.id),
                    requested_by_organization_id=str(org),
                    object_key=artifact.object_key,
                ),
                student_text=inputs.student_text,
                criteria=inputs.criteria,
                reviewer_guidance=inputs.reviewer_guidance,
                reference_url=self.runtime.sign_artifact_read(
                    organization_id=str(org),
                    artifact_version_id=str(reference.id),
                    requested_by_organization_id=str(org),
                    object_key=reference.object_key,
                )
                if reference
                else None,
            )
        try:
            event = (
                await self.provider.lookup_assist(request)
                if lookup
                else await self.provider.submit_assist(request)
            )
            async with self.runtime.transaction() as session:
                run = await row(session, ReviewAssistRun, org, identity, lock=True)
                if run.lease_token != token or run.status not in {"unknown_outcome", "running"}:
                    return True
                if event:
                    await ReviewAssistService(self.runtime, session).accept(org, event)
                else:
                    run.status = "running"
                    run.lease_until = self.runtime.clock() + timedelta(seconds=5)
        except (OSError, TimeoutError, WorkspaceFailure):
            async with self.runtime.transaction() as session:
                run = await row(session, ReviewAssistRun, org, identity, lock=True)
                if run.lease_token == token and run.status in {"unknown_outcome", "running"}:
                    run.status = "unknown_outcome"
                    run.lease_until = self.runtime.clock() + timedelta(seconds=15)
        return True
