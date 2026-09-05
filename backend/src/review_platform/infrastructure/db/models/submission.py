"""Submission versions, immutable artifact snapshots, and promotion intents."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
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
    json_dict_default,
    string_enum_check,
    tenant_candidate_key,
    tenant_foreign_key,
)

ARTIFACT_PROVIDERS = ("github", "google_docs", "upload")
READ_CAPABILITIES = ("available", "requires_action", "unavailable")
FEEDBACK_CAPABILITIES = ("available", "not_supported", "requires_action")
SUBMISSION_VERSION_PHASES = ("before_deadline", "revision")
SUBMISSION_VERSION_STATUSES = (
    "validating",
    "ready",
    "access_error",
    "pending_review",
    "superseded",
)
ARTIFACT_PROMOTION_STATES = (
    "staged",
    "db_committed",
    "promoting",
    "promoted",
    "action_required",
)


class Submission(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """One student's version history for one CourseRun and Homework."""

    __tablename__ = "submission"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "course_run_homework_id",
            "course_run_homework",
            name="fk_submission_org_course_run_homework",
        ),
        tenant_foreign_key("course_run_id", "course_run", name="fk_submission_org_course_run"),
        tenant_foreign_key("homework_id", "homework", name="fk_submission_org_homework"),
        UniqueConstraint(
            "organization_id",
            "id",
            "course_run_id",
            "homework_id",
            name="uq_submission_org_id_run_homework",
        ),
        UniqueConstraint(
            "organization_id",
            "course_run_id",
            "homework_id",
            "student_id",
            name="uq_submission_org_run_homework_student",
        ),
        ForeignKeyConstraint(
            ["organization_id", "id", "current_predeadline_version_id"],
            [
                "submission_version.organization_id",
                "submission_version.submission_id",
                "submission_version.id",
            ],
            name="fk_submission_current_predeadline_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_submission_org_student", "organization_id", "student_id"),
    )

    course_run_homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    student_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_predeadline_version_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE,
        nullable=True,
    )


class ArtifactReference(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Provider-neutral mutable locator and capability projection."""

    __tablename__ = "artifact_reference"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_artifact_reference_org_credential_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(string_enum_check("provider", ARTIFACT_PROVIDERS), name="provider"),
        CheckConstraint(
            "(provider = 'upload' AND credential_binding_id IS NULL AND credential_binding_version IS NULL) OR (provider != 'upload' AND credential_binding_id IS NOT NULL AND credential_binding_version IS NOT NULL)",
            name="credential_source",
        ),
        CheckConstraint(
            string_enum_check("read_capability", READ_CAPABILITIES),
            name="read_capability",
        ),
        CheckConstraint(
            string_enum_check("feedback_capability", FEEDBACK_CAPABILITIES),
            name="feedback_capability",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint(
            "credential_binding_version >= 1",
            name="credential_binding_version_positive",
        ),
        Index("ix_artifact_reference_org_provider", "organization_id", "provider"),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_binding_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    credential_binding_version: Mapped[int | None] = mapped_column(nullable=True)
    original_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    locator: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    read_capability: Mapped[str] = mapped_column(String(32), nullable=False)
    feedback_capability: Mapped[str] = mapped_column(String(32), nullable=False)
    last_checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ArtifactVersion(TenantEntityMixin, Base):
    """Immutable captured bytes and object-store provenance."""

    __tablename__ = "artifact_version"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "artifact_reference_id",
            "artifact_reference",
            name="fk_artifact_version_org_reference",
        ),
        UniqueConstraint(
            "organization_id",
            "artifact_reference_id",
            "id",
            name="uq_artifact_version_org_reference_id",
        ),
        UniqueConstraint(
            "organization_id",
            "artifact_reference_id",
            "content_digest",
            name="uq_artifact_version_org_reference_digest",
        ),
        CheckConstraint("byte_size >= 1", name="byte_size_positive"),
    )

    artifact_reference_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    provider_version: Mapped[str] = mapped_column(String(256), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=json_dict_default,
    )


class SubmissionVersion(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Immutable submission inputs plus capture lifecycle state."""

    __tablename__ = "submission_version"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
            ["organization_id", "homework_id", "homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_submission_version_org_homework_version",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "artifact_reference_id",
            "artifact_reference",
            name="fk_submission_version_org_artifact_reference",
        ),
        ForeignKeyConstraint(
            ["organization_id", "artifact_reference_id", "artifact_version_id"],
            [
                "artifact_version.organization_id",
                "artifact_version.artifact_reference_id",
                "artifact_version.id",
            ],
            name="fk_submission_version_org_artifact_version",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "capture_operation_id",
            "operation",
            name="fk_submission_version_org_capture_operation",
        ),
        UniqueConstraint(
            "organization_id",
            "submission_id",
            "id",
            name="uq_submission_version_org_submission_id",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            "course_run_id",
            "homework_id",
            name="uq_submission_version_org_id_scope",
        ),
        UniqueConstraint(
            "organization_id",
            "submission_id",
            "sequence",
            name="uq_submission_version_org_submission_sequence",
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            string_enum_check("phase", SUBMISSION_VERSION_PHASES),
            name="phase",
        ),
        CheckConstraint(
            string_enum_check("status", SUBMISSION_VERSION_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )

    submission_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("('')"))
    sequence: Mapped[int] = mapped_column(nullable=False)
    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    artifact_reference_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    artifact_version_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    effective_deadline: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    capture_operation_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)


class ArtifactPromotion(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Durable staged-to-final object-store promotion intent."""

    __tablename__ = "artifact_promotion"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "artifact_version_id",
            "artifact_version",
            name="fk_artifact_promotion_org_artifact_version",
        ),
        tenant_foreign_key(
            "operation_id",
            "operation",
            name="fk_artifact_promotion_org_operation",
        ),
        UniqueConstraint(
            "organization_id",
            "artifact_version_id",
            name="uq_artifact_promotion_org_artifact_version",
        ),
        UniqueConstraint(
            "organization_id",
            "operation_id",
            name="uq_artifact_promotion_org_operation",
        ),
        CheckConstraint(
            string_enum_check("state", ARTIFACT_PROMOTION_STATES),
            name="state",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempts <= max_attempts", name="attempt_budget"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index(
            "ix_artifact_promotion_state_lease",
            "state",
            "lease_expires_at",
        ),
    )

    artifact_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    staged_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    final_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


__all__ = [
    "ARTIFACT_PROMOTION_STATES",
    "ARTIFACT_PROVIDERS",
    "FEEDBACK_CAPABILITIES",
    "READ_CAPABILITIES",
    "SUBMISSION_VERSION_PHASES",
    "SUBMISSION_VERSION_STATUSES",
    "ArtifactPromotion",
    "ArtifactReference",
    "ArtifactVersion",
    "Submission",
    "SubmissionVersion",
]
