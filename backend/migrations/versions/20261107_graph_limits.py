"""Repository indexing limits and safe attempt diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "20261107_graph_limits"
down_revision = "20261103_graph_default_order"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("repositories", sa.Column("code_graph_limits", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    for table in ("code_graph_repositories", "code_graph_snapshots"):
        op.add_column(table, sa.Column("failure", sa.JSON()))


def downgrade():
    for table in ("code_graph_repositories", "code_graph_snapshots"):
        op.drop_column(table, "failure")
    op.drop_column("repositories", "code_graph_limits")
