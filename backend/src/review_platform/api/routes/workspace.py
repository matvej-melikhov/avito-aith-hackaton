"""Version 2 HTTP boundary for complete workspace screens and student self-review."""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable, Coroutine
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID, uuid5

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.artifacts import upload
from review_platform.application.workspace.catalog import CatalogService
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    finish,
    replay,
    require_roles,
    reserve,
    revision,
    row,
)
from review_platform.application.workspace.preparation import PreparationService
from review_platform.application.workspace.projections import WorkspaceQueries
from review_platform.application.workspace.reviews import lock_review_scope
from review_platform.application.workspace.self_review import SelfReviewService, draft_view
from review_platform.contracts.workspace import (
    AssignmentInput,
    AssignmentsView,
    CatalogView,
    CourseInput,
    CourseRunInput,
    DirectoryView,
    DownloadView,
    DraftInput,
    DraftList,
    DraftView,
    EditorDraftInput,
    EditorDraftView,
    EmptyInput,
    ExportInput,
    ExportView,
    ExtraRequirementInput,
    GradePreview,
    HomeworkTitleInput,
    MembershipInput,
    NotificationInput,
    NotificationsView,
    NotificationView,
    OpenWorkInput,
    OutcomeInput,
    PreferencesInput,
    PreferencesView,
    PreparationView,
    PriorityInput,
    PrivateHomeworkInput,
    PrivateHomeworkView,
    PublicationPolicyInput,
    PublicationPolicyView,
    PublishedGradeView,
    PublishedWorkspaceHomework,
    PublishWithPolicyInput,
    PublishWorkspaceReviewInput,
    ReleaseInput,
    ResourceResult,
    ReviewContext,
    ReviewDraftView,
    SelfReviewEvent,
    SelfReviewView,
    StatisticView,
    StudentContext,
    StudentSubmissionView,
    UploadInput,
    UploadView,
    WorkList,
    WorkspaceCommand,
    WorkspaceSearchView,
)
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Homework,
    HomeworkVersion,
    Organization,
    OrganizationMembership,
    ReviewCase,
    ReviewIteration,
    Submission,
)
from review_platform.infrastructure.db.models.workspace import (
    HomeworkEditorDraft,
    HomeworkPrivateDetails,
    PublicationPolicy,
    ReviewOutcome,
    SelfReviewQuota,
    SelfReviewRun,
    WorkDraft,
    WorkspaceArtifact,
    WorkspaceCourseDetails,
    WorkspaceExport,
    WorkspaceNotification,
    WorkspaceRunSettings,
)


class WorkspaceRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except WorkspaceFailure as error:
                payload: dict[str, JsonValue] = {
                    "code": error.code,
                    "message": str(error),
                    "action": None,
                }
                if error.quota:
                    payload["quota"] = error.quota.model_dump(mode="json")
                return JSONResponse(payload, status_code=error.status)

        return handle


router = APIRouter(prefix="/v2", tags=["workspace-v2"], route_class=WorkspaceRoute)


async def context(request: Request) -> tuple[FoundationRuntime, RequestActor]:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(runtime, FoundationRuntime):
        raise WorkspaceFailure("configuration_required", "Сервер не настроен.", 503)
    if not isinstance(actor, RequestActor):
        raise WorkspaceFailure("unauthenticated", "Войдите в систему.", 401)
    await runtime.user_auth_guard.revalidate(actor=actor)
    return runtime, actor


async def mutate[T: BaseModel, R: BaseModel](
    request: Request,
    body: WorkspaceCommand[T],
    name: str,
    target: UUID,
    roles: tuple[str, ...],
    model: type[R],
    work: Callable[[FoundationRuntime, AsyncSession, RequestActor], Awaitable[R]],
) -> R:
    runtime, actor = await context(request)
    require_roles(actor, *roles)
    async with runtime.transaction() as session:
        receipt, created = await reserve(session, runtime, actor, body, name, target)
        if not created:
            return model.model_validate(replay(receipt))
        value = await work(runtime, session, actor)
        await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=session)
        audit = {
            k: v
            for k, v in body.payload.model_dump(mode="json").items()
            if k in {"student_id", "reviewer_id", "reason", "self_review_limit", "priority"}
        }
        finish(
            session,
            runtime,
            actor,
            receipt,
            cast(JsonValue, value.model_dump(mode="json")),
            details=audit,
        )
        return value


@router.get("/catalog", response_model=CatalogView)
async def catalog(request: Request) -> CatalogView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await CatalogService(runtime, session).catalog(actor)


@router.get("/directory", response_model=DirectoryView)
async def directory(request: Request) -> DirectoryView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await CatalogService(runtime, session).directory(actor)


@router.post(
    "/courses", response_model=ResourceResult, openapi_extra={"x-command-name": "create_course"}
)
async def create_course(request: Request, body: WorkspaceCommand[CourseInput]) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        org = await session.scalar(
            select(Organization).where(Organization.id == actor.organization_id).with_for_update()
        )
        if org is None:
            raise WorkspaceFailure("organization_missing", "Организация не найдена.", 404)
        revision(org.revision, body.expected_revision)
        result = await CatalogService(runtime, session).create_course(actor, body.payload)
        org.revision += 1
        return result

    _, actor = await context(request)
    return await mutate(
        request,
        body,
        "create_course",
        actor.organization_id,
        ("methodologist",),
        ResourceResult,
        work,
    )


@router.post(
    "/courses/{identity}/course-runs",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "create_course_run"},
)
async def create_run(
    identity: UUID, request: Request, body: WorkspaceCommand[CourseRunInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        return await CatalogService(runtime, session).create_run(
            actor, identity, body.expected_revision, body.payload
        )

    return await mutate(
        request, body, "create_course_run", identity, ("methodologist",), ResourceResult, work
    )


@router.get("/reviewer/preferences", response_model=PreferencesView)
async def preferences(request: Request) -> PreferencesView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await CatalogService(runtime, session).preferences(actor)


@router.post(
    "/reviewer/preferences",
    response_model=PreferencesView,
    openapi_extra={"x-command-name": "save_preferences"},
)
async def save_preferences(
    request: Request, body: WorkspaceCommand[PreferencesInput]
) -> PreferencesView:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> PreferencesView:
        return await CatalogService(runtime, session).save_preferences(
            actor, body.expected_revision, body.payload
        )

    _, actor = await context(request)
    assert actor.user_id
    return await mutate(
        request, body, "save_preferences", actor.user_id, ("reviewer",), PreferencesView, work
    )


@router.get("/course-runs/{identity}/assignments", response_model=AssignmentsView)
async def assignments(identity: UUID, request: Request) -> AssignmentsView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await CatalogService(runtime, session).assignments(actor, identity)


@router.post(
    "/course-runs/{identity}/assignments",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "assign_student"},
)
async def assign(
    identity: UUID, request: Request, body: WorkspaceCommand[AssignmentInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        return await CatalogService(runtime, session).assign(
            actor, identity, body.expected_revision, body.payload
        )

    return await mutate(
        request, body, "assign_student", identity, ("methodologist",), ResourceResult, work
    )


@router.post(
    "/course-runs/{identity}/memberships",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "set_course_membership"},
)
async def membership(
    identity: UUID, request: Request, body: WorkspaceCommand[MembershipInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        return await CatalogService(runtime, session).membership(
            actor, identity, body.expected_revision, body.payload
        )

    return await mutate(
        request, body, "set_course_membership", identity, ("methodologist",), ResourceResult, work
    )


@router.get("/works", response_model=WorkList)
async def works(
    request: Request,
    course_run_id: UUID | None = None,
    homework_id: UUID | None = None,
    q: str = Query(default="", max_length=200),
    state: str = "",
    view: str = "all",
    stuck: bool = False,
    priority: Literal["assigned", "deadline"] | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
) -> WorkList:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await WorkspaceQueries(runtime, session).works(
            actor,
            run_id=course_run_id,
            homework_id=homework_id,
            search=q,
            state=state,
            view=view,
            stuck=stuck,
            priority=priority,
            offset=offset,
            limit=limit,
        )


@router.get("/drafts", response_model=DraftList)
async def drafts(request: Request) -> DraftList:
    runtime, actor = await context(request)
    require_roles(actor, "student")
    async with runtime.transaction() as session:
        entries = (
            await session.scalars(
                select(WorkDraft)
                .where(
                    WorkDraft.organization_id == actor.organization_id,
                    WorkDraft.student_id == actor.user_id,
                )
                .order_by(WorkDraft.created_at.desc(), WorkDraft.id)
            )
        ).all()
        return DraftList(items=[draft_view(d) for d in entries])


@router.get("/course-run-homeworks/{identity}/student-context", response_model=StudentContext)
async def student_context(identity: UUID, request: Request) -> StudentContext:
    runtime, actor = await context(request)
    require_roles(actor, "student")
    async with runtime.transaction() as session:
        return await WorkspaceQueries(runtime, session).student_context(actor, identity)


@router.post(
    "/course-run-homeworks/{identity}/draft",
    response_model=DraftView,
    openapi_extra={"x-command-name": "save_work_draft"},
)
async def save_draft(
    identity: UUID, request: Request, body: WorkspaceCommand[DraftInput]
) -> DraftView:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> DraftView:
        return await SelfReviewService(runtime, session).save_draft(
            actor, identity, body.expected_revision, body.payload
        )

    return await mutate(request, body, "save_work_draft", identity, ("student",), DraftView, work)


@router.post(
    "/course-run-homeworks/{identity}/policy",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "set_publication_policy"},
)
async def set_policy(
    identity: UUID, request: Request, body: WorkspaceCommand[PublicationPolicyInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        policy = await SelfReviewService(runtime, session).set_policy(
            actor, identity, body.expected_revision, body.payload
        )
        return ResourceResult(id=policy.id, revision=policy.revision)

    return await mutate(
        request, body, "set_publication_policy", identity, ("methodologist",), ResourceResult, work
    )


@router.post(
    "/work-drafts/{identity}/self-reviews",
    response_model=SelfReviewView,
    status_code=202,
    openapi_extra={"x-command-name": "start_self_review"},
)
async def start_self_review(
    identity: UUID, request: Request, body: WorkspaceCommand[EmptyInput]
) -> SelfReviewView:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> SelfReviewView:
        return await SelfReviewService(runtime, session).start(
            actor, identity, body.expected_revision
        )

    return await mutate(
        request, body, "start_self_review", identity, ("student",), SelfReviewView, work
    )


@router.get("/self-reviews/{identity}", response_model=SelfReviewView)
async def self_review(identity: UUID, request: Request) -> SelfReviewView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await SelfReviewService(runtime, session).get(actor, identity)


@router.post("/internal/self-review/events", response_model=SelfReviewView)
async def self_review_event(request: Request, event: SelfReviewEvent) -> SelfReviewView:
    secret = os.environ.get("REVIEW_PLATFORM_SELF_REVIEW_EVENT_TOKEN", "")
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
    org = os.environ.get("REVIEW_PLATFORM_ORGANIZATION_ID", "")
    if not secret or not org:
        raise WorkspaceFailure("configuration_required", "AI callback is not configured.", 503)
    if not hmac.compare_digest(bearer, secret):
        raise WorkspaceFailure("unauthenticated", "Invalid component credential.", 401)
    runtime = getattr(request.app.state, "foundation_runtime", None)
    if not isinstance(runtime, FoundationRuntime):
        raise WorkspaceFailure("configuration_required", "Runtime is not configured.", 503)
    async with runtime.transaction() as session:
        return await SelfReviewService(runtime, session).accept(UUID(org), event)


@router.post(
    "/self-reviews/{identity}/release",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "release_self_review"},
)
async def release_self_review(
    identity: UUID, request: Request, body: WorkspaceCommand[ReleaseInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        run = await row(session, SelfReviewRun, actor.organization_id, identity, lock=True)
        if run.disposition != "reserved":
            raise WorkspaceFailure("already_final", "Запуск уже завершён.")
        if run.status not in {"unknown_outcome", "failed"}:
            raise WorkspaceFailure("still_running", "Нельзя освободить выполняющийся запуск.")
        quota = await row(session, SelfReviewQuota, actor.organization_id, run.quota_id, lock=True)
        run.disposition = "released"
        run.status = "failed"
        run.error_code = "released_by_coordinator"
        run.attempt += 1
        run.finished_at = runtime.clock()
        quota.reserved -= 1
        SelfReviewService(runtime, session).record(run, "release")
        return ResourceResult(id=identity, revision=run.attempt)

    return await mutate(
        request, body, "release_self_review", identity, ("methodologist",), ResourceResult, work
    )


@router.post(
    "/uploads", response_model=UploadView, openapi_extra={"x-command-name": "upload_artifact"}
)
async def upload_artifact(request: Request, body: WorkspaceCommand[UploadInput]) -> UploadView:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> UploadView:
        return await upload(runtime, session, actor, body.payload)

    _, actor = await context(request)
    assert actor.user_id
    return await mutate(
        request,
        body,
        "upload_artifact",
        actor.user_id,
        ("student", "reviewer", "methodologist"),
        UploadView,
        work,
    )


@router.get("/artifacts/{identity}/download", response_model=DownloadView)
async def artifact_download(identity: UUID, request: Request) -> DownloadView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        from review_platform.infrastructure.db.models import (
            ArtifactVersion,
            SubmissionVersion,
        )

        artifact = await session.scalar(
            select(WorkspaceArtifact).where(
                WorkspaceArtifact.organization_id == actor.organization_id,
                WorkspaceArtifact.id == identity,
            )
        )
        legacy = (
            await session.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.organization_id == actor.organization_id,
                    ArtifactVersion.id == identity,
                )
            )
            if artifact is None
            else None
        )
        if artifact is None and legacy is None:
            raise WorkspaceFailure("not_found", "Файл недоступен.", 404)
        allowed = artifact is not None and artifact.owner_id == actor.user_id
        if (
            artifact
            and artifact.private
            and not actor.roles.intersection({"reviewer", "methodologist"})
        ):
            allowed = False
        if not allowed:
            # Public homework attachments and private rubric references use different audiences.
            details_rows = (
                await session.execute(
                    select(HomeworkPrivateDetails, HomeworkVersion)
                    .join(
                        HomeworkVersion,
                        (HomeworkVersion.id == HomeworkPrivateDetails.id)
                        & (
                            HomeworkVersion.organization_id
                            == HomeworkPrivateDetails.organization_id
                        ),
                    )
                    .where(HomeworkPrivateDetails.organization_id == actor.organization_id)
                )
            ).all()
            for details, version in details_rows:
                public_material = str(identity) in (details.material_upload_ids or [])
                private_reference = details.reference_upload_id == identity and bool(
                    actor.roles.intersection({"reviewer", "methodologist"})
                )
                if not public_material and not private_reference:
                    continue
                relations = (
                    await session.scalars(
                        select(CourseRunHomework).where(
                            CourseRunHomework.organization_id == actor.organization_id,
                            CourseRunHomework.homework_id == version.homework_id,
                        )
                    )
                ).all()
                for relation in relations:
                    try:
                        await course_scope(session, actor, relation.course_run_id)
                        # Students see files only after this exact version has been published.
                        if not actor.roles.intersection({"reviewer", "methodologist"}):
                            published_version = await session.scalar(
                                select(CourseRunHomeworkPublication.id).where(
                                    CourseRunHomeworkPublication.organization_id
                                    == actor.organization_id,
                                    CourseRunHomeworkPublication.course_run_homework_id
                                    == relation.id,
                                    CourseRunHomeworkPublication.homework_version_id == version.id,
                                )
                            )
                            if published_version is None:
                                continue
                        allowed = True
                        break
                    except WorkspaceFailure as error:
                        if error.status != 403:
                            raise
                if allowed:
                    break
        if not allowed and (artifact is None or not artifact.private):
            submitted = (
                (
                    await session.execute(
                        select(Submission)
                        .join(
                            SubmissionVersion,
                            (SubmissionVersion.submission_id == Submission.id)
                            & (SubmissionVersion.organization_id == Submission.organization_id),
                        )
                        .where(
                            Submission.organization_id == actor.organization_id,
                            SubmissionVersion.artifact_version_id == identity,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for submission in submitted:
                if submission.student_id == actor.user_id or actor.roles.intersection(
                    {"reviewer", "methodologist"}
                ):
                    await course_scope(session, actor, submission.course_run_id)
                    allowed = True
                    break
            if not allowed and actor.roles.intersection({"reviewer", "methodologist"}):
                run = await session.scalar(
                    select(SelfReviewRun).where(
                        SelfReviewRun.organization_id == actor.organization_id,
                        SelfReviewRun.artifact_id == identity,
                    )
                )
                if run:
                    draft = await row(session, WorkDraft, actor.organization_id, run.draft_id)
                    relation = await row(
                        session, CourseRunHomework, actor.organization_id, draft.publication_id
                    )
                    await course_scope(session, actor, relation.course_run_id)
                    allowed = True
        if not allowed:
            raise WorkspaceFailure("forbidden", "Файл недоступен.", 403)
        selected_artifact = artifact or legacy
        assert selected_artifact is not None
        return DownloadView(
            filename=artifact.filename if artifact else None,
            url=runtime.object_storage.sign_read(
                key=selected_artifact.object_key,
                organization_id=str(actor.organization_id),
                artifact_version_id=str(identity),
                requested_by_organization_id=str(actor.organization_id),
                expires_in_seconds=900,
            ),
            expires_at=runtime.clock() + timedelta(minutes=15),
        )


@router.get("/reviews/{identity}/context", response_model=ReviewContext)
async def review_context(identity: UUID, request: Request) -> ReviewContext:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await WorkspaceQueries(runtime, session).review_context(actor, identity)


@router.post(
    "/homework-versions/{identity}/private-details",
    response_model=PrivateHomeworkView,
    openapi_extra={"x-command-name": "save_private_homework"},
)
async def save_private(
    identity: UUID, request: Request, body: WorkspaceCommand[PrivateHomeworkInput]
) -> PrivateHomeworkView:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> PrivateHomeworkView:
        await row(session, HomeworkVersion, actor.organization_id, identity, lock=True)
        current = await session.scalar(
            select(HomeworkPrivateDetails)
            .where(
                HomeworkPrivateDetails.organization_id == actor.organization_id,
                HomeworkPrivateDetails.id == identity,
            )
            .with_for_update()
        )
        revision(current.revision if current else 0, body.expected_revision)
        from review_platform.infrastructure.db.models import Criterion, CriterionSet

        stable_keys = set(
            (
                await session.scalars(
                    select(Criterion.stable_key)
                    .join(
                        CriterionSet,
                        (CriterionSet.id == Criterion.criterion_set_id)
                        & (CriterionSet.organization_id == Criterion.organization_id),
                    )
                    .where(
                        Criterion.organization_id == actor.organization_id,
                        CriterionSet.homework_version_id == identity,
                    )
                )
            ).all()
        )
        if not set(body.payload.criterion_settings).issubset(stable_keys):
            raise WorkspaceFailure(
                "unknown_criterion", "Настройки относятся к неизвестному критерию.", 422
            )
        if body.payload.reference_upload_id:
            reference = await row(
                session, WorkspaceArtifact, actor.organization_id, body.payload.reference_upload_id
            )
            if not reference.private:
                raise WorkspaceFailure(
                    "reference_not_private", "Эталон должен быть загружен как закрытый файл.", 422
                )
        if current is None:
            current = HomeworkPrivateDetails(
                id=identity, organization_id=actor.organization_id, revision=0
            )
            session.add(current)
        for material_id in body.payload.material_upload_ids:
            material = await row(session, WorkspaceArtifact, actor.organization_id, material_id)
            if material.private:
                raise WorkspaceFailure(
                    "material_private",
                    "Открытые материалы должны быть загружены как общедоступные.",
                    422,
                )
        from review_platform.application.workspace.source_policy import DEFAULT_SOURCES
        from review_platform.infrastructure.db.models import CourseRunHomeworkPublication

        existing_sources = (
            current.allowed_sources if current.allowed_sources is not None else DEFAULT_SOURCES
        )
        if set(existing_sources) != set(body.payload.allowed_sources) and await session.scalar(
            select(CourseRunHomeworkPublication.id)
            .where(
                CourseRunHomeworkPublication.organization_id == actor.organization_id,
                CourseRunHomeworkPublication.homework_version_id == identity,
            )
            .limit(1)
        ):
            raise WorkspaceFailure(
                "source_policy_published",
                "Для изменения способа сдачи создайте новую версию задания.",
            )
        current.allowed_sources = list(body.payload.allowed_sources)
        current.material_upload_ids = [str(i) for i in body.payload.material_upload_ids]
        current.reviewer_guidance = body.payload.reviewer_guidance
        current.reference_upload_id = body.payload.reference_upload_id
        current.criterion_classes = dict(body.payload.criterion_classes)
        current.criterion_settings = {
            key: value.model_dump(mode="json")
            for key, value in body.payload.criterion_settings.items()
        }
        current.revision += 1
        return PrivateHomeworkView(**body.payload.model_dump(), revision=current.revision)

    return await mutate(
        request,
        body,
        "save_private_homework",
        identity,
        ("methodologist",),
        PrivateHomeworkView,
        work,
    )


@router.post(
    "/reviews/{identity}/outcome",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "save_review_outcome"},
)
async def save_outcome(
    identity: UUID, request: Request, body: WorkspaceCommand[OutcomeInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        iteration = await lock_review_scope(session, actor, identity)
        if iteration.status in {"published", "canceled"}:
            raise WorkspaceFailure(
                "closed", "Для изменения опубликованного результата создайте исправление."
            )
        current = await session.scalar(
            select(ReviewOutcome)
            .where(
                ReviewOutcome.organization_id == actor.organization_id, ReviewOutcome.id == identity
            )
            .with_for_update()
        )
        revision(current.revision if current else 0, body.expected_revision)
        if current is None:
            current = ReviewOutcome(id=identity, organization_id=actor.organization_id, revision=0)
            session.add(current)
        current.decision = body.payload.decision
        current.revision_deadline = body.payload.revision_deadline
        current.reason = body.payload.reason
        current.revision += 1
        iteration.revision += 1
        return ResourceResult(id=identity, revision=current.revision)

    return await mutate(
        request,
        body,
        "save_review_outcome",
        identity,
        ("reviewer", "methodologist"),
        ResourceResult,
        work,
    )


@router.post(
    "/submissions/{identity}/open-review",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "open_work"},
)
async def open_work(
    identity: UUID, request: Request, body: WorkspaceCommand[OpenWorkInput]
) -> ResourceResult:
    from review_platform.api.routes.submissions import _ReviewAudit, _ReviewAuthorization
    from review_platform.application.services.review_iterations import (
        OpenReviewIterationCommand,
        ReviewIterationService,
    )
    from review_platform.infrastructure.db.repositories.submissions import (
        SqlReviewIterationRepository,
    )

    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        sub = await row(session, Submission, actor.organization_id, identity, lock=True)
        revision(sub.revision, body.expected_revision)
        await course_scope(session, actor, sub.course_run_id, write=True)
        case = await session.scalar(
            select(ReviewCase).where(
                ReviewCase.organization_id == actor.organization_id,
                ReviewCase.course_run_id == sub.course_run_id,
                ReviewCase.homework_id == sub.homework_id,
                ReviewCase.student_id == sub.student_id,
            )
        )
        case_id = (
            case.id
            if case
            else uuid5(
                actor.organization_id, f"{sub.course_run_id}:{sub.homework_id}:{sub.student_id}"
            )
        )
        result = await ReviewIterationService(
            repository=SqlReviewIterationRepository(session),
            authorization=_ReviewAuthorization(runtime),
            audit=_ReviewAudit(runtime),
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        ).open(
            OpenReviewIterationCommand(
                organization_id=actor.organization_id,
                review_case_id=case_id,
                submission_version_id=body.payload.submission_version_id,
                expected_review_case_revision=case.revision if case else 0,
                request_id=body.request_id,
                trace_id=body.request_id,
            ),
            actor=actor,
            transaction=session,
        )
        iteration = await row(
            session, ReviewIteration, actor.organization_id, result.review_iteration_id
        )
        from review_platform.infrastructure.db.models import ReviewResponsibility

        latest = await session.scalar(
            select(ReviewResponsibility)
            .where(
                ReviewResponsibility.organization_id == actor.organization_id,
                ReviewResponsibility.review_iteration_id == iteration.id,
                ReviewResponsibility.reviewer_id == actor.user_id,
            )
            .order_by(ReviewResponsibility.occurred_at.desc(), ReviewResponsibility.id.desc())
            .limit(1)
        )
        if latest is None or latest.action == "released":
            session.add(
                ReviewResponsibility(
                    id=runtime.id_factory(),
                    organization_id=actor.organization_id,
                    review_case_id=iteration.review_case_id,
                    review_iteration_id=iteration.id,
                    reviewer_id=actor.user_id,
                    actor_id=actor.user_id,
                    action="joined",
                    occurred_at=runtime.clock(),
                )
            )
        return ResourceResult(id=result.review_iteration_id, revision=iteration.revision)

    return await mutate(
        request, body, "open_work", identity, ("reviewer", "methodologist"), ResourceResult, work
    )


@router.get("/statistics", response_model=StatisticView)
async def statistics(
    request: Request, days: int = Query(default=30, ge=1, le=365), course_run_id: UUID | None = None
) -> StatisticView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await WorkspaceQueries(runtime, session).statistics(
            actor,
            runtime.clock() - timedelta(days=days),
            runtime.clock(),
            course_run_id=course_run_id,
        )


@router.post(
    "/course-runs/{identity}/reminders",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "remind_reviewers"},
)
async def remind(
    identity: UUID, request: Request, body: WorkspaceCommand[NotificationInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        run = await row(session, CourseRun, actor.organization_id, identity, lock=True)
        revision(run.revision, body.expected_revision)
        recipients = (
            await session.scalars(
                select(CourseMembership.user_id).where(
                    CourseMembership.organization_id == actor.organization_id,
                    CourseMembership.course_run_id == identity,
                    CourseMembership.kind == "reviewer",
                    CourseMembership.status == "active",
                    CourseMembership.user_id.in_(body.payload.reviewer_ids),
                )
            )
        ).all()
        if set(recipients) != set(body.payload.reviewer_ids):
            raise WorkspaceFailure(
                "invalid_recipient", "Получатель не является ревьюером потока.", 422
            )
        for recipient in recipients:
            session.add(
                WorkspaceNotification(
                    id=runtime.id_factory(),
                    organization_id=actor.organization_id,
                    course_run_id=identity,
                    recipient_id=recipient,
                    text=body.payload.text,
                )
            )
        return ResourceResult(id=body.request_id, revision=run.revision)

    return await mutate(
        request, body, "remind_reviewers", identity, ("methodologist",), ResourceResult, work
    )


@router.post(
    "/exports",
    response_model=ExportView,
    status_code=202,
    openapi_extra={"x-command-name": "create_export"},
)
async def create_export(request: Request, body: WorkspaceCommand[ExportInput]) -> ExportView:
    from review_platform.application.workspace.exports import STUDENT_COLUMNS

    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ExportView:
        run = await course_scope(session, actor, body.payload.course_run_id)
        revision(run.revision, body.expected_revision)
        if body.payload.audience == "students" and not set(body.payload.columns) <= STUDENT_COLUMNS:
            raise WorkspaceFailure(
                "private_columns", "Недопустимые колонки студенческой копии.", 422
            )
        job = WorkspaceExport(
            id=runtime.id_factory(),
            organization_id=actor.organization_id,
            course_run_id=run.id,
            owner_id=actor.user_id,
            options=body.payload.model_dump(mode="json"),
            status="queued",
            row_count=0,
        )
        session.add(job)
        await session.flush()
        return ExportView(id=job.id, status="queued", rows=0, download=None, error=None)

    return await mutate(
        request,
        body,
        "create_export",
        body.payload.course_run_id,
        ("methodologist",),
        ExportView,
        work,
    )


@router.get("/exports/{identity}", response_model=ExportView)
async def get_export(identity: UUID, request: Request) -> ExportView:
    from review_platform.application.workspace.exports import export_view

    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await export_view(runtime, session, actor, identity)


@router.post(
    "/work-drafts/{identity}/submit-upload",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "submit_uploaded_draft"},
)
async def submit_upload(
    identity: UUID, request: Request, body: WorkspaceCommand[EmptyInput]
) -> ResourceResult:
    from review_platform.application.workspace.submissions import submit_uploaded_draft

    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        return await submit_uploaded_draft(
            runtime, session, actor, identity, body.expected_revision
        )

    return await mutate(
        request, body, "submit_uploaded_draft", identity, ("student",), ResourceResult, work
    )


@router.get("/submissions/{identity}", response_model=StudentSubmissionView)
async def get_student_submission(identity: UUID, request: Request) -> StudentSubmissionView:
    from review_platform.application.workspace.submissions import student_submission

    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await student_submission(runtime, session, actor, identity)


@router.get("/notifications", response_model=NotificationsView)
async def notifications(request: Request) -> NotificationsView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        rows = (
            await session.scalars(
                select(WorkspaceNotification)
                .where(
                    WorkspaceNotification.organization_id == actor.organization_id,
                    WorkspaceNotification.recipient_id == actor.user_id,
                )
                .order_by(WorkspaceNotification.created_at.desc(), WorkspaceNotification.id)
                .limit(100)
            )
        ).all()
        return NotificationsView(
            items=[
                NotificationView(
                    id=n.id,
                    course_run_id=n.course_run_id,
                    text=n.text,
                    created_at=n.created_at,
                    read=n.read_at is not None,
                )
                for n in rows
            ]
        )


@router.post(
    "/notifications/{identity}/read",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "read_notification"},
)
async def read_notification(
    identity: UUID, request: Request, body: WorkspaceCommand[EmptyInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        note = await row(session, WorkspaceNotification, actor.organization_id, identity, lock=True)
        if note.recipient_id != actor.user_id:
            raise WorkspaceFailure("forbidden", "Уведомление недоступно.", 403)
        note.read_at = runtime.clock()
        return ResourceResult(id=identity, revision=0)

    return await mutate(
        request,
        body,
        "read_notification",
        identity,
        ("student", "reviewer", "methodologist"),
        ResourceResult,
        work,
    )


@router.post(
    "/course-runs/{identity}/priority",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "set_run_priority"},
)
async def set_run_priority(
    identity: UUID, request: Request, body: WorkspaceCommand[PriorityInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        await row(session, CourseRun, actor.organization_id, identity, lock=True)
        settings = await session.scalar(
            select(WorkspaceRunSettings)
            .where(
                WorkspaceRunSettings.organization_id == actor.organization_id,
                WorkspaceRunSettings.id == identity,
            )
            .with_for_update()
        )
        revision(settings.revision if settings else 0, body.expected_revision)
        if settings is None:
            settings = WorkspaceRunSettings(
                id=identity, organization_id=actor.organization_id, revision=0
            )
            session.add(settings)
        settings.priority = body.payload.priority
        settings.revision += 1
        return ResourceResult(id=identity, revision=settings.revision)

    return await mutate(
        request, body, "set_run_priority", identity, ("methodologist",), ResourceResult, work
    )


@router.get("/homeworks/{identity}/editor-draft", response_model=EditorDraftView)
async def editor_draft(identity: UUID, course_run_id: UUID, request: Request) -> EditorDraftView:
    runtime, actor = await context(request)
    require_roles(actor, "methodologist")
    async with runtime.transaction() as session:
        homework = await row(session, Homework, actor.organization_id, identity)
        run = await course_scope(session, actor, course_run_id)
        if run.course_id != homework.course_id:
            raise WorkspaceFailure(
                "scope_mismatch", "Задание и поток относятся к разным курсам.", 422
            )
        draft = await session.scalar(
            select(HomeworkEditorDraft).where(
                HomeworkEditorDraft.organization_id == actor.organization_id,
                HomeworkEditorDraft.homework_id == identity,
                HomeworkEditorDraft.course_run_id == course_run_id,
            )
        )
        return EditorDraftView(
            homework_title=homework.title,
            homework_revision=homework.revision,
            revision=draft.revision if draft else 0,
            value=EditorDraftInput.model_validate(draft.value) if draft else None,
        )


@router.post(
    "/homeworks/{identity}/editor-draft",
    response_model=EditorDraftView,
    openapi_extra={"x-command-name": "save_editor_draft"},
)
async def save_editor_draft(
    identity: UUID, request: Request, body: WorkspaceCommand[EditorDraftInput]
) -> EditorDraftView:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> EditorDraftView:
        homework = await row(session, Homework, actor.organization_id, identity, lock=True)
        run = await course_scope(session, actor, body.payload.course_run_id, write=True)
        if run.course_id != homework.course_id:
            raise WorkspaceFailure(
                "scope_mismatch", "Задание и поток относятся к разным курсам.", 422
            )
        draft = await session.scalar(
            select(HomeworkEditorDraft)
            .where(
                HomeworkEditorDraft.organization_id == actor.organization_id,
                HomeworkEditorDraft.homework_id == identity,
                HomeworkEditorDraft.course_run_id == run.id,
            )
            .with_for_update()
        )
        revision(draft.revision if draft else 0, body.expected_revision)
        if draft is None:
            draft = HomeworkEditorDraft(
                id=runtime.id_factory(),
                organization_id=actor.organization_id,
                homework_id=identity,
                course_run_id=run.id,
                revision=0,
            )
            session.add(draft)
        draft.value = body.payload.model_dump(mode="json")
        draft.revision += 1
        return EditorDraftView(
            homework_title=homework.title,
            homework_revision=homework.revision,
            revision=draft.revision,
            value=body.payload,
        )

    return await mutate(
        request, body, "save_editor_draft", identity, ("methodologist",), EditorDraftView, work
    )


@router.get("/course-run-homeworks/{identity}/policy", response_model=PublicationPolicyView | None)
async def get_policy(identity: UUID, request: Request) -> PublicationPolicyView | None:
    runtime, actor = await context(request)
    require_roles(actor, "methodologist")
    async with runtime.transaction() as session:
        publication = await row(session, CourseRunHomework, actor.organization_id, identity)
        await course_scope(session, actor, publication.course_run_id)
        policy = await session.scalar(
            select(PublicationPolicy).where(
                PublicationPolicy.organization_id == actor.organization_id,
                PublicationPolicy.id == identity,
            )
        )
        return (
            PublicationPolicyView.model_validate({**policy.policy, "revision": policy.revision})
            if policy
            else None
        )


@router.get("/homework-versions/{identity}/private-details", response_model=PrivateHomeworkView)
async def get_private(identity: UUID, request: Request) -> PrivateHomeworkView:
    runtime, actor = await context(request)
    require_roles(actor, "methodologist")
    async with runtime.transaction() as session:
        await row(session, HomeworkVersion, actor.organization_id, identity)
        details = await session.scalar(
            select(HomeworkPrivateDetails).where(
                HomeworkPrivateDetails.organization_id == actor.organization_id,
                HomeworkPrivateDetails.id == identity,
            )
        )
        return (
            PrivateHomeworkView.model_validate(
                {
                    "revision": details.revision,
                    "allowed_sources": details.allowed_sources
                    if details.allowed_sources is not None
                    else ["upload", "github", "google_docs"],
                    "material_upload_ids": details.material_upload_ids or [],
                    "reviewer_guidance": details.reviewer_guidance,
                    "reference_upload_id": details.reference_upload_id,
                    "criterion_classes": details.criterion_classes,
                    "criterion_settings": details.criterion_settings or {},
                }
            )
            if details
            else PrivateHomeworkView(revision=0)
        )


@router.post(
    "/homework-versions/{identity}/publish",
    response_model=PublishedWorkspaceHomework,
    openapi_extra={"x-command-name": "publish_workspace_homework"},
)
async def publish_workspace(
    identity: UUID, request: Request, body: WorkspaceCommand[PublishWithPolicyInput]
) -> PublishedWorkspaceHomework:
    from review_platform.api.routes.homeworks import _service

    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> PublishedWorkspaceHomework:
        payload = body.payload
        publication = await _service(runtime, session).publish_version(
            transaction=session,
            organization_id=actor.organization_id,
            course_run_id=payload.course_run_id,
            homework_version_id=identity,
            expected_version_revision=body.expected_revision,
            submission_deadline=payload.submission_deadline,
            review_deadline=payload.review_deadline,
            actor=actor,
            request_id=body.request_id,
            trace_id=body.request_id,
        )
        history = await row(
            session, CourseRunHomeworkPublication, actor.organization_id, publication.publication_id
        )
        policy = await SelfReviewService(runtime, session).set_policy(
            actor, history.course_run_homework_id, payload.expected_policy_revision, payload.policy
        )
        relation = await row(
            session, CourseRunHomework, actor.organization_id, history.course_run_homework_id
        )
        return PublishedWorkspaceHomework(
            publication_id=relation.id,
            history_publication_id=history.id,
            revision=relation.revision,
            policy_revision=policy.revision,
        )

    return await mutate(
        request,
        body,
        "publish_workspace_homework",
        identity,
        ("methodologist",),
        PublishedWorkspaceHomework,
        work,
    )


@router.get("/reviews/{identity}/grade-preview", response_model=GradePreview)
async def preview_grade(identity: UUID, request: Request) -> GradePreview:
    from review_platform.application.workspace.grading import grade_preview

    runtime, actor = await context(request)
    require_roles(actor, "reviewer", "methodologist")
    async with runtime.transaction() as session:
        iteration = await row(session, ReviewIteration, actor.organization_id, identity)
        await course_scope(session, actor, iteration.course_run_id)
        return await grade_preview(session, actor.organization_id, iteration)


@router.post(
    "/reviews/{identity}/publish",
    response_model=PublishedGradeView,
    openapi_extra={"x-command-name": "publish_workspace_review"},
)
async def publish_workspace_review(
    identity: UUID, request: Request, body: WorkspaceCommand[PublishWorkspaceReviewInput]
) -> PublishedGradeView:
    from review_platform.api.routes.review_composition import (
        ReviewCompositionError,
        dispatch_review_mutation,
    )
    from review_platform.application.workspace.grading import grade_preview
    from review_platform.contracts.commands import WireCommand

    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> PublishedGradeView:
        iteration = await lock_review_scope(session, actor, identity)
        revision(iteration.revision, body.expected_revision)
        if (
            iteration.status in {"published", "canceled"}
            or iteration.current_revision_id != body.payload.review_revision_id
        ):
            raise WorkspaceFailure("revision_conflict", "Нужна текущая сохранённая версия ревью.")
        grade = await grade_preview(session, actor.organization_id, iteration)
        core = WireCommand.model_validate(
            {
                **body.model_dump(mode="json"),
                "command_name": "publish_review",
                "revision_target": "review_iteration",
                "payload": {"review_revision_id": str(body.payload.review_revision_id)},
            }
        )
        try:
            result = await dispatch_review_mutation(
                runtime=runtime,
                actor=actor,
                command=core,
                transaction=session,
            )
        except ReviewCompositionError as error:
            raise WorkspaceFailure("publication_conflict", str(error)) from error
        assert result is not None
        publication_id = UUID(str(result["id"]))
        return PublishedGradeView(id=publication_id, grade=grade)

    return await mutate(
        request,
        body,
        "publish_workspace_review",
        identity,
        ("reviewer", "methodologist"),
        PublishedGradeView,
        work,
    )


@router.post(
    "/reviews/{identity}/requirements",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "add_review_requirement"},
)
async def add_review_requirement(
    identity: UUID, request: Request, body: WorkspaceCommand[ExtraRequirementInput]
) -> ResourceResult:
    from review_platform.application.workspace.reviews import add_requirement

    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        return await add_requirement(runtime, session, actor, body)

    return await mutate(
        request,
        body,
        "add_review_requirement",
        identity,
        ("reviewer", "methodologist"),
        ResourceResult,
        work,
    )


@router.post(
    "/work-drafts/{identity}/prepare",
    response_model=PreparationView,
    status_code=202,
    openapi_extra={"x-command-name": "prepare_work_draft"},
)
async def prepare_work_draft(
    identity: UUID, request: Request, body: WorkspaceCommand[EmptyInput]
) -> PreparationView:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> PreparationView:
        return await PreparationService(runtime, session).start(
            actor, identity, body.expected_revision
        )

    return await mutate(
        request, body, "prepare_work_draft", identity, ("student",), PreparationView, work
    )


@router.get("/preparations/{identity}", response_model=PreparationView)
async def preparation(identity: UUID, request: Request) -> PreparationView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await PreparationService(runtime, session).get(actor, identity)


@router.post(
    "/work-drafts/{identity}/submit",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "submit_work_draft"},
)
async def submit_work_draft(
    identity: UUID, request: Request, body: WorkspaceCommand[EmptyInput]
) -> ResourceResult:
    from review_platform.application.workspace.submissions import submit_uploaded_draft

    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        return await submit_uploaded_draft(
            runtime, session, actor, identity, body.expected_revision
        )

    return await mutate(
        request, body, "submit_work_draft", identity, ("student",), ResourceResult, work
    )


@router.post(
    "/courses/{identity}",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "update_course"},
)
async def update_course(
    identity: UUID, request: Request, body: WorkspaceCommand[CourseInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        course = await row(session, Course, actor.organization_id, identity, lock=True)
        revision(course.revision, body.expected_revision)
        if body.payload.owner_id:
            owner = await session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == actor.organization_id,
                    OrganizationMembership.user_id == body.payload.owner_id,
                    OrganizationMembership.status == "active",
                )
            )
            if owner is None or "methodologist" not in owner.roles:
                raise WorkspaceFailure(
                    "invalid_owner", "Ответственный должен быть координатором.", 422
                )
        course.title, course.description = body.payload.title, body.payload.description
        details = await session.scalar(
            select(WorkspaceCourseDetails).where(
                WorkspaceCourseDetails.organization_id == actor.organization_id,
                WorkspaceCourseDetails.id == identity,
            )
        )
        if details is None:
            details = WorkspaceCourseDetails(id=identity, organization_id=actor.organization_id)
            session.add(details)
        details.owner_id = body.payload.owner_id
        details.stepik_url = body.payload.stepik_url
        course.revision += 1
        return ResourceResult(id=identity, revision=course.revision)

    return await mutate(
        request, body, "update_course", identity, ("methodologist",), ResourceResult, work
    )


@router.post(
    "/course-runs/{identity}",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "update_course_run"},
)
async def update_course_run(
    identity: UUID, request: Request, body: WorkspaceCommand[CourseRunInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        run = await row(session, CourseRun, actor.organization_id, identity, lock=True)
        revision(run.revision, body.expected_revision)
        run.title, run.starts_at, run.ends_at, run.timezone = (
            body.payload.title,
            body.payload.starts_at,
            body.payload.ends_at,
            body.payload.timezone,
        )
        settings = await session.scalar(
            select(WorkspaceRunSettings).where(
                WorkspaceRunSettings.organization_id == actor.organization_id,
                WorkspaceRunSettings.id == identity,
            )
        )
        if settings is None:
            settings = WorkspaceRunSettings(
                id=identity, organization_id=actor.organization_id, revision=0
            )
            session.add(settings)
        settings.priority = body.payload.priority
        settings.revision += 1
        run.revision += 1
        return ResourceResult(id=identity, revision=run.revision)

    return await mutate(
        request, body, "update_course_run", identity, ("methodologist",), ResourceResult, work
    )


@router.get("/reviews/{identity}/draft", response_model=ReviewDraftView)
async def get_review_draft(identity: UUID, request: Request) -> ReviewDraftView:
    from review_platform.application.projections.review_detail import (
        _decision,
        _note,
        _revision_summary,
    )
    from review_platform.infrastructure.db.models import (
        ReviewCriterionDecision,
        ReviewNote,
        ReviewRevision,
    )

    runtime, actor = await context(request)
    require_roles(actor, "reviewer", "methodologist")
    async with runtime.transaction() as session:
        iteration = await row(session, ReviewIteration, actor.organization_id, identity)
        await course_scope(session, actor, iteration.course_run_id)
        current = (
            await row(session, ReviewRevision, actor.organization_id, iteration.current_revision_id)
            if iteration.current_revision_id
            else None
        )
        decisions = (
            (
                await session.scalars(
                    select(ReviewCriterionDecision)
                    .where(
                        ReviewCriterionDecision.organization_id == actor.organization_id,
                        ReviewCriterionDecision.review_revision_id == current.id,
                    )
                    .order_by(ReviewCriterionDecision.criterion_id)
                )
            ).all()
            if current
            else []
        )
        notes = (
            (
                await session.scalars(
                    select(ReviewNote)
                    .where(
                        ReviewNote.organization_id == actor.organization_id,
                        ReviewNote.review_revision_id == current.id,
                    )
                    .order_by(ReviewNote.position)
                )
            ).all()
            if current
            else []
        )
        return ReviewDraftView.model_validate(
            {
                "revision": iteration.revision,
                "status": iteration.status,
                "current_review_revision_id": current.id if current else None,
                "current_review_revision": _revision_summary(current) if current else None,
                "criterion_decisions": [_decision(d) for d in decisions],
                "review_notes": [_note(n) for n in notes],
            }
        )


@router.get("/search", response_model=WorkspaceSearchView)
async def search_workspace(
    request: Request, q: str = Query(default="", max_length=200)
) -> WorkspaceSearchView:
    from review_platform.application.workspace.search import workspace_search

    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await workspace_search(runtime, session, actor, q)


@router.post(
    "/homeworks/{identity}/title",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "update_workspace_homework"},
)
async def update_workspace_homework(
    identity: UUID, request: Request, body: WorkspaceCommand[HomeworkTitleInput]
) -> ResourceResult:
    async def work(
        runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor
    ) -> ResourceResult:
        from review_platform.infrastructure.db.models import Course

        homework = await row(session, Homework, actor.organization_id, identity, lock=True)
        course = await row(session, Course, actor.organization_id, homework.course_id, lock=True)
        if course.status != "active":
            raise WorkspaceFailure("archived", "Изменения в архивном курсе недоступны.")
        revision(homework.revision, body.expected_revision)
        homework.title = body.payload.title
        homework.revision += 1
        return ResourceResult(id=homework.id, revision=homework.revision)

    return await mutate(
        request,
        body,
        "update_workspace_homework",
        identity,
        ("methodologist",),
        ResourceResult,
        work,
    )


@router.get("/artifacts/{identity}/open", include_in_schema=False)
async def open_snapshot(identity: UUID, request: Request) -> Response:
    from fastapi.responses import RedirectResponse

    download = await artifact_download(identity, request)
    return RedirectResponse(download.url, status_code=307)
