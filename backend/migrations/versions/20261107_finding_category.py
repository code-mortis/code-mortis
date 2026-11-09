"""Preserve the public 50-character finding category contract in existing databases."""

import sqlalchemy as sa
from alembic import op

revision = "20261107_finding_category"
down_revision = "20261107_inference_admission"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("findings", "category", existing_type=sa.String(30), type_=sa.String(50),
                    existing_nullable=False)


def downgrade():
    op.alter_column("findings", "category", existing_type=sa.String(50), type_=sa.String(30),
                    existing_nullable=False)
