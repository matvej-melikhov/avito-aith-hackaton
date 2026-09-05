"""Public submission-source policy bound to an immutable homework version."""

from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.workspace.common import WorkspaceFailure, row
from review_platform.infrastructure.db.models import CourseRunHomework, CourseRunHomeworkPublication
from review_platform.infrastructure.db.models.workspace import HomeworkPrivateDetails

DEFAULT_SOURCES = ["upload", "github", "google_docs"]


async def allowed_sources(session: AsyncSession, org: UUID, version_id: UUID) -> list[str]:
    details = await session.scalar(
        select(HomeworkPrivateDetails).where(
            HomeworkPrivateDetails.organization_id == org, HomeworkPrivateDetails.id == version_id
        )
    )
    return (
        list(details.allowed_sources)
        if details and details.allowed_sources is not None
        else list(DEFAULT_SOURCES)
    )


async def require_source(session: AsyncSession, org: UUID, version_id: UUID, source: str) -> None:
    if source not in await allowed_sources(session, org, version_id):
        raise WorkspaceFailure(
            "source_not_allowed", "Этот способ сдачи не разрешён для задания.", 422
        )


async def require_publication_source(
    session: AsyncSession, publication: CourseRunHomework, artifact_url: str, upload_id: UUID | None
) -> None:
    if not publication.current_publication_id:
        raise WorkspaceFailure("not_published", "Задание ещё не опубликовано.")
    published = await row(
        session,
        CourseRunHomeworkPublication,
        publication.organization_id,
        publication.current_publication_id,
    )
    source = (
        "upload"
        if upload_id
        else ("github" if urlsplit(artifact_url).hostname == "github.com" else "google_docs")
    )
    await require_source(
        session, publication.organization_id, published.homework_version_id, source
    )
