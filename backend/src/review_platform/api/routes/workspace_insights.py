"""Coordinator-only aggregate inspection of one course run."""

from uuid import UUID

from fastapi import APIRouter, Request

from review_platform.api.routes.workspace import WorkspaceRoute, context
from review_platform.application.workspace.insights import workspace_insights
from review_platform.contracts.workspace import WorkspaceInsightsView

router = APIRouter(prefix="/v2", tags=["workspace-v2"], route_class=WorkspaceRoute)


@router.get("/course-runs/{identity}/insights", response_model=WorkspaceInsightsView)
async def insights(
    request: Request, identity: UUID, homework_id: UUID | None = None
) -> WorkspaceInsightsView:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await workspace_insights(runtime, session, actor, identity, homework_id)
