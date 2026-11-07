"""Add immutable execution plans and task-level recovery without rewriting history."""

import sqlalchemy as sa
from alembic import op

revision = "20261107_engine_foundation"
down_revision = "20261107_graph_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("review_runs.id", ondelete="CASCADE")),
        sa.Column("scan_id", sa.String(36), sa.ForeignKey("security_scans.id", ondelete="CASCADE")),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "analysis_budgets",
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("analysis_plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stage", sa.String(20), primary_key=True),
        sa.Column("token_limit", sa.BigInteger()),
        sa.Column("charged_tokens", sa.BigInteger(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("charged_tokens >= 0 AND (token_limit IS NULL OR token_limit >= 0)", name="ck_analysis_budget_tokens"),
    )
    op.create_table(
        "analysis_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("analysis_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.UniqueConstraint("plan_id", "key", name="uq_analysis_task_key"),
    )
    op.create_index("ix_analysis_tasks_plan_id", "analysis_tasks", ["plan_id"])
    op.create_table(
        "analysis_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(64), sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("usage_tokens", sa.BigInteger()),
        sa.Column("usage_state", sa.String(20), nullable=False),
        sa.Column("provider_request_id", sa.String(250)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.UniqueConstraint("task_id", "number", name="uq_analysis_attempt_number"),
        sa.CheckConstraint("reserved_tokens >= 0 AND (usage_tokens IS NULL OR usage_tokens >= 0)", name="ck_analysis_attempt_tokens"),
    )
    op.create_index("ix_analysis_attempts_task_id", "analysis_attempts", ["task_id"])
    op.execute("""
        CREATE FUNCTION analysis_plan_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'analysis plans are immutable'; END $$;
        CREATE TRIGGER analysis_plan_immutable BEFORE UPDATE ON analysis_plans
        FOR EACH ROW EXECUTE FUNCTION analysis_plan_immutable();
        CREATE FUNCTION analysis_task_inputs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF jsonb_build_array(NEW.id, NEW.plan_id, NEW.key, NEW.stage, NEW.input_hash, NEW.scope, NEW.dependencies)
                IS DISTINCT FROM jsonb_build_array(OLD.id, OLD.plan_id, OLD.key, OLD.stage, OLD.input_hash, OLD.scope, OLD.dependencies)
            THEN RAISE EXCEPTION 'analysis task inputs are immutable'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER analysis_task_inputs_immutable BEFORE UPDATE ON analysis_tasks
        FOR EACH ROW EXECUTE FUNCTION analysis_task_inputs_immutable();
        CREATE FUNCTION analysis_budget_inputs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.plan_id, NEW.stage, NEW.token_limit, NEW.deadline_at)
                IS DISTINCT FROM (OLD.plan_id, OLD.stage, OLD.token_limit, OLD.deadline_at)
            THEN RAISE EXCEPTION 'analysis budget inputs are immutable'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER analysis_budget_inputs_immutable BEFORE UPDATE ON analysis_budgets
        FOR EACH ROW EXECUTE FUNCTION analysis_budget_inputs_immutable();
    """)


def downgrade():
    op.drop_table("analysis_attempts")
    op.drop_table("analysis_tasks")
    op.drop_table("analysis_budgets")
    op.drop_table("analysis_plans")
    op.execute("DROP FUNCTION analysis_task_inputs_immutable(); DROP FUNCTION analysis_plan_immutable(); "
               "DROP FUNCTION analysis_budget_inputs_immutable();")
