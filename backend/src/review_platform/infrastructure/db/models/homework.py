"""Immutable homework versions and per-CourseRun publication history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
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
    TimestampMixin,
    UTCDateTime,
    string_enum_check,
    tenant_candidate_key,
    tenant_foreign_key,
)

COURSE_RUN_HOMEWORK_STATUSES = ("draft", "active", "archived")


def _json_list_default() -> list[Any]:
    return []


class Homework(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Stable homework identity inside a Course."""

    __tablename__ = "homework"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("course_id", "course", name="fk_homework_org_course"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_homework_org_course", "organization_id", "course_id"),
    )

    course_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)


class HomeworkVersion(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Immutable requirements snapshot with no global publication state."""

    __tablename__ = "homework_version"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("homework_id", "homework", name="fk_homework_version_org_homework"),
        UniqueConstraint(
            "organization_id",
            "homework_id",
            "id",
            name="uq_homework_version_org_homework_id",
        ),
        UniqueConstraint(
            "organization_id",
            "homework_id",
            "version_number",
            name="uq_homework_version_org_homework_number",
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("max_score >= 0", name="max_score_nonnegative"),
        CheckConstraint(
            "estimated_review_minutes >= 1 AND estimated_review_minutes <= 10080",
            name="estimated_review_minutes_range",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )

    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    review_only: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("0")
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    student_text: Mapped[str] = mapped_column(Text, nullable=False)
    max_score: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    artifact_kinds: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=_json_list_default,
    )
    estimated_review_minutes: Mapped[int] = mapped_column(nullable=False)


class CriterionSet(TenantEntityMixin, TimestampMixin, Base):
    """The one immutable ordered criterion collection for a HomeworkVersion."""

    __tablename__ = "criterion_set"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "homework_version_id",
            "homework_version",
            name="fk_criterion_set_org_homework_version",
        ),
        UniqueConstraint(
            "organization_id",
            "homework_version_id",
            name="uq_criterion_set_org_homework_version",
        ),
    )

    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)


class Criterion(TenantEntityMixin, TimestampMixin, Base):
    """Stable-key scoring criterion inside one CriterionSet."""

    __tablename__ = "criterion"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "criterion_set_id",
            "criterion_set",
            name="fk_criterion_org_criterion_set",
        ),
        UniqueConstraint(
            "organization_id",
            "criterion_set_id",
            "stable_key",
            name="uq_criterion_org_set_stable_key",
        ),
        UniqueConstraint(
            "organization_id",
            "criterion_set_id",
            "position",
            name="uq_criterion_org_set_position",
        ),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("max_points >= 0", name="max_points_nonnegative"),
    )

    criterion_set_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    max_points: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )


class CourseRunHomework(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Per-CourseRun homework state and its relation-local current pointer."""

    __tablename__ = "course_run_homework"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "course_run_id",
            "course_run",
            name="fk_course_run_homework_org_course_run",
        ),
        tenant_foreign_key(
            "homework_id",
            "homework",
            name="fk_course_run_homework_org_homework",
        ),
        UniqueConstraint(
            "organization_id",
            "homework_id",
            "id",
            name="uq_course_run_homework_org_homework_id",
        ),
        UniqueConstraint(
            "organization_id",
            "course_run_id",
            "homework_id",
            name="uq_course_run_homework_org_run_homework",
        ),
        ForeignKeyConstraint(
            ["organization_id", "id", "current_publication_id"],
            [
                "course_run_homework_publication.organization_id",
                "course_run_homework_publication.course_run_homework_id",
                "course_run_homework_publication.id",
            ],
            name="fk_course_run_homework_current_publication",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            string_enum_check("status", COURSE_RUN_HOMEWORK_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_course_run_homework_org_run", "organization_id", "course_run_id"),
    )

    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    current_publication_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="draft",
        server_default=text("'draft'"),
    )


class CourseRunHomeworkPublication(TenantEntityMixin, Base):
    """Append-only publication of one immutable version to one relation."""

    __tablename__ = "course_run_homework_publication"
    __table_args__ = (
        tenant_candidate_key(),
        UniqueConstraint(
            "organization_id",
            "course_run_homework_id",
            "id",
            name="uq_crh_publication_org_relation_id",
        ),
        UniqueConstraint(
            "organization_id",
            "course_run_homework_id",
            "publication_sequence",
            name="uq_crh_publication_org_relation_sequence",
        ),
        ForeignKeyConstraint(
            ["organization_id", "homework_id", "course_run_homework_id"],
            [
                "course_run_homework.organization_id",
                "course_run_homework.homework_id",
                "course_run_homework.id",
            ],
            name="fk_crh_publication_org_relation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "homework_id", "homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_crh_publication_org_homework_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("publication_sequence >= 1", name="publication_sequence_positive"),
        CheckConstraint(
            "submission_deadline <= review_deadline",
            name="deadline_order",
        ),
        Index(
            "ix_crh_publication_org_relation_published",
            "organization_id",
            "course_run_homework_id",
            "published_at",
        ),
    )

    course_run_homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    publication_sequence: Mapped[int] = mapped_column(nullable=False)
    submission_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    review_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


__all__ = [
    "COURSE_RUN_HOMEWORK_STATUSES",
    "CourseRunHomework",
    "CourseRunHomeworkPublication",
    "Criterion",
    "CriterionSet",
    "Homework",
    "HomeworkVersion",
]
