"""External delivery attempts and append-only reconciliation observations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from review_platform.infrastructure.db.base import (
    UUID_TYPE,
    Base,
    TenantEntityMixin,
    UTCDateTime,
    string_enum_check,
    tenant_candidate_key,
    tenant_foreign_key,
)

DELIVERY_ATTEMPT_OUTCOMES = (
    "processing",
    "succeeded",
    "retryable_failed",
    "unknown_outcome",
    "action_required",
)
DELIVERY_RECONCILIATION_OUTCOMES = (
    "succeeded",
    "retryable_failed",
    "unknown_outcome",
    "action_required",
    "not_found",
)


class DeliveryAttempt(TenantEntityMixin, Base):
    """One numbered, leased provider call for an exact delivery snapshot."""

    __tablename__ = "delivery_attempt"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "delivery_id",
            "external_delivery",
            name="fk_delivery_attempt_org_delivery",
        ),
        tenant_foreign_key(
            "external_delivery_id",
            "external_delivery",
            name="fk_delivery_attempt_org_external_delivery",
        ),
        tenant_foreign_key(
            "operation_id",
            "operation",
            name="fk_delivery_attempt_org_operation",
        ),
        ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_delivery_attempt_org_credential_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "delivery_id",
            "id",
            name="uq_delivery_attempt_org_delivery_id",
        ),
        UniqueConstraint(
            "organization_id",
            "delivery_id",
            "id",
            "attempt_number",
            "operation_id",
            "credential_binding_id",
            "credential_binding_version",
            name="uq_delivery_attempt_org_exact_snapshot",
        ),
        UniqueConstraint(
            "organization_id",
            "delivery_id",
            "attempt_number",
            name="uq_delivery_attempt_org_delivery_number",
        ),
        UniqueConstraint(
            "organization_id",
            "external_delivery_id",
            "attempt_number",
            name="uq_delivery_attempt_org_external_delivery_number",
        ),
        UniqueConstraint(
            "organization_id",
            "claim_token",
            name="uq_delivery_attempt_org_claim_token",
        ),
        CheckConstraint(
            "delivery_id = external_delivery_id",
            name="delivery_identity_coherent",
        ),
        CheckConstraint("state = outcome", name="state_outcome_coherent"),
        CheckConstraint(
            string_enum_check("state", DELIVERY_ATTEMPT_OUTCOMES),
            name="state",
        ),
        CheckConstraint(
            string_enum_check("outcome", DELIVERY_ATTEMPT_OUTCOMES),
            name="outcome",
        ),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            "credential_binding_version >= 1",
            name="credential_binding_version_positive",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        CheckConstraint(
            "lease_expires_at >= started_at",
            name="lease_after_started",
        ),
        CheckConstraint(
            "sanitized_error IS NULL OR CHAR_LENGTH(CAST(sanitized_error AS CHAR)) <= 2048",
            name="sanitized_error_bounded",
        ),
        Index(
            "ix_delivery_attempt_org_delivery_started",
            "organization_id",
            "delivery_id",
            "started_at",
            "id",
        ),
        Index(
            "ix_delivery_attempt_org_lease",
            "organization_id",
            "state",
            "lease_expires_at",
        ),
    )

    delivery_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    external_delivery_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    credential_binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_version: Mapped[int] = mapped_column(nullable=False)
    worker_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    claim_token: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class DeliveryReconciliationObservation(TenantEntityMixin, Base):
    """Append-only result of reconciling one exact delivery attempt."""

    __tablename__ = "delivery_reconciliation_observation"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "delivery_id",
            "external_delivery",
            name="fk_delivery_observation_org_delivery",
        ),
        tenant_foreign_key(
            "external_delivery_id",
            "external_delivery",
            name="fk_delivery_observation_org_external_delivery",
        ),
        ForeignKeyConstraint(
            ["organization_id", "delivery_id", "delivery_attempt_id"],
            [
                "delivery_attempt.organization_id",
                "delivery_attempt.delivery_id",
                "delivery_attempt.id",
            ],
            name="fk_delivery_observation_org_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        tenant_foreign_key(
            "operation_id",
            "operation",
            name="fk_delivery_observation_org_operation",
        ),
        ForeignKeyConstraint(
            ["organization_id", "credential_binding_id", "credential_binding_version"],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_delivery_observation_org_credential_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "delivery_id",
            "id",
            name="uq_delivery_observation_org_delivery_id",
        ),
        UniqueConstraint(
            "organization_id",
            "delivery_id",
            "delivery_attempt_id",
            "result_digest",
            name="uq_delivery_observation_org_attempt_result",
        ),
        CheckConstraint(
            "delivery_id = external_delivery_id",
            name="delivery_identity_coherent",
        ),
        CheckConstraint(
            string_enum_check("outcome", DELIVERY_RECONCILIATION_OUTCOMES),
            name="outcome",
        ),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            "credential_binding_version >= 1",
            name="credential_binding_version_positive",
        ),
        CheckConstraint(
            "CHAR_LENGTH(request_digest) = 71 AND request_digest LIKE 'sha256:%'",
            name="request_digest_shape",
        ),
        CheckConstraint(
            "CHAR_LENGTH(result_digest) = 71 AND result_digest LIKE 'sha256:%'",
            name="result_digest_shape",
        ),
        CheckConstraint(
            "sanitized_error IS NULL OR CHAR_LENGTH(CAST(sanitized_error AS CHAR)) <= 2048",
            name="sanitized_error_bounded",
        ),
        Index(
            "ix_delivery_observation_org_delivery_observed",
            "organization_id",
            "delivery_id",
            "observed_at",
            "id",
        ),
    )

    delivery_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    external_delivery_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    delivery_attempt_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    operation_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_version: Mapped[int] = mapped_column(nullable=False)
    request_payload_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    result_payload_version: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


__all__ = [
    "DELIVERY_ATTEMPT_OUTCOMES",
    "DELIVERY_RECONCILIATION_OUTCOMES",
    "DeliveryAttempt",
    "DeliveryReconciliationObservation",
]
