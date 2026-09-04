"""Create the organization and foundational operation tables.

Revision ID: 0001_foundation
Revises: none
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.Uuid(as_uuid=True, native_uuid=False)


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "organization",
        sa.Column("id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name=op.f("ck_organization_status"),
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_organization_revision_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization")),
        sa.UniqueConstraint("slug", name="uq_organization_slug"),
    )

    op.create_table(
        "command_receipt",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("command_name", sa.String(length=100), nullable=False),
        sa.Column("target_id", UUID, nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(length=71), nullable=False),
        sa.Column("actor_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'reserved'"),
            nullable=False,
        ),
        sa.Column("result_reference", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status IN ('reserved', 'processing', 'succeeded', 'failed', 'canceled', "
            "'invalidated')",
            name=op.f("ck_command_receipt_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_command_receipt_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_command_receipt")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_command_receipt_organization_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_command_receipt_org_idempotency",
        ),
    )

    op.create_table(
        "audit_event",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", UUID, nullable=True),
        sa.Column("installation_operator_id", sa.String(length=255), nullable=True),
        sa.Column("agent_id", UUID, nullable=True),
        sa.Column("agent_authorization_id", UUID, nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("before_revision", sa.Integer(), nullable=True),
        sa.Column("after_revision", sa.Integer(), nullable=True),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("trace_id", UUID, nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("sanitized_details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_audit_event_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_audit_event_organization_id"),
        ),
    )
    op.create_index(
        "ix_audit_event_org_entity",
        "audit_event",
        ["organization_id", "entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_event_org_occurred",
        "audit_event",
        ["organization_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "operation",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("input_version", sa.String(length=255), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('course_import', 'artifact_capture', 'ai_review', 'external_delivery', "
            "'email_delivery')",
            name=op.f("ck_operation_kind"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'partial', 'succeeded', 'retryable_failed', "
            "'unknown_outcome', 'reconciling', 'action_required', 'stale')",
            name=op.f("ck_operation_state"),
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_operation_revision_nonnegative")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_operation_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operation")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_operation_organization_id"),
        ),
    )
    op.create_index(
        "ix_operation_org_state",
        "operation",
        ["organization_id", "state"],
        unique=False,
    )

    op.create_table(
        "operation_attempt",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("operation_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_identity", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name=op.f("ck_operation_attempt_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "outcome IN ('processing', 'succeeded', 'retryable_failed', 'unknown_outcome', "
            "'action_required')",
            name=op.f("ck_operation_attempt_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_operation_attempt_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operation_id"],
            ["operation.organization_id", "operation.id"],
            name="fk_operation_attempt_org_operation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operation_attempt")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_operation_attempt_organization_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "operation_id",
            "attempt_number",
            name="uq_operation_attempt_org_operation_number",
        ),
    )

    op.create_table(
        "outbox_message",
        sa.Column("message_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("aggregate_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_version", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_token", UUID, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enqueue_state",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "enqueue_state IN ('pending', 'leased', 'enqueued', 'completed', 'action_required')",
            name=op.f("ck_outbox_message_enqueue_state"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_message_attempts_nonnegative")),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name=op.f("ck_outbox_message_max_attempts_positive"),
        ),
        sa.CheckConstraint(
            "attempts <= max_attempts",
            name=op.f("ck_outbox_message_attempt_budget"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_outbox_message_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_outbox_message")),
        sa.UniqueConstraint(
            "organization_id",
            "message_id",
            name="uq_outbox_message_org_message",
        ),
    )
    op.create_index(
        "ix_outbox_message_available_lease",
        "outbox_message",
        ["enqueue_state", "available_at", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_message_available_lease", table_name="outbox_message")
    op.drop_table("outbox_message")
    op.drop_table("operation_attempt")
    op.drop_index("ix_operation_org_state", table_name="operation")
    op.drop_table("operation")
    op.drop_index("ix_audit_event_org_occurred", table_name="audit_event")
    op.drop_index("ix_audit_event_org_entity", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_table("command_receipt")
    op.drop_table("organization")
