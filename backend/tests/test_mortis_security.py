"""Offline Mortis security routing; scanner evidence remains independent of PR exclusions."""

import json
from copy import deepcopy

import pytest

from code_mortis.core.security import digest
from code_mortis.security_scans import engine

SHA = "a" * 40
OLD = "b" * 40


def selected_result(task, *, required=None):
    evidence = [{"source_id": "source_1", "start_line": 1, "end_line": 1}]
    if task == "analysis":
        return {"findings": [{"title": "Missing ownership check", "category": "authorization",
            "severity": "high", "path": "app.py", "explanation": "A different owner can read the resource.",
            "trigger": "A request supplies another owner's identifier.", "impact": "Private data is returned.",
            "remediation": "Check resource ownership.", "verification": "Test access as another owner.",
            "evidence": evidence}], "required_paths": required or []}
    return {"verdict": "uncertain", "explanation": "The caller needs inspection.", "evidence": evidence,
        "required_paths": required or [], **({"finding_id": None} if task == "assessment" else {
            "change_explanation": "", "original_issue_present": True})}


@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
def test_mortis_callback_preserves_security_evidence_expansion_and_redaction(tmp_path, monkeypatch, task):
    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    secret = "synthetic-雪-credential"
    item.secret_values.append(secret)
    # uv.lock is mandatory-excluded from PR evidence but remains valid scanner evidence.
    item.inventory = {path: {"hash": digest("safe")} for path in ["app.py", "uv.lock"]}
    reads, calls = [], []
    item.read = lambda path, revision=None: reads.append((path, revision)) or "resolved package"
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "unsafe " + secret}},
               "diff": "changed", "history": [], "original_sha": OLD}
    original = deepcopy(context)

    def legacy(*args, **kwargs):
        pytest.fail("Mortis called the legacy OCR transport")

    def safe_infer(supplied, **kwargs):
        calls.append((deepcopy(supplied), kwargs["round"], kwargs["budget"]))
        assert secret not in json.dumps(supplied, ensure_ascii=False)
        assert "uv.lock" in supplied["available_paths"]
        raw = selected_result(task, required=["uv.lock"] if len(calls) == 1 else [])
        if task == "analysis":
            raw["findings"][0]["explanation"] = secret
        else:
            raw["explanation"] = secret
        return kwargs["validator"](raw, supplied), {"tokens": 7}

    monkeypatch.setattr(engine.verification, "api_verify", legacy)
    monkeypatch.setattr(engine.runner_client, "review", legacy)
    result, usage = engine.ask(item, {"id": "shared", "kind": "api", "model": "selected"}, {}, task,
        context, 100, 60, lambda: False, "task", infer=safe_infer)
    assert usage == {"tokens": 14}
    assert context == original
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert [(round, budget) for _, round, budget in calls] == [(0, 100), (1, 93)]
    assert reads == [("uv.lock", None)] + ([("uv.lock", OLD)] if task == "verification" else [])
    assert calls[1][0]["sources"]["current"]["uv.lock"] == "resolved package"
    evidence = result["findings"][0]["evidence"] if task == "analysis" else result["evidence"]
    assert evidence[0]["quote"] == "unsafe [redacted]"


def test_mortis_callback_rejects_citations_before_persistence(tmp_path):
    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}}
    checked = []

    def infer(supplied, **kwargs):
        raw = selected_result("analysis")
        raw["findings"][0]["evidence"][0]["source_id"] = "not-authorized"
        with pytest.raises(engine.verification.ResultValidationError):
            kwargs["validator"](raw, supplied)
        checked.append(True)
        raise engine.verification.VerificationOutputError("Invalid citation", {"tokens": 7})

    with pytest.raises(engine.verification.VerificationOutputError) as caught:
        engine.ask(item, {"kind": "api"}, {}, "analysis", {"assigned_path": "app.py", "sources": {
            "current": {"app.py": "unsafe"}}}, 100, 60, lambda: False, "task", infer=infer)
    assert checked and caught.value.usage == {"tokens": 7}


@pytest.fixture
def scan_workflow(seed, github_stub, tmp_path, monkeypatch):
    from contextlib import nullcontext
    from datetime import timedelta

    from code_mortis.core.analysis import engine_snapshot
    from code_mortis.core.db import now, transaction, uid
    from code_mortis.integrations import github, llm, ocr
    from code_mortis.models import Job
    from code_mortis.schemas.security import SecurityScanInput
    from code_mortis.security_scans import service

    remote = {"id": 22, "private": True, "default_branch": "main", "permissions": {"pull": True, "push": True}}
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: remote)
    monkeypatch.setattr(github, "request", lambda method, path, token, **kw: {"sha": SHA} if "/commits/" in path else remote)
    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {"code": True, "hash": digest("unsafe provider-secret"), "lines": 1},
                      "uv.lock": {"code": False, "hash": digest("resolved package"), "lines": 1}}
    item.read = lambda path, sha=None: "unsafe provider-secret" if path == "app.py" else "resolved package"
    item.renamed = lambda sha, path: path
    monkeypatch.setattr(engine, "snapshot", lambda *a: nullcontext(item))
    state = {"calls": [], "stale": False, "usage": 12, "after_response": None, "item": item}
    monkeypatch.setattr(engine, "trivy", lambda *a: {"findings": [], "targets": {},
        "problems": ["Advisory database is stale"] if state["stale"] else [], "metadata": {"database_fresh": not state["stale"]}})
    monkeypatch.setattr(ocr, "command", lambda *a, **kw: pytest.fail("Mortis executed OCR"))
    monkeypatch.setattr(service, "legacy_configuration", lambda *a, **kw: pytest.fail("Mortis configured OCR"))
    with transaction() as db:
        scan = service.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=1, token_budget=200_000))
        scan.config = {**scan.config, **engine_snapshot({"analysis_engine": "mortis"})}
        state["scan_id"] = scan.id
        job = db.query(Job).filter(Job.kind == "security_scan").one()
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=10)
        state["claim"] = f"{job.id}:1"

    async def send(connection, credentials, request, **kwargs):
        from code_mortis.schemas.inference import Response, Usage

        state["calls"].append(request)
        if "findings" in request.output_schema["properties"]:
            result = selected_result("analysis")
        else:
            result = selected_result("assessment")
            result.update(verdict="confirmed", explanation="The missing guard allows another owner's resource.")
        if state["after_response"]:
            state["after_response"]()
        return Response(content=json.dumps(result), output=result, usage=Usage(total_tokens=state["usage"]))

    monkeypatch.setattr(llm, "send", send)
    return state


@pytest.mark.parametrize("stale", [False, True])
def test_security_workflow_uses_durable_gateway_and_keeps_scanner_freshness(scan_workflow, stale):
    from sqlalchemy import select

    from code_mortis.core.db import transaction
    from code_mortis.models import (
        AnalysisAttempt,
        AnalysisBudget,
        AnalysisPlan,
        AnalysisTask,
        SecurityFinding,
        SecurityScan,
    )
    from code_mortis.security_scans import service

    scan_workflow["stale"] = stale
    service.perform(scan_workflow["scan_id"], scan_workflow["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, scan_workflow["scan_id"])
        assert scan.status == ("partial" if stale else "completed"), scan.error
        assert scan.grade == "D" and scan.progress["tokens"] == 24
        assert scan.report["schema_version"] == engine.POLICY
        assert "uv.lock" in scan.coverage["inventory"]
        finding = db.scalar(select(SecurityFinding))
        assert finding.evidence["sources"] == ["mortis"]
        assert "provider-secret" not in json.dumps(finding.evidence)
        plan = db.scalar(select(AnalysisPlan))
        assert plan.scan_id == scan.id
        assert db.get(AnalysisBudget, (plan.id, "security")).charged_tokens == 24
        tasks = list(db.scalars(select(AnalysisTask)))
        assert len(tasks) == 2 and all(task.status == "completed" for task in tasks)
        assert "provider-secret" not in json.dumps([task.result for task in tasks])
        assert all(attempt.usage_state == "known" for attempt in db.scalars(select(AnalysisAttempt)))
    assert len(scan_workflow["calls"]) == 2
    assert all("provider-secret" not in message.content for request in scan_workflow["calls"] for message in request.messages)


def test_security_unknown_gateway_usage_stops_later_stages(scan_workflow):
    from sqlalchemy import select

    from code_mortis.core.db import transaction
    from code_mortis.models import AnalysisAttempt, SecurityFinding, SecurityScan
    from code_mortis.security_scans import service

    scan_workflow["usage"] = None
    service.perform(scan_workflow["scan_id"], scan_workflow["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, scan_workflow["scan_id"])
        assert scan.status in {"failed", "partial"} and scan.progress["tokens"] is None
        assert not db.scalar(select(SecurityFinding))
        assert db.scalar(select(AnalysisAttempt)).usage_state == "unknown"
    assert len(scan_workflow["calls"]) == 1


def test_security_permission_revocation_during_gateway_response_blocks_persistence(scan_workflow):
    from sqlalchemy import select

    from code_mortis.core.db import transaction
    from code_mortis.models import AnalysisTask, SecurityFinding, SecurityScan, User
    from code_mortis.security_scans import service

    def revoke():
        with transaction() as db:
            db.get(User, 7).enabled = False

    scan_workflow["after_response"] = revoke
    service.perform(scan_workflow["scan_id"], scan_workflow["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, scan_workflow["scan_id"])
        assert scan.status != "completed" and not db.scalar(select(SecurityFinding))
        assert scan.progress["tokens"] == 12
        task = db.scalar(select(AnalysisTask))
        assert task.result is None and task.status != "completed"
    assert len(scan_workflow["calls"]) == 1


def test_security_semantic_fix_uses_original_and_current_source_before_resolution(scan_workflow, seed, monkeypatch):
    from sqlalchemy import select
    from test_repository_security import saved_finding, saved_scan

    from code_mortis.core.db import transaction
    from code_mortis.integrations import llm
    from code_mortis.models import SecurityFinding, SecurityOccurrence, SecurityScan
    from code_mortis.schemas.inference import Response, Usage
    from code_mortis.security_scans import service

    with transaction() as db:
        earlier = saved_scan(db, seed, status="completed", sha=OLD)
        finding_id = saved_finding(db, seed, earlier).id

    item = scan_workflow["item"]
    item.read = lambda path, sha=None: "unsafe" if sha == OLD else "safe"
    item.diff = lambda original: "-unsafe\n+safe"
    item.inventory["app.py"]["hash"] = digest("safe")

    async def send(connection, credentials, request, **kwargs):
        scan_workflow["calls"].append(request)
        if "findings" in request.output_schema["properties"]:
            result = {"findings": [], "required_paths": []}
        else:
            result = {"verdict": "fixed", "original_issue_present": True, "explanation": "The original guard was missing.",
                "change_explanation": "The current guard rejects a different owner.", "required_paths": [], "evidence": [
                    {"source_id": "source_1", "start_line": 1, "end_line": 1},
                    {"source_id": "source_2", "start_line": 1, "end_line": 1}]}
        return Response(content=json.dumps(result), output=result, usage=Usage(total_tokens=12))

    monkeypatch.setattr(llm, "send", send)
    service.perform(scan_workflow["scan_id"], scan_workflow["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, scan_workflow["scan_id"])
        assert scan.status == "completed", scan.error
        assert scan.grade == "A" and scan.progress["tokens"] == 24
        assert db.get(SecurityFinding, finding_id).state == "resolved"
        occurrence = db.scalar(select(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan.id))
        assert occurrence.outcome == "fixed"
        assert {citation["revision"] for citation in occurrence.snapshot["verification"]["evidence"]} == {"original", "current"}
    assert len(scan_workflow["calls"]) == 2


def test_security_stop_keeps_known_consumption_from_earlier_evidence_rounds(tmp_path):
    from code_mortis.core.errors import ReviewStopped

    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "uv.lock": {}}
    item.read = lambda *args: "resolved package"

    def infer(supplied, **kwargs):
        if kwargs["round"]:
            error = ReviewStopped("Permission revoked")
            error.usage = {"tokens": 11}
            raise error
        return kwargs["validator"](selected_result("assessment", required=["uv.lock"]), supplied), {"tokens": 7}

    with pytest.raises(ReviewStopped) as caught:
        engine.ask(item, {"kind": "api"}, {}, "assessment", {"history": [], "sources": {
            "current": {"app.py": "unsafe"}}}, 100, 60, lambda: False, "task", infer=infer)
    assert caught.value.usage == {"tokens": 18}


def test_completed_mortis_scan_is_readable_through_browser_pat_and_security_mcp(scan_workflow, client, monkeypatch):
    from sqlalchemy import select
    from test_mcp_auth import CALLBACK, credentials, rpc

    from code_mortis.core.db import now, transaction
    from code_mortis.integrations import github
    from code_mortis.mcp.models import McpClient
    from code_mortis.models import AppConfig, LoginSession, Repository
    from code_mortis.security_scans import service

    service.perform(scan_workflow["scan_id"], scan_workflow["claim"])
    calls = len(scan_workflow["calls"])
    scan_id = scan_workflow["scan_id"]
    prefix = f"/api/v1/security/scans/{scan_id}"
    detail = client.get(prefix)
    assert detail.status_code == 200, detail.text
    assert detail.json()["scan"]["status"] == "completed"
    assert detail.json()["scan"]["config"]["analysis_engine"] == "mortis"
    report = client.get(prefix + "/report")
    findings = client.get(prefix + "/findings")
    assert report.status_code == findings.status_code == 200
    assert report.json()["tokens"] == 24 and findings.json()["total"] == 1
    finding_id = findings.json()["items"][0]["id"]
    assert report.json()["findings"][0]["finding_id"] == finding_id

    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository))))
    with transaction() as db:
        db.get(LoginSession, digest("session")).authenticated_at = now()
        db.get(AppConfig, 1).mcp_settings = {"reviews_enabled": False, "security_enabled": True,
            "management_enabled": False, "allowed_client_domains": []}
        db.add(McpClient(client_id="test-cli", client_name="Test CLI", redirect_uris=[CALLBACK]))
    oauth_token = credentials(client, domain="security", scopes=["security:read"])["access_token"]
    created = client.post("/api/v1/api-tokens", json={"name": "Mortis security history", "scopes": ["security:read"],
        "repository_ids": [22], "expires_in_days": 1})
    assert created.status_code == 201, created.text
    client.cookies.clear()
    headers = {"Authorization": "Bearer " + created.json()["token"]}
    for suffix in ("", "/report", "/findings"):
        response = client.get(prefix + suffix, headers=headers)
        assert response.status_code == 200, response.text
        assert "provider-secret" not in response.text
    assert client.get(prefix + "/report", headers=headers).json() == report.json()

    for tool, params in [("security_get_report", {"scan_id": scan_id, "format": "json"}),
                         ("security_list_findings", {"scan_id": scan_id})]:
        response = rpc(client, oauth_token, domain="security", method="tools/call", arguments={"name": tool,
            "arguments": {"params": params}})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result.get("isError") is not True, result
        saved = result["structuredContent"]
        if tool == "security_get_report":
            assert json.loads(saved["content"]) == report.json()
        else:
            assert saved["total"] == 1 and saved["items"][0]["id"] == finding_id
        assert "provider-secret" not in response.text
    assert calls == 2 and len(scan_workflow["calls"]) == calls, "Saved reads must not start inference"
