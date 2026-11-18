"""Retire execution without converting saved engines or erasing consumed work."""

from sqlalchemy import select

from code_mortis.core.analysis import RETIRED_ENGINE_MESSAGE, cancel_tasks_in_transaction, engine_snapshot
from code_mortis.core.db import now
from code_mortis.core.inference_admission import release_in_transaction
from code_mortis.core.shared_execution import INFERENCE_JOBS, cancel_retired_review
from code_mortis.core.shared_execution import settle_job as settle_inference_job
from code_mortis.models import InferenceAdmission, Job, ReviewRun, SecurityScan

ENGINE_JOBS = INFERENCE_JOBS - {"test_connection"}


def is_retired(config):
    try:
        return engine_snapshot(config)["analysis_engine"] == "ocr"
    except ValueError:
        # Invalid snapshots fail the execution gate without changing their saved identity.
        return False


def retired_job(db, job):
    if job.kind not in ENGINE_JOBS:
        return False
    if job.kind == "generate_background":
        return is_retired(job.payload)
    if job.kind == "generate_summary":
        return is_retired(job.payload.get("config", {}))
    owner = (db.get(SecurityScan, job.payload.get("scan_id")) if job.kind == "security_scan"
             else db.get(ReviewRun, job.payload.get("run_id")))
    return owner is not None and is_retired(owner.config)


def preserve_owner(owner):
    if "engine_retirement" not in owner.progress:
        owner.progress = {**owner.progress, "engine_retirement": {
            "engine": "ocr", "recorded_at": now().isoformat(), "prior_status": owner.status,
            "prior_stages": owner.stages, "prior_report": owner.report,
            "prior_summary": owner.summary if isinstance(owner, ReviewRun) else None,
            "known_tokens_before_stop": owner.progress.get("tokens"),
        }}


def settle_job(db, job):
    if not retired_job(db, job):
        return
    model = SecurityScan if job.kind == "security_scan" else ReviewRun
    identity = job.payload.get("scan_id" if job.kind == "security_scan" else "run_id")
    owner = db.scalar(select(model).where(model.id == identity).with_for_update()
                      .execution_options(populate_existing=True)) if identity else None
    if owner is not None:
        preserve_owner(owner)
    if "engine_retirement" not in job.payload:
        job.payload = {**job.payload, "engine_retirement": {
            "engine": "ocr", "prior_status": job.status, "prior_error": job.error,
            "recorded_at": now().isoformat(), "prior_usage": job.payload.get("usage"),
        }}
    cancel_tasks_in_transaction(db, job.id)
    settle_inference_job(db, job, RETIRED_ENGINE_MESSAGE)


def retire_ocr(db):
    """Operator cutover: stop admission and all consumers/requests before this transaction."""
    from code_mortis.security_scans.service import cancel

    counts = {"reviews": 0, "scans": 0, "jobs": 0, "admissions": 0}
    for run in db.scalars(select(ReviewRun).where(ReviewRun.status.in_({"queued", "running", "blocked"}))
                         .order_by(ReviewRun.id).with_for_update()):
        if is_retired(run.config):
            preserve_owner(run)
            cancel_retired_review(db, run, RETIRED_ENGINE_MESSAGE)
            counts["reviews"] += 1
    for scan in db.scalars(select(SecurityScan).where(SecurityScan.status.in_({"queued", "running", "blocked"}))
                          .order_by(SecurityScan.id).with_for_update()):
        if is_retired(scan.config):
            preserve_owner(scan)
            cancel(db, scan, reason=RETIRED_ENGINE_MESSAGE)
            counts["scans"] += 1
    for job in db.scalars(select(Job).where(Job.kind.in_(ENGINE_JOBS), Job.status.in_({"queued", "running"}))
                         .order_by(Job.id).with_for_update()):
        if retired_job(db, job):
            settle_job(db, job)
            counts["jobs"] += 1
    # Only this operator cutover proves the old processes/requests have stopped.
    for ticket in db.scalars(select(InferenceAdmission).where(
            InferenceAdmission.status.in_({"active", "queued"})).order_by(InferenceAdmission.id)):
        job = db.get(Job, ticket.job_id) if ticket.job_id else None
        if job and retired_job(db, job) and release_in_transaction(db, ticket.id):
            counts["admissions"] += 1
    return counts
