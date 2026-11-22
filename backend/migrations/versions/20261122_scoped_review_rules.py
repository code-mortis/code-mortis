"""Move repository custom review rules into scoped records."""

from datetime import UTC, datetime
from itertools import groupby
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision = "20261122_scoped_review_rules"
down_revision = "20261122_review_rule_usage"
branch_labels = None
depends_on = None

repositories = sa.table("repositories", sa.column("id", sa.BigInteger()), sa.column("settings", sa.JSON()))


def _timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Review rule timestamps must include a timezone.")
    return parsed


def upgrade():
    rules = op.create_table(
        "review_rules",
        sa.Column("rule_id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="CASCADE")),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("(scope = 'repository' AND repository_id IS NOT NULL) OR "
                           "(scope = 'workspace' AND repository_id IS NULL)", name="ck_review_rule_scope"),
        sa.CheckConstraint("position >= 0", name="ck_review_rule_position"),
        sa.UniqueConstraint("repository_id", "position", name="uq_review_rule_repository_position"),
    )
    op.create_index("uq_review_rule_workspace_position", "review_rules", ["position"],
                    unique=True, postgresql_where=sa.text("scope = 'workspace'"))

    connection = op.get_bind()
    for repository_id, settings in connection.execute(sa.select(repositories.c.id, repositories.c.settings)):
        if not isinstance(settings, dict) or "review_rules" not in settings:
            continue
        saved = settings["review_rules"]
        if saved is not None and not isinstance(saved, list):
            raise ValueError(f"Invalid custom review rules for repository {repository_id}.")
        if not saved:
            continue
        for position, definition in enumerate(saved):
            try:
                if not isinstance(definition, dict):
                    raise ValueError("Review rule definition must be an object.")
                values = dict(definition)
                raw_id = values.pop("rule_id", None)
                identity = UUID(raw_id) if raw_id is not None else uuid4()
                if identity.version != 4:
                    raise ValueError("Review rule identity must be a version 4 UUID.")
                created_at = _timestamp(values.pop("created_at", None))
                updated_at = _timestamp(values.pop("updated_at", None))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid custom review rule at position {position} for repository {repository_id}.") from exc
            connection.execute(rules.insert().values(
                rule_id=str(identity), repository_id=repository_id, scope="repository", position=position,
                definition=values, created_at=created_at, updated_at=updated_at,
            ))
        remaining = {key: value for key, value in settings.items() if key != "review_rules"}
        connection.execute(repositories.update().where(repositories.c.id == repository_id).values(settings=remaining))


def downgrade():
    rules = sa.table("review_rules", sa.column("rule_id"), sa.column("scope"), sa.column("repository_id"),
                     sa.column("position"), sa.column("definition", sa.JSON()),
                     sa.column("created_at", sa.DateTime(timezone=True)),
                     sa.column("updated_at", sa.DateTime(timezone=True)))
    connection = op.get_bind()
    if connection.scalar(sa.select(sa.exists().where(rules.c.scope == "workspace"))):
        raise ValueError("Workspace-wide review rules cannot be represented by the previous schema.")
    rows = connection.execute(sa.select(
        rules.c.repository_id, rules.c.rule_id, rules.c.definition, rules.c.created_at, rules.c.updated_at,
        repositories.c.settings,
    ).select_from(rules.join(repositories, rules.c.repository_id == repositories.c.id))
        .order_by(rules.c.repository_id, rules.c.position))
    for repository_id, group in groupby(rows, key=lambda row: row.repository_id):
        entries = list(group)
        saved = []
        for entry in entries:
            record = {**entry.definition, "rule_id": entry.rule_id}
            for key in ("created_at", "updated_at"):
                if timestamp := getattr(entry, key):
                    record[key] = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
            saved.append(record)
        settings = {**entries[0].settings, "review_rules": saved}
        connection.execute(repositories.update().where(repositories.c.id == repository_id).values(settings=settings))
    op.drop_table("review_rules")
