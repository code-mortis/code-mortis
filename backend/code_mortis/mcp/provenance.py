"""MCP grant provenance for durable worker lifecycle.

Preserves the originating MCP grant/scope across queued jobs and follow-up
jobs, revalidates before execution/model consumption/publication, and cancels
only originating work on revocation. Provenance lives in existing JSON
columns (no model changes): ``Job.payload["mcp"]``, ``ReviewRun.progress["mcp"]``,
``SecurityScan.progress["mcp"]``, ``Finding.sync_intent["mcp"]`` and
``SecurityFinding.issue["mcp"]``.

The bind helpers below also propagate personal API token (``pat``) origin
from its own execution context. MCP semantics are unchanged: the ``mcp``
record, validation, revocation and cleanup behave exactly as before, and a
valid MCP grant never implies PAT authorization (or vice versa).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import or_, select

from code_mortis.api_tokens import provenance as pat_provenance

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
    pat = pat_provenance.current_provenance()
    if pat is not None:
        payload[pat_provenance.PROVENANCE_KEY] = pat
    return payload


def with_provenance(payload: dict | None, provenance: Any) -> dict:
    payload = dict(payload or {})
    cleaned = _clean(provenance)
    if cleaned is not None and PROVENANCE_KEY not in payload:
        payload[PROVENANCE_KEY] = cleaned
    pat = pat_provenance.current_provenance()
    if pat is not None and pat_provenance.PROVENANCE_KEY not in payload:
        payload[pat_provenance.PROVENANCE_KEY] = pat
    return payload


def bind_intent(payload: dict) -> dict:
    """An explicit user retry replaces the previous command's authorization."""
    payload = {key: value for key, value in payload.items() if key != PROVENANCE_KEY}
    payload = with_provenance(payload, current_provenance())
    return pat_provenance.bind_intent(payload)


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
    pat = pat_provenance.current_provenance()
    if pat is not None:
        progress[pat_provenance.PROVENANCE_KEY] = pat
    return progress


def validate(db, provenance: Any, *, domain=None, scope=None, repository_id=None, lock=False):
    from code_mortis.mcp.auth import ensure_grant

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
    from code_mortis.models import Finding, ReviewRun, SecurityFinding, SecurityScan

    direct = provenance_from_payload(getattr(job, "payload", None))
    if direct is not None:
        return direct
    payload = getattr(job, "payload", None) or {}
    if job.kind in {"generate_summary", "sync_summary"} and payload.get("summary_attempt_version") == 1:
        return None
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
        from code_mortis.reviews import summary as _summaries

        run = _summaries.latest_run(db, pr_id)
        if run is not None:
            found = provenance_from_run(run)
            if found is not None:
                return found
    return None


def job_revoked(db, job, **kwargs) -> bool:
    payload = getattr(job, "payload", None) or {}
    if (job.kind in {"generate_summary", "sync_summary"} and payload.get("summary_attempt_version") == 1
            and PROVENANCE_KEY in payload and _clean(payload[PROVENANCE_KEY]) is None):
        return True
    return revoked(db, job_provenance(db, job), **kwargs)


def repository_id_for_job(db, job) -> int | None:
    from code_mortis.models import PullRequest, ReviewRun, SecurityScan

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
    from code_mortis.models import ReviewRun, SecurityScan

    provenance = job_provenance(db, job)
    if provenance is None:
        return False

    def same_group(origin):
        return origin is not None and all(origin.get(key) == provenance.get(key) for key in ("grant_id", "domain"))

    payload = getattr(job, "payload", None) or {}
    run_id = payload.get("run_id")
    if isinstance(run_id, str):
        run = db.get(ReviewRun, run_id)
        origin = provenance_from_run(run)
        if run is not None and same_group(origin):
            if run.status in ("queued", "running", "blocked") and not run.cancel_requested:
                from code_mortis.reviews.service import cancel_review

                cancel_review(db, run)
            return True
    scan_id = payload.get("scan_id")
    if isinstance(scan_id, str):
        scan = db.get(SecurityScan, scan_id)
        origin = provenance_from_scan(scan)
        if scan is not None and same_group(origin):
            if scan.status in ("queued", "running", "blocked") and not scan.cancel_requested:
                from code_mortis.security_scans import service as repository_security

                repository_security.cancel(db, scan, reason=REVOKED_MESSAGE)
            return True
    return False


def revoke_work(db, grant_id: str, *, groups: set[str] | None = None) -> dict:
    """Cancel work originating from the grant and, optionally, the given tool groups.

    Locks jobs before runs/scans, matching the worker claim order (job first,
    then run/scan), with ordered row locks like the existing cancellation paths.
    """
    from code_mortis.core.analysis import cancel_tasks_in_transaction
    from code_mortis.core.db import now
    from code_mortis.models import Finding, Job, ReviewRun, SecurityFinding, SecurityScan

    def matches(record):
        origin = record[PROVENANCE_KEY]
        grant_matches = origin["grant_id"].as_string() == grant_id
        return grant_matches if groups is None else grant_matches & origin["domain"].as_string().in_(groups)

    # Follow-up jobs may inherit their authority from the saved object instead
    # of carrying it in their payload. Check the effective origin after locking.
    job_matches = or_(
        matches(Job.payload),
        (Job.payload[PROVENANCE_KEY]["grant_id"].as_string().is_(None)) & or_(
            select(ReviewRun.id).where(
                matches(ReviewRun.progress),
                or_(
                    ReviewRun.id == Job.payload["run_id"].as_string(),
                    (Job.kind == "sync_summary") & (ReviewRun.pr_id == Job.payload["pr_id"].as_string()),
                ),
            ).exists(),
            select(SecurityScan.id).where(
                matches(SecurityScan.progress), SecurityScan.id == Job.payload["scan_id"].as_string(),
            ).exists(),
            select(Finding.id).where(
                matches(Finding.sync_intent), Finding.id == Job.payload["finding_id"].as_string(),
            ).exists(),
            select(SecurityFinding.id).where(
                matches(SecurityFinding.issue), SecurityFinding.id == Job.payload["finding_id"].as_string(),
            ).exists(),
        ),
    )
    counts = {"runs": 0, "scans": 0, "jobs": 0, "findings": 0, "issues": 0}
    for job in db.scalars(
        select(Job)
        .where(Job.status.in_(["queued", "running"]), job_matches)
        .order_by(Job.id)
        .with_for_update()
    ):
        origin = job_provenance(db, job)
        if not origin or origin["grant_id"] != grant_id or (groups is not None and origin.get("domain") not in groups):
            continue
        cancel_tasks_in_transaction(db, job.id)
        job.status, job.error, job.lease_until, job.dispatched_at = "failed", REVOKED_MESSAGE, None, None
        job.available_at = now()
        if job.kind == "generate_summary":
            from code_mortis.reviews.summary import fail_retry

            fail_retry(db, job, REVOKED_MESSAGE)
        counts["jobs"] += 1
    for run in db.scalars(
        select(ReviewRun)
        .where(ReviewRun.status.in_(["queued", "running", "blocked"]),
               matches(ReviewRun.progress))
        .order_by(ReviewRun.id)
        .with_for_update()
    ):
        from code_mortis.reviews.service import cancel_review

        cancel_review(db, run)
        counts["runs"] += 1
    for scan in db.scalars(
        select(SecurityScan)
        .where(SecurityScan.status.in_(["queued", "running", "blocked"]),
               matches(SecurityScan.progress))
        .order_by(SecurityScan.id)
        .with_for_update()
    ):
        from code_mortis.security_scans import service as repository_security

        repository_security.cancel(db, scan, reason=REVOKED_MESSAGE)
        counts["scans"] += 1
    for finding in db.scalars(
        select(Finding)
        .where(Finding.desired_status.is_not(None), Finding.sync_error.is_(None), matches(Finding.sync_intent))
        .order_by(Finding.id)
        .with_for_update()
    ):
        finding.sync_error = REVOKED_MESSAGE
        counts["findings"] += 1
    for item in db.scalars(
        select(SecurityFinding)
        .where(matches(SecurityFinding.issue), SecurityFinding.issue["sync_status"].as_string().is_distinct_from("synced"))
        .order_by(SecurityFinding.id)
        .with_for_update()
    ):
        issue = dict(item.issue or {})
        if issue.get("sync_status") == "failed" and issue.get("error"):
            continue
        item.issue = {**issue, "sync_status": "failed", "error": REVOKED_MESSAGE}
        counts["issues"] += 1
    return counts
