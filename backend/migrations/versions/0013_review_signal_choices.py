"""Keep human decisions on AI signals alongside their immutable source run."""

from alembic import op
import sqlalchemy as sa

revision = "0013_review_signal_choices"
down_revision = "0012_workspace_durable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0011 needed a server default only to backfill pre-existing submissions.
    op.alter_column("submission_version", "comment", existing_type=sa.Text(), server_default=None)
    op.add_column(
        "workspace_review_ai_choice", sa.Column("signal_decisions", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.execute("ALTER TABLE submission_version MODIFY COLUMN comment TEXT NOT NULL DEFAULT ('')")
    op.drop_column("workspace_review_ai_choice", "signal_decisions")
