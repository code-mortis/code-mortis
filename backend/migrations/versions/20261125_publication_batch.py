"""Index findings selected for a publication batch."""

import sqlalchemy as sa
from alembic import op

revision = "20261125_publication_batch"
down_revision = "20261122_pr_description"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_findings_publication_batch", "findings", ["publication_run_id", "publication_batch"],
        postgresql_where=sa.text("publication_run_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("ix_findings_publication_batch", table_name="findings")
