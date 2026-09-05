"""Coordinator catalog includes stable homework drafts without exposing them to students."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import require_roles, row
from review_platform.contracts.workspace import CoordinatorHomeworkItem, CoordinatorHomeworkList
from review_platform.infrastructure.db.models import (
    Course,
    CourseRunHomework,
    Homework,
    HomeworkVersion,
)


async def course_homeworks(
    session: AsyncSession, actor: RequestActor, course_id: UUID
) -> CoordinatorHomeworkList:
    require_roles(actor, "methodologist")
    await row(session, Course, actor.organization_id, course_id)
    homework_rows = (
        await session.scalars(
            select(Homework)
            .where(
                Homework.organization_id == actor.organization_id,
                Homework.course_id == course_id,
            )
            .order_by(Homework.created_at, Homework.id)
        )
    ).all()
    ids = [homework.id for homework in homework_rows]
    version_rows = (
        await session.execute(
            select(HomeworkVersion.homework_id, func.max(HomeworkVersion.version_number))
            .where(
                HomeworkVersion.organization_id == actor.organization_id,
                HomeworkVersion.homework_id.in_(ids),
            )
            .group_by(HomeworkVersion.homework_id)
        )
    ).all()
    latest = {homework_id: number for homework_id, number in version_rows}
    publications = (
        await session.scalars(
            select(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == actor.organization_id,
                CourseRunHomework.homework_id.in_(ids),
                CourseRunHomework.current_publication_id.is_not(None),
            )
            .order_by(CourseRunHomework.course_run_id)
        )
    ).all()
    return CoordinatorHomeworkList(
        course_id=course_id,
        items=[
            CoordinatorHomeworkItem(
                id=homework.id,
                title=homework.title,
                revision=homework.revision,
                latest_version_number=latest.get(homework.id),
                published_run_ids=[
                    publication.course_run_id
                    for publication in publications
                    if publication.homework_id == homework.id
                ],
            )
            for homework in homework_rows
        ],
    )
