"""Remember confirmed uninstalls without archiving installation history."""

import sqlalchemy as sa
from alembic import op

revision = "20261022_installation_removal"
down_revision = "20261021_report_grades"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("installations", sa.Column("uninstalled", sa.Boolean(), nullable=False, server_default=sa.false()))
    # A completed uninstall has confirmed absence; inactive alone can mean suspended.
    op.execute("""
        UPDATE installations SET uninstalled = true
        WHERE active = false AND EXISTS (
            SELECT 1 FROM jobs
            WHERE kind = 'uninstall_installation' AND status = 'done'
                AND json_typeof(payload -> 'installation_id') = 'number'
                AND payload ->> 'installation_id' = installations.id::text
        )
    """)


def downgrade():
    op.drop_column("installations", "uninstalled")
