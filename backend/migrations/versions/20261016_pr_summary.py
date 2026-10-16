"""Track PR overviews without changing historical reviews."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_pr_summary"
down_revision = "20261011_assessment_attempts"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [
        ("summary_comment_id", sa.BigInteger()), ("summary_url", sa.Text()),
        ("summary_error", sa.Text()), ("summary_hash", sa.String(64)),
        ("summary_synced_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("pull_requests", sa.Column(name, kind))
    op.add_column("review_runs", sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("review_runs", "summary")
    for name in ("summary_comment_id", "summary_url", "summary_error", "summary_hash", "summary_synced_at"):
        op.drop_column("pull_requests", name)
