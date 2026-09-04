"""Course, CourseRun, roster, and destination persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from review_platform.infrastructure.db.base import (
    UUID_TYPE,
    Base,
    RevisionMixin,
    TenantEntityMixin,
    TenantOwnedMixin,
    TimestampMixin,
    UTCDateTime,
    UUIDIdentityMixin,
    string_enum_check,
    tenant_candidate_key,
    tenant_foreign_key,
)

COURSE_SOURCE_KINDS = ("external", "standalone")
ARCHIVE_STATUSES = ("active", "archived")
COURSE_RUN_STATUSES = ("draft", "active", "archived")
COURSE_MEMBERSHIP_KINDS = ("student", "reviewer")
COURSE_MEMBERSHIP_SOURCES = ("imported", "invitation", "self_selected")
COURSE_MEMBERSHIP_STATUSES = ("active", "removed", "archived")
DESTINATION_KINDS = ("stepik", "github")


class Course(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Stable course identity within an Organization."""

    __tablename__ = "course"
    __table_args__ = (
        tenant_candidate_key(),
        CheckConstraint(
            string_enum_check("source_kind", COURSE_SOURCE_KINDS),
            name="source_kind",
        ),
        CheckConstraint(string_enum_check("status", ARCHIVE_STATUSES), name="status"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_course_org_status", "organization_id", "status"),
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )


class ExternalCourseBinding(UUIDIdentityMixin, TenantOwnedMixin, TimestampMixin, Base):
    """Versioned provider binding for repeatable course synchronization."""

    __tablename__ = "external_course_binding"
    __table_args__ = (
        tenant_foreign_key(
            "course_id",
            "course",
            name="fk_external_course_binding_org_course",
        ),
        ForeignKeyConstraint(
            ["organization_id", "credential_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_external_course_binding_org_credential_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            "binding_version",
            name="uq_external_course_binding_org_id_version",
        ),
        UniqueConstraint(
            "organization_id",
            "provider",
            "external_course_id",
            "binding_version",
            name="uq_external_course_binding_org_provider_course_version",
        ),
        CheckConstraint(string_enum_check("status", ARCHIVE_STATUSES), name="status"),
        CheckConstraint("binding_version >= 1", name="binding_version_positive"),
        CheckConstraint(
            "credential_binding_version >= 1",
            name="credential_binding_version_positive",
        ),
    )

    course_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_course_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_version: Mapped[int] = mapped_column(nullable=False)
    binding_version: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class CourseRun(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """One independently archived cohort of a Course."""

    __tablename__ = "course_run"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("course_id", "course", name="fk_course_run_org_course"),
        UniqueConstraint(
            "organization_id",
            "course_id",
            "external_run_id",
            name="uq_course_run_org_course_external_run",
        ),
        CheckConstraint(
            string_enum_check("status", COURSE_RUN_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR starts_at <= ends_at",
            name="date_order",
        ),
        Index("ix_course_run_org_course_status", "organization_id", "course_id", "status"),
    )

    course_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    external_run_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default=text("'draft'")
    )


class CourseMembership(TenantEntityMixin, TimestampMixin, Base):
    """Current and historically relevant participation in one CourseRun."""

    __tablename__ = "course_membership"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "course_run_id",
            "course_run",
            name="fk_course_membership_org_course_run",
        ),
        UniqueConstraint(
            "organization_id",
            "course_run_id",
            "user_id",
            "kind",
            name="uq_course_membership_org_run_user_kind",
        ),
        CheckConstraint(
            string_enum_check("kind", COURSE_MEMBERSHIP_KINDS),
            name="kind",
        ),
        CheckConstraint(
            string_enum_check("source", COURSE_MEMBERSHIP_SOURCES),
            name="source",
        ),
        CheckConstraint(
            string_enum_check("status", COURSE_MEMBERSHIP_STATUSES),
            name="status",
        ),
        Index("ix_course_membership_org_user", "organization_id", "user_id"),
    )

    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    external_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class DestinationBinding(
    UUIDIdentityMixin,
    TenantOwnedMixin,
    RevisionMixin,
    TimestampMixin,
    Base,
):
    """Versioned required publication recipient for one CourseRun."""

    __tablename__ = "destination_binding"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "id",
            "binding_version",
            name="uq_destination_binding_org_id_version",
        ),
        tenant_foreign_key(
            "course_run_id",
            "course_run",
            name="fk_destination_binding_org_course_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "credential_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_destination_binding_org_credential_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(string_enum_check("kind", DESTINATION_KINDS), name="kind"),
        CheckConstraint(string_enum_check("status", ARCHIVE_STATUSES), name="status"),
        CheckConstraint("binding_version >= 1", name="binding_version_positive"),
        CheckConstraint(
            "credential_binding_version >= 1",
            name="credential_binding_version_positive",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index(
            "ix_destination_binding_org_run_status",
            "organization_id",
            "course_run_id",
            "status",
        ),
    )

    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    binding_version: Mapped[int] = mapped_column(primary_key=True)
    recipient_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    credential_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_version: Mapped[int] = mapped_column(nullable=False)
    required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )


__all__ = [
    "ARCHIVE_STATUSES",
    "COURSE_MEMBERSHIP_KINDS",
    "COURSE_MEMBERSHIP_SOURCES",
    "COURSE_MEMBERSHIP_STATUSES",
    "COURSE_RUN_STATUSES",
    "COURSE_SOURCE_KINDS",
    "DESTINATION_KINDS",
    "Course",
    "CourseMembership",
    "CourseRun",
    "DestinationBinding",
    "ExternalCourseBinding",
]
