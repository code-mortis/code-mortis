from sqlalchemy import String, and_, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from .models import Finding, FindingVerification, ReviewRun
from .ordering import list_order
from .schemas import FindingOut, ResolvedFilters, ResolvedPage


def scope(run: ReviewRun):
    return and_(Finding.pr_id == run.pr_id, Finding.id.in_(select(FindingVerification.finding_id).where(
        FindingVerification.run_id == run.id, FindingVerification.resolution == "resolved",
    )))


def metadata(db: Session, run: ReviewRun):
    stamp = cast(func.json_build_array(Finding.id, Finding.status, Finding.sync_version, Finding.updated_at), String)
    return db.execute(select(
        func.count(), func.md5(func.coalesce(func.string_agg(stamp, aggregate_order_by(literal(","), Finding.id)), "")),
    ).select_from(Finding).where(scope(run))).one()


def list_resolved(db: Session, run: ReviewRun, filters: ResolvedFilters) -> ResolvedPage:
    resolved = scope(run)
    registered_total, revision = metadata(db, run)
    severity_counts = dict.fromkeys(["critical", "high", "medium", "low"], 0)
    severity_counts.update(dict(db.execute(
        select(Finding.severity, func.count()).where(resolved).group_by(Finding.severity)
    ).all()))
    category_counts = dict(db.execute(
        select(Finding.category, func.count()).where(resolved).group_by(Finding.category)
    ).all())
    conditions = [resolved]
    if filters.severity:
        conditions.append(Finding.severity.in_(filters.severity))
    if filters.category:
        conditions.append(Finding.category.in_(filters.category))
    if query := filters.q.strip():
        conditions.append(or_(*[
            column.icontains(query, autoescape=True)
            for column in [Finding.content, Finding.path, Finding.category]
        ]))
    total = db.scalar(select(func.count()).select_from(Finding).where(*conditions))
    size = filters.page_size
    page = min(filters.page, max(1, (total + size - 1) // size))
    items = db.scalars(select(Finding).where(*conditions).order_by(
        *list_order(filters.ordering, {"created_at": Finding.created_at, "updated_at": Finding.updated_at,
                "severity": Finding.severity, "path": Finding.path}, Finding.id.desc()),
    ).offset((page - 1) * size).limit(size))
    return ResolvedPage(
        items=[FindingOut.model_validate(item) for item in items], total=total,
        registered_total=registered_total, severity_counts=severity_counts, category_counts=category_counts,
        revision=revision, page=page, page_size=size, has_more=page * size < total,
    )
