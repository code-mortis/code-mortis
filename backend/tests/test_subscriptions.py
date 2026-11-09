from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from code_mortis.core.db import now, transaction
from code_mortis.core.security import digest, seal
from code_mortis.integrations import github, llm
from code_mortis.models import (
    Connection,
    ConnectionGrant,
    ConsoleTicket,
    Installation,
    Membership,
    OAuthState,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.reviews import service as reviews


def personal(client, kind="chatgpt"):
    with transaction() as db:
        connection = Connection(name="Historical personal account", kind=kind, owner_id=7,
                                provider="openai", protocol="native", model="review-model",
                                base_url="", secret=seal("{}"))
        db.add(connection)
        db.flush()
        return connection.id


def test_two_login_tabs_pkce_replay_expiration_and_browser_binding(seed, client, monkeypatch):
    monkeypatch.setattr(github, "exchange_code", lambda *a: {"access_token": "token"})
    monkeypatch.setattr(github, "request", lambda *a, **k: {"id": 7, "login": "owner", "avatar_url": ""})
    client.get("/api/v1/setup/status")
    urls = [client.get("/api/v1/auth/login", follow_redirects=False).headers["location"] for _ in range(2)]
    states = [parse_qs(urlsplit(url).query)["state"][0] for url in urls]
    assert states[0] != states[1] and all("code_challenge_method=S256" in url for url in urls)
    for state in states:
        response = client.get(f"/api/v1/auth/callback?code=valid&state={state}", follow_redirects=False)
        assert response.headers["location"] == "http://testserver/"
    response = client.get(f"/api/v1/auth/callback?code=valid&state={states[0]}", follow_redirects=False)
    assert "auth_error=expired" in response.headers["location"] and response.headers["x-request-id"]
    url = client.get("/api/v1/auth/login", follow_redirects=False).headers["location"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    with transaction() as db:
        for attempt in db.scalars(select(OAuthState)):
            attempt.expires_at = now() - timedelta(seconds=1)
    assert (
        "auth_error=expired"
        in client.get(f"/api/v1/auth/callback?code=valid&state={state}", follow_redirects=False).headers[
            "location"
        ]
    )


def test_installation_flow_checks_current_access_and_external_login(seed, client, monkeypatch):
    monkeypatch.setattr(github, "user_token", lambda *a: "test-user-token")
    available = [{"id": 90, "app_id": 123, "account": {"id": 77, "login": "team", "type": "Organization"},
                  "repository_selection": "selected", "suspended_at": None}]
    monkeypatch.setattr(github, "pages", lambda *a: available)
    client.get("/api/v1/setup/status")
    location = client.get("/api/v1/installations/start", follow_redirects=False).headers["location"]
    state = parse_qs(urlsplit(location).query)["state"][0]
    assert (
        "auth_error=installation_denied"
        in client.get(
            f"/api/v1/installations/callback?installation_id=91&state={state}", follow_redirects=False
        ).headers["location"]
    )
    with transaction() as db:
        assert db.get(Installation, 91) is None
    # A direct GitHub installation is accepted only after current access is verified.
    assert (
        "installed=1"
        in client.get("/api/v1/installations/callback?installation_id=90", follow_redirects=False).headers[
            "location"
        ]
    )
    available.clear()
    assert (
        "installation_denied"
        in client.get("/api/v1/installations/callback?installation_id=90", follow_redirects=False).headers[
            "location"
        ]
    )
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert (
        "installation_admin"
        in client.get("/api/v1/installations/callback?installation_id=90", follow_redirects=False).headers[
            "location"
        ]
    )
    client.cookies.clear()
    response = client.get("/api/v1/installations/callback?installation_id=90", follow_redirects=False)
    assert response.headers["location"] == "http://testserver/api/v1/auth/login?installation_id=90"


def test_member_cannot_create_subscription_or_edit_shared_api(seed, client):
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert client.post("/api/v1/connections", json={"name": "Personal", "kind": "chatgpt"}).status_code == 422
    assert client.post("/api/v1/connections/connection/test").status_code == 403
    identity = personal(client)
    assert all(row["id"] != identity for row in client.get("/api/v1/connections").json())


def test_other_users_cannot_authenticate_or_consume_personal_connection(seed, client, monkeypatch):
    identity = personal(client)
    with transaction() as db:
        db.add(User(id=8, login="other", access_token=seal("other-token")))
        db.add(Membership(github_id=8, login="other"))
        db.flush()
        db.get(Connection, identity).owner_id = 8
    assert not any(row["id"] == identity for row in client.get("/api/v1/connections").json())
    assert client.post(f"/api/v1/connections/{identity}/auth").status_code == 403
    assert client.post(f"/api/v1/connections/{identity}/console-ticket").status_code == 403
    assert client.delete(f"/api/v1/connections/{identity}/auth").status_code == 403
    assert client.post(f"/api/v1/connections/{identity}/disconnect").status_code == 403
    assert (
        client.put(f"/api/v1/connections/{identity}/grants/22", json={"automatic": True}).status_code == 403
    )


def test_owner_consent_automation_and_revocation(seed, client, github_stub, monkeypatch):
    from code_mortis.models import PullRequest

    identity = personal(client)
    assert client.put("/api/v1/repositories/22", json={"enabled": True, "connection_id": identity}).status_code == 422
    assert client.put(f"/api/v1/connections/{identity}/grants/22", json={"automatic": True}).status_code == 410
    with transaction() as db:
        db.add(ConnectionGrant(connection_id=identity, repository_id=22, automatic=True))
        repo = db.get(Repository, 22)
        repo.connection_id = identity
        with pytest.raises(ValueError, match="retired"):
            reviews.create_run(db, db.get(PullRequest, "pr"), repo, "automatic")
    assert client.delete(f"/api/v1/connections/{identity}/grants/22").status_code == 200
    with transaction() as db:
        assert db.get(ConnectionGrant, (identity, 22)) is None
        assert not list(db.scalars(select(ReviewRun)))


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("operation", ["cancel_auth", "disconnect"])
def test_retired_auth_revocation_is_local_and_preserves_history(seed, client, monkeypatch, kind, operation):
    identity = personal(client, kind)
    report = {"message": "Saved historical result"}
    with transaction() as db:
        db.add(ConnectionGrant(connection_id=identity, repository_id=22, automatic=True))
        db.add(ConsoleTicket(token_hash=digest("historical-ticket"), connection_id=identity,
                             session_hash=digest("session"), expires_at=now() + timedelta(minutes=1)))
        db.add(ReviewRun(id="historical", pr_id="pr", connection_id=identity, engine=kind,
                         trigger="manual", dedup_key="historical", base_sha="b" * 40, head_sha="a" * 40,
                         config={"connection": {"id": identity, "kind": kind}}, status="completed",
                         publication="published", report=report))
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("Local revocation must not call GitHub"))
    monkeypatch.setattr(llm, "http_client", lambda: pytest.fail("Local revocation must not call a provider"))
    response = (client.delete(f"/api/v1/connections/{identity}/auth") if operation == "cancel_auth"
                else client.post(f"/api/v1/connections/{identity}/disconnect"))
    assert response.status_code == 200
    assert client.get(f"/api/v1/connections/{identity}/auth").status_code == 410
    with transaction() as db:
        assert db.get(Connection, identity).auth_status == "revoked"
        assert db.scalar(select(ConsoleTicket)) is None
        assert bool(db.get(ConnectionGrant, (identity, 22))) is (operation == "cancel_auth")
        run = db.get(ReviewRun, "historical")
        assert (run.status, run.publication, run.engine, run.report) == ("completed", "published", kind, report)


def test_retired_connection_cannot_review_or_replace_shared_default(seed, client, github_stub, monkeypatch):
    from code_mortis.models import PullRequest

    identity = personal(client)
    monkeypatch.setattr(llm, "http_client", lambda: pytest.fail("Retired connection must not call a provider"))
    with transaction() as db:
        with pytest.raises(ValueError, match="retired"):
            reviews.create_run(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "manual", db.get(User, 7), identity)
        assert db.get(Repository, 22).connection_id == "connection"
        assert not list(db.scalars(select(ReviewRun)))


def test_retired_console_rejects_origin_and_replay(seed, client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    identity = personal(client, "claude_code")
    assert client.post(f"/api/v1/connections/{identity}/console-ticket").status_code == 410
    for origin in ("https://untrusted.example", "http://testserver"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/v1/connections/{identity}/console", headers={"origin": origin}):
                pass
    with transaction() as db:
        assert db.scalar(select(ConsoleTicket)) is None
