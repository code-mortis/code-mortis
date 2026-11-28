"""Add scoped repository selections and retire the legacy MCP grants."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "20261127_unified_mcp"
down_revision = "20261125_publication_batch"
branch_labels = None
depends_on = None


def retire_grants(is_legacy):
    from code_mortis.core.db import now
    from code_mortis.mcp.models import McpGrant
    from code_mortis.mcp.provenance import revoke_work

    domain_filter = McpGrant.domain != "unified" if is_legacy else McpGrant.domain == "unified"
    bind = op.get_bind()
    with Session(bind=bind, join_transaction_mode="rollback_only") as db:
        grants = db.scalars(sa.select(McpGrant).where(
            domain_filter, McpGrant.revoked_at.is_(None),
        ).order_by(McpGrant.id).with_for_update()).all()
        for grant in grants:
            grant.revoked_at = now()
            revoke_work(db, grant.id)
        db.commit()


def upgrade():
    op.add_column("mcp_grants", sa.Column("repository_ids_by_group", sa.JSON(), nullable=False, server_default="{}"))
    op.execute("""
        UPDATE app_config
        SET mcp_settings = jsonb_set(mcp_settings::jsonb, '{enabled}', 'false')::json
        WHERE mcp_settings::jsonb <> '{}'::jsonb
          AND mcp_settings->>'enabled' IS DISTINCT FROM 'false'
    """)
    op.execute("""
        UPDATE mcp_authorizations SET expires_at = LEAST(expires_at, now())
        WHERE domain != 'unified'
    """)
    retire_grants(is_legacy=True)


def downgrade():
    retire_grants(is_legacy=False)
    op.drop_column("mcp_grants", "repository_ids_by_group")
