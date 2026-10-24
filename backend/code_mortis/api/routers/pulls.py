from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from code_mortis.api.dependencies import DB, USER, pr_access, review_pr_access
from code_mortis.api.presenters import pr_out, repo_out
from code_mortis.integrations import github
from code_mortis.models import Finding, FindingVerification, PullRequest, ReviewRun
from code_mortis.reviews import findings as finding_list
from code_mortis.reviews import inbox, verification
from code_mortis.reviews import summary as pr_summary
from code_mortis.reviews.service import ACTIVE, can_publish, cancel_pr_reviews, create_run, upsert_pr
from code_mortis.schemas.common import (
    LIST_PAGE_SIZE,
    FindingFilters,
    FindingsOut,
    IgnorePullInput,
    InboxFilters,
    InboxOut,
    PRDetail,
    PullRequestOut,
    RunOut,
    RunPage,
    StartRunInput,
)

router = APIRouter()


@router.get("/pulls", response_model=InboxOut)
def pull_inbox(db: DB, user: USER, filters: Annotated[InboxFilters, Query()]):
    return inbox.list_pulls(db, github.visible_repositories(db, user), filters)


@router.get("/pulls/{pr_id}", response_model=PRDetail)
def pull_detail(pr_id: str, db: DB, user: USER):
    pr, repo, remote = pr_access(db, user, pr_id)
    return PRDetail(
        pull_request=pr_out(db, pr),
        repository=repo_out(db, repo),
        run_count=db.scalar(select(func.count()).select_from(ReviewRun).where(ReviewRun.pr_id == pr.id)),
        has_active_run=db.scalar(select(select(ReviewRun.id).where(
            ReviewRun.pr_id == pr.id, ReviewRun.status.in_(["queued", "running"]),
        ).exists())),
        cancellable_run_count=db.scalar(select(func.count()).select_from(ReviewRun).where(
            ReviewRun.pr_id == pr.id, ReviewRun.status.in_(ACTIVE),
        )),
        latest_observed_run_id=db.scalar(
            select(ReviewRun.id).where(ReviewRun.pr_id == pr.id, ReviewRun.status.in_(["completed", "partial"]))
            .order_by(ReviewRun.created_at.desc(), ReviewRun.id.desc()).limit(1)
        ),
        can_write=remote.get("permissions", {}).get("push", False),
    )


@router.put("/pulls/{pr_id}/ignore", response_model=PullRequestOut)
def ignore_pull(pr_id: str, body: IgnorePullInput, db: DB, user: USER):
    pr_access(db, user, pr_id, write=True)
    pr = db.scalar(select(PullRequest).where(PullRequest.id == pr_id).with_for_update()
                   .execution_options(populate_existing=True))
    pr.ignored = body.ignored
    if body.ignored:
        cancel_pr_reviews(db, pr)
        pending_resolutions = set(db.scalars(select(FindingVerification.run_id).join(ReviewRun).where(
            ReviewRun.pr_id == pr.id, FindingVerification.resolution.in_(["pending", "applying", "failed"]),
        )))
        # Suppress outstanding work on terminal runs too, including summary retries.
        for run in db.scalars(select(ReviewRun).where(ReviewRun.pr_id == pr.id).order_by(ReviewRun.id)
                              .with_for_update().execution_options(populate_existing=True)):
            if (run.publication in {"pending", "publishing", "failed", "partially_published"}
                or run.summary.get("status") in {"pending", "running"} or run.id in pending_resolutions):
                # Invalidate old work without cancelling a finished review's future explicit retries.
                run.progress = {key: value for key, value in run.progress.items()
                                if key not in {"execution", "summary_retry"}}
                run.progress = {**run.progress, "publication_stopped": True}
                if "summary" in run.stages:
                    run.stages = {**run.stages, "summary": {**run.stages["summary"], "execution": None}}
                if not run.cancel_requested and can_publish(run) and run.publication in {"pending", "publishing", "failed", "partially_published"}:
                    run.publication = "partially_published" if run.review_ids else "failed"
                pr_summary.interrupt(run)
        for finding in db.scalars(select(Finding).where(
            Finding.pr_id == pr.id, Finding.desired_status.is_not(None),
            Finding.sync_intent["verification_id"].as_string().is_not(None),
        ).order_by(Finding.id).with_for_update().execution_options(populate_existing=True)):
            verification.invalidate(db, finding, "Automatic resolution was stopped because the pull request was ignored.")
    db.flush()
    return pr_out(db, pr)


@router.post("/pulls/{pr_id}/cancel", response_model=list[RunOut])
def cancel_pull_reviews(pr_id: str, db: DB, user: USER):
    pr_access(db, user, pr_id, write=True)
    pr = db.scalar(select(PullRequest).where(PullRequest.id == pr_id).with_for_update())
    runs = cancel_pr_reviews(db, pr)
    if not runs:
        raise HTTPException(409, "This pull request has no reviews left to stop.")
    return [RunOut.model_validate(run) for run in runs]


@router.get("/pulls/{pr_id}/runs", response_model=RunPage)
def pull_runs(pr_id: str, db: DB, user: USER, page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    pr, _, _ = pr_access(db, user, pr_id)
    total = db.scalar(select(func.count()).select_from(ReviewRun).where(ReviewRun.pr_id == pr.id))
    page = min(page, max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE))
    runs = db.scalars(
        select(ReviewRun).where(ReviewRun.pr_id == pr.id)
        .order_by(ReviewRun.created_at.desc(), ReviewRun.id.desc())
        .offset((page - 1) * LIST_PAGE_SIZE).limit(LIST_PAGE_SIZE)
    )
    return RunPage(
        items=[RunOut.model_validate(run) for run in runs], total=total, page=page,
        has_more=page * LIST_PAGE_SIZE < total,
    )


@router.get("/pulls/{pr_id}/findings", response_model=FindingsOut)
def pull_findings(pr_id: str, db: DB, user: USER, filters: Annotated[FindingFilters, Query()]):
    pr, _, _ = pr_access(db, user, pr_id)
    return finding_list.list_findings(db, pr.id, filters)


@router.post("/pulls/{pr_id}/runs", response_model=RunOut, status_code=202)
def start_run(pr_id: str, db: DB, user: USER, body: StartRunInput | None = None):
    pr, repo, _ = review_pr_access(db, user, pr_id)
    token = github.installation_token(db, repo.installation_id)
    data = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
    pr = upsert_pr(db, repo, data)
    if pr.state != "open":
        raise HTTPException(409, "Reopen the pull request before starting another review.")
    run = create_run(db, pr, repo, "manual", user, body.connection_id if body else None)
    db.flush()
    return RunOut.model_validate(run)


@router.post("/pulls/{pr_id}/summary", status_code=202)
def retry_summary_sync(pr_id: str, db: DB, user: USER) -> dict:
    pr, _, _ = review_pr_access(db, user, pr_id)
    if not pr_summary.latest_run(db, pr.id):
        raise HTTPException(409, "This pull request has no overview to synchronize. Start a new review first.")
    if not pr.summary_error:
        raise HTTPException(409, "There is no failed GitHub overview update to retry.")
    pr.summary_error = None
    pr_summary.enqueue_sync(db, pr)
    return {"status": "pending"}
