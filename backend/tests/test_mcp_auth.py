import asyncio
import base64
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from threading import Event, get_ident
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import event, select

from code_mortis import github, mcp_auth, mcp_server, mcp_tools, reviews
from code_mortis.db import engine, now, transaction
from code_mortis.mcp_models import McpAuthorization, McpClient, McpGrant, McpToken
from code_mortis.models import AppConfig, Job, Membership, Repository, ReviewRun
from code_mortis.security import digest

VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
CALLBACK = "http://127.0.0.1:8765/callback"


@pytest.fixture
def enabled(seed, github_stub, monkeypatch):
    monkeypatch.setattr(mcp_auth, "rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository))))
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"reviews_enabled": True, "security_enabled": True,
            "management_enabled": False, "allowed_client_domains": []}
        db.add(McpClient(client_id="test-cli", client_name="Test CLI", redirect_uris=[CALLBACK]))
    return seed


def authorization(client, *, domain="reviews", scopes=None, **overrides):
    values = {"client_id": "test-cli", "response_type": "code", "redirect_uri": CALLBACK,
        "resource": "http://testserver/mcp/" + domain, "scope": scopes or domain + ":read",
        "state": "client-state", "code_challenge": CHALLENGE, "code_challenge_method": "S256", **overrides}
    response = client.get("/oauth/authorize", params=values, follow_redirects=False)
    assert response.status_code == 303, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]


def consent_code(client, *, domain="reviews", scopes=None, **overrides):
    scopes = scopes or [domain + ":read"]
    request_id = authorization(client, domain=domain, scopes=" ".join(scopes), **overrides)
    preview = client.get("/api/v1/mcp/authorize", params={"request_id": request_id})
    assert preview.status_code == 200, preview.text
    response = client.post("/api/v1/mcp/authorize", json={"request_id": request_id, "repository_ids": [22],
        "scopes": scopes, "approve": True})
    assert response.status_code == 200, response.text
    redirect = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert redirect["state"] == ["client-state"]
    assert redirect["iss"] == ["http://testserver"]
    return redirect["code"][0]


def exchange(client, code, **overrides):
    return client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": "test-cli",
        "code": code, "redirect_uri": CALLBACK, "resource": "http://testserver/mcp/reviews",
        "code_verifier": VERIFIER, **overrides})


def credentials(client, **kwargs):
    response = exchange(client, consent_code(client, **kwargs), resource="http://testserver/mcp/" + kwargs.get("domain", "reviews"))
    assert response.status_code == 200, response.text
    return response.json()


def rpc(client, token=None, method="initialize", arguments=None, domain="reviews", **headers):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if method == "initialize":
        payload["params"] = {"protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "tests", "version": "1"}}
    elif arguments is not None:
        payload["params"] = arguments
    return client.post("/mcp/" + domain, json=payload, headers={"Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25", **({"Authorization": "Bearer " + token} if token else {}), **headers})


def test_default_settings_are_disabled_and_metadata_is_public(seed, client):
    response = client.get("/api/v1/mcp/settings")
    assert response.status_code == 200
    assert response.json()["reviews_enabled"] is False
    assert response.json()["security_enabled"] is False
    assert response.json()["management_enabled"] is False
    metadata = client.get("/.well-known/oauth-protected-resource/mcp/security")
    assert metadata.json()["resource"] == "http://testserver/mcp/security"
    assert "security:issues" in metadata.json()["scopes_supported"]
    assert "reviews:read" not in metadata.json()["scopes_supported"]
    assert metadata.headers["cache-control"] == "no-store"
    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["code_challenge_methods_supported"] == ["S256"]
    assert "password" not in server["grant_types_supported"]


def test_oauth_code_flow_hashes_tokens_and_authenticates_actual_transport(enabled, client):
    tokens = credentials(client)
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 900
    with transaction() as db:
        token = db.get(McpToken, digest(tokens["access_token"]))
        assert token.refresh_hash == digest(tokens["refresh_token"])
        grant = db.get(McpGrant, token.grant_id)
        assert grant.repository_ids == [22]
        assert grant.scopes == ["reviews:read"]
    response = rpc(client, tokens["access_token"])
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "Code Mortis reviews"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("operation", ["verify_token", "list_tools", "call_tool", "error"])
def test_mcp_database_work_runs_off_event_loop_with_auth_context(enabled, client, monkeypatch, operation):
    tokens = credentials(client)
    verifier = mcp_auth.McpTokenVerifier("reviews")
    token = asyncio.run(verifier.verify_token(tokens["access_token"]))
    access = ContextVar("test_mcp_access", default=None)
    monkeypatch.setattr(mcp_auth, "get_access_token", access.get)
    monkeypatch.setattr(mcp_server, "get_access_token", access.get)
    loop_thread = get_ident()
    threads = []

    @contextmanager
    def checked_transaction():
        threads.append(get_ident())
        assert get_ident() != loop_thread, "Blocking database work ran on the event loop"
        with transaction() as db:
            yield db

    monkeypatch.setattr(mcp_auth, "transaction", checked_transaction)
    monkeypatch.setattr(mcp_server, "transaction", checked_transaction)
    server = mcp_server.DomainServer("reviews")
    mcp_tools.register_tools(server, "reviews")

    async def check():
        access.set(token)
        if operation == "verify_token":
            assert (await verifier.verify_token(tokens["access_token"])).client_id == "test-cli"
        elif operation == "list_tools":
            names = {tool.name for tool in await server.list_tools()}
            assert "reviews_get_pull_request" in names
            assert "reviews_start_review" not in names
        else:
            result = await server.call_tool("reviews_get_pull_request", {
                "params": {"pr_id": "missing" if operation == "error" else "pr"}})
            assert result.is_error is (operation == "error")
            if operation == "error":
                assert result.structured_content["error"]["status"] == 404
            else:
                assert result.structured_content["pull_request"]["id"] == "pr"

    asyncio.run(check())
    assert threads


def test_slow_mcp_authorization_keeps_event_loop_responsive(enabled, client, monkeypatch):
    tokens = credentials(client)
    token = asyncio.run(mcp_auth.McpTokenVerifier("reviews").verify_token(tokens["access_token"]))
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: token)
    entered, release = Event(), Event()
    original = mcp_server.tool_access

    def slow_access(*args):
        entered.set()
        assert release.wait(5), "The event loop could not release the blocked authorization"
        return original(*args)

    monkeypatch.setattr(mcp_server, "tool_access", slow_access)
    server = mcp_server.DomainServer("reviews")
    mcp_tools.register_tools(server, "reviews")

    async def check():
        task = asyncio.create_task(server.list_tools())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert not task.done()
        finally:
            release.set()
        assert await task

    asyncio.run(check())


@pytest.mark.parametrize("endpoint", ["token", "revoke"])
def test_oauth_post_io_runs_off_event_loop(enabled, client, monkeypatch, endpoint):
    tokens = credentials(client)
    code = consent_code(client)
    loop_threads = set()
    original_body, original_parameters = Request.body, mcp_auth.oauth_parameters

    async def body(request):
        loop_threads.add(get_ident())
        return await original_body(request)

    def parameters(*args):
        assert get_ident() not in loop_threads, "Redis rate limiting ran on the event loop"
        return original_parameters(*args)

    def query(*args):
        assert get_ident() not in loop_threads, "Database query ran on the event loop"

    monkeypatch.setattr(Request, "body", body)
    monkeypatch.setattr(mcp_auth, "oauth_parameters", parameters)
    event.listen(engine(), "before_cursor_execute", query)
    try:
        response = exchange(client, code) if endpoint == "token" else client.post("/oauth/revoke", data={
            "client_id": "test-cli", "token": tokens["access_token"]})
        assert response.status_code == 200, response.text
        assert loop_threads
    finally:
        event.remove(engine(), "before_cursor_execute", query)


@pytest.mark.parametrize("domain", ["reviews", "security"])
def test_oauth_accepts_identical_repeated_resources(enabled, client, domain):
    resource = "http://testserver/mcp/" + domain
    code = consent_code(client, domain=domain, resource=[resource, resource])
    response = exchange(client, code, resource=[resource, resource])
    assert response.status_code == 200, response.text
    response = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "test-cli",
        "resource": [resource, resource], "refresh_token": response.json()["refresh_token"]})
    assert response.status_code == 200, response.text
    assert rpc(client, response.json()["access_token"], domain=domain).status_code == 200


@pytest.mark.parametrize("endpoint", ["authorize", "token", "revoke"])
@pytest.mark.parametrize("pairs", [
    [("resource", "http://testserver/mcp/reviews"), ("resource", "http://testserver/mcp/security")],
    [("resource", "http://testserver/mcp/security"), ("resource", "http://testserver/mcp/reviews")],
    [("state", "same-state"), ("state", "same-state")],
    [("redirect_uri", CALLBACK), ("redirect_uri", CALLBACK)],
    [("resource", "a" * 4097)],
])
def test_oauth_rejects_conflicting_duplicate_or_unbounded_parameters(enabled, client, endpoint, pairs):
    if endpoint == "authorize":
        response = client.get("/oauth/authorize", params=pairs, follow_redirects=False)
    else:
        response = client.post("/oauth/" + endpoint, content=urlencode(pairs),
            headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "OAuth parameters must be unique and bounded."


def test_modern_transport_lists_tools_without_initialize_or_session(enabled, client):
    tokens = credentials(client)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "tests", "version": "1"}}
    response = rpc(client, tokens["access_token"], method="tools/list", arguments={"_meta": meta},
        **{"MCP-Protocol-Version": "2026-07-28", "MCP-Method": "tools/list"})
    assert response.status_code == 200, response.text
    assert "Mcp-Session-Id" not in response.headers
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert "reviews_get_pull_request" in names
    assert "reviews_start_review" not in names
    detail = rpc(client, tokens["access_token"], method="tools/call", arguments={"_meta": meta,
        "name": "reviews_get_pull_request", "arguments": {"params": {"pr_id": "pr"}}},
        **{"MCP-Protocol-Version": "2026-07-28", "MCP-Method": "tools/call", "MCP-Name": "reviews_get_pull_request"})
    assert detail.status_code == 200, detail.text
    assert detail.json()["result"].get("isError") is not True, detail.text
    assert detail.json()["result"]["structuredContent"]["pull_request"]["id"] == "pr"


@pytest.mark.parametrize("client_id", ["https://client.example:invalid/metadata.json", "https://[invalid/metadata.json"])
def test_metadata_invalid_url_returns_public_error(client_id):
    with pytest.raises(HTTPException) as error:
        mcp_auth.fetch_client_metadata(client_id, ["client.example"])
    assert error.value.status_code == 400


@pytest.mark.parametrize("callback", ["https://client.example:invalid/callback", "https://[invalid/callback",
    "http://127.0.0.1/callback#fragment", "https://user:password@client.example/callback"])
def test_dynamic_registration_rejects_malformed_callbacks(enabled, client, callback):
    response = client.post("/oauth/register", json={"client_name": "Test", "redirect_uris": [callback]})
    assert response.status_code == 422


def test_cookie_and_foreign_tokens_do_not_authenticate_mcp(enabled, client):
    for token in (None, "session", "github-user-token", "installation-token", "provider-secret"):
        response = rpc(client, token)
        assert response.status_code == 401
        assert "oauth-protected-resource/mcp/reviews" in response.headers["www-authenticate"]


@pytest.mark.parametrize("changes", [
    {"code_challenge": ""}, {"code_challenge_method": "plain"}, {"response_type": "token"},
    {"redirect_uri": "https://attacker.example/callback"}, {"resource": "https://elsewhere.example/mcp"},
    {"scope": "security:read"},
])
def test_authorization_rejects_invalid_protocol_and_resource(enabled, client, changes):
    values = {"client_id": "test-cli", "response_type": "code", "redirect_uri": CALLBACK,
        "resource": "http://testserver/mcp/reviews", "scope": "reviews:read", "state": "state",
        "code_challenge": CHALLENGE, "code_challenge_method": "S256", **changes}
    response = client.get("/oauth/authorize", params=values, follow_redirects=False)
    assert response.status_code == 400
    assert "location" not in response.headers


@pytest.mark.parametrize("overrides", [
    {"code_verifier": "b" * 64}, {"code_verifier": ""}, {"resource": "http://testserver/mcp/security"},
    {"redirect_uri": CALLBACK + "/other"}, {"client_id": "another-client"},
])
def test_code_exchange_rejects_wrong_bindings(enabled, client, overrides):
    code = consent_code(client)
    assert exchange(client, code, **overrides).status_code == 400
    with transaction() as db:
        assert not db.scalar(select(McpToken))


def test_code_replay_revokes_issued_grant(enabled, client):
    code = consent_code(client)
    response = exchange(client, code)
    assert response.status_code == 200, response.text
    assert exchange(client, code).status_code == 400
    assert rpc(client, response.json()["access_token"]).status_code == 401


def test_refresh_rotates_and_reuse_revokes_family(enabled, client):
    tokens = credentials(client)
    data = {"grant_type": "refresh_token", "client_id": "test-cli",
        "resource": "http://testserver/mcp/reviews", "refresh_token": tokens["refresh_token"]}
    refreshed = client.post("/oauth/token", data=data)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/oauth/token", data=data).status_code == 400
    assert rpc(client, refreshed.json()["access_token"]).status_code == 401


def test_refresh_scope_narrowing_does_not_restore_write_access(enabled, client):
    tokens = credentials(client, scopes=["reviews:read", "reviews:write"])
    narrowed = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "test-cli",
        "resource": "http://testserver/mcp/reviews", "refresh_token": tokens["refresh_token"], "scope": "reviews:read"})
    assert narrowed.status_code == 200, narrowed.text
    with transaction() as db:
        assert db.get(McpToken, digest(narrowed.json()["access_token"])).scopes == ["reviews:read"]


def test_grant_revoke_and_domain_disable_are_immediate_and_independent(enabled, client):
    tokens = credentials(client)
    grant_id = client.get("/api/v1/mcp/grants").json()[0]["id"]
    assert client.delete("/api/v1/mcp/grants/" + grant_id).status_code == 200
    assert rpc(client, tokens["access_token"]).status_code == 401
    tokens = credentials(client)
    response = client.put("/api/v1/mcp/settings", json={"reviews_enabled": False,
        "security_enabled": True, "management_enabled": False, "allowed_client_domains": []})
    assert response.status_code == 200, response.text
    assert response.json()["security_enabled"] is True
    assert rpc(client, tokens["access_token"]).status_code == 401
    client.put("/api/v1/mcp/settings", json={"reviews_enabled": True,
        "security_enabled": True, "management_enabled": False, "allowed_client_domains": []})
    assert rpc(client, tokens["access_token"]).status_code == 401


def test_review_token_cannot_access_security(enabled, client):
    tokens = credentials(client)
    response = client.post("/mcp/security", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        headers={"Accept": "application/json, text/event-stream", "Authorization": "Bearer " + tokens["access_token"]})
    assert response.status_code == 401


def test_consent_bound_to_browser_and_session_and_cannot_expand(enabled, client):
    identity = authorization(client)
    assert client.get("/api/v1/mcp/authorize", params={"request_id": identity},
        headers={"cookie": "cm_session=session; cm_mcp_browser=" + "b" * 43}).status_code == 409
    identity = authorization(client)
    response = client.post("/api/v1/mcp/authorize", json={"request_id": identity,
        "repository_ids": [22], "scopes": ["reviews:read", "reviews:write"], "approve": True})
    assert response.status_code == 422
    response = client.post("/api/v1/mcp/authorize", json={"request_id": identity,
        "repository_ids": [99], "scopes": ["reviews:read"], "approve": True})
    assert response.status_code == 403
    response = client.post("/api/v1/mcp/authorize", json={"request_id": identity, "approve": False})
    assert response.status_code == 200
    assert parse_qs(urlsplit(response.json()["redirect_url"]).query)["error"] == ["access_denied"]
    assert client.get("/api/v1/mcp/authorize", params={"request_id": identity}).status_code == 409


def test_transport_rejects_untrusted_origin_and_revoked_membership(enabled, client):
    tokens = credentials(client)
    assert rpc(client, tokens["access_token"], Origin="https://attacker.example").status_code == 403
    with transaction() as db:
        db.delete(db.get(Membership, 7))
    assert rpc(client, tokens["access_token"]).status_code == 401


def test_expired_code_and_access_token_are_refused(enabled, client):
    code = consent_code(client)
    with transaction() as db:
        db.scalar(select(McpAuthorization).where(McpAuthorization.code_hash == digest(code))).expires_at = now() - timedelta(seconds=1)
    assert exchange(client, code).status_code == 400
    tokens = credentials(client)
    with transaction() as db:
        db.get(McpToken, digest(tokens["access_token"])).expires_at = now() - timedelta(seconds=1)
    assert rpc(client, tokens["access_token"]).status_code == 401


def test_registration_policy_and_client_removal(enabled, client):
    assert client.post("/oauth/register", json={"client_name": "Untrusted", "redirect_uris": ["https://evil.example/callback"]}).status_code == 403
    assert client.post("/oauth/register", json={"client_name": "CLI", "redirect_uris": [CALLBACK]}).status_code == 201
    tokens = credentials(client)
    assert client.delete("/api/v1/mcp/clients/test-cli").status_code == 200
    assert rpc(client, tokens["access_token"]).status_code == 401
    response = client.get("/oauth/authorize", params={"client_id": "test-cli"}, follow_redirects=False)
    assert response.status_code == 400


def test_cimd_refuses_unallowed_or_private_hosts(monkeypatch):
    def private(*args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(mcp_auth.socket, "getaddrinfo", private)
    for url, domains in [("https://evil.example/client.json", []),
        ("https://allowed.example/client.json", ["allowed.example"]),
        ("http://allowed.example/client.json", ["allowed.example"]),
        ("https://allowed.example/", ["allowed.example"])]:
        with pytest.raises(HTTPException):
            mcp_auth.fetch_client_metadata(url, domains)


@pytest.mark.parametrize("callbacks,allowed", [
    (["https://client.example/callback"], True),
    (["https://callback.example/callback"], True),
    ([CALLBACK], True),
    (["http://localhost:8765/callback"], True),
    (["http://[::1]:8765/callback"], True),
    (["https://evil.example/callback"], False),
    (["https://sub.client.example/callback"], False),
    (["https://localhost.evil.example/callback"], False),
    ([CALLBACK, "https://evil.example/callback"], False),
])
def test_cimd_callback_allowlist_on_creation_and_refresh(enabled, monkeypatch, callbacks, allowed):
    client_id = "https://client.example/metadata.json"
    metadata = {"client_id": client_id, "client_name": "Metadata client", "redirect_uris": callbacks}
    connection = Mock()
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.read.side_effect = lambda size: json.dumps(metadata).encode()
    monkeypatch.setattr(mcp_auth.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(mcp_auth.socket, "create_connection", Mock())
    monkeypatch.setattr(mcp_auth.ssl, "create_default_context", Mock())
    monkeypatch.setattr(mcp_auth.http.client, "HTTPSConnection", Mock(return_value=connection))
    with transaction() as db:
        config = db.get(AppConfig, 1)
        config.mcp_settings = {**config.mcp_settings,
            "allowed_client_domains": ["client.example", "callback.example"]}
        if not allowed:
            with pytest.raises(HTTPException) as error:
                mcp_auth.resolve_client(db, client_id)
            assert error.value.status_code == 400
            assert db.get(McpClient, client_id) is None
            return
        client = mcp_auth.resolve_client(db, client_id)
        assert client.metadata_managed is True
        assert client.redirect_uris == callbacks
        metadata["redirect_uris"] = ["https://evil.example/callback"]
        with pytest.raises(HTTPException) as error:
            mcp_auth.resolve_client(db, client_id)
        assert error.value.status_code == 400
        assert client.redirect_uris == callbacks


def test_browser_settings_keep_csrf_and_admin_checks(enabled, client):
    values = {"reviews_enabled": True, "security_enabled": True, "management_enabled": True, "allowed_client_domains": []}
    assert client.put("/api/v1/mcp/settings", json=values, headers={"x-csrf-token": "wrong"}).status_code == 403
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert client.put("/api/v1/mcp/settings", json=values).status_code == 403
    assert client.get("/api/v1/mcp/settings").json()["can_manage"] is False


def test_actual_tool_discovery_and_calls_obey_scopes(enabled, client):
    tokens = credentials(client)
    listed = rpc(client, tokens["access_token"], method="tools/list")
    assert listed.status_code == 200, listed.text
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "reviews_get_pull_request" in names
    assert "reviews_start_review" not in names
    assert not any(name.startswith(("security_", "management_")) for name in names)
    detail = rpc(client, tokens["access_token"], method="tools/call",
        arguments={"name": "reviews_get_pull_request", "arguments": {"params": {"pr_id": "pr"}}})
    assert detail.status_code == 200, detail.text
    assert detail.json()["result"].get("isError") is not True, detail.text
    assert detail.json()["result"]["structuredContent"]["pull_request"]["id"] == "pr"
    denied = rpc(client, tokens["access_token"], method="tools/call",
        arguments={"name": "reviews_start_review", "arguments": {"params": {"pr_id": "pr", "request_key": "no-permission-key"}}})
    assert denied.json()["result"]["isError"] is True
    with transaction() as db:
        assert not db.scalar(select(ReviewRun))


def test_actual_write_tool_is_durable_idempotent_and_bound_to_arguments(enabled, client, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: {
        "number": 3, "title": "Fix retry logic", "user": {"login": "owner"},
        "html_url": "https://github.com/team/project/pull/3", "state": "open",
        "head": {"sha": "a" * 40, "ref": "fix/retries", "repo": {"id": 22}},
        "base": {"sha": "b" * 40}})
    tokens = credentials(client, scopes=["reviews:read", "reviews:run"])
    arguments = {"name": "reviews_start_review", "arguments": {"params": {
        "pr_id": "pr", "connection_id": "connection", "request_key": "start-review-unique-key"}}}
    first = rpc(client, tokens["access_token"], method="tools/call", arguments=arguments)
    assert first.json()["result"].get("isError") is not True, first.text
    run_id = first.json()["result"]["structuredContent"]["id"]
    second = rpc(client, tokens["access_token"], method="tools/call", arguments=arguments)
    assert second.json()["result"]["structuredContent"]["id"] == run_id
    with transaction() as db:
        assert len(list(db.scalars(select(ReviewRun)))) == 1
        assert db.get(ReviewRun, run_id).progress["mcp"]["grant_id"]
    arguments["arguments"]["params"]["connection_id"] = "different"
    denied = rpc(client, tokens["access_token"], method="tools/call", arguments=arguments)
    assert denied.json()["result"]["structuredContent"]["error"]["status"] == 409


def test_actual_detail_tool_cannot_escape_selected_repositories(enabled, client):
    tokens = credentials(client)
    with transaction() as db:
        db.scalar(select(McpGrant)).repository_ids = []
    response = rpc(client, tokens["access_token"], method="tools/call",
        arguments={"name": "reviews_get_pull_request", "arguments": {"params": {"pr_id": "pr"}}})
    assert response.json()["result"]["structuredContent"]["error"]["status"] == 403


def test_action_approval_is_human_bound_exact_once_and_retryable(enabled, client, monkeypatch):
    tokens = credentials(client, domain="security", scopes=["security:issues"])
    with transaction() as db:
        token = db.get(McpToken, digest(tokens["access_token"]))
        grant_id = token.grant_id
    access = mcp_auth.AccessToken(token=tokens["access_token"], client_id="test-cli",
        scopes=["security:issues"], resource="http://testserver/mcp/security")
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: access)

    def action(db, user):
        job = reviews.enqueue(db, "security_issue_sync", {"finding_id": "synthetic"})
        return {"job_id": job.id}

    args = {"repository_id": 22, "public": True, "title": "Public title", "version": 1}
    pending = mcp_auth.invoke_tool("security", "security:issues", "security_create_issue", args, action,
        request_key="issue-approval-test-key", approval=True)
    assert pending["status"] == "approval_required"
    assert pending["approval_url"].startswith("http://testserver/mcp/approvals/")
    identity = pending["approval_id"]
    with transaction() as db:
        assert not db.scalar(select(Job))
    assert client.post("/api/v1/mcp/approvals/" + identity, json={"approve": True},
        headers={"x-csrf-token": "wrong"}).status_code == 403
    assert client.post("/api/v1/mcp/approvals/" + identity, json={"approve": True}).status_code == 200
    with pytest.raises(HTTPException):
        mcp_auth.invoke_tool("security", "security:issues", "security_create_issue",
            {**args, "title": "Changed", "approval_id": identity}, action,
            request_key="issue-approval-test-key", approval=True)
    result = mcp_auth.invoke_tool("security", "security:issues", "security_create_issue",
        {**args, "approval_id": identity}, action, request_key="issue-approval-test-key", approval=True)
    repeated = mcp_auth.invoke_tool("security", "security:issues", "security_create_issue",
        {**args, "approval_id": identity}, action, request_key="issue-approval-test-key", approval=True)
    assert repeated == result
    with transaction() as db:
        assert len(list(db.scalars(select(Job)))) == 1
        assert db.scalar(select(Job)).payload["mcp"]["grant_id"] == grant_id


def test_tool_errors_and_results_do_not_expose_saved_credentials(enabled, client, monkeypatch):
    from code_mortis import mcp_tools

    tokens = credentials(client)
    monkeypatch.setattr(mcp_tools.review_routes, "pull_detail", lambda *args: (_ for _ in ()).throw(
        github.GitHubError(502, "Provider error containing provider-secret")))
    response = rpc(client, tokens["access_token"], method="tools/call",
        arguments={"name": "reviews_get_pull_request", "arguments": {"params": {"pr_id": "pr"}}})
    assert "provider-secret" not in response.text
    assert response.json()["result"]["isError"] is True
    monkeypatch.setattr(mcp_tools.review_routes, "pull_detail", lambda *args: {"unsafe": "provider-secret"})
    response = rpc(client, tokens["access_token"], method="tools/call",
        arguments={"name": "reviews_get_pull_request", "arguments": {"params": {"pr_id": "pr"}}})
    assert "provider-secret" not in response.text
    assert response.json()["result"]["isError"] is True


def test_approved_disconnect_commits_revocation_and_replays_without_native_logout(enabled, client, monkeypatch):
    from code_mortis import runner_client
    from code_mortis.models import Connection

    with transaction() as db:
        config = db.get(AppConfig, 1)
        config.mcp_settings = {**config.mcp_settings, "management_enabled": True}
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "ready"
    calls = []
    monkeypatch.setattr(runner_client, "request", lambda *args, **kwargs: calls.append(args) or {})
    tokens = credentials(client, scopes=["connections:manage"])
    args = {"name": "management_disconnect_connection", "arguments": {"params": {
        "connection_id": "connection", "request_key": "approved-disconnect-key"}}}
    preview = rpc(client, tokens["access_token"], method="tools/call", arguments=args)
    pending = preview.json()["result"]["structuredContent"]
    assert pending["status"] == "approval_required", preview.text
    assert not calls
    assert client.post("/api/v1/mcp/approvals/" + pending["approval_id"], json={"approve": True}).status_code == 200
    args["arguments"]["params"]["approval_id"] = pending["approval_id"]
    first = rpc(client, tokens["access_token"], method="tools/call", arguments=args)
    assert first.json()["result"].get("isError") is not True, first.text
    replay = rpc(client, tokens["access_token"], method="tools/call", arguments=args)
    assert replay.json()["result"]["structuredContent"] == first.json()["result"]["structuredContent"]
    assert len(calls) == 1
    with transaction() as db:
        assert db.get(Connection, "connection").auth_status == "revoked"


def test_preregistered_https_client_does_not_require_metadata_fetch(enabled, monkeypatch):
    monkeypatch.setattr(mcp_auth, "fetch_client_metadata", lambda *args: pytest.fail("Registered metadata is authoritative"))
    with transaction() as db:
        client_id = "https://client.example/oauth.json"
        db.add(McpClient(client_id=client_id, client_name="Registered client", redirect_uris=[CALLBACK]))
        db.flush()
        assert mcp_auth.resolve_client(db, client_id).client_name == "Registered client"


def test_scan_request_keys_are_isolated_between_mcp_grants(enabled, client, monkeypatch):
    from code_mortis import repository_security
    from code_mortis.models import SecurityScan

    monkeypatch.setattr(repository_security, "branch_head", lambda *args: {
        "default_branch": "main", "head_sha": "a" * 40, "checked_at": now().isoformat()})
    first = credentials(client, domain="security", scopes=["security:run"])
    second = credentials(client, domain="security", scopes=["security:run"])
    args = {"name": "security_start_scan", "arguments": {"params": {"repository_id": 22,
        "connection_id": "connection", "request_key": "same-key-for-two-clients"}}}
    response = rpc(client, first["access_token"], method="tools/call", arguments=args, domain="security")
    assert response.json()["result"].get("isError") is not True, response.text
    original = response.json()["result"]["structuredContent"]["id"]
    stopped = rpc(client, first["access_token"], method="tools/call", domain="security", arguments={
        "name": "security_cancel_scan", "arguments": {"params": {"scan_id": original,
            "request_key": "stop-before-new-grant"}}})
    assert stopped.json()["result"].get("isError") is not True, stopped.text
    response = rpc(client, second["access_token"], method="tools/call", arguments=args, domain="security")
    assert response.json()["result"].get("isError") is not True, response.text
    created = response.json()["result"]["structuredContent"]["id"]
    assert created != original
    repeated = rpc(client, second["access_token"], method="tools/call", arguments=args, domain="security")
    assert repeated.json()["result"]["structuredContent"]["id"] == created
    with transaction() as db:
        rows = list(db.scalars(select(SecurityScan)))
        assert len(rows) == 2
        assert len({row.request_key for row in rows}) == 2
        assert len({row.progress["mcp"]["grant_id"] for row in rows}) == 2
