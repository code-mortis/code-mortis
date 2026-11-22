"""Save GitHub pull request descriptions for Overview."""

import sqlalchemy as sa
from alembic import op

revision = "20261122_pr_description"
down_revision = "20261122_graph_absolute_imports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pull_requests", sa.Column("description", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("pull_requests", "description")
