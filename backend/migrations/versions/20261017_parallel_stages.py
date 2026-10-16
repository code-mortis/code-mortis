"""Separate new review stages without rewriting historical runs."""

import sqlalchemy as sa
from alembic import op

revision = "20261017_parallel_stages"
down_revision = "20261016_pr_summary"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_runs", sa.Column("stages", sa.JSON(), nullable=False, server_default="{}"))
    repositories = sa.table("repositories", sa.column("id", sa.BigInteger()), sa.column("settings", sa.JSON()))
    connection = op.get_bind()
    for identity, settings in connection.execute(sa.select(repositories.c.id, repositories.c.settings)):
        settings = dict(settings or {})
        for name in ("assessment_token_budget", "verification_token_budget"):
            settings.setdefault(name, settings.get("token_budget", 200000))
        settings.setdefault("summary_diagram_enabled", False)
        settings.setdefault("ai_fix_prompts_enabled", False)
        connection.execute(repositories.update().where(repositories.c.id == identity).values(settings=settings))


def downgrade():
    op.drop_column("review_runs", "stages")
