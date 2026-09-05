"""Create human review preferences, successors, publication, and delivery intents.

Revision ID: 0007_human_review
Revises: 0006_ai_review
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_human_review"
down_revision: str | None = "0006_ai_review"
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
        "availability_plan",
        sa.Column("reviewer_id", UUID, nullable=False),
        sa.Column("planned_minutes", sa.Integer(), nullable=False),
        sa.Column("until_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint(
            "planned_minutes >= 0",
            name="ck_availability_plan_planned_minutes_nonnegative",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_availability_plan_revision_nonnegative",
        ),
        _org_fk("availability_plan"),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewer_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_availability_plan_org_reviewer_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_availability_plan"),
        _tenant_uq("availability_plan"),
        sa.UniqueConstraint(
            "organization_id",
            "reviewer_id",
            name="uq_availability_plan_org_reviewer",
        ),
    )
    op.create_index(
        "ix_availability_plan_org_until",
        "availability_plan",
        ["organization_id", "until_at"],
        unique=False,
    )

    op.create_table(
        "reviewer_course_selection",
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("reviewer_id", UUID, nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
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
        _org_fk("reviewer_course_selection"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_reviewer_course_selection_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewer_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_reviewer_course_selection_org_reviewer_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reviewer_course_selection"),
        _tenant_uq("reviewer_course_selection"),
        sa.UniqueConstraint(
            "organization_id",
            "course_run_id",
            "reviewer_id",
            name="uq_reviewer_course_selection_org_run_reviewer",
        ),
    )
    op.create_index(
        "ix_reviewer_course_selection_org_reviewer_active",
        "reviewer_course_selection",
        ["organization_id", "reviewer_id", "active"],
        unique=False,
    )
    op.create_index(
        "ix_reviewer_course_selection_org_run_active",
        "reviewer_course_selection",
        ["organization_id", "course_run_id", "active"],
        unique=False,
    )

    op.create_table(
        "review_responsibility",
        sa.Column("review_case_id", UUID, nullable=False),
        sa.Column("review_iteration_id", UUID, nullable=True),
        sa.Column("reviewer_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.CheckConstraint(
            "action IN ('started', 'joined', 'released', 'completed')",
            name="ck_review_responsibility_action",
        ),
        _org_fk("review_responsibility"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id"],
            ["review_case.organization_id", "review_case.id"],
            name="fk_review_responsibility_org_review_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id", "review_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_responsibility_org_case_iteration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewer_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_review_responsibility_org_reviewer_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "actor_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_review_responsibility_org_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_responsibility"),
        _tenant_uq("review_responsibility"),
    )
    op.create_index(
        "ix_review_responsibility_org_case_occurred",
        "review_responsibility",
        ["organization_id", "review_case_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_review_responsibility_org_iteration_occurred",
        "review_responsibility",
        ["organization_id", "review_iteration_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_review_responsibility_org_reviewer_occurred",
        "review_responsibility",
        ["organization_id", "reviewer_id", "occurred_at", "id"],
        unique=False,
    )

    op.create_table(
        "review_iteration_relation",
        sa.Column("review_case_id", UUID, nullable=False),
        sa.Column("predecessor_iteration_id", UUID, nullable=False),
        sa.Column("successor_iteration_id", UUID, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.CheckConstraint(
            "predecessor_iteration_id <> successor_iteration_id",
            name="ck_review_iteration_relation_different_iterations",
        ),
        sa.CheckConstraint(
            "kind IN ('correction', 'requirements_migration')",
            name="ck_review_iteration_relation_kind",
        ),
        _org_fk("review_iteration_relation"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id", "predecessor_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_relation_org_case_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id", "successor_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_relation_org_case_successor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_iteration_relation"),
        _tenant_uq("review_iteration_relation"),
        sa.UniqueConstraint(
            "organization_id",
            "predecessor_iteration_id",
            "successor_iteration_id",
            name="uq_review_relation_org_predecessor_successor",
        ),
    )
    op.create_index(
        "ix_review_relation_org_successor",
        "review_iteration_relation",
        ["organization_id", "successor_iteration_id"],
        unique=False,
    )

    op.create_table(
        "review_impact_event",
        sa.Column("source_event_id", UUID, nullable=False),
        sa.Column("review_case_id", UUID, nullable=False),
        sa.Column("review_iteration_id", UUID, nullable=False),
        sa.Column("course_run_homework_id", UUID, nullable=False),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("previous_homework_version_id", UUID, nullable=False),
        sa.Column("current_homework_version_id", UUID, nullable=False),
        sa.Column("previous_publication_id", UUID, nullable=False),
        sa.Column("current_publication_id", UUID, nullable=False),
        sa.Column("publication_sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by_iteration_id", UUID, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.CheckConstraint(
            "previous_homework_version_id <> current_homework_version_id",
            name="ck_review_impact_event_different_homework_versions",
        ),
        sa.CheckConstraint(
            "publication_sequence >= 1",
            name="ck_review_impact_event_publication_sequence_positive",
        ),
        _org_fk("review_impact_event"),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_event_id"],
            ["outbox_message.organization_id", "outbox_message.message_id"],
            name="fk_review_impact_org_source_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id", "review_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_impact_org_case_iteration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_homework_id"],
            ["course_run_homework.organization_id", "course_run_homework.id"],
            name="fk_review_impact_org_run_homework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_review_impact_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id"],
            ["homework.organization_id", "homework.id"],
            name="fk_review_impact_org_homework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id", "previous_homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_review_impact_org_previous_homework_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id", "current_homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_review_impact_org_current_homework_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_homework_id", "previous_publication_id"],
            [
                "course_run_homework_publication.organization_id",
                "course_run_homework_publication.course_run_homework_id",
                "course_run_homework_publication.id",
            ],
            name="fk_review_impact_org_previous_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_homework_id", "current_publication_id"],
            [
                "course_run_homework_publication.organization_id",
                "course_run_homework_publication.course_run_homework_id",
                "course_run_homework_publication.id",
            ],
            name="fk_review_impact_org_current_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_case_id", "resolved_by_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_impact_org_resolving_iteration",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_impact_event"),
        _tenant_uq("review_impact_event"),
        sa.UniqueConstraint(
            "organization_id",
            "source_event_id",
            "review_iteration_id",
            name="uq_review_impact_org_event_iteration",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "current_homework_version_id",
            name="uq_review_impact_org_iteration_version",
        ),
    )
    op.create_index(
        "ix_review_impact_org_iteration_occurred",
        "review_impact_event",
        ["organization_id", "review_iteration_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_review_impact_org_unresolved",
        "review_impact_event",
        ["organization_id", "resolved_at", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "publication_request",
        sa.Column("review_iteration_id", UUID, nullable=False),
        sa.Column("review_revision_id", UUID, nullable=False),
        sa.Column("requested_by_user_id", UUID, nullable=False),
        sa.Column("agent_id", UUID, nullable=False),
        sa.Column("agent_authorization_id", UUID, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", UUID, nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected', 'expired', 'superseded')",
            name="ck_publication_request_status",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_publication_request_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'confirmed' AND confirmed_by IS NOT NULL "
            "AND confirmed_at IS NOT NULL) OR "
            "(status <> 'confirmed' AND confirmed_by IS NULL "
            "AND confirmed_at IS NULL)",
            name="ck_publication_request_confirmation_coherent",
        ),
        _org_fk("publication_request"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "review_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_publication_request_org_iteration_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "requested_by_user_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_publication_request_org_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_authorization_id"],
            ["agent_authorization.organization_id", "agent_authorization.id"],
            name="fk_publication_request_org_agent_authorization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "confirmed_by"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_publication_request_org_confirmer_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_publication_request"),
        _tenant_uq("publication_request"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "review_iteration_id",
            "review_revision_id",
            name="uq_publication_request_org_id_revision",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_publication_request_org_idempotency",
        ),
    )
    op.create_index(
        "ix_publication_request_org_iteration_status",
        "publication_request",
        ["organization_id", "review_iteration_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_publication_request_org_status_expires",
        "publication_request",
        ["organization_id", "status", "expires_at"],
        unique=False,
    )

    op.create_table(
        "review_publication",
        sa.Column("review_iteration_id", UUID, nullable=False),
        sa.Column("review_revision_id", UUID, nullable=False),
        sa.Column("publication_request_id", UUID, nullable=True),
        sa.Column("publication_version", sa.Integer(), nullable=False),
        sa.Column("published_by", UUID, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            server_default=sa.text("'published'"),
            nullable=False,
        ),
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "status IN ('published')",
            name="ck_review_publication_status",
        ),
        sa.CheckConstraint(
            "publication_version >= 1",
            name="ck_review_publication_publication_version_positive",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_review_publication_revision_nonnegative",
        ),
        _org_fk("review_publication"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "review_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_review_publication_org_iteration_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "publication_request_id",
                "review_iteration_id",
                "review_revision_id",
            ],
            [
                "publication_request.organization_id",
                "publication_request.id",
                "publication_request.review_iteration_id",
                "publication_request.review_revision_id",
            ],
            name="fk_review_publication_org_request_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "published_by"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_review_publication_org_publisher_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_publication"),
        _tenant_uq("review_publication"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "review_iteration_id",
            "review_revision_id",
            name="uq_review_publication_org_id_revision",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "review_revision_id",
            name="uq_review_publication_org_iteration_revision",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "publication_version",
            name="uq_review_publication_org_iteration_version",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "publication_request_id",
            name="uq_review_publication_org_request",
        ),
    )
    op.create_index(
        "ix_review_publication_org_iteration_published",
        "review_publication",
        ["organization_id", "review_iteration_id", "published_at"],
        unique=False,
    )

    op.create_table(
        "external_delivery",
        sa.Column("publication_id", UUID, nullable=False),
        sa.Column("operation_id", UUID, nullable=False),
        sa.Column("delivery_key", sa.String(512), nullable=False),
        sa.Column("destination_binding_id", UUID, nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("credential_binding_id", UUID, nullable=False),
        sa.Column("credential_binding_version", sa.Integer(), nullable=False),
        sa.Column("destination_kind", sa.String(32), nullable=False),
        sa.Column("recipient_ref", sa.String(512), nullable=False),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("homework_version_id", UUID, nullable=False),
        sa.Column("criterion_set_id", UUID, nullable=False),
        sa.Column("submission_version_id", UUID, nullable=False),
        sa.Column("artifact_version_id", UUID, nullable=False),
        sa.Column("artifact_content_digest", sa.String(71), nullable=False),
        sa.Column("review_iteration_id", UUID, nullable=False),
        sa.Column("review_revision_id", UUID, nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("publication_fingerprint", sa.String(71), nullable=False),
        sa.Column("payload_version", sa.String(64), nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "state",
            sa.String(32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("external_url", sa.String(2048), nullable=True),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("sanitized_error", sa.JSON(), nullable=True),
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
        sa.CheckConstraint(
            "destination_kind IN ('stepik', 'github')",
            name="ck_external_delivery_destination_kind",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'retryable_failed', 'unknown_outcome', "
            "'reconciling', 'succeeded', 'action_required', 'superseded')",
            name="ck_external_delivery_state",
        ),
        sa.CheckConstraint(
            "binding_version >= 1",
            name="ck_external_delivery_binding_version_positive",
        ),
        sa.CheckConstraint(
            "credential_binding_version >= 1",
            name="ck_external_delivery_credential_binding_version_positive",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_external_delivery_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_external_delivery_revision_nonnegative",
        ),
        _org_fk("external_delivery"),
        sa.ForeignKeyConstraint(
            [
                "organization_id",
                "publication_id",
                "review_iteration_id",
                "review_revision_id",
            ],
            [
                "review_publication.organization_id",
                "review_publication.id",
                "review_publication.review_iteration_id",
                "review_publication.review_revision_id",
            ],
            name="fk_external_delivery_org_publication_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operation_id"],
            ["operation.organization_id", "operation.id"],
            name="fk_external_delivery_org_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "destination_binding_id", "binding_version"],
            [
                "destination_binding.organization_id",
                "destination_binding.id",
                "destination_binding.binding_version",
            ],
            name="fk_external_delivery_org_destination_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_external_delivery_org_credential_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_external_delivery_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_version_id"],
            ["homework_version.organization_id", "homework_version.id"],
            name="fk_external_delivery_org_homework_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "criterion_set_id"],
            ["criterion_set.organization_id", "criterion_set.id"],
            name="fk_external_delivery_org_criterion_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "submission_version_id"],
            ["submission_version.organization_id", "submission_version.id"],
            name="fk_external_delivery_org_submission_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_version_id"],
            ["artifact_version.organization_id", "artifact_version.id"],
            name="fk_external_delivery_org_artifact_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "review_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_external_delivery_org_iteration_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_delivery"),
        _tenant_uq("external_delivery"),
        sa.UniqueConstraint(
            "organization_id",
            "publication_id",
            "destination_binding_id",
            "binding_version",
            "payload_version",
            name="uq_external_delivery_org_publication_destination_payload",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "operation_id",
            name="uq_external_delivery_org_operation",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "delivery_key",
            name="uq_external_delivery_org_delivery_key",
        ),
    )
    op.create_index(
        "ix_external_delivery_org_publication",
        "external_delivery",
        ["organization_id", "publication_id"],
        unique=False,
    )
    op.create_index(
        "ix_external_delivery_org_state_next_attempt",
        "external_delivery",
        ["organization_id", "state", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("external_delivery")
    op.drop_table("review_publication")
    op.drop_table("publication_request")
    op.drop_table("review_impact_event")
    op.drop_table("review_iteration_relation")
    op.drop_table("review_responsibility")
    op.drop_table("reviewer_course_selection")
    op.drop_table("availability_plan")
