"""Student-only cabinet list, independently scoped from reviewer registries."""

from typing import Literal

from fastapi import APIRouter, Query, Request

from review_platform.api.routes.workspace import WorkspaceRoute, context
from review_platform.application.workspace.student_works import student_homeworks
from review_platform.contracts.workspace import StudentHomeworkList

router = APIRouter(prefix="/v2", tags=["workspace-v2"], route_class=WorkspaceRoute)


@router.get("/student/homeworks", response_model=StudentHomeworkList)
async def list_student_homeworks(
    request: Request,
    state: Literal["", "in_progress", "completed"] = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
) -> StudentHomeworkList:
    runtime, actor = await context(request)
    async with runtime.transaction() as session:
        return await student_homeworks(session, actor, state=state, offset=offset, limit=limit)
