"""Exercise native and fallback workflows through the authenticated HTTP transport."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.exceptions import HTTPException
from test_mcp_auth import credentials, enabled, rpc  # noqa: F401

from code_mortis.core.db import now, transaction
from code_mortis.integrations import github
from code_mortis.mcp import elicitation, tasks
from code_mortis.mcp.models import McpApproval, McpGrant, McpOperation, McpTask
from code_mortis.mcp.server import install_mcp
from code_mortis.mcp.skills import Skills
from code_mortis.mcp.tasks import Tasks
from code_mortis.models import (
    AppConfig,
    CodeGraphRepository,
    Job,
    Membership,
    Repository,
    ReviewRun,
    SecurityFinding,
    SecurityScan,
)
from code_mortis.security_scans import engine as security_engine
from code_mortis.security_scans import service as scans

pytestmark = pytest.mark.usefixtures("enabled")

TASKS = {"extensions": {Tasks.identifier: {}}}
FORM = {"elicitation": {"form": {}}}
URL = {"elicitation": {"url": {}}}


def wire(client, token, method, params=None, caps=None):
    params = dict(params or {})
    headers = {"MCP-Protocol-Version": "2026-07-28", "MCP-Method": method}
    name = params.get("name") or params.get("taskId") or (params.get("uri") if method == "resources/read" else None)
    if name:
        headers["MCP-Name"] = name
    params["_meta"] = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                       "io.modelcontextprotocol/clientCapabilities": caps or {},
                       "io.modelcontextprotocol/clientInfo": {"name": "workflow-tests", "version": "1"}}
    response = rpc(client, token, method=method, arguments=params, **headers)
    assert response.status_code == 200 or (response.status_code == 400 and "error" in response.json()), response.text
    return response.json()


def call(client, token, name, args, caps=None, **round_fields):
    return wire(client, token, "tools/call", {"name": name, "arguments": {"params": args}, **round_fields}, caps)


@pytest.mark.parametrize(("scope", "name"), [
    ("reviews:read", "explain-pr-review"), ("security:read", "prioritize-security-findings"),
    ("code:read", "trace-change-impact")])
def test_guides_have_one_scoped_source(client, scope, name):
    with transaction() as db:
        config = db.get(AppConfig, 1)
        config.mcp_settings = {**config.mcp_settings, "code_enabled": True}
        db.add(CodeGraphRepository(repository_id=22, enabled=True))
    token = credentials(client, scopes=[scope])["access_token"]
    caps = {"extensions": {Skills.identifier: {}}}
    listed = wire(client, token, "skills/list", caps=caps)["result"]["skills"]
    assert [entry["frontmatter"]["name"] for entry in listed] == [name]
    uri = listed[0]["uri"]
    assert wire(client, token, "skills/get", {"uri": uri}, caps)["result"]["skill"] == listed[0]
    text = wire(client, token, "resources/read", {"uri": uri})["result"]["contents"][0]["text"]
    assert listed[0]["resources"][0]["digest"] == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    assert [r["uri"] for r in wire(client, token, "resources/list")["result"]["resources"]] == [uri]
    foreign_name = "explain-pr-review" if name != "explain-pr-review" else "trace-change-impact"
    foreign = f"skill://{foreign_name}/SKILL.md"
    assert "error" in wire(client, token, "skills/get", {"uri": foreign}, caps)
    assert "error" in wire(client, token, "resources/read", {"uri": foreign})
    fallback = rpc(client, token, "tools/call", {"name": "get_workflow_guide", "arguments": {"name": name}})
    assert fallback.json()["result"]["structuredContent"]["text"] == text
    assert wire(client, token, "tools/call", {"name": "get_workflow_guide", "arguments": {
        "name": foreign_name}})["result"]["isError"]


def test_discovery_only_advertises_served_catalogs(client):
    token = credentials(client, scopes=["reviews:read"])["access_token"]
    capabilities = wire(client, token, "server/discover")["result"]["capabilities"]
    assert "tools" in capabilities and "resources" in capabilities and "prompts" not in capabilities
    assert {Skills.identifier, Tasks.identifier} <= set(capabilities["extensions"])
    legacy = rpc(client, token).json()["result"]["capabilities"]
    assert "prompts" not in legacy and Tasks.identifier not in legacy.get("extensions", {})


def test_scan_form_then_durable_task(client, monkeypatch):
    monkeypatch.setattr(scans, "branch_head", lambda *args: {"default_branch": "main", "head_sha": "a" * 40,
                                                              "checked_at": now().isoformat()})
    token = credentials(client, scopes=["security:read", "security:run"])["access_token"]
    caps = {**FORM, **TASKS}
    args = {"repository_id": 22, "request_key": "scan-form-durable-key"}
    def start(**fields):
        return call(client, token, "security_start_scan", args, caps, **fields)

    assert call(client, token, "security_start_scan", args)["result"]["isError"]
    prompt = start()["result"]
    assert prompt["resultType"] == "input_required", prompt
    options = prompt["inputRequests"]["connection_id"]["params"]["requestedSchema"]["properties"]["connection_id"]
    assert options["oneOf"] == [{"const": "connection", "title": "Test model"}]
    state = {"requestState": prompt["requestState"]}
    assert start(**state, inputResponses={"connection_id": {"action": "cancel"}})["result"]["isError"]
    assert start(**state, inputResponses={"connection_id": {"action": "accept", "content": {
        "connection_id": "unlisted"}}})["result"]["structuredContent"]["error"]["status"] == 422
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(SecurityScan)) == 0
    answers = {"inputResponses": {"connection_id": {"action": "accept", "content": {"connection_id": "connection"}}}}
    assert "error" in call(client, token, "security_start_scan", {**args, "request_key": "different-scan-request"}, caps,
                           **state, **answers)
    task = start(**state, **answers)["result"]
    assert task["resultType"] == "task", task
    identity = {"taskId": task["taskId"]}
    assert start(**state, **answers)["result"]["taskId"] == identity["taskId"]
    with transaction() as db:
        scan = db.scalar(select(SecurityScan))
        scan.status, scan.report = "partial", {"problems": ["scanner timed out"]}
    terminal = wire(client, token, "tasks/get", identity, caps)["result"]
    assert terminal["status"] == "completed", terminal
    assert terminal["result"]["structuredContent"]["problems"] == ["scanner timed out"]
    assert terminal["result"]["structuredContent"]["status"] == "partial"
    other = credentials(client, scopes=["security:read", "security:run"])["access_token"]
    assert "error" in wire(client, other, "tasks/get", identity, caps)
    assert wire(client, token, "tasks/get", identity)["error"]["code"] == -32021
    assert wire(client, token, "tasks/cancel", identity, caps)["result"]["resultType"] == "complete"

    with transaction() as db:
        grant = db.get(McpGrant, db.get(McpOperation, identity["taskId"]).grant_id)
        grant.repository_ids_by_group = {"security": []}
    assert "error" in wire(client, token, "tasks/get", identity, caps)


@pytest.fixture
def github_pull(monkeypatch):
    monkeypatch.setattr(github, "request", lambda *args, **kw: {
        "number": 3, "title": "A review", "user": {"login": "owner"},
        "html_url": "https://github.com/team/project/pull/3", "state": "open",
        "head": {"sha": "a"*40, "ref": "fix", "repo": {"id": 22}}, "base": {"sha": "b"*40}})


@pytest.mark.parametrize("state", ['[', '42', 'null', '{"connection": true}', '"connection"', '["connection", 42]'])
def test_scan_form_rejects_invalid_offered_connections(client, monkeypatch, state):
    # The transport signs the state, so supply the invalid payload before signing.
    input_round = elicitation.input_round
    monkeypatch.setattr(elicitation, "input_round", lambda key, original, **kw: input_round(key, state, **kw))
    token = credentials(client, scopes=["security:run"])["access_token"]
    args = {"repository_id": 22, "request_key": "invalid-scan-form-state"}
    prompt = call(client, token, "security_start_scan", args, FORM)["result"]
    result = call(client, token, "security_start_scan", args, FORM, requestState=prompt["requestState"],
                  inputResponses={"connection_id": {"action": "accept", "content": {"connection_id": "connection"}}})
    assert result["result"]["structuredContent"]["error"] == {
        "status": 422, "message": "Choose one of the offered shared API connections."}
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(SecurityScan)) == 0


@pytest.mark.parametrize("completed", [False, True])
def test_task_io_leaves_grant_and_task_unlocked(client, github_pull, monkeypatch, completed):
    token = credentials(client, scopes=["reviews:read", "reviews:run"])["access_token"]
    args = {"pr_id": "pr", "connection_id": "connection", "request_key": "task-lock-boundaries"}
    task_id = call(client, token, "reviews_start_review", args, TASKS)["result"]["taskId"]
    with transaction() as db:
        grant_id = db.get(McpOperation, task_id).grant_id
        run_id = db.get(McpTask, task_id).target_id
        if completed:
            run = db.get(ReviewRun, run_id)
            run.status, run.publication = "completed", "not_applicable"

    def check_locks(fn):
        def checked(*args, **kwargs):
            with transaction() as db:
                assert db.get(McpGrant, grant_id, with_for_update={"nowait": True})
                assert db.get(McpTask, task_id, with_for_update={"nowait": True})
            return fn(*args, **kwargs)
        return checked

    monkeypatch.setattr(tasks.auth, "rate_limit", check_locks(tasks.auth.rate_limit))
    monkeypatch.setattr(github, "authorize_repo", check_locks(github.authorize_repo))
    monkeypatch.setattr(tasks, "saved_credential_redactor", check_locks(tasks.saved_credential_redactor))
    key = {"taskId": task_id}
    assert "result" in wire(client, token, "tasks/cancel", key, TASKS)
    expected = "completed" if completed else "cancelled"
    assert wire(client, token, "tasks/get", key, TASKS)["result"]["status"] == expected
    with transaction() as db:
        assert db.get(ReviewRun, run_id).status == expected
        assert db.get(McpTask, task_id).snapshot["status"] == expected


@pytest.mark.parametrize("token", [
    None, SimpleNamespace(), SimpleNamespace(claims=None), SimpleNamespace(claims={}),
    SimpleNamespace(claims={"grant_id": ""}), SimpleNamespace(claims={"grant_id": 42}),
])
def test_task_requires_grant_before_database_lookup(monkeypatch, token):
    monkeypatch.setattr(tasks, "get_access_token", lambda: token)
    monkeypatch.setattr(tasks, "transaction", lambda: pytest.fail("Task lookup started without a grant"))
    with pytest.raises(HTTPException) as error:
        tasks.access_task("test-task", "tasks/get")
    assert error.value.status_code == 401


def test_poll_preserves_concurrent_cancellation(client, github_pull, monkeypatch):
    token = credentials(client, scopes=["reviews:read", "reviews:run"])["access_token"]
    args = {"pr_id": "pr", "connection_id": "connection", "request_key": "poll-during-cancel"}
    task_id = call(client, token, "reviews_start_review", args, TASKS)["result"]["taskId"]
    observe = tasks.observe

    def observe_then_cancel(db, name, task, user, cancel):
        state = observe(db, name, task, user, cancel)
        if not cancel:
            with transaction() as other:
                grant_id = other.get(McpOperation, task_id).grant_id
                assert other.get(McpGrant, grant_id, with_for_update={"nowait": True})
                assert other.get(McpTask, task_id, with_for_update={"nowait": True})
            assert "result" in wire(client, token, "tasks/cancel", {"taskId": task_id}, TASKS)
        return state

    monkeypatch.setattr(tasks, "observe", observe_then_cancel)
    assert wire(client, token, "tasks/get", {"taskId": task_id}, TASKS)["result"]["status"] == "cancelled"
    with transaction() as db:
        assert db.get(McpTask, task_id).snapshot == {"status": "cancelled"}


@pytest.mark.parametrize("method", ["tasks/get", "tasks/cancel"])
def test_task_rechecks_grant_after_github_access(client, github_pull, monkeypatch, method):
    token = credentials(client, scopes=["reviews:read", "reviews:run"])["access_token"]
    args = {"pr_id": "pr", "connection_id": "connection", "request_key": "grant-during-task-access"}
    task_id = call(client, token, "reviews_start_review", args, TASKS)["result"]["taskId"]
    authorize = github.authorize_repo

    def remove_repository(*args, **kwargs):
        remote = authorize(*args, **kwargs)
        with transaction() as db:
            grant_id = db.get(McpOperation, task_id).grant_id
            db.get(McpGrant, grant_id, with_for_update={"nowait": True}).repository_ids_by_group = {"reviews": []}
        return remote

    monkeypatch.setattr(github, "authorize_repo", remove_repository)
    assert wire(client, token, method, {"taskId": task_id}, TASKS)["error"]["message"] == (
        "This repository was not selected for this MCP client.")
    with transaction() as db:
        task = db.get(McpTask, task_id)
        assert task.snapshot == {"status": "working"}
        assert db.get(ReviewRun, task.target_id).status == "queued"


@pytest.mark.parametrize("outcome", ["cancel", "partial", "failed"])
def test_review_task_poll_cancel_and_plain_replay(client, github_pull, outcome):
    token = credentials(client, scopes=["reviews:read", "reviews:run"])["access_token"]
    args = {"pr_id": "pr", "connection_id": "connection", "request_key": "review-task-request"}
    task = call(client, token, "reviews_start_review", args, TASKS)["result"]
    assert task["resultType"] == "task", task
    key = {"taskId": task["taskId"]}
    assert wire(client, token, "tasks/get", key, TASKS)["result"]["status"] == "working"
    assert "result" in wire(client, token, "tasks/update", {**key, "inputResponses": {}}, TASKS)
    plain = call(client, token, "reviews_start_review", args)["result"]["structuredContent"]
    with transaction() as db:
        assert db.get(McpTask, key["taskId"]).target_id == plain["id"]
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1
    if outcome == "cancel":
        assert "result" in wire(client, token, "tasks/cancel", key, TASKS)
        assert wire(client, token, "tasks/get", key, TASKS)["result"]["status"] == "cancelled"
        return
    with transaction() as db:
        run = db.get(ReviewRun, plain["id"])
        run.status, run.publication = outcome, "not_applicable"
        run.report = {"manifest": {"coverage": {"completed": [{"path": "verified.py"}], "failed": ["other.py"]}}}
        if outcome == "failed":
            run.error = "provider-secret"
    terminal = wire(client, token, "tasks/get", key, TASKS)["result"]
    assert terminal["status"] == "completed"
    result = terminal["result"]
    assert result["structuredContent"]["status"] == outcome
    assert result["isError"] is (outcome == "failed")
    if outcome == "partial":
        assert result["structuredContent"]["coverage"] == {"completed": 1, "failed": 1}
        assert result["structuredContent"]["publication"] == "not_applicable"
    else:
        assert "provider-secret" not in str(result)


@pytest.mark.parametrize(("name", "target", "model"), [
    ("reviews_start_review", {"pr_id": "pr"}, ReviewRun),
    ("security_start_scan", {"repository_id": 22}, SecurityScan),
    ("reviews_generate_background", {"repository_id": 22}, Job),
])
def test_task_rejects_reused_repository_name(client, github_pull, monkeypatch, name, target, model):
    monkeypatch.setattr(scans, "branch_head", lambda *args: {"default_branch": "main", "head_sha": "a" * 40,
                                                              "checked_at": now().isoformat()})
    group = name.split("_", 1)[0]
    token = credentials(client, scopes=[group + ":read", group + ":run"])["access_token"]
    args = {**target, "connection_id": "connection", "request_key": "repository-identity-test"}
    task_id = call(client, token, name, args, TASKS)["result"]["taskId"]
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kw: {"id": 23, "permissions": {"pull": True, "push": True}})

    for method, extra in [("tasks/get", {}), ("tasks/update", {"inputResponses": {}}), ("tasks/cancel", {})]:
        error = wire(client, token, method, {"taskId": task_id, **extra}, TASKS)["error"]
        assert error["message"] == "GitHub repository identity changed."
    replay = call(client, token, name, args, TASKS)["result"]
    assert replay["isError"] and replay["structuredContent"]["error"]["status"] == 403
    with transaction() as db:
        task = db.get(McpTask, task_id)
        assert db.get(model, task.target_id).status == "queued"


@pytest.mark.parametrize(("status", "publication"), [("completed", "pending"), ("partial", "publishing")])
def test_review_task_rejects_cancel_during_publication(client, github_pull, status, publication):
    token = credentials(client, scopes=["reviews:read", "reviews:run"])["access_token"]
    args = {"pr_id": "pr", "connection_id": "connection", "request_key": "review-publication-test"}
    task_id = call(client, token, "reviews_start_review", args, TASKS)["result"]["taskId"]
    key = {"taskId": task_id}
    with transaction() as db:
        run_id = db.get(McpTask, task_id).target_id
        run = db.get(ReviewRun, run_id)
        run.status, run.publication = status, publication

    assert "publication can no longer be cancelled" in wire(client, token, "tasks/cancel", key, TASKS)["error"]["message"]
    assert wire(client, token, "tasks/get", key, TASKS)["result"]["status"] == "working"
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert (run.status, run.publication, run.cancel_requested) == (status, publication, False)
        run.publication = "published"
    result = wire(client, token, "tasks/get", key, TASKS)["result"]
    assert result["status"] == "completed" and result["result"]["structuredContent"]["publication"] == "published"


@pytest.mark.parametrize(("cached", "review_repositories"), [
    (False, [22, 23]), (True, [22, 23]), (True, [22]),
], ids=["first-poll", "cached-result", "other-group-only"])
def test_task_result_uses_current_grant_scoped_secret_evidence(client, cached, review_repositories):
    with transaction() as db:
        db.add(Repository(id=23, installation_id=10, full_name="team/other"))
    token = credentials(client, scopes=["reviews:read", "reviews:run", "security:read"],
                        repositories={"reviews": review_repositories, "security": [23]})["access_token"]
    args = {"repository_id": 22, "connection_id": "connection", "request_key": "task-credential-test"}
    task_id = call(client, token, "reviews_generate_background", args, TASKS)["result"]["taskId"]
    key = {"taskId": task_id}
    credential = 'synthetic "multiline"\ncredential'
    with transaction() as db:
        job_id = db.get(McpTask, task_id).target_id
        job = db.get(Job, job_id)
        job.status, job.payload = "done", {**job.payload, "background": credential}
    if cached:
        result = wire(client, token, "tasks/get", key, TASKS)["result"]["result"]
        assert result["structuredContent"]["background"] == credential
    with transaction() as db:
        scan = SecurityScan(repository_id=23, actor_id=7, connection_id="connection", branch="main",
                            head_sha="a" * 40, request_key="other-repo-scan", config={})
        db.add(scan)
        db.flush()
        db.add(SecurityFinding(repository_id=23, first_scan_id=scan.id, last_scan_id=scan.id, fingerprint="example",
            kind="secret", severity="high", title="Credential", path="config", evidence={
                "rule_id": "fixture", "match_bytes": len(credential.encode()),
                "identity": security_engine.secure_digest("fixture\0" + credential)}))

    result = wire(client, token, "tasks/get", key, TASKS)["result"]
    assert result["status"] == "completed"
    if 23 not in review_repositories:
        assert not result["result"]["isError"]
        assert result["result"]["structuredContent"]["background"] == credential
        return
    assert result["result"]["isError"]
    summary = {"id": job_id, "status": "done", "message": "Inspect the saved result in Code Mortis."}
    assert result["result"]["structuredContent"] == json.loads(result["result"]["content"][0]["text"]) == summary
    with transaction() as db:
        assert db.get(McpTask, task_id).snapshot["result"] == result["result"]
        assert db.get(Job, job_id).payload["background"] == credential


def test_cancel_active_scan_keeps_the_cancellation_report(client, monkeypatch):
    monkeypatch.setattr(scans, "branch_head", lambda *args: {"default_branch": "main", "head_sha": "a" * 40,
                                                              "checked_at": now().isoformat()})
    token = credentials(client, scopes=["security:read", "security:run"])["access_token"]
    args = {"repository_id": 22, "request_key": "cancel-scan-request", "connection_id": "connection"}
    task_id = call(client, token, "security_start_scan", args, TASKS)["result"]["taskId"]
    key = {"taskId": task_id}
    assert "result" in wire(client, token, "tasks/cancel", key, TASKS)
    assert wire(client, token, "tasks/get", key, TASKS)["result"]["status"] == "cancelled"
    with transaction() as db:
        row = db.get(SecurityScan, db.get(McpTask, task_id).target_id)
        assert row.status == "cancelled" and row.report["problems"] == ["Security scan cancelled."]


def test_background_task_requires_admin_on_reconnect(client):
    token = credentials(client, scopes=["reviews:read", "reviews:run"])["access_token"]
    args = {"repository_id": 22, "connection_id": "connection", "request_key": "background-task-request"}
    task = call(client, token, "reviews_generate_background", args, TASKS)["result"]
    assert task["resultType"] == "task", task
    key = {"taskId": task["taskId"]}
    with transaction() as db:
        job = db.get(Job, db.get(McpTask, key["taskId"]).target_id)
        job.status, job.payload = "done", {**job.payload, "background": "A generated draft"}
    restarted = FastAPI()
    install_mcp(restarted)
    with TestClient(restarted) as reconnected:
        result = wire(reconnected, token, "tasks/get", key, TASKS)["result"]["result"]["structuredContent"]
    assert result["background"] == "A generated draft"
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert "error" in wire(client, token, "tasks/get", key, TASKS)
    assert call(client, token, "reviews_generate_background", args, TASKS)["result"]["isError"]


def test_background_cancellation_targets_the_saved_job(client):
    token = credentials(client, scopes=["reviews:read", "reviews:run"])["access_token"]
    args = {"repository_id": 22, "connection_id": "connection", "request_key": "cancel-draft-request"}
    plain = call(client, token, "reviews_generate_background", args)["result"]["structuredContent"]
    task_id = call(client, token, "reviews_generate_background", args, TASKS)["result"]["taskId"]
    key = {"taskId": task_id}
    assert "result" in wire(client, token, "tasks/cancel", key, TASKS)
    assert wire(client, token, "tasks/get", key, TASKS)["result"]["status"] == "cancelled"
    with transaction() as db:
        assert db.get(Job, plain["id"]).status == "cancelled"
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "generate_background")) == 1


@pytest.fixture
def finding():
    with transaction() as db:
        scan = SecurityScan(repository_id=22, actor_id=7, connection_id="connection", branch="main",
                            head_sha="a"*40, request_key="seed-scan", config={})
        db.add(scan)
        db.flush()
        row = SecurityFinding(repository_id=22, first_scan_id=scan.id, last_scan_id=scan.id,
            fingerprint="example", kind="misconfiguration", severity="low", title="Example", path="config", evidence={})
        db.add(row)
        db.flush()
        return row.id


def test_triage_form_version_and_decline(finding, client):
    token = credentials(client, scopes=["security:write"])["access_token"]
    args = {"finding_id": finding, "version": 0, "state": "accepted_risk", "request_key": "triage-form-request"}
    prompt = call(client, token, "security_triage_finding", args, FORM)["result"]
    state = {"requestState": prompt["requestState"]}
    def resume(action):
        return call(client, token, "security_triage_finding", args, FORM, **state,
                    inputResponses={"reason": {"action": action, "content": {"reason": "Mitigation verified"}}})
    assert resume("decline")["result"]["isError"]
    assert resume("accept")["result"]["structuredContent"]["state"] == "accepted_risk"
    assert resume("accept")["result"]["structuredContent"]["version"] == 1
    assert call(client, token, "security_triage_finding", {**args, "request_key": "second-triage-request"},
                FORM)["result"]["structuredContent"]["error"]["status"] == 409


def test_issue_url_requires_exact_browser_approval(finding, client, monkeypatch):
    from code_mortis.api.routers import security
    calls = []
    def create(identity, body, db, user):
        calls.append(identity)
        return {"id": identity}
    monkeypatch.setattr(security, "create_issue", create)
    token = credentials(client, scopes=["security:issues"])["access_token"]
    args = {"finding_id": finding, "version": 0, "title": "Issue", "body": "Redacted", "request_key": "issue-url-request"}
    prompt = call(client, token, "security_create_issue", args, URL)["result"]
    request = prompt["inputRequests"]["approve_issue"]["params"]
    assert request["mode"] == "url"
    fields = {"requestState": prompt["requestState"], "inputResponses": {"approve_issue": {"action": "accept"}}}
    pending = call(client, token, "security_create_issue", args, URL, **fields)
    assert pending["result"]["structuredContent"]["status"] == "approval_required" and not calls
    other = credentials(client, scopes=["security:issues"])["access_token"]
    assert "error" in call(client, other, "security_create_issue", args, URL, **fields)
    approval_id = request["url"].rsplit("/", 1)[1]
    with transaction() as db:
        assert db.get(McpApproval, approval_id).approved_at is None
    response = client.post("/api/v1/mcp/approvals/" + approval_id, json={"approve": True})
    assert response.status_code == 200
    assert call(client, token, "security_create_issue", args, URL, **fields)["result"]["structuredContent"]["id"] == finding
    assert call(client, token, "security_create_issue", args, URL, **fields)["result"]["structuredContent"]["id"] == finding
    assert calls == [finding]
