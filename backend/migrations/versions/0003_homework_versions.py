"""Create immutable homework versions and per-CourseRun publications.

Revision ID: 0003_homework_versions
Revises: 0002_identity_and_courses
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_homework_versions"
down_revision: str | None = "0002_identity_and_courses"
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


def _tenant_candidate(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint(
        "organization_id",
        "id",
        name=f"uq_{table}_organization_id",
    )


def upgrade() -> None:
    op.create_table(
        "homework",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("course_id", UUID, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_homework_revision_nonnegative"),
        _organization_fk("homework"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_id"],
            ["course.organization_id", "course.id"],
            name="fk_homework_org_course",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_homework"),
        _tenant_candidate("homework"),
    )
    op.create_index(
        "ix_homework_org_course",
        "homework",
        ["organization_id", "course_id"],
        unique=False,
    )

    op.create_table(
        "homework_version",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("student_text", sa.Text(), nullable=False),
        sa.Column("max_score", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("artifact_kinds", sa.JSON(), nullable=False),
        sa.Column("estimated_review_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_homework_version_version_number_positive",
        ),
        sa.CheckConstraint(
            "max_score >= 0",
            name="ck_homework_version_max_score_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_review_minutes >= 1 AND estimated_review_minutes <= 10080",
            name="ck_homework_version_estimated_review_minutes_range",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_homework_version_revision_nonnegative",
        ),
        _organization_fk("homework_version"),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id"],
            ["homework.organization_id", "homework.id"],
            name="fk_homework_version_org_homework",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_homework_version"),
        _tenant_candidate("homework_version"),
        sa.UniqueConstraint(
            "organization_id",
            "homework_id",
            "id",
            name="uq_homework_version_org_homework_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "homework_id",
            "version_number",
            name="uq_homework_version_org_homework_number",
        ),
    )

    op.create_table(
        "criterion_set",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("homework_version_id", UUID, nullable=False),
        _organization_fk("criterion_set"),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_version_id"],
            ["homework_version.organization_id", "homework_version.id"],
            name="fk_criterion_set_org_homework_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_criterion_set"),
        _tenant_candidate("criterion_set"),
        sa.UniqueConstraint(
            "organization_id",
            "homework_version_id",
            name="uq_criterion_set_org_homework_version",
        ),
    )

    op.create_table(
        "criterion",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        *_timestamps(),
        sa.Column("criterion_set_id", UUID, nullable=False),
        sa.Column("stable_key", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("max_points", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_criterion_position_nonnegative"),
        sa.CheckConstraint("max_points >= 0", name="ck_criterion_max_points_nonnegative"),
        _organization_fk("criterion"),
        sa.ForeignKeyConstraint(
            ["organization_id", "criterion_set_id"],
            ["criterion_set.organization_id", "criterion_set.id"],
            name="fk_criterion_org_criterion_set",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_criterion"),
        _tenant_candidate("criterion"),
        sa.UniqueConstraint(
            "organization_id",
            "criterion_set_id",
            "stable_key",
            name="uq_criterion_org_set_stable_key",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "criterion_set_id",
            "position",
            name="uq_criterion_org_set_position",
        ),
    )

    op.create_table(
        "course_run_homework",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.Column("course_run_id", UUID, nullable=False),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("current_publication_id", UUID, nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_course_run_homework_status",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_course_run_homework_revision_nonnegative",
        ),
        _organization_fk("course_run_homework"),
        sa.ForeignKeyConstraint(
            ["organization_id", "course_run_id"],
            ["course_run.organization_id", "course_run.id"],
            name="fk_course_run_homework_org_course_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id"],
            ["homework.organization_id", "homework.id"],
            name="fk_course_run_homework_org_homework",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_run_homework"),
        _tenant_candidate("course_run_homework"),
        sa.UniqueConstraint(
            "organization_id",
            "homework_id",
            "id",
            name="uq_course_run_homework_org_homework_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "course_run_id",
            "homework_id",
            name="uq_course_run_homework_org_run_homework",
        ),
    )
    op.create_index(
        "ix_course_run_homework_org_run",
        "course_run_homework",
        ["organization_id", "course_run_id"],
        unique=False,
    )

    op.create_table(
        "course_run_homework_publication",
        sa.Column("id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("course_run_homework_id", UUID, nullable=False),
        sa.Column("homework_id", UUID, nullable=False),
        sa.Column("homework_version_id", UUID, nullable=False),
        sa.Column("publication_sequence", sa.Integer(), nullable=False),
        sa.Column("submission_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_sequence >= 1",
            name="ck_course_run_homework_publication_publication_sequence_positive",
        ),
        sa.CheckConstraint(
            "submission_deadline <= review_deadline",
            name="ck_course_run_homework_publication_deadline_order",
        ),
        _organization_fk("course_run_homework_publication"),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id", "course_run_homework_id"],
            [
                "course_run_homework.organization_id",
                "course_run_homework.homework_id",
                "course_run_homework.id",
            ],
            name="fk_crh_publication_org_relation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "homework_id", "homework_version_id"],
            [
                "homework_version.organization_id",
                "homework_version.homework_id",
                "homework_version.id",
            ],
            name="fk_crh_publication_org_homework_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_run_homework_publication"),
        _tenant_candidate("course_run_homework_publication"),
        sa.UniqueConstraint(
            "organization_id",
            "course_run_homework_id",
            "id",
            name="uq_crh_publication_org_relation_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "course_run_homework_id",
            "publication_sequence",
            name="uq_crh_publication_org_relation_sequence",
        ),
    )
    op.create_index(
        "ix_crh_publication_org_relation_published",
        "course_run_homework_publication",
        ["organization_id", "course_run_homework_id", "published_at"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_course_run_homework_current_publication",
        "course_run_homework",
        "course_run_homework_publication",
        ["organization_id", "id", "current_publication_id"],
        ["organization_id", "course_run_homework_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_course_run_homework_current_publication",
        "course_run_homework",
        type_="foreignkey",
    )
    op.drop_table("criterion")
    op.drop_table("criterion_set")
    op.drop_table("course_run_homework_publication")
    op.drop_table("course_run_homework")
    op.drop_table("homework_version")
    op.drop_table("homework")
