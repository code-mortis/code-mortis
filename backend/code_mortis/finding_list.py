from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Finding
from .schemas import LIST_PAGE_SIZE, FindingFilters, FindingOut, FindingPage, FindingsOut


def list_findings(db: Session, pr_id: str, filters: FindingFilters) -> FindingsOut:
    scope = Finding.pr_id == pr_id
    counts = {status: 0 for status in ["open", "resolved", "dismissed"]}
    counts.update(dict(db.execute(
        select(Finding.status, func.count()).where(scope).group_by(Finding.status)
    ).all()))
    category_counts = dict(db.execute(
        select(Finding.category, func.count()).where(scope).group_by(Finding.category)
    ).all())
    conditions = [scope]
    if filters.status:
        conditions.append(Finding.status.in_(filters.status))
    if filters.severity:
        conditions.append(Finding.severity.in_(filters.severity))
    if filters.category:
        conditions.append(Finding.category.in_(filters.category))
    if query := filters.q.strip():
        conditions.append(or_(*[
            column.icontains(query, autoescape=True)
            for column in [Finding.content, Finding.path, Finding.category]
        ]))
    totals = dict(db.execute(
        select(Finding.status, func.count()).where(*conditions).group_by(Finding.status)
    ).all())
    pages = []
    for status in counts:
        if (filters.status and status not in filters.status) or (filters.group and status != filters.group):
            continue
        total = totals.get(status, 0)
        page = min(filters.page if filters.group else 1, max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE))
        items = db.scalars(select(Finding).where(*conditions, Finding.status == status).order_by(
            Finding.created_at.desc(), Finding.id.desc(),
        ).offset((page - 1) * LIST_PAGE_SIZE).limit(LIST_PAGE_SIZE))
        pages.append(FindingPage(
            group=status, items=[FindingOut.model_validate(item) for item in items],
            total=total, page=page, has_more=page * LIST_PAGE_SIZE < total,
        ))
    return FindingsOut(
        groups=pages, total=sum(totals.values()), registered_total=sum(counts.values()), status_counts=counts,
        category_counts=category_counts,
    )
