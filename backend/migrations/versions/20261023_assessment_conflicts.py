"""Preserve the reason an assessment was interrupted for targeted recovery."""

import sqlalchemy as sa
from alembic import op

revision = "20261023_assessment_conflicts"
down_revision = "20261022_installation_removal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_candidates", sa.Column("failure_reason", sa.String(30), nullable=True))


def downgrade():
    op.drop_column("review_candidates", "failure_reason")
