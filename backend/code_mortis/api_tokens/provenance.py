"""Personal API token origin for durable worker lifecycle.

Mirrors the MCP provenance protocol with a separate ``pat`` origin key so the
two credential families stay distinguishable. The token row is authoritative;
the stored record is only evidence and never grants access on its own.

Origin lives in existing JSON columns (no model changes):
``Job.payload["pat"]``, ``ReviewRun.progress["pat"]``,
``SecurityScan.progress["pat"]``, ``Finding.sync_intent["pat"]`` and
``SecurityFinding.issue["pat"]``.

Differences from MCP provenance (all deliberate):
- a present-but-null/malformed ``pat`` marker denies work instead of falling
  back to system execution; only a wholly absent key means system work;
- ``scoped`` raises on non-null malformed input instead of silently clearing;
- records require a valid token id, positive int owner id and known scope;
- revalidation goes through ``api_tokens.service.authorize`` so expiry,
  revocation, membership, scopes, repository selection and current GitHub
  permission are all rechecked at every gate, with run/write scopes implying
  push revalidation and job families restricted to initiating scopes;
- explicit user retries adopt the initiating session via ``adopt_intent`` /
  ``adopt_progress``; background retries preserve the stored origin;
- ``bind_intent`` only replaces from ambient PAT context and is kept for
  background-compatible paths.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import select

from code_mortis.api_tokens import policy

execution_context: ContextVar[dict | None] = ContextVar("execution_context", default=None)

PROVENANCE_KEY = "pat"
REVOKED_MESSAGE = ("The personal API token for this work was revoked or expired. "
                   "No further paid or GitHub actions will run.")

_ABSENT = object()


class TransientAuthorizationError(Exception):
    """Temporary authorization outage (rate limit, 5xx, network). Not revocation.

    Gates must let this propagate into existing job retry handling instead of
    labelling the outage as revoked. All errors still prevent execution.
    """


_TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

try:  # Classification only; absence just means this failure mode cannot occur.
    from redis.exceptions import RedisError as _RedisError

    _TRANSIENT_ERRORS: tuple = (TimeoutError, ConnectionError, OSError, _RedisError)
except ImportError:  # pragma: no cover
    _TRANSIENT_ERRORS = (TimeoutError, ConnectionError, OSError)


def _is_transient(exc: BaseException) -> bool:
    if getattr(exc, "retry_after", 0) > 0:
        return True
    status = getattr(exc, "status_code", getattr(exc, "status", None))
    if isinstance(status, bool):
        return False
    if isinstance(status, int):
        return status in _TRANSIENT_STATUSES
    return isinstance(exc, _TRANSIENT_ERRORS)

_WRITE_SCOPES = frozenset({"reviews:run", "reviews:write", "security:run", "security:write"})
_REVIEW_RUN_SCOPES = frozenset({"reviews:run"})
REVIEW_SYNC_SCOPES = frozenset({"reviews:run", "reviews:write"})
_SECURITY_RUN_SCOPES = frozenset({"security:run"})
# Exact initiating scope per job kind. reviews:run starts paid inference
# (review, assessment, verification, model summaries); reviews:write resolves
# and syncs but never starts it. Kinds absent here are session-only, admin or
# internal and never execute on PAT authority (the internal cleanup kind stays
# exempt at admission instead).
_JOB_SCOPES: dict[str, frozenset] = {
    "review": _REVIEW_RUN_SCOPES,
    "publish": _REVIEW_RUN_SCOPES,
    "generate_summary": _REVIEW_RUN_SCOPES,
    "finalize_review": _REVIEW_RUN_SCOPES,
    # Pipeline stage kinds (reviews/pipeline.py KINDS).
    "verify_fixes": _REVIEW_RUN_SCOPES,
    "summarize_review": _REVIEW_RUN_SCOPES,
    "assess_candidates": _REVIEW_RUN_SCOPES,
    "sync_pr": _REVIEW_RUN_SCOPES,
    "scan_repository": _REVIEW_RUN_SCOPES,
    "resolve": REVIEW_SYNC_SCOPES,
    "sync_summary": REVIEW_SYNC_SCOPES,
    "sync_check": REVIEW_SYNC_SCOPES,
    "security_scan": _SECURITY_RUN_SCOPES,
    "security_refresh": _SECURITY_RUN_SCOPES,
    "security_issue_publish": frozenset({"security:run", "security:write"}),
    "security_issue_sync": frozenset({"security:run", "security:write"}),
}


def current_provenance() -> dict | None:
    current = execution_context.get()
    return dict(current) if isinstance(current, dict) and _clean(current) else None


@contextmanager
def scoped(provenance: dict | None):
    if provenance is None:
        cleaned = None
    else:
        cleaned = _clean(provenance)
        if cleaned is None:
            raise ValueError("Personal API token origin is malformed.")
    token = execution_context.set(cleaned)
    try:
        yield execution_context.get()
    finally:
        execution_context.reset(token)


def _clean(provenance: Any) -> dict | None:
    if not isinstance(provenance, dict):
        return None
    token_id = provenance.get("token_id")
    if not isinstance(token_id, str) or not token_id:
        return None
    user_id = provenance.get("user_id")
    if type(user_id) is not int or user_id <= 0:
        return None
    scope = provenance.get("scope")
    if scope is not None and scope not in policy.SCOPES:
        return None
    cleaned: dict[str, Any] = {"token_id": token_id, "user_id": user_id}
    if scope is not None:
        cleaned["scope"] = scope
    repository_id = provenance.get("repository_id")
    if repository_id is not None:
        if type(repository_id) is not int or repository_id <= 0:
            return None
        cleaned["repository_id"] = repository_id
    return cleaned


def bind_payload(payload: dict | None) -> dict:
    payload = dict(payload or {})
    current = current_provenance()
    if current is not None:
        payload[PROVENANCE_KEY] = current
    return payload


def attach_progress(progress: Any) -> dict:
    progress = dict(progress or {})
    current = current_provenance()
    if current is not None:
        progress[PROVENANCE_KEY] = current
    return progress


def bind_intent(intent: dict) -> dict:
    """A background retry preserves the stored origin; explicit adoption uses adopt_intent."""
    intent = dict(intent or {})
    current = current_provenance()
    if current is None:
        return intent
    intent = {key: value for key, value in intent.items() if key != PROVENANCE_KEY}
    intent[PROVENANCE_KEY] = current
    return intent


def adopt_intent(intent: dict) -> dict:
    """Explicit user retry adopts the initiating session: the current PAT replaces
    the stored origin, while a session without PAT clears it. Never use for
    background retries, which must preserve the stored origin."""
    intent = dict(intent or {})
    current = current_provenance()
    if current is None:
        intent.pop(PROVENANCE_KEY, None)
        return intent
    intent[PROVENANCE_KEY] = current
    return intent


def adopt_progress(progress: Any) -> dict:
    """Explicit user retry adopts the stored run/scan origin like adopt_intent."""
    progress = dict(progress or {})
    current = current_provenance()
    if current is None:
        progress.pop(PROVENANCE_KEY, None)
        return progress
    progress[PROVENANCE_KEY] = current
    return progress


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


def origin_for(payload: Any, fallback: Any = None) -> Any:
    """Raw payload origin when the key is present (even null/malformed), else fallback.

    The fallback may be a run/scan (uses ``.progress``) or a plain intent
    dict. Presence stops the fallback so a tampered payload marker can never
    be laundered into an older valid origin or system work. Returns _ABSENT
    only when neither level carries the key; present null is returned as None
    so gates deny it instead of treating it as absent.
    """
    if isinstance(payload, dict) and PROVENANCE_KEY in payload:
        return payload.get(PROVENANCE_KEY)
    if isinstance(fallback, dict):
        if PROVENANCE_KEY in fallback:
            return fallback.get(PROVENANCE_KEY)
        return _ABSENT
    progress = getattr(fallback, "progress", None)
    if isinstance(progress, dict) and PROVENANCE_KEY in progress:
        return progress.get(PROVENANCE_KEY)
    return _ABSENT


def validate(db, provenance: Any, *, scope: str | None = None, repository_id: int | None = None,
             write: bool = False, lock: bool = False, network: bool = True):
    """Revalidate the stored origin against the live token row.

    The scope snapshot is only evidence: a missing or unknown scope denies.
    The recorded owner must still own the token row. With network=False only
    token/membership/scope checks run (sweeps); no GitHub request is made.
    """
    from code_mortis.api_tokens import service

    cleaned = _clean(provenance)
    if cleaned is None:
        from fastapi import HTTPException

        raise HTTPException(401, "This API token origin is invalid.",
                            headers={"WWW-Authenticate": "Bearer"})
    required = scope if scope is not None else cleaned.get("scope")
    if required not in policy.SCOPES:
        from fastapi import HTTPException

        raise HTTPException(401, "This API token origin is invalid.",
                            headers={"WWW-Authenticate": "Bearer"})
    repo = repository_id if repository_id is not None else cleaned.get("repository_id")
    if not network:
        repo, write = None, False
    user, token = service.authorize(
        db,
        cleaned["token_id"],
        scope=required,
        repository_id=repo,
        write=write,
        lock=lock,
    )
    if token.user_id != cleaned["user_id"]:
        from fastapi import HTTPException

        raise HTTPException(401, "This API token origin is invalid.",
                            headers={"WWW-Authenticate": "Bearer"})
    return user, token


def revoked(db, provenance: Any, *, repository_id: int | None = None, scope: str | None = None,
            write: bool | None = None, network: bool = True) -> bool:
    """Fail closed: only a verifiably live origin allows work.

    _ABSENT (key nowhere present) means system work and allows. Present null
    or malformed records deny. Run/write scopes imply current GitHub push
    revalidation whenever a repository is known, unless write is passed
    explicitly.
    """
    if provenance is _ABSENT:
        return False
    cleaned = _clean(provenance)
    if cleaned is None:
        return True
    required = scope if scope is not None else cleaned.get("scope")
    effective_write = write if write is not None else required in _WRITE_SCOPES
    try:
        validate(db, cleaned, repository_id=repository_id, scope=required,
                 write=bool(effective_write), network=network)
    except TransientAuthorizationError:
        raise
    except Exception as exc:
        if _is_transient(exc):
            raise TransientAuthorizationError(str(exc) or "Token authorization is temporarily unavailable.") from exc
        return True
    return False


def mapping_revoked(db, mapping: Any, **kwargs) -> bool:
    """Deny when the mapping carries a present-but-dead origin; allow when absent."""
    if not isinstance(mapping, dict) or PROVENANCE_KEY not in mapping:
        return False
    return revoked(db, mapping.get(PROVENANCE_KEY), **kwargs)


def run_revoked(db, run, *, allowed: frozenset = _REVIEW_RUN_SCOPES, **kwargs) -> bool:
    progress = getattr(run, "progress", None)
    if not isinstance(progress, dict) or PROVENANCE_KEY not in progress:
        return False
    raw = progress.get(PROVENANCE_KEY)
    if raw is None or _scope_denied(raw, allowed):
        return True
    return revoked(db, raw, **kwargs)


def scan_revoked(db, scan, *, allowed: frozenset = _SECURITY_RUN_SCOPES, **kwargs) -> bool:
    progress = getattr(scan, "progress", None)
    if not isinstance(progress, dict) or PROVENANCE_KEY not in progress:
        return False
    raw = progress.get(PROVENANCE_KEY)
    if raw is None or _scope_denied(raw, allowed):
        return True
    return revoked(db, raw, **kwargs)


def _scope_denied(raw: Any, allowed: frozenset | None) -> bool:
    """A stored origin outside the allowed initiating scopes denies."""
    if allowed is None:
        allowed = frozenset()
    cleaned = _clean(raw)
    return cleaned is None or cleaned.get("scope") not in allowed


def _kind_denied(raw: Any, kind: str | None) -> bool:
    """PAT origins are rejected on session-only/admin/unknown job kinds.

    Only the kinds below may execute on PAT authority, each with its
    initiating scopes (reviews:run starts paid inference; reviews:write
    resolves and syncs but never starts it).
    """
    allowed = _JOB_SCOPES.get(kind)
    if allowed is None:
        return True
    return _scope_denied(raw, allowed)


def _effective_raw(db, job) -> tuple[bool, Any]:
    """First present origin wins: payload, then run/scan/finding/PR summary run.

    Presence (even malformed) stops the fallback so a tampered marker can
    never be laundered into system work.
    """
    from code_mortis.models import Finding, ReviewRun, SecurityFinding, SecurityScan

    payload = getattr(job, "payload", None) or {}
    if isinstance(payload, dict) and PROVENANCE_KEY in payload:
        return True, payload.get(PROVENANCE_KEY)
    if job.kind in {"generate_summary", "sync_summary"} and payload.get("summary_attempt_version") == 1:
        # Explicit regeneration owns its new authorization, separate from historical review origin.
        return False, None
    run_id = payload.get("run_id")
    if isinstance(run_id, str):
        run = db.get(ReviewRun, run_id)
        if run is not None and isinstance(run.progress, dict) and PROVENANCE_KEY in run.progress:
            return True, run.progress.get(PROVENANCE_KEY)
    scan_id = payload.get("scan_id")
    if isinstance(scan_id, str):
        scan = db.get(SecurityScan, scan_id)
        if scan is not None and isinstance(scan.progress, dict) and PROVENANCE_KEY in scan.progress:
            return True, scan.progress.get(PROVENANCE_KEY)
    finding_id = payload.get("finding_id")
    if isinstance(finding_id, str):
        finding = db.get(Finding, finding_id)
        if finding is not None and isinstance(finding.sync_intent, dict) and PROVENANCE_KEY in finding.sync_intent:
            return True, finding.sync_intent.get(PROVENANCE_KEY)
        security = db.get(SecurityFinding, finding_id)
        if security is not None and isinstance(security.issue, dict) and PROVENANCE_KEY in security.issue:
            return True, security.issue.get(PROVENANCE_KEY)
    pr_id = payload.get("pr_id")
    if isinstance(pr_id, str) and job.kind in {"sync_summary", "generate_summary"}:
        from code_mortis.reviews import summary as _summaries

        run = _summaries.latest_run(db, pr_id)
        if run is not None and isinstance(run.progress, dict) and PROVENANCE_KEY in run.progress:
            return True, run.progress.get(PROVENANCE_KEY)
    return False, None


def job_revoked(db, job, **kwargs) -> bool:
    found, raw = _effective_raw(db, job)
    if not found:
        return False
    if raw is None or _kind_denied(raw, getattr(job, "kind", None)):
        return True
    return revoked(db, raw, **kwargs)


def job_provenance(db, job) -> dict | None:
    """Effective cleaned origin for worker execution: payload first, then
    run/scan/intent fallbacks. None when absent or malformed (malformed jobs
    are denied at admission before execution)."""
    found, raw = _effective_raw(db, job)
    if not found:
        return None
    return _clean(raw)


def closeout_allowed(db, job) -> bool:
    """Narrow terminal-only exemption for sync_check closeout.

    After revocation/expiry a queued sync_check may still post its
    cancelled/failed conclusion to an EXISTING check run, never create a new
    check or publish new content. Requires a terminal cancelled/superseded run
    with an existing check id and a stored origin matching the job's origin.
    """
    if getattr(job, "kind", None) != "sync_check":
        return False
    from code_mortis.models import ReviewRun

    payload = getattr(job, "payload", None) or {}
    if not isinstance(payload, dict) or PROVENANCE_KEY not in payload:
        return False
    job_origin = _clean(payload.get(PROVENANCE_KEY))
    if job_origin is None:
        return False
    run_id = payload.get("run_id")
    run = db.get(ReviewRun, run_id) if isinstance(run_id, str) else None
    if run is None or not run.check_id:
        return False
    if run.status not in {"cancelled", "superseded"}:
        return False
    run_origin = _clean((run.progress or {}).get(PROVENANCE_KEY))
    return run_origin is not None and run_origin == job_origin


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
    """Cancel the single run/scan that queued this job when its token is dead.

    Runs inside the worker's claim transaction after the job row lock, so the
    lock order (job, then run/scan) matches the existing worker paths.
    """
    from code_mortis.models import ReviewRun, SecurityScan

    found, raw = _effective_raw(db, job)
    token_id = _clean(raw)["token_id"] if found and _clean(raw) else None
    if token_id is None:
        return False
    payload = getattr(job, "payload", None) or {}
    run_id = payload.get("run_id")
    if isinstance(run_id, str):
        run = db.get(ReviewRun, run_id)
        origin = provenance_from_run(run)
        if run is not None and origin is not None and origin["token_id"] == token_id:
            if run.status in ("queued", "running", "blocked") and not run.cancel_requested:
                from code_mortis.reviews.service import cancel_review

                cancel_review(db, run)
            return True
    scan_id = payload.get("scan_id")
    if isinstance(scan_id, str):
        scan = db.get(SecurityScan, scan_id)
        origin = provenance_from_scan(scan)
        if scan is not None and origin is not None and origin["token_id"] == token_id:
            if scan.status in ("queued", "running", "blocked") and not scan.cancel_requested:
                from code_mortis.security_scans import service as repository_security

                repository_security.cancel(db, scan, reason=REVOKED_MESSAGE)
            return True
    return False


def revoke_work(db, token_id: str) -> dict:
    """Cancel only unfinished work originating from ``token_id``.

    Idempotent: reruns find nothing left to settle. Never starts model work
    or publishes new content. Locks jobs before runs/scans, matching the
    worker claim order, and never holds a row lock through a network request.
    """
    from code_mortis.api_tokens.service import REVOKE_CLEANUP_KIND
    from code_mortis.core.db import now
    from code_mortis.models import Finding, Job, ReviewRun, SecurityFinding, SecurityScan

    counts = {"runs": 0, "scans": 0, "jobs": 0, "findings": 0, "issues": 0}
    if not isinstance(token_id, str) or not token_id:
        return counts
    # sync_check jobs are never failed here: a cancelled run's closeout may
    # still post its terminal conclusion through the narrow exemption.
    for job in db.scalars(
        select(Job)
        .where(Job.status.in_(["queued", "running"]),
               Job.kind != REVOKE_CLEANUP_KIND,
               Job.kind != "sync_check",
               Job.payload[PROVENANCE_KEY]["token_id"].as_string() == token_id)
        .order_by(Job.id)
        .with_for_update()
    ):
        job.status, job.error, job.lease_until, job.dispatched_at = "failed", REVOKED_MESSAGE, None, None
        job.available_at = now()
        if job.kind == "generate_summary":
            from code_mortis.reviews.summary import fail_retry

            fail_retry(db, job, REVOKED_MESSAGE)
        counts["jobs"] += 1
    for run in db.scalars(
        select(ReviewRun)
        .where(ReviewRun.status.in_(["queued", "running", "blocked"]),
               ReviewRun.progress[PROVENANCE_KEY]["token_id"].as_string() == token_id)
        .order_by(ReviewRun.id)
        .with_for_update()
    ):
        from code_mortis.reviews.service import cancel_review

        cancel_review(db, run)
        counts["runs"] += 1
    for scan in db.scalars(
        select(SecurityScan)
        .where(SecurityScan.status.in_(["queued", "running", "blocked"]),
               SecurityScan.progress[PROVENANCE_KEY]["token_id"].as_string() == token_id)
        .order_by(SecurityScan.id)
        .with_for_update()
    ):
        from code_mortis.security_scans import service as repository_security

        repository_security.cancel(db, scan, reason=REVOKED_MESSAGE)
        counts["scans"] += 1
    for finding in db.scalars(
        select(Finding)
        .where(Finding.sync_intent[PROVENANCE_KEY]["token_id"].as_string() == token_id)
        .order_by(Finding.id)
        .with_for_update()
    ):
        if not finding.sync_error:
            finding.sync_error = REVOKED_MESSAGE
        counts["findings"] += 1
    for item in db.scalars(
        select(SecurityFinding)
        .where(SecurityFinding.issue[PROVENANCE_KEY]["token_id"].as_string() == token_id)
        .order_by(SecurityFinding.id)
        .with_for_update()
    ):
        issue = dict(item.issue or {})
        if issue.get("sync_status") != "failed" or not issue.get("error"):
            issue = {**issue, "sync_status": "failed", "error": REVOKED_MESSAGE}
            item.issue = issue
        counts["issues"] += 1
    return counts


def settle_blocked(db, *, limit: int = 100) -> dict:
    """Bounded no-network sweep: cancel blocked runs/scans whose token is dead.

    Expiry and revocation use the same gates as admission, but without a
    repository check so no GitHub request runs inside the sweep. Repository
    or permission drift is still caught at retry and publication gates.
    """
    from code_mortis.models import ReviewRun, SecurityScan

    counts = {"runs": 0, "scans": 0}
    runs = list(db.scalars(
        select(ReviewRun)
        .where(ReviewRun.status == "blocked",
               ReviewRun.progress[PROVENANCE_KEY]["token_id"].as_string().is_not(None))
        .order_by(ReviewRun.id)
        .limit(limit)
        .with_for_update()
    ))
    for run in runs:
        raw = (run.progress or {}).get(PROVENANCE_KEY)
        if PROVENANCE_KEY in (run.progress or {}) and revoked(db, raw, network=False):
            from code_mortis.reviews.service import cancel_review

            cancel_review(db, run)
            counts["runs"] += 1
    scans = list(db.scalars(
        select(SecurityScan)
        .where(SecurityScan.status == "blocked",
               SecurityScan.progress[PROVENANCE_KEY]["token_id"].as_string().is_not(None))
        .order_by(SecurityScan.id)
        .limit(limit)
        .with_for_update()
    ))
    for scan in scans:
        raw = (scan.progress or {}).get(PROVENANCE_KEY)
        if PROVENANCE_KEY in (scan.progress or {}) and revoked(db, raw, network=False):
            from code_mortis.security_scans import service as repository_security

            repository_security.cancel(db, scan, reason=REVOKED_MESSAGE)
            counts["scans"] += 1
    return counts
