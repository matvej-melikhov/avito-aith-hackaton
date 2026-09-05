"""Versioned public submission source policy, with legacy all-source default."""

from alembic import op
import sqlalchemy as sa

revision = "0015_homework_sources"
down_revision = "0014_homework_check_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_private_details", sa.Column("allowed_sources", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("homework_private_details", "allowed_sources")
