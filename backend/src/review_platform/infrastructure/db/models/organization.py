"""The installation's explicit tenant anchor."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from review_platform.infrastructure.db.base import (
    Base,
    RevisionMixin,
    TimestampMixin,
    UUIDIdentityMixin,
    string_enum_check,
)

ORGANIZATION_STATUSES = ("active", "archived")


class Organization(UUIDIdentityMixin, RevisionMixin, TimestampMixin, Base):
    """Single-installation organization, retained as an explicit boundary."""

    __tablename__ = "organization"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organization_slug"),
        CheckConstraint(
            string_enum_check("status", ORGANIZATION_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )

    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )


__all__ = ["ORGANIZATION_STATUSES", "Organization"]
