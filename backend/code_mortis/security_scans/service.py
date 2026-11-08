"""Repository security lifecycle. Absence never substitutes for fix evidence."""

import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import nullcontext
from datetime import datetime, timedelta
from threading import Event
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import func, select

from code_mortis.api_tokens.provenance import TransientAuthorizationError
from code_mortis.core import source as git_source
from code_mortis.core.analysis import (
    engine_snapshot,
    job_live,
    require_available_engine,
    require_execution_engine,
)
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction, uid
from code_mortis.core.errors import ReviewStopped
from code_mortis.core.security import clean_text, digest, safe_repo_name, unseal
from code_mortis.core.shared_execution import RETIRED_MESSAGE, is_shared
from code_mortis.integrations import github, runner_client
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
from code_mortis.schemas.common import SecuritySummary
from code_mortis.schemas.security import SecurityScanOut
from code_mortis.security_scans import engine as security_engine

ACTIVE = {"queued", "running", "blocked"}
STAGES = ("snapshot", "trivy", "analysis", "assessment", "verification", "report")
RATING_POLICY = "highest-unresolved-severity/v1"


def legacy_configuration(*args, **kwargs):
    from code_mortis.integrations import ocr

    return ocr.configuration(*args, **kwargs)


def rolling_analysis(paths, concurrency, submit, stopped, stop_workers):
    paths, pending = iter(paths), {}
    try:
        while True:
            while len(pending) < concurrency and not stop_workers.is_set():
                if stopped():
                    raise ReviewStopped("Security scan cancelled.")
                path = next(paths, None)
                if path is None:
                    break
                pending[submit(path)] = path
            if not pending:
                return
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            # Consume every ready result before refilling; unknown usage stops new work.
            for future in ready:
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
            raise runner_client.RunnerBlocked("authentication")
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


def create_scan(db, repo, actor, body, remote=None, parent=None):
    connection = db.get(Connection, body.connection_id)
    require_available_engine(parent.config if parent else repo.settings)
    authorized = authorize(db, repo, actor, connection, fresh=remote is None)
    freshness = branch_head(repo.full_name, github.installation_token(db, repo.installation_id), remote or authorized)
    db.scalar(select(Repository).where(Repository.id == repo.id).with_for_update())
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
    config = {"connection": connection_snapshot(connection),
              "limits": {"timeout_seconds": body.timeout_seconds, "token_budget": body.token_budget,
                         "concurrency": body.concurrency if connection.kind == "api" else 1},
              "policy": security_engine.POLICY, "rating_policy": RATING_POLICY,
              "trivy_version": settings().trivy_version, "language": repo.settings.get("language", "English")}
    config.update(engine_snapshot(parent.config if parent else repo.settings))
    if parent is None or "task_contract" in parent.config:
        config["task_contract"] = parent.config["task_contract"] if parent else security_engine.TASK_CONTRACT
    config["connection"]["model"] = body.model.strip() or (repo.settings.get("model") if repo.connection_id == connection.id else None) or connection.model
    if not config["connection"]["model"]:
        raise ValueError("Choose a model before starting a security scan.")
    if parent:
        if "concurrency" not in body.model_fields_set:
            config["limits"]["concurrency"] = parent.config["limits"].get("concurrency", 1)
        original = {**parent.config, **engine_snapshot(parent.config), "limits": {**parent.config["limits"],
                    "concurrency": config["limits"]["concurrency"]}}
        if config != original:
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
    latest = {row.repository_id: row for row in db.scalars(select(SecurityScan).where(SecurityScan.repository_id.in_(ids))
        .distinct(SecurityScan.repository_id).order_by(SecurityScan.repository_id, SecurityScan.created_at.desc(), SecurityScan.id.desc()))}
    graded = {row.repository_id: row for row in db.scalars(select(SecurityScan).where(
        SecurityScan.repository_id.in_(ids), SecurityScan.grade.is_not(None))
        .distinct(SecurityScan.repository_id).order_by(SecurityScan.repository_id, SecurityScan.created_at.desc(), SecurityScan.id.desc()))}
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
            fresh = scan.progress.get("freshness", {})
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


def checkpoint(scan_id, key, state, **values):
    authorization_error = None
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if not scan or scan.status != "running" or scan.cancel_requested:
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
        if "source_packing" in values and "source_packing" in tasks.get(key, {}):
            values["source_packing"] = tasks[key]["source_packing"]
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


def stage(scan_id, name, status, **values):
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if scan and scan.status == "running":
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
        citations = [*evidence.get("evidence", []), *evidence.get("locations", []),
                     *evidence.get("ai_analysis", {}).get("evidence", [])]
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


def observe(db, scan, evidence, existing_id=None):
    scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan.id).with_for_update().execution_options(populate_existing=True))
    if scan.status != "running" or scan.cancel_requested:
        raise ReviewStopped("Security scan is no longer authorized to record observations.")
    query = select(SecurityFinding).where(SecurityFinding.id == existing_id) if existing_id else select(SecurityFinding).where(
        SecurityFinding.repository_id == scan.repository_id, SecurityFinding.fingerprint == evidence["fingerprint"])
    finding = db.scalar(query.with_for_update().execution_options(populate_existing=True))
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
    previous = db.scalar(select(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan.id,
                                                         SecurityOccurrence.finding_id == finding.id))
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


def dependency_advisory(evidence, packages, token, stopped, cache):
    rule = evidence.get("rule_id", "")
    if evidence.get("package_type") not in {"bun", "npm", "yarn", "pnpm"} or not re.fullmatch(r"CVE-\d{4}-\d{4,}", rule):
        return None
    if stopped():
        raise ReviewStopped("Security scan stopped before advisory verification.")
    if rule not in cache:
        try:
            cache[rule] = (github.request("GET", f"/advisories?cve_id={rule}&type=reviewed&per_page=100", token), now().isoformat())
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
        if engine_snapshot(scan.config)["analysis_engine"] == "mortis":
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
            if engine_snapshot(scan.config)["analysis_engine"] != "mortis":
                scan.status, scan.error, scan.finished_at = "partial", "Worker stopped; usage may have been consumed. Retry explicitly.", now()
                interrupt_work(scan)
                scan.report = report_data(db, scan, [scan.error])
                return
        repo, actor, connection = db.get(Repository, scan.repository_id), db.get(User, scan.actor_id), db.get(Connection, scan.connection_id)
        authorize(db, repo, actor, connection)
        if connection.kind != "api":
            db.scalar(select(Connection).where(Connection.id == connection.id).with_for_update())
            if connection_busy(db, connection.id, scan_id=scan.id):
                scan.status = "blocked"
                raise runner_client.RunnerBlocked("busy")
        current_connection = connection_snapshot(connection)
        if any(current_connection[key] != scan.config["connection"][key]
               for key in ("kind", "owner_id", "protocol", "base_url", "provider", "options")):
            raise ValueError("The AI connection changed. Start a new security scan.")
        scan.status, scan.started_at = "running", scan.started_at or now()
        scan.progress = {**scan.progress, "execution": claim}
        config, full_name = scan.config, repo.full_name
        credentials = json.loads(unseal(connection.secret))
        token = github.installation_token(db, repo.installation_id)
        scan.progress = {**scan.progress, "freshness": branch_head(repo.full_name, token)}
        previous = [finding_snapshot(row) for row in db.scalars(select(SecurityFinding).where(
            SecurityFinding.repository_id == repo.id))]
        saved = scan.progress.get("tasks", {})
        if scan.parent_id:
            parent = db.get(SecurityScan, scan.parent_id)
            saved = parent.progress.get("tasks", {})
    mortis = engine_snapshot(config)["analysis_engine"] == "mortis"
    limits = config["limits"]
    concurrency = limits.get("concurrency", 1) if config["connection"]["kind"] == "api" else 1
    stop_workers = Event()

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
            except (ValueError, runner_client.RunnerBlocked):
                return True
            job_id, _, attempt = claim.rpartition(":")
            job = db.get(Job, job_id) if attempt.isdigit() else None
            if job:
                if job.status != "running" or job.attempts != int(attempt):
                    return True
                if not job.lease_until or job.lease_until < now() + timedelta(seconds=300):
                    job.lease_until = now() + timedelta(seconds=360)
        if limits["timeout_seconds"] is not None and time.monotonic() - started >= limits["timeout_seconds"]:
            raise TimeoutError("Security scan reached its time limit. Retry with a larger limit.")
        return False

    def timeout():
        return max(1, int(limits["timeout_seconds"] - (time.monotonic() - started))) if limits["timeout_seconds"] is not None else None

    def task_input(task, context, key):
        context = {**context, "language": config.get("language", "English"),
                   "task_contract": config.get("task_contract", "security-task/v2")}
        if task == "assessment":
            context["assessment_policy"] = digest(security_engine.INSTRUCTIONS + security_engine.ASSESSMENT_INSTRUCTIONS)
        cache_config = {**config, "limits": {key: value for key, value in limits.items() if key != "concurrency"}}
        identity = digest(json.dumps([cache_config, context], sort_keys=True))
        cached = saved.get(key, {})
        reusable = (cached.get("status") == "completed" and cached.get("input_hash") == identity
                    and cached.get("result", {}).get("verdict") != "uncertain")
        return context, identity, cached if reusable else None

    def ai(item, task, context, key, allowance=None, *, parallel=False):
        context, identity, cached = task_input(task, context, key)
        if cached is not None:
            checkpoint(scan_id, key, "completed", result=cached["result"], input_hash=identity, tokens=0, reused=True,
                       **({"citation_contract": cached["citation_contract"]} if "citation_contract" in cached else {}),
                       **({"source_packing": cached["source_packing"]} if "source_packing" in cached else {}),
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
            packing = row.progress.get("tasks", {}).get(key, {}).get("source_packing",
                saved.get(key, {}).get("source_packing"))
        if used is None:
            raise ValueError("Token usage is unknown; further AI checks require an explicit retry.")
        remaining = None if limits["token_budget"] is None else limits["token_budget"] - used
        if allowance is not None:
            remaining = min(remaining, allowance)
        if remaining is not None and remaining <= 0 and not mortis:
            raise verification.VerificationOutputError("Security token budget exhausted.", {"tokens": 0})
        if remaining is not None:
            remaining = max(0, remaining)
        checkpoint(scan_id, key, "running", input_hash=identity, **({"proposal": context["proposal"]} if task == "assessment" else {}))
        usage = {}
        try:
            def select_packing(value):
                return checkpoint(scan_id, key, "running", source_packing=value)["source_packing"]

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
                        if (selected.secret != connection.secret or connection_snapshot(selected) != current_connection):
                            raise ReviewStopped("The shared connection changed during the security scan.")
                        authorize(db, db.get(Repository, current.repository_id), db.get(User, current.actor_id), selected)

                return execute(config["connection"], credentials, context, instructions=instructions, schema=schema,
                    validator=validator, claim=claim, stage="security", task_key=f"{key}:round:{round}", scan_id=scan_id,
                    stopped=stopped, authorize=authorized, effort="high", token_budget=budget, timeout=timeout)

            result, usage = security_engine.ask(item, config["connection"], credentials, task, context,
                                                remaining, timeout(), stopped, uid(),
                                                **({"configuration_managed": True} if parallel else {}),
                                                **({"infer": infer, "source_packing": packing,
                                                    "select_packing": select_packing} if mortis else {}))
            checkpoint(scan_id, key, "completed", result=result, input_hash=identity, tokens=usage["tokens"],
                       citation_contract=security_engine.CITATION_CONTRACT)
            return result
        except TransientAuthorizationError:
            raise
        except ReviewStopped as exc:
            # Revocation blocks results, but already consumed inference still belongs in history.
            with transaction() as db:
                row = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
                if row and row.progress.get("execution") == claim:
                    tokens = getattr(exc, "usage", usage).get("tokens")
                    tokens = tokens if type(tokens) is int and tokens >= 0 else None
                    used = row.progress.get("tokens")
                    tasks = dict(row.progress.get("tasks", {}))
                    tasks[key] = {**tasks.get(key, {}), "status": "failed", "tokens": tokens,
                                  "error": "Security inference stopped before result acceptance."}
                    row.progress = {**row.progress, "tasks": tasks,
                                    "tokens": used + tokens if used is not None and tokens is not None else None}
            raise
        except (verification.VerificationOutputError, runner_client.RunnerBlocked) as exc:
            tokens = getattr(exc, "usage", {}).get("tokens")
            feedback = getattr(exc, "feedback", None)
            try:
                detail = item.redact(str(exc))[:2000]
                feedback = security_engine.redacted_context(item, feedback) if feedback else None
            except Exception:
                detail, feedback = f"AI task failed ({type(exc).__name__}); diagnostic text could not be safely sanitized.", None
            checkpoint(scan_id, key, "failed", error=detail, tokens=tokens,
                       **({"validation_feedback": feedback} if feedback else {}))
            raise
        except Exception:
            checkpoint(scan_id, key, "failed", error="AI analysis was interrupted; token consumption is unknown.", tokens=None)
            raise

    problems, observed, verdicts, advisories = [], set(), {}, {}
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
            result = security_engine.trivy(item, timeout(), stopped)
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
                    observed.add(finding.id)
            stage(scan_id, "trivy", "completed" if not result["problems"] else "partial", findings=len(result["findings"]))
            candidates = []
            code_paths = [path for path, value in item.inventory.items() if value["code"]]
            completed, failed = 0, 0
            stage(scan_id, "analysis", "running", total=len(code_paths), completed=0, failed=0, concurrency=concurrency)

            parallel = concurrency > 1 and len(code_paths) > 1
            configuration = (legacy_configuration(config["connection"], credentials, {}, stopped, session_per_task=True, structured_result=True)
                             if parallel and not mortis else nullcontext())
            with configuration, ThreadPoolExecutor(max_workers=concurrency) as pool:
                rolling = mortis and parallel and limits["token_budget"] is None
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
                            fatal = fatal or exc
                            stop_workers.set()
                        stage(scan_id, "analysis", "running", completed=completed, failed=failed)
                    for path in batch:
                        candidates.extend(results.get(path, {}).get("findings", []))
                    if fatal:
                        raise fatal
            stage(scan_id, "analysis", "partial" if failed else "completed", files=len(code_paths), candidates=len(candidates))
            assessment_completed, assessment_failed = 0, 0
            assessment_problem_count = len(problems)
            stage(scan_id, "assessment", "running", total=len(candidates), completed=0, failed=0, concurrency=concurrency)
            remaining = [SimpleNamespace(id=number, position=number, proposal=candidate) for number, candidate in enumerate(candidates)]
            parallel = concurrency > 1 and len(candidates) > 1
            configuration = (legacy_configuration(config["connection"], credentials, {}, stopped, session_per_task=True, structured_result=True)
                             if parallel and not mortis else nullcontext())
            with configuration, ThreadPoolExecutor(max_workers=concurrency) as pool:
                while remaining:
                    if stopped():
                        raise ReviewStopped("Security scan cancelled.")
                    with transaction() as db:
                        history = [finding_snapshot(row) for row in db.scalars(select(SecurityFinding).where(
                            SecurityFinding.repository_id == scan.repository_id).order_by(SecurityFinding.id))]
                        used = db.get(SecurityScan, scan_id).progress.get("tokens")
                    batch = assessment_batch(remaining, history, item, concurrency)
                    contexts = {candidate.id: {"proposal": candidate.proposal,
                        "history": assessment_history(candidate.proposal, history, item), "head_sha": item.sha, "diff": "",
                        "sources": {"current": {citation["path"]: item.source(citation["path"])
                                   for citation in candidate.proposal["evidence"]}}} for candidate in batch}
                    needed = sum(task_input("assessment", contexts[row.id], f"assessment:{row.id}")[2] is None for row in batch)
                    if used is None:
                        raise ValueError("Token usage is unknown; further AI checks require an explicit retry.")
                    allowance = None if limits["token_budget"] is None or not needed else (limits["token_budget"] - used) // needed
                    pending = {}
                    for candidate in batch:
                        pending[pool.submit(ai, item, "assessment", contexts[candidate.id], f"assessment:{candidate.id}", allowance,
                                            parallel=parallel)] = candidate
                    results, fatal = {}, None
                    for future in as_completed(pending):
                        candidate = pending[future]
                        try:
                            results[candidate.id] = future.result()
                        except verification.VerificationOutputError as exc:
                            if exc.feedback and type(exc.usage.get("tokens")) is int:
                                assessment_failed += 1
                                problems.append(validation_problem(candidate.proposal["path"], exc.feedback, task="assessment"))
                            else:
                                fatal = fatal or exc
                                stop_workers.set()
                        except Exception as exc:
                            fatal = fatal or exc
                            stop_workers.set()
                        stage(scan_id, "assessment", "running", failed=assessment_failed)
                    if fatal:
                        raise fatal
                    if stopped():
                        raise ReviewStopped("Security scan cancelled.")
                    with transaction() as db:
                        row = db.get(SecurityScan, scan_id)
                        authorize(db, db.get(Repository, row.repository_id), db.get(User, row.actor_id), db.get(Connection, row.connection_id))
                        # Check once before this batch's own ordered history updates.
                        current = [finding_snapshot(row) for row in db.scalars(select(SecurityFinding).where(
                            SecurityFinding.repository_id == scan.repository_id).order_by(SecurityFinding.id).with_for_update())]
                        if current != history:
                            raise ValueError("Finding history changed during assessment. Retry this security scan.")
                        for candidate in batch:
                            assessed = results.get(candidate.id)
                            if assessed is None:
                                continue
                            assessment_completed += 1
                            if assessed["verdict"] == "uncertain":
                                problems.append("A proposed security finding requires additional verification.")
                            if assessed["verdict"] != "confirmed":
                                continue
                            proposal = candidate.proposal
                            evidence = {**proposal, "kind": "code", "sources": ["mortis" if mortis else "ocr"], "source_sha": item.sha,
                                        "source_hash": item.inventory[proposal["path"]]["hash"], "assessment": assessed,
                                        "fingerprint": digest(json.dumps(["code", proposal["path"], proposal["category"],
                                                                          proposal["evidence"][0]["quote"], proposal["title"]]))}
                            finding = observe(db, db.get(SecurityScan, scan_id), evidence, assessed["finding_id"])
                            observed.add(finding.id)
                    stage(scan_id, "assessment", "running", completed=assessment_completed)
                    remaining = [row for row in remaining if row not in batch]
            stage(scan_id, "assessment", "completed" if len(problems) == assessment_problem_count else "partial")
            stage(scan_id, "verification", "running", total=sum(old["id"] not in observed and
                (old["state"] in {"open", "accepted_risk"} or old["pending_close"]) for old in previous), completed=0)
            for old in previous:
                if old["id"] in observed or (old["state"] not in {"open", "accepted_risk"} and not old["pending_close"]):
                    continue
                try:
                    if old["kind"] == "code":
                        original = old["evidence"]["source_sha"]
                        current_path = item.renamed(original, old["path"])
                        context = {"finding": old, "original_sha": original, "head_sha": item.sha,
                                   "diff": item.diff(original), "sources": {
                                       "original": {old["path"]: item.source(old["path"], original)},
                                       "current": {current_path: item.source(current_path)} if current_path in item.inventory else {}}}
                        verdict = ai(item, "verification", context, "verification:" + old["id"])
                    else:
                        verdict = deterministic_fix(item, old, result, advisory_lookup=lambda evidence, packages:
                            dependency_advisory(evidence, packages, token, stopped, advisories))
                except (ValueError, KeyError) as exc:
                    verdict = {"verdict": "uncertain", "explanation": item.redact(str(exc))[:1000]}
                verdicts[old["id"]] = {**verdict, "finding_version": old["version"]}
                stage(scan_id, "verification", "running", completed=len(verdicts))
            stage(scan_id, "verification", "partial" if any(row["verdict"] == "uncertain" for row in verdicts.values()) else "completed", findings=len(verdicts))
            fresh = branch_head(full_name, token)
            finish(scan_id, verdicts, problems, fresh, bool(code_paths or result["targets"]))
    except TransientAuthorizationError:
        with transaction() as db:
            row = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
            if row and row.status == "running" and row.progress.get("execution") == claim:
                interrupt_work(row)
                row.status = "queued"
                row.progress = {**row.progress, "execution": None}
        raise
    except ReviewStopped:
        with transaction() as db:
            cancel(db, db.get(SecurityScan, scan_id))
    except Exception as exc:
        try:
            message = clean_text(item.redact(str(exc)) if item else str(exc),
                                 (token, credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values()))
        except Exception:
            message = f"Security scan failed ({type(exc).__name__}); diagnostic text could not be safely sanitized."
        with transaction() as db:
            row = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
            if row and row.status in ACTIVE:
                row.status = "blocked" if isinstance(exc, runner_client.RunnerBlocked) else "partial" if observed else "failed"
                row.error, row.finished_at = message[:2000], now()
                row.stages = {name: {**value, "status": "failed" if value.get("status") == "running" else "skipped"}
                              if value.get("status") in {"running", "pending"} else value for name, value in row.stages.items()}
                row.report = report_data(db, row, problems + [message[:2000]])
                activity(db, "scan_failed", scan=row, reason=row.error)


def finish(scan_id, verdicts, problems, freshness, supported):
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        if not scan or scan.status != "running" or scan.cancel_requested:
            return
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        if _mcp.scan_revoked(db, scan, repository_id=scan.repository_id):
            cancel(db, scan, reason=_mcp.REVOKED_MESSAGE)
            return
        if _pat.scan_revoked(db, scan, repository_id=scan.repository_id):
            cancel(db, scan, reason=_pat.REVOKED_MESSAGE)
            return
        authorize(db, db.get(Repository, scan.repository_id), db.get(User, scan.actor_id), db.get(Connection, scan.connection_id))
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if not scan or scan.status != "running" or scan.cancel_requested:
            return
        repo, actor, connection = db.get(Repository, scan.repository_id), db.get(User, scan.actor_id), db.get(Connection, scan.connection_id)
        authorize(db, repo, actor, connection, fresh=False)
        current = scan.head_sha == freshness["head_sha"] and scan.branch == freshness["default_branch"]
        if not supported:
            problems.append("No supported security analysis targets were available.")
        if db.scalar(select(SecurityFinding.id).where(SecurityFinding.repository_id == scan.repository_id,
            SecurityFinding.state.in_(["open", "accepted_risk"]), SecurityFinding.severity.not_in(security_engine.SEVERITIES)).limit(1)):
            problems.append("An unresolved finding has unclassified severity; the repository cannot be graded.")
        # Coverage failures still block resolution; another finding's uncertainty or stale version does not.
        resolution_ready = not problems
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
            verified = resolution_ready and current and finding.version == verdict["finding_version"] and verdict["verdict"] == "fixed"
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
    result.progress = {key: value for key, value in result.progress.items() if key not in {"tasks", "execution"}}
    result.progress["work"] = {state: sum(task["status"] == state for task in tasks.values())
                                for state in ("running", "completed", "failed", "interrupted")}
    result.progress["work"]["reused"] = sum(bool(task.get("reused")) for task in tasks.values())
    result.progress["work"]["analysis_total"] = sum(bool(value.get("code")) for value in result.coverage.get("inventory", {}).values())
    result.progress["work"]["analysis_completed"] = sum(key.startswith("analysis:") and value["status"] == "completed" for key, value in tasks.items())
    return result
