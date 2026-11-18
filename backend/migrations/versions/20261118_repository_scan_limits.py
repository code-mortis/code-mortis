"""Store execution limits for each repository's security scans."""

import sqlalchemy as sa
from alembic import op

revision = "20261118_repository_scan_limits"
down_revision = "20261118_pr_sync_check"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("repositories", sa.Column("scan_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))


def downgrade():
    op.drop_column("repositories", "scan_settings")
