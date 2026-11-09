"""Select Mortis for future work; preserve every saved execution and result."""

from alembic import op

revision = "20261108_mortis_only"
down_revision = "20261107_finding_category"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE repositories
        SET settings = jsonb_set(settings::jsonb, '{analysis_engine}', '"mortis"'::jsonb)::json
        WHERE jsonb_typeof(settings::jsonb) = 'object'
          AND (settings->>'analysis_engine' IS NULL OR settings->>'analysis_engine' = 'ocr')
    """)


def downgrade():
    # Mutable selections are never silently switched back to a retired engine.
    pass
