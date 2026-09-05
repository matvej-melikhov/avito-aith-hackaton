"""Coordinator search scoped to one organization and public student identifiers."""

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import require_roles
from review_platform.application.workspace.projections import WorkspaceQueries
from review_platform.contracts.workspace import SearchHomeworkView, WorkspaceSearchView
from review_platform.infrastructure.db.models import CourseRunHomework, Homework


async def workspace_search(
    runtime: FoundationRuntime,
    session: AsyncSession,
    actor: RequestActor,
    query: str,
) -> WorkspaceSearchView:
    require_roles(actor, "methodologist")
    query = query.strip()
    if not query:
        return WorkspaceSearchView(students=[], homeworks=[])
    works = await WorkspaceQueries(runtime, session).works(
        actor, search=query, search_student_only=True, limit=20
    )
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    homeworks = (
        await session.execute(
            select(Homework, CourseRunHomework.course_run_id)
            .outerjoin(
                CourseRunHomework,
                and_(
                    CourseRunHomework.homework_id == Homework.id,
                    CourseRunHomework.organization_id == Homework.organization_id,
                ),
            )
            .where(
                Homework.organization_id == actor.organization_id,
                Homework.title.ilike(f"%{escaped}%", escape="\\"),
            )
            .order_by(Homework.title, Homework.id, CourseRunHomework.course_run_id)
            .limit(20)
        )
    ).all()
    return WorkspaceSearchView(
        students=works.items,
        homeworks=[
            SearchHomeworkView(id=homework.id, title=homework.title, course_run_id=run_id)
            for homework, run_id in homeworks
        ],
    )
