"""Keep verification evidence and synchronization intent independently of inference."""

import sqlalchemy as sa
from alembic import op

revision = "20261007_verified_resolution"
down_revision = "20261007_github_checks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("findings", sa.Column("sync_intent", sa.JSON()))
    op.create_table(
        "finding_verifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("finding_id", sa.String(36), sa.ForeignKey("findings.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("review_runs.id"), nullable=False),
        sa.Column("occurrence_id", sa.String(36), sa.ForeignKey("occurrences.id"), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("finding_version", sa.Integer(), nullable=False),
        sa.Column("thread_snapshot", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("verdict", sa.String(20)),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.String(20), nullable=False),
        sa.Column("sync_attempt", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("finding_id", "head_sha", name="uq_verification_finding_head"),
    )
    op.create_index("ix_finding_verifications_finding_id", "finding_verifications", ["finding_id"])
    op.create_index("ix_finding_verifications_run_id", "finding_verifications", ["run_id"])


def downgrade():
    op.drop_table("finding_verifications")
    op.drop_column("findings", "sync_intent")
