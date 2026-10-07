"""Separate authentication transactions and add personal review connections.

Revision ID: 20261006_connections
Revises: 8397bfc751ae
"""

import sqlalchemy as sa
from alembic import op

revision = "20261006_connections"
down_revision = "8397bfc751ae"
branch_labels = None
depends_on = None


def upgrade():
    for name, type_, default in [
        ("browser_hash", sa.String(64), ""),
        ("verifier", sa.Text(), ""),
        ("return_path", sa.Text(), "/"),
    ]:
        op.add_column("oauth_states", sa.Column(name, type_, nullable=False, server_default=default))
    # Old attempts have no browser binding. They cannot be upgraded securely.
    op.execute("DELETE FROM oauth_states")
    op.add_column("connections", sa.Column("kind", sa.String(20), nullable=False, server_default="api"))
    op.add_column("connections", sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("users.id")))
    op.add_column(
        "connections", sa.Column("auth_status", sa.String(30), nullable=False, server_default="disconnected")
    )
    op.add_column("connections", sa.Column("auth_message", sa.Text()))
    op.create_table(
        "connection_grants",
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "repository_id",
            sa.BigInteger(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("automatic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "console_tickets",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_hash",
            sa.String(64),
            sa.ForeignKey("login_sessions.token_hash", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("review_runs", sa.Column("engine", sa.String(30), nullable=False, server_default="ocr"))
    op.add_column(
        "review_runs",
        sa.Column("connection_id", sa.String(36), sa.ForeignKey("connections.id", ondelete="SET NULL")),
    )
    op.create_index("ix_review_runs_connection_id", "review_runs", ["connection_id"])
    op.add_column("review_runs", sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("users.id")))
    op.add_column("review_runs", sa.Column("progress", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("review_runs", sa.Column("blocked_reason", sa.Text()))
    op.execute("""UPDATE review_runs SET connection_id = connections.id
        FROM connections WHERE review_runs.config->'connection'->>'id' = connections.id""")


def downgrade():
    for name in ["blocked_reason", "progress", "owner_id", "connection_id", "engine"]:
        op.drop_column("review_runs", name)
    op.drop_table("console_tickets")
    op.drop_table("connection_grants")
    for name in ["auth_message", "auth_status", "owner_id", "kind"]:
        op.drop_column("connections", name)
    for name in ["return_path", "verifier", "browser_hash"]:
        op.drop_column("oauth_states", name)
