"""Preserve candidate assessment attempts across technical retries."""

import sqlalchemy as sa
from alembic import op

revision = "20261011_assessment_attempts"
down_revision = "20261010_installation_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_candidates", sa.Column("attempts", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("review_candidates", "attempts")
