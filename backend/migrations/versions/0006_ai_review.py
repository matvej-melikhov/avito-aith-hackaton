"""Create durable AI review runs, attempts, receipts, and outputs.

Revision ID: 0006_ai_review
Revises: 0005_review_spine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_ai_review"
down_revision: str | None = "0005_review_spine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid(as_uuid=True, native_uuid=False)


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
        "ai_review_run",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.Column("review_iteration_id", UUID, nullable=False),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("submission_version_id", UUID, nullable=False),
        sa.Column("artifact_version_id", UUID, nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("homework_version_id", UUID, nullable=False),
        sa.Column("homework_digest", sa.String(71), nullable=False),
        sa.Column("criterion_set_id", UUID, nullable=False),
        sa.Column("criteria_digest", sa.String(71), nullable=False),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("fingerprint_algorithm", sa.String(32), nullable=False),
        sa.Column("input_fingerprint", sa.String(71), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_attempt_no", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'partial', 'succeeded', "
            "'retryable_failed', 'action_required', 'stale')",
            name="ck_ai_review_run_status",
        ),
        sa.CheckConstraint(
            "current_attempt_no >= 0", name="ck_ai_review_run_current_attempt_nonnegative"
        ),
        sa.CheckConstraint("revision >= 0", name="ck_ai_review_run_revision_nonnegative"),
        _org_fk("ai_review_run"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_iteration_id"],
            ["review_iteration.organization_id", "review_iteration.id"],
            name="fk_ai_run_org_iteration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_ai_run_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "submission_version_id"],
            ["submission_version.organization_id", "submission_version.id"],
            name="fk_ai_run_org_submission_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_version_id"],
            ["artifact_version.organization_id", "artifact_version.id"],
            name="fk_ai_run_org_artifact_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_version_id"],
            ["homework_version.organization_id", "homework_version.id"],
            name="fk_ai_run_org_homework_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "criterion_set_id"],
            ["criterion_set.organization_id", "criterion_set.id"],
            name="fk_ai_run_org_criterion_set",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_review_run"),
        _tenant_uq("ai_review_run"),
        sa.UniqueConstraint(
            "organization_id", "input_fingerprint", name="uq_ai_review_run_org_fingerprint"
        ),
    )

    op.create_table(
        "ai_review_attempt",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("ai_review_run_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("credential_binding_id", UUID, nullable=False),
        sa.Column("credential_binding_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'partial', 'succeeded', "
            "'retryable_failed', 'action_required', 'stale')",
            name="ck_ai_review_attempt_status",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_ai_review_attempt_attempt_number_positive"
        ),
        sa.CheckConstraint(
            "last_sequence >= 0", name="ck_ai_review_attempt_last_sequence_nonnegative"
        ),
        sa.CheckConstraint(
            "credential_binding_version >= 1",
            name="ck_ai_review_attempt_credential_version_positive",
        ),
        _org_fk("ai_review_attempt"),
        sa.ForeignKeyConstraint(
            ["organization_id", "ai_review_run_id"],
            ["ai_review_run.organization_id", "ai_review_run.id"],
            name="fk_ai_attempt_org_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_ai_attempt_org_credential_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_review_attempt"),
        _tenant_uq("ai_review_attempt"),
        sa.UniqueConstraint(
            "organization_id",
            "ai_review_run_id",
            "id",
            "attempt_number",
            name="uq_ai_attempt_org_run_id_number",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "ai_review_run_id",
            "attempt_number",
            name="uq_ai_attempt_org_run_number",
        ),
    )

    op.create_table(
        "ai_review_event_receipt",
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("ai_review_run_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'partial', 'succeeded', 'retryable_failed', 'action_required')",
            name="ck_ai_review_event_receipt_status",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_ai_review_event_receipt_attempt_number_positive"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_ai_review_event_receipt_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_ai_event_receipt_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ai_review_run_id", "attempt_id", "attempt_number"],
            [
                "ai_review_attempt.organization_id",
                "ai_review_attempt.ai_review_run_id",
                "ai_review_attempt.id",
                "ai_review_attempt.attempt_number",
            ],
            name="fk_ai_event_receipt_org_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_ai_review_event_receipt"),
        sa.UniqueConstraint("organization_id", "event_id", name="uq_ai_event_receipt_org_event"),
        sa.UniqueConstraint(
            "organization_id",
            "ai_review_run_id",
            "attempt_number",
            "sequence",
            name="uq_ai_event_receipt_org_attempt_sequence",
        ),
    )

    op.create_table(
        "ai_criterion_suggestion",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("ai_review_run_id", UUID, nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("criterion_id", UUID, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("proposed_points", sa.Numeric(12, 2), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("student_feedback", sa.Text(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('suggested', 'needs_human', 'not_checked')",
            name="ck_ai_criterion_suggestion_status",
        ),
        sa.CheckConstraint(
            "confidence IN ('low', 'medium', 'high')", name="ck_ai_criterion_suggestion_confidence"
        ),
        sa.CheckConstraint(
            "proposed_points IS NULL OR proposed_points >= 0",
            name="ck_ai_criterion_suggestion_points_nonnegative",
        ),
        _org_fk("ai_criterion_suggestion"),
        sa.ForeignKeyConstraint(
            ["organization_id", "ai_review_run_id"],
            ["ai_review_run.organization_id", "ai_review_run.id"],
            name="fk_ai_suggestion_org_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["ai_review_event_receipt.organization_id", "ai_review_event_receipt.event_id"],
            name="fk_ai_suggestion_org_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "criterion_id"],
            ["criterion.organization_id", "criterion.id"],
            name="fk_ai_suggestion_org_criterion",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_criterion_suggestion"),
        _tenant_uq("ai_criterion_suggestion"),
        sa.UniqueConstraint(
            "organization_id",
            "event_id",
            "criterion_id",
            name="uq_ai_suggestion_org_event_criterion",
        ),
    )

    op.create_table(
        "ai_signal",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("ai_review_run_id", UUID, nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("level", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        _org_fk("ai_signal"),
        sa.ForeignKeyConstraint(
            ["organization_id", "ai_review_run_id"],
            ["ai_review_run.organization_id", "ai_review_run.id"],
            name="fk_ai_signal_org_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["ai_review_event_receipt.organization_id", "ai_review_event_receipt.event_id"],
            name="fk_ai_signal_org_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_signal"),
        _tenant_uq("ai_signal"),
        sa.UniqueConstraint("organization_id", "event_id", name="uq_ai_signal_org_event"),
    )
    op.create_foreign_key(
        "fk_review_decision_org_ai_suggestion",
        "review_criterion_decision",
        "ai_criterion_suggestion",
        ["organization_id", "ai_suggestion_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_review_decision_org_ai_suggestion", "review_criterion_decision", type_="foreignkey"
    )
    op.drop_table("ai_signal")
    op.drop_table("ai_criterion_suggestion")
    op.drop_table("ai_review_event_receipt")
    op.drop_table("ai_review_attempt")
    op.drop_table("ai_review_run")
