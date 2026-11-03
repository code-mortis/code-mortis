"""Preserve the order of successful default-branch observations during indexing."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_graph_default_order"
down_revision = "20261026_graph_readiness"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("code_graph_repositories", sa.Column("default_observed_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE code_graph_repositories SET default_observed_at = COALESCE(checked_at, CURRENT_TIMESTAMP) "
               "WHERE default_sha IS NOT NULL")


def downgrade():
    op.drop_column("code_graph_repositories", "default_observed_at")
