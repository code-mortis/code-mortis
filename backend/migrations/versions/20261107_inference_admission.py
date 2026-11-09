"""Durable connection-wide inference admission, preserving existing task history."""

import sqlalchemy as sa
from alembic import op

revision = "20261107_inference_admission"
down_revision = "20261107_engine_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inference_admissions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("connection_id", sa.String(36), sa.ForeignKey("connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_key", sa.String(100), nullable=False),
        sa.Column("task_id", sa.String(64), sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE")),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE")),
        sa.Column("job_attempt", sa.Integer()),
        sa.Column("slots", sa.Integer(), nullable=False),
        sa.Column("run_limit", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("slots BETWEEN 1 AND 8 AND run_limit BETWEEN 1 AND 8", name="ck_inference_capacity"),
    )
    op.create_index("ix_inference_connection_queue", "inference_admissions", ["connection_id", "status", "created_at"])
    op.create_index("ix_inference_run_grants", "inference_admissions", ["connection_id", "run_key", "admitted_at"])


def downgrade():
    op.drop_table("inference_admissions")
