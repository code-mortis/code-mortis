import json
import subprocess
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from code_mortis import github, jobs, native_runner, security_engine, security_issues
from code_mortis import repository_security as security
from code_mortis.db import now, transaction, uid
from code_mortis.models import (
    ConnectionGrant,
    Job,
    Repository,
    SecurityFinding,
    SecurityOccurrence,
    SecurityScan,
)
from code_mortis.security import digest
from code_mortis.security_schemas import SecurityScanInput

SHA = "a" * 40
OLD = "b" * 40


@pytest.fixture
def security_github(github_stub, monkeypatch):
    remote = {"id": 22, "private": True, "default_branch": "main", "permissions": {"pull": True, "push": True}}
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: remote)
    monkeypatch.setattr(github, "request", lambda method, path, token, **kwargs: {"sha": SHA} if "/commits/" in path else remote)
    return remote


def saved_scan(db, seed, status="running", sha=SHA, **values):
    scan = SecurityScan(id=uid(), repository_id=seed["repo"].id, actor_id=seed["user"].id,
        connection_id=seed["connection"].id, request_key=uid(), branch="main", head_sha=sha,
        config={"connection": security.connection_snapshot(seed["connection"]), "limits": {"token_budget": 100000, "timeout_seconds": 900}},
        status=status, progress={"tasks": {}, "tokens": 0}, stages={}, **values)
    db.add(scan)
    db.flush()
    return scan


def saved_finding(db, seed, scan, kind="code", severity="high", state="open", **extra):
    evidence = {"kind": kind, "fingerprint": digest(uid()), "title": "Missing ownership check", "path": "app.py",
                "severity": severity, "source_sha": OLD, "source_hash": digest("unsafe"), "sources": ["ocr"],
                "explanation": "Another account's resource can be read.", "remediation": "Check ownership.",
                "verification": "Test access using another account.", **extra}
    finding = SecurityFinding(id=uid(), repository_id=seed["repo"].id, fingerprint=evidence["fingerprint"],
        state=state, kind=kind, severity=severity, title=evidence["title"], path=evidence["path"],
        first_scan_id=scan.id, last_scan_id=scan.id, evidence=evidence, version=0)
    db.add(finding)
    db.flush()
    return finding


def freshness(sha=SHA):
    return {"head_sha": sha, "default_branch": "main", "checked_at": now().isoformat()}


@pytest.mark.parametrize(("severity", "grade"), [(None, "A"), ("low", "B"), ("medium", "C"), ("high", "D"), ("critical", "F"), ("unknown", None)])
def test_rating_uses_highest_unresolved_severity(severity, grade):
    rows = [SimpleNamespace(state="open", severity=severity)] if severity else []
    assert security.grade_for(rows) == grade
    assert security.grade_for([SimpleNamespace(state="false_positive", severity="critical")]) == "A"
    assert security.grade_for([SimpleNamespace(state="accepted_risk", severity="critical")]) == "F"


def test_create_scan_is_independent_of_pr_reviews_and_idempotent(seed, security_github, client):
    from code_mortis.models import Repository

    with transaction() as db:
        db.get(Repository, 22).enabled = False
    body = {"connection_id": "connection", "request_key": uid(), "token_budget": 100000, "timeout_seconds": 900}
    first = client.post("/api/v1/security/repositories/22/scans", json=body)
    assert first.status_code == 202, first.text
    scan = first.json()
    assert scan["head_sha"] == SHA and scan["branch"] == "main" and scan["status"] == "queued"
    assert "tasks" not in scan["progress"]
    assert client.post("/api/v1/security/repositories/22/scans", json=body).json()["id"] == scan["id"]
    assert client.post("/api/v1/security/repositories/22/scans", json={**body, "request_key": uid()}).status_code == 409
    with transaction() as db:
        assert db.scalar(select(Job.kind)) == "security_scan"
    assert jobs.job_queue("security_scan") == "security"
    assert jobs.job_queue("security_issue_publish") == "sync"


def test_scan_reads_and_writes_recheck_github_access(seed, security_github, client, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed)
        identity = scan.id
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: (_ for _ in ()).throw(github.GitHubError(403, "Denied")))
    assert client.get(f"/api/v1/security/scans/{identity}").status_code == 403
    assert client.post(f"/api/v1/security/scans/{identity}/cancel").status_code == 403


def test_personal_scans_require_owner_and_repository_grant(seed, security_github):
    with transaction() as db:
        connection = db.get(type(seed["connection"]), "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        body = SecurityScanInput(connection_id="connection", request_key=uid())
        with pytest.raises(ValueError, match="Authorize this repository"):
            security.create_scan(db, seed["repo"], seed["user"], body)
        db.add(ConnectionGrant(connection_id="connection", repository_id=22, automatic=False))
        db.flush()
        assert security.create_scan(db, seed["repo"], seed["user"], body).config["connection"]["kind"] == "chatgpt"


@pytest.mark.parametrize(("complete", "current", "resolved"), [(True, True, True), (False, True, False), (True, False, False)])
def test_resolution_requires_complete_current_evidence(seed, security_github, complete, current, resolved):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        scan_id, finding_id = scan.id, finding.id
    security.finish(scan_id, {finding_id: {"verdict": "fixed", "explanation": "Ownership is now checked.", "finding_version": 0}},
                    [] if complete else ["Other source was not analyzed"], freshness(SHA if current else "c" * 40), True)
    with transaction() as db:
        scan, finding = db.get(SecurityScan, scan_id), db.get(SecurityFinding, finding_id)
        assert (finding.state == "resolved") == resolved
        assert scan.grade == ("A" if resolved else None)
        assert scan.report["commit"] == SHA


def test_missing_later_observation_is_not_a_fix(seed, security_github, monkeypatch):
    monkeypatch.setattr(security.ocr, "git", lambda *args, **kwargs: "regular file")
    item = SimpleNamespace(renamed=lambda sha, path: path, read=lambda path, sha=None: "unsafe", diff=lambda sha: "",
                           git=None, env={}, sha=SHA, stopped=lambda: False)
    old = {"evidence": {"kind": "dependency", "source_sha": OLD}, "path": "requirements.txt"}
    assert security.deterministic_fix(item, old, {"targets": {}})["verdict"] == "uncertain"


def test_summaries_keep_historical_grade_neutral_after_partial_scan(seed):
    with transaction() as db:
        prior = saved_scan(db, seed, "completed", grade="A", finished_at=now() - timedelta(minutes=5))
        prior.created_at = now() - timedelta(minutes=5)
        latest = saved_scan(db, seed, "partial")
        latest.progress = {"freshness": freshness()}
        saved_finding(db, seed, latest, severity="critical")
        summary = security.summaries(db, [22])[22]
        assert summary.grade == "A" and summary.state == "incomplete"
        assert summary.counts == {"critical": 1}
        latest.progress = {"freshness": freshness("c" * 40)}
        assert security.summaries(db, [22])[22].state == "outdated"
        latest.progress = {"freshness": {"checked_at": now().isoformat(), "error": "Unavailable"}}
        assert security.summaries(db, [22])[22].state == "unavailable"


def test_finding_recurrence_reuses_identity_and_invalidates_rotation(seed):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan, kind="secret", state="resolved")
        finding.rotation = {"identity": "old"}
        finding.issue = {"number": 9, "closed_by_app": True}
        evidence = {**finding.evidence, "source_sha": SHA}
        found = security.observe(db, scan, evidence)
        assert found.id == finding.id and found.state == "open" and found.rotation is None
        assert found.issue["desired_state"] == "open"
        assert found.issue["intent_version"] == found.version


def test_reworded_or_renamed_confirmed_finding_updates_canonical_location_without_duplication(seed):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        found = security.observe(db, scan, {**finding.evidence, "path": "renamed.py", "title": "Ownership guard missing"}, finding.id)
        assert found.id == finding.id and found.path == "renamed.py" and found.title == "Ownership guard missing"
        assert len(list(db.scalars(select(SecurityFinding)))) == 1


def test_reports_preserve_snapshots_after_triage(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed, "completed")
        finding = saved_finding(db, seed, scan)
        db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        scan.report = security.report_data(db, scan, [])
        scan_id, finding_id = scan.id, finding.id
    assert client.patch(f"/api/v1/security/findings/{finding_id}", json={"state": "false_positive", "reason": "Protected by middleware", "version": 0}).status_code == 200
    report = client.get(f"/api/v1/security/scans/{scan_id}/report").json()
    assert report["findings"][0]["state"] == "open"
    assert client.get(f"/api/v1/security/scans/{scan_id}/report?format=markdown").headers["content-type"].startswith("text/markdown")
    assert client.get(f"/api/v1/security/findings/{finding_id}").json()["finding"]["state"] == "false_positive"


@pytest.mark.parametrize(("current", "fixed", "safe"), [
    ("1.2.4", "1.2.3", True), ("1.2.2", "1.2.3", False), ("1.1.0", "1.0.7, 1.1.4", False),
    ("1.0.8", "1.0.7, 1.1.4", True), ("2.0.0", "1.0.7, 1.1.4", True),
    ("1.2.3-rc.1", "1.2.3", False), ("1!2.0", "1!2.0", True), ("1.0", "", False),
])
def test_dependency_resolution_does_not_guess_version_schemes(current, fixed, safe):
    assert security.safe_dependency_version(current, fixed) == safe


def test_trivy_secret_values_are_hmac_identified_and_removed_from_evidence(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    raw = "API_TOKEN = 'synthetic-credential-for-tests'\n"
    value = "synthetic-credential-for-tests"
    item.read = lambda path, sha=None: raw
    item.inventory = {"app.py": {"hash": digest(raw), "lines": 1}}
    report = {"Results": [{"Target": str(item.files / "app.py"), "Class": "secret", "Secrets": [
        {"RuleID": "test-token", "Title": "Exposed token", "Severity": "HIGH", "StartLine": 1, "EndLine": 1,
         "Offset": raw.index(value), "Match": raw.strip().replace(value, "*" * len(value))}]}]}
    result = security_engine.normalize_trivy(item, report)
    assert value not in json.dumps(result)
    assert value not in item.source("app.py")
    finding = result["findings"][0]
    assert finding["identity"] == security_engine.secure_digest("test-token\0" + value)
    assert raw.encode()[finding["match_offset"]:finding["match_offset"] + finding["match_bytes"]].decode() == value
    assert security_engine.secure_digest(value) != digest(value)


def test_trivy_refuses_targets_outside_prepared_snapshot(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    with pytest.raises(ValueError):
        security_engine.normalize_trivy(item, {"Results": [{"Target": "../outside", "Secrets": []}]})


def test_snapshot_never_follows_symlinks_or_executes_hooks(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, security_engine.ocr.child_environment(), lambda: False)
    item.git.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=item.git, check=True)
    (item.git / "app.py").write_text("print('fixture')\n")
    (item.git / "escape").symlink_to("/etc/passwd")
    subprocess.run(["git", "add", "app.py", "escape"], cwd=item.git, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture", "--no-verify"], cwd=item.git, check=True)
    item.sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=item.git, text=True).strip()
    (item.git / ".git/hooks/post-checkout").write_text("#!/bin/sh\ntouch ../executed\n")
    (item.git / ".git/hooks/post-checkout").chmod(0o700)
    item.populate()
    assert list(item.inventory) == ["app.py"]
    assert not (item.files / "escape").exists() and not (tmp_path / "executed").exists()
    assert item.excluded[0]["reason"] == "Symlink or submodule is not followed"


def test_scanner_environment_excludes_all_application_and_provider_credentials(tmp_path, monkeypatch):
    for key in ("AWS_SECRET_ACCESS_KEY", "CM_DATABASE_URL", "CM_RUNNER_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.setenv(key, "private-value")
    env = security_engine.scanner_environment(tmp_path)
    assert "private-value" not in env.values()
    assert env["HOME"] == str(tmp_path)


def test_citations_and_native_task_boundaries():
    evidence = [{"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}]
    assert security_engine.validate_citations(evidence, {"current": {"app.py": "unsafe\n"}}) == {"current"}
    with pytest.raises(ValueError):
        security_engine.validate_citations(evidence, {"current": {"app.py": "safe\n"}})
    for task in ("security_analysis", "security_verification"):
        instructions = native_runner.task_instructions(SimpleNamespace(task=task))
        assert "untrusted data" in instructions and "Do not execute code" in instructions
    assert native_runner.CODEX_CONFIG["features.shell_tool"] is False
    assert native_runner.CODEX_CONFIG["permissions.mortis-review.network.enabled"] is False


def test_complete_pipeline_uses_existing_ai_transport_and_persists_grade(seed, security_github, monkeypatch, tmp_path):
    item = SimpleNamespace(inventory={"app.py": {"code": True, "hash": digest("safe"), "lines": 1}},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, env={})

    @contextmanager
    def snapshot(*args):
        yield item

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args: {"findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}})
    calls = []

    def ask(*args):
        calls.append(args[3])
        return {"findings": [], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid()))
        identity = scan.id
    security.perform(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "completed", scan.error
        assert scan.grade == "A" and scan.progress["tokens"] == 12
        assert scan.report["schema_version"] == security_engine.POLICY
    assert calls == ["analysis"]


def test_github_issue_creation_is_explicit_and_reconciles_unknown_delivery(seed, security_github, monkeypatch, client):
    security_github["private"] = False
    with transaction() as db:
        db.get(Repository, 22).private = False
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        identity = finding.id
    draft = client.get(f"/api/v1/security/findings/{identity}/github-issue").json()
    assert draft["public"] is True
    approved = client.post(f"/api/v1/security/findings/{identity}/github-issue", json={"title": draft["title"], "body": draft["body"], "version": 0, "public": True})
    assert approved.status_code == 202
    posted = []

    def request(method, path, token, **kwargs):
        assert method == "POST" and path.endswith("/issues")
        posted.append(kwargs["json"])
        raise github.GitHubError(503, "Unknown outcome")

    monkeypatch.setattr(github, "request", request)
    security_issues.publish(identity)
    assert len(posted) == 1
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "open",
              "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
              "user": {"login": "mortis-test[bot]", "type": "Bot"}, "body": posted[0]["body"]}
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [remote])
    security_issues.publish(identity)
    security_issues.publish(identity)
    assert len(posted) == 1
    with transaction() as db:
        assert db.get(SecurityFinding, identity).issue["number"] == 9


def test_issue_permission_upgrade_is_actionable(monkeypatch):
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: {"token": "test", "permissions": {"issues": "read"}})
    with pytest.raises(github.GitHubError, match="Issues: Read and write"):
        github.installation_token_from_jwt("jwt", 10, issues=True)


def test_issue_sync_does_not_infer_a_fix_from_human_closure(seed, security_github, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed, "completed")
        finding = saved_finding(db, seed, scan)
        identity = finding.id
        operation = uid()
        finding.issue = {"number": 9, "id": 99, "node_id": "issue-node", "operation_id": operation,
                         "actor_id": 7, "closed_by_app": False}
        body = security_issues.marker(finding, operation)
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "closed", "body": body,
              "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
              "user": {"login": "mortis-test[bot]", "type": "Bot"}, "closed_by": {"login": "owner"}}
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: remote)
    security_issues.sync(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.state == "open" and finding.issue["remote_state"] == "closed"
        assert finding.issue["closed_by_app"] is False


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
def test_security_transports_validate_terminal_empty_result_and_evidence_expansion(tmp_path, monkeypatch, kind):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    item.read = lambda path, sha=None: "safe guard"
    calls = []

    def api(*args, **kwargs):
        calls.append(args[4])
        raw = {"findings": [], "required_paths": ["guard.py"] if len(calls) == 1 else []}
        assert kwargs["schema"]["additionalProperties"] is False
        return kwargs["validator"](raw, args[4]), {"tokens": 7}

    def native(connection, identity, payload, stopped):
        calls.append(payload)
        assert payload["task"] == "security_analysis" and "untrusted data" in payload["prompt"]
        return {"findings": [], "required_paths": ["guard.py"] if len(calls) == 1 else []}, {"tokens": 7}

    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    monkeypatch.setattr(security_engine.runner_client, "review", native)
    result, usage = security_engine.ask(item, {"id": "connection", "kind": kind, "model": "test"}, {}, "analysis",
        {"assigned_path": "app.py", "diff": "", "sources": {"current": {"app.py": "safe"}}}, 100, 60, lambda: False, uid())
    assert result == {"findings": [], "required_paths": []} and usage == {"tokens": 14}
    assert len(calls) == 2


def test_failed_expansion_keeps_already_consumed_tokens(tmp_path, monkeypatch):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"guard.py": {}}
    item.read = lambda *args: "safe"
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise security_engine.verification.VerificationOutputError("Invalid result", {"tokens": 5})
        return {"findings": [], "required_paths": ["guard.py"]}, {"tokens": 7}

    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    with pytest.raises(security_engine.verification.VerificationOutputError) as error:
        security_engine.ask(item, {"kind": "api"}, {}, "analysis", {"diff": "", "sources": {"current": {}}}, 100, 60, lambda: False, uid())
    assert error.value.usage["tokens"] == 12


def test_secret_censored_multiline_identity_is_recovered_without_persisting_material(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    source = "key = '''BEGIN\nsynthetic-private-material\nEND'''\n"
    material = "BEGIN\nsynthetic-private-material\nEND"
    item.read = lambda *args: source
    raw = {"Offset": source.index("BEGIN"), "StartLine": 1, "EndLine": 3,
           "Match": "key = '''*****", "Code": {"Lines": [{"Number": 3, "Content": "***'''"}]}}
    value, offset = security_engine.secret_material(item, "app.py", raw)
    assert value == material and source[offset:offset + len(value)] == value
    item.secret_values.append(value)
    assert material not in item.redact(source)
    assert "synthetic-private-material" not in item.redact(json.dumps({"source": source}))


@pytest.mark.parametrize(("acknowledged", "present", "outcome"), [(False, False, "uncertain"), (True, False, "fixed"), (True, True, "still_present")])
def test_secret_resolution_requires_rotation_and_repository_wide_removal(monkeypatch, acknowledged, present, outcome):
    material = "synthetic-private-material"
    evidence = {"kind": "secret", "source_sha": OLD, "rule_id": "fixture", "match_offset": 0, "match_bytes": len(material),
                "identity": security_engine.secure_digest("fixture\0" + material)}
    old = {"path": "secret.txt", "evidence": evidence, "rotation": {"identity": evidence["identity"]} if acknowledged else None}
    item = SimpleNamespace(sha=SHA, git=None, env={}, stopped=lambda: False, inventory={"elsewhere.txt": {}},
        renamed=lambda sha, path: path, diff=lambda sha: "removed", read=lambda path, sha=None: material if sha or present else "safe")
    monkeypatch.setattr(security.ocr, "git", lambda *args, **kwargs: "")
    assert security.deterministic_fix(item, old, {"targets": {}})["verdict"] == outcome


@pytest.mark.parametrize("data_age", [None, 1, 25])
def test_scanner_database_freshness_and_trusted_options(tmp_path, monkeypatch, data_age):
    cache = tmp_path / "cache"
    if data_age is not None:
        (cache / "db").mkdir(parents=True)
        (cache / "db/metadata.json").write_text(json.dumps({"UpdatedAt": (now() - timedelta(hours=data_age)).isoformat()}))
    config = SimpleNamespace(trivy_cache_dir=cache, trivy_binary="trivy", trivy_version="0.76.0")
    monkeypatch.setattr(security_engine, "settings", lambda: config)
    calls = []

    def command(args, *rest, **kwargs):
        calls.append(args)
        if "--version" in args:
            return 0, "Version: 0.76.0", ""
        if "--format" in args:
            return 0, json.dumps({"SchemaVersion": 2, "Results": []}), ""
        return 1, "", "unavailable"

    monkeypatch.setattr(security_engine.ocr, "command", command)
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    result = security_engine.trivy(item, 30, lambda: False)
    assert result["metadata"]["database_fresh"] == (data_age == 1)
    assert bool(result["problems"]) == (data_age != 1)
    scan = next(args for args in calls if "--format" in args)
    assert "--offline-scan" in scan and "--skip-check-update" in scan and "--skip-java-db-update" in scan
    assert "code_mortis.security_scanner" in scan and "--show-secrets" not in scan


def test_unknown_severity_prevents_completion_and_grade(seed, security_github):
    with transaction() as db:
        scan = saved_scan(db, seed)
        saved_finding(db, seed, scan, severity="unknown")
        identity = scan.id
    security.finish(identity, {}, [], freshness(), True)
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "partial" and scan.grade is None and "unclassified" in scan.error


def test_cancel_saves_report_and_explicit_retry_creates_a_new_history_entry(seed, security_github, client):
    body = {"connection_id": "connection", "request_key": uid(), "token_budget": 100000, "timeout_seconds": 900}
    created = client.post("/api/v1/security/repositories/22/scans", json=body).json()
    cancelled = client.post(f"/api/v1/security/scans/{created['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/v1/security/scans/{created['id']}/report").status_code == 200
    retry = client.post(f"/api/v1/security/scans/{created['id']}/retry", json={**body, "request_key": uid()})
    assert retry.status_code == 202 and retry.json()["parent_id"] == created["id"] and retry.json()["id"] != created["id"]


def test_busy_checks_cover_running_background_and_summary_jobs(seed):
    with transaction() as db:
        job = Job(id=uid(), kind="generate_background", payload={"connection_id": "connection"}, status="running")
        db.add(job)
        db.flush()
        assert security.connection_busy(db, "connection")
        assert not security.connection_busy(db, "connection", job_id=job.id)
        job.kind, job.payload = "generate_summary", {"config": {"connection": {"id": "connection"}}, "run_id": "run"}
        assert security.connection_busy(db, "connection") and not security.connection_busy(db, "connection", run_id="run")


def test_security_migration_preserves_existing_workspace(seed):
    from alembic import command
    from alembic.config import Config

    from code_mortis.models import Connection, PullRequest, User

    with transaction() as db:
        original = (db.get(User, 7).access_token, db.get(Connection, "connection").secret, db.get(PullRequest, "pr").title)
    command.downgrade(Config("alembic.ini"), "20261019_ignored_pulls")
    command.upgrade(Config("alembic.ini"), "head")
    with transaction() as db:
        assert (db.get(User, 7).access_token, db.get(Connection, "connection").secret, db.get(PullRequest, "pr").title) == original


@pytest.mark.parametrize("lost_response", [False, True])
def test_verified_issue_close_and_recurrence_reopen_reconcile_without_inference(seed, security_github, monkeypatch, lost_response):
    with transaction() as db:
        scan = saved_scan(db, seed, "completed")
        finding = saved_finding(db, seed, scan, state="resolved")
        identity = finding.id
        operation = uid()
        finding.issue = {"number": 9, "id": 99, "node_id": "issue-node", "operation_id": operation, "actor_id": 7,
            "closed_by_app": False, "desired_state": "closed", "intent_version": 0, "verification_scan_id": scan.id}
        body = security_issues.marker(finding, operation)
    bot = {"login": "mortis-test[bot]", "type": "Bot"}
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "open", "body": body,
        "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
        "user": bot, "closed_by": None}
    comments, mutations = [], []

    def request(method, path, token, **kwargs):
        if method == "GET":
            if "/issues/9" in path:
                return dict(remote)
            return {"sha": SHA} if "/commits/" in path else security_github
        if method == "POST":
            assert path.endswith("/comments")
            comments.append({"body": kwargs["json"]["body"], "user": bot})
            return comments[-1]
        assert method == "PATCH"
        remote.update(state=kwargs["json"]["state"], closed_by=bot if kwargs["json"]["state"] == "closed" else None)
        mutations.append(kwargs["json"]["state"])
        if lost_response and len(mutations) == 1:
            raise github.GitHubError(503, "Connection closed after mutation")
        return dict(remote)

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: list(comments))
    monkeypatch.setattr(security_engine, "ask", lambda *args: pytest.fail("Issue synchronization must never repeat inference"))
    security_issues.sync(identity)
    if lost_response:
        security_issues.sync(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.state == "resolved" and finding.issue["closed_by_app"] and finding.issue["sync_status"] == "synced", finding.issue
        finding.state, finding.version = "open", 1
        finding.issue = {**finding.issue, "desired_state": "open", "intent_version": 1}
    security_issues.sync(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.issue["remote_state"] == "open" and finding.issue["sync_status"] == "synced", finding.issue
        assert finding.issue["closed_by_app"] is False
    assert mutations == ["closed", "open"] and len(comments) == 2


def test_secret_material_and_configured_credentials_cannot_enter_issue_payload(seed, security_github, client):
    material = "synthetic-private-material"
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan, kind="secret", rule_id="fixture", match_bytes=len(material),
                                identity=security_engine.secure_digest("fixture\0" + material))
        identity = finding.id
    for secret in (material, "provider-secret"):
        response = client.post(f"/api/v1/security/findings/{identity}/github-issue", json={"title": "Security", "body": f"A credential: {secret}", "version": 0, "public": False})
        assert response.status_code == 422, response.text
    with transaction() as db:
        assert not db.get(SecurityFinding, identity).issue


def test_issue_double_submission_is_idempotent_and_stale_unsent_payload_can_be_reapproved(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        identity = finding.id
    path = f"/api/v1/security/findings/{identity}/github-issue"
    body = {"title": "Security finding", "body": "Apply an ownership guard.", "version": 0, "public": False}
    first = client.post(path, json=body)
    assert first.status_code == 202
    assert client.post(path, json=body).json()["issue"]["operation_id"] == first.json()["issue"]["operation_id"]
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        finding.version += 1
    security_issues.publish(identity)
    assert client.get(path).status_code == 200
    second = client.post(path, json={**body, "version": 1})
    assert second.status_code == 202
    assert second.json()["issue"]["operation_id"] != first.json()["issue"]["operation_id"]


def test_cancelled_scan_cannot_write_late_observations_and_retains_unknown_consumption(seed):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        scan.progress = {"tokens": 20, "tasks": {"analysis:app.py": {"status": "running"}}}
        security.cancel(db, scan)
        assert scan.progress["tokens"] is None and scan.report["tokens"] is None
        with pytest.raises(security.ocr.ReviewStopped):
            security.observe(db, scan, finding.evidence)


def test_expired_security_job_preserves_interruption_without_automatic_inference_retry(seed, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan.progress = {"tokens": 20, "tasks": {"analysis:app.py": {"status": "running"}}}
        identity = scan.id
        job = Job(id=uid(), kind="security_scan", payload={"scan_id": identity}, status="running", lease_until=now() - timedelta(minutes=1))
        db.add(job)
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda *args, **kwargs: pytest.fail("Interrupted inference must not be dispatched again"))
    jobs.dispatch()
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "partial" and scan.progress["tokens"] is None and "Retry explicitly" in scan.error


def test_issue_visibility_is_bound_to_the_reviewed_preview(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        identity = saved_finding(db, seed, scan).id
    path = f"/api/v1/security/findings/{identity}/github-issue"
    draft = client.get(path).json()
    assert draft["public"] is False
    body = {"title": draft["title"], "body": draft["body"], "version": draft["version"], "public": draft["public"]}
    security_github["private"] = False
    assert client.post(path, json=body).status_code == 409
    security_github["private"] = True
    assert client.post(path, json=body).status_code == 202
    security_github["private"] = False
    security_issues.publish(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert not finding.issue["started"] and not finding.issue.get("number")
        assert "visibility changed" in finding.issue["error"]
    assert client.get(path).json()["public"] is True
    assert client.post(path, json={**body, "public": True}).status_code == 202
