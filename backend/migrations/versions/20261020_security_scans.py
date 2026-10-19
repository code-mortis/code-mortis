"""Add repository security scans without changing pull-request history."""

import sqlalchemy as sa
from alembic import op

revision = "20261020_security_scans"
down_revision = "20261019_ignored_pulls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "security_scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("connection_id", sa.String(36), sa.ForeignKey("connections.id", ondelete="SET NULL")),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("security_scans.id")),
        sa.Column("request_key", sa.String(100), nullable=False, unique=True),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        *(sa.Column(name, sa.JSON(), nullable=False) for name in ("stages", "progress", "coverage", "report")),
        sa.Column("grade", sa.String(1)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for name in ("repository_id", "connection_id", "status"):
        op.create_index(f"ix_security_scans_{name}", "security_scans", [name])
    op.create_index("uq_security_active_repository", "security_scans", ["repository_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running', 'blocked')"))
    op.create_table(
        "security_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("first_scan_id", sa.String(36), sa.ForeignKey("security_scans.id"), nullable=False),
        sa.Column("last_scan_id", sa.String(36), sa.ForeignKey("security_scans.id"), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rotation", sa.JSON()),
        sa.Column("issue", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "fingerprint"),
    )
    for name in ("repository_id", "state"):
        op.create_index(f"ix_security_findings_{name}", "security_findings", [name])
    op.create_table(
        "security_occurrences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scan_id", sa.String(36), sa.ForeignKey("security_scans.id"), nullable=False),
        sa.Column("finding_id", sa.String(36), sa.ForeignKey("security_findings.id"), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scan_id", "finding_id"),
    )
    op.create_table(
        "security_activities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scan_id", sa.String(36), sa.ForeignKey("security_scans.id")),
        sa.Column("finding_id", sa.String(36), sa.ForeignKey("security_findings.id")),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table in ("security_occurrences", "security_activities"):
        for name in ("scan_id", "finding_id"):
            op.create_index(f"ix_{table}_{name}", table, [name])


def downgrade():
    for table in ("security_activities", "security_occurrences", "security_findings", "security_scans"):
        op.drop_table(table)
