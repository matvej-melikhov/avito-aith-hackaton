"""Student-facing registries expose provider identifiers, never personal names."""

from uuid import UUID

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from review_platform.infrastructure.db.models import ExternalIdentity, OrganizationMembership, User


def student_identifier(
    user_id: ColumnElement[UUID] | InstrumentedAttribute[UUID],
) -> ColumnElement[str]:
    provider_id = (
        select(func.min(ExternalIdentity.subject))
        .where(
            ExternalIdentity.user_id == user_id,
            ExternalIdentity.provider == "stepik",
            ExternalIdentity.issuer == "https://stepik.org",
            ExternalIdentity.status == "active",
        )
        .correlate_except(ExternalIdentity)
        .scalar_subquery()
    )
    # UUID7 prefixes share their timestamp; use its stable tail to distinguish local users.
    return func.coalesce(provider_id, func.substr(cast(user_id, String), -12))


async def student_labels(
    session: AsyncSession, organization_id: UUID, users: list[UUID]
) -> dict[UUID, str]:
    rows = (
        await session.execute(
            select(User.id, student_identifier(User.id))
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == organization_id, User.id.in_(users))
        )
    ).all()
    return {identity: f"Студент {identifier}" for identity, identifier in rows}
