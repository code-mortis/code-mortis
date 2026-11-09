"""Durable coordination for independently budgeted review stages."""

import json

from sqlalchemy import select

from code_mortis.core import source as git_source
from code_mortis.core.db import now, transaction
from code_mortis.core.errors import ReviewStopped, RunnerBlocked
from code_mortis.core.review_results import report_tokens
from code_mortis.core.security import unseal
from code_mortis.integrations import github
from code_mortis.models import (
    Connection,
    FindingVerification,
    Installation,
    Job,
    Membership,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRun,
    User,
)
from code_mortis.reviews import assessment, verification
from code_mortis.reviews import summary as pr_summary

TERMINAL = {"completed", "partial", "failed", "skipped", "cancelled"}

# The stored analysis key remains readable in historical runs. It never selects an executable engine.
STAGES = ("ocr", "assessment", "verification", "summary")
KINDS = {"verify_fixes": "verification", "summarize_review": "summary", "assess_candidates": "assessment"}


def enabled(run):
    return run.config.get("pipeline_version") == 2


def owns(run, stage, claim):
    return (run.stages or {}).get(stage, {}).get("execution") == claim


def update(run, stage, status, **values):
    previous = (run.stages or {}).get(stage, {})
    timing = {"finished_at": now().isoformat()} if status in TERMINAL and previous.get("status") not in TERMINAL else {}
    run.stages = {**(run.stages or {}), stage: {**previous, "status": status, **timing, **values}}


def usage(values):
    return sum(values) if all(type(value) is int and value >= 0 for value in values) else None


def enqueue_finalizer(db, run):
    from code_mortis.reviews.service import enqueue

    if not db.scalar(select(Job.id).where(Job.kind == "finalize_review", Job.status == "queued",
                                          Job.payload["run_id"].as_string() == run.id).limit(1)):
        enqueue(db, "finalize_review", {"run_id": run.id})


def start(db, run, claim):
    update(run, "ocr", "running", execution=claim, started_at=run.started_at.isoformat(), tokens=None)
    enqueue_stages(db, run)


def enqueue_stages(db, run):
    from code_mortis.reviews.service import enqueue

    for kind, stage in KINDS.items():
        if stage == "assessment":
            continue  # Only the finalizer schedules assessments deferred behind verification.
        if run.stages.get(stage, {}).get("status") != "pending":
            continue
        if stage == "verification" and not db.scalar(select(FindingVerification.id).where(
            FindingVerification.run_id == run.id, FindingVerification.status == "queued",
        ).limit(1)):
            update(run, stage, "skipped", tokens=0)
            continue
        if stage == "verification" and run.config["settings"].get("max_files") is not None:
            if not run.progress.get("file_limit_checked"):
                continue
            if run.progress.get("file_limit_exceeded"):
                reason = "Fix verification was skipped because the review exceeded its file limit."
                verification.skip_pending(db, run, reason)
                update(run, stage, "skipped", tokens=0, error=reason)
                continue
        existing = db.scalar(select(Job.id).where(Job.kind == kind, Job.payload["run_id"].as_string() == run.id).limit(1))
        if not existing:
            enqueue(db, kind, {"run_id": run.id})


def review_done(db, run, error=None):
    status = run.progress.get("analysis_status", "failed")
    update(run, "ocr", status, tokens=report_tokens(run.report), error=error)
    if error or not run.progress.get("assessment_deferred"):
        assessment_done(db, run, error)
    enqueue_finalizer(db, run)


def assessment_done(db, run, error=None):
    status = run.progress.get("analysis_status", "failed")
    if error:
        assessment.withhold_pending(db, run, error)
    rows = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == run.id)))
    amounts = [item.usage.get("tokens", 0 if not item.usage else None) for item in rows]
    amounts += [attempt["usage"].get("tokens", 0 if not attempt["usage"] else None) for item in rows for attempt in item.attempts]
    outcome = "skipped" if status not in {"completed", "partial"} else "partial" if any(row.status != "completed" for row in rows) else "completed"
    update(run, "assessment", outcome, tokens=usage(amounts), error=None if outcome == "completed" else error,
           waiting_for=None)


def fail_stage(db, run, stage, error):
    if run.stages.get(stage, {}).get("status") in TERMINAL:
        return
    if stage == "summary" and run.summary.get("status") == "completed" and run.summary.get("result"):
        update(run, stage, "completed", tokens=run.summary.get("tokens"), error=None)
        enqueue_finalizer(db, run)
        return
    update(run, stage, "failed", error=error)
    if stage == "verification":
        verification.skip_pending(db, run, error)
    elif stage == "assessment":
        assessment_done(db, run, error)
    else:
        run.summary = {**run.summary, "status": "failed", "error": error}
    enqueue_finalizer(db, run)


def defer_assessment(db, run):
    if run.progress.get("assessment_deferred"):
        return True  # A recovered analysis job must not take ownership from the assessment job.
    if (run.config["connection"]["kind"] != "api"
        or run.stages.get("verification", {}).get("status") in TERMINAL
        or not db.scalar(select(ReviewCandidate.id).where(
            ReviewCandidate.run_id == run.id, ReviewCandidate.status == "queued",
        ).limit(1))):
        return False
    # Verification appends finding history; taking the assessment snapshot now races those writes.
    run.progress = {**run.progress, "assessment_deferred": True}
    update(run, "assessment", "waiting", waiting_for="verification", execution=None, started_at=None)
    enqueue_finalizer(db, run)
    return True


def perform(payload):
    from code_mortis.core.analysis import require_execution_engine
    from code_mortis.core.shared_execution import cancel_retired_review, is_shared
    from code_mortis.reviews.service import (
        authorize_connection,
        execution_identity,
        review_stopped,
    )

    run_id, claim = payload["run_id"], payload["_claim"]
    with transaction() as db:
        job = db.get(Job, payload["_job_id"])
        if not job or job.status != "running" or claim != f"{job.id}:{job.attempts}" or job.lease_until <= now():
            return
        stage = KINDS[job.kind]
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not run or run.cancel_requested or run.status in {"cancelled", "superseded", "completed", "partial", "failed", "skipped"} or run.stages.get(stage, {}).get("status") in TERMINAL:
            return
        if not is_shared(run.config.get("connection", {})):
            cancel_retired_review(db, run)
            return
        require_execution_engine(run.config)
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        if _mcp.revoked(db, _mcp.provenance_from_payload(payload) or _mcp.provenance_from_run(run),
                        repository_id=db.get(PullRequest, run.pr_id).repository_id):
            fail_stage(db, run, stage, _mcp.REVOKED_MESSAGE)
            return
        if _pat.revoked(db, _pat.origin_for(payload, run),
                        repository_id=db.get(PullRequest, run.pr_id).repository_id):
            fail_stage(db, run, stage, _pat.REVOKED_MESSAGE)
            return
        if run.status != "running":
            raise RunnerBlocked("busy")
        previous = run.stages.get(stage, {})
        if previous.get("status") == "running":
            previous_id, _, previous_attempt = previous.get("execution", "").rpartition(":")
            previous_job = db.get(Job, previous_id) if previous_id else None
            if previous_job and previous_job.status == "running" and str(previous_job.attempts) == previous_attempt and previous_job.lease_until > now():
                return
            if stage == "summary":
                fail_stage(db, run, stage, "The worker stopped during this stage. Unknown usage will not be spent again automatically.")
                return
        connection = db.get(Connection, run.connection_id)
        snapshot = run.config["connection"]
        if not connection or any(snapshot[key] != getattr(connection, key) for key in ("kind", "owner_id", "provider", "protocol", "base_url")):
            raise ValueError("The selected connection changed. Start a new review with the current settings.")
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if pr.ignored or not repo.enabled or not repo.active or not db.get(Installation, repo.installation_id).active:
            raise ReviewStopped()
        actor_id, trigger = execution_identity(run)
        actor = db.get(User, actor_id) if actor_id else None
        if actor_id:
            if not actor or not actor.enabled or not db.get(Membership, actor.id):
                raise ValueError("The requesting user no longer has access.")
            github.authorize_repo(db, actor, repo.full_name, write=True)
        authorize_connection(db, connection, repo, trigger, actor)
        token = github.installation_token(db, repo.installation_id)
        remote = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
        if remote["state"] != "open" or (remote["head"]["sha"], remote["base"]["sha"]) != (run.head_sha, run.base_sha):
            raise ReviewStopped("The pull request changed before this stage started.")
        from code_mortis.reviews.context import execution_credentials

        credentials = execution_credentials(db, repo.id, json.loads(unseal(connection.secret)), token)
        update(run, stage, "running", execution=claim, started_at=now().isoformat(), error=None, waiting_for=None)
        options = dict(run.config["settings"])
        if stage == "summary":
            options["timeout_seconds"] = options.get("summary_timeout_seconds", 180)
        elif options["timeout_seconds"] is not None:
            options["timeout_seconds"] = max(1, int(options["timeout_seconds"] - (now() - run.started_at).total_seconds()))
        full_name, number = repo.full_name, pr.number

    def stopped():
        return review_stopped(run_id, claim, stage=stage)

    def after_checkout(_result, repo_path, env, fetch_env):
        if stage == "verification":
            verification.perform(run_id, repo_path, env, fetch_env, snapshot, credentials, claim)
        elif stage == "assessment":
            assessment.perform(run_id, repo_path, env, fetch_env, snapshot, credentials, claim, stage=stage)
        else:
            pr_summary.generate(run_id, repo_path, env, snapshot, credentials, claim, stage="summary")

    with git_source.review_snapshot(full_name, number, run.base_sha, run.head_sha, token, stopped,
                                timeout_seconds=options["timeout_seconds"]) as (repo_path, env, fetch_env, _):
        after_checkout(("completed", {}, []), repo_path, env, fetch_env)
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not owns(run, stage, claim) or run.cancel_requested or run.status in {"cancelled", "superseded"}:
            return
        if stage == "summary":
            update(run, stage, run.summary["status"], tokens=run.summary.get("tokens"), error=run.summary.get("error"))
        elif stage == "assessment":
            assessment_done(db, run)
        else:
            rows = list(db.scalars(select(FindingVerification).where(FindingVerification.run_id == run_id)))
            outcome = "failed" if any(row.status == "failed" for row in rows) else "skipped" if all(row.status == "skipped" for row in rows) else "completed"
            error = next((row.error for row in rows if row.status == "failed" and row.error), None)
            error = error or next((row.error for row in rows if row.status == "skipped" and row.error), None)
            update(run, stage, outcome, tokens=usage([row.usage.get("tokens", 0 if not row.usage else None) for row in rows]),
                   error=error)
        enqueue_finalizer(db, run)


def finalize(run_id):
    from code_mortis.reviews.service import complete_saved_review, enqueue

    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not run or not enabled(run) or run.progress.get("pipeline_finalized") or run.progress.get("assessment_retry"):
            return
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if (pr.head_sha, pr.base_sha) != (run.head_sha, run.base_sha) or pr.state != "open":
            run.status, run.cancel_requested, run.publication, run.finished_at = "superseded", True, "not_applicable", now()
        elif pr.ignored or not repo.active or not repo.enabled or not db.get(Installation, repo.installation_id).active:
            run.status, run.cancel_requested, run.publication, run.finished_at = "cancelled", True, "not_applicable", now()
        if run.cancel_requested or run.status in {"cancelled", "superseded"}:
            assessment.withhold_pending(db, run, "Assessment stopped because the review was cancelled or replaced by a newer run. Check the latest review.")
            for stage in STAGES:
                if run.stages.get(stage, {}).get("status") not in TERMINAL:
                    update(run, stage, "cancelled", waiting_for=None)
            run.progress = {**run.progress, "pipeline_finalized": True}
            return
        if (run.progress.get("assessment_deferred")
            and run.stages.get("assessment", {}).get("status") == "waiting"
            and run.stages.get("assessment", {}).get("waiting_for") == "verification"
            and run.stages.get("ocr", {}).get("status") in TERMINAL
            and run.stages.get("verification", {}).get("status") in TERMINAL):
            if not db.scalar(select(Job.id).where(
                Job.kind == "assess_candidates", Job.payload["run_id"].as_string() == run.id,
            ).limit(1)):
                enqueue(db, "assess_candidates", {"run_id": run.id})
            update(run, "assessment", "pending", waiting_for=None)
        if any(run.stages.get(stage, {}).get("status") not in TERMINAL for stage in STAGES):
            return
        run.progress = {**run.progress, "pipeline_finalized": True}
        run.status = run.progress.get("analysis_status", "failed")
        run.finished_at = now()
        for row in db.scalars(select(FindingVerification).where(FindingVerification.run_id == run.id)):
            verification.apply_resolution(db, row)
        verification.record_summary(db, run)
        if not complete_saved_review(db, run, finalizing=True):
            run.publication = "not_applicable"
