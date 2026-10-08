from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from .models import Finding, PullRequest, Repository, ReviewRun
from .schemas import InboxFilters, InboxOut, InboxPage, InboxPullOut, InboxRepository

PAGE_SIZE = 20


def list_pulls(db: Session, repositories: list[Repository], filters: InboxFilters) -> InboxOut:
    permitted = [repo.id for repo in repositories]
    current = and_(ReviewRun.head_sha == PullRequest.head_sha, ReviewRun.base_sha == PullRequest.base_sha)
    runs = select(
        ReviewRun.pr_id, ReviewRun.id.label("run_id"), ReviewRun.status,
        ReviewRun.publication, ReviewRun.publication_error, current.label("current"),
        func.row_number().over(
            partition_by=ReviewRun.pr_id,
            order_by=(
                case((current, 0), else_=1),
                case((ReviewRun.status.in_(["running", "queued", "blocked"]), 0), else_=1),
                ReviewRun.created_at.desc(), ReviewRun.id.desc(),
            ),
        ).label("rank"),
    ).join(PullRequest, PullRequest.id == ReviewRun.pr_id).where(
        PullRequest.repository_id.in_(permitted)
    ).cte("inbox_runs")
    findings = select(Finding.pr_id, func.count().label("open_findings")).join(
        PullRequest, PullRequest.id == Finding.pr_id
    ).where(PullRequest.repository_id.in_(permitted), Finding.status == "open").group_by(
        Finding.pr_id
    ).cte("inbox_findings")
    review_status = case(
        (runs.c.run_id.is_(None), "not_reviewed"),
        (runs.c.current.is_(False), "outdated"),
        else_=runs.c.status,
    )
    group = case(
        (PullRequest.state == "merged", "merged"),
        (PullRequest.state == "closed", "closed"),
        (review_status == "completed", "reviewed"),
        else_="active",
    )
    base = select(
        PullRequest.id, PullRequest.repository_id, Repository.full_name.label("repository_name"),
        PullRequest.number, PullRequest.title, PullRequest.author, PullRequest.state,
        PullRequest.draft, PullRequest.created_at, PullRequest.updated_at, review_status.label("review_status"),
        runs.c.run_id, runs.c.publication, runs.c.publication_error,
        func.coalesce(findings.c.open_findings, 0).label("open_findings"), group.label("group"),
    ).join(Repository, Repository.id == PullRequest.repository_id).outerjoin(
        runs, and_(runs.c.pr_id == PullRequest.id, runs.c.rank == 1)
    ).outerjoin(findings, findings.c.pr_id == PullRequest.id).where(
        PullRequest.repository_id.in_(permitted)
    ).cte("inbox_base")
    conditions = []
    if filters.repo:
        conditions.append(base.c.repository_id.in_(filters.repo))
    if filters.review:
        conditions.append(base.c.review_status.in_(filters.review))
    if filters.author:
        conditions.append(func.lower(base.c.author).in_([value.lower() for value in filters.author]))
    if filters.findings:
        conditions.append(base.c.open_findings > 0 if filters.findings == "open" else base.c.open_findings == 0)
    if query := filters.q.strip():
        conditions.append(or_(
            base.c.title.icontains(query, autoescape=True),
            base.c.repository_name.icontains(query, autoescape=True),
            base.c.author.icontains(query.lstrip("@"), autoescape=True),
            cast(base.c.number, String) == query.lstrip("#"),
        ))
    state_counts = {state: 0 for state in ["open", "merged", "closed"]}
    state_counts.update(dict(db.execute(
        select(base.c.state, func.count()).where(*conditions).group_by(base.c.state)
    ).all()))
    if "all" not in filters.state:
        conditions.append(base.c.state.in_(filters.state))
    filtered = select(base).where(*conditions).cte("inbox_filtered")
    totals = dict(db.execute(select(filtered.c.group, func.count()).group_by(filtered.c.group)).all())
    states = {"open", "merged", "closed"} if "all" in filters.state else set(filters.state)
    enabled = [name for name, state in [
        ("active", "open"), ("reviewed", "open"), ("merged", "merged"), ("closed", "closed")
    ] if state in states]
    sort_column = {
        "created_at": filtered.c.created_at, "updated_at": filtered.c.updated_at,
        "title": func.lower(filtered.c.title), "repository": func.lower(filtered.c.repository_name),
        "author": func.lower(filtered.c.author),
    }[filters.ordering.removeprefix("-")]
    order = sort_column.desc() if filters.ordering.startswith("-") else sort_column.asc()
    pages = []
    for name in enabled:
        if filters.group and name != filters.group:
            continue
        total = totals.get(name, 0)
        page = min(filters.page if filters.group else 1, max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
        items = db.execute(select(filtered).where(filtered.c.group == name).order_by(
            case((and_(filtered.c.group == "active", filtered.c.review_status == "running"), 0),
                 (and_(filtered.c.group == "active", filtered.c.review_status == "queued"), 1), else_=2),
            order.nulls_last(), filtered.c.id.desc(),
        ).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).mappings()
        pages.append(InboxPage(
            group=name, items=[InboxPullOut.model_validate(row) for row in items],
            total=total, page=page, has_more=page * PAGE_SIZE < total,
        ))
    registered = select(PullRequest).where(PullRequest.repository_id.in_(permitted)).subquery()
    return InboxOut(
        groups=pages, total=sum(totals.values()),
        repository_count=db.scalar(select(func.count(func.distinct(filtered.c.repository_id)))),
        registered_total=db.scalar(select(func.count()).select_from(registered)),
        repositories=[InboxRepository(id=repo.id, full_name=repo.full_name) for repo in repositories],
        authors=list(db.scalars(select(registered.c.author).distinct().order_by(registered.c.author))),
        state_counts=state_counts,
    )
