"""Immutable human review revisions, decisions, and notes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Table,
    Text,
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
from review_platform.infrastructure.db.models.review_case import ReviewIteration

REVIEW_DECISIONS = ("accepted", "changed", "manual")


class ReviewRevision(TenantEntityMixin, Base):
    __tablename__ = "review_revision"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "review_iteration_id", "review_iteration", name="fk_review_revision_org_iteration"
        ),
        UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "id",
            name="uq_review_revision_org_iteration_id",
        ),
        UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "revision_number",
            name="uq_review_revision_org_iteration_number",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "base_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_review_revision_org_base_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("total_score >= 0", name="total_score_nonnegative"),
    )

    review_iteration_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    revision_number: Mapped[int] = mapped_column(nullable=False)
    author_user_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    base_revision_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ReviewCriterionDecision(TenantEntityMixin, Base):
    __tablename__ = "review_criterion_decision"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "review_revision_id", "review_revision", name="fk_review_decision_org_revision"
        ),
        tenant_foreign_key("criterion_id", "criterion", name="fk_review_decision_org_criterion"),
        tenant_foreign_key(
            "ai_suggestion_id",
            "ai_criterion_suggestion",
            name="fk_review_decision_org_ai_suggestion",
        ),
        UniqueConstraint(
            "organization_id",
            "review_revision_id",
            "criterion_id",
            name="uq_review_decision_org_revision_criterion",
        ),
        CheckConstraint("points >= 0", name="points_nonnegative"),
        CheckConstraint(string_enum_check("decision", REVIEW_DECISIONS), name="decision"),
    )

    review_revision_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    criterion_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    ai_suggestion_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    points: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class ReviewNote(TenantEntityMixin, Base):
    __tablename__ = "review_note"
    __table_args__ = (
        tenant_candidate_key(),
        tenant_foreign_key(
            "review_revision_id", "review_revision", name="fk_review_note_org_revision"
        ),
        tenant_foreign_key("criterion_id", "criterion", name="fk_review_note_org_criterion"),
        UniqueConstraint(
            "organization_id",
            "review_revision_id",
            "position",
            name="uq_review_note_org_revision_position",
        ),
        CheckConstraint("position >= 0", name="position_nonnegative"),
    )

    review_revision_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    criterion_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(nullable=False)


iteration_table = cast(Table, ReviewIteration.__table__)
if not any(
    constraint.name == "fk_review_iteration_current_revision"
    for constraint in iteration_table.constraints
):
    iteration_table.append_constraint(
        ForeignKeyConstraint(
            ["organization_id", "id", "current_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_review_iteration_current_revision",
            ondelete="RESTRICT",
            use_alter=True,
        )
    )


__all__ = ["REVIEW_DECISIONS", "ReviewCriterionDecision", "ReviewNote", "ReviewRevision"]
