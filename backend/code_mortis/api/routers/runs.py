from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from code_mortis.api.dependencies import DB, USER, pr_access, review_pr_access
from code_mortis.api.presenters import pr_out, repo_out
from code_mortis.api_tokens import provenance as pat_provenance
from code_mortis.core.analysis import require_execution_engine
from code_mortis.core.ordering import RunFindingOrdering, list_order
from code_mortis.core.shared_execution import RETIRED_MESSAGE
from code_mortis.integrations import github
from code_mortis.models import (
    Connection,
    Finding,
    FindingVerification,
    Occurrence,
    PullRequest,
    ReviewCandidate,
    ReviewRun,
)
from code_mortis.reviews import analytics, assessment, pipeline, run_findings
from code_mortis.reviews import candidates as candidate_list
from code_mortis.reviews import resolved as resolved_list
from code_mortis.reviews import summary as pr_summary
from code_mortis.reviews.service import can_publish, cancel_review, enqueue
from code_mortis.schemas.common import (
    CandidateDetail,
    CandidateFilters,
    CandidateOut,
    CandidatePage,
    OccurrenceOut,
    ResolvedFilters,
    ResolvedPage,
    RunDetail,
    RunFindingFilters,
    RunFindingPage,
    RunOut,
)

router = APIRouter()


@router.get("/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: str, db: DB, user: USER, ordering: RunFindingOrdering = "-severity"):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr, repo, remote = pr_access(db, user, run.pr_id)
    output = RunOut.model_validate(run)
    if (stage := output.stages.get("verification")) and stage.status == "skipped" and not stage.error:
        stage.error = db.scalar(select(FindingVerification.error).where(
            FindingVerification.run_id == run.id, FindingVerification.status == "skipped",
            FindingVerification.error.is_not(None), FindingVerification.error != "",
        ).order_by(FindingVerification.created_at, FindingVerification.id).limit(1))
    resolved_count, resolved_revision = resolved_list.metadata(db, run)
    finding_state_counts, findings_revision = run_findings.metadata(db, run)
    new_findings = dict(db.execute(select(
        ReviewCandidate.finding_id, func.bool_or(ReviewCandidate.is_new),
    ).where(ReviewCandidate.run_id == run.id, ReviewCandidate.finding_id.is_not(None))
        .group_by(ReviewCandidate.finding_id)).all())
    return RunDetail(
        run=output,
        total_tokens=analytics.run_tokens(db, run),
        pull_request=pr_out(db, pr),
        repository=repo_out(db, repo),
        assessment=assessment.summary(db, run),
        occurrences=[
            OccurrenceOut.model_validate(item).model_copy(update={
                "is_new": new_findings.get(item.finding_id), "current_status": status,
            })
            for item, status in db.execute(select(Occurrence, Finding.status)
                .join(Finding, Finding.id == Occurrence.finding_id).where(Occurrence.run_id == run.id)
                .order_by(*list_order(ordering, {"severity": Occurrence.snapshot["severity"].as_string(),
                    "path": Occurrence.snapshot["path"].as_string()}, Occurrence.id)))
        ],
        resolved_count=resolved_count, resolved_revision=resolved_revision,
        finding_state_counts=finding_state_counts, findings_revision=findings_revision,
        assessment_revision=run.progress.get("assessment_revision", 0),
        can_write=remote.get("permissions", {}).get("push", False),
    )


@router.get("/runs/{run_id}/findings", response_model=RunFindingPage)
def run_saved_findings(run_id: str, db: DB, user: USER, filters: Annotated[RunFindingFilters, Query()]):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id)
    return run_findings.list_findings(db, run, filters)


@router.get("/runs/{run_id}/findings/{finding_id}", response_model=OccurrenceOut)
def run_saved_finding(run_id: str, finding_id: str, db: DB, user: USER,
    status: Literal["open", "resolved"] = "open",
):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id)
    item = db.scalar(run_findings.scope(run, status).where(Occurrence.finding_id == finding_id))
    if not item:
        raise HTTPException(404, "Finding not recorded in this run.")
    return OccurrenceOut.model_validate(item)


@router.get("/runs/{run_id}/resolved", response_model=ResolvedPage)
def run_resolved(run_id: str, db: DB, user: USER, filters: Annotated[ResolvedFilters, Query()]):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id)
    return resolved_list.list_resolved(db, run, filters)


@router.get("/runs/{run_id}/candidates", response_model=CandidatePage)
def run_candidates(run_id: str, db: DB, user: USER, filters: Annotated[CandidateFilters, Query()]):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id)
    return candidate_list.list_candidates(db, run, filters)


@router.get("/runs/{run_id}/candidates/{candidate_id}", response_model=CandidateDetail)
def candidate_detail(run_id: str, candidate_id: str, db: DB, user: USER):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id)
    revision = run.progress.get("assessment_revision", 0)
    candidate = db.get(ReviewCandidate, candidate_id)
    if not candidate or candidate.run_id != run.id:
        raise HTTPException(404, "Assessment candidate not found.")
    return CandidateDetail.model_validate(candidate).model_copy(update={"revision": revision})


@router.post("/runs/{run_id}/candidates/{candidate_id}/retry", response_model=CandidateOut, status_code=202)
def retry_assessment(run_id: str, candidate_id: str, db: DB, user: USER):
    from code_mortis.reviews.service import ACTIVE, authorize_connection, execution_identity

    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr, repo, _ = review_pr_access(db, user, run.pr_id)
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    if run.config.get("connection", {}).get("kind") != "api":
        raise HTTPException(410, RETIRED_MESSAGE)
    try:
        require_execution_engine(run.config)
    except ValueError as exc:
        raise HTTPException(410, str(exc)) from exc
    candidate = db.get(ReviewCandidate, candidate_id)
    if not candidate or candidate.run_id != run.id:
        raise HTTPException(404, "Assessment candidate not found.")
    if not CandidateOut.model_validate(candidate).retryable:
        raise HTTPException(409, "Only failed assessments or history conflicts without a completed result can be retried.")
    if (run.status not in {"completed", "partial"} or run.cancel_requested
        or not assessment.enabled(run) or not run.progress.get("analysis_saved")):
        raise HTTPException(409, "Finish the review before retrying an assessment.")
    if run.publication not in {"published", "no_new_comments"}:
        raise HTTPException(409, "Finish GitHub publication before retrying an assessment.")
    if db.scalar(select(ReviewRun.id).where(ReviewRun.pr_id == pr.id, ReviewRun.id != run.id,
                                         ReviewRun.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, "Another review is active for this pull request. Wait for it to finish.")
    connection = db.get(Connection, run.connection_id) if run.connection_id else None
    if not connection or any(run.config["connection"][key] != getattr(connection, key)
        for key in ("id", "kind", "owner_id", "provider", "protocol", "base_url")):
        raise HTTPException(409, "The original AI connection changed or is no longer available. Start a new review.")
    if not repo.enabled:
        raise HTTPException(409, "Enable reviews for this repository before retrying an assessment.")
    try:
        authorize_connection(db, connection, repo, "manual", user)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    current = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}",
                             github.installation_token(db, repo.installation_id))
    assessment_only = current["state"] == "closed" and current.get("merged") is True
    if (current["state"] != "open" and not assessment_only) or current["head"]["sha"] != run.head_sha or current["base"]["sha"] != run.base_sha:
        raise HTTPException(409, "The pull request changed or closed. Start a review of the current commit.")
    budget, known = assessment.remaining_budget(db, run)
    if budget is not None and (not known or budget <= 0):
        raise HTTPException(409, "The saved run's remaining AI budget cannot support another assessment. Start a new review.")
    candidate.attempts = [*candidate.attempts, {
        "status": candidate.status, "result": candidate.result, "error": candidate.error,
        "failure_reason": candidate.failure_reason,
        "context": candidate.context, "usage": candidate.usage, "actor_id": execution_identity(run)[0],
        "finished_at": candidate.finished_at.isoformat() if candidate.finished_at else None,
    }]
    candidate.status, candidate.result, candidate.error, candidate.finished_at = "queued", None, None, None
    candidate.failure_reason = None
    candidate.context, candidate.usage = ({"assessment_only": True} if assessment_only else {}), {}
    # Explicit retry adopts the initiating session (or its PAT) for the stored origin.
    run.progress = pat_provenance.adopt_progress({**run.progress, "assessment_retry": {
        "candidate_id": candidate.id, "actor_id": user.id, "assessment_only": assessment_only,
    }})
    if assessment_only:
        pr.state = "merged"
    run.status, run.error, run.blocked_reason = "queued", None, None
    if pipeline.enabled(run):
        pipeline.update(run, "assessment", "pending", started_at=None, finished_at=None, error=None, execution=None)
    enqueue(db, "review", {"run_id": run.id})
    assessment.record_summary(db, run, advance_revision=True)
    return CandidateOut.model_validate(candidate)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: str, db: DB, user: USER):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id, write=True)
    db.scalar(select(PullRequest).where(PullRequest.id == run.pr_id).with_for_update())
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    if run.status not in {"queued", "running", "blocked"}:
        raise HTTPException(409, "This review is no longer running.")
    cancel_review(db, run)
    return RunOut.model_validate(run)


@router.post("/runs/{run_id}/retry", response_model=RunOut, status_code=202)
def retry_review(run_id: str, db: DB, user: USER):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr, repo, _ = review_pr_access(db, user, run.pr_id)
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    if run.status != "blocked":
        raise HTTPException(
            409, "Start a new review to retry a terminal run; only blocked reviews can resume."
        )
    from code_mortis.reviews.service import authorize_connection, execution_identity

    try:
        require_execution_engine(run.config)
    except ValueError as exc:
        raise HTTPException(410, str(exc)) from exc
    connection = db.get(Connection, run.connection_id)
    if not connection or (connection.owner_id and connection.owner_id != user.id):
        raise HTTPException(403, "Only the connection owner can resume this review.")
    _, trigger = execution_identity(run)
    authorize_connection(db, connection, repo, trigger, user)
    run.status, run.error, run.blocked_reason = "queued", None, None
    run.failure_reason = None
    # Explicit resume adopts the initiating session (or its PAT) for the stored origin.
    run.progress = pat_provenance.adopt_progress(run.progress)
    enqueue(db, "review", {"run_id": run.id})
    return RunOut.model_validate(run)


@router.post("/runs/{run_id}/publish", status_code=202)
def retry_publication(run_id: str, db: DB, user: USER) -> dict:
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    review_pr_access(db, user, run.pr_id)
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    if run.cancel_requested or not can_publish(run) or run.publication not in {"failed", "partially_published"}:
        raise HTTPException(409, "There are no failed GitHub comments to resend for this review. Refresh the page to see its current status.")
    run.publication, run.publication_error = "pending", None
    run.progress = {key: value for key, value in run.progress.items() if key != "publication_stopped"}
    # Explicit retry adopts the initiating session (or its PAT) for the stored origin.
    run.progress = pat_provenance.adopt_progress(run.progress)
    job = enqueue(db, "publish", {"run_id": run.id})
    return {"job_id": job.id}


@router.post("/runs/{run_id}/summary/retry", status_code=202)
def retry_summary_generation(run_id: str, db: DB, user: USER) -> dict:
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    _, repo, _ = review_pr_access(db, user, run.pr_id)
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    db.refresh(repo)
    try:
        job = pr_summary.retry_generation(db, run, repo, user)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "pending", "job_id": job.id}
