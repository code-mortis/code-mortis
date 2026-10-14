from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .models import Finding, FindingVerification, ReviewRun
from .schemas import LIST_PAGE_SIZE, FindingOut, ResolvedFilters, ResolvedPage


def scope(run: ReviewRun):
    return and_(Finding.pr_id == run.pr_id, Finding.id.in_(select(FindingVerification.finding_id).where(
        FindingVerification.run_id == run.id, FindingVerification.resolution == "resolved",
    )))


def list_resolved(db: Session, run: ReviewRun, filters: ResolvedFilters) -> ResolvedPage:
    resolved = scope(run)
    registered_total = db.scalar(select(func.count()).select_from(Finding).where(resolved))
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
    page = min(filters.page, max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE))
    items = db.scalars(select(Finding).where(*conditions).order_by(
        Finding.created_at.desc(), Finding.id.desc(),
    ).offset((page - 1) * LIST_PAGE_SIZE).limit(LIST_PAGE_SIZE))
    return ResolvedPage(
        items=[FindingOut.model_validate(item) for item in items], total=total,
        registered_total=registered_total, severity_counts=severity_counts, category_counts=category_counts,
        page=page, has_more=page * LIST_PAGE_SIZE < total,
    )
