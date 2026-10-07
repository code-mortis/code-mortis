"""Track GitHub checks independently of review execution and publication."""

import sqlalchemy as sa
from alembic import op

revision = "20261007_github_checks"
down_revision = "20261006_connections"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [
        ("failure_reason", sa.String(30)),
        ("check_id", sa.BigInteger()),
        ("check_url", sa.Text()),
        ("check_status", sa.String(30)),
        ("check_conclusion", sa.String(30)),
        ("check_error", sa.Text()),
        ("check_payload_hash", sa.String(64)),
        ("check_synced_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("review_runs", sa.Column(name, kind))
    for name in ("check_enabled", "check_pending"):
        op.add_column("review_runs", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_unique_constraint("uq_review_runs_check_id", "review_runs", ["check_id"])
    op.add_column("webhook_deliveries", sa.Column("handled_at", sa.DateTime(timezone=True)))
    op.execute("""UPDATE review_runs SET check_enabled = true, check_pending = true
        WHERE status IN ('queued', 'running', 'blocked')
           OR publication IN ('pending', 'publishing') AND status IN ('completed', 'partial')""")
    for name in ("check_enabled", "check_pending"):
        op.alter_column("review_runs", name, server_default=sa.true())


def downgrade():
    op.drop_column("webhook_deliveries", "handled_at")
    op.drop_constraint("uq_review_runs_check_id", "review_runs", type_="unique")
    for name in (
        "failure_reason",
        "check_enabled",
        "check_pending",
        "check_id",
        "check_url",
        "check_status",
        "check_conclusion",
        "check_error",
        "check_payload_hash",
        "check_synced_at",
    ):
        op.drop_column("review_runs", name)
