"""Create identity, access, course, and CourseRun tables.

Revision ID: 0002_identity_and_courses
Revises: 0001_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002_identity_and_courses"
down_revision: str | None = "0001_foundation"
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


def _organization_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organization.id"],
        name=f"fk_{table}_organization_id_organization",
        ondelete="RESTRICT",
    )


def _tenant_candidate(table: str, identity: str = "id") -> sa.UniqueConstraint:
    return sa.UniqueConstraint(
        "organization_id",
        identity,
        name=f"uq_{table}_organization_id",
    )


def _ascii_varchar(length: int) -> mysql.VARCHAR:
    return mysql.VARCHAR(length=length, charset="ascii", collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_user_status"),
        sa.PrimaryKeyConstraint("id", name="pk_user"),
    )

    op.create_table(
        "external_identity",
        sa.Column("id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("provider", _ascii_varchar(64), nullable=False),
        sa.Column("issuer", _ascii_varchar(512), nullable=False),
        sa.Column("subject", _ascii_varchar(512), nullable=False),
        sa.Column("verified_email", sa.String(length=320), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_external_identity_status"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_external_identity_user_id_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_identity"),
        sa.UniqueConstraint(
            "provider",
            "issuer",
            "subject",
            name="uq_external_identity_provider_issuer_subject",
        ),
    )

    op.create_table(
        "organization_membership",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("auth_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", UUID, nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_organization_membership_status",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_organization_membership_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "auth_epoch >= 0",
            name="ck_organization_membership_auth_epoch_nonnegative",
        ),
        _organization_fk("organization_membership"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_organization_membership_user_id_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["user.id"],
            name="fk_organization_membership_revoked_by_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_membership"),
        _tenant_candidate("organization_membership"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_membership_org_user",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "user_id",
            name="uq_organization_membership_org_id_user",
        ),
    )
    op.create_index(
        "ix_organization_membership_org_status",
        "organization_membership",
        ["organization_id", "status"],
        unique=False,
    )

    op.create_table(
        "invitation",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("token_digest", _ascii_varchar(71), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_by", UUID, nullable=False),
        sa.Column("consumed_by", UUID, nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('methodologist', 'reviewer')", name="ck_invitation_role"),
        sa.CheckConstraint(
            "status IN ('active', 'consumed', 'revoked', 'expired')",
            name="ck_invitation_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_invitation_revision_nonnegative"),
        _organization_fk("invitation"),
        sa.ForeignKeyConstraint(
            ["issued_by"],
            ["user.id"],
            name="fk_invitation_issued_by_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by"],
            ["user.id"],
            name="fk_invitation_consumed_by_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitation"),
        _tenant_candidate("invitation"),
        sa.UniqueConstraint("token_digest", name="uq_invitation_token_digest"),
    )
    op.create_index(
        "ix_invitation_org_email_status",
        "invitation",
        ["organization_id", "normalized_email", "status"],
        unique=False,
    )

    op.create_table(
        "external_credential",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_external_credential_status",
        ),
        sa.CheckConstraint(
            "binding_version >= 1",
            name="ck_external_credential_binding_version_positive",
        ),
        _organization_fk("external_credential"),
        sa.PrimaryKeyConstraint("id", "binding_version", name="pk_external_credential"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "binding_version",
            name="uq_external_credential_org_id_version",
        ),
    )
    op.create_index(
        "ix_external_credential_org_provider",
        "external_credential",
        ["organization_id", "provider"],
        unique=False,
    )

    op.create_table(
        "session",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("membership_revision", sa.Integer(), nullable=False),
        sa.Column("auth_epoch", sa.Integer(), nullable=False),
        sa.Column("token_digest", _ascii_varchar(71), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_session_status",
        ),
        sa.CheckConstraint(
            "membership_revision >= 0",
            name="ck_session_membership_revision_nonnegative",
        ),
        sa.CheckConstraint("auth_epoch >= 0", name="ck_session_auth_epoch_nonnegative"),
        _organization_fk("session"),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id", "user_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.id",
                "organization_membership.user_id",
            ],
            name="fk_session_org_membership_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session"),
        _tenant_candidate("session"),
        sa.UniqueConstraint("token_digest", name="uq_session_token_digest"),
    )
    op.create_index(
        "ix_session_org_user_status",
        "session",
        ["organization_id", "user_id", "status"],
        unique=False,
    )

    op.create_table(
        "agent_authorization",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("agent_id", UUID, nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("membership_revision", sa.Integer(), nullable=False),
        sa.Column("auth_epoch", sa.Integer(), nullable=False),
        sa.Column("token_digest", _ascii_varchar(71), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_agent_authorization_status",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_agent_authorization_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "membership_revision >= 0",
            name="ck_agent_authorization_membership_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "auth_epoch >= 0",
            name="ck_agent_authorization_auth_epoch_nonnegative",
        ),
        _organization_fk("agent_authorization"),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_agent_authorization_org_user_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_authorization"),
        _tenant_candidate("agent_authorization"),
        sa.UniqueConstraint(
            "token_digest",
            name="uq_agent_authorization_token_digest",
        ),
    )
    op.create_index(
        "ix_agent_authorization_org_user_status",
        "agent_authorization",
        ["organization_id", "user_id", "status"],
        unique=False,
    )

    op.create_table(
        "oauth_state",
        sa.Column("state_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("state_digest", _ascii_varchar(71), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("credential_binding_id", UUID, nullable=False),
        sa.Column("credential_binding_version", sa.Integer(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column("pkce_verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resulting_session_id", UUID, nullable=True),
        sa.CheckConstraint(
            "credential_binding_version >= 1",
            name="ck_oauth_state_credential_version_positive",
        ),
        _organization_fk("oauth_state"),
        sa.ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_oauth_state_org_credential_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resulting_session_id"],
            ["session.organization_id", "session.id"],
            name="fk_oauth_state_org_resulting_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("state_id", name="pk_oauth_state"),
        sa.UniqueConstraint(
            "organization_id",
            "state_id",
            name="uq_oauth_state_org_state",
        ),
        sa.UniqueConstraint("state_digest", name="uq_oauth_state_state_digest"),
    )
    op.create_index(
        "ix_oauth_state_org_expires",
        "oauth_state",
        ["organization_id", "expires_at"],
        unique=False,
    )

    op.create_table(
        "course",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_kind IN ('external', 'standalone')",
            name="ck_course_source_kind",
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_course_status"),
        sa.CheckConstraint("revision >= 0", name="ck_course_revision_nonnegative"),
        _organization_fk("course"),
        sa.PrimaryKeyConstraint("id", name="pk_course"),
        _tenant_candidate("course"),
    )
    op.create_index(
        "ix_course_org_status",
        "course",
        ["organization_id", "status"],
        unique=False,
    )

    op.create_table(
        "course_run",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("course_id", UUID, nullable=False),
        sa.Column("external_run_id", sa.String(length=256), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_course_run_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_course_run_revision_nonnegative"),
        sa.CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR starts_at <= ends_at",
            name="ck_course_run_date_order",
        ),
        _organization_fk("course_run"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_id"],
            ["course.organization_id", "course.id"],
            name="fk_course_run_org_course",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_run"),
        _tenant_candidate("course_run"),
        sa.UniqueConstraint(
            "organization_id",
            "course_id",
            "external_run_id",
            name="uq_course_run_org_course_external_run",
        ),
    )
    op.create_index(
        "ix_course_run_org_course_status",
        "course_run",
        ["organization_id", "course_id", "status"],
        unique=False,
    )

    op.create_table(
        "external_course_binding",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("course_id", UUID, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_course_id", sa.String(length=512), nullable=False),
        sa.Column("external_url", sa.String(length=2048), nullable=False),
        sa.Column("provider_version", sa.String(length=255), nullable=False),
        sa.Column("credential_id", UUID, nullable=False),
        sa.Column("credential_binding_version", sa.Integer(), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_external_course_binding_status",
        ),
        sa.CheckConstraint(
            "binding_version >= 1",
            name="ck_external_course_binding_binding_version_positive",
        ),
        sa.CheckConstraint(
            "credential_binding_version >= 1",
            name="ck_external_course_binding_credential_binding_version_positive",
        ),
        _organization_fk("external_course_binding"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_id"],
            ["course.organization_id", "course.id"],
            name="fk_external_course_binding_org_course",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "credential_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_external_course_binding_org_credential_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", "binding_version", name="pk_external_course_binding"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "binding_version",
            name="uq_external_course_binding_org_id_version",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "external_course_id",
            "binding_version",
            name="uq_external_course_binding_org_provider_course_version",
        ),
    )

    op.create_table(
        "course_membership",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("external_version", sa.String(length=255), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('student', 'reviewer')", name="ck_course_membership_kind"),
        sa.CheckConstraint(
            "source IN ('imported', 'invitation', 'self_selected')",
            name="ck_course_membership_source",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'removed', 'archived')",
            name="ck_course_membership_status",
        ),
        _organization_fk("course_membership"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_course_membership_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_course_membership_user_id_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_membership"),
        _tenant_candidate("course_membership"),
        sa.UniqueConstraint(
            "organization_id",
            "course_run_id",
            "user_id",
            "kind",
            name="uq_course_membership_org_run_user_kind",
        ),
    )
    op.create_index(
        "ix_course_membership_org_user",
        "course_membership",
        ["organization_id", "user_id"],
        unique=False,
    )

    op.create_table(
        "destination_binding",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("recipient_ref", sa.String(length=512), nullable=False),
        sa.Column("credential_id", UUID, nullable=False),
        sa.Column("credential_binding_version", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('stepik', 'github')", name="ck_destination_binding_kind"),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_destination_binding_status",
        ),
        sa.CheckConstraint(
            "binding_version >= 1",
            name="ck_destination_binding_binding_version_positive",
        ),
        sa.CheckConstraint(
            "credential_binding_version >= 1",
            name="ck_destination_binding_credential_binding_version_positive",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_destination_binding_revision_nonnegative",
        ),
        _organization_fk("destination_binding"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_destination_binding_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "credential_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_destination_binding_org_credential_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", "binding_version", name="pk_destination_binding"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "binding_version",
            name="uq_destination_binding_org_id_version",
        ),
    )
    op.create_index(
        "ix_destination_binding_org_run_status",
        "destination_binding",
        ["organization_id", "course_run_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("destination_binding")
    op.drop_table("course_membership")
    op.drop_table("external_course_binding")
    op.drop_table("course_run")
    op.drop_table("course")
    op.drop_table("oauth_state")
    op.drop_table("agent_authorization")
    op.drop_table("session")
    op.drop_table("external_credential")
    op.drop_table("invitation")
    op.drop_table("organization_membership")
    op.drop_table("external_identity")
    op.drop_table("user")
