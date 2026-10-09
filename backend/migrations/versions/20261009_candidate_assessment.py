"""Keep review candidates separate from accepted findings and reserve publication."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_candidate_assessment"
down_revision = "20261008_pull_creation_time"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("findings", sa.Column("publication_run_id", sa.String(36), sa.ForeignKey("review_runs.id")))
    op.add_column("findings", sa.Column("publication_batch", sa.Integer()))
    op.add_column("findings", sa.Column("published_review_id", sa.BigInteger()))
    op.create_table(
        "review_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("finding_id", sa.String(36), sa.ForeignKey("findings.id")),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "position"),
    )
    op.create_index("ix_review_candidates_run_id", "review_candidates", ["run_id"])
    op.create_index("ix_review_candidates_finding_id", "review_candidates", ["finding_id"])


def downgrade():
    op.drop_table("review_candidates")
    for name in ("published_review_id", "publication_batch", "publication_run_id"):
        op.drop_column("findings", name)
