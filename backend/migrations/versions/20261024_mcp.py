"""Authenticated MCP access, consent and durable command provenance."""

import sqlalchemy as sa
from alembic import op

revision = "20261024_mcp"
down_revision = "20261023_assessment_conflicts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_config", sa.Column("mcp_settings", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table('mcp_clients',
        sa.Column('client_id', sa.String(length=2048), primary_key=True, nullable=False),
        sa.Column('client_name', sa.String(length=100), nullable=False),
        sa.Column('redirect_uris', sa.JSON(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata_managed', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_table('mcp_grants',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('client_id', sa.String(length=2048), sa.ForeignKey('mcp_clients.client_id'), nullable=False),
        sa.Column('domain', sa.String(length=20), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('repository_ids', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_mcp_grants_client_id', 'mcp_grants', ['client_id'])
    op.create_index('ix_mcp_grants_user_id', 'mcp_grants', ['user_id'])
    op.create_table('mcp_authorizations',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('client_id', sa.String(length=2048), sa.ForeignKey('mcp_clients.client_id'), nullable=False),
        sa.Column('domain', sa.String(length=20), nullable=False),
        sa.Column('redirect_uri', sa.Text(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('browser_hash', sa.String(length=64), nullable=False),
        sa.Column('session_hash', sa.String(length=64), nullable=True),
        sa.Column('code_challenge', sa.String(length=128), nullable=False),
        sa.Column('code_challenge_method', sa.String(length=10), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('code_hash', sa.String(length=64), unique=True, nullable=True),
        sa.Column('grant_id', sa.String(length=36), sa.ForeignKey('mcp_grants.id'), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_mcp_authorizations_expires_at', 'mcp_authorizations', ['expires_at'])
    op.create_table('mcp_tokens',
        sa.Column('access_hash', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('refresh_hash', sa.String(length=64), unique=True, nullable=False),
        sa.Column('grant_id', sa.String(length=36), sa.ForeignKey('mcp_grants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refresh_used_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_mcp_tokens_expires_at', 'mcp_tokens', ['expires_at'])
    op.create_index('ix_mcp_tokens_grant_id', 'mcp_tokens', ['grant_id'])
    op.create_table('mcp_operations',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('grant_id', sa.String(length=36), sa.ForeignKey('mcp_grants.id'), nullable=False),
        sa.Column('tool', sa.String(length=100), nullable=False),
        sa.Column('request_key', sa.String(length=100), nullable=False),
        sa.Column('arguments_hash', sa.String(length=64), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('grant_id', 'tool', 'request_key'),
    )
    op.create_index('ix_mcp_operations_grant_id', 'mcp_operations', ['grant_id'])
    op.create_table('mcp_approvals',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('grant_id', sa.String(length=36), sa.ForeignKey('mcp_grants.id'), nullable=False),
        sa.Column('tool', sa.String(length=100), nullable=False),
        sa.Column('arguments_hash', sa.String(length=64), nullable=False),
        sa.Column('arguments', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('denied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_mcp_approvals_grant_id', 'mcp_approvals', ['grant_id'])
    op.create_table('mcp_audit',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('grant_id', sa.String(length=36), sa.ForeignKey('mcp_grants.id'), nullable=False),
        sa.Column('tool', sa.String(length=100), nullable=False),
        sa.Column('outcome', sa.String(length=30), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_mcp_audit_grant_id', 'mcp_audit', ['grant_id'])


def downgrade():
    op.drop_table('mcp_audit')
    op.drop_table('mcp_approvals')
    op.drop_table('mcp_operations')
    op.drop_table('mcp_tokens')
    op.drop_table('mcp_authorizations')
    op.drop_table('mcp_grants')
    op.drop_table('mcp_clients')
    op.drop_column("app_config", "mcp_settings")
