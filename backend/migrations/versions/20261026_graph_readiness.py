"""Persist graph readiness and explicit cancellation without enabling repositories."""

import sqlalchemy as sa
from alembic import op

revision = "20261026_graph_readiness"
down_revision = "20261026_graph_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("code_graph_repositories", sa.Column("observed_sha", sa.String(40)))
    op.add_column("code_graph_repositories", sa.Column("checked_at", sa.DateTime(timezone=True)))
    op.add_column("code_graph_repositories", sa.Column("error", sa.Text()))
    op.add_column("code_graph_repositories", sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    for column in ("paused", "error", "checked_at", "observed_sha"):
        op.drop_column("code_graph_repositories", column)
