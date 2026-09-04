"""Create submissions, artifact promotion, and initial review-case spine.

Revision ID: 0004_submissions_and_artifacts
Revises: 0003_homework_versions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_submissions_and_artifacts"
down_revision: str | None = "0003_homework_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid(as_uuid=True, native_uuid=False)


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
    )


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organization.id"],
        name=f"fk_{table}_organization_id_organization",
        ondelete="RESTRICT",
    )


def _tenant_uq(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id")


def upgrade() -> None:
    op.create_table(
        "artifact_reference",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("original_url", sa.String(2048), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=True),
        sa.Column("read_capability", sa.String(32), nullable=False),
        sa.Column("feedback_capability", sa.String(32), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('github', 'google_docs')", name="ck_artifact_reference_provider"
        ),
        sa.CheckConstraint(
            "read_capability IN ('available', 'requires_action', 'unavailable')",
            name="ck_artifact_reference_read_capability",
        ),
        sa.CheckConstraint(
            "feedback_capability IN ('available', 'not_supported', 'requires_action')",
            name="ck_artifact_reference_feedback_capability",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_artifact_reference_revision_nonnegative"),
        _org_fk("artifact_reference"),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_reference"),
        _tenant_uq("artifact_reference"),
    )
    op.create_index(
        "ix_artifact_reference_org_provider",
        "artifact_reference",
        ["organization_id", "provider"],
        unique=False,
    )

    op.create_table(
        "artifact_version",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("artifact_reference_id", UUID, nullable=False),
        sa.Column("provider_version", sa.String(256), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint("byte_size >= 1", name="ck_artifact_version_byte_size_positive"),
        _org_fk("artifact_version"),
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_reference_id"],
            ["artifact_reference.organization_id", "artifact_reference.id"],
            name="fk_artifact_version_org_reference",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_version"),
        _tenant_uq("artifact_version"),
        sa.UniqueConstraint(
            "organization_id",
            "artifact_reference_id",
            "id",
            name="uq_artifact_version_org_reference_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "artifact_reference_id",
            "content_digest",
            name="uq_artifact_version_org_reference_digest",
        ),
    )

    op.create_table(
        "submission",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("course_run_homework_id", UUID, nullable=False),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("student_id", UUID, nullable=False),
        sa.Column("current_predeadline_version_id", UUID, nullable=True),
        sa.CheckConstraint("revision >= 0", name="ck_submission_revision_nonnegative"),
        _org_fk("submission"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_homework_id"],
            ["course_run_homework.organization_id", "course_run_homework.id"],
            name="fk_submission_org_course_run_homework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_submission_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id"],
            ["homework.organization_id", "homework.id"],
            name="fk_submission_org_homework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["user.id"], name="fk_submission_student_id_user", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_submission"),
        _tenant_uq("submission"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "course_run_id",
            "homework_id",
            name="uq_submission_org_id_run_homework",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "course_run_id",
            "homework_id",
            "student_id",
            name="uq_submission_org_run_homework_student",
        ),
    )
    op.create_index(
        "ix_submission_org_student", "submission", ["organization_id", "student_id"], unique=False
    )

    op.create_table(
        "submission_version",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("submission_id", UUID, nullable=False),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("homework_version_id", UUID, nullable=False),
        sa.Column("artifact_reference_id", UUID, nullable=False),
        sa.Column("artifact_version_id", UUID, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("capture_operation_id", UUID, nullable=True),
        sa.CheckConstraint("sequence >= 1", name="ck_submission_version_sequence_positive"),
        sa.CheckConstraint(
            "phase IN ('before_deadline', 'revision')", name="ck_submission_version_phase"
        ),
        sa.CheckConstraint(
            "status IN ('validating', 'ready', 'access_error', 'pending_review', 'superseded')",
            name="ck_submission_version_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_submission_version_revision_nonnegative"),
        _org_fk("submission_version"),
        sa.ForeignKeyConstraint(
            ["organization_id", "submission_id", "course_run_id", "homework_id"],
            [
                "submission.organization_id",
                "submission.id",
                "submission.course_run_id",
                "submission.homework_id",
            ],
            name="fk_submission_version_org_submission_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id", "homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_submission_version_org_homework_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_reference_id"],
            ["artifact_reference.organization_id", "artifact_reference.id"],
            name="fk_submission_version_org_artifact_reference",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_reference_id", "artifact_version_id"],
            [
                "artifact_version.organization_id",
                "artifact_version.artifact_reference_id",
                "artifact_version.id",
            ],
            name="fk_submission_version_org_artifact_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "capture_operation_id"],
            ["operation.organization_id", "operation.id"],
            name="fk_submission_version_org_capture_operation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_submission_version"),
        _tenant_uq("submission_version"),
        sa.UniqueConstraint(
            "organization_id", "submission_id", "id", name="uq_submission_version_org_submission_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "course_run_id",
            "homework_id",
            name="uq_submission_version_org_id_scope",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "submission_id",
            "sequence",
            name="uq_submission_version_org_submission_sequence",
        ),
    )
    op.create_foreign_key(
        "fk_submission_current_predeadline_version",
        "submission",
        "submission_version",
        ["organization_id", "id", "current_predeadline_version_id"],
        ["organization_id", "submission_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "artifact_promotion",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("artifact_version_id", UUID, nullable=False),
        sa.Column("operation_id", UUID, nullable=False),
        sa.Column("staged_key", sa.String(1024), nullable=False),
        sa.Column("final_key", sa.String(1024), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_token", UUID, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "state IN ('staged', 'db_committed', 'promoting', 'promoted', 'action_required')",
            name="ck_artifact_promotion_state",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_artifact_promotion_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_artifact_promotion_max_attempts_positive"),
        sa.CheckConstraint("attempts <= max_attempts", name="ck_artifact_promotion_attempt_budget"),
        sa.CheckConstraint("revision >= 0", name="ck_artifact_promotion_revision_nonnegative"),
        _org_fk("artifact_promotion"),
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_version_id"],
            ["artifact_version.organization_id", "artifact_version.id"],
            name="fk_artifact_promotion_org_artifact_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operation_id"],
            ["operation.organization_id", "operation.id"],
            name="fk_artifact_promotion_org_operation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_promotion"),
        _tenant_uq("artifact_promotion"),
        sa.UniqueConstraint(
            "organization_id",
            "artifact_version_id",
            name="uq_artifact_promotion_org_artifact_version",
        ),
        sa.UniqueConstraint(
            "organization_id", "operation_id", name="uq_artifact_promotion_org_operation"
        ),
    )
    op.create_index(
        "ix_artifact_promotion_state_lease",
        "artifact_promotion",
        ["state", "lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "review_case",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("student_id", UUID, nullable=False),
        sa.Column("current_iteration_id", UUID, nullable=True),
        sa.CheckConstraint("revision >= 0", name="ck_review_case_revision_nonnegative"),
        _org_fk("review_case"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_review_case_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id"],
            ["homework.organization_id", "homework.id"],
            name="fk_review_case_org_homework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["user.id"], name="fk_review_case_student_id_user", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_case"),
        _tenant_uq("review_case"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "course_run_id",
            "homework_id",
            "student_id",
            name="uq_review_case_org_id_scope",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "course_run_id",
            "homework_id",
            "student_id",
            name="uq_review_case_org_run_homework_student",
        ),
    )
    op.create_index(
        "ix_review_case_org_student", "review_case", ["organization_id", "student_id"], unique=False
    )

    op.create_table(
        "review_iteration",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("review_case_id", UUID, nullable=False),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("student_id", UUID, nullable=False),
        sa.Column("iteration_number", sa.Integer(), nullable=False),
        sa.Column("submission_version_id", UUID, nullable=False),
        sa.Column(
            "initial_submission_version_id",
            UUID,
            sa.Computed(
                "CASE WHEN origin = 'initial' THEN submission_version_id ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("artifact_version_id", UUID, nullable=False),
        sa.Column("homework_version_id", UUID, nullable=False),
        sa.Column("criterion_set_id", UUID, nullable=False),
        sa.Column("effective_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responsible_reviewer_id", UUID, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_revision_id", UUID, nullable=True),
        sa.Column("predecessor_iteration_id", UUID, nullable=True),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "iteration_number >= 1", name="ck_review_iteration_iteration_number_positive"
        ),
        sa.CheckConstraint(
            "origin IN ('initial', 'resubmission', 'correction', 'requirements_migration')",
            name="ck_review_iteration_origin",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'in_review', 'ready_to_publish', 'published', 'canceled')",
            name="ck_review_iteration_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_review_iteration_revision_nonnegative"),
        _org_fk("review_iteration"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id", "course_run_id", "homework_id", "student_id"],
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_version_id"],
            ["artifact_version.organization_id", "artifact_version.id"],
            name="fk_review_iteration_org_artifact_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id", "homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_review_iteration_org_homework_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "criterion_set_id"],
            ["criterion_set.organization_id", "criterion_set.id"],
            name="fk_review_iteration_org_criterion_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id", "predecessor_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_iteration_org_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_reviewer_id"],
            ["user.id"],
            name="fk_review_iteration_responsible_reviewer_id_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_iteration"),
        _tenant_uq("review_iteration"),
        sa.UniqueConstraint(
            "organization_id", "review_case_id", "id", name="uq_review_iteration_org_case_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "review_case_id",
            "iteration_number",
            name="uq_review_iteration_org_case_number",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "initial_submission_version_id",
            name="uq_review_iteration_org_initial_submission",
        ),
    )
    op.create_index(
        "ix_review_iteration_org_status",
        "review_iteration",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_review_case_current_iteration",
        "review_case",
        "review_iteration",
        ["organization_id", "id", "current_iteration_id"],
        ["organization_id", "review_case_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_review_case_current_iteration", "review_case", type_="foreignkey")
    op.drop_constraint(
        "fk_submission_current_predeadline_version", "submission", type_="foreignkey"
    )
    op.drop_table("review_iteration")
    op.drop_table("review_case")
    op.drop_table("artifact_promotion")
    op.drop_table("submission_version")
    op.drop_table("submission")
    op.drop_table("artifact_version")
    op.drop_table("artifact_reference")
