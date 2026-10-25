import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from threading import Event, Thread

import pytest
import uvicorn
import yaml
from jsonschema import Draft202012Validator
from sqlalchemy import select

from code_mortis.core.db import transaction
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.main import app
from code_mortis.models import LoginSession, Repository

COLLECTION = Path(__file__).resolve().parents[2] / "collections" / "code-mortis"


def test_noodle_requests_match_api_contract():
    schema = app.openapi()
    variables = dict(
        line.split("=", 1) for line in (COLLECTION / ".environments/development.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    variables.update(cm_session="test-session", provider_api_key="test-key", member_login="teammate")
    seen = set()
    for file in COLLECTION.rglob("*.yml"):
        if file.name in {"folder.yml", "settings.yml"} or ".noodle" in file.relative_to(COLLECTION).parts:
            continue
        request = yaml.safe_load(file.read_text())
        path = re.sub(r":(\w+)", r"{\1}", request["url"].removeprefix("$base_url"))
        method = request["method"].lower()
        operation = schema["paths"][path][method]
        assert (path, method) not in seen
        seen.add((path, method))
        assert str(request["assert"][0]["value"]) in operation["responses"]
        if "smoke" in request["tags"]:
            assert method == "get" and not request.get("path_params")
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

    excluded = {
        "/api/v1/setup/manifest", "/api/v1/setup/callback", "/api/v1/auth/login",
        "/api/v1/auth/callback", "/api/v1/webhooks/github", "/api/v1/installations/start",
        "/api/v1/installations/callback",
    }
    expected = {
        (path, method) for path, methods in schema["paths"].items()
        if (path.startswith("/api/v1/") or path.startswith("/health/")) and path not in excluded
        for method in methods if method in {"get", "post", "put", "patch", "delete"}
    }
    assert seen == expected


@pytest.mark.skipif(not shutil.which("noodle"), reason="Install Noodle to run its HTTP integration check")
def test_noodle_session_and_csrf_over_http(seed, github_stub, monkeypatch, tmp_path):
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository))))
    collection = tmp_path / "collection"
    shutil.copytree(COLLECTION, collection)
    env_file = collection / ".environments/development.env"

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env_file.write_text(env_file.read_text().replace("http://localhost:5173", base_url)
                            .replace("pr_id=REPLACE_ME", "pr_id=pr"))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                Event().wait(0.01)
            assert server.started

            def run(*args, session="session"):
                result = subprocess.run(
                    ["noodle", *args, "--env", "development", "--json"],
                    env={**os.environ, "cm_session": session}, capture_output=True, text=True, timeout=30,
                )
                return result.returncode, json.loads(result.stdout)

            code, result = run("request", "run", "api/session/me", "--collection", str(collection), session="")
            assert code == 1 and result["data"]["result"].get("response") is None
            code, result = run("request", "run", "api/session/me", "--collection", str(collection), session="expired")
            assert code == 1 and result["data"]["result"]["response"]["status"] == 401
            code, result = run("collection", "run", str(collection), "--tag", "smoke", "--fail-fast")
            assert code == 0, result
            assert result["data"]["summary"]["requestSuccesses"] == 13
            with transaction() as db:
                db.get(LoginSession, digest("session")).csrf = ""
            code, result = run("request", "run", "api/pulls/ignore-pull", "--collection", str(collection))
            assert code == 1 and result["data"]["result"].get("response") is None
            with transaction() as db:
                db.get(LoginSession, digest("session")).csrf = "rotated-csrf"
            # This existing request exercises real cookie and CSRF validation without external effects.
            code, result = run("request", "run", "api/pulls/ignore-pull", "--collection", str(collection))
            assert code == 0, result
            assert result["data"]["result"]["response"]["status"] == 200
            code, result = run("request", "run", "api/session/logout", "--collection", str(collection))
            assert code == 0, result
            code, result = run("request", "run", "api/session/me", "--collection", str(collection))
            assert code == 1 and result["data"]["result"]["response"]["status"] == 401
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive()
