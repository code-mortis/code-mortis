"""Tool behavior with seeded data; transport and authorization are tested separately."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from code_mortis.mcp import tools as mcp_tools
from code_mortis.mcp.tools import (
    CreateIssueInput,
    FindingScopedInput,
    FindingStatusInput,
    ReportInput,
    RunScopedInput,
    StartReviewInput,
    TriageInput,
    UpdateRepositoryInput,
)

CALLS = []
TEST_USER_ID = 7


def fake_invoke_tool(domain, scope, name, arguments, callback, *, request_key=None, approval=False):
    from code_mortis.core.db import transaction
    from code_mortis.models import User

    CALLS.append({"domain": domain, "scope": scope, "name": name,
                  "request_key": request_key, "approval": approval})
    with transaction() as db:
        user = db.get(User, TEST_USER_ID)
        result = callback(db, user)
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else result


@pytest.fixture(autouse=True)
def stub_invoke_tool(monkeypatch):
    monkeypatch.setattr(mcp_tools, "invoke_tool", fake_invoke_tool)


@pytest.fixture(autouse=True)
def stub_github(monkeypatch, seed):
    from sqlalchemy import select

    from code_mortis.integrations import github
    from code_mortis.models import Repository

    monkeypatch.setattr(
        github, "authorize_repo", lambda *args, **kwargs: {"permissions": {"pull": True, "push": True}})
    monkeypatch.setattr(
        github, "visible_repositories",
        lambda db, user: list(db.scalars(select(Repository).where(Repository.active.is_(True)))))
    monkeypatch.setattr(github, "installation_token", lambda *args, **kwargs: "installation-token")
    monkeypatch.setattr(github, "user_token", lambda *args, **kwargs: "user-token")
    CALLS.clear()


def seed_security_finding(seed):
    from code_mortis.core.db import transaction
    from code_mortis.models import SecurityFinding, SecurityScan
    from code_mortis.security_scans.engine import secure_digest

    material = "synthetic-secret-material"
    with transaction() as db:
        scan = SecurityScan(repository_id=seed["repo"].id, actor_id=7, connection_id="connection",
                            request_key="scan-key", branch="main", head_sha="a" * 40, config={})
        db.add(scan)
        db.flush()
        finding = SecurityFinding(
            repository_id=seed["repo"].id, fingerprint="fp-1", kind="secret", severity="high",
            title="Exposed key", path="app.py", first_scan_id=scan.id, last_scan_id=scan.id,
            evidence={"kind": "secret", "source_sha": "a" * 40, "rule_id": "fixture",
                      "match_offset": 0, "match_bytes": len(material),
                      "identity": secure_digest("fixture\0" + material)},
            version=0)
        db.add(finding)
        db.flush()
        return {"scan_id": scan.id, "finding_id": finding.id, "material": material}


def test_catalog_covers_both_domains():

    reviews = asyncio.run(_names("reviews"))
    security = asyncio.run(_names("security"))
    assert {"reviews_start_review", "reviews_cancel_run", "reviews_set_finding_status", "reviews_get_run_section"} <= set(reviews)
    assert {"security_start_scan", "security_cancel_scan", "security_create_issue", "security_triage_finding"} <= set(security)
    management_names = {entry[1] for entry in mcp_tools.MANAGEMENT_TOOLS}
    assert set(reviews) & set(security) == management_names
    for name in reviews:
        assert name.startswith(("reviews_", "management_")), name
    for name in reviews + security:
        assert "proxy" not in name and "openapi" not in name


async def _names(domain):
    from mcp.server import MCPServer

    server = MCPServer(name=f"catalog-{domain}")
    mcp_tools.register_tools(server, domain)
    return [tool.name for tool in await server.list_tools()]


def test_catalog_meta_scopes_match_prefixes():
    from mcp.server import MCPServer

    server = MCPServer(name="catalog-meta")
    mcp_tools.register_tools(server, "reviews")
    mcp_tools.register_tools(server, "security")
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert len(by_name) == len(tools)
    for tool in tools:
        scope = (tool.meta or {}).get("scope")
        assert scope, tool.name
        assert scope.split(":")[0] in {"reviews", "security", "instance", "connections"}, tool.name
        if tool.name.startswith("reviews_"):
            assert scope.startswith("reviews:"), tool.name
        if tool.name.startswith("management_"):
            assert scope in {"instance:manage", "connections:manage"}, tool.name
    read_tools = [name for _, name, _, _, read_only in mcp_tools.REVIEW_TOOLS if read_only]
    read_tools += [name for _, name, _, _, read_only in mcp_tools.SECURITY_TOOLS if read_only]
    read_tools += [name for _, name, _, _, read_only in mcp_tools.MANAGEMENT_TOOLS if read_only]
    for name in read_tools:
        assert by_name[name].annotations and by_name[name].annotations.read_only_hint is True


def test_reject_unknown_domain():
    from mcp.server import MCPServer

    with pytest.raises(ValueError):
        mcp_tools.register_tools(MCPServer(name="catalog-bogus"), "bogus")


def test_list_pull_requests_returns_seeded_pr(seed):
    from code_mortis.schemas.common import InboxFilters

    result = mcp_tools.reviews_list_pull_requests(InboxFilters())
    assert result["total"] >= 1
    assert any(item["id"] == "pr" for page in result["groups"] for item in page["items"])


def test_update_connection_preserves_credentials_and_target(seed):
    from code_mortis.core.db import transaction
    from code_mortis.core.security import unseal
    from code_mortis.models import Connection

    with transaction() as db:
        secret = json.loads(unseal(db.get(Connection, "connection").secret))
    result = mcp_tools.management_update_connection(mcp_tools.UpdateConnectionInput(
        connection_id="connection", name="Renamed connection", provider="openai", protocol="openai",
        model="updated-model", base_url="https://api.openai.com/v1", request_key="update-connection-key"))
    assert result["id"] == "connection"
    assert result["name"] == "Renamed connection"
    assert result["model"] == "updated-model"
    with transaction() as db:
        assert json.loads(unseal(db.get(Connection, "connection").secret)) == secret
    assert CALLS[-1] == {"domain": "reviews", "scope": "connections:manage",
        "name": "management_update_connection", "request_key": "update-connection-key", "approval": True}


def test_run_and_finding_not_found(seed):
    with pytest.raises(HTTPException):
        mcp_tools.reviews_get_run(RunScopedInput(run_id="missing"))
    with pytest.raises(HTTPException):
        mcp_tools.reviews_get_finding(FindingScopedInput(finding_id="missing"))


def test_dismiss_requires_reason():
    with pytest.raises(ValidationError):
        FindingStatusInput(finding_id="x", status="dismissed", reason="  ")


def test_triage_updates_state_and_rejects_stale_version(seed):
    ids = seed_security_finding(seed)
    result = mcp_tools.security_triage_finding(
        TriageInput(finding_id=ids["finding_id"], state="false_positive",
                    reason="test fixture decision", version=0,
                    request_key="triage-key-12345678"))
    assert result["state"] == "false_positive"
    assert result["version"] == 1
    with pytest.raises(HTTPException):
        mcp_tools.security_triage_finding(
            TriageInput(finding_id=ids["finding_id"], state="open",
                        reason="stale write", version=0, request_key="triage-key-87654321"))
    assert CALLS[-1]["scope"] == "security:write"
    assert CALLS[-1]["request_key"] == "triage-key-87654321"


def test_triage_redacts_connection_secret(seed):
    from sqlalchemy import select

    from code_mortis.core.db import transaction
    from code_mortis.models import SecurityActivity

    ids = seed_security_finding(seed)
    mcp_tools.security_triage_finding(
        TriageInput(finding_id=ids["finding_id"], state="accepted_risk",
                    reason=f'key provider-secret and {ids["material"]} leaked here', version=0,
                    request_key="redact-key-12345678"))
    with transaction() as db:
        rows = db.scalars(select(SecurityActivity).where(
            SecurityActivity.finding_id == ids["finding_id"],
            SecurityActivity.action == "finding_triaged")).all()
        assert rows, "triage must preserve an audit trail"
        for row in rows:
            assert "provider-secret" not in str(row.detail)
            assert ids["material"] not in str(row.detail)
            assert "leaked here" in str(row.detail)


def test_create_issue_rejects_credential_material(seed, monkeypatch):
    from code_mortis.integrations import github

    monkeypatch.setattr(
        github, "authorize_repo",
        lambda *args, **kwargs: {"permissions": {"pull": True, "push": True}, "private": False})
    ids = seed_security_finding(seed)
    with pytest.raises(ValueError, match="credential"):
        mcp_tools.security_create_issue(CreateIssueInput(
            finding_id=ids["finding_id"], title="Leak", body="contains provider-secret",
            version=0, public=True, request_key="issue-key-12345678"))
    assert CALLS[-1]["approval"] is True

    result = mcp_tools.security_preview_issue(FindingScopedInput(finding_id=ids["finding_id"]))
    assert result["public"] is True
    assert result["version"] == 0


def test_admin_gates_preserved(seed):
    from code_mortis.core.db import transaction
    from code_mortis.models import Membership, User

    global TEST_USER_ID
    with transaction() as db:
        db.add(User(id=8, login="member", role="member"))
        db.add(Membership(github_id=8, login="member", role="member"))
    TEST_USER_ID = 8
    try:
        with pytest.raises(HTTPException):
            mcp_tools.reviews_update_repository_config(
                UpdateRepositoryInput(repository_id=22, request_key="admin-key-12345678"))
        with pytest.raises(HTTPException):
            mcp_tools.management_get_instance_settings()
    finally:
        TEST_USER_ID = 7
    result = mcp_tools.management_get_instance_settings()
    assert result["public_url"]
    assert result["members"]


def test_report_is_bounded(seed):
    from code_mortis.core.db import transaction
    from code_mortis.models import SecurityScan

    ids = seed_security_finding(seed)
    with transaction() as db:
        scan = db.get(SecurityScan, ids["scan_id"])
        scan.report = {"findings": [{"title": "x" * 1000, "path": f"file-{i}.py"} for i in range(2000)]}
    result = mcp_tools.security_get_report(ReportInput(scan_id=ids["scan_id"], format="json"))
    assert result["truncated"] is True
    assert len(result["content"]) <= mcp_tools.REPORT_MAX_CHARS


def test_start_review_uses_request_key(seed, monkeypatch):
    from code_mortis.integrations import github

    monkeypatch.setattr(github, "request", lambda *args, **kwargs: {
        "number": 3, "title": "Fix retry logic", "user": {"login": "owner"},
        "html_url": "https://github.com/team/project/pull/3", "state": "open",
        "head": {"sha": "a" * 40, "ref": "fix/retries", "repo": {"id": 22}},
        "base": {"sha": "b" * 40}})
    result = mcp_tools.reviews_start_review(
        StartReviewInput(pr_id="pr", connection_id="connection", request_key="review-key-12345678"))
    assert result["pr_id"] == "pr"
    assert result["status"] in {"queued", "running"}
    assert CALLS[-1]["request_key"] == "review-key-12345678"
