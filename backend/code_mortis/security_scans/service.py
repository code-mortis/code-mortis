"""Repository security lifecycle. Absence never substitutes for fix evidence."""

import json
import re
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import nullcontext
from datetime import datetime, timedelta
from functools import partial
from itertools import groupby
from threading import Event, Lock
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import func, select

from code_mortis.api_tokens.provenance import TransientAuthorizationError
from code_mortis.core import source as git_source
from code_mortis.core.analysis import (
    EXECUTION_LIMITS,
    engine_snapshot,
    job_live,
    new_engine_snapshot,
    require_execution_engine,
)
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction, uid
from code_mortis.core.errors import ReviewStopped, RunnerBlocked
from code_mortis.core.security import clean_text, digest, safe_repo_name, unseal
from code_mortis.core.shared_execution import RETIRED_MESSAGE, is_shared
from code_mortis.integrations import github
from code_mortis.models import (
    Connection,
    ConnectionGrant,
    Installation,
    Job,
    Membership,
    Repository,
    ReviewRun,
    SecurityActivity,
    SecurityFinding,
    SecurityOccurrence,
    SecurityScan,
    User,
)
from code_mortis.reviews import assessment, verification
from code_mortis.reviews import diagnostics as result_diagnostics
from code_mortis.reviews.service import authorize_connection, connection_snapshot, enqueue
from code_mortis.schemas.common import ScanSettings, SecuritySummary
from code_mortis.schemas.security import SecurityScanOut
from code_mortis.security_scans import engine as security_engine
from code_mortis.security_scans import payload

ACTIVE = {"queued", "running", "blocked"}
STAGES = ("snapshot", "trivy", "analysis", "assessment", "verification", "report")
RATING_POLICY = "highest-unresolved-severity/v1"


def rolling_analysis(paths, concurrency, submit, stopped, stop_workers):
    paths, pending = iter(paths), {}
    admission_error, failed = None, False
    try:
        while True:
            while len(pending) < concurrency and not stop_workers.is_set():
                if stopped():
                    raise ReviewStopped("Security scan cancelled.")
                path = next(paths, None)
                if path is None:
                    break
                try:
                    pending[submit(path)] = path
                except Exception as exc:
                    if not pending:
                        raise
                    # A peer may have saved unknown usage before its failure future became ready.
                    admission_error = exc
                    stop_workers.set()
                    break
            if not pending:
                if admission_error and not failed:
                    raise admission_error
                return
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in ready:
                failed |= not future.cancelled() and future.exception() is not None
                yield pending.pop(future), future
    except BaseException:
        stop_workers.set()
        for future in pending:
            future.cancel()
        raise


def activity(db, action, *, scan=None, finding=None, actor=None, **detail):
    db.add(SecurityActivity(id=uid(), scan_id=scan.id if scan else None,
                           finding_id=finding.id if finding else None,
                           actor_id=actor.id if actor else None, action=action, detail=detail))


def authorize(db, repo, actor, connection, *, fresh=True):
    if not repo or not repo.active or not (installation := db.get(Installation, repo.installation_id)) or not installation.active:
        raise ValueError("The repository or GitHub installation is no longer available.")
    if not actor or not actor.enabled or not db.get(Membership, actor.id):
        raise ValueError("The user requesting this security scan no longer has access.")
    if not connection:
        raise ValueError("The selected AI connection no longer exists.")
    authorize_connection(db, connection, repo, "manual", actor, check_repository=False)
    if connection.kind != "api":
        if not db.get(ConnectionGrant, (connection.id, repo.id)):
            raise ValueError("Authorize this repository on your personal AI connection before scanning it.")
        if connection.auth_status != "connected":
            raise RunnerBlocked("authentication")
    return github.authorize_repo(db, actor, repo.full_name, write=True) if fresh else None


def branch_head(full_name, token, remote=None):
    safe_repo_name(full_name)
    remote = remote or github.request("GET", f"/repos/{full_name}", token)
    branch = remote.get("default_branch")
    if not isinstance(branch, str) or not branch or len(branch) > 255:
        raise ValueError("GitHub did not return a valid default branch.")
    commit = github.request("GET", f"/repos/{full_name}/commits/{quote(branch, safe='')}", token)
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("GitHub did not return a valid default-branch commit.")
    return {"default_branch": branch, "head_sha": sha, "checked_at": now().isoformat()}


def without_request_timeout(config):
    connection = config["connection"]
    return {**config, "connection": {**connection, "options": {
        key: value for key, value in connection["options"].items() if key != "timeout_sec"}}}


def checkpoint_config(config):
    return {**config, "limits": {key: value for key, value in config["limits"].items() if key != "concurrency"}}


def create_scan(db, repo, actor, body, remote=None, parent=None):
    connection = db.get(Connection, body.connection_id)
    if parent:
        require_execution_engine(parent.config)
    else:
        new_engine_snapshot(repo.settings)
    authorized = authorize(db, repo, actor, connection, fresh=remote is None)
    freshness = branch_head(repo.full_name, github.installation_token(db, repo.installation_id), remote or authorized)
    scan_settings = db.scalar(select(Repository.scan_settings).where(Repository.id == repo.id).with_for_update())
    existing = db.scalar(select(SecurityScan).where(SecurityScan.request_key == body.request_key))
    if existing:
        if existing.repository_id != repo.id or existing.actor_id != actor.id:
            raise HTTPException(409, "This scan request identity is already in use.")
        return existing
    active = db.scalar(select(SecurityScan).where(SecurityScan.repository_id == repo.id, SecurityScan.status.in_(ACTIVE)))
    if active:
        raise HTTPException(409, f"A security scan is already active for this repository: {active.id}.",
                            headers={"Location": f"/api/v1/security/scans/{active.id}", "X-Active-Security-Scan": active.id})
    if parent and (parent.branch != freshness["default_branch"] or parent.head_sha != freshness["head_sha"]):
        raise HTTPException(409, "The default branch changed. Start a new scan instead of retrying the old revision.")
    workflow = parent is None or parent.config.get("execution_limits") == EXECUTION_LIMITS
    limits = ScanSettings.model_validate(scan_settings).model_dump() if parent is None else {
        "concurrency": 1, **parent.config["limits"],
    }
    limits.update(body.model_dump(include=set(ScanSettings.model_fields), exclude_unset=True))
    if connection.kind != "api":
        limits["concurrency"] = 1
    config = {"connection": connection_snapshot(connection, workflow=workflow),
              "limits": limits,
              "policy": security_engine.POLICY, "rating_policy": RATING_POLICY,
              "trivy_version": settings().trivy_version, "language": repo.settings.get("language", "English")}
    config.update(engine_snapshot(parent.config) if parent else new_engine_snapshot(repo.settings))
    if workflow:
        config["execution_limits"] = EXECUTION_LIMITS
    if parent is None or "task_contract" in parent.config:
        config["task_contract"] = parent.config["task_contract"] if parent else security_engine.TASK_CONTRACT
    config["connection"]["model"] = body.model.strip() or (repo.settings.get("model") if repo.connection_id == connection.id else None) or connection.model
    if not config["connection"]["model"]:
        raise ValueError("Choose a model before starting a security scan.")
    if parent:
        # Retain inert metadata in immutable retry identities without restoring its adapter.
        if "ocr_version" in parent.config["connection"]:
            config["connection"]["ocr_version"] = parent.config["connection"]["ocr_version"]
        original = {**parent.config, **engine_snapshot(parent.config), "limits": {**parent.config["limits"],
                    "concurrency": parent.config["limits"].get("concurrency", 1) if workflow else config["limits"]["concurrency"]}}
        if without_request_timeout(config) != without_request_timeout(original):
            raise HTTPException(409, "Retry must use the original connection, model and limits. Start a new scan to change them.")
    run = SecurityScan(id=uid(), repository_id=repo.id, actor_id=actor.id, connection_id=connection.id,
                       parent_id=parent.id if parent else None, request_key=body.request_key,
                       branch=freshness["default_branch"], head_sha=freshness["head_sha"], config=config,
                       stages={name: {"status": "pending"} for name in STAGES},
                       progress={"freshness": freshness, "tasks": {}, "tokens": 0})
    from code_mortis.mcp import provenance as _mcp

    run.progress = _mcp.attach_progress(run.progress)
    db.add(run)
    db.flush()
    enqueue(db, "security_scan", {"scan_id": run.id})
    activity(db, "scan_queued", scan=run, actor=actor, commit=run.head_sha)
    return run


def summaries(db, repository_ids):
    ids = list(repository_ids)
    if not ids:
        return {}
    scans = (select(SecurityScan.repository_id, SecurityScan.id).where(SecurityScan.repository_id.in_(ids))
             .distinct(SecurityScan.repository_id)
             .order_by(SecurityScan.repository_id, SecurityScan.created_at.desc(), SecurityScan.id.desc()))
    latest = {row.repository_id: row for row in db.execute(scans.add_columns(
        SecurityScan.status, SecurityScan.created_at, SecurityScan.progress["freshness"].label("freshness"),
    ))}
    graded = {row.repository_id: row for row in db.execute(scans.add_columns(
        SecurityScan.head_sha, SecurityScan.branch, SecurityScan.finished_at,
    ).where(SecurityScan.grade.is_not(None)))}
    counts = {}
    for repo_id, severity, count in db.execute(select(SecurityFinding.repository_id, SecurityFinding.severity, func.count())
        .where(SecurityFinding.repository_id.in_(ids), SecurityFinding.state.in_(["open", "accepted_risk"]))
        .group_by(SecurityFinding.repository_id, SecurityFinding.severity)):
        counts.setdefault(repo_id, {})[severity] = count
    result = {}
    for identity in ids:
        scan, grade = latest.get(identity), graded.get(identity)
        data = SecuritySummary(counts=counts.get(identity, {}))
        if scan:
            fresh = scan.freshness or {}
            data.coverage = "complete" if scan.status == "completed" else "incomplete"
            data.latest_scan_id, data.latest_status, data.latest_at = scan.id, scan.status, scan.created_at
            for name in ("default_branch", "head_sha", "checked_at"):
                if fresh.get(name):
                    setattr(data, name, datetime.fromisoformat(fresh[name]) if name == "checked_at" else fresh[name])
            if grade:
                data.grade = grade_for(counts.get(identity, {}))
                data.grade_scan_id, data.grade_sha, data.graded_at = grade.id, grade.head_sha, grade.finished_at
            data.state = "current" if grade and scan.id == grade.id and scan.status == "completed" else "incomplete"
            checked = datetime.fromisoformat(fresh["checked_at"]) if fresh.get("checked_at") else None
            if fresh.get("error") or not checked or (now() - checked).total_seconds() > 600:
                data.freshness = "unavailable"
            elif grade and (grade.head_sha != fresh.get("head_sha") or grade.branch != fresh.get("default_branch")):
                data.freshness = "outdated"
            else:
                data.freshness = "current"
            if data.freshness != "current":
                data.state = data.freshness
        result[identity] = data
    return result


def grade_for(severities):
    severities = set(severities)
    if severities - security_engine.SEVERITIES:
        return None
    return next((grade for severity, grade in [("critical", "F"), ("high", "D"), ("medium", "C"), ("low", "B")]
                 if severity in severities), "A")


def validation_problem(path, feedback, *, task="analysis"):
    if (feedback or {}).get("kind") in {"context_limit", "redaction"}:
        return f"Security task withheld for {path}: {feedback.get('reason', 'Context could not be safely supplied')}."
    if (feedback or {}).get("kind") == "delivery":
        return f"AI result delivery failed for {path}: {feedback['reason']}" + (
            " " + result_diagnostics.explanation(feedback["transport"]) if feedback.get("transport") else "")
    if (feedback or {}).get("kind") == "citation_selection":
        path = feedback.get("path", path)
        return f"AI source selection is invalid in {path}: {feedback.get('reason', 'source reference or line range was not supplied')}."
    if (feedback or {}).get("reason") in {"quote is not verbatim text within the cited source lines", "invalid source line range"}:
        path = feedback.get("path", path)
        return f"AI citation mismatch in {path}: the quoted text or line numbers do not match the supplied source."
    label = "Proposed finding" if task == "assessment" else "Source analysis"
    return f"{label} could not be validated: {path}."


def redact_finding_text(db, finding, text):
    secrets = []
    scan = db.get(SecurityScan, finding.last_scan_id)
    connection = db.get(Connection, scan.connection_id) if scan and scan.connection_id else None
    if connection:
        credentials = json.loads(unseal(connection.secret))
        secrets = [credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values()]
    rules = [secret.evidence for secret in db.scalars(select(SecurityFinding).where(
        SecurityFinding.repository_id == finding.repository_id, SecurityFinding.kind == "secret"))]
    return security_engine.redact_secrets(text, secrets, rules)


def connection_busy(db, connection_id, *, scan_id=None, run_id=None, job_id=None):
    scans = select(SecurityScan.id).where(SecurityScan.connection_id == connection_id, SecurityScan.status == "running")
    if scan_id:
        scans = scans.where(SecurityScan.id != scan_id)
    reviews = select(ReviewRun.id).where(ReviewRun.connection_id == connection_id, ReviewRun.status == "running")
    if run_id:
        reviews = reviews.where(ReviewRun.id != run_id)
    jobs = select(Job).where(Job.kind.in_(["generate_background", "generate_summary"]), Job.status == "running")
    if job_id:
        jobs = jobs.where(Job.id != job_id)
    for job in db.scalars(jobs):
        payload = job.payload
        selected = payload.get("connection_id") or payload.get("config", {}).get("connection", {}).get("id")
        if selected == connection_id and (not run_id or payload.get("run_id") != run_id):
            return True
    return db.scalar(scans.limit(1)) is not None or db.scalar(reviews.limit(1)) is not None


def cancel(db, scan, actor=None, reason="Security scan cancelled."):
    if scan.status not in ACTIVE:
        return
    scan.cancel_requested, scan.status, scan.finished_at, scan.error = True, "cancelled", now(), reason
    interrupt_work(scan)
    scan.stages = {name: {**value, "status": "cancelled"} if value.get("status") in {"running", "pending"} else value
                   for name, value in scan.stages.items()}
    scan.report = report_data(db, scan, [reason])
    activity(db, "scan_cancelled", scan=scan, actor=actor, reason=reason)


def interrupt_work(scan):
    tasks = scan.progress.get("tasks", {})
    if any(task["status"] == "running" for task in tasks.values()):
        scan.progress = {**scan.progress, "tokens": None, "tasks": {
            key: {**task, "status": "interrupted"} if task["status"] == "running" else task for key, task in tasks.items()}}


def cancel_access(db, *, repository_ids=None, connection_id=None, actor_id=None, reason="Security scan access was revoked."):
    query = select(SecurityScan).where(SecurityScan.status.in_(ACTIVE))
    if repository_ids is not None:
        query = query.where(SecurityScan.repository_id.in_(repository_ids))
    if connection_id:
        query = query.where(SecurityScan.connection_id == connection_id)
    if actor_id:
        query = query.where(SecurityScan.actor_id == actor_id)
    for scan in db.scalars(query.order_by(SecurityScan.id).with_for_update()):
        cancel(db, scan, reason=reason)


def executing(scan, claim):
    return scan and scan.status == "running" and (claim is None or scan.progress.get("execution") == claim)


def live_claim(db, claim, *, lock=False):
    if claim is None:
        return True
    job_id, _, attempt = claim.rpartition(":")
    return attempt.isdigit() and job_live(db.get(Job, job_id, with_for_update=lock or None), int(attempt), now())


def checkpoint(scan_id, key, state, *, claim=None, **values):
    authorization_error = None
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if not executing(scan, claim) or scan.cancel_requested or not live_claim(db, claim):
            raise ReviewStopped("Security scan is no longer running.")
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        if _mcp.scan_revoked(db, scan, repository_id=scan.repository_id):
            scan.cancel_requested = True
            raise ReviewStopped(_mcp.REVOKED_MESSAGE)
        try:
            pat_denied = _pat.scan_revoked(db, scan, repository_id=scan.repository_id)
        except TransientAuthorizationError as exc:
            if state not in {"completed", "failed"}:
                raise
            # Save already consumed usage and results before retrying authorization.
            authorization_error, pat_denied = exc, False
        if pat_denied:
            scan.cancel_requested = True
            raise ReviewStopped(_pat.REVOKED_MESSAGE)
        tasks = dict(scan.progress.get("tasks", {}))
        # Pin context choices per task so retries preserve the durable request identity.
        for field in ("source_packing", "source_navigation"):
            if field in values and field in tasks.get(key, {}):
                values[field] = tasks[key][field]
        tasks[key] = {**tasks.get(key, {}), "status": state, **values}
        previous = scan.progress.get("tokens", 0)
        if values.get("tokens") is None and "tokens" in values:
            previous = None
        elif type(values.get("tokens")) is int and state in {"completed", "failed"}:
            previous = previous + values["tokens"] if previous is not None else None
        scan.progress = {**scan.progress, "tasks": tasks, "tokens": previous}
    if authorization_error is not None:
        raise authorization_error
    return tasks[key]


def stage(scan_id, name, status, *, claim=None, **values):
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if executing(scan, claim) and not scan.cancel_requested and live_claim(db, claim):
            previous = scan.stages.get(name, {})
            scan.stages = {**scan.stages, name: {**previous, "status": status, **values}}
            if previous.get("status") != status:
                activity(db, "scan_stage", scan=scan, stage=name, status=status)


def finding_snapshot(row):
    return {"id": row.id, "state": row.state, "kind": row.kind, "severity": row.severity,
            "title": row.title, "path": row.path, "version": row.version, "evidence": row.evidence,
            "rotation": row.rotation, "pending_close": row.issue.get("desired_state") == "closed"}


def assessment_keys(value, item, history=()):
    evidence = value.get("evidence", [])
    source_sha = item.sha
    if isinstance(evidence, dict):
        source_sha = evidence["source_sha"]
        ai_analysis = evidence.get("ai_analysis", {})
        citations = [*evidence.get("evidence", []), *evidence.get("locations", []),
                     *ai_analysis.get("evidence", []),
                     *(evidence.get("assessment") or {}).get("evidence", []),
                     *(ai_analysis.get("assessment") or {}).get("evidence", [])]
    else:
        citations = evidence
    paths = {value["path"], *(citation["path"] for citation in citations)}
    if source_sha != item.sha:
        for path in list(paths):
            try:
                paths.add(item.renamed(source_sha, path))
            except ValueError:
                pass  # Original paths and citation anchors still relate unavailable historical commits.
    connected = {("path", path) for path in paths} | {
        ("anchor", citation["quote"].strip()) for citation in citations if citation.get("quote", "").strip()}
    dependencies = [assessment_keys(old, item) for old in history]
    while True:
        expanded = connected.union(*(group for group in dependencies if connected & group))
        if expanded == connected:
            return connected
        connected = expanded


def assessment_history(proposal, history, item):
    connected = assessment_keys(proposal, item, history)
    return [old for old in history if connected & assessment_keys(old, item)]


def assessment_batch(rows, history, item, concurrency):
    keys = {row.id: assessment_keys(row.proposal, item, history) for row in rows}
    return assessment.independent_batch(rows, keys, concurrency)


def merged_observation(snapshot, evidence):
    previous = snapshot["evidence"]
    combined = combine_observation_sources({**previous, "ai_analysis": evidence}, evidence)
    return {**snapshot, "version": snapshot["version"] + 1, "evidence": combined}


def combine_observation_sources(evidence, previous):
    return {**evidence, "sources": sorted(set(previous.get("sources", [])) | set(evidence.get("sources", [])))}


def assessment_replay(db, sources, findings):
    if not findings or not any(key.startswith("assessment:") for source in sources for key in source.progress.get("tasks", {})):
        return {}
    changes, matching, recognized = {}, dict(findings), {}
    observations = {}
    source_ids = [scan.id for scan in sources]
    merges = {}
    for row in db.scalars(select(SecurityActivity).where(
        SecurityActivity.scan_id.in_(source_ids), SecurityActivity.action == "ai_evidence_merged",
    )):
        merges.setdefault((row.scan_id, row.finding_id), []).append(row.detail["evidence"])
    first_scans = dict(db.execute(select(SecurityFinding.id, SecurityFinding.first_scan_id).where(
        SecurityFinding.id.in_(list(findings)))).all())
    for row in db.scalars(select(SecurityOccurrence).where(
        SecurityOccurrence.scan_id.in_(source_ids), SecurityOccurrence.outcome == "present",
    )):
        observations.setdefault(row.scan_id, []).append(row)
    for source in sources:
        confirmed = [(int(key.split(":", 1)[1]), task["proposal"], task["result"])
                     for key, task in source.progress.get("tasks", {}).items()
                     if key.startswith("assessment:") and task.get("status") == "completed"
                     and task.get("proposal") and task.get("result", {}).get("verdict") == "confirmed"]
        for occurrence in observations.get(source.id, ()):
            identity, after = occurrence.finding_id, occurrence.snapshot
            current = matching.get(identity)
            merged = merges.get((source.id, identity), [])
            merge_before = None
            # If Trivy did not observe the finding, the first occurrence already contains the merge.
            if len(merged) == 1 and after["kind"] != "code" and after["evidence"].get("ai_analysis") != merged[0]:
                merge_before, after = after, merged_observation(after, merged[0])
            if current is None:
                continue
            if after != current:
                steps, scanner_only = recognized.get(identity, (0, False))
                if not steps or after["version"] != current["version"] - steps:
                    continue
                prior = {**current, "version": after["version"]}
                if prior != after:
                    # A child may record Trivy's evidence and stop before restoring the matching AI merge.
                    scanner_evidence = {key: value for key, value in after["evidence"].items() if key != "ai_analysis"}
                    if not scanner_only or len(merged) != 1 or prior != {**after, "evidence": scanner_evidence}:
                        continue
            recognized[identity] = (1 if merge_before is None else 2,
                                    after["kind"] != "code" and not merged and "ai_analysis" not in after["evidence"])
            matching[identity] = after
            evidence = after["evidence"]
            model = evidence.get("ai_analysis", evidence)
            positions = [position for position, proposal, result in confirmed if model.get("assessment") == result
                         and all(model.get(key) == value for key, value in proposal.items())]
            if len(positions) != 1:
                if model.get("kind") != "code":
                    changes.setdefault(source.id, {})[identity] = (-1, None, after)
                continue
            before = merge_before
            if before is None and first_scans[identity] != source.id:
                if source.started_at is None:
                    continue
                before = db.scalar(select(SecurityOccurrence.snapshot).where(
                    SecurityOccurrence.finding_id == identity, SecurityOccurrence.created_at < source.started_at,
                ).order_by(SecurityOccurrence.created_at.desc(), SecurityOccurrence.id).limit(1))
                if before is None or before.get("version") != after["version"] - 1:
                    continue
                before = {key: before[key] for key in after if key in before}
            changes.setdefault(source.id, {})[identity] = (positions[0], before, after)
    return changes


def observe(db, scan, evidence, existing_id=None):
    scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan.id).with_for_update().execution_options(populate_existing=True))
    if scan.status != "running" or scan.cancel_requested:
        raise ReviewStopped("Security scan is no longer authorized to record observations.")
    query = select(SecurityFinding).where(SecurityFinding.id == existing_id) if existing_id else select(SecurityFinding).where(
        SecurityFinding.repository_id == scan.repository_id, SecurityFinding.fingerprint == evidence["fingerprint"])
    finding = db.scalar(query.with_for_update().execution_options(populate_existing=True))
    previous = None
    if finding and finding.repository_id != scan.repository_id:
        raise ValueError("Finding identity belongs to another repository.")
    if finding is None:
        finding = SecurityFinding(id=uid(), repository_id=scan.repository_id, fingerprint=evidence["fingerprint"],
            kind=evidence["kind"], severity=evidence["severity"], title=evidence["title"], path=evidence["path"],
            first_scan_id=scan.id, last_scan_id=scan.id, evidence=evidence)
        db.add(finding)
        db.flush()
        activity(db, "finding_created", scan=scan, finding=finding)
    else:
        previous = db.scalar(select(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan.id,
                                                             SecurityOccurrence.finding_id == finding.id))
        if (previous and previous.outcome == "present" and finding.last_scan_id == scan.id
                and finding.state != "resolved"):
            snapshot, current = previous.snapshot, finding_snapshot(finding)
            observed = snapshot["evidence"]
            same_evidence = combine_observation_sources(evidence, observed) == observed
            merged = finding.evidence.get("ai_analysis")
            if snapshot == current and (same_evidence or (finding.kind != "code" and evidence == merged)):
                return finding
            if (finding.kind != "code" and merged and (same_evidence or evidence == merged)
                    and merged_observation(snapshot, merged) == current):
                return finding
        finding.version += 1
        if finding.state == "resolved":
            finding.state, finding.rotation = "open", None
            activity(db, "finding_recurred", scan=scan, finding=finding)
        sources = sorted(set(finding.evidence.get("sources", [])) | set(evidence.get("sources", [])))
        if evidence["kind"] == "code" and finding.kind != "code":
            activity(db, "ai_evidence_merged", scan=scan, finding=finding, evidence=evidence)
            evidence = {**finding.evidence, "ai_analysis": evidence}
        finding.evidence = {**evidence, "sources": sources}
        finding.title, finding.path = evidence["title"], evidence["path"]
        finding.last_scan_id, finding.updated_at = scan.id, now()
        if finding.state != "false_positive":
            finding.severity = evidence["severity"]
        if finding.state == "open" and finding.issue.get("closed_by_app"):
            finding.issue = {**finding.issue, "desired_state": "open", "sync_status": "pending",
                             "intent_version": finding.version, "verification_scan_id": scan.id}
            enqueue(db, "security_issue_sync", {"finding_id": finding.id})
        elif finding.state == "open" and finding.issue.get("desired_state") == "closed":
            finding.issue = {**finding.issue, "desired_state": None, "sync_status": "synced", "error": None}
    if not previous:
        db.add(SecurityOccurrence(id=uid(), scan_id=scan.id, finding_id=finding.id, outcome="present",
                                  snapshot=finding_snapshot(finding)))
    return finding


def stable_version(value):
    if not re.fullmatch(r"v?\d+(?:\.\d+)*", value):
        return None
    parts = [int(part) for part in value.removeprefix("v").split(".")]
    return tuple(parts + [0] * max(0, 4 - len(parts)))


def safe_dependency_version(current, fixed):
    # shortcut: automatic version ordering covers stable numeric releases; other schemes require exact fixes.
    versions = [part.strip() for part in fixed.split(",") if part.strip()]
    if current in versions:
        return True
    actual, fixes = stable_version(current), [stable_version(value) for value in versions]
    if not actual or not fixes or any(value is None for value in fixes):
        return False
    branch = [value for value in fixes if value[:2] == actual[:2]]
    return actual >= max(branch or fixes)


def dependency_advisory(evidence, packages, installation_token, stopped, cache):
    rule = evidence.get("rule_id", "")
    if evidence.get("package_type") not in {"bun", "npm", "yarn", "pnpm"} or not re.fullmatch(r"CVE-\d{4}-\d{4,}", rule):
        return None
    if stopped():
        raise ReviewStopped("Security scan stopped before advisory verification.")
    if rule not in cache:
        try:
            cache[rule] = (github.request("GET", f"/advisories?cve_id={rule}&type=reviewed&per_page=100",
                                          installation_token()), now().isoformat())
        except github.GitHubError as exc:
            cache[rule] = exc
    if stopped():
        raise ReviewStopped("Security scan stopped during advisory verification.")
    if isinstance(cache[rule], github.GitHubError):
        raise ValueError("GitHub advisory metadata could not be verified; no dependency fix was established.") from cache[rule]
    rows, checked_at = cache[rule]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    advisory = rows[0]
    ghsa = advisory.get("ghsa_id", "")
    if (not isinstance(ghsa, str) or not re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", ghsa)
        or advisory.get("cve_id") != rule or advisory.get("type") != "reviewed"
        or "withdrawn_at" not in advisory or advisory["withdrawn_at"] is not None
        or advisory.get("html_url") != f"https://github.com/advisories/{ghsa}"):
        return None
    try:
        updated = datetime.fromisoformat(advisory["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if updated.tzinfo is None or updated > datetime.fromisoformat(checked_at):
        return None
    entries = advisory.get("vulnerabilities")
    if not isinstance(entries, list) or not all(isinstance(row, dict) and isinstance(row.get("package"), dict) for row in entries):
        return None
    entries = [row for row in entries if row["package"] == {"ecosystem": "npm", "name": evidence["package"]}]
    if len(entries) != 1:
        return None
    affected = entries[0].get("vulnerable_version_range")
    # shortcut: only a single inclusive upper bound on stable npm releases is supported; other ranges stay uncertain.
    stable = r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}"
    boundary = re.fullmatch(rf"<=\s*({stable})", affected) if isinstance(affected, str) else None
    limit = stable_version(boundary[1]) if boundary else None
    original = stable_version(evidence["installed_version"])
    versions = sorted({p["version"] for p in packages})
    if (not limit or not original or not re.fullmatch(stable, evidence["installed_version"]) or not versions
        or any(p["name"] != evidence["package"] for p in packages) or original > limit
        or any(not re.fullmatch(stable, version) or stable_version(version) <= limit for version in versions)):
        return None
    return {"schema_version": "code-mortis.dependency-advisory/v1", "ghsa_id": ghsa, "cve_id": rule,
        "url": advisory["html_url"], "updated_at": advisory["updated_at"], "checked_at": checked_at,
        "package": evidence["package"], "ecosystem": "npm", "affected_range": affected,
        "original_version": evidence["installed_version"], "current_versions": versions}


def deterministic_fix(item, old, trivy_result, *, advisory_lookup=None):
    evidence, path = old["evidence"], old["path"]
    advisory = None
    original = evidence["source_sha"]
    current_path = item.renamed(original, path)
    original_source = item.read(path, original)
    exists = bool(git_source.git(item.git, item.env, "ls-tree", item.sha, "--", current_path, stopped=item.stopped).strip())
    current_source = item.read(current_path) if exists else None
    changed = current_source != original_source and bool(item.diff(original).strip())
    if not changed:
        return {"verdict": "uncertain", "explanation": "No source change establishes a fix; scanner absence is insufficient."}
    if evidence["kind"] == "secret":
        if not old["rotation"] or old["rotation"].get("identity") != evidence["identity"]:
            return {"verdict": "uncertain", "explanation": "Credential rotation or revocation has not been acknowledged."}
        raw = original_source.encode()[evidence["match_offset"]:evidence["match_offset"] + evidence["match_bytes"]].decode()
        if security_engine.secure_digest(evidence["rule_id"] + "\0" + raw) != evidence["identity"]:
            return {"verdict": "uncertain", "explanation": "Original credential identity could not be revalidated."}
        if any(raw in item.read(candidate) for candidate in item.inventory):
            return {"verdict": "still_present", "explanation": "The original credential material remains in the repository."}
    elif evidence["kind"] == "dependency":
        target = trivy_result["targets"].get(current_path)
        if exists and not target:
            return {"verdict": "uncertain", "explanation": "The affected dependency target was not successfully analyzed."}
        packages = [p for row in trivy_result["targets"].values() for p in row["packages"]
                    if p["name"] == evidence["package"]]
        if any(p["version"] == evidence["installed_version"] for p in packages):
            return {"verdict": "uncertain", "explanation": "The affected package version is still present."}
        if packages and any(not safe_dependency_version(p["version"], evidence.get("fixed_version", "")) for p in packages):
            if not evidence.get("fixed_version") and advisory_lookup:
                advisory = advisory_lookup(evidence, packages)
            if not advisory:
                reason = "The new version is not an independently established safe version for the original advisory."
                if not evidence.get("fixed_version"):
                    reason += " No fixed version was reported. Review the advisory or record a justified triage decision."
                return {"verdict": "uncertain", "explanation": reason}
    elif evidence["kind"] == "infrastructure":
        checks = trivy_result["targets"].get(current_path, {}).get("checks", [])
        if exists and not any(check["id"] == evidence["rule_id"] and check["status"] == "PASS"
                              and check.get("resource", "") == evidence.get("resource", "") for check in checks):
            return {"verdict": "uncertain", "explanation": "The original configuration check did not explicitly pass."}
    return {"verdict": "fixed", "explanation": (
                "The lockfile changed, the original package version is absent, and every installed version is outside "
                f"the GitHub-reviewed advisory's affected range ({advisory['affected_range']})."
                if advisory else "The original condition changed and the relevant scanner check confirms its removal."),
            "original_sha": original, "current_sha": item.sha, "path": path, "current_path": current_path,
            "original_hash": digest(original_source), "current_hash": digest(current_source) if current_source is not None else None,
            **({"advisory": advisory} if advisory else {})}


def perform(scan_id, claim):
    started = time.monotonic()
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if not scan or scan.status not in ACTIVE or scan.cancel_requested:
            return
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        if _mcp.scan_revoked(db, scan, repository_id=scan.repository_id):
            cancel(db, scan, reason=_mcp.REVOKED_MESSAGE)
            return
        if _pat.scan_revoked(db, scan, repository_id=scan.repository_id):
            cancel(db, scan, reason=_pat.REVOKED_MESSAGE)
            return
        if not is_shared(scan.config.get("connection", {})):
            cancel(db, scan, reason=RETIRED_MESSAGE)
            return
        require_execution_engine(scan.config)
        job_id, _, attempt = claim.rpartition(":")
        job = db.get(Job, job_id) if attempt.isdigit() else None
        if not attempt.isdigit() or not job_live(job, int(attempt), now()) or job.payload.get("scan_id") != scan_id:
            raise ValueError("Mortis security analysis requires its live durable job attempt.")
        execution = scan.progress.get("execution")
        if execution and scan.status == "running":
            if execution == claim:
                return
            job_id, _, attempt = execution.rpartition(":")
            owner = db.get(Job, job_id) if attempt.isdigit() else None
            if owner and owner.status == "running" and owner.attempts == int(attempt) and owner.lease_until and owner.lease_until > now():
                return
        repo, actor, connection = db.get(Repository, scan.repository_id), db.get(User, scan.actor_id), db.get(Connection, scan.connection_id)
        authorize(db, repo, actor, connection)
        if connection.kind != "api":
            db.scalar(select(Connection).where(Connection.id == connection.id).with_for_update())
            if connection_busy(db, connection.id, scan_id=scan.id):
                scan.status = "blocked"
                raise RunnerBlocked("busy")
        workflow = scan.config.get("execution_limits") == EXECUTION_LIMITS
        current_connection = connection_snapshot(connection, workflow=workflow)
        if any(current_connection[key] != scan.config["connection"][key]
               for key in ("kind", "owner_id", "protocol", "base_url", "provider", "options")):
            raise ValueError("The AI connection changed. Start a new security scan.")
        scan.status, scan.started_at = "running", scan.started_at or now()
        if workflow:
            started -= scan.progress.get("execution_seconds", 0)
        scan.progress = {**scan.progress, "execution": claim}
        config, full_name = scan.config, repo.full_name
        credentials = json.loads(unseal(connection.secret))
        token = github.installation_token(db, repo.installation_id)
        scan.progress = {**scan.progress, "freshness": branch_head(repo.full_name, token)}
        previous = [finding_snapshot(row) for row in db.scalars(select(SecurityFinding).where(
            SecurityFinding.repository_id == repo.id))]
        cache_config = checkpoint_config(config)
        sources, saved = [], {}
        source, seen = scan, set()
        while source and source.id not in seen:
            if (source.repository_id, source.branch, source.head_sha) != (scan.repository_id, scan.branch, scan.head_sha):
                break
            source_config = checkpoint_config(source.config)
            if without_request_timeout(source_config) != without_request_timeout(cache_config):
                break
            sources.append(source)
            seen.add(source.id)
            for key, saved_task in source.progress.get("tasks", {}).items():
                saved.setdefault(key, []).append((source.id, source_config, saved_task))
            source = db.get(SecurityScan, source.parent_id) if source.parent_id else None
        replayed = {row["id"]: row for row in previous}
        replay = assessment_replay(db, sources, replayed)
        replay_merges = {}
        for changes in replay.values():
            for identity, (_, before, after) in changes.items():
                if before and after["kind"] != "code":
                    replay_merges.setdefault(identity, []).extend((before, after))
    limits = config["limits"]
    concurrency = limits.get("concurrency", 1) if config["connection"]["kind"] == "api" else 1
    stop_workers = Event()
    save_task = partial(checkpoint, scan_id, claim=claim)

    def fresh_installation_token():
        with transaction() as db:
            fresh_token = github.installation_token(db, db.get(Repository, scan.repository_id).installation_id)
        item.secret_values.append(fresh_token)
        return fresh_token

    def remember_observation(finding):
        if finding.id in replayed:
            previous_snapshot = replayed[finding.id]
            observed_snapshot = finding_snapshot(finding)
            candidates = (previous_snapshot, *replay_merges.get(finding.id, ()))
            if any(observed_snapshot == {**candidate, "version": previous_snapshot["version"] + 1,
                                         "evidence": combine_observation_sources(candidate["evidence"], previous_snapshot["evidence"])}
                   for candidate in candidates):
                replayed[finding.id] = observed_snapshot

    def settlement_row(db):
        if live_claim(db, claim, lock=True):
            row = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
            if executing(row, claim):
                return row

    def check_deadline():
        if limits["timeout_seconds"] is not None and time.monotonic() - started >= limits["timeout_seconds"]:
            raise TimeoutError("Security scan reached its time limit. Start a new scan with a larger timeout.")

    def stopped():
        if stop_workers.is_set():
            return True
        with transaction() as db:
            row = db.get(SecurityScan, scan_id)
            if not row or row.cancel_requested or row.status != "running" or row.progress.get("execution") != claim:
                return True
            from code_mortis.api_tokens import provenance as _pat
            from code_mortis.mcp import provenance as _mcp

            if _mcp.scan_revoked(db, row, repository_id=row.repository_id):
                row.cancel_requested = True
                return True
            if _pat.scan_revoked(db, row, repository_id=row.repository_id, network=False):
                row.cancel_requested = True
                return True
            try:
                authorize(db, db.get(Repository, row.repository_id), db.get(User, row.actor_id), db.get(Connection, row.connection_id), fresh=False)
            except (ValueError, RunnerBlocked):
                return True
            job_id, _, attempt = claim.rpartition(":")
            job = db.get(Job, job_id)
            timestamp = now()
            if not job_live(job, int(attempt), timestamp):
                return True
            if job.lease_until < timestamp + timedelta(seconds=300):
                job.lease_until = timestamp + timedelta(seconds=360)
        check_deadline()
        return False

    def timeout():
        if limits["timeout_seconds"] is None:
            return None
        remaining = limits["timeout_seconds"] - (time.monotonic() - started)
        if workflow:
            if remaining <= 0:
                raise TimeoutError("Security scan reached its time limit. Start a new scan with a larger timeout.")
            return remaining
        return max(1, int(remaining))

    def task_input(task, context, key, saved_histories=None):
        context = {**context, "language": config.get("language", "English"),
                   "task_contract": config.get("task_contract", "security-task/v2")}
        if task == "assessment":
            supplement = (security_engine.policy.instructions(task)
                          if context["task_contract"] == security_engine.TASK_CONTRACT else "")
            context["assessment_policy"] = digest(security_engine.INSTRUCTIONS + security_engine.ASSESSMENT_INSTRUCTIONS + supplement)
        identity = digest(json.dumps([cache_config, context], sort_keys=True))
        for source_id, saved_config, cached in saved.get(key, ()):
            if (cached.get("status") != "completed" or cached.get("result", {}).get("verdict") == "uncertain"
                    or not payload.reusable_source_context(cached)):
                continue
            contexts = [context]
            if saved_histories and source_id in saved_histories:
                contexts.append({**context, "history": saved_histories[source_id]})
            for candidate in contexts:
                if cached.get("input_hash") == digest(json.dumps([saved_config, candidate], sort_keys=True)):
                    try:
                        if security_engine.redacted_context(item, cached["result"]) != cached["result"]:
                            break
                    except verification.VerificationOutputError:
                        break
                    return context, digest(json.dumps([cache_config, candidate], sort_keys=True)), (source_id, cached)
        return context, identity, None

    def ai(item, task, context, key, allowance=None, *, parallel=False, record_required_paths=None, saved_histories=None):
        context, identity, checkpoint_match = task_input(task, context, key, saved_histories)
        if checkpoint_match is not None:
            source_id, cached = checkpoint_match
            if source_id == scan_id:
                return cached["result"]
            save_task(key, "completed", result=cached["result"], input_hash=identity, tokens=0, reused=True,
                      **({"citation_contract": cached["citation_contract"]} if "citation_contract" in cached else {}),
                      **({"source_packing": cached["source_packing"]} if "source_packing" in cached else {}),
                      **({"context_paths": cached["context_paths"]} if "context_paths" in cached else {}),
                      **({"source_navigation": cached["source_navigation"]} if "source_navigation" in cached else {}),
                      **({"proposal": context["proposal"]} if task == "assessment" else {}))
            return cached["result"]
        with transaction() as db:
            row = db.get(SecurityScan, scan_id)
            from code_mortis.api_tokens import provenance as _pat
            from code_mortis.mcp import provenance as _mcp

            if _mcp.scan_revoked(db, row, repository_id=row.repository_id):
                raise ReviewStopped(_mcp.REVOKED_MESSAGE)
            if _pat.scan_revoked(db, row, repository_id=row.repository_id):
                raise ReviewStopped(_pat.REVOKED_MESSAGE)
            authorize(db, db.get(Repository, row.repository_id), db.get(User, row.actor_id), db.get(Connection, row.connection_id))
            used = row.progress.get("tokens")
            packing, navigation = None, None
            for prior in (row.progress.get("tasks", {}).get(key, {}), *(entry[2] for entry in saved.get(key, ()))):
                if not payload.reusable_source_context(prior):
                    continue
                if packing is None:
                    packing = prior.get("source_packing")
                if navigation is None and prior.get("input_hash") == identity and "source_navigation" in prior:
                    navigation = prior["source_navigation"]
        if used is None:
            raise ValueError("Token usage is unknown; further AI checks require an explicit retry.")
        remaining = None if limits["token_budget"] is None else limits["token_budget"] - used
        if allowance is not None:
            remaining = min(remaining, allowance)
        if remaining is not None:
            remaining = max(0, remaining)
        save_task(key, "running", input_hash=identity, **({"proposal": context["proposal"]} if task == "assessment" else {}))
        usage = {}
        try:
            def select_packing(value):
                return save_task(key, "running", source_packing=value)["source_packing"]

            def select_navigation(value):
                return save_task(key, "running", source_navigation=value)["source_navigation"]

            def record_sources(paths):
                save_task(key, "running", context_paths=paths)

            def infer(context, *, schema, instructions, validator, budget, timeout, round):
                from code_mortis.core.workflow_inference import execute

                def authorized():
                    if stopped():
                        raise ReviewStopped("Security scan cancelled.")
                    with transaction() as db:
                        current = db.get(SecurityScan, scan_id)
                        from code_mortis.api_tokens import provenance as _pat
                        from code_mortis.mcp import provenance as _mcp

                        if (_mcp.scan_revoked(db, current, repository_id=current.repository_id)
                                or _pat.scan_revoked(db, current, repository_id=current.repository_id)):
                            raise ReviewStopped("The originating security scan permission was revoked.")
                        selected = db.get(Connection, current.connection_id)
                        if (not selected or selected.secret != connection.secret
                                or connection_snapshot(selected, workflow=workflow) != current_connection):
                            raise ReviewStopped("The shared connection changed during the security scan.")
                        authorize(db, db.get(Repository, current.repository_id), db.get(User, current.actor_id), selected)

                return execute(config["connection"], credentials, context, instructions=instructions, schema=schema,
                    validator=validator, claim=claim, stage="security", task_key=f"{key}:round:{round}", scan_id=scan_id,
                    stopped=stopped, authorize=authorized, effort="high", token_budget=budget, timeout=timeout)

            from code_mortis.core.workflow_inference import output_token_policy

            token_policy = output_token_policy(claim.rsplit(":", 1)[0], config)
            result, usage = security_engine.ask(item, config["connection"], credentials, task, context,
                                                remaining, timeout(), stopped, uid(),
                                                infer=infer, source_packing=packing, output_token_policy=token_policy,
                                                select_packing=select_packing, navigation=navigation,
                                                select_navigation=select_navigation, record_sources=record_sources,
                                                record_required_paths=record_required_paths)
            save_task(key, "completed", result=result, input_hash=identity, tokens=usage["tokens"],
                      citation_contract=security_engine.CITATION_CONTRACT)
            return result
        except TransientAuthorizationError:
            raise
        except ReviewStopped as exc:
            # Revocation blocks results, but already consumed inference still belongs in history.
            with transaction() as db:
                row = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
                if row and row.progress.get("execution") == claim and live_claim(db, claim):
                    tokens = getattr(exc, "usage", usage).get("tokens")
                    tokens = tokens if type(tokens) is int and tokens >= 0 else None
                    used = row.progress.get("tokens")
                    tasks = dict(row.progress.get("tasks", {}))
                    tasks[key] = {**tasks.get(key, {}), "status": "failed", "tokens": tokens,
                                  "error": "Security inference stopped before result acceptance."}
                    row.progress = {**row.progress, "tasks": tasks,
                                    "tokens": used + tokens if used is not None and tokens is not None else None}
            raise
        except (verification.VerificationOutputError, RunnerBlocked) as exc:
            tokens = getattr(exc, "usage", {}).get("tokens")
            feedback = getattr(exc, "feedback", None)
            try:
                detail = item.redact(str(exc))[:2000]
                feedback = security_engine.redacted_context(item, feedback) if feedback else None
            except Exception:
                detail, feedback = f"AI task failed ({type(exc).__name__}); diagnostic text could not be safely sanitized.", None
            save_task(key, "failed", error=detail, tokens=tokens,
                      **({"validation_feedback": feedback} if feedback else {}))
            raise
        except Exception:
            save_task(key, "failed", error="AI analysis was interrupted; token consumption is unknown.", tokens=None)
            raise

    problems, observed = [], set()
    assessment_problems, uncertain_keys, assessment_blockers = [], set(), set()
    item = None
    try:
        settings().work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage(scan_id, "snapshot", "running")
        old_shas = [row["evidence"]["source_sha"] for row in previous]
        with security_engine.snapshot(full_name, scan.head_sha, old_shas, token, stopped) as item:
            item.secret_values.extend(value for value in (token, credentials.get("api_key", ""),
                                      *credentials.get("extra_headers", {}).values()) if value)
            stage(scan_id, "snapshot", "completed", files=len(item.inventory))
            problems.extend(item.problems)
            with transaction() as db:
                db.get(SecurityScan, scan_id).coverage = {"inventory": item.inventory, "excluded": item.excluded, "problems": item.problems}
            stage(scan_id, "trivy", "running")
            result = security_engine.trivy(item, timeout(), stopped, **({"execution_limits": True} if workflow else {}))
            item.secret_rules = []
            for old in previous:
                if old["kind"] == "secret":
                    try:
                        secret = old["evidence"]
                        raw = item.read(old["path"], secret["source_sha"]).encode()[
                            secret["match_offset"]:secret["match_offset"] + secret["match_bytes"]].decode()
                        if security_engine.secure_digest(secret["rule_id"] + "\0" + raw) != secret["identity"]:
                            raise ValueError("Original credential identity changed.")
                        item.secret_values.append(raw)
                    except (ValueError, KeyError):
                        item.secret_rules.append(old["evidence"])
            problems.extend(result["problems"])
            with transaction() as db:
                row = db.get(SecurityScan, scan_id)
                row.coverage = {"inventory": item.inventory, "excluded": item.excluded, "problems": item.problems,
                                "trivy": result["metadata"], "targets": json.loads(item.redact(json.dumps(result["targets"]))),
                                "redacted_lines": item.secret_lines}
                for evidence in result["findings"]:
                    old = next((p for p in previous if p["kind"] == evidence["kind"]
                                and p["evidence"].get("rule_id") == evidence.get("rule_id")
                                and p["evidence"].get("package") == evidence.get("package")
                                and p["evidence"].get("resource") == evidence.get("resource")
                                and item.renamed(p["evidence"]["source_sha"], p["path"]) == evidence["path"]), None)
                    finding = observe(db, row, evidence, old["id"] if old and evidence["kind"] != "secret" else None)
                    remember_observation(finding)
                    observed.add(finding.id)
            stage(scan_id, "trivy", "completed" if not result["problems"] else "partial", findings=len(result["findings"]))
            candidates = []
            code_paths = [path for path, value in item.inventory.items() if value["code"]]
            completed, failed = 0, 0
            stage(scan_id, "analysis", "running", claim=claim,
                  total=len(code_paths), completed=0, failed=0, concurrency=concurrency)

            parallel = concurrency > 1 and len(code_paths) > 1
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                rolling = parallel and limits["token_budget"] is None
                batches = [code_paths] if rolling else [code_paths[offset:offset + concurrency]
                                                      for offset in range(0, len(code_paths), concurrency)]
                for batch in batches:
                    if stopped():
                        raise ReviewStopped("Security scan cancelled.")
                    if rolling:
                        def submit(path):
                            with transaction() as db:
                                if db.get(SecurityScan, scan_id).progress.get("tokens") is None:
                                    raise ValueError("Token usage is unknown; further AI checks require an explicit retry.")
                            context = {"assigned_path": path, "head_sha": item.sha, "diff": "",
                                       "sources": {"current": {path: item.source(path)}}}
                            return pool.submit(ai, item, "analysis", context, "analysis:" + path, parallel=True)

                        pending = rolling_analysis(batch, concurrency, submit, stopped, stop_workers)
                    else:
                        contexts = {path: {"assigned_path": path, "head_sha": item.sha, "diff": "",
                                    "sources": {"current": {path: item.source(path)}}} for path in batch}
                        needed = sum(task_input("analysis", contexts[path], "analysis:" + path)[2] is None for path in batch)
                        with transaction() as db:
                            used = db.get(SecurityScan, scan_id).progress.get("tokens")
                        if used is None:
                            raise ValueError("Token usage is unknown; further AI checks require an explicit retry.")
                        allowance = None if limits["token_budget"] is None or not needed else (limits["token_budget"] - used) // needed
                        futures = {pool.submit(ai, item, "analysis", contexts[path], "analysis:" + path, allowance,
                                               parallel=parallel): path for path in batch}
                        pending = ((futures[future], future) for future in as_completed(futures))
                    results, fatal = {}, None
                    for path, future in pending:
                        try:
                            results[path] = future.result()
                            completed += 1
                        except verification.VerificationOutputError as exc:
                            if exc.feedback and type(exc.usage.get("tokens")) is int:
                                failed += 1
                                problems.append(validation_problem(path, exc.feedback))
                            else:
                                fatal = fatal or exc
                                stop_workers.set()
                        except Exception as exc:
                            if fatal is None or isinstance(exc, RunnerBlocked):
                                fatal = exc
                            stop_workers.set()
                        stage(scan_id, "analysis", "running", claim=claim, completed=completed, failed=failed)
                    for path in batch:
                        candidates.extend(results.get(path, {}).get("findings", []))
                    if fatal:
                        raise fatal
            stage(scan_id, "analysis", "partial" if failed else "completed", claim=claim,
                  files=len(code_paths), candidates=len(candidates))
            assessment_completed, assessment_failed = 0, 0
            assessment_problem_count = len(problems)
            stage(scan_id, "assessment", "running", claim=claim,
                  total=len(candidates), completed=0, failed=0, concurrency=concurrency)
            remaining = [SimpleNamespace(id=number, position=number, proposal=candidate) for number, candidate in enumerate(candidates)]
            parallel = concurrency > 1 and len(candidates) > 1
            rolling = parallel and limits["token_budget"] is None

            def assessment_history_snapshot(db, *, lock=False):
                query = select(SecurityFinding).where(SecurityFinding.repository_id == scan.repository_id).order_by(SecurityFinding.id)
                return [finding_snapshot(row) for row in db.scalars(query.with_for_update() if lock else query)]

            def assessment_context(candidate, history, current_progress):
                paths = list(dict.fromkeys(citation["path"] for citation in candidate.proposal["evidence"]))
                if config.get("task_contract") == security_engine.TASK_CONTRACT:
                    analysis_task = current_progress.get("tasks", {}).get("analysis:" + candidate.proposal["path"], {})
                    # Assess with the same source context, including guards not quoted in the allegation.
                    paths = sorted(set(paths) | set(analysis_task.get("context_paths", [])))
                context = {"proposal": candidate.proposal,
                    "history": assessment_history(candidate.proposal, history, item), "head_sha": item.sha, "diff": "",
                    "sources": {"current": {path: item.source(path) for path in paths}}}
                # Reconstruct history only to recognize cached inputs. New requests use live history.
                saved_histories = {}
                for source_id, _, _ in saved.get(f"assessment:{candidate.id}", ()):
                    changes = replay.get(source_id)
                    if not changes:
                        continue
                    recovered = []
                    for row in history:
                        update = changes.get(row["id"])
                        if update and replayed.get(row["id"]) == row:
                            position, before, after = update
                            row = before if candidate.position <= position else after
                        if row is not None:
                            recovered.append(row)
                    saved_histories[source_id] = assessment_history(candidate.proposal, recovered, item)
                return context, saved_histories

            def submit_assessment(pool, candidate, context, required_paths, allowance=None):
                paths = required_paths[candidate.id] = []
                return pool.submit(ai, item, "assessment", context[0], f"assessment:{candidate.id}", allowance,
                                   parallel=parallel, record_required_paths=paths.extend, saved_histories=context[1])

            def assessment_responses(pool, ready, admitted, history, current_progress, required_paths, stop_admitting):
                futures, prepared = {}, {}
                settled = 0
                try:
                    while True:
                        while ready and len(futures) < concurrency and not stop_admitting.is_set():
                            if stopped():
                                raise ReviewStopped("Security scan cancelled.")
                            candidate = ready[0]
                            if candidate.id not in prepared:
                                prepared[candidate.id] = assessment_context(candidate, history, current_progress)
                            if settled:
                                with transaction() as db:
                                    current = db.get(SecurityScan, scan_id)
                                    if current.progress.get("tokens") is None or assessment_history_snapshot(db) != history:
                                        stop_admitting.set()
                                        break
                            if any(future.done() for future in futures):
                                break
                            ready.popleft()
                            admitted[candidate.id] = settled
                            futures[submit_assessment(pool, candidate, prepared.pop(candidate.id), required_paths)] = candidate
                        if not futures:
                            return
                        done, _ = wait(futures, return_when=FIRST_COMPLETED)
                        for future in done:
                            settled += 1
                            yield futures.pop(future), future
                except BaseException:
                    stop_workers.set()
                    for future in futures:
                        future.cancel()
                    raise

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                while remaining:
                    if stopped():
                        raise ReviewStopped("Security scan cancelled.")
                    with transaction() as db:
                        history = assessment_history_snapshot(db)
                        current_progress = db.get(SecurityScan, scan_id).progress
                        used = current_progress.get("tokens")
                    if used is None:
                        raise ValueError("Token usage is unknown; further AI checks require an explicit retry.")

                    required_paths = {}
                    stop_admitting = Event()
                    if rolling:
                        initial_keys = {row.id: assessment_keys(row.proposal, item, history) for row in remaining}
                        keys = initial_keys.copy()
                        candidate_keys = set().union(*initial_keys.values())
                        admitted, added_citations = {}, {}
                        ready = deque(assessment.independent_batch(remaining, keys))
                        pending = assessment_responses(
                            pool, ready, admitted, history, current_progress, required_paths, stop_admitting,
                        )
                    else:
                        batch = assessment_batch(remaining, history, item, concurrency)
                        contexts = {row.id: assessment_context(row, history, current_progress) for row in batch}
                        needed = sum(task_input("assessment", contexts[row.id][0], f"assessment:{row.id}",
                                                contexts[row.id][1])[2] is None for row in batch)
                        allowance = None if limits["token_budget"] is None or not needed else (limits["token_budget"] - used) // needed
                        futures = {submit_assessment(pool, row, contexts[row.id], required_paths, allowance): row for row in batch}
                        pending = ((futures[future], future) for future in as_completed(futures))

                    results, fatal = {}, None
                    first_failed_wave = len(remaining)
                    for candidate, future in pending:
                        try:
                            assessed = results[candidate.id] = future.result()
                            assessment_completed += 1
                            if rolling and assessed["verdict"] == "confirmed":
                                cited = assessment_keys({"path": candidate.proposal["path"], "evidence": assessed["evidence"]}, item)
                                added = (cited & candidate_keys) - initial_keys[candidate.id]
                                if added:
                                    added_citations[candidate.id] = added
                                    keys[candidate.id] = initial_keys[candidate.id] | added
                                    ready.clear()
                                    ready.extend(assessment.independent_batch(remaining, keys, admitted=admitted))
                        except Exception as exc:
                            if (isinstance(exc, verification.VerificationOutputError) and exc.feedback
                                    and type(exc.usage.get("tokens")) is int):
                                assessment_failed += 1
                                problems.append(validation_problem(candidate.proposal["path"], exc.feedback, task="assessment"))
                            else:
                                if fatal is None or isinstance(fatal, TransientAuthorizationError) or isinstance(exc, RunnerBlocked):
                                    fatal = exc
                                if rolling:
                                    first_failed_wave = min(first_failed_wave, admitted[candidate.id])
                                stop_admitting.set()
                                if not isinstance(exc, TransientAuthorizationError):
                                    stop_workers.set()
                        stage(scan_id, "assessment", "running", claim=claim, completed=assessment_completed, failed=assessment_failed)
                    if fatal:
                        if not (rolling and first_failed_wave and isinstance(fatal, (verification.VerificationOutputError, RunnerBlocked))):
                            raise fatal
                        # All peers have drained. Recheck authority before saving any earlier successful waves.
                        stop_workers.clear()
                    if stopped():
                        raise ReviewStopped("Security scan cancelled.")
                    stale_results = {}
                    if rolling:
                        settling = {identity: wave for identity, wave in admitted.items() if wave < first_failed_wave}
                        batch = [row for row in remaining if row.id in settling]
                        earlier_citations = set()
                        # A later admission may finish before an earlier response reveals their dependency.
                        for _, wave in groupby(settling, key=settling.__getitem__):
                            wave_citations = set()
                            for identity in wave:
                                if identity not in results:
                                    continue
                                if initial_keys[identity] & earlier_citations:
                                    stale_results[identity] = results[identity] = {
                                        **results[identity], "verdict": "uncertain", "finding_id": None,
                                        "explanation": "Finding history changed during assessment. Retry this security scan to evaluate the candidate with the updated history.",
                                    }
                                else:
                                    wave_citations.update(added_citations.get(identity, ()))
                            earlier_citations.update(wave_citations)
                    with transaction() as db:
                        row = db.get(SecurityScan, scan_id)
                        if not executing(row, claim) or row.cancel_requested or not live_claim(db, claim):
                            raise ReviewStopped("Security scan cancelled.")
                        authorize(db, db.get(Repository, row.repository_id), db.get(User, row.actor_id), db.get(Connection, row.connection_id))
                    with transaction() as db:
                        row = settlement_row(db)
                        if row is None or row.cancel_requested:
                            raise ReviewStopped("Security scan cancelled.")
                        if (_mcp.scan_revoked(db, row, repository_id=row.repository_id)
                                or _pat.scan_revoked(db, row, repository_id=row.repository_id, network=False)):
                            raise ReviewStopped("The originating security scan permission was revoked.")
                        authorize(db, db.get(Repository, row.repository_id), db.get(User, row.actor_id),
                                  db.get(Connection, row.connection_id), fresh=False)
                        # Admitted candidates share one history snapshot until their ordered observations are saved.
                        if assessment_history_snapshot(db, lock=True) != history:
                            raise ValueError("Finding history changed during assessment. Retry this security scan.")
                        check_deadline()
                        for candidate in batch:
                            assessed = results.get(candidate.id)
                            if assessed is None:
                                continue
                            if assessed["verdict"] == "uncertain":
                                assessment_problems.append("A proposed security finding requires additional verification.")
                                proposal = candidate.proposal
                                uncertain_keys.update(assessment_keys({
                                    **proposal,
                                    "evidence": [*proposal["evidence"], *assessed["evidence"],
                                                 *({"path": path} for path in required_paths[candidate.id])],
                                }, item, history))
                            if assessed["verdict"] != "confirmed":
                                continue
                            proposal = candidate.proposal
                            evidence = {**proposal, "kind": "code", "sources": ["mortis"], "source_sha": item.sha,
                                        "source_hash": item.inventory[proposal["path"]]["hash"], "assessment": assessed,
                                        "fingerprint": digest(json.dumps(["code", proposal["path"], proposal["category"],
                                                                          proposal["evidence"][0]["quote"], proposal["title"]]))}
                            finding = observe(db, row, evidence, assessed["finding_id"])
                            remember_observation(finding)
                            observed.add(finding.id)
                        if stale_results:
                            tasks = dict(row.progress["tasks"])
                            for identity, assessed in stale_results.items():
                                key = f"assessment:{identity}"
                                tasks[key] = {**tasks[key], "result": assessed}
                            row.progress = {**row.progress, "tasks": tasks}
                    if fatal:
                        raise fatal
                    done = {row.id for row in batch}
                    remaining = [row for row in remaining if row.id not in done]
            stage(scan_id, "assessment",
                  "completed" if len(problems) == assessment_problem_count and not assessment_problems else "partial",
                  claim=claim)
            to_verify = [old for old in previous if old["id"] not in observed and
                         (old["state"] in {"open", "accepted_risk"} or old["pending_close"])]
            stage(scan_id, "verification", "running", claim=claim, total=len(to_verify), completed=0)
            advisories = {}
            advisory_locks = {old["evidence"].get("rule_id", ""): Lock() for old in to_verify
                              if old["evidence"].get("kind") == "dependency"}

            def advisory_lookup(evidence, packages):
                with advisory_locks[evidence.get("rule_id", "")]:
                    return dependency_advisory(evidence, packages, fresh_installation_token, stopped, advisories)

            def verify_one(old):
                try:
                    if old["kind"] == "code":
                        original = old["evidence"]["source_sha"]
                        current_path = item.renamed(original, old["path"])
                        context = {"finding": old, "original_sha": original, "head_sha": item.sha,
                                   "diff": item.diff(original, model_context=True), "sources": {
                                       "original": {old["path"]: item.source(old["path"], original)},
                                       "current": {current_path: item.source(current_path)} if current_path in item.inventory else {}}}
                        verdict = ai(item, "verification", context, "verification:" + old["id"])
                    else:
                        verdict = deterministic_fix(item, old, result, advisory_lookup=advisory_lookup)
                except (ValueError, KeyError) as exc:
                    verdict = {"verdict": "uncertain", "explanation": item.redact(str(exc))[:1000]}
                return {**verdict, "finding_version": old["version"]}

            # Versions and Assessment blockers are applied after verification over the pinned snapshot.
            verdicts, fatal = {}, None
            rolling = concurrency > 1 and len(to_verify) > 1 and limits["token_budget"] is None
            with ThreadPoolExecutor(max_workers=concurrency) if rolling else nullcontext() as pool:
                stop_admitting = Event()
                pending = (rolling_analysis(to_verify, concurrency, lambda old: pool.submit(verify_one, old),
                                            stopped, stop_admitting) if pool else ((old, None) for old in to_verify))
                for old, future in pending:
                    try:
                        verdict = future.result() if future is not None else verify_one(old)
                        if verdict["verdict"] == "fixed" and uncertain_keys:
                            fix_keys = assessment_keys(old, item) | assessment_keys({
                                "path": old["path"], "evidence": verdict.get("evidence", []),
                            }, item)
                            if uncertain_keys & fix_keys:
                                assessment_blockers.add(old["id"])
                        verdicts[old["id"]] = verdict
                        stage(scan_id, "verification", "running", claim=claim, completed=len(verdicts))
                    except BaseException as exc:
                        if pool is None:
                            raise
                        if fatal is None or isinstance(fatal, TransientAuthorizationError):
                            fatal = exc
                        stop_admitting.set()
                        # Let admitted peers settle their real usage across a temporary authorization outage.
                        if not isinstance(exc, TransientAuthorizationError):
                            stop_workers.set()
                if fatal:
                    raise fatal
            verdicts = {old["id"]: verdicts[old["id"]] for old in to_verify}
            if stopped():
                raise ReviewStopped("Security scan cancelled.")
            stage(scan_id, "verification", "partial" if any(row["verdict"] == "uncertain" for row in verdicts.values())
                  else "completed", claim=claim, findings=len(verdicts))
            token = fresh_installation_token()
            fresh = branch_head(full_name, token)
            finish(scan_id, verdicts, problems, fresh, bool(code_paths or result["targets"]),
                   claim=claim, assessment_problems=assessment_problems, assessment_blockers=assessment_blockers)
    except TransientAuthorizationError:
        with transaction() as db:
            if row := settlement_row(db):
                interrupt_work(row)
                row.status = "queued"
                row.progress = {**row.progress, "execution": None}
                if workflow:
                    row.progress["execution_seconds"] = time.monotonic() - started
        raise
    except ReviewStopped:
        with transaction() as db:
            if row := settlement_row(db):
                cancel(db, row)
    except Exception as exc:
        try:
            message = clean_text(item.redact(str(exc)) if item else str(exc),
                                 (token, credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values()))
        except Exception:
            message = f"Security scan failed ({type(exc).__name__}); diagnostic text could not be safely sanitized."
        with transaction() as db:
            if row := settlement_row(db):
                row.status = "blocked" if isinstance(exc, RunnerBlocked) else "partial" if observed else "failed"
                row.error, row.finished_at = message[:2000], now()
                row.stages = {name: {**value, "status": "failed" if value.get("status") == "running" else "skipped"}
                              if value.get("status") in {"running", "pending"} else value for name, value in row.stages.items()}
                row.report = report_data(db, row, problems + assessment_problems + [message[:2000]])
                activity(db, "scan_failed", scan=row, reason=row.error)


def finish(scan_id, verdicts, problems, freshness, supported, *, claim=None,
           assessment_problems=(), assessment_blockers=()):
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        if not executing(scan, claim) or scan.cancel_requested or not live_claim(db, claim):
            return
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        reason = None
        if _mcp.scan_revoked(db, scan, repository_id=scan.repository_id):
            reason = _mcp.REVOKED_MESSAGE
        elif _pat.scan_revoked(db, scan, repository_id=scan.repository_id):
            reason = _pat.REVOKED_MESSAGE
        if reason:
            if live_claim(db, claim, lock=True):
                db.refresh(scan, with_for_update=True)
                if executing(scan, claim):
                    cancel(db, scan, reason=reason)
            return
        authorize(db, db.get(Repository, scan.repository_id), db.get(User, scan.actor_id), db.get(Connection, scan.connection_id))
    with transaction() as db:
        if not live_claim(db, claim, lock=True):
            return
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if not executing(scan, claim) or scan.cancel_requested:
            return
        repo, actor, connection = db.get(Repository, scan.repository_id), db.get(User, scan.actor_id), db.get(Connection, scan.connection_id)
        authorize(db, repo, actor, connection, fresh=False)
        current = scan.head_sha == freshness["head_sha"] and scan.branch == freshness["default_branch"]
        if not supported:
            problems.append("No supported security analysis targets were available.")
        if db.scalar(select(SecurityFinding.id).where(SecurityFinding.repository_id == scan.repository_id,
            SecurityFinding.state.in_(["open", "accepted_risk"]), SecurityFinding.severity.not_in(security_engine.SEVERITIES)).limit(1)):
            problems.append("An unresolved finding has unclassified severity; the repository cannot be graded.")
        # Coverage failures block all closures. Candidate uncertainty only blocks related findings.
        resolution_ready = not problems
        problems = [*problems, *assessment_problems]
        for identity, verdict in verdicts.items():
            finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == identity).with_for_update())
            if finding.version != verdict["finding_version"]:
                problems.append(f"Finding state changed during verification: {finding.title} ({finding.path}).")
            if verdict["verdict"] == "uncertain":
                problems.append(f"Fix verification remains uncertain for {finding.title} ({finding.path}): {verdict['explanation']}")
        complete = not problems
        for identity, verdict in verdicts.items():
            finding = db.get(SecurityFinding, identity)
            outcome = {"fixed": "fixed", "still_present": "present", "uncertain": "uncertain"}[verdict["verdict"]]
            verified = (resolution_ready and current and identity not in assessment_blockers
                        and finding.version == verdict["finding_version"] and verdict["verdict"] == "fixed")
            if verified and finding.state in {"open", "accepted_risk"}:
                finding.state, finding.updated_at = "resolved", now()
                finding.version += 1
                if finding.issue.get("number"):
                    finding.issue = {**finding.issue, "desired_state": "closed", "sync_status": "pending",
                                     "intent_version": finding.version, "verification_scan_id": scan.id}
                    enqueue(db, "security_issue_sync", {"finding_id": finding.id})
                activity(db, "finding_resolved", scan=scan, finding=finding, verification=verdict)
            elif verified and finding.state == "resolved" and finding.issue.get("desired_state") == "closed":
                finding.issue = {**finding.issue, "sync_status": "pending", "intent_version": finding.version,
                                 "verification_scan_id": scan.id}
                enqueue(db, "security_issue_sync", {"finding_id": finding.id})
            elif outcome == "fixed":
                outcome = "not_observed"
            db.add(SecurityOccurrence(id=uid(), scan_id=scan.id, finding_id=finding.id,
                                      outcome=outcome, snapshot={**finding_snapshot(finding), "verification": verdict}))
        scan.status, scan.finished_at = "completed" if complete else "partial", now()
        scan.error = "; ".join(str(problem) for problem in problems)[:2000] or None
        scan.progress = {**scan.progress, "freshness": freshness}
        if supported:
            scan.grade = grade_for(db.scalars(select(SecurityFinding.severity).where(
                SecurityFinding.repository_id == scan.repository_id, SecurityFinding.state.in_(["open", "accepted_risk"]))))
        scan.stages = {**scan.stages, "report": {"status": "completed"}}
        scan.report = report_data(db, scan, problems)
        activity(db, "scan_completed", scan=scan, grade=scan.grade, status=scan.status, current=current)


def report_data(db, scan, problems):
    db.flush()
    observations = [{"finding_id": row.finding_id, "outcome": row.outcome, **row.snapshot}
                    for row in db.scalars(select(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan.id))]
    scan.coverage = {**scan.coverage, "units": work_units(scan.progress)}
    return {"schema_version": security_engine.POLICY, "repository_id": scan.repository_id,
            "scan_id": scan.id, "branch": scan.branch, "commit": scan.head_sha, "grade": scan.grade,
            "rating_policy": RATING_POLICY, "status": scan.status, "findings": observations,
            "coverage": scan.coverage, "problems": problems, "tokens": scan.progress.get("tokens"),
            "unsettled_candidates": [{"proposal": task.get("proposal", {}), "assessment": task["result"]}
                for key, task in scan.progress.get("tasks", {}).items()
                if key.startswith("assessment:") and task.get("result", {}).get("verdict") == "uncertain"],
            "generated_at": now().isoformat()}


def work_units(progress):
    return [{"task": key, **{name: value for name, value in task.items() if name not in {"result", "proposal"}}}
            for key, task in progress.get("tasks", {}).items()]


def markdown_report(report):
    lines = ["# Code Mortis security report", "", f"Commit: {report['commit']}",
             f"Status: {report['status']}", f"Grade: {report.get('grade') or 'Not rated'}", ""]
    for item in report.get("findings", []):
        evidence = item.get("evidence", {})
        lines.extend([f"## {item['title']}", "", f"{item['severity'].title()} | {item['outcome']} | {item['path']}", "",
                      evidence.get("explanation", ""), "", "### Remediation", "", evidence.get("remediation", ""),
                      "", "### Verification", "", evidence.get("verification", ""), ""])
        if verification := item.get("verification"):
            lines.extend(["### Fix verification", "", verification["verdict"], "", verification["explanation"], ""])
    for candidate in report.get("unsettled_candidates", []):
        proposal = candidate["proposal"]
        lines.extend([f"## Requires verification: {proposal.get('title', 'Security candidate')}", "",
                      proposal.get("path", ""), "", candidate["assessment"]["explanation"], ""])
    if report.get("problems"):
        lines.extend(["## Incomplete analysis", "", *(f"- {value}" for value in report["problems"]), ""])
    lines.extend(["## Coverage", "", "```json", json.dumps(report.get("coverage", {}), indent=2), "```", ""])
    return "\n".join(lines)


def refresh_repository(repository_id):
    with transaction() as db:
        repo = db.get(Repository, repository_id)
        scan = db.scalar(select(SecurityScan).where(SecurityScan.repository_id == repository_id)
                         .order_by(SecurityScan.created_at.desc(), SecurityScan.id.desc()).limit(1))
        if not repo or not repo.active or not scan:
            return
        token = github.installation_token(db, repo.installation_id)
        name, identity = repo.full_name, scan.id
    requested_at = now().isoformat()
    try:
        freshness = branch_head(name, token)
    except (github.GitHubError, ValueError):
        freshness = {"error": "Unable to verify default-branch freshness"}
    freshness = {**freshness, "checked_at": requested_at}
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == identity).with_for_update())
        if scan and scan.progress.get("freshness", {}).get("checked_at", "") <= requested_at:
            scan.progress = {**scan.progress, "freshness": freshness}


def public_scan(scan):
    result = SecurityScanOut.model_validate(scan)
    # Checkpoints are internal result transports; the report and counters are the public history.
    tasks = result.progress.get("tasks", {})
    for key, task in tasks.items():
        feedback = task.get("validation_feedback") or {}
        if key.startswith(("analysis:", "assessment:")) and ("quote" in feedback or feedback.get("kind") == "delivery"):
            assessment = key.startswith("assessment:")
            path = task.get("proposal", {}).get("path", "") if assessment else key.removeprefix("analysis:")
            if not path:
                continue
            label = "Proposed finding" if assessment else "Source analysis"
            old = f"{label} could not be validated: {path}."
            message = validation_problem(path, task["validation_feedback"], task="assessment" if assessment else "analysis")
            result.error = result.error.replace(old, message) if result.error else None
            if "problems" in result.report:
                result.report = {**result.report, "problems": [problem.replace(old, message) if isinstance(problem, str) else problem
                                                             for problem in result.report["problems"]]}
    result.progress = {key: value for key, value in result.progress.items()
                       if key not in {"tasks", "execution", "execution_seconds"}}
    result.progress["work"] = {state: sum(task["status"] == state for task in tasks.values())
                                for state in ("running", "completed", "failed", "interrupted")}
    result.progress["work"]["reused"] = sum(bool(task.get("reused")) for task in tasks.values())
    result.progress["work"]["analysis_total"] = sum(bool(value.get("code")) for value in result.coverage.get("inventory", {}).values())
    completed = sum(key.startswith("analysis:") and value["status"] == "completed" for key, value in tasks.items())
    result.progress["work"]["analysis_completed"] = completed
    analysis_stage = result.stages.get("analysis")
    if isinstance(analysis_stage, dict) and analysis_stage.get("status") in ("running", "cancelled", "failed"):
        saved, total = analysis_stage.get("completed"), analysis_stage.get("total")
        if type(saved) is int and type(total) is int and 0 <= saved < completed <= total:
            # An interrupted dispatcher may never consume the future after checkpointing.
            result.stages = {**result.stages, "analysis": {**analysis_stage, "completed": completed}}
    return result
