"""Persist criterion score increments and optional quality-check gates."""

from alembic import op
import sqlalchemy as sa

revision = "0014_homework_check_settings"
down_revision = "0013_review_signal_choices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_private_details", sa.Column("criterion_settings", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("homework_private_details", "criterion_settings")
