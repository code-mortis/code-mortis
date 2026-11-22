from collections import defaultdict
from datetime import UTC

from sqlalchemy import or_, select

from code_mortis.models import CustomReviewRule
from code_mortis.schemas.common import StoredReviewRule


def record(row):
    return StoredReviewRule.model_validate({
        **row.definition, "rule_id": row.rule_id,
        "created_at": row.created_at.astimezone(UTC) if row.created_at is not None else None,
        "updated_at": row.updated_at.astimezone(UTC) if row.updated_at is not None else None,
    })


def repository_rows(db, repository_ids):
    if not repository_ids:
        return []
    return db.scalars(select(CustomReviewRule).where(
        CustomReviewRule.scope == "repository", CustomReviewRule.repository_id.in_(repository_ids),
    ).order_by(CustomReviewRule.repository_id, CustomReviewRule.position)).all()


def by_repository(db, repository_ids):
    grouped = defaultdict(list)
    for row in repository_rows(db, repository_ids):
        grouped[row.repository_id].append(record(row))
    return grouped


def effective(db, repository_id, *, repository_draft=None):
    workspace = CustomReviewRule.scope == "workspace"
    scope = or_(workspace, CustomReviewRule.repository_id == repository_id) if repository_draft is None else workspace
    rows = db.scalars(select(CustomReviewRule).where(scope).order_by(
        CustomReviewRule.repository_id.asc().nulls_first(), CustomReviewRule.position,
    )).all()
    records = [record(row) for row in rows]
    if repository_draft is not None:
        return [*records, *repository_draft], len(records)
    return records, sum(row.scope == "workspace" for row in rows)
