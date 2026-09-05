"""Review successors, publication authority, and durable delivery intents."""

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
    func,
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
    string_enum_check,
    tenant_candidate_key,
    tenant_foreign_key,
)

REVIEW_ITERATION_RELATION_KINDS = ("correction", "requirements_migration")
REVIEW_PUBLICATION_STATUSES = ("published",)
PUBLICATION_REQUEST_STATUSES = (
    "pending",
    "confirmed",
    "rejected",
    "expired",
    "superseded",
)
EXTERNAL_DELIVERY_STATES = (
    "pending",
    "processing",
    "retryable_failed",
    "unknown_outcome",
    "reconciling",
    "succeeded",
    "action_required",
    "superseded",
)
DELIVERY_DESTINATION_KINDS = ("stepik", "github")


class ReviewIterationRelation(TenantEntityMixin, Base):
    """Append-only predecessor/successor relationship within one ReviewCase."""

    __tablename__ = "review_iteration_relation"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
            ["organization_id", "review_case_id", "predecessor_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_relation_org_case_predecessor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_case_id", "successor_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_relation_org_case_successor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "predecessor_iteration_id",
            "successor_iteration_id",
            name="uq_review_relation_org_predecessor_successor",
        ),
        CheckConstraint(
            "predecessor_iteration_id <> successor_iteration_id",
            name="different_iterations",
        ),
        CheckConstraint(
            string_enum_check("kind", REVIEW_ITERATION_RELATION_KINDS),
            name="kind",
        ),
        Index(
            "ix_review_relation_org_successor",
            "organization_id",
            "successor_iteration_id",
        ),
    )

    review_case_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    predecessor_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    successor_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class ReviewImpactEvent(TenantEntityMixin, Base):
    """Append-only impact of a newer CourseRun homework publication on a review."""

    __tablename__ = "review_impact_event"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "source_event_id",
            "outbox_message",
            referred_id_column="message_id",
            name="fk_review_impact_org_source_event",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_case_id", "review_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_impact_org_case_iteration",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "course_run_homework_id",
            "course_run_homework",
            name="fk_review_impact_org_run_homework",
        ),
        tenant_foreign_key(
            "course_run_id",
            "course_run",
            name="fk_review_impact_org_course_run",
        ),
        tenant_foreign_key(
            "homework_id",
            "homework",
            name="fk_review_impact_org_homework",
        ),
        ForeignKeyConstraint(
            ["organization_id", "homework_id", "previous_homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_review_impact_org_previous_homework_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "homework_id", "current_homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_review_impact_org_current_homework_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "course_run_homework_id", "previous_publication_id"],
            [
                "course_run_homework_publication.organization_id",
                "course_run_homework_publication.course_run_homework_id",
                "course_run_homework_publication.id",
            ],
            name="fk_review_impact_org_previous_publication",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "course_run_homework_id", "current_publication_id"],
            [
                "course_run_homework_publication.organization_id",
                "course_run_homework_publication.course_run_homework_id",
                "course_run_homework_publication.id",
            ],
            name="fk_review_impact_org_current_publication",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_case_id", "resolved_by_iteration_id"],
            [
                "review_iteration.organization_id",
                "review_iteration.review_case_id",
                "review_iteration.id",
            ],
            name="fk_review_impact_org_resolving_iteration",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "source_event_id",
            "review_iteration_id",
            name="uq_review_impact_org_event_iteration",
        ),
        UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "current_homework_version_id",
            name="uq_review_impact_org_iteration_version",
        ),
        CheckConstraint(
            "previous_homework_version_id <> current_homework_version_id",
            name="different_homework_versions",
        ),
        CheckConstraint("publication_sequence >= 1", name="publication_sequence_positive"),
        Index(
            "ix_review_impact_org_iteration_occurred",
            "organization_id",
            "review_iteration_id",
            "occurred_at",
        ),
        Index(
            "ix_review_impact_org_unresolved",
            "organization_id",
            "resolved_at",
            "occurred_at",
        ),
    )

    source_event_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    review_case_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    review_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    course_run_homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    previous_homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    current_homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    previous_publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    current_publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    publication_sequence: Mapped[int] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    resolved_by_iteration_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PublicationRequest(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Idempotent agent request for a human to publish one exact revision."""

    __tablename__ = "publication_request"
    __table_args__ = (
        tenant_candidate_key(),
        UniqueConstraint(
            "organization_id",
            "id",
            "review_iteration_id",
            "review_revision_id",
            name="uq_publication_request_org_id_revision",
        ),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_publication_request_org_idempotency",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "review_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_publication_request_org_iteration_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "requested_by_user_id"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_publication_request_org_requester_membership",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "agent_authorization_id",
            "agent_authorization",
            name="fk_publication_request_org_agent_authorization",
        ),
        ForeignKeyConstraint(
            ["organization_id", "confirmed_by"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_publication_request_org_confirmer_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            string_enum_check("status", PUBLICATION_REQUEST_STATUSES),
            name="status",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint(
            "(status = 'confirmed' AND confirmed_by IS NOT NULL "
            "AND confirmed_at IS NOT NULL) OR "
            "(status <> 'confirmed' AND confirmed_by IS NULL "
            "AND confirmed_at IS NULL)",
            name="confirmation_coherent",
        ),
        Index(
            "ix_publication_request_org_iteration_status",
            "organization_id",
            "review_iteration_id",
            "status",
        ),
        Index(
            "ix_publication_request_org_status_expires",
            "organization_id",
            "status",
            "expires_at",
        ),
    )

    review_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    review_revision_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    agent_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    agent_authorization_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    confirmed_by: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ReviewPublication(TenantEntityMixin, RevisionMixin, Base):
    """A terminal human publication of immutable ReviewRevision bytes."""

    __tablename__ = "review_publication"
    __table_args__ = (
        tenant_candidate_key(),
        UniqueConstraint(
            "organization_id",
            "id",
            "review_iteration_id",
            "review_revision_id",
            name="uq_review_publication_org_id_revision",
        ),
        UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "review_revision_id",
            name="uq_review_publication_org_iteration_revision",
        ),
        UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "publication_version",
            name="uq_review_publication_org_iteration_version",
        ),
        UniqueConstraint(
            "organization_id",
            "publication_request_id",
            name="uq_review_publication_org_request",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "review_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_review_publication_org_iteration_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
            ["organization_id", "published_by"],
            [
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ],
            name="fk_review_publication_org_publisher_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            string_enum_check("status", REVIEW_PUBLICATION_STATUSES),
            name="status",
        ),
        CheckConstraint("publication_version >= 1", name="publication_version_positive"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index(
            "ix_review_publication_org_iteration_published",
            "organization_id",
            "review_iteration_id",
            "published_at",
        ),
    )

    review_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    review_revision_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    publication_request_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    publication_version: Mapped[int] = mapped_column(nullable=False)
    published_by: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="published",
        server_default=text("'published'"),
    )


class ExternalDelivery(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    """Mutable delivery state over immutable destination and review snapshots."""

    __tablename__ = "external_delivery"
    __table_args__ = (
        tenant_candidate_key(),
        ForeignKeyConstraint(
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
        tenant_foreign_key(
            "operation_id",
            "operation",
            name="fk_external_delivery_org_operation",
        ),
        ForeignKeyConstraint(
            ["organization_id", "destination_binding_id", "binding_version"],
            [
                "destination_binding.organization_id",
                "destination_binding.id",
                "destination_binding.binding_version",
            ],
            name="fk_external_delivery_org_destination_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "organization_id",
                "credential_binding_id",
                "credential_binding_version",
            ],
            [
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ],
            name="fk_external_delivery_org_credential_version",
            ondelete="RESTRICT",
        ),
        tenant_foreign_key(
            "course_run_id",
            "course_run",
            name="fk_external_delivery_org_course_run",
        ),
        tenant_foreign_key(
            "homework_version_id",
            "homework_version",
            name="fk_external_delivery_org_homework_version",
        ),
        tenant_foreign_key(
            "criterion_set_id",
            "criterion_set",
            name="fk_external_delivery_org_criterion_set",
        ),
        tenant_foreign_key(
            "submission_version_id",
            "submission_version",
            name="fk_external_delivery_org_submission_version",
        ),
        tenant_foreign_key(
            "artifact_version_id",
            "artifact_version",
            name="fk_external_delivery_org_artifact_version",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "review_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_external_delivery_org_iteration_revision",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "publication_id",
            "destination_binding_id",
            "binding_version",
            "payload_version",
            name="uq_external_delivery_org_publication_destination_payload",
        ),
        UniqueConstraint(
            "organization_id",
            "operation_id",
            name="uq_external_delivery_org_operation",
        ),
        UniqueConstraint(
            "organization_id",
            "delivery_key",
            name="uq_external_delivery_org_delivery_key",
        ),
        CheckConstraint(
            string_enum_check("destination_kind", DELIVERY_DESTINATION_KINDS),
            name="destination_kind",
        ),
        CheckConstraint(
            string_enum_check("state", EXTERNAL_DELIVERY_STATES),
            name="state",
        ),
        CheckConstraint("binding_version >= 1", name="binding_version_positive"),
        CheckConstraint(
            "credential_binding_version >= 1",
            name="credential_binding_version_positive",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index(
            "ix_external_delivery_org_state_next_attempt",
            "organization_id",
            "state",
            "next_attempt_at",
        ),
        Index(
            "ix_external_delivery_org_publication",
            "organization_id",
            "publication_id",
        ),
    )

    publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)

    # Immutable destination snapshot.
    delivery_key: Mapped[str] = mapped_column(String(512), nullable=False)
    destination_binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    binding_version: Mapped[int] = mapped_column(nullable=False)
    credential_binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    credential_binding_version: Mapped[int] = mapped_column(nullable=False)
    destination_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_ref: Mapped[str] = mapped_column(String(512), nullable=False)

    # Immutable publication provenance snapshot.
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    criterion_set_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    submission_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    artifact_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    artifact_content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    review_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    review_revision_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    payload_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitized_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


__all__ = [
    "DELIVERY_DESTINATION_KINDS",
    "EXTERNAL_DELIVERY_STATES",
    "PUBLICATION_REQUEST_STATUSES",
    "REVIEW_ITERATION_RELATION_KINDS",
    "REVIEW_PUBLICATION_STATUSES",
    "ExternalDelivery",
    "PublicationRequest",
    "ReviewImpactEvent",
    "ReviewIterationRelation",
    "ReviewPublication",
]
