"""Allow a new run to retry verification while preserving each earlier attempt."""

from alembic import op

revision = "20261018_verification_retries"
down_revision = "20261017_parallel_stages"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_verification_finding_run", "finding_verifications", ["finding_id", "run_id"])
    op.drop_constraint("uq_verification_finding_head", "finding_verifications", type_="unique")


def downgrade():
    # Recreating the older constraint refuses a downgrade if it would require deleting retry history.
    op.create_unique_constraint("uq_verification_finding_head", "finding_verifications", ["finding_id", "head_sha"])
    op.drop_constraint("uq_verification_finding_run", "finding_verifications", type_="unique")
