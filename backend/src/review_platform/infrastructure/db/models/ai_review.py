"""AI review runs, attempts, event receipts, suggestions, and signals."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
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

AI_RUN_STATUSES = (
    "pending",
    "running",
    "partial",
    "succeeded",
    "retryable_failed",
    "action_required",
    "stale",
)
AI_ATTEMPT_STATUSES = (
    "pending",
    "running",
    "partial",
    "succeeded",
    "retryable_failed",
    "action_required",
    "stale",
)
AI_EVENT_STATUSES = ("running", "partial", "succeeded", "retryable_failed", "action_required")
AI_SUGGESTION_STATUSES = ("suggested", "needs_human", "not_checked")
AI_CONFIDENCE_LEVELS = ("low", "medium", "high")


class AIReviewRun(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "ai_review_run"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "review_iteration_id", "review_iteration", name="fk_ai_run_org_iteration"
        ),
        tenant_foreign_key("course_run_id", "course_run", name="fk_ai_run_org_course_run"),
        tenant_foreign_key(
            "submission_version_id", "submission_version", name="fk_ai_run_org_submission_version"
        ),
        tenant_foreign_key(
            "artifact_version_id", "artifact_version", name="fk_ai_run_org_artifact_version"
        ),
        tenant_foreign_key(
            "homework_version_id", "homework_version", name="fk_ai_run_org_homework_version"
        ),
        tenant_foreign_key("criterion_set_id", "criterion_set", name="fk_ai_run_org_criterion_set"),
        UniqueConstraint(
            "organization_id", "input_fingerprint", name="uq_ai_review_run_org_fingerprint"
        ),
        CheckConstraint(string_enum_check("status", AI_RUN_STATUSES), name="status"),
        CheckConstraint("current_attempt_no >= 0", name="current_attempt_nonnegative"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )

    review_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    submission_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    artifact_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    criterion_set_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    criteria_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_attempt_no: Mapped[int] = mapped_column(nullable=False, default=0)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AIReviewAttempt(TenantEntityMixin, Base):
    __tablename__ = "ai_review_attempt"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("ai_review_run_id", "ai_review_run", name="fk_ai_attempt_org_run"),
        ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_ai_attempt_org_credential_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "ai_review_run_id",
            "id",
            "attempt_number",
            name="uq_ai_attempt_org_run_id_number",
        ),
        UniqueConstraint(
            "organization_id",
            "ai_review_run_id",
            "attempt_number",
            name="uq_ai_attempt_org_run_number",
        ),
        CheckConstraint(string_enum_check("status", AI_ATTEMPT_STATUSES), name="status"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint("last_sequence >= 0", name="last_sequence_nonnegative"),
        CheckConstraint("credential_binding_version >= 1", name="credential_version_positive"),
    )

    ai_review_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    credential_binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_sequence: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AIReviewEventReceipt(Base):
    __tablename__ = "ai_review_event_receipt"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_ai_event_receipt_org",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        UniqueConstraint("organization_id", "event_id", name="uq_ai_event_receipt_org_event"),
        UniqueConstraint(
            "organization_id",
            "ai_review_run_id",
            "attempt_number",
            "sequence",
            name="uq_ai_event_receipt_org_attempt_sequence",
        ),
        CheckConstraint(string_enum_check("status", AI_EVENT_STATUSES), name="status"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    event_id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    ai_review_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    attempt_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_final: Mapped[bool] = mapped_column(nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AICriterionSuggestion(TenantEntityMixin, Base):
    __tablename__ = "ai_criterion_suggestion"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("ai_review_run_id", "ai_review_run", name="fk_ai_suggestion_org_run"),
        ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["ai_review_event_receipt.organization_id", "ai_review_event_receipt.event_id"],
            name="fk_ai_suggestion_org_event",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key("criterion_id", "criterion", name="fk_ai_suggestion_org_criterion"),
        UniqueConstraint(
            "organization_id",
            "event_id",
            "criterion_id",
            name="uq_ai_suggestion_org_event_criterion",
        ),
        CheckConstraint(string_enum_check("status", AI_SUGGESTION_STATUSES), name="status"),
        CheckConstraint(string_enum_check("confidence", AI_CONFIDENCE_LEVELS), name="confidence"),
        CheckConstraint(
            "proposed_points IS NULL OR proposed_points >= 0", name="points_nonnegative"
        ),
    )

    ai_review_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    event_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    criterion_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    proposed_points: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    student_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class AISignal(TenantEntityMixin, Base):
    __tablename__ = "ai_signal"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("ai_review_run_id", "ai_review_run", name="fk_ai_signal_org_run"),
        ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["ai_review_event_receipt.organization_id", "ai_review_event_receipt.event_id"],
            name="fk_ai_signal_org_event",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("organization_id", "event_id", name="uq_ai_signal_org_event"),
    )

    ai_review_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    event_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    level: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    questions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


__all__ = [
    "AICriterionSuggestion",
    "AIReviewAttempt",
    "AIReviewEventReceipt",
    "AIReviewRun",
    "AISignal",
]
