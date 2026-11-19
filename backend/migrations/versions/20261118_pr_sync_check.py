"""Record successful GitHub pull-request checks."""

import sqlalchemy as sa
from alembic import op

revision = "20261118_pr_sync_check"
down_revision = "20261112_graph_diagnostics"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("repositories", sa.Column("last_pr_sync_check_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("repositories", "last_pr_sync_check_at")
