from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from code_mortis.core.ordering import list_order
from code_mortis.models import ReviewCandidate, ReviewRun
from code_mortis.schemas.common import CandidateFilters, CandidateOut, CandidatePage


def list_candidates(db: Session, run: ReviewRun, filters: CandidateFilters) -> CandidatePage:
    revision = run.progress.get("assessment_revision", 0)
    scope = ReviewCandidate.run_id == run.id
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
    category = ReviewCandidate.snapshot["category"].as_string()
    category_counts = {key: count for key, count in db.execute(
        select(category, func.count()).where(scope).group_by(category)
    ) if key}
    conditions = [scope]
    if filters.outcome:
        conditions.append(outcome.in_(filters.outcome))
    if filters.severity:
        conditions.append(severity.in_(filters.severity))
    if filters.category:
        conditions.append(category.in_(filters.category))
    if query := filters.q.strip():
        conditions.append(or_(*[
            ReviewCandidate.snapshot[key].as_string().icontains(query, autoescape=True)
            for key in ["content", "path", "category"]
        ]))
    total = db.scalar(select(func.count()).select_from(ReviewCandidate).where(*conditions))
    size = filters.page_size
    page = min(filters.page, max(1, (total + size - 1) // size))
    items = db.scalars(select(ReviewCandidate).where(*conditions).order_by(
        *list_order(filters.ordering, {"position": ReviewCandidate.position, "created_at": ReviewCandidate.created_at,
            "finished_at": ReviewCandidate.finished_at, "severity": severity, "path": ReviewCandidate.snapshot["path"].as_string()},
            ReviewCandidate.position, ReviewCandidate.id),
    ).offset((page - 1) * size).limit(size))
    return CandidatePage(
        items=[CandidateOut.model_validate(item) for item in items], total=total,
        registered_total=sum(counts.values()), outcome_counts=counts, severity_counts=severity_counts,
        category_counts=category_counts,
        revision=revision, page=page, page_size=size, has_more=page * size < total,
    )
