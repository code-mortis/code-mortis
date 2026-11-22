"""Track custom rule usage for future PR reviews."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20261122_review_rule_usage"
down_revision = "20261120_activity_snapshots"
branch_labels = None
depends_on = None


def _assign_rule_ids():
    repositories = sa.table("repositories", sa.column("id", sa.BigInteger()), sa.column("settings", sa.JSON()))
    connection = op.get_bind()
    for identity, settings in connection.execute(sa.select(repositories.c.id, repositories.c.settings)):
        rules = settings.get("review_rules") if isinstance(settings, dict) else None
        if not isinstance(rules, list):
            continue
        changed = False
        for rule in rules:
            if isinstance(rule, dict) and not rule.get("rule_id"):
                rule["rule_id"] = str(uuid4())
                changed = True
        if changed:
            connection.execute(repositories.update().where(repositories.c.id == identity).values(settings=settings))


def upgrade():
    op.add_column("review_runs", sa.Column("rule_tracking", sa.JSON()))
    op.create_table(
        "review_rule_uses",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("review_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("rule_id", sa.String(36), primary_key=True),
        sa.Column("path_hash", sa.String(64), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_rule_uses_rule_date", "review_rule_uses", ["rule_id", "used_at"])
    # Assign identities to current definitions only. Saved review settings and history remain intact.
    _assign_rule_ids()


def downgrade():
    # Preserve stable identities when the tracking schema is downgraded and re-applied.
    op.drop_table("review_rule_uses")
    op.drop_column("review_runs", "rule_tracking")
