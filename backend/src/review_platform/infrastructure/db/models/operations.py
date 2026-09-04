"""Foundation persistence: idempotency, operations, audit, and outbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from review_platform.infrastructure.db.base import (
    UUID_TYPE,
    Base,
    RevisionMixin,
    TenantEntityMixin,
    TenantOwnedMixin,
    TimestampMixin,
    UTCDateTime,
    json_dict_default,
    string_enum_check,
    tenant_candidate_key,
    tenant_foreign_key,
)

COMMAND_RECEIPT_STATUSES = (
    "reserved",
    "processing",
    "succeeded",
    "failed",
    "canceled",
    "invalidated",
)
OPERATION_KINDS = (
    "course_import",
    "artifact_capture",
    "ai_review",
    "external_delivery",
    "email_delivery",
)
OPERATION_STATES = (
    "pending",
    "processing",
    "partial",
    "succeeded",
    "retryable_failed",
    "unknown_outcome",
    "reconciling",
    "action_required",
    "stale",
)
OPERATION_ATTEMPT_OUTCOMES = (
    "processing",
    "succeeded",
    "retryable_failed",
    "unknown_outcome",
    "action_required",
)
OUTBOX_STATES = ("pending", "leased", "enqueued", "completed", "action_required")


class CommandReceipt(TenantEntityMixin, TimestampMixin, Base):
    """One durable result reservation per tenant idempotency key."""

    __tablename__ = "command_receipt"
    __table_args__ = (
        tenant_candidate_key(),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_command_receipt_org_idempotency",
        ),
        CheckConstraint(
            string_enum_check("status", COMMAND_RECEIPT_STATUSES),
            name="status",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    command_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    expected_revision: Mapped[int] = mapped_column(nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    actor_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=json_dict_default,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="reserved",
        server_default=text("'reserved'"),
    )
    result_reference: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AuditEvent(TenantEntityMixin, Base):
    """Append-only, sanitized record of a consequential action."""

    __tablename__ = "audit_event"
    __table_args__ = (
        tenant_candidate_key(),
        Index("ix_audit_event_org_entity", "organization_id", "entity_type", "entity_id"),
        Index("ix_audit_event_org_occurred", "organization_id", "occurred_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    installation_operator_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    agent_authorization_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    before_revision: Mapped[int | None] = mapped_column(nullable=True)
    after_revision: Mapped[int | None] = mapped_column(nullable=True)
    request_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    trace_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    sanitized_details: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=json_dict_default,
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class Operation(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Observable asynchronous work and its current state."""

    __tablename__ = "operation"
    __table_args__ = (
        tenant_candidate_key(),
        CheckConstraint(string_enum_check("kind", OPERATION_KINDS), name="kind"),
        CheckConstraint(string_enum_check("state", OPERATION_STATES), name="state"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_operation_org_state", "organization_id", "state"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    input_version: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    attempts: Mapped[list[OperationAttempt]] = relationship(
        back_populates="operation",
        cascade="all, delete-orphan",
        order_by="OperationAttempt.attempt_number",
        lazy="selectin",
    )


class OperationAttempt(TenantEntityMixin, Base):
    """One ordered attempt of an Operation."""

    __tablename__ = "operation_attempt"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "operation_id",
            "operation",
            ondelete="CASCADE",
            name="fk_operation_attempt_org_operation",
        ),
        UniqueConstraint(
            "organization_id",
            "operation_id",
            "attempt_number",
            name="uq_operation_attempt_org_operation_number",
        ),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            string_enum_check("outcome", OPERATION_ATTEMPT_OUTCOMES),
            name="outcome",
        ),
    )

    operation_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    worker_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    operation: Mapped[Operation] = relationship(back_populates="attempts")


class OutboxMessage(TenantOwnedMixin, Base):
    """Transactional outbox row with a recoverable expiring lease."""

    __tablename__ = "outbox_message"
    __table_args__ = (
        tenant_candidate_key("message_id", name="uq_outbox_message_org_message"),
        CheckConstraint(string_enum_check("enqueue_state", OUTBOX_STATES), name="enqueue_state"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempts <= max_attempts", name="attempt_budget"),
        Index(
            "ix_outbox_message_available_lease",
            "enqueue_state",
            "available_at",
            "lease_expires_at",
        ),
    )

    message_id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=json_dict_default,
    )
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    enqueue_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.current_timestamp(),
    )


__all__ = [
    "AuditEvent",
    "CommandReceipt",
    "Operation",
    "OperationAttempt",
    "OutboxMessage",
]
