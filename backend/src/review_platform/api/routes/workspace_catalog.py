"""Read-only coordinator catalog extension."""

from uuid import UUID

from fastapi import APIRouter, Request

from review_platform.api.routes.workspace import WorkspaceRoute, context
from review_platform.application.workspace.catalog_editor import course_homeworks
from review_platform.contracts.workspace import CoordinatorHomeworkList

router = APIRouter(prefix="/v2", tags=["workspace-v2"], route_class=WorkspaceRoute)


@router.get("/courses/{identity}/homeworks", response_model=CoordinatorHomeworkList)
async def homeworks(request: Request, identity: UUID) -> CoordinatorHomeworkList:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await course_homeworks(session, actor, identity)
