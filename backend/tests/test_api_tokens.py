"""Personal API token contracts: policy inventory, lifecycle and auth boundary."""

import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from code_mortis.api_tokens import policy, service
from code_mortis.core.db import now, transaction
from code_mortis.core.security import clean_text, digest
from code_mortis.main import app
from code_mortis.models import (
    Connection,
    ConnectionGrant,
    LoginSession,
    Membership,
    PersonalApiToken,
    Repository,
    User,
)


def test_policy_covers_every_api_operation():
    operations = {(method.upper(), path) for path, methods in app.openapi()["paths"].items()
                  for method in methods if path.startswith("/api/v1")}
    known = (set(policy.PAT_ROUTE_SCOPES) | policy.SESSION_ONLY_ROUTES
             | set(policy.SPECIAL_ROUTES) | policy.PUBLIC_ROUTES | {policy.AUTH_TOKEN_ROUTE})
    assert len(operations) > 90
    assert operations - known == set()
    assert len(policy.PAT_ROUTE_SCOPES) == 53
    assert policy.AUTH_TOKEN_ROUTE not in policy.PAT_ROUTE_SCOPES
    assert policy.pat_scope("GET", "/api/v1/repositories") == "repositories:read"
    assert policy.pat_scope("GET", "/api/v1/nope") is None


def test_scope_catalog_matches_plan():
    assert set(policy.SCOPES) == {
        "repositories:read", "reviews:read", "reviews:write", "reviews:run",
        "security:read", "security:write", "security:run", "connections:read", "analytics:read",
    }


def test_every_pat_route_enforces_its_scope(pat_client):
    import re

    client, raw, token_id = pat_client
    with transaction() as db:
        db.get(PersonalApiToken, token_id).scopes = ["repositories:read"]
    for (method, template), required in policy.PAT_ROUTE_SCOPES.items():
        if required == "repositories:read":
            continue
        path = re.sub(r"\{[^}]+\}", "22", template)
        response = client.request(method, path, headers={"Authorization": f"Bearer {raw}"})
        assert response.status_code == 403, (method, template, response.text)


def test_every_session_only_route_refuses_pat(pat_client):
    import re

    client, raw, _ = pat_client
    for method, template in policy.SESSION_ONLY_ROUTES:
        path = re.sub(r"\{[^}]+\}", "22", template)
        response = client.request(method, path, headers={"Authorization": f"Bearer {raw}"})
        assert response.status_code in (401, 403), (method, template, response.text)


def test_unselected_repository_cannot_leak_history_counts_or_facets(pat_client):
    from test_analytics import finding, run
    from test_inbox import pull
    from test_security_analytics import finding as security_finding
    from test_security_analytics import scan

    client, raw, token_id = pat_client
    with transaction() as db:
        db.get(PersonalApiToken, token_id).scopes = [scope for scope in policy.SCOPES if scope.endswith(":read")]
        db.add(Repository(id=23, installation_id=10, full_name="team/hidden-repository", enabled=True))
        db.flush()
        pull(db, "hidden-pull", repo=23)
        run(db, "hidden-run", pr="hidden-pull")
        finding(db, "hidden-finding", pr_id="hidden-pull", last_seen_run_id="hidden-run", category="hidden-category")
        scan(db, "hidden-scan", repository_id=23)
        security_finding(db, "hidden-security", repository_id=23, first_scan_id="hidden-scan", last_scan_id="hidden-scan", kind="hidden-kind")
    headers = {"Authorization": f"Bearer {raw}"}
    for path in ("/repositories", "/pulls", "/security/repositories", "/analytics/overview?all_time=true",
                 "/analytics/findings?all_time=true", "/analytics/reviews?all_time=true", "/analytics/usage?all_time=true",
                 "/analytics/security?all_time=true"):
        response = client.get("/api/v1" + path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        assert "hidden-" not in response.text, (path, response.text)
        if path.startswith("/analytics/"):
            assert {repo["id"] for repo in response.json()["scope"]["repositories"]} == {22}
    overview = client.get("/api/v1/analytics/overview?all_time=true", headers=headers).json()
    assert overview["reviewed_prs"] == overview["findings"] == 0
    facets = client.get("/api/v1/analytics/findings?all_time=true", headers=headers).json()
    assert facets["total"] == facets["unfiltered_total"] == 0
    assert "hidden-category" not in facets["category_options"]
    for path in ("/repositories/23/pulls", "/runs/hidden-run", "/findings/hidden-finding", "/security/repositories/23"):
        assert client.get("/api/v1" + path, headers=headers).status_code in (403, 404)
    assert client.get("/api/v1/analytics/overview?repo=23", headers=headers).status_code == 404


def test_http_dependency_preserves_origin_through_threadpool_and_resets(pat_client):
    from code_mortis.models import Job

    client, raw, token_id = pat_client
    with transaction() as db:
        db.get(PersonalApiToken, token_id).scopes = ["reviews:run"]
    response = client.post("/api/v1/repositories/22/sync", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 202, response.text
    with transaction() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job.payload["pat"] == {"token_id": token_id, "user_id": 7,
                                      "scope": "reviews:run", "repository_id": 22}
        assert raw not in str(job.payload)
    client.cookies.set("cm_session", "fresh")
    browser = client.post("/api/v1/repositories/22/sync", headers={"x-csrf-token": "csrf"})
    assert browser.status_code == 202, browser.text
    with transaction() as db:
        assert "pat" not in db.get(Job, browser.json()["job_id"]).payload


def test_token_format_validation():
    good = "cm_pat_" + "A" * 43
    assert service.parse_raw_token(good) == good
    for bad in ["", "cm_pat_short", "mcp_token", "cm_pat_" + "A" * 44, "x" * 201]:
        with pytest.raises(HTTPException):
            service.parse_raw_token(bad)


def test_scrubber_redacts_pat_format():
    raw = "cm_pat_" + "B" * 43
    assert raw not in clean_text(f"leaked {raw} here")
    assert raw not in clean_text(f"Authorization: Bearer {raw}")


def test_effective_selection_intersects():
    from code_mortis.core.access import effective_repository_ids

    class Fake:
        pass

    user = Fake()
    assert effective_repository_ids(user) is None
    user.api_token_repository_ids = frozenset({1, 2, 3})
    assert effective_repository_ids(user) == {1, 2, 3}
    user.mcp_repository_ids = frozenset({2, 3, 4})
    assert effective_repository_ids(user) == {2, 3}


@pytest.fixture
def fresh_session(seed):
    with transaction() as db:
        db.add(LoginSession(token_hash=digest("fresh"), user_id=seed["user"].id,
                            csrf="csrf", expires_at=now() + timedelta(days=1),
                            authenticated_at=now()))
    return seed


@pytest.fixture
def pat_client(fresh_session, monkeypatch):
    from code_mortis.integrations import github

    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"permissions": {"pull": True, "push": True}})
    def visible(db, user):
        selected = getattr(user, "api_token_repository_ids", None)
        return [repo for repo in db.scalars(select(Repository)).all()
                if selected is None or repo.id in selected]
    monkeypatch.setattr(github, "visible_repositories", visible)
    with transaction() as db:
        user = db.get(User, fresh_session["user"].id)
        token, raw = service.create_token(
            db, user, name="noodle", scopes=["repositories:read", "reviews:read", "connections:read"],
            repository_ids=[fresh_session["repo"].id], expires_in_days=30)
        token_id = token.id
    with TestClient(app) as client:
        client.cookies.clear()
        yield client, raw, token_id


def test_create_lists_and_revokes(fresh_session, github_stub, monkeypatch):
    from code_mortis.integrations import github

    def visible(db, user):
        selected = getattr(user, "api_token_repository_ids", None)
        return [repo for repo in db.scalars(select(Repository)).all()
                if selected is None or repo.id in selected]
    monkeypatch.setattr(github, "visible_repositories", visible)
    with TestClient(app) as client:
        client.cookies.set("cm_session", "fresh")
        client.headers["x-csrf-token"] = "csrf"
        created = client.post("/api/v1/api-tokens", json={
            "name": "noodle", "scopes": ["repositories:read"],
            "repository_ids": [fresh_session["repo"].id], "expires_in_days": 30})
        assert created.status_code == 201, created.text
        secret, item = created.json()["token"], created.json()["item"]
        assert secret.startswith("cm_pat_") and item["prefix"] == secret[:12]
        assert item["status"] == "active" and "token" not in item
        listed = client.get("/api/v1/api-tokens").json()
        assert listed["total"] == 1 and listed["active_count"] == 1
        assert listed["reauth_required"] is False
        assert listed["permissions"] and listed["default_expiration_days"] == 30
        assert all("token" not in row for row in listed["items"])
        revoked = client.delete(f"/api/v1/api-tokens/{item['id']}")
        assert revoked.status_code == 202
        assert client.delete(f"/api/v1/api-tokens/{item['id']}").status_code == 202
        assert client.get("/api/v1/api-tokens?status=revoked").json()["total"] == 1
        assert secret not in client.get("/api/v1/api-tokens?status=all").text


def test_create_requires_fresh_session(seed, client):
    response = client.post("/api/v1/api-tokens", json={
        "name": "stale", "scopes": ["repositories:read"], "repository_ids": [22], "expires_in_days": 30})
    assert response.status_code == 403
    assert client.get("/api/v1/api-tokens").json()["reauth_required"] is True


def test_sign_in_returns_to_token_creation_page(seed, client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from code_mortis.integrations import github

    monkeypatch.setattr(github, "exchange_code", lambda *a: {"access_token": "token"})
    monkeypatch.setattr(github, "request", lambda *a, **k: {"id": 7, "login": "owner", "avatar_url": ""})
    client.get("/api/v1/setup/status")
    login = client.get("/api/v1/auth/login", params={"return_to": "/settings/api-tokens/new"}, follow_redirects=False)
    assert login.status_code == 303
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = client.get("/api/v1/auth/callback", params={"code": "valid", "state": state}, follow_redirects=False)
    assert callback.headers["location"] == "http://testserver/settings/api-tokens/new"
    assert client.get("/api/v1/api-tokens").json()["reauth_required"] is False


@pytest.mark.parametrize("destination", [
    "https://example.com/settings/api-tokens/new", "//example.com/settings/api-tokens/new",
    "/settings/api-tokens/new?next=https://example.com", "/settings/api-tokens/other",
])
def test_token_creation_return_address_stays_exact(seed, client, destination):
    response = client.get("/api/v1/auth/login", params={"return_to": destination}, follow_redirects=False)
    assert response.status_code == 400


@pytest.mark.parametrize("refresh", [False, True])
def test_create_with_real_github_credential_transaction(fresh_session, monkeypatch, refresh):
    from contextlib import contextmanager

    from sqlalchemy import text

    from code_mortis.core.security import seal, unseal
    from code_mortis.integrations import github

    # Exercise the real credential lock; only the external GitHub calls are fake.
    @contextmanager
    def bounded_transaction():
        with transaction() as db:
            db.execute(text("SET LOCAL lock_timeout = '1s'"))
            yield db

    monkeypatch.setattr(github, "transaction", bounded_transaction)
    monkeypatch.setattr(github, "pages", lambda *a, **k: [{"id": 22}])
    refreshed = []

    def refresh_credentials(data):
        refreshed.append(data["grant_type"])
        return {"access_token": "new-github-access", "refresh_token": "new-github-refresh", "expires_in": 3600}

    monkeypatch.setattr(github, "oauth_request", refresh_credentials)
    if refresh:
        with transaction() as db:
            user = db.get(User, fresh_session["user"].id)
            user.token_expires = now() - timedelta(seconds=1)
            user.refresh_token = seal("old-github-refresh")
    with TestClient(app) as browser:
        browser.cookies.set("cm_session", "fresh")
        response = browser.post("/api/v1/api-tokens", headers={"x-csrf-token": "csrf"}, json={
            "name": "real-credential-lock", "scopes": ["repositories:read"],
            "repository_ids": [22], "expires_in_days": 30})
        assert response.status_code == 201, response.text
        result = response.json()
        assert response.headers["cache-control"] == "no-store"
        assert browser.get("/api/v1/api-tokens").json()["total"] == 1
    assert refreshed == (["refresh_token"] if refresh else [])
    with transaction() as db:
        assert db.get(PersonalApiToken, result["item"]["id"]).token_hash == digest(result["token"])
        if refresh:
            assert unseal(db.get(User, fresh_session["user"].id).refresh_token) == "new-github-refresh"


def test_active_cap_is_atomic(fresh_session, github_stub, monkeypatch):
    from code_mortis.integrations import github

    def visible(db, user):
        selected = getattr(user, "api_token_repository_ids", None)
        return [repo for repo in db.scalars(select(Repository)).all()
                if selected is None or repo.id in selected]
    monkeypatch.setattr(github, "visible_repositories", visible)
    with transaction() as db:
        user = db.get(User, fresh_session["user"].id)
        for _ in range(policy.MAX_ACTIVE_TOKENS):
            service.create_token(db, user, name="n", scopes=["repositories:read"],
                                 repository_ids=[fresh_session["repo"].id], expires_in_days=30)
    with TestClient(app) as client:
        client.cookies.set("cm_session", "fresh")
        client.headers["x-csrf-token"] = "csrf"
        response = client.post("/api/v1/api-tokens", json={
            "name": "extra", "scopes": ["repositories:read"],
            "repository_ids": [fresh_session["repo"].id], "expires_in_days": 30})
        assert response.status_code == 409


def test_pat_auth_boundary(pat_client, github_stub):
    client, raw, _ = pat_client
    ok = client.get("/api/v1/repositories", headers={"Authorization": f"Bearer {raw}"})
    assert ok.status_code == 200, ok.text
    denied = client.put("/api/v1/repositories/22", headers={"Authorization": f"Bearer {raw}"}, json={})
    assert denied.status_code == 403
    unknown = client.get("/api/v1/settings", headers={"Authorization": f"Bearer {raw}"})
    assert unknown.status_code == 403
    mixed = client.get("/api/v1/repositories", headers={"Authorization": f"Bearer {raw}"},
                       cookies={"cm_session": "session"})
    assert mixed.status_code == 401
    malformed = client.get("/api/v1/repositories", headers={"Authorization": "Bearer not-a-token"})
    assert malformed.status_code == 401
    assert malformed.headers.get("WWW-Authenticate") == "Bearer"
    forged = client.get("/api/v1/repositories", headers={"Authorization": f"Bearer cm_pat_{'C' * 43}"})
    assert forged.status_code == 401
    identity = client.get("/api/v1/auth/token", headers={"Authorization": f"Bearer {raw}"})
    assert identity.status_code == 200
    assert identity.json()["token"]["scopes"] == ["connections:read", "repositories:read", "reviews:read"]
    assert identity.json()["user"] == {"id": 7, "login": "owner", "avatar_url": "", "role": "admin"}
    assert "csrf" not in identity.json()["user"]


@pytest.mark.parametrize("reason", ["duplicate_credential", "cleartext_transport", "mixed_credential",
                                    "malformed_credential", "malformed_token"])
@pytest.mark.parametrize("status", [401, 429, 503])
def test_early_credential_refusals_check_redis_outside_event_loop(client, monkeypatch, reason, status):
    checks, reasons = [], []

    def check_failed_auth(identity):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        checks.append(identity)
        if status == 429:
            raise HTTPException(429, "Too many API token requests.", headers={"Retry-After": "60"})
        if status == 503:
            raise service.TokenUnavailable()

    monkeypatch.setattr(service, "check_failed_auth", check_failed_auth)
    monkeypatch.setattr(service, "log_failed_auth", lambda request, cause: reasons.append(cause))
    if reason != "mixed_credential":
        client.cookies.clear()
    headers = [("Authorization", "Basic invalid" if reason == "malformed_credential" else "Bearer invalid")]
    if reason == "duplicate_credential":
        headers *= 2
    path = "/api/v1/auth/token"
    if reason == "cleartext_transport":
        path = "http://remote.test" + path
    response = client.get(path, headers=headers)
    assert response.status_code == status
    assert len(checks) == 1 and reasons == [reason]
    if status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    if status == 429:
        assert response.headers["Retry-After"] == "60"


def test_pat_scope_and_repo_restriction(pat_client):
    client, raw, _ = pat_client
    headers = {"Authorization": f"Bearer {raw}"}
    assert client.post("/api/v1/pulls/x/runs", headers=headers, json={}).status_code == 403
    with transaction() as db:
        db.add(Repository(id=99, installation_id=10, full_name="team/other"))
    assert client.get("/api/v1/repositories/99/pulls", headers=headers).status_code == 403


def test_revoked_token_refused(pat_client):
    client, raw, token_id = pat_client
    headers = {"Authorization": f"Bearer {raw}"}
    with transaction() as db:
        service.revoke(db, db.get(PersonalApiToken, token_id), reason="user")
    assert client.get("/api/v1/repositories", headers=headers).status_code == 401
    with transaction() as db:
        remaining = db.scalar(select(PersonalApiToken).where(PersonalApiToken.id == token_id))
        assert remaining.revoked_at is not None


def test_authorize_rejects_inactive_and_wrong_scope(fresh_session):
    with transaction() as db:
        user_id = fresh_session["user"].id
        expired = PersonalApiToken(id="expired", user_id=user_id, name="e", token_hash=digest("e"),
                                   prefix="cm_pat_e", scopes=["reviews:read"], repository_ids=[22],
                                   created_at=now(), expires_at=now() - timedelta(seconds=1))
        narrow = PersonalApiToken(id="narrow", user_id=user_id, name="n", token_hash=digest("n"),
                                  prefix="cm_pat_n", scopes=["reviews:read"], repository_ids=[22],
                                  created_at=now(), expires_at=now() + timedelta(days=1))
        db.add_all([expired, narrow])
    with transaction() as db:
        with pytest.raises(HTTPException):
            service.authorize(db, "expired")
        with pytest.raises(HTTPException):
            service.authorize(db, "narrow", scope="reviews:run")
        with pytest.raises(HTTPException):
            service.authorize(db, "narrow", scope="reviews:read", repository_id=99)
        user, token = service.authorize(db, "narrow", scope="reviews:read", lock=True)
        assert user.id == user_id and token.id == "narrow"
        assert set(user.api_token_repository_ids) == {22}


def test_session_identity_rejects_bearer(pat_client):
    from code_mortis.api.dependencies import current_user

    client, raw, _ = pat_client
    # Session-only routes use the session helper directly, which refuses any
    # bearer credential with 401 instead of consulting PAT policy.
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 401
    # The session helper itself refuses bearer credentials instead of reading a cookie.
    with transaction() as db:
        request = _request_with_auth("Bearer session-value")
        with pytest.raises(HTTPException) as exc:
            current_user(request, db)
        assert exc.value.status_code == 401


def _request_with_auth(value: str):
    from starlette.requests import Request as StarletteRequest

    scope = {"type": "http", "method": "GET", "headers": [(b"authorization", value.encode())],
             "query_string": b"", "server": ("testserver", 80), "scheme": "http", "path": "/"}
    return StarletteRequest(scope)


def test_member_removal_revokes_permanently(seed, client):
    with transaction() as db:
        db.add(User(id=9, login="teammate", role="member"))
        db.add(Membership(github_id=9, login="teammate", role="member"))
        db.flush()
        db.add(PersonalApiToken(id="token-9", user_id=9, name="t", token_hash=digest("x"),
                                prefix="cm_pat_x", scopes=["reviews:read"], repository_ids=[22],
                                created_at=now(), expires_at=now() + timedelta(days=1)))
    assert client.delete("/api/v1/members/9").status_code == 200
    with transaction() as db:
        assert db.get(PersonalApiToken, "token-9").revoked_at is not None
        from code_mortis.models import ApiTokenEvent

        audit = db.scalar(select(ApiTokenEvent).where(ApiTokenEvent.token_id == "token-9"))
        assert (audit.event, audit.actor_id, audit.reason) == ("revoked", 7, "membership_removed")


def test_connections_filtered_to_selected(pat_client, seed):
    client, raw, _ = pat_client
    headers = {"Authorization": f"Bearer {raw}"}
    with transaction() as db:
        db.add(Connection(id="other-api", name="Other", provider="openai", protocol="openai",
                          model="m", base_url="", secret=seed["connection"].secret, kind="api"))
        db.add(Repository(id=23, installation_id=10, full_name="team/unselected",
                          connection_id="other-api"))
        db.flush()
        db.add(ConnectionGrant(connection_id="other-api", repository_id=23))
    rows = client.get("/api/v1/connections", headers=headers).json()
    assert {row["id"] for row in rows} == {"connection"}
    assert "team/unselected" not in client.get("/api/v1/connections", headers=headers).text
    assert client.get("/api/v1/connections/other-api/models", headers=headers).status_code == 403


def test_owned_personal_connection_needs_grant(pat_client, seed, monkeypatch):
    from code_mortis.integrations import runner_client

    client, raw, _ = pat_client
    headers = {"Authorization": f"Bearer {raw}"}
    with transaction() as db:
        db.add(Connection(id="personal", name="Mine", provider="anthropic", protocol="native",
                          model="", base_url="", secret=seed["connection"].secret,
                          kind="claude_code", owner_id=7))
        db.flush()
        db.add(ConnectionGrant(connection_id="personal", repository_id=22))
    monkeypatch.setattr(runner_client, "request", lambda *a, **k: pytest.fail("Native model discovery"))
    listed = client.get("/api/v1/connections", headers=headers).json()
    assert "personal" not in {row["id"] for row in listed}
    assert client.get("/api/v1/connections/personal/models", headers=headers).status_code == 410


def test_connections_hidden_when_github_access_lost(pat_client, monkeypatch):
    from code_mortis.integrations import github

    client, raw, _ = pat_client
    headers = {"Authorization": f"Bearer {raw}"}
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: [])
    assert client.get("/api/v1/connections", headers=headers).json() == []


def test_list_paginates_in_sql(fresh_session, github_stub, monkeypatch):
    from code_mortis.integrations import github

    def visible(db, user):
        selected = getattr(user, "api_token_repository_ids", None)
        return [repo for repo in db.scalars(select(Repository)).all()
                if selected is None or repo.id in selected]
    monkeypatch.setattr(github, "visible_repositories", visible)
    with transaction() as db:
        user = db.get(User, fresh_session["user"].id)
        for index in range(12):
            service.create_token(db, user, name=f"t{index}", scopes=["repositories:read"],
                                 repository_ids=[fresh_session["repo"].id], expires_in_days=30)
        stale = db.scalars(select(PersonalApiToken).where(
            PersonalApiToken.user_id == user.id).order_by(PersonalApiToken.created_at)).first()
        stale.expires_at = now() - timedelta(seconds=1)
        gone = db.scalars(select(PersonalApiToken).where(
            PersonalApiToken.user_id == user.id).order_by(PersonalApiToken.created_at.desc())).first()
        service.revoke(db, gone, actor_id=user.id, reason="user")
    with TestClient(app) as client:
        client.cookies.set("cm_session", "fresh")
        client.headers["x-csrf-token"] = "csrf"
        first = client.get("/api/v1/api-tokens?status=all").json()
        assert (first["total"], len(first["items"]), first["has_more"], first["active_count"]) == (12, 10, True, 10)
        second = client.get("/api/v1/api-tokens?status=all&page=2").json()
        assert (len(second["items"]), second["has_more"], second["page"]) == (2, False, 2)
        assert client.get("/api/v1/api-tokens?status=active").json()["total"] == 10
        assert client.get("/api/v1/api-tokens?status=expired").json()["total"] == 1
        assert client.get("/api/v1/api-tokens?status=revoked").json()["total"] == 1
        inactive = client.get("/api/v1/api-tokens?status=inactive").json()
        assert (inactive["total"], inactive["has_more"]) == (2, False)
        assert {item["status"] for item in inactive["items"]} == {"revoked", "expired"}

        with transaction() as db:
            for token in db.scalars(select(PersonalApiToken).where(
                    PersonalApiToken.user_id == user.id, PersonalApiToken.revoked_at.is_(None),
                    PersonalApiToken.expires_at > now()).limit(9)):
                token.expires_at = now() - timedelta(seconds=1)
        first = client.get("/api/v1/api-tokens?status=inactive").json()
        second = client.get("/api/v1/api-tokens?status=inactive&page=2").json()
        assert (first["total"], len(first["items"]), first["has_more"], first["active_count"]) == (11, 10, True, 1)
        assert (len(second["items"]), second["has_more"], second["page"]) == (1, False, 2)
        assert len({item["id"] for item in first["items"] + second["items"]}) == 11
        assert all(item["status"] != "active" for item in first["items"] + second["items"])


def test_create_rejects_non_integer_repository_ids(fresh_session, client):
    client.cookies.set("cm_session", "fresh")
    client.headers["x-csrf-token"] = "csrf"
    for bad in (["22"], [22.0], [True], [0], [-3], [22, 22]):
        response = client.post("/api/v1/api-tokens", json={
            "name": "bad", "scopes": ["repositories:read"],
            "repository_ids": bad, "expires_in_days": 30})
        assert response.status_code == 422, bad


def test_create_rejects_future_session(fresh_session, client):
    with transaction() as db:
        session = db.get(LoginSession, service.digest("fresh"))
        session.authenticated_at = now() + timedelta(hours=1)
    client.cookies.set("cm_session", "fresh")
    client.headers["x-csrf-token"] = "csrf"
    assert client.post("/api/v1/api-tokens", json={
        "name": "future", "scopes": ["repositories:read"],
        "repository_ids": [22], "expires_in_days": 30}).status_code == 403
    assert client.get("/api/v1/api-tokens").json()["reauth_required"] is True


def test_create_refuses_removed_member(seed, client):
    with transaction() as db:
        db.add(User(id=11, login="ghost", role="member", enabled=False))
        db.flush()
        db.add(LoginSession(token_hash=digest("ghost"), user_id=11, csrf="csrf",
                            expires_at=now() + timedelta(days=1), authenticated_at=now()))
    client.cookies.set("cm_session", "ghost")
    client.headers["x-csrf-token"] = "csrf"
    assert client.post("/api/v1/api-tokens", json={
        "name": "ghost", "scopes": ["repositories:read"],
        "repository_ids": [22], "expires_in_days": 30}).status_code == 403


def test_bearer_refused_over_remote_cleartext(pat_client):
    client, raw, _ = pat_client
    headers = {"Authorization": f"Bearer {raw}"}
    with TestClient(app, base_url="http://remote.test") as remote:
        assert remote.get("/api/v1/repositories", headers=headers).status_code == 401
        spoofed = dict(headers, **{"X-Forwarded-Proto": "https", "X-Forwarded-For": "1.2.3.4"})
        assert remote.get("/api/v1/repositories", headers=spoofed).status_code == 401


def test_local_cleartext_requires_local_configuration(pat_client, monkeypatch):
    from code_mortis.core.config import settings

    client, raw, _ = pat_client
    monkeypatch.setattr(settings(), "public_url", "https://mortis.example.com")
    assert client.get("/api/v1/repositories", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_https_termination_requires_trusted_proxy(pat_client, monkeypatch):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from code_mortis.core.config import settings

    _, raw, _ = pat_client
    monkeypatch.setattr(settings(), "public_url", "https://mortis.example.com")
    monkeypatch.setattr(settings(), "cookie_secure", True)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=["10.20.30.40"])
    headers = {"Authorization": f"Bearer {raw}", "X-Forwarded-Proto": "https"}
    with TestClient(wrapped, base_url="http://mortis.example.com", client=("10.20.30.40", 1234)) as trusted:
        assert trusted.get("/api/v1/repositories", headers=headers).status_code == 200
    with TestClient(wrapped, base_url="http://mortis.example.com", client=("10.20.30.41", 1234)) as untrusted:
        assert untrusted.get("/api/v1/repositories", headers=headers).status_code == 401


def test_session_refuses_empty_authorization(fresh_session, client):
    assert client.get("/api/v1/auth/me", headers={"Authorization": ""}).status_code == 401


def test_authorize_refreshes_token_after_external_check(fresh_session, monkeypatch):
    with transaction() as db:
        db.add(PersonalApiToken(id="racing", user_id=fresh_session["user"].id, name="racing",
                                token_hash=digest("racing"), prefix="cm_pat_test",
                                scopes=["reviews:run"], repository_ids=[22],
                                expires_at=now() + timedelta(days=1)))

    def revoked_during_check(*args, **kwargs):
        with transaction() as other:
            other.get(PersonalApiToken, "racing").revoked_at = now()
        return {"permissions": {"push": True}}

    from code_mortis.integrations import github

    monkeypatch.setattr(github, "authorize_repo", revoked_during_check)
    with transaction() as db:
        with pytest.raises(HTTPException) as refused:
            service.authorize(db, "racing", scope="reviews:run", repository_id=22, write=True, lock=True)
        assert refused.value.status_code == 401


def test_revoke_refreshes_stale_token(fresh_session):
    from code_mortis.models import ApiTokenEvent, Job

    with transaction() as db:
        db.add(PersonalApiToken(id="stale-revoke", user_id=fresh_session["user"].id, name="revoke",
                                token_hash=digest("stale-revoke"), prefix="cm_pat_test",
                                scopes=["reviews:read"], repository_ids=[22],
                                expires_at=now() + timedelta(days=1)))
    with transaction() as db:
        stale = db.get(PersonalApiToken, "stale-revoke")
        with transaction() as other:
            service.revoke(other, other.get(PersonalApiToken, stale.id))
        service.revoke(db, stale)
        assert len(db.scalars(select(ApiTokenEvent).where(ApiTokenEvent.token_id == stale.id)).all()) == 1
        assert len(db.scalars(select(Job).where(Job.kind == service.REVOKE_CLEANUP_KIND)).all()) == 1


def test_concurrent_creation_cannot_exceed_active_cap(pat_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import func

    _, _, token_id = pat_client
    with transaction() as db:
        owner_id = db.get(PersonalApiToken, token_id).user_id
        for index in range(policy.MAX_ACTIVE_TOKENS - 2):
            db.add(PersonalApiToken(user_id=owner_id, name=f"capacity-{index}",
                                    token_hash=digest(f"capacity-{index}"), prefix="cm_pat_test",
                                    scopes=["reviews:read"], repository_ids=[22],
                                    expires_at=now() + timedelta(days=1)))
    barrier = Barrier(2)

    def issue(index):
        try:
            with transaction() as db:
                user = db.get(User, owner_id)
                barrier.wait(timeout=5)
                service.create_token(db, user, name=f"racer-{index}", scopes=["reviews:read"],
                                     repository_ids=[22], expires_in_days=30)
            return 201
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(issue, range(2))) == [201, 409]
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(PersonalApiToken).where(
            PersonalApiToken.user_id == owner_id, PersonalApiToken.revoked_at.is_(None),
            PersonalApiToken.expires_at > now())) == policy.MAX_ACTIVE_TOKENS


def test_unmapped_routes_deny_pat(pat_client):
    client, raw, _ = pat_client
    headers = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v1/repositories-evil", headers=headers).status_code == 404
    assert client.get("/api/v1/repositories/22/evil", headers=headers).status_code == 404
    assert policy.pat_scope("GET", "/api/v1/repositories2") is None
    assert policy.pat_scope("GET", "/api/v1/repositories/") is None


def test_failed_auth_bucket_limits_guessing(pat_client, monkeypatch):
    import time as stdlib_time

    from redis import Redis

    from code_mortis.core.config import settings

    with Redis.from_url(settings().redis_url) as cache:
        for key in cache.scan_iter("code-mortis:pat:failed:*"):
            cache.delete(key)
    real_time = stdlib_time.time
    monkeypatch.setattr(stdlib_time, "time", lambda: 9_999_999_999.0)
    try:
        client, _, _ = pat_client
        forged = {"Authorization": f"Bearer cm_pat_{'D' * 43}"}
        statuses = [client.get("/api/v1/repositories", headers=forged).status_code for _ in range(21)]
    finally:
        monkeypatch.setattr(stdlib_time, "time", real_time)
    assert statuses[-1] == 429
    assert statuses[0] == 401


def test_worker_authorization_does_not_dirty_token(fresh_session):
    with transaction() as db:
        db.add(PersonalApiToken(id="locked", user_id=fresh_session["user"].id, name="l",
                                token_hash=digest("locked"), prefix="cm_pat_l",
                                scopes=["reviews:read"], repository_ids=[22],
                                created_at=now(), expires_at=now() + timedelta(days=1)))
    with transaction() as db:
        user, token = service.authorize(db, "locked", scope="reviews:read", lock=True)
        assert token.last_used_at is None and user.id == fresh_session["user"].id
        assert not db.is_modified(token)


def test_last_use_commits_before_handler_network_work(pat_client, monkeypatch):
    from sqlalchemy import text

    from code_mortis.integrations import github

    client, raw, token_id = pat_client

    def revoke_while_discovering(db, user):
        with transaction() as other:
            other.execute(text("SET LOCAL lock_timeout='1s'"))
            token = other.get(PersonalApiToken, token_id)
            assert token.last_used_at is not None
            service.revoke(other, token)
        return []

    monkeypatch.setattr(github, "visible_repositories", revoke_while_discovering)
    headers = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v1/repositories", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/token", headers=headers).status_code == 401
