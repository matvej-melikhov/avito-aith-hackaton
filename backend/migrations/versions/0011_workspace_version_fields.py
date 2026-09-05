"""Persist review-only rubric versions and immutable submission comments."""
from alembic import op
import sqlalchemy as sa

revision = "0011_workspace_version_fields"
down_revision = "0010_uploaded_artifacts"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("homework_version", sa.Column("review_only", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("submission_version", sa.Column("comment", sa.Text(), nullable=False, server_default=sa.text("('')")))

def downgrade() -> None:
    op.drop_column("submission_version", "comment")
    op.drop_column("homework_version", "review_only")
