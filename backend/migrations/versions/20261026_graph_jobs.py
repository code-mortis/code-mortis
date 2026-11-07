"""Persist graph activation and the current durable job, without enabling repositories."""

import sqlalchemy as sa
from alembic import op

revision = "20261026_graph_jobs"
down_revision = "20261026_code_graph"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "code_graph_repositories",
        sa.Column("repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("default_sha", sa.String(40)),
    )
    op.add_column("code_graph_snapshots", sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL")))
    op.add_column("code_graph_snapshots", sa.Column("policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))


def downgrade():
    op.drop_column("code_graph_snapshots", "policy")
    op.drop_column("code_graph_snapshots", "job_id")
    op.drop_table("code_graph_repositories")
