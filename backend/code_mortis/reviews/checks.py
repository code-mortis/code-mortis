"""GitHub checks mirror persisted review state; they never execute models."""

import json

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction, uid
from code_mortis.core.security import clean_text, digest
from code_mortis.integrations import github
from code_mortis.models import (
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
        # The check follows its run: inherit the run's stored PAT origin only,
        # never ambient context, so unrelated work cannot misattribute it.
        # MCP deliberately leaves sync_check unattributed; see provenance docs.
        from code_mortis.api_tokens import provenance as _pat

        payload = {"run_id": run.id}
        origin = _pat.provenance_from_run(run)
        if origin is not None:
            payload[_pat.PROVENANCE_KEY] = origin
        db.add(Job(id=uid(), kind="sync_check", payload=payload))


@event.listens_for(Session, "before_flush")
def queue_check_changes(db, _context, _instances):
    # One transaction hook covers API actions, subscription revocation and worker recovery alike.
    for run in list(db.new) + list(db.dirty):
        if not isinstance(run, ReviewRun):
            continue
        attrs = inspect(run).attrs
        previous = attrs.progress.history.deleted
        # UI revisions do not change the GitHub check payload.
        progress_changed = attrs.progress.history.has_changes() and (
            {key: value for key, value in ((previous[0] or {}) if previous else {}).items() if key != "assessment_revision"}
            != {key: value for key, value in (run.progress or {}).items() if key != "assessment_revision"}
        )
        if run in db.new or progress_changed or any(attrs[name].history.has_changes() for name in WATCHED - {"progress"}):
            if not run.id:
                run.id = uid()
            enqueue_sync(db, run)


def check_payload(run: ReviewRun, findings: int) -> dict:
    from code_mortis.reviews.assessment import assessment_only

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
        if run.publication in {"published", "no_new_comments"}:
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
        conclusion, title = "neutral", "Review complete"

    lines = [f"Reviewed commit: `{run.head_sha}`", f"Findings: {findings}"]
    assessment = run.progress.get("assessment")
    if assessment is not None:
        lines[1] = (
            f"New validated findings: {assessment['new_findings']}. "
            f"Repeated observations: {assessment['repeated_observations']}. "
            f"Not supported candidates: {assessment.get('rejected_candidates', 0)}. "
            f"Withheld candidates: {assessment['withheld_candidates']}. "
            f"Comments published: {assessment['published_comments']}."
        )
        if (status == "completed" and conclusion in {"success", "neutral"} and (
            assessment.get("failed") or assessment.get("skipped")
            or run.stages.get("assessment", {}).get("status") in {"partial", "failed", "cancelled"}
        )):
            conclusion, title = "neutral", "Assessment incomplete"
            lines.append("Some proposed findings could not be assessed. Open the run to inspect errors and retry eligible assessments.")
        if status == "completed" and conclusion == "success":
            title = f"Review complete: {assessment['new_findings']} new validated findings"
    if assessment_only(run):
        lines.append("Assessment retried against the original commit after merge. Historical verdicts are saved without creating findings, publishing comments, or changing resolution states.")
        if status == "completed" and conclusion == "success":
            title = "Assessment complete at original commit"
        elif conclusion == "cancelled":
            title = "Assessment cancelled or superseded"
        elif run.status == "running":
            title = "Assessing original commit"
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
            ("Historical assessment completed; no new comments were published. " if assessment_only(run)
             else "Review completed; no additional comments were needed. " if run.publication == "no_new_comments"
             else "Review completed and comments published. ")
            + "Findings are informational; this is not approval to merge."
        )
    if run.status == "skipped" and isinstance((run.report or {}).get("message"), str):
        lines.append(clean_text(run.report["message"])[:2000])
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
    if assessment_only(run) and conclusion and (finished := run.progress["assessment_retry"].get("finished_at")):
        payload["completed_at"] = finished
    return payload


def sync_check(run_id: str):
    from code_mortis.reviews import assessment
    from code_mortis.reviews.verification import record_summary

    # Serialize create/update and state transitions through the same row lock.
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not run:
            return
        assessment.record_summary(db, run, advance_revision=False)
        if run.progress.get("verification_prepared"):
            record_summary(db, run)
        if not run.check_enabled:
            return
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if not repo.active or not db.get(Installation, repo.installation_id).active:
            raise ValueError("GitHub check cannot be updated because installation access was removed.")
        from code_mortis.api_tokens import provenance as _pat

        pat_closeout = False
        if _pat.PROVENANCE_KEY in (run.progress or {}):
            if _pat.revoked(db, (run.progress or {}).get(_pat.PROVENANCE_KEY), repository_id=repo.id):
                # Revoked after claim or retried after revocation: only a
                # terminal closeout PATCH to the existing check is allowed,
                # never a new check, new findings, full content or model work.
                if run.status not in {"cancelled", "superseded"} or not run.check_id:
                    run.check_pending, run.check_error = False, _pat.REVOKED_MESSAGE
                    return
                pat_closeout = True
        count = db.scalar(select(func.count()).select_from(Occurrence).where(Occurrence.run_id == run.id))
        if pat_closeout:
            payload = {
                "name": "Code Mortis",
                "external_id": run.id,
                "details_url": f"{settings().public_url}/runs/{run.id}",
                "status": "completed",
                "conclusion": "cancelled",
                "output": {"title": "Review cancelled",
                           "summary": _pat.REVOKED_MESSAGE},
            }
        else:
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
    from code_mortis.reviews.service import authorize_connection, can_publish, create_run, enqueue, upsert_pr

    delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update())
    if not delivery:
        raise ValueError("The GitHub Re-run request could not be verified. Start a new review from Code Mortis or ask the administrator to check webhook delivery.")
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
    if pr.ignored:
        raise ValueError("Restore this pull request before reviewing it.")
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
    if can_publish(run) and run.publication in {"failed", "partially_published"}:
        run.publication, run.publication_error = "pending", None
        enqueue(db, "publish", {"run_id": run.id})
    elif can_publish(run) and run.publication in {"pending", "publishing"}:
        pass  # An equivalent publication is already active.
    else:
        connection = db.get(Connection, run.connection_id) if run.connection_id else None
        if not connection:
            raise ValueError(
                "The original AI connection is no longer available. Select a connection in Code Mortis."
            )
        authorize_connection(db, connection, repo, "manual", actor)
        if run.status == "blocked":
            from code_mortis.core.analysis import require_execution_engine

            require_execution_engine(run.config)
            run.status, run.error, run.blocked_reason, run.failure_reason = "queued", None, None, None
            enqueue(db, "review", {"run_id": run.id})
        else:
            create_run(db, pr, repo, "manual", actor, connection.id)
    delivery.handled_at = now()
