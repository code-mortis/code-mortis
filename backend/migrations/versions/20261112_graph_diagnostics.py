"""Keep bounded parser locations alongside saved file coverage."""

import sqlalchemy as sa
from alembic import op

revision = "20261112_graph_diagnostics"
down_revision = "20261108_mortis_only"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("code_graph_files", sa.Column("diagnostic", sa.JSON()))


def downgrade():
    op.drop_column("code_graph_files", "diagnostic")
