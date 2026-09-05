"""Additive workspace persistence with tenant-scoped foreign keys."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.selectable import FromClause

from review_platform.infrastructure.db.base import (
    UUID_TYPE,
    Base,
    RevisionMixin,
    TenantEntityMixin,
    TimestampMixin,
    UTCDateTime,
    tenant_candidate_key,
    tenant_foreign_key,
)


class PublicationPolicy(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "workspace_publication_policy"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "course_run_homework", name="fk_ws_policy_publication"),
        CheckConstraint("self_review_limit >= 0", name="limit_nonnegative"),
    )
    self_review_limit: Mapped[int] = mapped_column(nullable=False)
    policy: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)


class WorkspaceArtifact(TenantEntityMixin, TimestampMixin, Base):
    __tablename__ = "workspace_artifact"
    __table_args__ = (tenant_candidate_key(),)
    owner_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user.id"), nullable=False)
    provenance: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    private: Mapped[bool] = mapped_column(nullable=False, default=False)


class WorkDraft(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "work_draft"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("publication_id", "course_run_homework", name="fk_ws_draft_publication"),
        tenant_foreign_key("upload_id", "workspace_artifact", name="fk_ws_draft_upload"),
        UniqueConstraint(
            "organization_id", "publication_id", "student_id", name="uq_ws_draft_scope"
        ),
    )
    publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    student_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user.id"), nullable=False)
    artifact_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    upload_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SelfReviewQuota(TenantEntityMixin, TimestampMixin, Base):
    __tablename__ = "self_review_quota"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("draft_id", "work_draft", name="fk_ws_quota_draft"),
        UniqueConstraint("organization_id", "draft_id", name="uq_ws_quota_draft"),
        CheckConstraint("used >= 0 AND reserved >= 0 AND reserved <= 1", name="valid_counters"),
    )
    draft_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    used: Mapped[int] = mapped_column(nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(nullable=False, default=0)


class SelfReviewRun(TenantEntityMixin, TimestampMixin, Base):
    __tablename__ = "self_review_run"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("quota_id", "self_review_quota", name="fk_ws_run_quota"),
        tenant_foreign_key("draft_id", "work_draft", name="fk_ws_run_draft"),
        tenant_foreign_key("artifact_id", "workspace_artifact", name="fk_ws_run_artifact"),
        tenant_foreign_key("homework_version_id", "homework_version", name="fk_ws_run_homework"),
        CheckConstraint(
            "status IN ('queued','capturing','pending','running',"
            "'unknown_outcome','succeeded','failed')",
            name="status",
        ),
        CheckConstraint("disposition IN ('reserved','consumed','released')", name="disposition"),
    )
    membership_revision: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    auth_epoch: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    quota_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    draft_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    draft_revision: Mapped[int] = mapped_column(nullable=False)
    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    artifact_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    artifact_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    student_text: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[list[dict[str, JsonValue]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    disposition: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    sequence: Mapped[int] = mapped_column(nullable=False, default=-1)
    input_fingerprint: Mapped[str | None] = mapped_column(String(71), nullable=True)
    result: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SelfReviewEventReceipt(TenantEntityMixin, TimestampMixin, Base):
    __tablename__ = "self_review_event_receipt"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("run_id", "self_review_run", name="fk_ws_event_run"),
    )
    run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)


class SelfReviewQuotaEvent(TenantEntityMixin, Base):
    __tablename__ = "self_review_quota_event"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("run_id", "self_review_run", name="fk_ws_qevent_run"),
        UniqueConstraint("organization_id", "run_id", "action", name="uq_ws_quota_event"),
        CheckConstraint("action IN ('reserve','consume','release')", name="action"),
    )
    run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WorkspacePreferences(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "workspace_preferences"
    __table_args__ = (
        tenant_candidate_key(),
        UniqueConstraint("organization_id", "user_id", name="uq_ws_preferences_user"),
    )
    user_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user.id"), nullable=False)
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)


class StudentReviewerAssignment(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "student_reviewer_assignment"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("course_run_id", "course_run", name="fk_ws_assignment_run"),
        UniqueConstraint(
            "organization_id", "course_run_id", "student_id", name="uq_ws_assignment_student"
        ),
    )
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    student_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user.id"), nullable=False)
    reviewer_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("user.id"), nullable=True
    )


class HomeworkPrivateDetails(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    criterion_settings: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    material_upload_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    __tablename__ = "homework_private_details"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "homework_version", name="fk_ws_private_homework"),
        tenant_foreign_key(
            "reference_upload_id", "workspace_artifact", name="fk_ws_private_reference"
        ),
    )
    reviewer_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reference_upload_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    criterion_classes: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON, nullable=False, default=dict
    )


class ReviewOutcome(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "workspace_review_outcome"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "review_iteration", name="fk_ws_outcome_iteration"),
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    revision_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


WORKSPACE_TABLES: tuple[FromClause, ...] = (
    PublicationPolicy.__table__,
    WorkspaceArtifact.__table__,
    WorkDraft.__table__,
    SelfReviewQuota.__table__,
    SelfReviewRun.__table__,
    SelfReviewEventReceipt.__table__,
    SelfReviewQuotaEvent.__table__,
    WorkspacePreferences.__table__,
    StudentReviewerAssignment.__table__,
    HomeworkPrivateDetails.__table__,
    ReviewOutcome.__table__,
)


class WorkspaceExport(TenantEntityMixin, TimestampMixin, Base):
    __tablename__ = "workspace_export"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("course_run_id", "course_run", name="fk_ws_export_run"),
        tenant_foreign_key("artifact_id", "workspace_artifact", name="fk_ws_export_artifact"),
    )
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    owner_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user.id"), nullable=False)
    options: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    row_count: Mapped[int] = mapped_column(nullable=False, default=0)
    artifact_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class WorkspaceNotification(TenantEntityMixin, TimestampMixin, Base):
    __tablename__ = "workspace_notification"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("course_run_id", "course_run", name="fk_ws_note_run"),
    )
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    recipient_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("user.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


WORKSPACE_TABLES += (WorkspaceExport.__table__, WorkspaceNotification.__table__)


class WorkspaceCourseDetails(TenantEntityMixin, Base):
    __tablename__ = "workspace_course_details"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "course", name="fk_ws_course_details"),
    )
    owner_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    stepik_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class WorkspaceRunSettings(TenantEntityMixin, RevisionMixin, Base):
    __tablename__ = "workspace_run_settings"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "course_run", name="fk_ws_run_settings"),
        CheckConstraint("priority IN ('assigned','deadline')", name="priority"),
    )
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="assigned")


WORKSPACE_TABLES += (WorkspaceCourseDetails.__table__, WorkspaceRunSettings.__table__)

# A global User foreign key alone does not establish tenant membership.

for _model, _fields in (
    (WorkspaceArtifact, ("owner_id",)),
    (WorkDraft, ("student_id",)),
    (WorkspacePreferences, ("user_id",)),
    (StudentReviewerAssignment, ("student_id", "reviewer_id")),
    (WorkspaceExport, ("owner_id",)),
    (WorkspaceNotification, ("recipient_id",)),
    (WorkspaceCourseDetails, ("owner_id",)),
):
    for _field in _fields:
        cast(Table, _model.__table__).append_constraint(
            ForeignKeyConstraint(
                ["organization_id", _field],
                ["organization_membership.organization_id", "organization_membership.user_id"],
                name=f"fk_{_model.__tablename__}_{_field}_member",
                ondelete="RESTRICT",
            )
        )


class HomeworkEditorDraft(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "homework_editor_draft"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("homework_id", "homework", name="fk_ws_editor_homework"),
        tenant_foreign_key("course_run_id", "course_run", name="fk_ws_editor_run"),
        UniqueConstraint(
            "organization_id", "homework_id", "course_run_id", name="uq_ws_editor_scope"
        ),
    )
    homework_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    course_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    value: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)


WORKSPACE_TABLES += (HomeworkEditorDraft.__table__,)


class SubmissionPolicySnapshot(TenantEntityMixin, Base):
    __tablename__ = "submission_policy_snapshot"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "submission_version", name="fk_ws_submission_policy"),
    )
    policy: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    policy_revision: Mapped[int] = mapped_column(nullable=False)


class WorkspacePublishedGrade(TenantEntityMixin, Base):
    __tablename__ = "workspace_published_grade"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "review_publication", name="fk_ws_published_grade"),
    )
    details: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)


WORKSPACE_TABLES += (SubmissionPolicySnapshot.__table__, WorkspacePublishedGrade.__table__)


class ArtifactPreparation(TenantEntityMixin, TimestampMixin, Base):
    __tablename__ = "workspace_artifact_preparation"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("draft_id", "work_draft", name="fk_ws_preparation_draft"),
        tenant_foreign_key("artifact_id", "workspace_artifact", name="fk_ws_preparation_artifact"),
        UniqueConstraint(
            "organization_id", "draft_id", "draft_revision", name="uq_ws_preparation_revision"
        ),
    )
    draft_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    draft_revision: Mapped[int] = mapped_column(nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    artifact_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)


class ReviewAssistRun(TenantEntityMixin, RevisionMixin, TimestampMixin, Base):
    __tablename__ = "workspace_review_assist_run"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("iteration_id", "review_iteration", name="fk_ws_assist_iteration"),
        tenant_foreign_key("artifact_id", "artifact_version", name="fk_ws_assist_artifact"),
        tenant_foreign_key("homework_version_id", "homework_version", name="fk_ws_assist_homework"),
        ForeignKeyConstraint(
            ["organization_id", "owner_id"],
            ["organization_membership.organization_id", "organization_membership.user_id"],
            name="fk_ws_assist_owner",
        ),
    )
    iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    owner_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    membership_revision: Mapped[int] = mapped_column(nullable=False)
    auth_epoch: Mapped[int] = mapped_column(nullable=False)
    artifact_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    homework_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    inputs: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    result: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sequence: Mapped[int] = mapped_column(nullable=False, default=-1)
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    lease_token: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ReviewAssistEventReceipt(TenantEntityMixin, Base):
    __tablename__ = "workspace_review_assist_event"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("run_id", "workspace_review_assist_run", name="fk_ws_assist_event_run"),
    )
    run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)


class ReviewAIChoice(TenantEntityMixin, Base):
    __tablename__ = "workspace_review_ai_choice"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key("id", "review_revision", name="fk_ws_ai_choice_revision"),
        tenant_foreign_key("run_id", "workspace_review_assist_run", name="fk_ws_ai_choice_run"),
    )
    signal_decisions: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    run_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)


WORKSPACE_TABLES += (
    ArtifactPreparation.__table__,
    ReviewAssistRun.__table__,
    ReviewAssistEventReceipt.__table__,
    ReviewAIChoice.__table__,
)
