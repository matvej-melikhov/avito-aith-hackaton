"""ReviewCase and immutable ReviewIteration provenance spine."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
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

REVIEW_ITERATION_ORIGINS = (
    "initial",
    "resubmission",
    "correction",
    "requirements_migration",
)
REVIEW_ITERATION_STATUSES = (
    "queued",
    "in_review",
    "ready_to_publish",
    "published",
    "canceled",
)


class ReviewCase(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Stable review identity for one student, CourseRun, and Homework."""

    __tablename__ = "review_case"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("course_run_id", "course_run", name="fk_review_case_org_course_run"),
        tenant_foreign_key("homework_id", "homework", name="fk_review_case_org_homework"),
        UniqueConstraint(
            "organization_id",
            "id",
            "course_run_id",
            "homework_id",
            "student_id",
            name="uq_review_case_org_id_scope",
        ),
        UniqueConstraint(
            "organization_id",
            "course_run_id",
            "homework_id",
            "student_id",
            name="uq_review_case_org_run_homework_student",
        ),
        ForeignKeyConstraint(
            ["organization_id", "id", "current_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_case_current_iteration",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_review_case_org_student", "organization_id", "student_id"),
    )

    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    student_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_iteration_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)


class ReviewIteration(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Immutable selected inputs plus mutable review workflow state."""

    __tablename__ = "review_iteration"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
            [
                "organization_id",
                "review_case_id",
                "course_run_id",
                "homework_id",
                "student_id",
            ],
            [
                "review_case.organization_id",
                "review_case.id",
                "review_case.course_run_id",
                "review_case.homework_id",
                "review_case.student_id",
            ],
            name="fk_review_iteration_org_case_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "submission_version_id", "course_run_id", "homework_id"],
            [
                "submission_version.organization_id",
                "submission_version.id",
                "submission_version.course_run_id",
                "submission_version.homework_id",
            ],
            name="fk_review_iteration_org_submission_scope",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "artifact_version_id",
            "artifact_version",
            name="fk_review_iteration_org_artifact_version",
        ),
        ForeignKeyConstraint(
            ["organization_id", "homework_id", "homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_review_iteration_org_homework_version",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "criterion_set_id",
            "criterion_set",
            name="fk_review_iteration_org_criterion_set",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_case_id", "predecessor_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_iteration_org_predecessor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "review_case_id",
            "id",
            name="uq_review_iteration_org_case_id",
        ),
        UniqueConstraint(
            "organization_id",
            "review_case_id",
            "iteration_number",
            name="uq_review_iteration_org_case_number",
        ),
        UniqueConstraint(
            "organization_id",
            "initial_submission_version_id",
            name="uq_review_iteration_org_initial_submission",
        ),
        CheckConstraint("iteration_number >= 1", name="iteration_number_positive"),
        CheckConstraint(
            string_enum_check("origin", REVIEW_ITERATION_ORIGINS),
            name="origin",
        ),
        CheckConstraint(
            string_enum_check("status", REVIEW_ITERATION_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_review_iteration_org_status", "organization_id", "status"),
    )

    review_case_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    student_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    iteration_number: Mapped[int] = mapped_column(nullable=False)
    submission_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    initial_submission_version_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE,
        Computed(
            "CASE WHEN origin = 'initial' THEN submission_version_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
    artifact_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    criterion_set_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    effective_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    responsible_reviewer_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_revision_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    predecessor_iteration_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)


__all__ = [
    "REVIEW_ITERATION_ORIGINS",
    "REVIEW_ITERATION_STATUSES",
    "ReviewCase",
    "ReviewIteration",
]
