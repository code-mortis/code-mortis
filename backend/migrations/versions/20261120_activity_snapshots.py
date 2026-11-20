"""Version notification completions and bind them to database snapshots."""

from alembic import op

revision = "20261120_activity_snapshots"
down_revision = "20261120_activity_inbox"
branch_labels = None
depends_on = None


def upgrade():
    # Local development databases may have received this metadata during the inbox rollout.
    op.execute("ALTER TABLE review_runs ADD COLUMN IF NOT EXISTS activity_finished_at timestamptz")
    for table in ("jobs", "review_runs", "security_scans"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS activity_xid text")
    op.execute("""
        CREATE OR REPLACE FUNCTION stamp_generation_activity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IN ('done', 'failed', 'cancelled') THEN
                IF TG_OP = 'INSERT' THEN
                    -- A cancellation fence for a request that has not reached enqueue is not an activity item.
                    IF NEW.kind <> 'generate_background' OR NEW.status <> 'cancelled'
                        OR NEW.payload->>'expires_at' IS NOT NULL THEN
                        NEW.finished_at := COALESCE(NEW.finished_at, clock_timestamp());
                        NEW.activity_xid := pg_current_xact_id()::text;
                    END IF;
                ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
                    NEW.finished_at := clock_timestamp();
                    NEW.activity_xid := pg_current_xact_id()::text;
                END IF;
            ELSE
                NEW.finished_at := NULL;
                NEW.activity_xid := NULL;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION stamp_run_activity() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            terminal text[] := ARRAY['completed', 'partial', 'failed', 'cancelled', 'superseded', 'skipped'];
            complete_again boolean := true;
        BEGIN
            IF NEW.status = ANY(terminal) THEN
                IF TG_OP = 'UPDATE' THEN
                    complete_again := NOT OLD.status = ANY(terminal) OR NEW.finished_at IS DISTINCT FROM OLD.finished_at;
                END IF;
                IF complete_again THEN
                    NEW.activity_xid := pg_current_xact_id()::text;
                    IF TG_TABLE_NAME = 'review_runs' THEN
                        -- An assessment retry may settle without changing the original review's finish time.
                        IF TG_OP = 'UPDATE' AND NEW.finished_at IS NOT DISTINCT FROM OLD.finished_at THEN
                            NEW.activity_finished_at := clock_timestamp();
                        ELSE
                            NEW.activity_finished_at := COALESCE(NEW.finished_at, clock_timestamp());
                        END IF;
                    END IF;
                END IF;
            ELSE
                NEW.activity_xid := NULL;
                IF TG_TABLE_NAME = 'review_runs' THEN
                    NEW.activity_finished_at := NULL;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    for table in ("review_runs", "security_scans"):
        op.execute(f"DROP TRIGGER IF EXISTS stamp_run_activity ON {table}")
        op.execute(f"""
            CREATE TRIGGER stamp_run_activity
            BEFORE INSERT OR UPDATE OF status, finished_at ON {table}
            FOR EACH ROW EXECUTE FUNCTION stamp_run_activity()
        """)


def downgrade():
    for table in ("review_runs", "security_scans"):
        op.execute(f"DROP TRIGGER stamp_run_activity ON {table}")
    op.execute("DROP FUNCTION stamp_run_activity()")
    op.execute("""
        CREATE OR REPLACE FUNCTION stamp_generation_activity() RETURNS trigger LANGUAGE plpgsql AS $$
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
    for table in ("jobs", "review_runs", "security_scans"):
        op.drop_column(table, "activity_xid")
    op.drop_column("review_runs", "activity_finished_at")
