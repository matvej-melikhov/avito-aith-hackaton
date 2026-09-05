"""Create delivery attempts and reconciliation observations.

Revision ID: 0008_delivery_recovery
Revises: 0007_human_review
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_delivery_recovery"
down_revision: str | None = "0007_human_review"
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
        "delivery_attempt",
        sa.Column("delivery_id", UUID, nullable=False),
        sa.Column("external_delivery_id", UUID, nullable=False),
        sa.Column("operation_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("credential_binding_id", UUID, nullable=False),
        sa.Column("credential_binding_version", sa.Integer(), nullable=False),
        sa.Column("worker_identity", sa.String(255), nullable=False),
        sa.Column("claim_token", UUID, nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.CheckConstraint(
            "delivery_id = external_delivery_id",
            name="ck_delivery_attempt_delivery_identity_coherent",
        ),
        sa.CheckConstraint(
            "state = outcome",
            name="ck_delivery_attempt_state_outcome_coherent",
        ),
        sa.CheckConstraint(
            "state IN ('processing', 'succeeded', 'retryable_failed', "
            "'unknown_outcome', 'action_required')",
            name="ck_delivery_attempt_state",
        ),
        sa.CheckConstraint(
            "outcome IN ('processing', 'succeeded', 'retryable_failed', "
            "'unknown_outcome', 'action_required')",
            name="ck_delivery_attempt_outcome",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_delivery_attempt_attempt_number_positive",
        ),
        sa.CheckConstraint(
            "credential_binding_version >= 1",
            name="ck_delivery_attempt_credential_binding_version_positive",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_delivery_attempt_finished_after_started",
        ),
        sa.CheckConstraint(
            "lease_expires_at >= started_at",
            name="ck_delivery_attempt_lease_after_started",
        ),
        sa.CheckConstraint(
            "sanitized_error IS NULL OR CHAR_LENGTH(CAST(sanitized_error AS CHAR)) <= 2048",
            name="ck_delivery_attempt_sanitized_error_bounded",
        ),
        _org_fk("delivery_attempt"),
        sa.ForeignKeyConstraint(
            ["organization_id", "delivery_id"],
            ["external_delivery.organization_id", "external_delivery.id"],
            name="fk_delivery_attempt_org_delivery",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "external_delivery_id"],
            ["external_delivery.organization_id", "external_delivery.id"],
            name="fk_delivery_attempt_org_external_delivery",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operation_id"],
            ["operation.organization_id", "operation.id"],
            name="fk_delivery_attempt_org_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_delivery_attempt_org_credential_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_delivery_attempt"),
        _tenant_uq("delivery_attempt"),
        sa.UniqueConstraint(
            "organization_id",
            "delivery_id",
            "id",
            name="uq_delivery_attempt_org_delivery_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "delivery_id",
            "id",
            "attempt_number",
            "operation_id",
            "credential_binding_id",
            "credential_binding_version",
            name="uq_delivery_attempt_org_exact_snapshot",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "delivery_id",
            "attempt_number",
            name="uq_delivery_attempt_org_delivery_number",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "external_delivery_id",
            "attempt_number",
            name="uq_delivery_attempt_org_external_delivery_number",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "claim_token",
            name="uq_delivery_attempt_org_claim_token",
        ),
    )
    op.create_index(
        "ix_delivery_attempt_org_delivery_started",
        "delivery_attempt",
        ["organization_id", "delivery_id", "started_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_delivery_attempt_org_lease",
        "delivery_attempt",
        ["organization_id", "state", "lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "delivery_reconciliation_observation",
        sa.Column("delivery_id", UUID, nullable=False),
        sa.Column("external_delivery_id", UUID, nullable=False),
        sa.Column("delivery_attempt_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("operation_id", UUID, nullable=False),
        sa.Column("credential_binding_id", UUID, nullable=False),
        sa.Column("credential_binding_version", sa.Integer(), nullable=False),
        sa.Column("request_payload_version", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("result_payload_version", sa.String(64), nullable=False),
        sa.Column("result_digest", sa.String(71), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("external_url", sa.String(2048), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.CheckConstraint(
            "delivery_id = external_delivery_id",
            name="ck_delivery_reconciliation_observation_delivery_identity_coherent",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'retryable_failed', 'unknown_outcome', "
            "'action_required', 'not_found')",
            name="ck_delivery_reconciliation_observation_outcome",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_delivery_reconciliation_observation_attempt_number_positive",
        ),
        sa.CheckConstraint(
            "credential_binding_version >= 1",
            name=("ck_delivery_reconciliation_observation_credential_binding_version_positive"),
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(request_digest) = 71 AND request_digest LIKE 'sha256:%'",
            name="ck_delivery_reconciliation_observation_request_digest_shape",
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(result_digest) = 71 AND result_digest LIKE 'sha256:%'",
            name="ck_delivery_reconciliation_observation_result_digest_shape",
        ),
        sa.CheckConstraint(
            "sanitized_error IS NULL OR CHAR_LENGTH(CAST(sanitized_error AS CHAR)) <= 2048",
            name="ck_delivery_reconciliation_observation_sanitized_error_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_delivery_observation_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "delivery_id"],
            ["external_delivery.organization_id", "external_delivery.id"],
            name="fk_delivery_observation_org_delivery",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "external_delivery_id"],
            ["external_delivery.organization_id", "external_delivery.id"],
            name="fk_delivery_observation_org_external_delivery",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "delivery_id", "delivery_attempt_id"],
            [
                "delivery_attempt.organization_id",
                "delivery_attempt.delivery_id",
                "delivery_attempt.id",
            ],
            name="fk_delivery_observation_org_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "delivery_id",
                "delivery_attempt_id",
                "attempt_number",
                "operation_id",
                "credential_binding_id",
                "credential_binding_version",
            ],
            [
                "delivery_attempt.organization_id",
                "delivery_attempt.delivery_id",
                "delivery_attempt.id",
                "delivery_attempt.attempt_number",
                "delivery_attempt.operation_id",
                "delivery_attempt.credential_binding_id",
                "delivery_attempt.credential_binding_version",
            ],
            name="fk_delivery_observation_org_exact_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operation_id"],
            ["operation.organization_id", "operation.id"],
            name="fk_delivery_observation_org_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_delivery_observation_org_credential_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_delivery_reconciliation_observation",
        ),
        _tenant_uq("delivery_reconciliation_observation"),
        sa.UniqueConstraint(
            "organization_id",
            "delivery_id",
            "id",
            name="uq_delivery_observation_org_delivery_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "delivery_id",
            "delivery_attempt_id",
            "result_digest",
            name="uq_delivery_observation_org_attempt_result",
        ),
    )
    op.create_index(
        "ix_delivery_observation_org_delivery_observed",
        "delivery_reconciliation_observation",
        ["organization_id", "delivery_id", "observed_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("delivery_reconciliation_observation")
    op.drop_table("delivery_attempt")
