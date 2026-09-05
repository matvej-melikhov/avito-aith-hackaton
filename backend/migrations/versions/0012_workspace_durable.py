"""Durable preparation and reviewer assist, authorization snapshots and provenance."""

from alembic import op
import sqlalchemy as sa

revision = "0012_workspace_durable"
down_revision = "0011_workspace_version_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_course_details", sa.Column("stepik_url", sa.String(2048), nullable=True)
    )
    op.add_column(
        "homework_private_details", sa.Column("material_upload_ids", sa.JSON(), nullable=True)
    )
    op.add_column("workspace_artifact", sa.Column("provenance", sa.JSON(), nullable=True))
    op.add_column(
        "self_review_run",
        sa.Column("membership_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "self_review_run", sa.Column("auth_epoch", sa.Integer(), nullable=False, server_default="0")
    )
    op.drop_constraint(
        "ck_artifact_reference_credential_source", "artifact_reference", type_="check"
    )
    op.drop_constraint("ck_artifact_reference_provider", "artifact_reference", type_="check")
    op.create_check_constraint(
        "ck_artifact_reference_provider",
        "artifact_reference",
        "provider IN ('github','google_docs','upload','workspace')",
    )
    op.create_check_constraint(
        "ck_artifact_reference_credential_source",
        "artifact_reference",
        "(provider IN ('upload','workspace') AND credential_binding_id IS NULL AND credential_binding_version IS NULL) OR (provider NOT IN ('upload','workspace') AND credential_binding_id IS NOT NULL AND credential_binding_version IS NOT NULL)",
    )
    # Freeze the generated table definitions in this migration; no runtime metadata imports.
    for name, columns, foreigns, unique in (
        (
            "workspace_artifact_preparation",
            [
                sa.Column("draft_id", sa.Uuid(native_uuid=False), nullable=False),
                sa.Column("draft_revision", sa.Integer(), nullable=False),
                sa.Column("source_url", sa.String(2048), nullable=False),
                sa.Column("artifact_id", sa.Uuid(native_uuid=False)),
                sa.Column("status", sa.String(16), nullable=False),
                sa.Column("error_code", sa.String(64)),
                sa.Column("lease_token", sa.Uuid(native_uuid=False)),
                sa.Column("lease_until", sa.DateTime()),
                sa.Column("attempts", sa.Integer(), nullable=False),
            ],
            [
                ("draft_id", "work_draft", "fk_ws_preparation_draft"),
                ("artifact_id", "workspace_artifact", "fk_ws_preparation_artifact"),
            ],
            [
                sa.UniqueConstraint(
                    "organization_id",
                    "draft_id",
                    "draft_revision",
                    name="uq_ws_preparation_revision",
                )
            ],
        ),
        (
            "workspace_review_assist_run",
            [
                sa.Column("iteration_id", sa.Uuid(native_uuid=False), nullable=False),
                sa.Column("owner_id", sa.Uuid(native_uuid=False), nullable=False),
                sa.Column("membership_revision", sa.Integer(), nullable=False),
                sa.Column("auth_epoch", sa.Integer(), nullable=False),
                sa.Column("artifact_id", sa.Uuid(native_uuid=False), nullable=False),
                sa.Column("homework_version_id", sa.Uuid(native_uuid=False), nullable=False),
                sa.Column("input_fingerprint", sa.String(71), nullable=False),
                sa.Column("inputs", sa.JSON(), nullable=False),
                sa.Column("status", sa.String(32), nullable=False),
                sa.Column("result", sa.JSON()),
                sa.Column("error_code", sa.String(64)),
                sa.Column("sequence", sa.Integer(), nullable=False),
                sa.Column("attempt", sa.Integer(), nullable=False),
                sa.Column("lease_token", sa.Uuid(native_uuid=False)),
                sa.Column("lease_until", sa.DateTime()),
                sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            ],
            [
                ("iteration_id", "review_iteration", "fk_ws_assist_iteration"),
                ("artifact_id", "artifact_version", "fk_ws_assist_artifact"),
                ("homework_version_id", "homework_version", "fk_ws_assist_homework"),
            ],
            [
                sa.ForeignKeyConstraint(
                    ["organization_id", "owner_id"],
                    ["organization_membership.organization_id", "organization_membership.user_id"],
                    name="fk_ws_assist_owner",
                )
            ],
        ),
        (
            "workspace_review_assist_event",
            [
                sa.Column("run_id", sa.Uuid(native_uuid=False), nullable=False),
                sa.Column("digest", sa.String(71), nullable=False),
            ],
            [("run_id", "workspace_review_assist_run", "fk_ws_assist_event_run")],
            [],
        ),
        (
            "workspace_review_ai_choice",
            [sa.Column("run_id", sa.Uuid(native_uuid=False), nullable=False)],
            [
                ("id", "review_revision", "fk_ws_ai_choice_revision"),
                ("run_id", "workspace_review_assist_run", "fk_ws_ai_choice_run"),
            ],
            [],
        ),
    ):
        timestamps = (
            []
            if name in {"workspace_review_assist_event", "workspace_review_ai_choice"}
            else [
                sa.Column(
                    "created_at",
                    sa.DateTime(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
                sa.Column(
                    "updated_at",
                    sa.DateTime(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
            ]
        )
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
            sa.Column("organization_id", sa.Uuid(native_uuid=False), nullable=False),
            *columns,
            *timestamps,
            sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("organization_id", "id", name=f"uq_{name}_organization_id"),
            *[
                sa.ForeignKeyConstraint(
                    ["organization_id", col],
                    [f"{table}.organization_id", f"{table}.id"],
                    name=fk,
                    ondelete="RESTRICT",
                )
                for col, table, fk in foreigns
            ],
            *unique,
        )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM artifact_reference WHERE provider='workspace'")
    ):
        raise RuntimeError("Workspace snapshots must be migrated before downgrading.")
    for name in (
        "workspace_review_ai_choice",
        "workspace_review_assist_event",
        "workspace_review_assist_run",
        "workspace_artifact_preparation",
    ):
        op.drop_table(name)
    op.drop_constraint(
        "ck_artifact_reference_credential_source", "artifact_reference", type_="check"
    )
    op.drop_constraint("ck_artifact_reference_provider", "artifact_reference", type_="check")
    op.create_check_constraint(
        "ck_artifact_reference_provider",
        "artifact_reference",
        "provider IN ('github','google_docs','upload')",
    )
    op.create_check_constraint(
        "ck_artifact_reference_credential_source",
        "artifact_reference",
        "(provider = 'upload' AND credential_binding_id IS NULL AND credential_binding_version IS NULL) OR (provider != 'upload' AND credential_binding_id IS NOT NULL AND credential_binding_version IS NOT NULL)",
    )
    op.drop_column("self_review_run", "auth_epoch")
    op.drop_column("self_review_run", "membership_revision")
    op.drop_column("workspace_artifact", "provenance")
    op.drop_column("homework_private_details", "material_upload_ids")
    op.drop_column("workspace_course_details", "stepik_url")
