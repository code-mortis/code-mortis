"""Track notification receipts and generation completion timestamps."""

import sqlalchemy as sa
from alembic import op

revision = "20261120_activity_inbox"
down_revision = "20261118_repository_scan_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column(
        "activity_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
    ))
    op.create_table(
        "activity_reads",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("item_id", sa.String(36), primary_key=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), primary_key=True),
    )
    op.add_column("jobs", sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("ix_jobs_activity", "jobs", ["kind", "status", "finished_at"], postgresql_where=sa.text(
        "kind IN ('generate_background', 'generate_summary')",
    ))
    op.create_index("ix_review_runs_finished_at", "review_runs", ["finished_at"])
    op.create_index("ix_security_scans_finished_at", "security_scans", ["finished_at"])
    op.execute("""
        CREATE FUNCTION stamp_generation_activity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IN ('done', 'failed', 'cancelled') THEN
                IF TG_OP = 'INSERT' THEN
                    -- A cancellation fence for a request that has not reached enqueue is not an activity item.
                    IF NEW.kind <> 'generate_background' OR NEW.status <> 'cancelled'
                        OR NEW.payload->>'expires_at' IS NOT NULL THEN
                        NEW.finished_at := COALESCE(NEW.finished_at, clock_timestamp());
                    END IF;
                ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
                    NEW.finished_at := clock_timestamp();
                END IF;
            ELSE
                NEW.finished_at := NULL;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER stamp_generation_activity
        BEFORE INSERT OR UPDATE OF status ON jobs
        FOR EACH ROW WHEN (NEW.kind IN ('generate_background', 'generate_summary'))
        EXECUTE FUNCTION stamp_generation_activity()
    """)

def downgrade():
    op.execute("DROP TRIGGER stamp_generation_activity ON jobs")
    op.execute("DROP FUNCTION stamp_generation_activity()")
    op.drop_index("ix_security_scans_finished_at", table_name="security_scans")
    op.drop_index("ix_review_runs_finished_at", table_name="review_runs")
    op.drop_index("ix_jobs_activity", table_name="jobs")
    op.drop_column("jobs", "finished_at")
    op.drop_table("activity_reads")
    op.drop_column("users", "activity_started_at")
