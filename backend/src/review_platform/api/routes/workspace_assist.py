"""Reviewer-only v2 assist and explicit AI provenance on human draft saves."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import JsonValue

from review_platform.api.routes.review_composition import (
    ReviewCompositionError,
    dispatch_review_mutation,
)
from review_platform.api.routes.workspace import WorkspaceRoute, context
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    finish,
    replay,
    reserve,
    row,
)
from review_platform.application.workspace.review_assist import ReviewAssistService
from review_platform.application.workspace.reviews import lock_review_scope
from review_platform.contracts.commands import WireCommand
from review_platform.contracts.workspace import (
    EmptyInput,
    ResourceResult,
    ReviewAssistResult,
    ReviewAssistView,
    WorkspaceCommand,
    WorkspaceReviewSaveInput,
)
from review_platform.infrastructure.db.models.workspace import ReviewAIChoice, ReviewAssistRun

router = APIRouter(prefix="/v2", tags=["workspace-v2"], route_class=WorkspaceRoute)


@router.get("/reviews/{identity}/assist", response_model=ReviewAssistView | None)
async def latest_assist(request: Request, identity: UUID) -> ReviewAssistView | None:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await ReviewAssistService(runtime, session).latest(actor, identity)


@router.get("/review-assists/{identity}", response_model=ReviewAssistView)
async def get_assist(request: Request, identity: UUID) -> ReviewAssistView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await ReviewAssistService(runtime, session).get(actor, identity)


@router.post(
    "/reviews/{identity}/assist",
    response_model=ReviewAssistView,
    openapi_extra={"x-command-name": "start_review_assist"},
)
async def start_assist(
    request: Request, identity: UUID, command: WorkspaceCommand[EmptyInput]
) -> ReviewAssistView:
    runtime, actor = await context(request)
    if not runtime.settings.workspace_enabled or not (
        runtime.settings.workspace_fixtures or runtime.settings.workspace_ai_url
    ):
        raise WorkspaceFailure("ai_unavailable", "Компонент AI не настроен.", 503)
    async with runtime.transaction() as session:
        receipt, created = await reserve(
            session, runtime, actor, command, "start_review_assist", identity
        )
        if not created:
            return ReviewAssistView.model_validate(replay(receipt))
        view = await ReviewAssistService(runtime, session).start(
            actor, identity, command.expected_revision
        )
        finish(session, runtime, actor, receipt, view.model_dump(mode="json"))
        return view


@router.post(
    "/review-assists/{identity}/retry",
    response_model=ReviewAssistView,
    openapi_extra={"x-command-name": "retry_review_assist"},
)
async def retry_assist(
    request: Request, identity: UUID, command: WorkspaceCommand[EmptyInput]
) -> ReviewAssistView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        receipt, created = await reserve(
            session, runtime, actor, command, "retry_review_assist", identity
        )
        if not created:
            return ReviewAssistView.model_validate(replay(receipt))
        view = await ReviewAssistService(runtime, session).retry(
            actor, identity, command.expected_revision
        )
        finish(session, runtime, actor, receipt, view.model_dump(mode="json"))
        return view


@router.post(
    "/reviews/{identity}/draft",
    response_model=ResourceResult,
    openapi_extra={"x-command-name": "save_workspace_review"},
)
async def save_assisted_draft(
    request: Request, identity: UUID, command: WorkspaceCommand[WorkspaceReviewSaveInput]
) -> ResourceResult:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        receipt, created = await reserve(
            session, runtime, actor, command, "save_workspace_review", identity
        )
        if not created:
            return ResourceResult.model_validate(replay(receipt))
        iteration = await lock_review_scope(session, actor, identity)
        source = command.payload.ai_run_id
        if command.payload.signal_decisions and source is None:
            raise WorkspaceFailure(
                "signal_source_required", "Для решения по сигналу выберите AI-разбор.", 422
            )
        if source:
            run = await row(session, ReviewAssistRun, actor.organization_id, source)
            if (
                run.iteration_id != identity
                or run.status != "succeeded"
                or run.artifact_id != iteration.artifact_version_id
                or run.homework_version_id != iteration.homework_version_id
            ):
                raise WorkspaceFailure(
                    "ai_source_stale", "Рекомендация относится к другим входным данным."
                )
            result_data = ReviewAssistResult.model_validate(run.result) if run.result else None
            signal = result_data.authorship_signal if result_data else None
            if any(
                signal is None or identity != signal.id
                for identity in command.payload.signal_decisions
            ):
                raise WorkspaceFailure(
                    "unknown_signal", "Сигнал не принадлежит выбранному AI-разбору.", 422
                )
        core = WireCommand.model_validate(
            {
                **command.model_dump(mode="json"),
                "command_name": "save_review_revision",
                "revision_target": "review_iteration",
                "payload": command.payload.draft.model_dump(mode="json"),
            }
        )
        try:
            result = await dispatch_review_mutation(
                runtime=runtime, actor=actor, command=core, transaction=session
            )
        except ReviewCompositionError as error:
            raise WorkspaceFailure("review_conflict", str(error)) from error
        assert result is not None
        view = ResourceResult(
            id=UUID(result["review_revision_id"]), revision=result["review_iteration_revision"]
        )
        if source:
            session.add(
                ReviewAIChoice(
                    id=view.id,
                    organization_id=actor.organization_id,
                    run_id=source,
                    signal_decisions=dict(command.payload.signal_decisions),
                )
            )
        finish(
            session,
            runtime,
            actor,
            receipt,
            view.model_dump(mode="json"),
            cast(dict[str, JsonValue], {"ai_run_id": str(source) if source else None}),
        )
        return view
