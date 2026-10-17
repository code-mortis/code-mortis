"""Preserve ignored pull requests and their review history."""

import sqlalchemy as sa
from alembic import op

revision = "20261019_ignored_pulls"
down_revision = "20261018_verification_retries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pull_requests", sa.Column("ignored", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("pull_requests", "ignored")
