"""Create immutable human review revisions.

Revision ID: 0005_review_spine
Revises: 0004_submissions_and_artifacts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_review_spine"
down_revision: str | None = "0004_submissions_and_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = sa.Uuid(as_uuid=True, native_uuid=False)


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organization.id"],
        name=f"fk_{table}_organization_id_organization",
        ondelete="RESTRICT",
    )


def _tenant_uq(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id")


def upgrade() -> None:
    op.create_table(
        "review_revision",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("review_iteration_id", UUID, nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("author_user_id", UUID, nullable=False),
        sa.Column("base_revision_id", UUID, nullable=True),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("total_score", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision_number >= 1", name="ck_review_revision_revision_number_positive"
        ),
        sa.CheckConstraint("total_score >= 0", name="ck_review_revision_total_score_nonnegative"),
        _org_fk("review_revision"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_iteration_id"],
            ["review_iteration.organization_id", "review_iteration.id"],
            name="fk_review_revision_org_iteration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_iteration_id", "base_revision_id"],
            [
                "review_revision.organization_id",
                "review_revision.review_iteration_id",
                "review_revision.id",
            ],
            name="fk_review_revision_org_base_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["user.id"],
            name="fk_review_revision_author_user_id_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_revision"),
        _tenant_uq("review_revision"),
        sa.UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "id",
            name="uq_review_revision_org_iteration_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "review_iteration_id",
            "revision_number",
            name="uq_review_revision_org_iteration_number",
        ),
    )

    op.create_table(
        "review_criterion_decision",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("review_revision_id", UUID, nullable=False),
        sa.Column("criterion_id", UUID, nullable=False),
        sa.Column("ai_suggestion_id", UUID, nullable=True),
        sa.Column("points", sa.Numeric(12, 2), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.CheckConstraint("points >= 0", name="ck_review_criterion_decision_points_nonnegative"),
        sa.CheckConstraint(
            "decision IN ('accepted', 'changed', 'manual')",
            name="ck_review_criterion_decision_decision",
        ),
        _org_fk("review_criterion_decision"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_revision_id"],
            ["review_revision.organization_id", "review_revision.id"],
            name="fk_review_decision_org_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "criterion_id"],
            ["criterion.organization_id", "criterion.id"],
            name="fk_review_decision_org_criterion",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_criterion_decision"),
        _tenant_uq("review_criterion_decision"),
        sa.UniqueConstraint(
            "organization_id",
            "review_revision_id",
            "criterion_id",
            name="uq_review_decision_org_revision_criterion",
        ),
    )

    op.create_table(
        "review_note",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("review_revision_id", UUID, nullable=False),
        sa.Column("criterion_id", UUID, nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author_user_id", UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_review_note_position_nonnegative"),
        _org_fk("review_note"),
        sa.ForeignKeyConstraint(
            ["organization_id", "review_revision_id"],
            ["review_revision.organization_id", "review_revision.id"],
            name="fk_review_note_org_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "criterion_id"],
            ["criterion.organization_id", "criterion.id"],
            name="fk_review_note_org_criterion",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["user.id"],
            name="fk_review_note_author_user_id_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_note"),
        _tenant_uq("review_note"),
        sa.UniqueConstraint(
            "organization_id",
            "review_revision_id",
            "position",
            name="uq_review_note_org_revision_position",
        ),
    )
    op.create_foreign_key(
        "fk_review_iteration_current_revision",
        "review_iteration",
        "review_revision",
        ["organization_id", "id", "current_revision_id"],
        ["organization_id", "review_iteration_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_review_iteration_current_revision", "review_iteration", type_="foreignkey"
    )
    op.drop_table("review_note")
    op.drop_table("review_criterion_decision")
    op.drop_table("review_revision")
