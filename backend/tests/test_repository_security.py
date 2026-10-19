import json
import subprocess
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from threading import Barrier, Lock
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
    SecurityActivity,
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


def test_security_detail_counts_include_empty_lists(seed, security_github, client):
    detail = client.get("/api/v1/security/repositories/22").json()
    assert detail["finding_count"] == detail["scan_count"] == 0
    with transaction() as db:
        scan_id = saved_scan(db, seed, status="completed").id
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["finding_count"] == detail["activity_count"] == 0


def test_security_detail_counts_are_scoped_totals_independent_of_pages_and_filters(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        other = saved_scan(db, seed, status="partial")
        scan_id = scan.id
        for index in range(25):
            finding = saved_finding(db, seed, scan, state=["open", "resolved", "false_positive", "accepted_risk"][index % 4])
            db.add(SecurityOccurrence(scan_id=scan.id if index < 21 else other.id, finding_id=finding.id,
                                      outcome="present", snapshot={}))
        db.add_all([SecurityActivity(scan_id=scan.id, action="finding_observed") for _ in range(205)])
        db.add(SecurityActivity(scan_id=other.id, action="scan_created"))
        repo = Repository(id=23, installation_id=10, full_name="team/other", enabled=True)
        db.add(repo)
        db.flush()
        unrelated = saved_scan(db, {**seed, "repo": repo}, status="completed")
        saved_finding(db, {**seed, "repo": repo}, unrelated)
    detail = client.get("/api/v1/security/repositories/22").json()
    assert detail["finding_count"] == 25 and detail["scan_count"] == 2
    page = client.get("/api/v1/security/repositories/22/findings?state=open").json()
    assert page["total"] == 7
    assert client.get("/api/v1/security/repositories/22").json()["finding_count"] == 25
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["finding_count"] == 21 and detail["activity_count"] == 205
    assert len(detail["activity"]) == 200
    page = client.get(f"/api/v1/security/scans/{scan_id}/findings").json()
    assert page["total"] == 21 and len(page["items"]) == 20


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
    assert client.get(f"/api/v1/security/scans/{identity}/assessments").status_code == 403
    assert client.get(f"/api/v1/security/scans/{identity}/assessments/0").status_code == 403


def test_security_assessment_history_is_live_scoped_filtered_and_read_only(seed, security_github, client):
    proposals = [{"title": f"Candidate {number}", "category": "Access Control", "severity": "high", "path": "app.py",
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix", "verification": "Test",
        "evidence": [{"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": f"source {number}"}]}
        for number in range(25)]
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan.stages = {"analysis": {"status": "completed"}, "assessment": {"status": "running"}}
        scan.coverage = {"inventory": {"app.py": {"code": True}}}
        tasks = {"analysis:app.py": {"status": "completed", "result": {"findings": proposals}}}
        for number, verdict in enumerate(["confirmed", "rejected", "uncertain"]):
            tasks[f"assessment:{number}"] = {"status": "completed", "proposal": proposals[number], "tokens": 12,
                "result": {"verdict": verdict, "finding_id": None, "explanation": verdict,
                           "evidence": proposals[number]["evidence"], "required_paths": [], "api_key": "internal-secret"},
                "input_hash": "internal-hash", "credentials": "internal-secret"}
        tasks["assessment:3"] = {"status": "failed", "proposal": proposals[3], "tokens": None, "error": "Invalid citation"}
        tasks["assessment:4"] = {"status": "running", "proposal": proposals[4]}
        tasks["assessment:5"] = {**tasks["assessment:0"], "proposal": proposals[5]}
        scan.progress = {"tasks": tasks, "tokens": None, "execution": "untouched:1"}
        fingerprint = digest(json.dumps(["code", "app.py", "Access Control", "source 0", "Candidate 0"]))
        finding = saved_finding(db, seed, scan, kind="infrastructure", ai_analysis={"fingerprint": fingerprint})
        db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        identity, finding_id = scan.id, finding.id
        original = json.dumps([scan.progress, scan.stages, scan.coverage], sort_keys=True)
        other = saved_scan(db, seed, status="completed")
        other_id = other.id
    detail = client.get(f"/api/v1/security/scans/{identity}").json()
    assert detail["assessment"]["total"] == 25
    assert detail["assessment"]["status_counts"] == {"completed": 3, "failed": 1, "running": 2, "queued": 19}
    assert detail["finding_count"] == 1
    page = client.get(f"/api/v1/security/scans/{identity}/assessments").json()
    assert page["total"] == page["registered_total"] == 25 and len(page["items"]) == 20
    assert page["outcome_counts"] == {"confirmed": 1, "rejected": 1, "uncertain": 1, "failed": 1, "pending": 21}
    assert page["revision"] == detail["assessment"]["revision"]
    assert page["items"][0]["finding_id"] == finding_id
    assert page["items"][5]["progress_phase"] == "validating" and page["items"][5]["finding_id"] is None
    assert all(value not in json.dumps(page) for value in ("internal-secret", "internal-hash", "credentials"))
    second = client.get(f"/api/v1/security/scans/{identity}/assessments?page=2").json()
    assert [row["id"] for row in second["items"]] == [str(number) for number in range(20, 25)]
    filtered = client.get(f"/api/v1/security/scans/{identity}/assessments?outcome=rejected&severity=high&category=Access%20Control&q=candidate%201").json()
    assert filtered["total"] == 1 and filtered["registered_total"] == 25 and filtered["items"][0]["id"] == "1"
    assert filtered["outcome_counts"] == page["outcome_counts"]
    assert client.get(f"/api/v1/security/scans/{identity}/assessments/3").json()["tokens"] is None
    assert client.get(f"/api/v1/security/scans/{identity}/assessments/99").status_code == 404
    assert client.get(f"/api/v1/security/scans/{other_id}/assessments/0").status_code == 404
    for query in ("page=0", "page_size=101", "outcome=invented", "severity=bad", "q=" + "x" * 201):
        assert client.get(f"/api/v1/security/scans/{identity}/assessments?{query}").status_code == 422
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert original == json.dumps([scan.progress, scan.stages, scan.coverage], sort_keys=True)
        scan.status = "partial"
    ended = client.get(f"/api/v1/security/scans/{identity}/assessments").json()
    assert ended["revision"] != page["revision"]
    assert ended["items"][4]["status"] == "interrupted"
    assert ended["items"][5]["outcome"] == "withheld"
    assert "pending" not in ended["outcome_counts"]


def test_security_assessments_support_legacy_checkpoints_and_do_not_renumber_during_analysis(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan.stages = {"analysis": {"status": "running"}, "assessment": {"status": "pending"}}
        scan.coverage = {"inventory": {"a.py": {"code": True}, "b.py": {"code": True}}}
        scan.progress = {"tasks": {"analysis:b.py": {"status": "completed", "result": {"findings": [{"title": "B", "path": "b.py"}]}},
            "analysis:a.py": {"status": "completed", "result": {"findings": [{"title": "A", "path": "a.py"}]}}}, "tokens": 0}
        identity = scan.id
    assert client.get(f"/api/v1/security/scans/{identity}/assessments").json()["registered_total"] == 0
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        scan.stages = {"analysis": {"status": "completed"}, "assessment": {"status": "running"}}
        scan.progress = {**scan.progress, "tasks": {**scan.progress["tasks"], "assessment:0": {
            "status": "completed", "result": {"verdict": "rejected", "explanation": "Not supported", "finding_id": None}}}}
    rows = client.get(f"/api/v1/security/scans/{identity}/assessments").json()["items"]
    assert [(row["id"], row["proposal"]["title"], row["outcome"]) for row in rows] == [("0", "A", "rejected"), ("1", "B", "pending")]
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        scan.coverage, scan.progress = {}, {"tasks": {"assessment:7": {"status": "failed", "error": "Legacy interrupted result"}}}
    rows = client.get(f"/api/v1/security/scans/{identity}/assessments").json()["items"]
    assert len(rows) == 1 and rows[0]["id"] == "7" and rows[0]["proposal"] == {}


def test_personal_scans_require_owner_and_repository_grant(seed, security_github):
    with transaction() as db:
        connection = db.get(type(seed["connection"]), "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        body = SecurityScanInput(connection_id="connection", request_key=uid(), concurrency=8)
        with pytest.raises(ValueError, match="Authorize this repository"):
            security.create_scan(db, seed["repo"], seed["user"], body)
        db.add(ConnectionGrant(connection_id="connection", repository_id=22, automatic=False))
        db.flush()
        scan = security.create_scan(db, seed["repo"], seed["user"], body)
        assert scan.config["connection"]["kind"] == "chatgpt"
        assert scan.config["limits"]["concurrency"] == 1


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
        assert scan.grade == ("A" if resolved else "D")
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


def test_finished_partial_report_is_graded_without_claiming_complete_coverage(seed, security_github):
    with transaction() as db:
        scan = saved_scan(db, seed)
        saved_finding(db, seed, scan, severity="high")
        identity = scan.id
    security.finish(identity, {}, ["Source analysis could not be validated: docker-compose.yml."], freshness(), True)
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.grade == scan.report["grade"] == "D"
        assert scan.status == scan.report["status"] == "partial"
        assert scan.stages["report"]["status"] == "completed"
        assert "docker-compose.yml" in scan.error
        summary = security.summaries(db, [22])[22]
        assert summary.grade == "D" and summary.state == "incomplete"
        assert summary.grade_scan_id == summary.latest_scan_id == identity


def test_legacy_citation_failure_explains_mismatch_without_rewriting_saved_evidence(seed):
    error = "Source analysis could not be validated: docker-compose.yml."
    with transaction() as db:
        scan = saved_scan(db, seed, "partial", error=error)
        scan.report = {"problems": [error]}
        scan.progress = {"tasks": {"analysis:docker-compose.yml": {"status": "failed", "error": "Invalid citation",
            "validation_feedback": {"quote": "synthetic", "reason": "quote is not verbatim text within the cited source lines"}}}}
        public = security.public_scan(scan)
        assert public.error.startswith("AI citation mismatch in docker-compose.yml:")
        assert public.report["problems"] == [public.error]
        assert scan.error == error and scan.report["problems"] == [error]


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


@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("budget", [None, 1200])
@pytest.mark.parametrize("failure", [None, "citation", "evidence", "usage", "quota", "cancel"])
def test_security_parallel_analysis_accounts_for_every_file_and_stops_on_fatal_errors(
    seed, security_github, monkeypatch, tmp_path, concurrency, budget, failure,
):
    paths = ["a.py", "bad.py", "c.py", "d.py", "e.py", "f.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest("safe"), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, env={})
    calls, configurations, active = [], [], [0, 0]
    original_ask = security_engine.ask
    lock, barrier = Lock(), Barrier(concurrency)

    @contextmanager
    def snapshot(*args):
        yield item

    @contextmanager
    def configuration(*args, **kwargs):
        configurations.append(kwargs)
        yield

    def ask(*args, **kwargs):
        path, allowance = args[4]["assigned_path"], args[5]
        with lock:
            active[0] += 1
            active[1] = max(active)
            calls.append((path, allowance))
        barrier.wait(timeout=10)
        with lock:
            active[0] -= 1
        assert kwargs.get("configuration_managed", False) is (concurrency > 1)
        if path == "bad.py":
            if failure == "cancel":
                with transaction() as db:
                    security.cancel(db, db.get(SecurityScan, identity))
            if failure in {"citation", "usage"}:
                raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": None if failure == "usage" else 12},
                    feedback={"errors": [{"reason": "invented quote"}]})
            if failure == "quota":
                raise security_engine.runner_client.RunnerBlocked("quota")
            if failure == "evidence":
                return original_ask(*args, **kwargs)
        return {"findings": [], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args: {"findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}})
    monkeypatch.setattr(security_engine.ocr, "configuration", configuration)
    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(security_engine.verification, "api_verify", lambda *args, **kwargs: (
        {"findings": [], "required_paths": ["bad.py"]}, {"tokens": 12}))
    with transaction() as db:
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=concurrency, token_budget=budget))
        identity = scan.id
    security.perform(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        if failure in {"usage", "quota", "cancel"}:
            assert scan.status == {"usage": "failed", "quota": "blocked", "cancel": "cancelled"}[failure], scan.error
            assert scan.progress["tokens"] is None and scan.grade is None
            assert len(calls) == (3 if concurrency == 3 else 2)
        else:
            assert scan.status == ("partial" if failure else "completed"), scan.error
            assert scan.grade == "A"
            assert scan.progress["tokens"] == (84 if failure == "evidence" else 72) and len(calls) == 6
            assert scan.stages["analysis"]["completed"] == (5 if failure else 6)
            assert scan.stages["analysis"]["failed"] == (1 if failure else 0)
            assert scan.stages["assessment"]["status"] == "completed"
            assert scan.stages["verification"]["status"] == "completed"
            assert scan.stages["report"]["status"] == "completed"
            if failure:
                task = scan.progress["tasks"]["analysis:bad.py"]
                if failure == "evidence":
                    assert task["validation_feedback"]["required_paths"] == ["bad.py"]
                    assert task["tokens"] == 24
                else:
                    assert task["error"] == "Invalid citation"
                assert scan.report["problems"] == ["Source analysis could not be validated: bad.py."]
        if budget:
            assert all(allowance > 0 for _, allowance in calls)
            assert sum(allowance for _, allowance in calls[:concurrency]) <= budget
            if len(calls) > concurrency:
                assert sum(allowance for _, allowance in calls[concurrency:2 * concurrency]) <= budget - 12 * concurrency
    assert active[1] == concurrency
    assert configurations == ([{"session_per_task": True}] if concurrency > 1 else [])


@pytest.mark.parametrize("value", [0, 9, 1.5, True, "4"])
def test_security_concurrency_is_validated_at_the_api_boundary(seed, security_github, client, value):
    response = client.post("/api/v1/security/repositories/22/scans", json={
        "connection_id": "connection", "request_key": uid(), "concurrency": value})
    assert response.status_code == 422


@pytest.mark.parametrize("failure", ["citation", "history_selection"])
def test_invalid_assessment_preserves_other_confirmed_findings(seed, security_github, monkeypatch, tmp_path, failure):
    item = SimpleNamespace(inventory={"app.py": {"code": True, "hash": digest("unsafe"), "lines": 1}},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "unsafe", redact=lambda text: text,
        sha=SHA, git=tmp_path, env={})
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    finding = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
               "explanation": "Synthetic test", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
               "verification": "Test", "evidence": [citation]}
    original_ask = security_engine.ask

    def api(*args, **kwargs):
        try:
            return kwargs["validator"]({"verdict": "confirmed", "finding_id": "history_999", "explanation": "Confirmed",
                "evidence": [citation], "required_paths": []}, args[4]), {"tokens": 12}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid history selection", {"tokens": 12}, feedback=exc.feedback) from exc

    @contextmanager
    def snapshot(*args):
        yield item

    def ask(*args, **kwargs):
        if args[3] == "analysis":
            return {"findings": [{**finding, "title": "Invalid candidate"}, finding], "required_paths": []}, {"tokens": 12}
        if args[4]["proposal"]["title"] == "Invalid candidate":
            if failure == "history_selection":
                return original_ask(*args, **kwargs)
            raise security_engine.verification.VerificationOutputError("Invented source", {"tokens": 12},
                feedback={"errors": [{"reason": "invented quote"}]})
        return {"verdict": "confirmed", "finding_id": None, "explanation": "Confirmed", "evidence": [citation], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine.ocr, "configuration", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    with transaction() as db:
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid()))
        identity = scan.id
    security.perform(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "partial" and scan.grade == "B", scan.error
        assert scan.progress["tokens"] == (48 if failure == "history_selection" else 36)
        assert scan.stages["assessment"]["completed"] == scan.stages["assessment"]["failed"] == 1
        assert scan.stages["assessment"]["status"] == "partial"
        assert scan.stages["verification"]["status"] == "completed"
        feedback = scan.progress["tasks"]["assessment:0"]["validation_feedback"]
        if failure == "history_selection":
            assert feedback == {"kind": "history_selection", "field": "finding_id", "allowed_values": [None]}
        else:
            assert feedback["errors"] == [{"reason": "invented quote"}]
        assert db.scalar(select(SecurityFinding.title)) == "Synthetic defect"
        assert len(scan.report["findings"]) == 1


@pytest.mark.parametrize(("earlier_error", "assessment_result", "verification_result"), [
    (None, "rejected", "still_present"),
    ("analysis", "rejected", "still_present"),
    ("trivy", "rejected", "still_present"),
    (None, "uncertain", "still_present"),
    (None, "error", "still_present"),
    ("analysis", "rejected", "uncertain"),
    (None, "rejected", "error"),
])
def test_security_stage_status_only_reflects_its_own_results(
    seed, security_github, monkeypatch, tmp_path, earlier_error, assessment_result, verification_result,
):
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest("unsafe"), "lines": 1}
                                     for path in ["app.py", "bad.py"]},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "unsafe",
        renamed=lambda sha, path: path, diff=lambda sha: "", redact=lambda text: text, sha=SHA, git=tmp_path, env={})
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    proposal = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
        "verification": "Test", "evidence": [citation]}

    def ask(*args, **kwargs):
        task, context = args[3:5]
        if task == "analysis":
            if context["assigned_path"] == "bad.py":
                if earlier_error == "analysis":
                    raise security_engine.verification.VerificationOutputError("Invalid analysis citation", {"tokens": 12},
                        feedback={"errors": [{"reason": "invented quote"}]})
                return {"findings": [], "required_paths": []}, {"tokens": 12}
            return {"findings": [proposal], "required_paths": []}, {"tokens": 12}
        verdict = assessment_result if task == "assessment" else verification_result
        if verdict == "error":
            if task == "verification":
                raise ValueError("Original evidence unavailable")
            raise security_engine.verification.VerificationOutputError("Invalid assessment citation", {"tokens": 12},
                feedback={"errors": [{"reason": "invented quote"}]})
        return {"verdict": verdict, "finding_id": None, "explanation": "Checked", "evidence": [citation],
                "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args: {"findings": [], "targets": {},
        "problems": ["Dependency scan incomplete"] if earlier_error == "trivy" else [], "metadata": {}})
    monkeypatch.setattr(security_engine.ocr, "configuration", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        finding = saved_finding(db, seed, saved_scan(db, seed, status="completed"))
        finding_id = finding.id
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid())).id
    security.perform(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        partial = bool(earlier_error or assessment_result != "rejected" or verification_result != "still_present")
        assert scan.status == ("partial" if partial else "completed"), scan.error
        assert scan.stages["assessment"]["status"] == ("completed" if assessment_result == "rejected" else "partial")
        assert scan.stages["verification"]["status"] == ("completed" if verification_result == "still_present" else "partial")
        assert scan.stages["verification"]["completed"] == scan.stages["verification"]["total"] == 1
        assert bool(scan.error) == bool(scan.report["problems"]) == partial
        assert scan.stages["report"]["status"] == "completed" and scan.grade == "D"
        assert db.get(SecurityFinding, finding_id).state == "open"


@pytest.mark.parametrize("concurrency", [1, 4])
@pytest.mark.parametrize("budget", [None, 1200])
@pytest.mark.parametrize("failure", [None, "citation", "evidence", "usage", "quota", "cancel", "timeout"])
def test_security_assessment_uses_configured_concurrency_and_preserves_limits(
    seed, security_github, monkeypatch, tmp_path, concurrency, budget, failure,
):
    paths = ["a.py", "bad.py", "c.py", "d.py", "e.py", "f.py", "g.py", "h.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest(path), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: path,
        redact=lambda text: text, sha=SHA, git=tmp_path, env={})
    calls, configurations, active = [], [], [0, 0]
    original_ask = security_engine.ask
    lock, barrier = Lock(), Barrier(concurrency)
    clock = [0]
    if failure == "timeout":
        monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])

    @contextmanager
    def configuration(*args, **kwargs):
        configurations.append(kwargs)
        yield

    def ask(*args, **kwargs):
        task, context, allowance = args[3:6]
        if task == "analysis":
            path = context["assigned_path"]
            citation = {"revision": "current", "path": path, "start_line": 1, "end_line": 1, "quote": path}
            return {"findings": [{"title": path, "category": "test", "severity": "low", "path": path,
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                "verification": "Test", "evidence": [citation]}], "required_paths": []}, {"tokens": 12}
        assert task == "assessment"
        path = context["proposal"]["path"]
        with lock:
            active[0] += 1
            active[1] = max(active)
            calls.append((path, allowance))
        barrier.wait(timeout=10)
        with lock:
            active[0] -= 1
        assert kwargs.get("configuration_managed", False) is (concurrency > 1)
        if path == "bad.py":
            if failure == "cancel":
                with transaction() as db:
                    security.cancel(db, db.get(SecurityScan, identity))
            if failure in {"citation", "usage"}:
                raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": None if failure == "usage" else 12},
                    feedback={"errors": [{"reason": "invented quote"}]})
            if failure == "quota":
                raise security_engine.runner_client.RunnerBlocked("quota")
            if failure == "timeout":
                clock[0] = 61
            if failure == "evidence":
                return original_ask(*args, **kwargs)
        return {"verdict": "confirmed", "finding_id": None, "explanation": "Confirmed",
                "evidence": context["proposal"]["evidence"], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine.ocr, "configuration", configuration)
    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(security_engine.verification, "api_verify", lambda *args, **kwargs: (
        {"verdict": "uncertain", "finding_id": None, "explanation": "Need the guard", "evidence": [], "required_paths": ["missing_guard.py"]}, {"tokens": 12}))
    with transaction() as db:
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=concurrency, token_budget=budget,
            timeout_seconds=60 if failure == "timeout" else None)).id
    security.perform(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        if failure in {"usage", "quota", "cancel", "timeout"}:
            assert scan.status == {"usage": "partial" if concurrency == 1 else "failed", "quota": "blocked", "cancel": "cancelled",
                                   "timeout": "partial" if concurrency == 1 else "failed"}[failure], scan.error
            assert scan.grade is None
            assert scan.progress["tokens"] == (96 + 12 * len(calls) if failure == "timeout" else None)
            assert len(calls) == (4 if concurrency == 4 else 2)
        else:
            assert scan.status == ("partial" if failure else "completed"), scan.error
            assert scan.grade == "B"
            assert scan.progress["tokens"] == (204 if failure == "evidence" else 192) and len(calls) == 8
            assert scan.stages["assessment"]["completed"] == (7 if failure else 8)
            assert scan.stages["assessment"]["failed"] == (1 if failure else 0)
            assert len(scan.report["findings"]) == (7 if failure else 8)
            if failure:
                task = scan.progress["tasks"]["assessment:1"]
                if failure == "evidence":
                    assert "missing_guard.py" in task["error"] and "prepared snapshot" in task["error"]
                    assert task["validation_feedback"]["required_paths"] == ["missing_guard.py"]
                    assert task["validation_feedback"]["kind"] == "evidence"
                else:
                    assert task["error"] == "Invalid citation"
        if budget:
            assert sum(allowance for _, allowance in calls[:concurrency]) <= budget - 96
            if len(calls) > concurrency:
                assert sum(allowance for _, allowance in calls[concurrency:2 * concurrency]) <= budget - 96 - 12 * concurrency
    assert active[1] == concurrency
    assert configurations == ([{"session_per_task": True}] * 2 if concurrency > 1 else [])


def test_security_assessment_batches_share_paths_anchors_and_renamed_history():
    def proposal(path, quote):
        return {"path": path, "evidence": [{"path": path, "quote": quote}]}

    proposals = [proposal("new.py", "first"), proposal("same.py", "shared"), proposal("same.py", "other"),
                 proposal("another.py", "shared"), proposal("bridge.py", "last"), proposal("independent.py", "independent")]
    rows = [SimpleNamespace(id=number, position=number, proposal=value) for number, value in enumerate(proposals)]
    history = [{"path": "old.py", "evidence": {"source_sha": OLD, "evidence": [{"path": "bridge.py", "quote": "old"}]}},
               {"path": "bridge.py", "evidence": {"source_sha": SHA, "locations": [{"path": "same.py"}]}}]
    item = SimpleNamespace(sha=SHA, renamed=lambda sha, path: "new.py" if path == "old.py" else path)
    assert [row.id for row in security.assessment_batch(rows, history, item, 4)] == [0, 5]
    assert [row.id for row in security.assessment_batch(rows[1:], history, item, 4)] == [1, 5]


@pytest.mark.parametrize("history_changed", [False, True])
def test_parallel_security_assessment_keeps_related_findings_ordered_and_rejects_stale_history(
    seed, security_github, monkeypatch, tmp_path, history_changed,
):
    paths = ["same.py", "independent.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest(path), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: path,
        redact=lambda text: text, sha=SHA, git=tmp_path, env={}, renamed=lambda sha, path: path)
    barrier, calls = Barrier(2), []

    def ask(*args, **kwargs):
        task, context = args[3:5]
        if task == "analysis":
            path = context["assigned_path"]
            citation = {"revision": "current", "path": path, "start_line": 1, "end_line": 1, "quote": path}
            proposal = {"title": path, "category": "test", "severity": "low", "path": path,
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                "verification": "Test", "evidence": [citation]}
            return {"findings": [proposal, {**proposal, "title": "Reworded"}] if path == "same.py" else [proposal],
                    "required_paths": []}, {"tokens": 12}
        assert task == "assessment"
        proposal = context["proposal"]
        calls.append(proposal["title"])
        if proposal["title"] != "Reworded":
            barrier.wait(timeout=10)
            if history_changed and proposal["path"] == "same.py":
                with transaction() as db:
                    saved_finding(db, seed, db.get(SecurityScan, identity), path="external.py")
            finding_id = None
        else:
            matches = [row for row in context["history"] if row["path"] == "same.py"]
            assert len(matches) == 1, "A related proposal must see the preceding accepted finding."
            finding_id = matches[0]["id"]
        return {"verdict": "confirmed", "finding_id": finding_id, "explanation": "Confirmed",
                "evidence": proposal["evidence"], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine.ocr, "configuration", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=4)).id
    security.perform(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        findings = list(db.scalars(select(SecurityFinding)))
        if history_changed:
            assert scan.status == "failed" and scan.grade is None
            assert "history changed" in scan.error
            assert len(findings) == 1 and findings[0].path == "external.py"
            assert scan.progress["tokens"] == 48 and len(calls) == 2
            assert not list(db.scalars(select(SecurityOccurrence)))
        else:
            assert scan.status == "completed", scan.error
            assert len(findings) == 2 and len(list(db.scalars(select(SecurityOccurrence)))) == 2
            assert scan.progress["tokens"] == 60
            assert calls[-1] == "Reworded"
            assert scan.stages["assessment"]["completed"] == 3


@pytest.mark.parametrize("concurrency", [None, 3])
def test_legacy_security_retry_preserves_completed_file_checkpoints(seed, security_github, monkeypatch, tmp_path, concurrency):
    item = SimpleNamespace(inventory={"app.py": {"code": True, "hash": digest("safe"), "lines": 1}},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, env={})

    @contextmanager
    def snapshot(*args):
        yield item

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", lambda *args: pytest.fail("Completed legacy analysis must be reused"))
    with transaction() as db:
        parent = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid()))
        config = {**parent.config, "limits": {"token_budget": None, "timeout_seconds": None}}
        context = {"assigned_path": "app.py", "head_sha": SHA, "diff": "", "sources": {"current": {"app.py": "safe"}}, "language": "English"}
        parent.config, parent.status = config, "partial"
        parent.progress = {"tokens": 12, "tasks": {"analysis:app.py": {"status": "completed", "tokens": 12,
            "input_hash": digest(json.dumps([config, context], sort_keys=True)), "result": {"findings": [], "required_paths": []}}}}
        with pytest.raises(security.HTTPException, match="Retry must use the original"):
            security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid(), token_budget=1000), parent=parent)
        retry = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid(),
            **({"concurrency": concurrency} if concurrency else {})), parent=parent)
        identity = retry.id
        assert retry.config["limits"]["concurrency"] == (concurrency or 1)
    security.perform(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "completed", scan.error
        assert scan.progress["tokens"] == 0 and scan.progress["tasks"]["analysis:app.py"]["reused"]


@pytest.mark.parametrize("revision", ["original", "current"])
@pytest.mark.parametrize("trailing_newline", [False, True])
def test_security_citations_reanchor_only_unchanged_unique_source_text(revision, trailing_newline):
    source = "def list_installations(db, user):\n    remote = {}\n    visible = user_installations(db, user)\n    results = {}\n"
    quote = "    remote = {}\n    visible = user_installations(db, user)\n    results = {}" + ("\n" if trailing_newline else "")
    citation = {"revision": revision, "path": "app.py", "start_line": 1, "end_line": 3, "quote": quote}
    assert security_engine.validate_citations([citation], {revision: {"app.py": source}}) == {revision}
    assert citation["start_line"] == 2 and citation["end_line"] == 4 and citation["quote"] == quote


@pytest.mark.parametrize("revision", ["original", "current"])
@pytest.mark.parametrize(("claimed", "expected"), [(75, 74), (56, 55), (34, 33), (44, None)])
def test_security_recovers_repeated_exact_quotes_only_at_a_unique_nearest_line(revision, claimed, expected):
    lines = ["# filler"] * 91
    for number in (33, 55, 74):
        lines[number - 1:number + 1] = ["    volumes:", "      - .:/app"]
    source = "\n".join(lines)
    quote = "    volumes:\n      - .:/app"
    citation = {"revision": revision, "path": "docker-compose.yml", "start_line": claimed, "end_line": claimed + 1, "quote": quote}
    if expected is None:
        with pytest.raises(security_engine.verification.ResultValidationError):
            security_engine.validate_citations([citation], {revision: {"docker-compose.yml": source}})
        assert citation["start_line"] == claimed
    else:
        assert security_engine.validate_citations([citation], {revision: {"docker-compose.yml": source}}) == {revision}
        assert citation["start_line"] == expected and citation["end_line"] == expected + 1
    assert citation["quote"] == quote


def test_security_recovers_multiple_exact_matches_on_the_same_source_line():
    citation = {"revision": "current", "path": "app.py", "start_line": 2, "end_line": 2, "quote": "safe"}
    assert security_engine.validate_citations([citation], {"current": {"app.py": "safe safe\nother"}}) == {"current"}
    assert citation["start_line"] == citation["end_line"] == 1


@pytest.mark.parametrize("quote", ["invented", "safe", 'value = \\"synthetic\\"', "safe\n...\nsafe"])
def test_security_citations_never_reanchor_invented_escaped_or_ambiguous_quotes(quote):
    citation = {"revision": "current", "path": "app.py", "start_line": 2, "end_line": 2, "quote": quote}
    with pytest.raises(security_engine.verification.ResultValidationError):
        security_engine.validate_citations([citation], {"current": {"app.py": 'safe\nvalue = "synthetic"\nsafe'}})
    assert citation["start_line"] == citation["end_line"] == 2


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
@pytest.mark.parametrize("case", ["first", "second", "new", "empty", "corrected", "repeat", "uuid", "empty_invalid", "number", "expand"])
def test_security_assessment_selects_history_references_and_server_assigns_uuid(tmp_path, monkeypatch, kind, case):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    item.read = lambda *args: "safe guard"
    identities = ["228f3fbd-bb86-4143-a810-4b2622b1cc19", "1226544f-2cf5-4104-bb24-d7c151182064"]
    history = [{"id": identity, "path": "app.py", "title": f"Finding {index}"} for index, identity in enumerate(identities)]
    if case in {"empty", "empty_invalid"}:
        history = []
    original = json.dumps(history, sort_keys=True)
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    calls, contexts, schemas = [], [], []
    bundle = security_engine.verification.evidence_bundle

    def evidence_bundle(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)

    def response():
        selected = "history_2" if case in {"second", "corrected"} else "history_1"
        if case in {"new", "empty"}:
            selected = None
        elif case in {"repeat", "empty_invalid"} or (case == "corrected" and len(calls) == 1):
            selected = "history_999"
        elif case == "uuid":
            selected = identities[0]
        elif case == "number":
            selected = 1
        expand = case == "expand" and len(calls) == 1
        return {"verdict": "uncertain" if expand else "confirmed", "finding_id": selected, "explanation": "Checked source",
                "evidence": [citation], "required_paths": ["guard.py"] if expand else []}

    def api(*args, **kwargs):
        evidence_bundle(args[4])
        calls.append(args[5])
        schemas.append(kwargs["schema"])
        try:
            return kwargs["validator"](response(), args[4]), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid selection", {"tokens": 7}, feedback=exc.feedback) from exc

    def native(connection, identity, payload, stopped):
        calls.append(payload["token_budget"])
        schemas.append(payload["output_schema"])
        return response(), {"tokens": 7}

    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    monkeypatch.setattr(security_engine.runner_client, "review", native)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    context = {"history": history, "sources": {"current": {"app.py": "unsafe"}}, "diff": ""}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    invalid = case in {"repeat", "uuid", "empty_invalid", "number"}
    if invalid:
        with pytest.raises(security_engine.verification.VerificationOutputError) as caught:
            security_engine.ask(item, connection, {}, "assessment", context, 100, 60, lambda: False, uid())
        assert caught.value.usage == {"tokens": 14}
        assert caught.value.feedback["kind"] == ("schema" if case == "number" else "history_selection")
    else:
        result, usage = security_engine.ask(item, connection, {}, "assessment", context, 100, 60, lambda: False, uid())
        expected_id = None if case in {"new", "empty"} else identities[1 if case in {"second", "corrected"} else 0]
        assert result["finding_id"] == expected_id
        assert usage == {"tokens": 7 * len(calls)}
        assert result["evidence"] == [citation]
    references = [f"history_{index + 1}" for index in range(len(history))]
    assert all(schema["properties"]["finding_id"]["enum"] == [*references, None] for schema in schemas)
    assert all([finding["id"] for finding in supplied["history"]] == references for supplied in contexts)
    assert json.dumps(history, sort_keys=True) == original
    assert calls == ([100, 93] if invalid or case in {"corrected", "expand"} else [100])
    if invalid or case == "corrected":
        feedback = contexts[1]["validation_feedback"]
        if case != "number":
            assert feedback == {"kind": "history_selection", "field": "finding_id", "allowed_values": [*references, None]}


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


@pytest.mark.parametrize("kind", ["api", "chatgpt"])
@pytest.mark.parametrize("failure,reason", [
    ("missing", "not in the prepared snapshot"),
    ("excluded", "Generated directory"),
    ("oversized", "4 MB evidence limit"),
    ("supplied", "already supplied"),
    ("protected", "agent instructions and MCP configuration"),
    ("file_limit", "100-source-file evidence limit"),
    ("pass_limit", "four evidence passes"),
])
def test_unavailable_security_evidence_reports_path_reason_and_usage(tmp_path, monkeypatch, kind, failure, reason):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.read = lambda *args: "safe"
    path = "AGENTS.md" if failure == "protected" else "guard.py"
    sources = {"app.py": "safe"}
    item.inventory = {path: {}}
    if failure in {"missing", "excluded", "oversized"}:
        item.inventory = {}
    if failure == "excluded":
        item.excluded = [{"path": path, "reason": "Generated directory"}]
    if failure == "oversized":
        item.problems = [{"path": path, "reason": "File exceeds the 4 MB evidence limit"}]
    if failure == "supplied":
        sources[path] = "safe"
    if failure == "file_limit":
        sources = {f"source{number}.py": "safe" for number in range(100)}
    if failure == "pass_limit":
        item.inventory = {f"guard{number}.py": {} for number in range(1, 5)}
        path = "guard4.py"
    calls = []

    def response():
        calls.append(1)
        return {"findings": [], "required_paths": [f"guard{len(calls)}.py" if failure == "pass_limit" else path]}

    def api(*args, **kwargs):
        return kwargs["validator"](response(), args[4]), {"tokens": 7}

    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    monkeypatch.setattr(security_engine.runner_client, "review", lambda *args: (response(), {"tokens": 7}))
    with pytest.raises(security_engine.verification.VerificationOutputError) as error:
        security_engine.ask(item, {"id": "connection", "kind": kind, "model": "test"}, {}, "analysis",
            {"assigned_path": "app.py", "sources": {"current": sources}, "diff": ""}, 100, 60, lambda: False, uid())
    assert len(calls) == (4 if failure == "pass_limit" else 1 if failure in {"protected", "file_limit"} else 2)
    assert error.value.usage == {"tokens": 7 * len(calls)}
    assert path in str(error.value) and reason in str(error.value)
    assert error.value.feedback["kind"] == "evidence"
    assert error.value.feedback["required_paths"] == [path]
    assert reason in error.value.feedback["reason"]


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
@pytest.mark.parametrize("case", ["corrected", "mixed", "repeat", "uncertain", "after_schema_error"])
def test_security_missing_evidence_can_use_real_inventory_before_failing(tmp_path, monkeypatch, kind, task, case):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {path: {} for path in ["app.py", "conf/settings.py", "README.md", "AGENTS.md",
        ".codex/config.toml", ".agents/skills/test/SKILL.md", ".aws/credentials"]}
    calls, contexts, reads, clock = [], [], [], [0]
    missing = ["config/settings.py", "api-collection/README.md"]
    bundle = security_engine.verification.evidence_bundle
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def read(path, sha=None):
        assert path in {"conf/settings.py", "README.md"}
        reads.append((path, sha))
        return "safe guard"

    def evidence_bundle(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)

    def response():
        clock[0] += 10
        step = len(calls) - int(case == "after_schema_error")
        paths = missing if step == 1 or case == "repeat" else ["conf/settings.py"] if step == 2 and case != "uncertain" else []
        if case == "mixed" and step == 1:
            paths = [*paths, "README.md", "README.md"]
        raw = {"findings": [], "required_paths": paths} if task == "analysis" else {
            "verdict": "uncertain", "explanation": "Required evidence is unavailable; no issue or fix is confirmed.",
            "evidence": [], "required_paths": paths,
            **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": False})}
        if case == "after_schema_error" and len(calls) == 1:
            raw["unexpected_field"] = True
        return raw

    def api(*args, **kwargs):
        evidence_bundle(args[4])
        calls.append((args[5], args[6]))
        try:
            return kwargs["validator"](response(), args[4]), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid schema", {"tokens": 7}, feedback=exc.feedback) from exc

    def native(connection, identity, payload, stopped):
        calls.append((payload["token_budget"], payload["timeout"]))
        return response(), {"tokens": 7}

    item.read = read
    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    monkeypatch.setattr(security_engine.runner_client, "review", native)
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "safe"}}, "diff": "", "original_sha": OLD,
               "available_paths": ["invented.py"]}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    if case == "repeat":
        with pytest.raises(security_engine.verification.VerificationOutputError) as error:
            security_engine.ask(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert error.value.usage == {"tokens": 14}
        assert error.value.feedback["required_paths"] == missing
    else:
        result, usage = security_engine.ask(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert result["required_paths"] == [] and usage == {"tokens": 7 * len(calls)}
        if task != "analysis":
            assert result["verdict"] == "uncertain"
    expected_calls = 4 if case == "after_schema_error" else 2 if case in {"repeat", "uncertain"} else 3
    assert calls == [(100 - 7 * step, 60 - 10 * step) for step in range(expected_calls)]
    assert all(context["available_paths"] == ["README.md", "app.py", "conf/settings.py"] for context in contexts)
    correction = contexts[2 if case == "after_schema_error" else 1]["validation_feedback"]
    assert correction["kind"] == "evidence" and correction["required_paths"] == missing
    assert "prepared snapshot" in correction["reason"]
    expected_reads = [("README.md", None)] if case == "mixed" else []
    if case == "mixed" and task == "verification":
        expected_reads.append(("README.md", OLD))
    if case not in {"repeat", "uncertain"}:
        expected_reads.append(("conf/settings.py", None))
        if task == "verification":
            expected_reads.append(("conf/settings.py", OLD))
    assert reads == expected_reads


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("limit", ["budget", "timeout", "cancel", "unknown_usage"])
def test_security_missing_path_correction_respects_execution_limits(tmp_path, monkeypatch, kind, limit):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    calls, clock = [], [0]
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def response(*args, **kwargs):
        calls.append(1)
        if limit == "timeout":
            clock[0] = 60
        return {"verdict": "uncertain", "finding_id": None, "explanation": "Need settings", "evidence": [],
                "required_paths": ["config/settings.py"]}, {"tokens": None if limit == "unknown_usage" else 7}

    monkeypatch.setattr(security_engine.verification, "api_verify", response)
    monkeypatch.setattr(security_engine.runner_client, "review", response)
    context = {"sources": {"current": {}}, "diff": ""}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    def stopped():
        return bool(calls) and limit == "cancel"

    error_type = security_engine.ocr.ReviewStopped if limit == "cancel" else security_engine.verification.VerificationOutputError
    with pytest.raises(error_type) as error:
        security_engine.ask(item, connection, {}, "assessment", context, 7 if limit == "budget" else 100, 60, stopped, uid())
    assert calls == [1]
    if limit != "cancel":
        assert error.value.usage == {"tokens": None if limit == "unknown_usage" else 7}


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
@pytest.mark.parametrize("case", ["empty", "mixed", "repeat", "budget", "unknown_usage"])
def test_security_repeated_evidence_gets_one_correction(tmp_path, monkeypatch, kind, task, case):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    reads, calls, contexts = [], [], []
    bundle = security_engine.verification.evidence_bundle

    def read(path, sha=None):
        reads.append((path, sha))
        return "safe guard"

    def evidence_bundle(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)

    def response():
        paths = ["app.py"] if len(calls) == 1 or case == "repeat" else []
        if case == "mixed" and len(calls) == 1:
            paths += ["guard.py", "guard.py"]
        if task == "analysis":
            return {"findings": [], "required_paths": paths}
        return {"verdict": "uncertain", "explanation": "Synthetic test", "evidence": [], "required_paths": paths,
                **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": False})}

    def api(*args, **kwargs):
        evidence_bundle(args[4])
        calls.append(args[5])
        return kwargs["validator"](response(), args[4]), {"tokens": None if case == "unknown_usage" else 7}

    def native(connection, identity, payload, stopped):
        calls.append(payload["token_budget"])
        return response(), {"tokens": None if case == "unknown_usage" else 7}

    item.read = read
    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    monkeypatch.setattr(security_engine.runner_client, "review", native)
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": ""}}, "diff": "", "original_sha": OLD}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    budget = 7 if case == "budget" else 100
    if case in {"repeat", "budget", "unknown_usage"}:
        with pytest.raises(security_engine.verification.VerificationOutputError) as error:
            security_engine.ask(item, connection, {}, task, context, budget, 60, lambda: False, uid())
        assert error.value.usage == {"tokens": None if case == "unknown_usage" else 14 if case == "repeat" else 7}
        if case == "repeat":
            assert error.value.feedback["required_paths"] == ["app.py"]
            assert error.value.feedback["kind"] == "evidence"
    else:
        result, usage = security_engine.ask(item, connection, {}, task, context, budget, 60, lambda: False, uid())
        assert result["required_paths"] == [] and usage == {"tokens": 14}
    assert calls == ([budget] if case in {"budget", "unknown_usage"} else [100, 93])
    assert "validation_feedback" not in contexts[0]
    if len(calls) == 2:
        assert contexts[1]["validation_feedback"]["kind"] == "evidence"
        assert contexts[1]["validation_feedback"]["required_paths"] == ["app.py"]
        assert "empty" in contexts[1]["validation_feedback"]["reason"]
        assert contexts[1]["sources"]["current"]["app.py"] == ""
    expected_reads = [("guard.py", None)] if case == "mixed" else []
    if case == "mixed" and task == "verification":
        expected_reads.append(("guard.py", OLD))
    assert reads == expected_reads


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
@pytest.mark.parametrize("corrected", [True, False])
def test_security_schema_errors_get_one_strict_correction(tmp_path, monkeypatch, kind, task, corrected):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    calls, contexts = [], []
    bundle = security_engine.verification.evidence_bundle

    def evidence_bundle(context):
        contexts.append(context)
        return bundle(context)

    def response():
        evidence = [citation]
        if task == "analysis":
            finding = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
                       "explanation": "Synthetic test", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                       "verification": "Test", "evidence": evidence}
            raw = {"findings": [finding, dict(finding)], "required_paths": []}
            target = raw["findings"][1]
        else:
            raw = {"verdict": "uncertain", "explanation": "Synthetic test", "evidence": evidence, "required_paths": [],
                   **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": True})}
            target = raw
        if len(calls) == 1 or not corrected:
            target.update(start_line=1, end_line=1, quote="unsafe")
        return raw

    def command(args, *a, **kwargs):
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        calls.append(int(args[args.index("--max-tokens-budget") + 1]))
        return 0, json.dumps({"status": "success", "summary": {"files_reviewed": 1, "total_tokens": 7}}), ""

    def native(connection, identity, payload, stopped):
        calls.append(payload["token_budget"])
        return response(), {"tokens": 7}

    monkeypatch.setattr(security_engine.ocr, "git", lambda *a, **kwargs: "")
    monkeypatch.setattr(security_engine.ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(security_engine.ocr, "command", command)
    monkeypatch.setattr(security_engine.verification, "terminal_result", lambda *a: response())
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    monkeypatch.setattr(security_engine.runner_client, "review", native)
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "unsafe"}}, "diff": "", "history": []}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    if corrected:
        result, usage = security_engine.ask(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert usage == {"tokens": 14}
        assert (result["findings"][1] if task == "analysis" else result)["evidence"] == [citation]
    else:
        with pytest.raises(security_engine.verification.VerificationOutputError) as error:
            security_engine.ask(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert error.value.usage == {"tokens": 14}
    assert calls == [100, 93]
    assert "validation_feedback" not in contexts[0]
    feedback = contexts[1]["validation_feedback"]
    assert feedback["kind"] == "schema"
    assert [problem["loc"][-1] for problem in feedback["errors"]] == ["start_line", "end_line", "quote"]
    assert all(problem["type"] == "extra_forbidden" and "input" not in problem for problem in feedback["errors"])
    if task == "analysis":
        assert all(problem["loc"][:2] == ("findings", 1) for problem in feedback["errors"])


@pytest.mark.parametrize("case", ["corrected", "repeat", "warning", "unknown_usage", "wrong_session", "budget"])
def test_security_terminal_delivery_recovery_keeps_usage_and_session_guards(tmp_path, monkeypatch, case):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    session_id = "00000000-0000-4000-8000-000000000001"
    folder = tmp_path / ".opencodereview/sessions/repo"
    folder.mkdir(parents=True)
    calls, contexts = [], []
    bundle = security_engine.verification.evidence_bundle

    def evidence_bundle(context):
        contexts.append(context)
        return bundle(context)

    def command(args, root, *a, **kwargs):
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        calls.append(int(args[args.index("--max-tokens-budget") + 1]))
        payload = {"state": "DONE"}
        if case == "corrected" and len(calls) == 2:
            payload["result"] = {"findings": [], "required_paths": []}
        header = {"type": "session_start", "cwd": str(root), "sessionId": session_id}
        row = {"type": "llm_response", "sessionId": "another-session" if case == "wrong_session" else session_id,
               "taskType": "main_task", "tool_calls": [{"name": "task_done", "arguments": json.dumps(payload)}]}
        (folder / f"{session_id}.jsonl").write_text(json.dumps(header) + "\n" + json.dumps(row) + "\n")
        return 0, json.dumps({"session_id": session_id, "status": "success",
            "summary": {"files_reviewed": 1, "total_tokens": None if case == "unknown_usage" else 71300},
            "warnings": ["incomplete"] if case == "warning" else None}), ""

    monkeypatch.setattr(security_engine.ocr, "git", lambda *a, **kwargs: "")
    monkeypatch.setattr(security_engine.ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(security_engine.ocr, "command", command)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "safe"}}, "diff": ""}
    budget = 71300 if case == "budget" else 200000
    if case == "corrected":
        result, usage = security_engine.ask(item, {"kind": "api"}, {}, "analysis", context, budget, 60, lambda: False, uid())
        assert result == {"findings": [], "required_paths": []} and usage == {"tokens": 142600}
    else:
        with pytest.raises(ValueError) as caught:
            security_engine.ask(item, {"kind": "api"}, {}, "analysis", context, budget, 60, lambda: False, uid())
        if case != "unknown_usage":
            assert caught.value.usage["tokens"] == (142600 if case == "repeat" else 71300)
    assert calls == ([200000, 128700] if case in {"corrected", "repeat"} else [budget])
    if len(calls) == 2:
        feedback = contexts[1]["validation_feedback"]
        assert feedback["kind"] == "delivery"
        assert feedback["received"]["argument_fields"] == ["state"]
        assert feedback["received"]["result_type"] == "NoneType"
    elif case in {"warning", "wrong_session"}:
        assert caught.value.feedback is None


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
def test_security_corrects_citations_once_with_exact_feedback_and_known_usage(tmp_path, monkeypatch, kind, task):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    source = 'url = "synthetic"'
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1,
                "quote": source.replace('"', '\\"')}
    calls, identities, clock = [], [], [0]
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def response():
        evidence = [{**citation, "quote": citation["quote"] if len(calls) == 1 else source}]
        if task == "analysis":
            finding = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
                       "explanation": "Synthetic test", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                       "verification": "Test", "evidence": evidence}
            return {"findings": [finding, finding], "required_paths": []}
        return {"verdict": "uncertain", "explanation": "Synthetic test", "evidence": evidence, "required_paths": [],
                **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": True})}

    def api(*args, **kwargs):
        calls.append((args[4], args[5], args[6]))
        clock[0] += 10
        try:
            return kwargs["validator"](response(), args[4]), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            assert len(exc.feedback["errors"]) == (2 if task == "analysis" else 1)
            raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": 7}, feedback=exc.feedback) from exc

    def native(connection, identity, payload, stopped):
        calls.append((payload, payload["token_budget"], payload["timeout"]))
        identities.append(identity)
        clock[0] += 10
        return response(), {"tokens": 7}

    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    monkeypatch.setattr(security_engine.runner_client, "review", native)
    result, usage = security_engine.ask(item, {"id": "connection", "kind": kind, "model": "test"}, {}, task,
        {"assigned_path": "app.py", "sources": {"current": {"app.py": source}}, "diff": "", "history": []},
        100, 60, lambda: False, uid())
    assert usage == {"tokens": 14} and len(calls) == 2
    assert [call[1:] for call in calls] == [(100, 60), (93, 50)]
    if kind == "api":
        feedback = calls[1][0]["validation_feedback"]
        assert feedback["source_lines"] == source and feedback["quote"] == citation["quote"]
        if task == "analysis":
            assert [error["field"] for error in feedback["errors"]] == ["findings[0].evidence[0]", "findings[1].evidence[0]"]
        assert "validation_feedback" not in calls[0][0]
    else:
        assert len(set(identities)) == 2
        assert '"source_lines": ' + json.dumps(source) in calls[1][0]["prompt"]
    assert (result["findings"][0]["evidence"] if task == "analysis" else result["evidence"])[0]["quote"] == source


@pytest.mark.parametrize(("tokens", "budget", "expected_calls", "expected_usage"), [
    (5, 100, 2, 10), (None, 100, 1, None), (5, 5, 1, 5),
])
def test_security_citation_recovery_is_bounded_and_never_repeats_unknown_usage(
    tmp_path, monkeypatch, tokens, budget, expected_calls, expected_usage,
):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": tokens},
            feedback={"field": "evidence[0]", "errors": [{"reason": "invalid source line range"}]})

    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    with pytest.raises(security_engine.verification.VerificationOutputError) as error:
        security_engine.ask(item, {"kind": "api"}, {}, "analysis", {"sources": {"current": {}}}, budget, 60, lambda: False, uid())
    assert len(calls) == expected_calls and error.value.usage["tokens"] == expected_usage


def test_security_citation_feedback_keeps_missing_source_ranges_and_quote_mismatches_distinct():
    evidence = [{"revision": "current", "path": "app.py", "start_line": start, "end_line": start, "quote": quote}
                for start, quote in [(1, "safe}"), (5, "missing"), (1, " ")]]
    evidence.append({**evidence[0], "path": "missing.py"})
    with pytest.raises(security_engine.verification.ResultValidationError) as error:
        security_engine.validate_citations(evidence, {"current": {"app.py": "safe\nsafe"}})
    problems = error.value.feedback["errors"]
    assert [problem["reason"] for problem in problems] == [
        "quote is not verbatim text within the cited source lines", "invalid source line range",
        "quote is not verbatim text within the cited source lines", "unavailable source",
    ]
    assert problems[0]["source_lines"] == problems[2]["source_lines"] == "safe\n"
    assert "source_lines" not in problems[1] and "source_lines" not in problems[3]


@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
def test_security_does_not_start_correction_after_cancellation_or_timeout(tmp_path, monkeypatch, interruption):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    clock, calls = [0], []
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def api(*args, **kwargs):
        calls.append(1)
        clock[0] = 60
        raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": 7},
            feedback={"field": "evidence[0]", "errors": [{"reason": "invalid source line range"}]})

    monkeypatch.setattr(security_engine.verification, "api_verify", api)
    error_type = security_engine.ocr.ReviewStopped if interruption == "cancel" else security_engine.verification.VerificationOutputError
    with pytest.raises(error_type) as error:
        security_engine.ask(item, {"kind": "api"}, {}, "analysis", {"sources": {"current": {}}}, 100, 60,
            lambda: bool(calls) and interruption == "cancel", uid())
    assert len(calls) == 1
    if interruption == "timeout":
        assert error.value.usage == {"tokens": 7}


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


def test_finished_report_grades_migration_uses_saved_findings_and_preserves_failures(seed):
    from alembic import command
    from alembic.config import Config

    command.downgrade(Config("alembic.ini"), "20261020_security_scans")
    cases = [("partial", "high", True, None, "D"), ("completed", "medium", True, None, "C"),
             ("partial", "unknown", True, None, None), ("partial", "high", False, None, None),
             ("cancelled", "high", True, None, None), ("partial", "high", True, "B", "B"),
             ("partial", None, True, None, "A")]
    identities = []
    with transaction() as db:
        for status, severity, supported, grade, expected in cases:
            scan = saved_scan(db, seed, status, grade=grade, finished_at=now(), error="Saved failure")
            scan.stages = {"report": {"status": "completed"}}
            scan.coverage = {"inventory": {"app.py": {"code": True}}} if supported else {}
            scan.report = {"schema_version": security_engine.POLICY, "grade": grade, "problems": ["Saved failure"],
                "findings": [{"state": "open", "severity": severity}] if severity else []}
            identities.append((scan.id, status, expected))
        saved_finding(db, seed, scan, severity="critical")  # Current state must not replace historical report evidence.
    command.upgrade(Config("alembic.ini"), "head")
    with transaction() as db:
        for identity, status, expected in identities:
            scan = db.get(SecurityScan, identity)
            assert scan.grade == scan.report["grade"] == expected
            assert scan.status == status and scan.error == "Saved failure"
            assert scan.report["problems"] == ["Saved failure"]


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
