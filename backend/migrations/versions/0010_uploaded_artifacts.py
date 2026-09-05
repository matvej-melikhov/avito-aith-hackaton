"""Allow credential-free uploaded artifacts while preserving external credentials."""
from alembic import op
import sqlalchemy as sa

revision = "0010_uploaded_artifacts"
down_revision = "0009_workspace_self_review"
branch_labels=None
depends_on=None

def upgrade() -> None:
    op.drop_constraint('ck_artifact_reference_provider','artifact_reference',type_='check')
    op.alter_column('artifact_reference','credential_binding_id',existing_type=sa.Uuid(native_uuid=False),nullable=True)
    op.alter_column('artifact_reference','credential_binding_version',existing_type=sa.Integer(),nullable=True)
    op.create_check_constraint('ck_artifact_reference_provider','artifact_reference',"provider IN ('github','google_docs','upload')")
    op.create_check_constraint('ck_artifact_reference_credential_source','artifact_reference',"(provider = 'upload' AND credential_binding_id IS NULL AND credential_binding_version IS NULL) OR (provider != 'upload' AND credential_binding_id IS NOT NULL AND credential_binding_version IS NOT NULL)")

def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM artifact_reference WHERE provider='upload'")):
        raise RuntimeError('Uploaded artifact rows must be migrated before downgrading.')
    op.drop_constraint('ck_artifact_reference_credential_source','artifact_reference',type_='check')
    op.drop_constraint('ck_artifact_reference_provider','artifact_reference',type_='check')
    op.alter_column('artifact_reference','credential_binding_id',existing_type=sa.Uuid(native_uuid=False),nullable=False)
    op.alter_column('artifact_reference','credential_binding_version',existing_type=sa.Integer(),nullable=False)
    op.create_check_constraint('ck_artifact_reference_provider','artifact_reference',"provider IN ('github','google_docs')")
