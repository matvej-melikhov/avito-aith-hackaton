"""Identity, membership, session, and credential persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.type_api import TypeEngine

from review_platform.infrastructure.db.base import (
    UUID_TYPE,
    Base,
    RevisionMixin,
    TenantEntityMixin,
    TenantOwnedMixin,
    TimestampMixin,
    UTCDateTime,
    UUIDIdentityMixin,
    string_enum_check,
    tenant_candidate_key,
    tenant_foreign_key,
)

USER_STATUSES = ("active", "disabled")
EXTERNAL_IDENTITY_STATUSES = ("active", "revoked")
MEMBERSHIP_STATUSES = ("active", "archived")
INVITATION_ROLES = ("methodologist", "reviewer")
INVITATION_STATUSES = ("active", "consumed", "revoked", "expired")
SESSION_STATUSES = ("active", "revoked", "expired")
AGENT_AUTHORIZATION_STATUSES = ("active", "revoked", "expired")
CREDENTIAL_STATUSES = ("active", "revoked")


def _ascii_string(length: int) -> TypeEngine[str]:
    """Keep long canonical identities indexable under MySQL's byte limit."""

    return String(length).with_variant(
        mysql.VARCHAR(length=length, charset="ascii", collation="ascii_bin"),
        "mysql",
    )


def _json_list_default() -> list[Any]:
    return []


class User(UUIDIdentityMixin, TimestampMixin, Base):
    """Installation-global human identity."""

    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint(string_enum_check("status", USER_STATUSES), name="status"),
    )

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )


class ExternalIdentity(UUIDIdentityMixin, TimestampMixin, Base):
    """Canonical provider identity linked to one local User."""

    __tablename__ = "external_identity"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "issuer",
            "subject",
            name="uq_external_identity_provider_issuer_subject",
        ),
        CheckConstraint(
            string_enum_check("status", EXTERNAL_IDENTITY_STATUSES),
            name="status",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(_ascii_string(64), nullable=False)
    issuer: Mapped[str] = mapped_column(_ascii_string(512), nullable=False)
    subject: Mapped[str] = mapped_column(_ascii_string(512), nullable=False)
    verified_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )


class OrganizationMembership(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """One user's roles and revocation epoch inside an Organization."""

    __tablename__ = "organization_membership"
    __table_args__ = (
        tenant_candidate_key(),
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_membership_org_user",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            "user_id",
            name="uq_organization_membership_org_id_user",
        ),
        CheckConstraint(
            string_enum_check("status", MEMBERSHIP_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint("auth_epoch >= 0", name="auth_epoch_nonnegative"),
        Index("ix_organization_membership_org_status", "organization_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=_json_list_default)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    auth_epoch: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=True,
    )


class Invitation(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """One-time, identity-bound reviewer or methodologist invitation."""

    __tablename__ = "invitation"
    __table_args__ = (
        tenant_candidate_key(),
        UniqueConstraint("token_digest", name="uq_invitation_token_digest"),
        CheckConstraint(string_enum_check("role", INVITATION_ROLES), name="role"),
        CheckConstraint(
            string_enum_check("status", INVITATION_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_invitation_org_email_status", "organization_id", "normalized_email", "status"),
    )

    role: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_digest: Mapped[str] = mapped_column(_ascii_string(71), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    issued_by: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    consumed_by: Mapped[UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )


class Session(TenantEntityMixin, TimestampMixin, Base):
    """Revocable session carrying an exact membership authorization snapshot."""

    __tablename__ = "session"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
            ["organization_id", "membership_id", "user_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.id",
                "organization_membership.user_id",
            ],
            name="fk_session_org_membership_user",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("token_digest", name="uq_session_token_digest"),
        CheckConstraint(string_enum_check("status", SESSION_STATUSES), name="status"),
        CheckConstraint("membership_revision >= 0", name="membership_revision_nonnegative"),
        CheckConstraint("auth_epoch >= 0", name="auth_epoch_nonnegative"),
        Index("ix_session_org_user_status", "organization_id", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    membership_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    membership_revision: Mapped[int] = mapped_column(nullable=False)
    auth_epoch: Mapped[int] = mapped_column(nullable=False)
    token_digest: Mapped[str] = mapped_column(_ascii_string(71), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )


class ExternalCredential(UUIDIdentityMixin, TenantOwnedMixin, TimestampMixin, Base):
    """Encrypted, versioned provider credential; plaintext is never persisted."""

    __tablename__ = "external_credential"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "id",
            "binding_version",
            name="uq_external_credential_org_id_version",
        ),
        CheckConstraint(
            string_enum_check("status", CREDENTIAL_STATUSES),
            name="status",
        ),
        CheckConstraint("binding_version >= 1", name="binding_version_positive"),
        Index("ix_external_credential_org_provider", "organization_id", "provider"),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_version: Mapped[int] = mapped_column(primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class OAuthState(TenantOwnedMixin, TimestampMixin, Base):
    """One-time OAuth protocol identity with encrypted PKCE material."""

    __tablename__ = "oauth_state"
    __table_args__ = (
        tenant_candidate_key("state_id", name="uq_oauth_state_org_state"),
        ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_oauth_state_org_credential_version",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "resulting_session_id",
            "session",
            ondelete="RESTRICT",
            name="fk_oauth_state_org_resulting_session",
        ),
        UniqueConstraint("state_digest", name="uq_oauth_state_state_digest"),
        CheckConstraint("credential_binding_version >= 1", name="credential_version_positive"),
        Index("ix_oauth_state_org_expires", "organization_id", "expires_at"),
    )

    state_id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True)
    state_digest: Mapped[str] = mapped_column(_ascii_string(71), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_version: Mapped[int] = mapped_column(nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    pkce_verifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resulting_session_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)


class AgentAuthorization(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Revocable, scoped authority for an agent acting as one member."""

    __tablename__ = "agent_authorization"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_agent_authorization_org_user_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("token_digest", name="uq_agent_authorization_token_digest"),
        CheckConstraint(
            string_enum_check("status", AGENT_AUTHORIZATION_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint("membership_revision >= 0", name="membership_revision_nonnegative"),
        CheckConstraint("auth_epoch >= 0", name="auth_epoch_nonnegative"),
        Index(
            "ix_agent_authorization_org_user_status",
            "organization_id",
            "user_id",
            "status",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    agent_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=_json_list_default)
    membership_revision: Mapped[int] = mapped_column(nullable=False)
    auth_epoch: Mapped[int] = mapped_column(nullable=False)
    token_digest: Mapped[str] = mapped_column(_ascii_string(71), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


__all__ = [
    "AGENT_AUTHORIZATION_STATUSES",
    "CREDENTIAL_STATUSES",
    "EXTERNAL_IDENTITY_STATUSES",
    "INVITATION_ROLES",
    "INVITATION_STATUSES",
    "MEMBERSHIP_STATUSES",
    "SESSION_STATUSES",
    "USER_STATUSES",
    "AgentAuthorization",
    "ExternalCredential",
    "ExternalIdentity",
    "Invitation",
    "OAuthState",
    "OrganizationMembership",
    "Session",
    "User",
]
