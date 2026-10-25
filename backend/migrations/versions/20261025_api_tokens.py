"""Personal API tokens and issuance freshness."""

import sqlalchemy as sa
from alembic import op

revision = "20261025_api_tokens"
down_revision = "20261024_mcp"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("login_sessions", sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "personal_api_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), unique=True, nullable=False),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("repository_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_personal_api_tokens_user_id", "personal_api_tokens", ["user_id"])
    op.create_index("ix_personal_api_tokens_token_hash", "personal_api_tokens", ["token_hash"])
    op.create_table(
        "api_token_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("token_id", sa.String(length=36), sa.ForeignKey("personal_api_tokens.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_token_events_token_id", "api_token_events", ["token_id"])


def downgrade():
    op.drop_index("ix_api_token_events_token_id", table_name="api_token_events")
    op.drop_table("api_token_events")
    op.drop_index("ix_personal_api_tokens_token_hash", table_name="personal_api_tokens")
    op.drop_index("ix_personal_api_tokens_user_id", table_name="personal_api_tokens")
    op.drop_table("personal_api_tokens")
    op.drop_column("login_sessions", "authenticated_at")
