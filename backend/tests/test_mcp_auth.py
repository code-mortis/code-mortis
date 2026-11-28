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

import httpx2
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import event, select

from code_mortis.core.access import effective_repository_ids
from code_mortis.core.db import engine, get_db, mcp_engine, now, transaction
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.main import app
from code_mortis.mcp import auth as mcp_auth
from code_mortis.mcp import request_body
from code_mortis.mcp import server as mcp_server
from code_mortis.mcp import tools as mcp_tools
from code_mortis.mcp.models import McpAuthorization, McpClient, McpGrant, McpToken
from code_mortis.models import AppConfig, Job, Membership, PullRequest, Repository, ReviewRun, SecurityScan
from code_mortis.reviews import service as reviews

VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
CALLBACK = "http://127.0.0.1:8765/callback"
RESOURCE = "http://testserver/mcp"


@pytest.fixture
def enabled(seed, github_stub, monkeypatch):
    monkeypatch.setattr(mcp_auth, "rate_limit", lambda *args, **kwargs: None)
    def visible_repositories(db, user):
        selected = effective_repository_ids(user)
        query = select(Repository)
        if selected is not None:
            query = query.where(Repository.id.in_(selected))
        return list(db.scalars(query))

    monkeypatch.setattr(github, "visible_repositories", visible_repositories)
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"enabled": True, "reviews_enabled": True, "security_enabled": True,
            "management_enabled": False, "allowed_client_domains": []}
        db.add(McpClient(client_id="test-cli", client_name="Test CLI", redirect_uris=[CALLBACK]))
    return seed


def authorization(client, *, domain="reviews", scopes=None, **overrides):
    values = {"client_id": "test-cli", "response_type": "code", "redirect_uri": CALLBACK,
        "resource": RESOURCE, "scope": scopes or domain + ":read",
        "state": "client-state", "code_challenge": CHALLENGE, "code_challenge_method": "S256", **overrides}
    if values["scope"] is None:
        del values["scope"]
    response = client.get("/oauth/authorize", params=values, follow_redirects=False)
    assert response.status_code == 303, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]


def consent_code(client, *, domain="reviews", scopes=None, repositories=None, **overrides):
    scopes = scopes or [domain + ":read"]
    request_id = authorization(client, domain=domain, scopes=" ".join(scopes), **overrides)
    preview = client.get("/api/v1/mcp/authorize", params={"request_id": request_id})
    assert preview.status_code == 200, preview.text
    groups = {mcp_auth.SCOPE_GROUP[scope] for scope in scopes}
    if repositories is None:
        repositories = {group: [22] for group in groups}
    response = client.post("/api/v1/mcp/authorize", json={"request_id": request_id, "repository_ids_by_group": repositories,
        "scopes": scopes, "approve": True})
    assert response.status_code == 200, response.text
    redirect = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert redirect["state"] == ["client-state"]
    assert redirect["iss"] == ["http://testserver"]
    return redirect["code"][0]


def exchange(client, code, **overrides):
    return client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": "test-cli",
        "code": code, "redirect_uri": CALLBACK, "resource": RESOURCE,
        "code_verifier": VERIFIER, **overrides})


@pytest.mark.parametrize("requested", [None, "reviews:read"])
def test_setup_offers_enabled_scopes_and_issues_only_selected_access(enabled, client, requested):
    request_id = authorization(client, scope=requested)
    preview = client.get("/api/v1/mcp/authorize", params={"request_id": request_id})
    assert preview.status_code == 200
    offered = {option["name"] for option in preview.json()["scopes"]}
    expected = set(mcp_auth.GROUP_SCOPES["reviews"]) | set(mcp_auth.GROUP_SCOPES["security"])
    assert offered == (expected if requested is None else {"reviews:read"})
    decision = client.post("/api/v1/mcp/authorize", json={
        "request_id": request_id, "scopes": ["reviews:read"], "repository_ids_by_group": {"reviews": [22]}, "approve": True,
    })
    assert decision.status_code == 200
    code = parse_qs(urlsplit(decision.json()["redirect_url"]).query)["code"][0]
    token = exchange(client, code)
    assert token.status_code == 200 and token.json()["scope"] == "reviews:read"


def credentials(client, **kwargs):
    response = exchange(client, consent_code(client, **kwargs))
    assert response.status_code == 200, response.text
    return response.json()


def rpc(client, token=None, method="initialize", arguments=None, domain="reviews", **headers):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if method == "initialize":
        payload["params"] = {"protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "tests", "version": "1"}}
    elif arguments is not None:
        payload["params"] = arguments
    return client.post("/mcp", json=payload, headers={"Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25", **({"Authorization": "Bearer " + token} if token else {}), **headers})


def test_default_settings_are_disabled_and_metadata_is_public(seed, client):
    response = client.get("/api/v1/mcp/settings")
    assert response.status_code == 200
    assert response.json()["reviews_enabled"] is False
    assert response.json()["security_enabled"] is False
    assert response.json()["management_enabled"] is False
    assert response.json()["enabled"] is False
    metadata = client.get("/.well-known/oauth-protected-resource/mcp")
    assert metadata.json()["resource"] == RESOURCE
    assert "security:issues" in metadata.json()["scopes_supported"]
    assert {"reviews:read", "code:read", "instance:manage"} <= set(metadata.json()["scopes_supported"])
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
        assert grant.repository_ids_by_group == {"reviews": [22]}
        assert grant.scopes == ["reviews:read"]
    response = rpc(client, tokens["access_token"])
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "Code Mortis"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("operation", ["verify_token", "list_tools", "call_tool", "error"])
def test_mcp_database_work_runs_off_event_loop_with_auth_context(enabled, client, monkeypatch, operation):
    tokens = credentials(client)
    verifier = mcp_auth.McpTokenVerifier()
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
    def checked_get_db():
        threads.append(get_ident())
        assert get_ident() != loop_thread, "Blocking database work ran on the event loop"
        yield from get_db()

    monkeypatch.setattr(mcp_auth, "get_db", checked_get_db)
    server = mcp_server.CodeMortisServer()
    mcp_tools.register_tools(server)

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
    assert bool(threads) is (operation != "list_tools")


def test_slow_mcp_authorization_keeps_event_loop_responsive(enabled, client, monkeypatch):
    tokens = credentials(client)
    entered, release = Event(), Event()
    original = mcp_auth.load_grant

    def slow_access(*args, **kwargs):
        entered.set()
        assert release.wait(5), "The event loop could not release the blocked authorization"
        return original(*args, **kwargs)

    monkeypatch.setattr(mcp_auth, "load_grant", slow_access)

    async def check():
        task = asyncio.create_task(mcp_auth.McpTokenVerifier().verify_token(tokens["access_token"]))
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
    event.listen(mcp_engine(), "before_cursor_execute", query)
    try:
        response = exchange(client, code) if endpoint == "token" else client.post("/oauth/revoke", data={
            "client_id": "test-cli", "token": tokens["access_token"]})
        assert response.status_code == 200, response.text
        assert loop_threads
    finally:
        event.remove(mcp_engine(), "before_cursor_execute", query)


@pytest.mark.parametrize("path", request_body.BODY_LIMITS)
def test_external_mcp_body_limit_applies_before_database_access(seed, client, path):
    def fail_checkout(*_):
        pytest.fail("An oversized OAuth request checked out a database connection")

    event.listen(mcp_engine(), "checkout", fail_checkout)
    event.listen(engine(), "checkout", fail_checkout)
    try:
        response = client.post(path, content=b"x" * (request_body.BODY_LIMITS[path] + 1))
        assert response.status_code == 413
        assert response.headers["cache-control"] == "no-store"
    finally:
        event.remove(engine(), "checkout", fail_checkout)
        event.remove(mcp_engine(), "checkout", fail_checkout)


def test_slow_oauth_upload_releases_capacity_without_database_access(seed, client, monkeypatch):
    monkeypatch.setattr(request_body, "BODY_TIMEOUT_SECONDS", 0.01)

    def fail_checkout(*_):
        pytest.fail("An incomplete OAuth upload checked out a database connection")

    async def slow_body():
        yield b"client_id="
        await asyncio.sleep(10)

    async def exercise():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as http:
            async with asyncio.timeout(2):
                responses = await asyncio.gather(*(http.post("/oauth/token", content=slow_body()) for _ in range(2)))
                assert [response.status_code for response in responses] == [408, 408]
                recovered = await http.post("/oauth/token")
                assert recovered.status_code == 400

    event.listen(mcp_engine(), "checkout", fail_checkout)
    event.listen(engine(), "checkout", fail_checkout)
    try:
        client.portal.call(exercise)
    finally:
        event.remove(engine(), "checkout", fail_checkout)
        event.remove(mcp_engine(), "checkout", fail_checkout)


@pytest.mark.parametrize("domain", ["reviews", "security"])
def test_oauth_accepts_identical_repeated_resources(enabled, client, domain):
    resource = RESOURCE
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
        assert "oauth-protected-resource/mcp" in response.headers["www-authenticate"]


@pytest.mark.parametrize("changes", [
    {"code_challenge": ""}, {"code_challenge_method": "plain"}, {"response_type": "token"},
    {"redirect_uri": "https://attacker.example/callback"}, {"resource": "https://elsewhere.example/mcp"},
    {"scope": "unsupported:read"}, {"resource": "http://testserver/mcp/reviews"},
])
def test_authorization_rejects_invalid_protocol_and_resource(enabled, client, changes):
    values = {"client_id": "test-cli", "response_type": "code", "redirect_uri": CALLBACK,
        "resource": RESOURCE, "scope": "reviews:read", "state": "state",
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
        "resource": RESOURCE, "refresh_token": tokens["refresh_token"]}
    refreshed = client.post("/oauth/token", data=data)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/oauth/token", data=data).status_code == 400
    assert rpc(client, refreshed.json()["access_token"]).status_code == 401


def test_refresh_scope_narrowing_does_not_restore_write_access(enabled, client):
    tokens = credentials(client, scopes=["reviews:read", "reviews:write"])
    narrowed = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "test-cli",
        "resource": RESOURCE, "refresh_token": tokens["refresh_token"], "scope": "reviews:read"})
    assert narrowed.status_code == 200, narrowed.text
    with transaction() as db:
        assert db.get(McpToken, digest(narrowed.json()["access_token"])).scopes == ["reviews:read"]


def test_grant_revoke_and_domain_disable_are_immediate_and_independent(enabled, client):
    tokens = credentials(client)
    grant_id = client.get("/api/v1/mcp/grants").json()[0]["id"]
    assert client.delete("/api/v1/mcp/grants/" + grant_id).status_code == 200
    assert rpc(client, tokens["access_token"]).status_code == 401
    tokens = credentials(client)
    response = client.put("/api/v1/mcp/settings", json={"enabled": True, "reviews_enabled": False,
        "security_enabled": True, "management_enabled": False, "allowed_client_domains": []})
    assert response.status_code == 200, response.text
    assert response.json()["security_enabled"] is True
    assert rpc(client, tokens["access_token"]).status_code == 401
    client.put("/api/v1/mcp/settings", json={"enabled": True, "reviews_enabled": True,
        "security_enabled": True, "management_enabled": False, "allowed_client_domains": []})
    assert rpc(client, tokens["access_token"]).status_code == 401


def test_legacy_transport_retires_without_redirect(enabled, client):
    tokens = credentials(client)
    response = client.post("/mcp/security", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        headers={"Accept": "application/json, text/event-stream", "Authorization": "Bearer " + tokens["access_token"]})
    assert response.status_code == 404


def test_consent_bound_to_browser_and_session_and_cannot_expand(enabled, client):
    identity = authorization(client)
    assert client.get("/api/v1/mcp/authorize", params={"request_id": identity},
        headers={"cookie": "cm_session=session; cm_mcp_browser=" + "b" * 43}).status_code == 409
    identity = authorization(client)
    response = client.post("/api/v1/mcp/authorize", json={"request_id": identity,
        "repository_ids_by_group": {"reviews": [22]}, "scopes": ["reviews:read", "reviews:write"], "approve": True})
    assert response.status_code == 422
    response = client.post("/api/v1/mcp/authorize", json={"request_id": identity,
        "repository_ids_by_group": {"reviews": [99]}, "scopes": ["reviews:read"], "approve": True})
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


def test_unified_grant_keeps_group_repository_selections_independent(enabled, client):
    with transaction() as db:
        db.add(Repository(id=23, installation_id=10, full_name="team/security-only"))
    tokens = credentials(client, scopes=["reviews:read", "security:read"],
        repositories={"reviews": [22], "security": [23]})
    token = tokens["access_token"]
    tools = rpc(client, token, method="tools/list").json()["result"]["tools"]
    assert {"reviews_list_repositories", "security_list_repositories"} <= {tool["name"] for tool in tools}
    for group, expected in (("reviews", 22), ("security", 23)):
        result = rpc(client, token, method="tools/call",
            arguments={"name": group + "_list_repositories",
                "arguments": {"params": {}} if group == "security" else {}}).json()["result"]
        assert not result.get("isError"), result
        items = result["structuredContent"]["items"]
        assert [item["repository"]["id"] if group == "security" else item["id"] for item in items] == [expected]
        if group == "security":
            assert not {"review_rules", "settings", "code_graph_enabled"} & items[0]["repository"].keys()
        else:
            assert not {"security", "scan_settings", "code_graph_enabled"} & items[0].keys()
    grant = client.get("/api/v1/mcp/grants").json()[0]
    assert grant["repository_ids_by_group"] == {"reviews": [22], "security": [23]}
    settings = {"enabled": True, "reviews_enabled": True, "security_enabled": False}
    assert client.put("/api/v1/mcp/settings", json=settings).status_code == 200
    assert client.put("/api/v1/mcp/settings", json={**settings, "security_enabled": True}).status_code == 200
    tools = rpc(client, token, method="tools/list").json()["result"]["tools"]
    assert all(tool["name"].startswith("reviews_") for tool in tools)
    assert tools
    assert client.put("/api/v1/mcp/settings", json={**settings, "enabled": False}).status_code == 200
    assert rpc(client, token).status_code == 401


def test_tool_discovery_coalesces_grant_usage_writes(enabled, client):
    token = credentials(client)["access_token"]
    assert rpc(client, token, method="tools/list").status_code == 200
    with transaction() as db:
        previous = db.scalar(select(McpGrant)).last_used_at
    assert previous is not None
    assert rpc(client, token, method="tools/list").status_code == 200
    with transaction() as db:
        grant = db.scalar(select(McpGrant))
        assert grant.last_used_at == previous
        grant.last_used_at = previous - timedelta(minutes=2)
    assert rpc(client, token, method="tools/list").status_code == 200
    with transaction() as db:
        assert db.scalar(select(McpGrant)).last_used_at >= previous


def test_tool_discovery_only_performs_transport_authorization_queries(enabled, client):
    token = credentials(client, scopes=["reviews:read", "security:read"])["access_token"]
    assert rpc(client, token).status_code == 200  # Warm the coalesced last-used update.
    queries = []

    def record_query(_conn, _cursor, statement, *_):
        queries.append(statement)

    event.listen(mcp_engine(), "before_cursor_execute", record_query)
    try:
        assert rpc(client, token).status_code == 200
        initialize_queries = list(queries)
        queries.clear()
        listed = rpc(client, token, method="tools/list")
        assert listed.status_code == 200
        assert len(listed.json()["result"]["tools"]) > 10
        assert queries == initialize_queries
    finally:
        event.remove(mcp_engine(), "before_cursor_execute", record_query)


def test_large_saved_reports_are_available_separately_from_mcp_history(enabled, client):
    report = {"findings": [], "notes": "🧪" * 80000}
    with transaction() as db:
        db.add(ReviewRun(id="large-run", pr_id="pr", trigger="manual", dedup_key="large-run",
            base_sha="b" * 40, head_sha="a" * 40, engine="mortis", status="completed", config={}, report=report))
        db.add(SecurityScan(id="large-scan", repository_id=22, actor_id=7, connection_id="connection",
            request_key="large-scan", branch="main", head_sha="a" * 40, config={}, report=report))
    review_token = credentials(client)["access_token"]
    security_token = credentials(client, domain="security")["access_token"]

    def call(name, params, domain="reviews"):
        token = review_token if domain == "reviews" else security_token
        response = rpc(client, token, method="tools/call", domain=domain,
                       arguments={"name": name, "arguments": {"params": params}})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert not result.get("isError"), result
        return result["structuredContent"]

    runs = call("reviews_list_runs", {"pr_id": "pr"})
    assert runs["total"] == 1
    assert runs["items"][0]["id"] == "large-run"
    assert "report" not in runs["items"][0]
    pulls = call("reviews_list_repository_pulls", {"repository_id": 22})
    assert pulls["groups"][0]["items"][0]["latest_run"]["id"] == "large-run"
    pull = call("reviews_get_pull_request", {"pr_id": "pr"})
    assert pull["pull_request"]["latest_run"]["id"] == "large-run"
    detail = call("reviews_get_run", {"run_id": "large-run"})
    assert detail["run"]["status"] == "completed"
    assert detail["pull_request"]["latest_run"]["id"] == "large-run"

    chunks, offset = [], 0
    while offset is not None:
        section = call("reviews_get_run_section", {"run_id": "large-run", "section": "report", "offset": offset})
        chunks.append(section["content"])
        assert section["next_offset"] is None or section["next_offset"] > offset
        offset = section["next_offset"]
    assert json.loads("".join(chunks)) == report

    scans = call("security_list_scans", {"repository_id": 22}, "security")
    assert scans["total"] == 1
    assert scans["items"][0]["id"] == "large-scan"
    assert "report" not in scans["items"][0]
    assert call("security_get_report", {"scan_id": "large-scan"}, "security")["next_offset"] > 0


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
        db.scalar(select(McpGrant)).repository_ids_by_group = {"reviews": []}
    response = rpc(client, tokens["access_token"], method="tools/call",
        arguments={"name": "reviews_get_pull_request", "arguments": {"params": {"pr_id": "pr"}}})
    assert response.json()["result"]["structuredContent"]["error"]["status"] == 403


def test_action_approval_is_human_bound_exact_once_and_retryable(enabled, client, monkeypatch):
    tokens = credentials(client, domain="security", scopes=["security:issues"])
    with transaction() as db:
        token = db.get(McpToken, digest(tokens["access_token"]))
        grant_id = token.grant_id
    access = mcp_auth.AccessToken(token=tokens["access_token"], client_id="test-cli",
        scopes=["security:issues"], resource=RESOURCE)
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
    tokens = credentials(client)
    args = {"name": "reviews_get_pull_request", "arguments": {"params": {"pr_id": "pr"}}}
    with monkeypatch.context() as patch:
        patch.setattr(mcp_tools.pulls_routes, "pull_detail", lambda *args: (_ for _ in ()).throw(
            github.GitHubError(502, "Provider error containing provider-secret")))
        response = rpc(client, tokens["access_token"], method="tools/call", arguments=args)
    assert "provider-secret" not in response.text
    assert response.json()["result"]["structuredContent"]["error"]["status"] == 502
    with transaction() as db:
        db.get(PullRequest, "pr").title = "Unsafe title with provider-secret"
    response = rpc(client, tokens["access_token"], method="tools/call", arguments=args)
    assert "provider-secret" not in response.text
    assert response.json()["result"]["structuredContent"]["error"]["status"] == 422


def test_approved_disconnect_commits_revocation_and_replays_without_native_logout(enabled, client, monkeypatch):
    from code_mortis.mcp.models import McpApproval
    from code_mortis.models import Connection

    with transaction() as db:
        config = db.get(AppConfig, 1)
        config.mcp_settings = {**config.mcp_settings, "management_enabled": True}
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "ready"
    tokens = credentials(client, scopes=["connections:manage"])
    with transaction() as db:
        grant_id = db.get(McpToken, digest(tokens["access_token"])).grant_id
        db.add(McpApproval(id="old-approved-native-action", grant_id=grant_id,
                          tool="management_grant_repository_access", arguments_hash="a" * 64,
                          arguments={"connection_id": "connection", "repository_id": 22},
                          approved_at=now(), expires_at=now() + timedelta(hours=1)))
    listed = rpc(client, tokens["access_token"], method="tools/list")
    names = {item["name"] for item in listed.json()["result"]["tools"]}
    assert "management_create_connection" in names and "management_test_connection" in names
    assert "management_disconnect_connection" not in names and "management_grant_repository_access" not in names
    for name in ("management_disconnect_connection", "management_grant_repository_access"):
        response = rpc(client, tokens["access_token"], method="tools/call", arguments={"name": name, "arguments": {"params": {
            "connection_id": "connection", "request_key": "old-approved-native-key", "approval_id": "old-approved-native-action"}}})
        assert response.json()["result"]["isError"] is True
    with transaction() as db:
        assert db.get(McpApproval, "old-approved-native-action").consumed_at is None
        assert db.get(McpGrant, grant_id).revoked_at is None
        assert db.get(Connection, "connection").auth_status == "ready"


def test_preregistered_https_client_does_not_require_metadata_fetch(enabled, monkeypatch):
    monkeypatch.setattr(mcp_auth, "fetch_client_metadata", lambda *args: pytest.fail("Registered metadata is authoritative"))
    with transaction() as db:
        client_id = "https://client.example/oauth.json"
        db.add(McpClient(client_id=client_id, client_name="Registered client", redirect_uris=[CALLBACK]))
        db.flush()
        assert mcp_auth.resolve_client(db, client_id).client_name == "Registered client"


def test_scan_request_keys_are_isolated_between_mcp_grants(enabled, client, monkeypatch):
    from code_mortis.models import SecurityScan
    from code_mortis.security_scans import service as repository_security

    monkeypatch.setattr(repository_security, "branch_head", lambda *args: {
        "default_branch": "main", "head_sha": "a" * 40, "checked_at": now().isoformat()})
    saved_limits = {"token_budget": 50000, "timeout_seconds": 600, "concurrency": 3,
                    "effort": "low", "max_output_tokens": 8192, "assessment_token_budget": 3000,
                    "verification_token_budget": 5000, "exclusions": "fixtures/**"}
    with transaction() as db:
        db.get(Repository, 22).scan_settings = saved_limits
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
        assert all(row.config["limits"] == saved_limits for row in rows)
        assert len({row.progress["mcp"]["grant_id"] for row in rows}) == 2
