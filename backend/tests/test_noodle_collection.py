"""Personal API token collection contract and Noodle HTTP checks.

The runnable collection under ``collections/code-mortis`` carries bearer
requests for PAT-supported operations only. Session-only operations are
documented as browser workflows in the collection README instead of runnable
YAML, with no cookie or CSRF fallback.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread

import httpx
import pytest
import uvicorn
import yaml
from jsonschema import Draft202012Validator
from sqlalchemy import select

from code_mortis.core.db import now, transaction, uid
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.main import app
from code_mortis.models import Connection, LoginSession, PersonalApiToken, Repository

policy = pytest.importorskip("code_mortis.api_tokens.policy")

COLLECTION = Path(__file__).resolve().parents[2] / "collections" / "code-mortis"

# Exact PAT inventory from docs/api-token-route-policy.md, including additions.
# GET /api/v1/auth/token is asserted separately because it carries
# no permission scope, only a valid PAT.
EXPECTED_PAT_SCOPES = {
    ("GET", "/api/v1/repositories"): "repositories:read",
    ("GET", "/api/v1/repositories/{repository_id}/pulls"): "reviews:read",
    ("POST", "/api/v1/repositories/{repository_id}/sync"): "reviews:run",
    ("GET", "/api/v1/analytics/overview"): "analytics:read",
    ("GET", "/api/v1/analytics/findings"): "analytics:read",
    ("GET", "/api/v1/analytics/reviews"): "analytics:read",
    ("GET", "/api/v1/analytics/usage"): "analytics:read",
    ("GET", "/api/v1/analytics/security"): "analytics:read",
    ("GET", "/api/v1/pulls"): "reviews:read",
    ("GET", "/api/v1/pulls/{pr_id}"): "reviews:read",
    ("PUT", "/api/v1/pulls/{pr_id}/ignore"): "reviews:write",
    ("POST", "/api/v1/pulls/{pr_id}/cancel"): "reviews:run",
    ("GET", "/api/v1/pulls/{pr_id}/runs"): "reviews:read",
    ("POST", "/api/v1/pulls/{pr_id}/runs"): "reviews:run",
    ("GET", "/api/v1/pulls/{pr_id}/findings"): "reviews:read",
    ("POST", "/api/v1/pulls/{pr_id}/summary"): "reviews:write",
    ("GET", "/api/v1/runs/{run_id}"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/findings"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/findings/{finding_id}"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/resolved"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/candidates"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}"): "reviews:read",
    ("POST", "/api/v1/runs/{run_id}/candidates/{candidate_id}/retry"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/cancel"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/retry"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/publish"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/summary/retry"): "reviews:run",
    ("GET", "/api/v1/findings/{finding_id}"): "reviews:read",
    ("PATCH", "/api/v1/findings/{finding_id}"): "reviews:write",
    ("POST", "/api/v1/findings/{finding_id}/sync"): "reviews:write",
    ("GET", "/api/v1/providers"): "connections:read",
    ("GET", "/api/v1/connections"): "connections:read",
    ("GET", "/api/v1/connections/{connection_id}/models"): "connections:read",
    ("GET", "/api/v1/security/repositories"): "security:read",
    ("GET", "/api/v1/security/repositories/{repository_id}"): "security:read",
    ("GET", "/api/v1/security/repositories/{repository_id}/scans"): "security:read",
    ("POST", "/api/v1/security/repositories/{repository_id}/scans"): "security:run",
    ("GET", "/api/v1/security/scans/{scan_id}"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/activity"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/coverage/{section}"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/assessments"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/assessments/{assessment_id}"): "security:read",
    ("POST", "/api/v1/security/scans/{scan_id}/cancel"): "security:run",
    ("POST", "/api/v1/security/scans/{scan_id}/retry"): "security:run",
    ("GET", "/api/v1/security/scans/{scan_id}/report"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/report/findings"): "security:read",
    ("GET", "/api/v1/security/repositories/{repository_id}/findings"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/findings"): "security:read",
    ("GET", "/api/v1/security/findings/{finding_id}"): "security:read",
    ("PATCH", "/api/v1/security/findings/{finding_id}"): "security:write",
    ("GET", "/api/v1/security/findings/{finding_id}/activity"): "security:read",
    ("GET", "/api/v1/security/findings/{finding_id}/observations"): "security:read",
    ("POST", "/api/v1/security/findings/{finding_id}/rotation"): "security:write",
}

EXPECTED_PUBLIC = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/v1/setup/status"),
}

# Session-only, browser-flow, bootstrap, webhook, and OAuth/MCP operations from
# docs/api-token-route-policy.md. None may have runnable collection YAML.
EXPECTED_SESSION_ONLY = {
    ("GET", "/api/v1/activity"),
    ("POST", "/api/v1/activity/read"),
    ("GET", "/api/v1/pulls/{pr_id}/overview"),
    ("GET", "/api/v1/pulls/{pr_id}/code"),
    ("POST", "/api/v1/pulls/{pr_id}/code/file"),
    ("POST", "/api/v1/pulls/{pr_id}/code/workspace"),
    ("GET", "/api/v1/pulls/{pr_id}/code-graph"),
    ("POST", "/api/v1/pulls/{pr_id}/code-graph/query"),
    ("POST", "/api/v1/pulls/{pr_id}/code-graph/index"),
    ("GET", "/api/v1/runs/{run_id}/summary/attempts/{job_id}"),
    ("POST", "/api/v1/setup/manifest"),
    ("GET", "/api/v1/setup/callback"),
    ("GET", "/api/v1/auth/login"),
    ("GET", "/api/v1/auth/callback"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/logout"),
    ("PUT", "/api/v1/repositories/{repository_id}"),
    ("PUT", "/api/v1/repositories/{repository_id}/review-rules"),
    ("GET", "/api/v1/repositories/{repository_id}/review-rules/files"),
    ("POST", "/api/v1/repositories/{repository_id}/review-rules/preview"),
    ("GET", "/api/v1/repositories/{repository_id}/activity"),
    ("GET", "/api/v1/repositories/{repository_id}/code-graph"),
    ("GET", "/api/v1/repositories/{repository_id}/code-graph/view"),
    ("GET", "/api/v1/repositories/{repository_id}/code-graph/files"),
    ("GET", "/api/v1/repositories/{repository_id}/code-graph/source"),
    ("POST", "/api/v1/repositories/{repository_id}/code-graph/retry"),
    ("POST", "/api/v1/repositories/{repository_id}/code-graph/cancel"),
    ("POST", "/api/v1/repositories/{repository_id}/background"),
    ("POST", "/api/v1/repositories/{repository_id}/background/cancel"),
    ("GET", "/api/v1/repositories/{repository_id}/background/{job_id}"),
    ("POST", "/api/v1/connections"),
    ("POST", "/api/v1/connections/models"),
    ("PUT", "/api/v1/connections/{connection_id}"),
    ("DELETE", "/api/v1/connections/{connection_id}"),
    ("POST", "/api/v1/connections/{connection_id}/test"),
    ("POST", "/api/v1/connections/{identity}/auth"),
    ("GET", "/api/v1/connections/{identity}/auth"),
    ("DELETE", "/api/v1/connections/{identity}/auth"),
    ("POST", "/api/v1/connections/{identity}/disconnect"),
    ("GET", "/api/v1/connections/{identity}/grants"),
    ("PUT", "/api/v1/connections/{identity}/grants/{repository_id}"),
    ("DELETE", "/api/v1/connections/{identity}/grants/{repository_id}"),
    ("POST", "/api/v1/connections/{identity}/console-ticket"),
    ("GET", "/api/v1/settings"),
    ("GET", "/api/v1/installations"),
    ("DELETE", "/api/v1/installations/{installation_id}"),
    ("POST", "/api/v1/installations/{installation_id}"),
    ("DELETE", "/api/v1/installations/{installation_id}/local"),
    ("GET", "/api/v1/installations/start"),
    ("GET", "/api/v1/installations/callback"),
    ("GET", "/api/v1/installations/available"),
    ("POST", "/api/v1/members"),
    ("DELETE", "/api/v1/members/{user_id}"),
    ("POST", "/api/v1/webhooks/github"),
    ("GET", "/api/v1/security/repositories/{repository_id}/github-issues"),
    ("POST", "/api/v1/security/repositories/{repository_id}/github-issues"),
    ("GET", "/api/v1/security/findings/{finding_id}/github-issue"),
    ("POST", "/api/v1/security/findings/{finding_id}/github-issue"),
    ("POST", "/api/v1/security/findings/{finding_id}/github-issue/sync"),
    ("POST", "/api/v1/security/findings/{finding_id}/github-issue/reapprove"),
    ("GET", "/.well-known/oauth-authorization-server"),
    ("GET", "/.well-known/oauth-protected-resource/mcp/{domain}"),
    ("POST", "/oauth/register"),
    ("GET", "/oauth/authorize"),
    ("POST", "/oauth/token"),
    ("POST", "/oauth/revoke"),
    ("GET", "/api/v1/mcp/authorize"),
    ("POST", "/api/v1/mcp/authorize"),
    ("GET", "/api/v1/mcp/settings"),
    ("PUT", "/api/v1/mcp/settings"),
    ("GET", "/api/v1/mcp/clients"),
    ("POST", "/api/v1/mcp/clients"),
    ("DELETE", "/api/v1/mcp/clients/{client_id}"),
    ("GET", "/api/v1/mcp/grants"),
    ("DELETE", "/api/v1/mcp/grants/{grant_id}"),
    ("GET", "/api/v1/mcp/approvals/{approval_id}"),
    ("POST", "/api/v1/mcp/approvals/{approval_id}"),
    ("GET", "/api/v1/api-tokens"),
    ("POST", "/api/v1/api-tokens"),
    ("DELETE", "/api/v1/api-tokens/{token_id}"),
}

TOKEN_IDENTITY = ("GET", "/api/v1/auth/token")


def _request_files():
    return [
        file for file in COLLECTION.rglob("*.yml")
        if file.name not in {"folder.yml", "settings.yml"}
        and ".noodle" not in file.relative_to(COLLECTION).parts
        and ".timeline" not in file.relative_to(COLLECTION).parts
    ]


def _operation_key(request):
    path = re.sub(r":(\w+)", r"{\1}", request["url"].removeprefix("$base_url"))
    return request["method"].upper(), path


def _normalize(template):
    return re.sub(r":\w+}", "}", template)


def test_noodle_requests_match_pat_policy():
    assert policy.AUTH_TOKEN_ROUTE == TOKEN_IDENTITY
    implemented = dict(policy.PAT_ROUTE_SCOPES)
    assert TOKEN_IDENTITY not in implemented
    assert implemented == EXPECTED_PAT_SCOPES
    assert set(policy.SCOPES) >= set(EXPECTED_PAT_SCOPES.values())
    classified = {(method, _normalize(template)) for method, template in
                  set(policy.SESSION_ONLY_ROUTES) | set(policy.SPECIAL_ROUTES) | set(policy.PUBLIC_ROUTES)}
    assert classified == EXPECTED_SESSION_ONLY | EXPECTED_PUBLIC

    schema = app.openapi()
    for method, template in list(implemented) + [TOKEN_IDENTITY]:
        assert template in schema["paths"], template
        assert method.lower() in schema["paths"][template], (method, template)

    variables = dict(
        line.split("=", 1) for line in (COLLECTION / ".environments/development.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    seen_api = set()
    seen_public = set()
    smoke = set()
    for file in _request_files():
        request = yaml.safe_load(file.read_text())
        key = _operation_key(request)
        relative = file.relative_to(COLLECTION).as_posix()
        if relative.startswith("api/"):
            assert key in implemented or key == TOKEN_IDENTITY, relative
            assert request.get("auth", {}).get("type") == "inherit", relative
            assert key not in seen_api, relative
            seen_api.add(key)
        elif relative.startswith("public/"):
            assert key in EXPECTED_PUBLIC, relative
            assert "auth" not in request, relative
            seen_public.add(key)
        else:
            pytest.fail(f"request outside api/ or public/: {relative}")
        assert key not in EXPECTED_SESSION_ONLY, relative
        assert str(request["assert"][0]["value"]) in schema["paths"][key[1]][key[0].lower()]["responses"]
        if "smoke" in request["tags"]:
            assert request["method"].lower() == "get" and not request.get("path_params"), relative
            smoke.add(key)
        operation = schema["paths"][key[1]][key[0].lower()]
        for parameter in operation.get("parameters", []):
            if parameter["in"] == "path":
                value = next(p["value"] for p in request["path_params"] if p["name"] == parameter["name"])
                if "enum" in parameter["schema"]:
                    assert variables[value.removeprefix("$")] in parameter["schema"]["enum"]
        if "body" in request:
            body = request["body"].replace("$random.uuid", "00000000-0000-4000-8000-000000000000")
            body = re.sub(r"\$(\w+)", lambda match: variables[match[1]], body)
            body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
            Draft202012Validator({"components": schema["components"], **body_schema}).validate(json.loads(body))

    assert seen_api == set(implemented) | {TOKEN_IDENTITY}
    assert seen_public == EXPECTED_PUBLIC
    assert len(smoke) == 14

    readme = (COLLECTION / "README.md").read_text()
    for marker in ("Browser-only workflows", "/api/v1/auth/me", "/api/v1/api-tokens", "cm_api_token"):
        assert marker in readme, marker

    # Bounded response history, no cookie jar or cookie/CSRF sourcing.
    settings = yaml.safe_load((COLLECTION / "settings.yml").read_text())
    assert settings["cookies"]["enabled"] is False
    assert settings["timeline_max_entries"] == 50
    folder = yaml.safe_load((COLLECTION / "api/folder.yml").read_text())
    assert folder["auth"] == {"type": "bearer", "token": "$cm_api_token"}
    guard = (COLLECTION / "scripts/auth.js").read_text().lower()
    assert "cookie" not in guard and "csrf" not in guard and "cm_session" not in guard
    for env_name in ("development.env", "server.env"):
        env_text = (COLLECTION / ".environments" / env_name).read_text()
        assert "# @secret cm_api_token" in env_text
        assert "cm_session" not in env_text


READ_SCOPES = ["repositories:read", "reviews:read", "analytics:read", "connections:read", "security:read"]


@pytest.mark.skipif(not shutil.which("noodle"), reason="Install Noodle to run its HTTP integration check")
def test_noodle_pat_over_http(seed, github_stub, monkeypatch, tmp_path):
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository))))
    # Fresh GitHub callback is an issuance safeguard set only by the real callback;
    # the test seed sets it explicitly so issuance can proceed without re-login.
    with transaction() as db:
        db.get(LoginSession, digest("session")).authenticated_at = now()
        db.add(Repository(id=23, installation_id=10, full_name="team/other",
                          connection_id=db.get(Connection, "connection").id, enabled=True))
    collection = tmp_path / "collection"
    shutil.copytree(COLLECTION, collection)
    settings_file = collection / "settings.yml"
    collection_settings = yaml.safe_load(settings_file.read_text())
    collection_settings["collection_id"] = uid()
    settings_file.write_text(yaml.safe_dump(collection_settings))
    env_file = collection / ".environments/development.env"
    env_file.write_text(env_file.read_text().replace("http://localhost:5173", "PLACEHOLDER").replace(
        "pr_id=REPLACE_ME", "pr_id=pr"))

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env_file.write_text(env_file.read_text().replace("PLACEHOLDER", base_url))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                Event().wait(0.01)
            assert server.started
            browser = httpx.Client(base_url=base_url, cookies={"cm_session": "session"},
                                   headers={"x-csrf-token": "csrf"}, timeout=10)

            def issue(name, scopes, repository_ids=(22,)):
                response = browser.post("/api/v1/api-tokens", json={
                    "name": name, "scopes": scopes, "repository_ids": list(repository_ids),
                    "expires_in_days": 30,
                })
                assert response.status_code == 201, response.text
                created = response.json()
                assert created["item"]["name"] == name
                return created["token"], created["item"]["id"]

            def run_noodle(*args, token=None):
                env = {key: value for key, value in os.environ.items() if key != "cm_api_token"}
                if token is not None:
                    env["cm_api_token"] = token
                result = subprocess.run(
                    ["noodle", *args, *(["--collection", str(collection)] if args[0] == "request" else []),
                     "--env", "development", "--json"],
                    env=env, capture_output=True, text=True, timeout=30,
                )
                return result.returncode, json.loads(result.stdout)

            def bearer(token):
                return httpx.Client(base_url=base_url,
                                    headers={"Authorization": f"Bearer {token}"}, timeout=10)

            token, _token_id = issue("noodle-http", READ_SCOPES + ["reviews:write"])

            # Bearer identity read through the real Noodle binary.
            code, result = run_noodle("request", "run", "api/auth/token", token=token)
            assert code == 0, result
            assert result["data"]["result"]["response"]["status"] == 200
            identity = bearer(token).get("/api/v1/auth/token")
            assert identity.status_code == 200, identity.text
            assert identity.json()["user"]["login"] == "owner"
            assert set(identity.json()["token"]["scopes"]) == set(READ_SCOPES + ["reviews:write"])

            # Read-only smoke suite without cookies or CSRF.
            code, result = run_noodle("collection", "run", str(collection), "--tag", "smoke", "--fail-fast",
                                      token=token)
            assert code == 0, result
            assert result["data"]["summary"]["requestSuccesses"] == 14

            # Reversible write: the seeded PR is not ignored, so this is a no-op write.
            code, result = run_noodle("request", "run", "api/pulls/ignore-pull", token=token)
            assert code == 0, result
            assert result["data"]["result"]["response"]["status"] == 200

            # No token: the guard fails before sending, proving no cookie fallback exists.
            code, result = run_noodle("request", "run", "api/auth/token")
            assert code == 1 and result["data"]["result"].get("response") is None

            # Invalid and oversized credentials are rejected without lookup.
            code, result = run_noodle("request", "run", "api/auth/token", token="cm_pat_" + "A" * 43)
            assert code == 1 and result["data"]["result"]["response"]["status"] == 401
            code, result = run_noodle("request", "run", "api/auth/token", token="cm_pat_" + "A" * 200)
            assert code == 1 and result["data"]["result"]["response"]["status"] == 401

            # Missing scope: a repositories-only token cannot read the PR inbox.
            narrow_token, _ = issue("noodle-narrow", ["repositories:read"])
            denied = bearer(narrow_token).get("/api/v1/pulls")
            assert denied.status_code == 403, denied.text

            # Missing repository: the main token selects repository 22, not 23.
            hidden = bearer(token).get("/api/v1/repositories/23/pulls")
            assert hidden.status_code in (403, 404), hidden.text

            # Expired tokens fail closed.
            with transaction() as db:
                stored = db.scalar(select(PersonalApiToken).where(
                    PersonalApiToken.token_hash == digest(narrow_token)))
                stored.expires_at = now() - timedelta(minutes=1)
            assert bearer(narrow_token).get("/api/v1/auth/token").status_code == 401

            # Revocation through the browser commits, then the bearer is refused.
            revoked_token, revoked_id = issue("noodle-revoked", READ_SCOPES)
            response = browser.delete(f"/api/v1/api-tokens/{revoked_id}")
            assert response.status_code == 202, response.text
            assert response.json() == {"revoked": True}
            code, result = run_noodle("request", "run", "api/auth/token", token=revoked_token)
            assert code == 1 and result["data"]["result"]["response"]["status"] == 401

            # Session-only routes reject bearer credentials.
            assert bearer(token).get("/api/v1/auth/me").status_code in (401, 403)
            assert bearer(token).get("/api/v1/settings").status_code in (401, 403)
            assert bearer(token).post("/api/v1/api-tokens", json={
                "name": "noodle-escalation", "scopes": READ_SCOPES,
                "repository_ids": [22], "expires_in_days": 30}).status_code in (401, 403)

            # Mixed cookie and bearer credentials are rejected instead of merged.
            mixed = httpx.Client(base_url=base_url, cookies={"cm_session": "session"},
                                 headers={"Authorization": f"Bearer {token}"}, timeout=10)
            assert mixed.get("/api/v1/pulls").status_code in (400, 401, 403, 422)

            # A missing bearer still gets a Bearer challenge on protected routes.
            anonymous = httpx.Client(base_url=base_url, timeout=10)
            challenge = anonymous.get("/api/v1/pulls")
            assert challenge.status_code == 401
            assert "bearer" in challenge.headers.get("www-authenticate", "").lower()
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive()


def test_browser_cookie_csrf_regression(seed, github_stub, client):
    response = client.put("/api/v1/pulls/pr/ignore", json={"ignored": False})
    assert response.status_code == 200
    client.headers.pop("x-csrf-token", None)
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": False}).status_code != 200
    client.headers["x-csrf-token"] = "wrong-csrf"
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": False}).status_code != 200
    client.cookies.clear()
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": False}).status_code == 401
