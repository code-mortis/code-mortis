import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph
from test_mcp_auth import authorization, credentials, rpc
from test_mcp_auth import enabled as enabled

from code_mortis.code_graph import service, store
from code_mortis.core.db import now, transaction
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.mcp import provenance
from code_mortis.mcp.models import McpAudit, McpGrant, McpOperation, McpToken
from code_mortis.models import (
    AppConfig,
    CodeGraphRepository,
    Installation,
    Job,
    Membership,
    Repository,
    User,
)


@pytest.fixture
def indexed(enabled, graph):
    root = graph["source"]
    (root / "a.py").write_text("from .lib import value\ndef first():\n    return value\ndef next_one():\n    return 2\n")
    (root / "lib.py").write_text("value = 42\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "imports")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")
    row, _ = run(service.enqueue_snapshot(22))
    graph["snapshot_id"] = row.id
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {**db.get(AppConfig, 1).mcp_settings, "code_enabled": True}
    return graph


def call(client, token, name, request=None):
    return rpc(client, token, method="tools/call", domain="code", arguments={
        "name": name, "arguments": {"request": request} if request is not None else {},
    })


def result(response):
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert not body.get("isError"), body
    return body["structuredContent"]


def denied(response):
    assert response.status_code in {401, 403} or response.json()["result"].get("isError"), response.text


def revision(indexed, **extra):
    return {"repository_id": 22, "commit_sha": indexed["head"], **extra}


def test_code_group_defaults_off_on_unified_endpoint(enabled, client):
    assert client.get("/api/v1/mcp/settings").json()["code_enabled"] is False
    assert client.get("/.well-known/oauth-protected-resource/mcp/code").status_code == 404
    assert client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == "http://testserver/mcp"
    denied(rpc(client, domain="code"))


def test_transport_navigation_is_read_only_bounded_and_exact(indexed, client):
    token = credentials(client, domain="code")["access_token"]
    with transaction() as db:
        jobs = db.scalar(select(func.count()).select_from(Job))
    catalog = rpc(client, token, method="tools/list", domain="code").json()["result"]["tools"]
    assert len(catalog) == 7 and all(t["annotations"]["readOnlyHint"] for t in catalog)
    assert all(t["_meta"]["scope"] == "code:read" and t["name"].startswith("code_") for t in catalog)
    repos = result(call(client, token, "code_list_repositories"))
    assert [item["repository_id"] for item in repos["items"]] == [22]
    status = result(call(client, token, "code_index_status", {"repository_id": 22}))
    assert status["commit_sha"] == indexed["head"] and status["snapshot_id"] == indexed["snapshot_id"]
    assert status["coverage"]["visible_files"] == 3  # a.py, lib.py, renamed.py
    first = result(call(client, token, "code_search", revision(indexed, limit=1)))
    second = result(call(client, token, "code_search", revision(indexed, limit=1, cursor=first["next_cursor"])))
    assert first["items"][0]["key"] != second["items"][0]["key"]
    symbol = result(call(client, token, "code_get_symbol", revision(indexed, symbol_key=first["items"][0]["key"])))
    assert "def first" in symbol["source"]["items"][0]["text"]
    relations = result(call(client, token, "code_get_relations", revision(indexed, paths=["a.py"])))
    assert relations["items"][0]["target_path"] == "lib.py"
    assert relations["items"][0]["provenance"]
    source = result(call(client, token, "code_get_source", revision(indexed, path="lib.py")))
    assert source["items"][0]["text"] == "value = 42\n" and source["content_trust"] == "untrusted"
    text = result(call(client, token, "code_search", revision(indexed, kind="text", query="return", paths=["a.py"], limit=1)))
    text2 = result(call(client, token, "code_search", revision(indexed, kind="text", query="return", paths=["a.py"], limit=1, cursor=text["next_cursor"])))
    assert text["items"][0]["start_line"] == 3 and text2["items"][0]["start_line"] == 5
    unsupported = result(call(client, token, "code_get_relations", revision(indexed, paths=["a.py"], relationship="callers")))
    assert unsupported["relationship_state"] == "unsupported" and not unsupported["items"]
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == jobs
        assert db.scalar(select(func.count()).select_from(McpOperation)) == 0
        assert db.scalar(select(func.count()).select_from(McpAudit).where(McpAudit.outcome == "succeeded")) >= 8


def test_consent_offers_only_opted_in_repos_and_scope(indexed, client):
    with transaction() as db:
        db.add(Repository(id=23, installation_id=10, full_name="team/other"))
        db.get(AppConfig, 1).mcp_settings = {**db.get(AppConfig, 1).mcp_settings, "management_enabled": True}
    identity = authorization(client, domain="code")
    preview = client.get("/api/v1/mcp/authorize", params={"request_id": identity}).json()
    assert [item["id"] for item in preview["repositories_by_group"]["code"]] == [22]
    assert [item["name"] for item in preview["scopes"]] == ["code:read"]
    response = client.get("/oauth/authorize", params={"resource": "http://testserver/mcp/code", "scope": "code:read instance:manage"})
    assert response.status_code == 400
    for scopes, repos in [(["instance:manage"], [22]), (["code:read"], [23])]:
        response = client.post("/api/v1/mcp/authorize", json={"request_id": identity, "repository_ids_by_group": {"code": repos}, "scopes": scopes, "approve": True})
        assert response.status_code in {403, 422}


@pytest.mark.parametrize("domain", ["reviews", "security"])
def test_scopes_isolate_code_from_other_groups(indexed, client, domain):
    old = credentials(client, domain=domain)["access_token"]
    denied(call(client, old, "code_index_status", revision(indexed)))
    code = credentials(client, domain="code")["access_token"]
    assert all(tool["name"].startswith("code_") for tool in rpc(client, code, method="tools/list").json()["result"]["tools"])
    denied(call(client, code, "reviews_list_repositories"))
    denied(call(client, code, "management_list_members"))
    # A bearer grant is not a browser/PAT credential, even alongside a valid browser cookie.
    response = client.get("/api/v1/repositories", headers={"Authorization": "Bearer " + code})
    assert response.status_code in {401, 403}
    with transaction() as db:
        identity = db.get(McpToken, digest(code)).grant_id
    with provenance.scoped({"grant_id": identity, "domain": "code", "scope": "code:read"}):
        with pytest.raises(service.AccessLost):
            service.enqueue_snapshot(22)
        with pytest.raises(service.AccessLost):
            service.configure(22, 7, enabled=False)


@pytest.mark.parametrize("change", ["grant", "token", "expiry", "setting", "user", "membership", "installation", "uninstall", "archived", "repository", "optin", "selection"])
def test_cached_catalog_and_repeated_calls_revalidate_access(indexed, client, change):
    token = credentials(client, domain="code")["access_token"]
    result(call(client, token, "code_get_source", revision(indexed, path="lib.py")))
    assert rpc(client, token, method="tools/list", domain="code").status_code == 200
    with transaction() as db:
        grant = db.scalar(select(McpGrant).where(McpGrant.domain == "unified"))
        if change == "grant":
            grant.revoked_at = now()
        elif change == "token":
            db.get(McpToken, digest(token)).expires_at = now() - timedelta(seconds=1)
        elif change == "expiry":
            grant.expires_at = now() - timedelta(seconds=1)
        elif change == "setting":
            db.get(AppConfig, 1).mcp_settings = {"code_enabled": False}
        elif change == "user":
            db.get(User, 7).enabled = False
        elif change == "membership":
            db.delete(db.get(Membership, 7))
        elif change == "installation":
            db.get(Installation, 10).active = False
        elif change == "uninstall":
            db.get(Installation, 10).uninstalled = True
        elif change == "archived":
            db.get(Installation, 10).archived = True
        elif change == "repository":
            db.get(Repository, 22).active = False
        elif change == "optin":
            db.get(CodeGraphRepository, 22).enabled = False
        else:
            grant.repository_ids_by_group = {"code": []}
    response = call(client, token, "code_get_source", revision(indexed, path="lib.py"))
    denied(response)
    assert "value = 42" not in response.text


def test_exclusions_missing_objects_revisions_and_repository_identity(indexed, client):
    token = credentials(client, domain="code")["access_token"]
    hidden = result(call(client, token, "code_search", revision(indexed, paths=["skip.py"], limit=1)))
    assert not hidden["items"] and not hidden["truncated"] and "skip.py" not in json.dumps(hidden)
    missing = result(call(client, token, "code_index_status", revision(indexed, commit_sha="f" * 40)))
    assert missing["status"] == "unavailable" and missing["snapshot_id"] is None
    denied(call(client, token, "code_get_source", revision(indexed, repository_id=23, path="lib.py")))
    store.git(store.directory(22), "update-ref", "-d", store.reference(indexed["snapshot_id"]))
    assert result(call(client, token, "code_get_source", revision(indexed, path="lib.py")))["source_state"] == "unavailable"
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "lib.py"}
    assert result(call(client, token, "code_index_status", revision(indexed)))["status"] == "unavailable"


def test_revoke_during_source_read_discards_result(indexed, client, monkeypatch):
    token = credentials(client, domain="code")["access_token"]
    original = store.read_blob

    def revoke(*args):
        raw = original(*args)
        with transaction() as db:
            db.scalar(select(McpGrant).where(McpGrant.domain == "unified")).revoked_at = now()
        return raw

    monkeypatch.setattr(store, "read_blob", revoke)
    response = call(client, token, "code_get_source", revision(indexed, path="lib.py"))
    denied(response)
    assert "value = 42" not in response.text


@pytest.mark.parametrize("change", ["policy", "token"])
def test_mid_read_policy_change_or_token_expiry_discards_result(indexed, client, monkeypatch, change):
    token = credentials(client, domain="code")["access_token"]
    original = store.read_blob

    def changed(*args):
        raw = original(*args)
        with transaction() as db:
            if change == "policy":
                db.get(Repository, 22).settings = {"exclusions": "lib.py"}
            else:
                db.get(McpToken, digest(token)).expires_at = now() - timedelta(seconds=1)
        return raw

    monkeypatch.setattr(store, "read_blob", changed)
    response = call(client, token, "code_get_source", revision(indexed, path="lib.py"))
    denied(response)
    assert "value = 42" not in response.text


def test_code_only_registration_disable_and_fresh_github_access(indexed, client, monkeypatch):
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"enabled": True, "code_enabled": True}
    registered = client.post("/oauth/register", json={"client_name": "Code reader", "redirect_uris": ["http://127.0.0.1:8123/callback"]})
    assert registered.status_code == 201
    token = credentials(client, domain="code")["access_token"]
    assert client.get("/mcp/code/unknown").status_code == 404

    def lost(*args, **kwargs):
        assert kwargs["fresh"] is True
        raise github.GitHubError(404, "hidden repository")

    monkeypatch.setattr(github, "authorize_repo", lost)
    response = call(client, token, "code_index_status", revision(indexed))
    denied(response)
    assert "hidden repository" not in response.text
    assert result(call(client, token, "code_list_repositories"))["items"] == []
    assert client.put("/api/v1/mcp/settings", json={"enabled": True, "code_enabled": False}).status_code == 200
    with transaction() as db:
        assert db.scalar(select(McpGrant).where(McpGrant.domain == "unified")).revoked_at


def test_code_grant_cannot_impersonate_index_owner_or_schedule_work(indexed, client):
    credentials(client, domain="code")
    with transaction() as db:
        grant_id = db.scalar(select(McpGrant.id).where(McpGrant.domain == "unified"))
    with provenance.scoped({"grant_id": grant_id, "domain": "code", "scope": "code:read"}), transaction() as db:
        with pytest.raises(service.AccessLost):
            service.local_access(db, 22, 8, initiator=False)
        with pytest.raises(service.AccessLost):
            service.schedule_refresh(db, 22)


@pytest.mark.parametrize("patch", [{"limit": 101}, {"cursor": {"offset": -1}}, {"kind": "text", "query": ""},
                                  {"kind": "text", "query": "a\nb"}, {"commit_sha": "main"}, {"paths": ["../outside"]}])
def test_tool_input_boundaries(indexed, client, patch):
    token = credentials(client, domain="code")["access_token"]
    denied(call(client, token, "code_search", revision(indexed, **patch)))


def test_source_is_checked_before_slicing_and_impact_uses_both_revisions(indexed, client):
    base = indexed["head"]
    (indexed["source"] / "lib.py").write_text("value = 'provider-secret'\n")
    fixture_git(indexed["source"], "add", ".")
    fixture_git(indexed["source"], "commit", "-qm", "changed")
    indexed["head"] = fixture_git(indexed["source"], "rev-parse", "HEAD")
    fixture_git(store.directory(22), "fetch", "-q", str(indexed["source"]), indexed["head"])
    row, _ = run(service.enqueue_snapshot(22))
    assert row.status in service.USABLE, row.error
    token = credentials(client, domain="code")["access_token"]
    response = call(client, token, "code_get_source", revision(indexed, path="lib.py", start_byte=12, end_byte=18))
    denied(response)
    assert "provider-secret" not in response.text
    impact = result(call(client, token, "code_change_impact", {"repository_id": 22, "base_sha": base, "head_sha": indexed["head"]}))
    assert impact["changed_files"] == [{"path": "lib.py", "change": "modified"}]
    assert [r["commit_sha"] for r in impact["relations"]] == [base, indexed["head"]]
    assert all(r["items"][0]["path"] == "a.py" for r in impact["relations"])


def test_source_keeps_regular_crlf_endings_and_original_byte_citations(enabled, graph, client):
    raw = b"def first():\r\n    return 1\r\n"
    (graph["source"] / "a.py").write_bytes(raw)
    fixture_git(graph["source"], "add", ".")
    fixture_git(graph["source"], "commit", "-qm", "CRLF source")
    graph["head"] = fixture_git(graph["source"], "rev-parse", "HEAD")
    row, _ = run(service.enqueue_snapshot(22))
    assert row.status in service.USABLE
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"enabled": True, "code_enabled": True}
    token = credentials(client, domain="code")["access_token"]
    source = result(call(client, token, "code_get_source", revision(graph, path="a.py")))["items"][0]
    assert source["text"].encode() == raw
    assert source["end_byte"] == len(raw) and source["end_line"] == 2


def test_shared_store_readers_exclude_writer_and_never_create_store(indexed, tmp_path, monkeypatch):
    with store.locked(shared=True), store.locked(shared=True), pytest.raises(store.RunnerBlocked):
        with store.locked():
            pass
    from code_mortis.core.config import settings

    missing = tmp_path / "absent"
    monkeypatch.setattr(settings(), "code_graph_store_dir", missing)
    with pytest.raises(FileNotFoundError), store.locked(shared=True):
        pass
    assert not missing.exists()
