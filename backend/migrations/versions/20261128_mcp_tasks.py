"""Retain the state of task-capable MCP operations."""
import sqlalchemy as sa
from alembic import op

revision = "20261128_mcp_tasks"
down_revision = "20261127_unified_mcp"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mcp_tasks",
        sa.Column("operation_id", sa.String(36), sa.ForeignKey("mcp_operations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("mcp_tasks")
