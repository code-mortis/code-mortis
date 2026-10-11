"""Retain GitHub account identity and archive removed installation entries."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_installation_accounts"
down_revision = "20261009_candidate_assessment"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("installations", sa.Column("account_id", sa.BigInteger()))
    op.add_column("installations", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Uninstall jobs retain the numeric account identity verified before removal.
    op.execute("""
        UPDATE installations SET account_id = (
            SELECT CASE
                WHEN json_typeof(payload -> 'account_id') = 'number'
                    AND payload ->> 'account_id' ~ '^[1-9][0-9]{0,17}$'
                THEN (payload ->> 'account_id')::bigint
            END
            FROM jobs
            WHERE kind = 'uninstall_installation'
                AND payload ->> 'installation_id' = installations.id::text
            LIMIT 1
        )
    """)


def downgrade():
    op.drop_column("installations", "archived")
    op.drop_column("installations", "account_id")
