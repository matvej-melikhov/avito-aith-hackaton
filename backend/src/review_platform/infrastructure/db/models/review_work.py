"""Reviewer preferences and append-only participation history."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
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

REVIEW_RESPONSIBILITY_ACTIONS = ("started", "joined", "released", "completed")


class AvailabilityPlan(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Advisory reviewer capacity, never a hard work-assignment limit."""

    __tablename__ = "availability_plan"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
            ["organization_id", "reviewer_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_availability_plan_org_reviewer_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "reviewer_id",
            name="uq_availability_plan_org_reviewer",
        ),
        CheckConstraint("planned_minutes >= 0", name="planned_minutes_nonnegative"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_availability_plan_org_until", "organization_id", "until_at"),
    )

    reviewer_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    planned_minutes: Mapped[int] = mapped_column(nullable=False)
    until_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ReviewerCourseSelection(TenantEntityMixin, TimestampMixin, Base):
    """A reviewer's current opt-in state for one exact CourseRun."""

    __tablename__ = "reviewer_course_selection"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "course_run_id",
            "course_run",
            name="fk_reviewer_course_selection_org_course_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewer_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_reviewer_course_selection_org_reviewer_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "course_run_id",
            "reviewer_id",
            name="uq_reviewer_course_selection_org_run_reviewer",
        ),
        Index(
            "ix_reviewer_course_selection_org_reviewer_active",
            "organization_id",
            "reviewer_id",
            "active",
        ),
        Index(
            "ix_reviewer_course_selection_org_run_active",
            "organization_id",
            "course_run_id",
            "active",
        ),
    )

    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    reviewer_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )


class ReviewResponsibility(TenantEntityMixin, Base):
    """Append-only, informational reviewer participation event."""

    __tablename__ = "review_responsibility"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "review_case_id",
            "review_case",
            name="fk_review_responsibility_org_review_case",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_case_id", "review_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_responsibility_org_case_iteration",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewer_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_review_responsibility_org_reviewer_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "actor_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_review_responsibility_org_actor_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            string_enum_check("action", REVIEW_RESPONSIBILITY_ACTIONS),
            name="action",
        ),
        Index(
            "ix_review_responsibility_org_case_occurred",
            "organization_id",
            "review_case_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_review_responsibility_org_iteration_occurred",
            "organization_id",
            "review_iteration_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_review_responsibility_org_reviewer_occurred",
            "organization_id",
            "reviewer_id",
            "occurred_at",
            "id",
        ),
    )

    review_case_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    review_iteration_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    reviewer_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


__all__ = [
    "REVIEW_RESPONSIBILITY_ACTIONS",
    "AvailabilityPlan",
    "ReviewResponsibility",
    "ReviewerCourseSelection",
]
