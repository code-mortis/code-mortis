"""Shared-only admission and durable settlement of retired subscription work."""

from sqlalchemy import select

from code_mortis.core.db import now

RETIRED_MESSAGE = "Subscription execution is retired. Ask an administrator to select a shared API connection in repository settings, then explicitly start new work."
INFERENCE_JOBS = {"review", "assess_candidates", "verify_fixes", "summarize_review", "generate_summary", "generate_background", "security_scan", "test_connection"}


def is_shared(connection) -> bool:
    return (connection.get("kind") if isinstance(connection, dict) else getattr(connection, "kind", None)) == "api"


def require_shared(connection):
    if not is_shared(connection):
        raise ValueError(RETIRED_MESSAGE)


def background_result_saved(payload) -> bool:
    mode = payload.get("mode")
    if mode in {"summary", "summary_draft", "summary_refresh"}:
        return bool(payload.get("project_summary"))
    return bool(payload.get("background") and (not mode or payload.get("project_summary")))


def cancel_retired_review(db, run, reason=RETIRED_MESSAGE):
    from code_mortis.reviews.service import cancel_review

    if run.status not in {"queued", "running", "blocked"}:
        return
    cancel_review(db, run)
    run.error = reason
    if run.summary.get("status") in {"pending", "running"}:
        run.summary = {**run.summary, "status": "failed", "error": reason,
                       "tokens": None if run.summary["status"] == "running" else run.summary.get("tokens", 0)}
    run.stages = {name: {**stage, "status": "cancelled", "tokens": None if stage.get("status") == "running" else stage.get("tokens", 0)}
                  if stage.get("status") in {"pending", "running", "waiting"} else stage for name, stage in run.stages.items()}


def retired_job(db, job) -> bool:
    from code_mortis.models import Connection, ReviewRun, SecurityScan

    if job.kind not in INFERENCE_JOBS:
        return False
    payload = job.payload
    if job.kind in {"review", "assess_candidates", "verify_fixes", "summarize_review"}:
        run = db.get(ReviewRun, payload.get("run_id"))
        snapshot = run.config.get("connection", {}) if run else None
    elif job.kind == "security_scan":
        scan = db.get(SecurityScan, payload.get("scan_id"))
        snapshot = scan.config.get("connection", {}) if scan else None
    elif job.kind == "generate_summary":
        snapshot = payload.get("config", {}).get("connection", {})
    elif job.kind == "generate_background":
        snapshot = payload.get("connection", {})
    else:
        return not is_shared(db.get(Connection, payload.get("connection_id")))
    return not is_shared(snapshot)


def settle_job(db, job, reason=RETIRED_MESSAGE):
    from code_mortis.models import ReviewRun, SecurityScan
    from code_mortis.reviews.summary import fail_retry
    from code_mortis.security_scans.service import cancel

    saved = job.kind == "generate_background" and background_result_saved(job.payload)
    if job.kind == "generate_summary":
        run = db.get(ReviewRun, job.payload.get("run_id"))
        outcome = job.payload.get("summary_attempt_result")
        if not outcome and run and run.progress.get("summary_retry") == job.id and run.summary.get("status") == "completed":
            outcome = run.summary
            job.payload = {**job.payload, "summary_attempt_result": outcome}
        saved = bool(outcome and outcome.get("status") == "completed")
    if saved:
        job.status, job.error, job.lease_until, job.dispatched_at = "done", None, None, None
        job.payload = {**job.payload, "retired_at": now().isoformat()}
        return
    if job.kind in {"review", "assess_candidates", "verify_fixes", "summarize_review"}:
        run = db.get(ReviewRun, job.payload.get("run_id"))
        if run:
            cancel_retired_review(db, run, reason)
    elif job.kind == "security_scan":
        scan = db.get(SecurityScan, job.payload.get("scan_id"))
        if scan:
            cancel(db, scan, reason=reason)
    elif job.kind == "generate_summary":
        fail_retry(db, job, reason)
    job.status, job.error, job.lease_until, job.dispatched_at = "failed", reason, None, None
    job.available_at = now()
    usage = {}
    if job.payload.get("inference_started") and "usage" not in job.payload:
        usage = {"usage": {"tokens": job.payload.get("summary_attempt_result", {}).get("tokens")}}
    job.payload = {**job.payload, "retired_at": now().isoformat(), **usage}


def cancel_connection_jobs(db, identity, repository_id=None):
    from code_mortis.models import Job, PullRequest, ReviewRun

    for job in db.scalars(select(Job).where(Job.kind.in_({"generate_background", "generate_summary"}),
                                          Job.status.in_({"queued", "running"}),
                                          Job.payload["connection_id"].as_string() == identity).order_by(Job.id).with_for_update()):
        target = job.payload.get("repository_id")
        if job.kind == "generate_summary":
            run = db.get(ReviewRun, job.payload.get("run_id"))
            pr = db.get(PullRequest, run.pr_id) if run else None
            target = pr.repository_id if pr else None
        if repository_id is None or repository_id == target:
            settle_job(db, job)


def retire_subscriptions(db) -> dict:
    """Run after stopping old admission/consumers and fencing their runner endpoint."""
    from code_mortis.models import Connection, Job, ReviewRun, SecurityScan
    from code_mortis.security_scans.service import cancel

    counts = {"reviews": 0, "scans": 0, "jobs": 0}
    for run in db.scalars(select(ReviewRun).where(ReviewRun.status.in_({"queued", "running", "blocked"})).order_by(ReviewRun.id).with_for_update()):
        if not is_shared(run.config.get("connection", {})):
            cancel_retired_review(db, run)
            counts["reviews"] += 1
    for scan in db.scalars(select(SecurityScan).where(SecurityScan.status.in_({"queued", "running", "blocked"})).order_by(SecurityScan.id).with_for_update()):
        if not is_shared(scan.config.get("connection", {})):
            cancel(db, scan, reason=RETIRED_MESSAGE)
            counts["scans"] += 1
    for job in db.scalars(select(Job).where(Job.kind.in_(INFERENCE_JOBS), Job.status.in_({"queued", "running"})).order_by(Job.id).with_for_update()):
        if retired_job(db, job):
            settle_job(db, job)
            counts["jobs"] += 1
    for connection in db.scalars(select(Connection).where(Connection.kind != "api").with_for_update()):
        connection.auth_status, connection.auth_message = "revoked", RETIRED_MESSAGE
    return counts
