from sqlalchemy import String, case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from code_mortis.core.ordering import list_order
from code_mortis.models import Finding, FindingVerification, Occurrence, ReviewCandidate, ReviewRun
from code_mortis.schemas.common import OccurrenceOut, RunFindingFilters, RunFindingPage


def scope(run: ReviewRun, status: str):
    query = select(Occurrence).join(Finding, Finding.id == Occurrence.finding_id).where(Finding.pr_id == run.pr_id)
    if status == "resolved":
        return query.join(FindingVerification, FindingVerification.occurrence_id == Occurrence.id).where(
            FindingVerification.run_id == run.id, FindingVerification.resolution == "resolved",
        )
    return query.where(Occurrence.run_id == run.id)


def metadata(db: Session, run: ReviewRun):
    counts, revisions = {}, []
    for status in ("open", "resolved"):
        rows = scope(run, status).subquery()
        stamp = cast(func.json_build_array(rows.c.id, rows.c.snapshot), String)
        count, revision = db.execute(select(func.count(), func.md5(func.coalesce(
            func.string_agg(stamp, aggregate_order_by(literal(","), rows.c.id)), ""))).select_from(rows)).one()
        counts[status] = count
        revisions.append(revision)
    return counts, f'{run.progress.get("assessment_revision", 0)}:' + ";".join(revisions)


def list_findings(db: Session, run: ReviewRun, filters: RunFindingFilters) -> RunFindingPage:
    counts, revision = metadata(db, run)
    candidates = select(ReviewCandidate.id).where(
        ReviewCandidate.run_id == run.id, ReviewCandidate.finding_id == Occurrence.finding_id,
    ).correlate(Occurrence)
    is_new = case((candidates.where(ReviewCandidate.is_new.is_(True)).exists(), True),
                  (candidates.exists(), False), else_=None)
    outcome = case((is_new.is_(True), "new"), (is_new.is_(False), "repeated"), else_="recorded")
    query = scope(run, filters.status)
    severity, category = (func.coalesce(Occurrence.snapshot[key].as_string(), "unknown") for key in ("severity", "category"))
    severity_counts = dict(db.execute(query.with_only_columns(severity, func.count()).group_by(severity)).all())
    category_counts = dict(db.execute(query.with_only_columns(category, func.count()).group_by(category)).all())
    outcome_counts = dict(db.execute(query.with_only_columns(outcome, func.count()).group_by(outcome)).all())
    if filters.outcome:
        query = query.where(outcome.in_([
            "repeated" if value in {"open_repeat", "resolved_repeat", "dismissed_repeat"} else value
            for value in filters.outcome
        ]))
    if filters.severity:
        query = query.where(severity.in_(filters.severity))
    if filters.category:
        query = query.where(category.in_(filters.category))
    if term := filters.q.strip():
        query = query.where(Occurrence.snapshot["content"].as_string().icontains(term, autoescape=True)
            | Occurrence.snapshot["path"].as_string().icontains(term, autoescape=True)
            | category.icontains(term, autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    size = filters.page_size
    page = min(filters.page, max(1, (total + size - 1) // size))
    rows = db.execute(query.add_columns(is_new).order_by(*list_order(filters.ordering, {
        "created_at": FindingVerification.created_at if filters.status == "resolved" else Finding.created_at,
        "updated_at": FindingVerification.finished_at if filters.status == "resolved" else Finding.updated_at,
        "severity": severity, "path": Occurrence.snapshot["path"].as_string(),
    }, Occurrence.id)).offset((page - 1) * size).limit(size))
    return RunFindingPage(
        items=[OccurrenceOut.model_validate(item).model_copy(update={"is_new": new}) for item, new in rows],
        total=total, registered_total=counts[filters.status], severity_counts=severity_counts,
        category_counts=category_counts, outcome_counts=outcome_counts, revision=revision,
        page=page, page_size=size, has_more=page * size < total,
    )
