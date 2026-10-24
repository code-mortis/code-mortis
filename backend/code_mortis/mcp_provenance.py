"""MCP grant provenance for durable worker lifecycle.

Preserves the originating MCP grant/scope across queued jobs and follow-up
jobs, revalidates before execution/model consumption/publication, and cancels
only originating work on revocation. Provenance lives in existing JSON
columns (no model changes): ``Job.payload["mcp"]``, ``ReviewRun.progress["mcp"]``,
``SecurityScan.progress["mcp"]``, ``Finding.sync_intent["mcp"]`` and
``SecurityFinding.issue["mcp"]``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import select

execution_context: ContextVar[dict | None] = ContextVar("mcp_execution_context", default=None)

PROVENANCE_KEY = "mcp"
REVOKED_MESSAGE = "The MCP grant for this work was revoked. No further paid or GitHub actions will run."


def current_provenance() -> dict | None:
    current = execution_context.get()
    return dict(current) if isinstance(current, dict) and current.get("grant_id") else None


@contextmanager
def scoped(provenance: dict | None):
    token = execution_context.set(dict(provenance) if provenance else None)
    try:
        yield execution_context.get()
    finally:
        execution_context.reset(token)


def _clean(provenance: Any) -> dict | None:
    if not isinstance(provenance, dict):
        return None
    grant_id = provenance.get("grant_id")
    if not isinstance(grant_id, str) or not grant_id:
        return None
    cleaned: dict[str, Any] = {"grant_id": grant_id}
    for key in ("domain", "scope"):
        value = provenance.get(key)
        if value is not None:
            cleaned[key] = value
    return cleaned


def bind_payload(payload: dict | None) -> dict:
    payload = dict(payload or {})
    current = _clean(current_provenance())
    if current is not None:
        payload[PROVENANCE_KEY] = current
    return payload


def with_provenance(payload: dict | None, provenance: Any) -> dict:
    payload = dict(payload or {})
    cleaned = _clean(provenance)
    if cleaned is not None and PROVENANCE_KEY not in payload:
        payload[PROVENANCE_KEY] = cleaned
    return payload


def bind_intent(payload: dict) -> dict:
    """An explicit user retry replaces the previous command's authorization."""
    payload = {key: value for key, value in payload.items() if key != PROVENANCE_KEY}
    return with_provenance(payload, current_provenance())


def provenance_from_payload(payload: Any) -> dict | None:
    if isinstance(payload, dict):
        return _clean(payload.get(PROVENANCE_KEY))
    return None


def provenance_from_run(run: Any) -> dict | None:
    progress = getattr(run, "progress", None)
    if isinstance(progress, dict):
        return _clean(progress.get(PROVENANCE_KEY))
    return None


def provenance_from_scan(scan: Any) -> dict | None:
    return provenance_from_run(scan)


def attach_progress(progress: Any) -> dict:
    progress = dict(progress or {})
    current = _clean(current_provenance())
    if current is not None:
        progress[PROVENANCE_KEY] = current
    return progress


def validate(db, provenance: Any, *, domain=None, scope=None, repository_id=None, lock=False):
    from .mcp_auth import ensure_grant

    cleaned = _clean(provenance)
    if cleaned is None:
        return None, None
    return ensure_grant(
        db,
        cleaned["grant_id"],
        domain=domain if domain is not None else cleaned.get("domain"),
        scope=scope if scope is not None else cleaned.get("scope"),
        repository_id=repository_id,
        lock=lock,
    )


def revoked(db, provenance: Any, **kwargs) -> bool:
    from fastapi import HTTPException

    if _clean(provenance) is None:
        return False
    try:
        validate(db, provenance, **kwargs)
    except HTTPException:
        return True
    return False


def run_revoked(db, run, **kwargs) -> bool:
    return revoked(db, provenance_from_run(run), **kwargs)


def scan_revoked(db, scan, **kwargs) -> bool:
    return revoked(db, provenance_from_scan(scan), **kwargs)


def job_provenance(db, job) -> dict | None:
    """Effective provenance: job payload first, else associated run/scan/finding."""
    from .models import Finding, ReviewRun, SecurityFinding, SecurityScan

    direct = provenance_from_payload(getattr(job, "payload", None))
    if direct is not None:
        return direct
    payload = getattr(job, "payload", None) or {}
    run_id = payload.get("run_id")
    if isinstance(run_id, str):
        run = db.get(ReviewRun, run_id)
        if run is not None:
            found = provenance_from_run(run)
            if found is not None:
                return found
    scan_id = payload.get("scan_id")
    if isinstance(scan_id, str):
        scan = db.get(SecurityScan, scan_id)
        if scan is not None:
            found = provenance_from_scan(scan)
            if found is not None:
                return found
    finding_id = payload.get("finding_id")
    if isinstance(finding_id, str):
        finding = db.get(Finding, finding_id)
        if finding is not None and isinstance(finding.sync_intent, dict):
            found = _clean(finding.sync_intent.get(PROVENANCE_KEY))
            if found is not None:
                return found
        security = db.get(SecurityFinding, finding_id)
        if security is not None and isinstance(security.issue, dict):
            found = _clean(security.issue.get(PROVENANCE_KEY))
            if found is not None:
                return found
    pr_id = payload.get("pr_id")
    if isinstance(pr_id, str) and job.kind in {"sync_summary", "generate_summary"}:
        from . import pr_summary as _summaries

        run = _summaries.latest_run(db, pr_id)
        if run is not None:
            found = provenance_from_run(run)
            if found is not None:
                return found
    return None


def job_revoked(db, job, **kwargs) -> bool:
    return revoked(db, job_provenance(db, job), **kwargs)


def repository_id_for_job(db, job) -> int | None:
    from .models import PullRequest, ReviewRun, SecurityScan

    payload = getattr(job, "payload", None) or {}
    if isinstance(payload.get("repository_id"), int):
        return payload["repository_id"]
    run_id = payload.get("run_id")
    if isinstance(run_id, str):
        run = db.get(ReviewRun, run_id)
        if run is not None:
            pr = db.get(PullRequest, run.pr_id)
            if pr is not None:
                return pr.repository_id
    scan_id = payload.get("scan_id")
    if isinstance(scan_id, str):
        scan = db.get(SecurityScan, scan_id)
        if scan is not None:
            return scan.repository_id
    return None


def cancel_origin(db, job) -> bool:
    """Cancel the single run/scan that queued this job when its grant is revoked.

    Runs inside the worker's claim transaction after the job row lock, so the
    lock order (job, then run/scan) matches the existing worker paths.
    """
    from .models import ReviewRun, SecurityScan

    provenance = job_provenance(db, job)
    grant_id = provenance["grant_id"] if provenance else None
    if grant_id is None:
        return False
    payload = getattr(job, "payload", None) or {}
    run_id = payload.get("run_id")
    if isinstance(run_id, str):
        run = db.get(ReviewRun, run_id)
        origin = provenance_from_run(run)
        if run is not None and origin is not None and origin["grant_id"] == grant_id:
            if run.status in ("queued", "running", "blocked") and not run.cancel_requested:
                from .reviews import cancel_review

                cancel_review(db, run)
            return True
    scan_id = payload.get("scan_id")
    if isinstance(scan_id, str):
        scan = db.get(SecurityScan, scan_id)
        origin = provenance_from_scan(scan)
        if scan is not None and origin is not None and origin["grant_id"] == grant_id:
            if scan.status in ("queued", "running", "blocked") and not scan.cancel_requested:
                from . import repository_security

                repository_security.cancel(db, scan, reason=REVOKED_MESSAGE)
            return True
    return False


def revoke_work(db, grant_id: str) -> dict:
    """Cancel only work originating from ``grant_id``; preserve everything else.

    Locks jobs before runs/scans, matching the worker claim order (job first,
    then run/scan), with ordered row locks like the existing cancellation paths.
    """
    from .db import now
    from .models import Finding, Job, ReviewRun, SecurityFinding, SecurityScan

    counts = {"runs": 0, "scans": 0, "jobs": 0, "findings": 0, "issues": 0}
    for job in db.scalars(
        select(Job)
        .where(Job.status.in_(["queued", "running"]),
               Job.payload[PROVENANCE_KEY]["grant_id"].as_string() == grant_id)
        .order_by(Job.id)
        .with_for_update()
    ):
        job.status, job.error, job.lease_until, job.dispatched_at = "failed", REVOKED_MESSAGE, None, None
        job.available_at = now()
        counts["jobs"] += 1
    for run in db.scalars(
        select(ReviewRun)
        .where(ReviewRun.status.in_(["queued", "running", "blocked"]),
               ReviewRun.progress[PROVENANCE_KEY]["grant_id"].as_string() == grant_id)
        .order_by(ReviewRun.id)
        .with_for_update()
    ):
        from .reviews import cancel_review

        cancel_review(db, run)
        counts["runs"] += 1
    for scan in db.scalars(
        select(SecurityScan)
        .where(SecurityScan.status.in_(["queued", "running", "blocked"]),
               SecurityScan.progress[PROVENANCE_KEY]["grant_id"].as_string() == grant_id)
        .order_by(SecurityScan.id)
        .with_for_update()
    ):
        from . import repository_security

        repository_security.cancel(db, scan, reason=REVOKED_MESSAGE)
        counts["scans"] += 1
    for finding in db.scalars(
        select(Finding)
        .where(Finding.sync_intent[PROVENANCE_KEY]["grant_id"].as_string() == grant_id)
        .order_by(Finding.id)
        .with_for_update()
    ):
        if not finding.sync_error:
            finding.sync_error = REVOKED_MESSAGE
        counts["findings"] += 1
    for item in db.scalars(
        select(SecurityFinding)
        .where(SecurityFinding.issue[PROVENANCE_KEY]["grant_id"].as_string() == grant_id)
        .order_by(SecurityFinding.id)
        .with_for_update()
    ):
        issue = dict(item.issue or {})
        if issue.get("sync_status") != "failed" or not issue.get("error"):
            issue = {**issue, "sync_status": "failed", "error": REVOKED_MESSAGE}
            item.issue = issue
        counts["issues"] += 1
    return counts
