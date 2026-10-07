"""GitHub checks mirror persisted review state; they never execute models."""

import json

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from . import github
from .config import settings
from .db import now, transaction, uid
from .models import (
    Connection,
    Installation,
    Job,
    Membership,
    Occurrence,
    PullRequest,
    Repository,
    ReviewRun,
    User,
    WebhookDelivery,
)
from .security import clean_text, digest

WATCHED = {
    "status",
    "publication",
    "error",
    "publication_error",
    "failure_reason",
    "blocked_reason",
    "started_at",
    "finished_at",
    "report",
    "progress",
    "cancel_requested",
    "check_enabled",
}


def enqueue_sync(db: Session, run: ReviewRun):
    if run.check_enabled is False:
        return
    run.check_pending = True
    if any(
        isinstance(job, Job) and job.kind == "sync_check" and job.payload.get("run_id") == run.id
        for job in db.new
    ):
        return
    if not db.scalar(
        select(Job.id)
        .where(Job.kind == "sync_check", Job.status == "queued", Job.payload["run_id"].as_string() == run.id)
        .limit(1)
    ):
        db.add(Job(id=uid(), kind="sync_check", payload={"run_id": run.id}))


@event.listens_for(Session, "before_flush")
def queue_check_changes(db, _context, _instances):
    # One transaction hook covers API actions, subscription revocation and worker recovery alike.
    for run in list(db.new) + list(db.dirty):
        if isinstance(run, ReviewRun) and (
            run in db.new or any(inspect(run).attrs[name].history.has_changes() for name in WATCHED)
        ):
            if not run.id:
                run.id = uid()
            enqueue_sync(db, run)


def check_payload(run: ReviewRun, findings: int) -> dict:
    status, conclusion, title = "completed", "failure", "Review failed"
    if run.status in {"cancelled", "superseded"} or run.failure_reason == "superseded" or (
        run.status in {"completed", "partial"} and run.publication == "not_applicable"
    ):
        conclusion, title = "cancelled", "Review cancelled or superseded"
    elif run.status == "queued":
        status, conclusion, title = "queued", None, "Waiting for review"
    elif run.status == "running":
        status, conclusion, title = "in_progress", None, "Reviewing changes"
    elif run.status == "blocked":
        conclusion, title = "action_required", "Review needs your attention"
    elif run.status == "skipped":
        conclusion, title = "skipped", "Review skipped"
    elif run.failure_reason == "timeout":
        conclusion, title = "timed_out", "Review timed out"
    elif run.status == "partial":
        title = "Review incomplete"
    elif run.publication in {"failed", "partially_published"}:
        title = "Comments could not be fully published"
    elif run.status == "completed":
        if run.publication == "published":
            conclusion, title = (
                "success",
                f"Review complete: {findings} finding{'s' if findings != 1 else ''}",
            )
        else:
            status, conclusion, title = "in_progress", None, "Publishing comments"

    verification = run.progress.get("verification", {})
    if status == "completed" and conclusion == "success" and verification.get("sync_failed"):
        conclusion, title = "action_required", "Verified fixes need GitHub synchronization"
    elif status == "completed" and conclusion == "success" and verification.get("pending"):
        status, conclusion, title = "in_progress", None, "Synchronizing verified fixes"
    elif status == "completed" and conclusion == "success" and (
        verification.get("failed") or verification.get("skipped") or verification.get("uncertain")
    ):
        conclusion, title = "neutral", "Review complete; some findings could not be verified"

    lines = [f"Reviewed commit: `{run.head_sha}`", f"Findings: {findings}"]
    if verification.get("selected"):
        lines.append(
            f"Fix verification: {verification.get('completed', 0)} of {verification['selected']} completed; "
            f"{verification.get('resolved', 0)} automatically resolved; "
            f"{verification.get('pending', 0)} pending synchronization."
        )
    coverage = (run.report or {}).get("manifest", {}).get("coverage", {})
    if isinstance(coverage.get("selected"), list):
        completed = len(coverage.get("completed", [])) + len(coverage.get("reused", []))
        lines.append(f"Coverage: {completed} of {len(coverage['selected'])} selected review items completed.")
    elif isinstance((run.progress or {}).get("selected"), int):
        lines.append(
            f"Coverage: {run.progress.get('completed', 0)} of {run.progress['selected']} assigned files completed."
        )
    if conclusion == "success":
        lines.append(
            "Review completed and comments published. Findings are informational; this is not approval to merge."
        )
    for value in (run.error, run.publication_error, run.blocked_reason):
        if value:
            lines.append(clean_text(value)[:2000])
    payload = {
        "name": "Code Mortis",
        "external_id": run.id,
        "details_url": f"{settings().public_url}/runs/{run.id}",
        "status": status,
        "conclusion": conclusion,
        "output": {"title": title, "summary": "\n\n".join(lines)},
    }
    if run.started_at:
        payload["started_at"] = run.started_at.isoformat()
    # Keep this timestamp stable for retries, including blocked reviews without finished_at.
    payload["completed_at"] = (
        (run.finished_at or run.started_at or run.created_at).isoformat() if conclusion else None
    )
    return payload


def sync_check(run_id: str):
    from .verification import record_summary

    # Serialize create/update and state transitions through the same row lock.
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not run:
            return
        if run.progress.get("verification_prepared"):
            record_summary(db, run)
        if not run.check_enabled:
            return
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if not repo.active or not db.get(Installation, repo.installation_id).active:
            raise ValueError("GitHub check cannot be updated because installation access was removed.")
        count = db.scalar(select(func.count()).select_from(Occurrence).where(Occurrence.run_id == run.id))
        payload = check_payload(run, count)
        fingerprint = digest(json.dumps(payload, sort_keys=True))
        if run.check_id and run.check_payload_hash == fingerprint:
            run.check_pending, run.check_error = False, None
            return
        token = github.installation_token(db, repo.installation_id, checks=True)
        wire = {key: value for key, value in payload.items() if value is not None}
        if not run.check_id:
            config = github.app_config(db)
            # external_id is not a GitHub idempotency key. Recover a lost POST response first.
            existing = next(
                (
                    item
                    for item in github.pages(
                        f"/repos/{repo.full_name}/commits/{run.head_sha}/check-runs?filter=all&app_id={config.github_app_id}",
                        token,
                        "check_runs",
                    )
                    if str(item.get("app", {}).get("id")) == config.github_app_id
                    and item.get("external_id") == run.id
                    and item.get("head_sha") == run.head_sha
                ),
                None,
            )
            if existing is None:
                existing = github.request(
                    "POST",
                    f"/repos/{repo.full_name}/check-runs",
                    token,
                    json={**wire, "head_sha": run.head_sha},
                )
            else:
                github.request(
                    "PATCH", f"/repos/{repo.full_name}/check-runs/{existing['id']}", token, json=wire
                )
            run.check_id, run.check_url = existing["id"], existing["html_url"]
        else:
            github.request("PATCH", f"/repos/{repo.full_name}/check-runs/{run.check_id}", token, json=wire)
        run.check_status, run.check_conclusion = payload["status"], payload["conclusion"]
        run.check_payload_hash, run.check_synced_at = fingerprint, now()
        run.check_pending, run.check_error = False, None


def rerequest(db: Session, body: dict, delivery_id: str):
    from .reviews import authorize_connection, create_run, enqueue, upsert_pr

    delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update())
    if not delivery:
        raise ValueError("GitHub Re-run is missing its verified delivery identity.")
    if delivery.handled_at:
        return
    check = body.get("check_run") or {}
    if str((check.get("app") or {}).get("id")) != github.app_config(db).github_app_id:
        return
    run = db.scalar(select(ReviewRun).where(ReviewRun.check_id == check.get("id")))
    if not run or check.get("external_id") != run.id or check.get("head_sha") != run.head_sha:
        raise ValueError("GitHub Re-run does not match a recorded Code Mortis check.")
    # Match create_run's PR-before-run lock order when requests race with new commits.
    pr = db.scalar(select(PullRequest).where(PullRequest.id == run.pr_id).with_for_update())
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run.id).with_for_update().execution_options(populate_existing=True))
    repo = db.get(Repository, pr.repository_id)
    if repo.id != (body.get("repository") or {}).get("id") or repo.installation_id != (
        body.get("installation") or {}
    ).get("id"):
        raise ValueError("GitHub Re-run does not match this repository installation.")
    if not repo.active or not repo.enabled or not db.get(Installation, repo.installation_id).active:
        raise ValueError("Enable the repository and installation before requesting another review.")
    actor = db.get(User, (body.get("sender") or {}).get("id"))
    if not actor or not actor.enabled or not db.get(Membership, actor.id):
        raise ValueError("Sign in as an enabled Code Mortis member before using GitHub Re-run.")
    github.authorize_repo(db, actor, repo.full_name, write=True)
    token = github.installation_token(db, repo.installation_id)
    remote = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
    if (
        remote["state"] != "open"
        or remote["head"]["sha"] != run.head_sha
        or remote["base"]["sha"] != run.base_sha
    ):
        raise ValueError("This check is obsolete. Open the current PR in Code Mortis to start a review.")
    pr = upsert_pr(db, repo, remote)
    if run.status == "completed" and run.publication in {"failed", "partially_published"}:
        run.publication, run.publication_error = "pending", None
        enqueue(db, "publish", {"run_id": run.id})
    elif run.status == "completed" and run.publication in {"pending", "publishing"}:
        pass  # An equivalent publication is already active.
    else:
        connection = db.get(Connection, run.connection_id) if run.connection_id else None
        if not connection:
            raise ValueError(
                "The original AI connection is no longer available. Select a connection in Code Mortis."
            )
        authorize_connection(db, connection, repo, "manual", actor)
        if run.status == "blocked":
            run.status, run.error, run.blocked_reason, run.failure_reason = "queued", None, None, None
            enqueue(db, "review", {"run_id": run.id})
        else:
            create_run(db, pr, repo, "manual", actor, connection.id)
    delivery.handled_at = now()
