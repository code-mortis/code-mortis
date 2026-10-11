from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .models import ReviewCandidate
from .schemas import LIST_PAGE_SIZE, CandidateFilters, CandidateOut, CandidatePage


def list_candidates(db: Session, run_id: str, filters: CandidateFilters) -> CandidatePage:
    scope = ReviewCandidate.run_id == run_id
    outcome = case(
        (ReviewCandidate.finding_id.is_not(None) & ReviewCandidate.is_new, "new"),
        (ReviewCandidate.finding_id.is_not(None), "repeated"),
        (ReviewCandidate.result["verdict"].as_string() == "rejected", "rejected"),
        (ReviewCandidate.status.in_(["queued", "running"]), "pending"),
        else_="withheld",
    )
    counts = dict.fromkeys(["new", "repeated", "rejected", "withheld", "pending"], 0)
    counts.update(dict(db.execute(select(outcome, func.count()).where(scope).group_by(outcome)).all()))
    severity = ReviewCandidate.snapshot["severity"].as_string()
    severity_counts = dict.fromkeys(["critical", "high", "medium", "low"], 0)
    severity_counts.update({key: count for key, count in db.execute(
        select(severity, func.count()).where(scope).group_by(severity)
    ) if key in severity_counts})
    conditions = [scope]
    if filters.outcome:
        conditions.append(outcome.in_(filters.outcome))
    if filters.severity:
        conditions.append(severity.in_(filters.severity))
    if query := filters.q.strip():
        conditions.append(or_(*[
            ReviewCandidate.snapshot[key].as_string().icontains(query, autoescape=True)
            for key in ["content", "path", "category"]
        ]))
    total = db.scalar(select(func.count()).select_from(ReviewCandidate).where(*conditions))
    page = min(filters.page, max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE))
    items = db.scalars(select(ReviewCandidate).where(*conditions).order_by(
        ReviewCandidate.position, ReviewCandidate.id,
    ).offset((page - 1) * LIST_PAGE_SIZE).limit(LIST_PAGE_SIZE))
    return CandidatePage(
        items=[CandidateOut.model_validate(item) for item in items], total=total,
        registered_total=sum(counts.values()), outcome_counts=counts, severity_counts=severity_counts,
        page=page, has_more=page * LIST_PAGE_SIZE < total,
    )
