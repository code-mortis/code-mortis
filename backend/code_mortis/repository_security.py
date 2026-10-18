"""Repository security lifecycle. Absence never substitutes for fix evidence."""

import json
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import func, select

from . import github, ocr, runner_client, security_engine, verification
from .config import settings
from .db import now, transaction, uid
from .models import (
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
from .reviews import authorize_connection, connection_snapshot, enqueue
from .schemas import SecuritySummary
from .security import clean_text, digest, safe_repo_name, unseal
from .security_schemas import SecurityScanOut

ACTIVE = {"queued", "running", "blocked"}
STAGES = ("snapshot", "trivy", "analysis", "assessment", "verification", "report")
RATING_POLICY = "highest-unresolved-severity/v1"


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
              "limits": {"timeout_seconds": body.timeout_seconds, "token_budget": body.token_budget},
              "policy": security_engine.POLICY, "rating_policy": RATING_POLICY,
              "trivy_version": settings().trivy_version, "language": repo.settings.get("language", "English")}
    config["connection"]["model"] = body.model.strip() or (repo.settings.get("model") if repo.connection_id == connection.id else None) or connection.model
    if not config["connection"]["model"]:
        raise ValueError("Choose a model before starting a security scan.")
    if parent and config != parent.config:
        raise HTTPException(409, "Retry must use the original connection, model and limits. Start a new scan to change them.")
    run = SecurityScan(id=uid(), repository_id=repo.id, actor_id=actor.id, connection_id=connection.id,
                       parent_id=parent.id if parent else None, request_key=body.request_key,
                       branch=freshness["default_branch"], head_sha=freshness["head_sha"], config=config,
                       stages={name: {"status": "pending"} for name in STAGES},
                       progress={"freshness": freshness, "tasks": {}, "tokens": 0})
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
            data.latest_scan_id, data.latest_status, data.latest_at = scan.id, scan.status, scan.created_at
            for name in ("default_branch", "head_sha", "checked_at"):
                if fresh.get(name):
                    setattr(data, name, datetime.fromisoformat(fresh[name]) if name == "checked_at" else fresh[name])
            if grade:
                data.grade, data.grade_scan_id, data.grade_sha, data.graded_at = grade.grade, grade.id, grade.head_sha, grade.finished_at
            data.state = "current" if grade and scan.id == grade.id else "incomplete"
            current_counts = counts.get(identity, {})
            observed_grade = next((value for severity, value in [("critical", "F"), ("high", "D"), ("medium", "C"), ("low", "B")]
                                   if current_counts.get(severity)), "A")
            if grade and (observed_grade != grade.grade or current_counts.get("unknown")):
                data.state = "incomplete"
            checked = datetime.fromisoformat(fresh["checked_at"]) if fresh.get("checked_at") else None
            if fresh.get("error") or not checked or (now() - checked).total_seconds() > 600:
                data.state = "unavailable"
            elif grade and (grade.head_sha != fresh.get("head_sha") or grade.branch != fresh.get("default_branch")):
                data.state = "outdated"
        result[identity] = data
    return result


def grade_for(findings):
    severities = {row.severity for row in findings if row.state in {"open", "accepted_risk"}}
    if severities - security_engine.SEVERITIES:
        return None
    return next((grade for severity, grade in [("critical", "F"), ("high", "D"), ("medium", "C"), ("low", "B")]
                 if severity in severities), "A")


def redact_finding_text(db, finding, text):
    secrets = []
    scan = db.get(SecurityScan, finding.last_scan_id)
    connection = db.get(Connection, scan.connection_id) if scan and scan.connection_id else None
    if connection:
        credentials = json.loads(unseal(connection.secret))
        secrets = [credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values()]
    for secret in db.scalars(select(SecurityFinding).where(SecurityFinding.repository_id == finding.repository_id,
                                                          SecurityFinding.kind == "secret")):
        evidence = secret.evidence
        size = evidence.get("match_bytes", 0)
        data = text.encode()
        if 0 < size <= len(data):
            for offset in range(len(data) - size + 1):
                try:
                    candidate = data[offset:offset + size].decode()
                except UnicodeDecodeError:
                    continue
                if security_engine.secure_digest(evidence["rule_id"] + "\0" + candidate) == evidence.get("identity"):
                    secrets.append(candidate)
    return clean_text(text, tuple(secrets))


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
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if not scan or scan.status != "running" or scan.cancel_requested:
            raise ocr.ReviewStopped("Security scan is no longer running.")
        tasks = dict(scan.progress.get("tasks", {}))
        tasks[key] = {**tasks.get(key, {}), "status": state, **values}
        previous = scan.progress.get("tokens", 0)
        if values.get("tokens") is None and "tokens" in values:
            previous = None
        elif type(values.get("tokens")) is int and state in {"completed", "failed"}:
            previous = previous + values["tokens"] if previous is not None else None
        scan.progress = {**scan.progress, "tasks": tasks, "tokens": previous}


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


def observe(db, scan, evidence, existing_id=None):
    scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan.id).with_for_update().execution_options(populate_existing=True))
    if scan.status != "running" or scan.cancel_requested:
        raise ocr.ReviewStopped("Security scan is no longer authorized to record observations.")
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


def deterministic_fix(item, old, trivy_result):
    evidence, path = old["evidence"], old["path"]
    original = evidence["source_sha"]
    current_path = item.renamed(original, path)
    original_source = item.read(path, original)
    exists = bool(ocr.git(item.git, item.env, "ls-tree", item.sha, "--", current_path, stopped=item.stopped).strip())
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
            return {"verdict": "uncertain", "explanation": "The new version is not an independently established safe version for the original advisory."}
    elif evidence["kind"] == "infrastructure":
        checks = trivy_result["targets"].get(current_path, {}).get("checks", [])
        if exists and not any(check["id"] == evidence["rule_id"] and check["status"] == "PASS" for check in checks):
            return {"verdict": "uncertain", "explanation": "The original configuration check did not explicitly pass."}
    return {"verdict": "fixed", "explanation": "The original condition changed and the relevant scanner check confirms its removal.",
            "original_sha": original, "current_sha": item.sha, "path": path, "current_path": current_path,
            "original_hash": digest(original_source), "current_hash": digest(current_source) if current_source is not None else None}


def perform(scan_id, claim):
    started = time.monotonic()
    with transaction() as db:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan_id).with_for_update())
        if not scan or scan.status not in ACTIVE or scan.cancel_requested:
            return
        execution = scan.progress.get("execution")
        if execution and scan.status == "running":
            if execution == claim:
                return
            job_id, _, attempt = execution.rpartition(":")
            owner = db.get(Job, job_id) if attempt.isdigit() else None
            if owner and owner.status == "running" and owner.attempts == int(attempt) and owner.lease_until and owner.lease_until > now():
                return
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
    limits = config["limits"]

    def stopped():
        with transaction() as db:
            row = db.get(SecurityScan, scan_id)
            if not row or row.cancel_requested or row.status != "running" or row.progress.get("execution") != claim:
                return True
            authorize(db, db.get(Repository, row.repository_id), db.get(User, row.actor_id), db.get(Connection, row.connection_id), fresh=False)
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

    def ai(item, task, context, key):
        context["language"] = config.get("language", "English")
        identity = digest(json.dumps([config, context], sort_keys=True))
        cached = saved.get(key, {})
        if cached.get("status") == "completed" and cached.get("input_hash") == identity:
            checkpoint(scan_id, key, "completed", result=cached["result"], input_hash=identity, tokens=0, reused=True,
                       **({"proposal": context["proposal"]} if task == "assessment" else {}))
            return cached["result"]
        with transaction() as db:
            row = db.get(SecurityScan, scan_id)
            authorize(db, db.get(Repository, row.repository_id), db.get(User, row.actor_id), db.get(Connection, row.connection_id))
            used = row.progress.get("tokens")
        if used is None:
            raise ValueError("Token usage is unknown; further AI checks require an explicit retry.")
        remaining = None if limits["token_budget"] is None else limits["token_budget"] - used
        checkpoint(scan_id, key, "running", input_hash=identity, **({"proposal": context["proposal"]} if task == "assessment" else {}))
        try:
            result, usage = security_engine.ask(item, config["connection"], credentials, task, context,
                                                remaining, timeout(), stopped, uid())
            checkpoint(scan_id, key, "completed", result=result, input_hash=identity, tokens=usage["tokens"])
            return result
        except (verification.VerificationOutputError, runner_client.RunnerBlocked) as exc:
            tokens = getattr(exc, "usage", {}).get("tokens")
            checkpoint(scan_id, key, "failed", error="AI analysis did not complete.", tokens=tokens)
            raise
        except Exception:
            checkpoint(scan_id, key, "failed", error="AI analysis was interrupted; token consumption is unknown.", tokens=None)
            raise

    problems, observed, verdicts = [], set(), {}
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
                        raise ValueError("Original secret evidence could not be revalidated for redaction. No source was submitted to AI.") from None
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
            stage(scan_id, "analysis", "running", total=len(code_paths), completed=0)
            for number, path in enumerate(code_paths):
                if stopped():
                    raise ocr.ReviewStopped("Security scan cancelled.")
                context = {"assigned_path": path, "head_sha": item.sha, "diff": "", "sources": {"current": {path: item.source(path)}}}
                analysis = ai(item, "analysis", context, "analysis:" + path)
                candidates.extend(analysis["findings"])
                stage(scan_id, "analysis", "running", completed=number + 1)
            stage(scan_id, "analysis", "completed", files=len(code_paths), candidates=len(candidates))
            stage(scan_id, "assessment", "running", total=len(candidates), completed=0)
            for number, candidate in enumerate(candidates):
                with transaction() as db:
                    history = [finding_snapshot(row) for row in db.scalars(select(SecurityFinding).where(SecurityFinding.repository_id == scan.repository_id))]
                context = {"proposal": candidate, "history": history, "head_sha": item.sha, "diff": "",
                           "sources": {"current": {citation["path"]: item.source(citation["path"]) for citation in candidate["evidence"]}}}
                assessed = ai(item, "assessment", context, f"assessment:{number}")
                stage(scan_id, "assessment", "running", completed=number + 1)
                if assessed["verdict"] == "uncertain":
                    problems.append("A proposed security finding requires additional verification.")
                if assessed["verdict"] != "confirmed":
                    continue
                evidence = {**candidate, "kind": "code", "sources": ["ocr"], "source_sha": item.sha,
                            "source_hash": item.inventory[candidate["path"]]["hash"], "assessment": assessed,
                            "fingerprint": digest(json.dumps(["code", candidate["path"], candidate["category"],
                                                              candidate["evidence"][0]["quote"], candidate["title"]]))}
                with transaction() as db:
                    row = db.get(SecurityScan, scan_id)
                    finding = observe(db, row, evidence, assessed["finding_id"])
                    observed.add(finding.id)
            stage(scan_id, "assessment", "completed" if not problems else "partial")
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
                        verdict = deterministic_fix(item, old, result)
                except (ValueError, KeyError) as exc:
                    verdict = {"verdict": "uncertain", "explanation": item.redact(str(exc))[:1000]}
                verdicts[old["id"]] = {**verdict, "finding_version": old["version"]}
                stage(scan_id, "verification", "running", completed=len(verdicts))
                if verdict["verdict"] == "uncertain":
                    problems.append(f"An earlier finding could not be verified: {old['id']}.")
            stage(scan_id, "verification", "completed" if not problems else "partial", findings=len(verdicts))
            fresh = branch_head(full_name, token)
            finish(scan_id, verdicts, problems, fresh, bool(code_paths or result["targets"]))
    except ocr.ReviewStopped:
        with transaction() as db:
            cancel(db, db.get(SecurityScan, scan_id))
    except Exception as exc:
        message = clean_text(item.redact(str(exc)) if item else str(exc),
                             (token, credentials.get("api_key", ""), *credentials.get("extra_headers", {}).values()))
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
        if any(row["verdict"] == "uncertain" for row in verdicts.values()):
            problems.append("Previous findings still require verification.")
        complete = not problems
        for identity, verdict in verdicts.items():
            finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == identity).with_for_update())
            if finding.version != verdict["finding_version"]:
                problems.append("Finding state changed during verification.")
                complete = False
        for identity, verdict in verdicts.items():
            finding = db.get(SecurityFinding, identity)
            outcome = {"fixed": "fixed", "still_present": "present", "uncertain": "uncertain"}[verdict["verdict"]]
            if complete and current and verdict["verdict"] == "fixed" and finding.state in {"open", "accepted_risk"}:
                finding.state, finding.updated_at = "resolved", now()
                finding.version += 1
                if finding.issue.get("number"):
                    finding.issue = {**finding.issue, "desired_state": "closed", "sync_status": "pending",
                                     "intent_version": finding.version, "verification_scan_id": scan.id}
                    enqueue(db, "security_issue_sync", {"finding_id": finding.id})
                activity(db, "finding_resolved", scan=scan, finding=finding, verification=verdict)
            elif complete and current and verdict["verdict"] == "fixed" and finding.state == "resolved" and finding.issue.get("desired_state") == "closed":
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
        findings = list(db.scalars(select(SecurityFinding).where(SecurityFinding.repository_id == scan.repository_id)))
        if complete and current:
            scan.grade = grade_for(findings)
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
    try:
        freshness = branch_head(name, token)
    except (github.GitHubError, ValueError):
        freshness = {"error": "Unable to verify default-branch freshness", "checked_at": now().isoformat()}
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        if scan:
            scan.progress = {**scan.progress, "freshness": freshness}


def public_scan(scan):
    result = SecurityScanOut.model_validate(scan)
    # Checkpoints are internal result transports; the report and counters are the public history.
    tasks = result.progress.get("tasks", {})
    result.progress = {key: value for key, value in result.progress.items() if key not in {"tasks", "execution"}}
    result.progress["work"] = {state: sum(task["status"] == state for task in tasks.values())
                                for state in ("running", "completed", "failed", "interrupted")}
    result.progress["work"]["reused"] = sum(bool(task.get("reused")) for task in tasks.values())
    result.progress["work"]["analysis_total"] = sum(bool(value.get("code")) for value in result.coverage.get("inventory", {}).values())
    result.progress["work"]["analysis_completed"] = sum(key.startswith("analysis:") and value["status"] == "completed" for key, value in tasks.items())
    return result
