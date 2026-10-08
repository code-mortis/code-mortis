"""Preserve GitHub pull-request creation times for inbox ordering."""

import sqlalchemy as sa
from alembic import op

revision = "20261008_pull_creation_time"
down_revision = "20261007_verified_resolution"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pull_requests", sa.Column("created_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("pull_requests", "created_at")
