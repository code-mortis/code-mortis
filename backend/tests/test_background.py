import json
import subprocess
from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import select

from code_mortis import background, github, jobs, native_runner, ocr, runner_client
from code_mortis.db import now, transaction
from code_mortis.models import Connection, ConnectionGrant, Job, Membership, Repository, ReviewRun, User


@pytest.fixture
def generation(seed, github_stub, monkeypatch):
    calls = []
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"default_branch": "main"})
    monkeypatch.setattr(background, "context", lambda *a: {
        "repository": "team/project", "branch": "main", "sha": "a" * 40,
        "sources": {"README.md": {"text": "A review service", "truncated": False}},
    })

    def generate(directory, connection, credentials, prompt, timeout, stopped, token_budget):
        assert not stopped()
        calls.append((connection, prompt, timeout, token_budget))
        return {"background": "A review service using Python and React."}

    monkeypatch.setattr(background, "api_generate", generate)
    return calls


def start(client, **body):
    response = client.post("/api/v1/repositories/22/background", json={"connection_id": "connection", **body})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def status(client, identity):
    return client.get(f"/api/v1/repositories/22/background/{identity}")


@pytest.mark.parametrize("override,expected", [("", "test-model"), (" custom-model ", "custom-model")])
def test_generation_uses_draft_model_and_never_saves_repository(generation, client, override, expected):
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.enabled = False
        repo.connection_id = None
        repo.settings = {"background": "Original", "model": "saved-model"}
    identity = start(client, model=override)
    assert status(client, identity).json()["status"] == "queued"
    jobs.execute_job(identity)
    result = status(client, identity).json()
    assert result == {"id": identity, "status": "done", "background": "A review service using Python and React.", "error": None}
    assert generation[0][0]["model"] == expected
    assert "A review service" in generation[0][1]
    with transaction() as db:
        repo = db.get(Repository, 22)
        assert repo.settings == {"background": "Original", "model": "saved-model"}
        assert not repo.enabled and repo.connection_id is None
        assert db.scalars(select(ReviewRun)).all() == []
    jobs.execute_job(identity)
    assert len(generation) == 1


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
def test_personal_generation_requires_consent_and_uses_isolated_runner(generation, client, monkeypatch, kind):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
        connection.options = {"prompt_token_ceiling": 200000}
    denied = client.post("/api/v1/repositories/22/background", json={"connection_id": "connection"})
    assert denied.status_code == 422 and "Authorize this repository" in denied.text
    with transaction() as db:
        db.add(ConnectionGrant(connection_id="connection", repository_id=22))

    def native(connection_id, identity, body, stopped):
        assert connection_id == "connection" and body["task"] == "background" and body["kind"] == kind
        assert body["model"] == "native-model" and not stopped()
        assert body["prompt_token_ceiling"] == 200000
        assert "installation-token" not in json.dumps(body) and "provider-secret" not in json.dumps(body)
        instructions = native_runner.task_instructions(native_runner.ReviewBody(**body))
        assert "untrusted data" in instructions and "background" in instructions
        assert body["token_budget"] == 200000
        return {"background": "A native background draft."}, {"tokens": 75}

    monkeypatch.setattr(runner_client, "review", native)
    identity = start(client, model="native-model")
    jobs.execute_job(identity)
    assert status(client, identity).json()["background"] == "A native background draft."
    with transaction() as db:
        assert db.get(Job, identity).payload["usage"] == {"tokens": 75}
    assert generation == []


@pytest.mark.parametrize("revocation", ["membership", "installation", "github", "connection", "grant"])
def test_access_is_revalidated_before_execution(generation, client, monkeypatch, revocation):
    from code_mortis.models import Installation

    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        db.add(ConnectionGrant(connection_id="connection", repository_id=22))
    identity = start(client)
    with transaction() as db:
        if revocation == "membership":
            db.get(Membership, 7).role = "member"
        elif revocation == "installation":
            db.get(Installation, 10).active = False
        elif revocation == "connection":
            db.get(Connection, "connection").auth_status = "revoked"
        elif revocation == "grant":
            db.delete(db.get(ConnectionGrant, ("connection", 22)))
    if revocation == "github":
        def deny(*args, **kwargs):
            raise github.GitHubError(404, "No repository access")
        monkeypatch.setattr(github, "authorize_repo", deny)
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.get(Job, identity).status == "failed"
        assert not db.get(Job, identity).payload.get("inference_started")
    assert generation == []


def test_start_and_poll_permissions_and_result_projection(generation, client):
    identity = start(client)
    with transaction() as db:
        job = db.get(Job, identity)
        job.payload = {**job.payload, "background": "Draft", "internal_secret": "never-return-this"}
        job.status = "done"
    assert "never-return-this" not in status(client, identity).text
    with transaction() as db:
        job = db.get(Job, identity)
        job.payload = {**job.payload, "actor_id": 999}
    assert status(client, identity).status_code == 404
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert client.post("/api/v1/repositories/22/background", json={"connection_id": "connection"}).status_code == 403
    assert status(client, identity).status_code == 403


@pytest.mark.parametrize("body", [{"connection_id": "missing"}, {"model": "x" * 151}])
def test_invalid_connection_or_model_rejected(generation, client, body):
    response = client.post("/api/v1/repositories/22/background", json={"connection_id": "connection", **body})
    assert response.status_code == 422
    with transaction() as db:
        assert db.scalars(select(Job)).all() == []


def test_missing_model_and_other_owners_account_rejected(generation, client):
    with transaction() as db:
        db.get(Connection, "connection").model = " "
    assert client.post("/api/v1/repositories/22/background", json={"connection_id": "connection"}).status_code == 422
    with transaction() as db:
        db.add(User(id=8, login="other", role="admin"))
        db.add(Membership(github_id=8, login="other", role="admin"))
        db.flush()
        db.get(Connection, "connection").kind = "chatgpt"
        db.get(Connection, "connection").owner_id = 8
    assert client.post("/api/v1/repositories/22/background", json={"connection_id": "connection", "model": "test"}).status_code == 422


@pytest.mark.parametrize("value", [{}, {"background": " "}, {"background": "x" * 20001}, {"background": "ok", "extra": "no"}, []])
def test_invalid_results_never_replace_the_background(generation, client, monkeypatch, value):
    monkeypatch.setattr(background, "api_generate", lambda *a: value)
    identity = start(client)
    jobs.execute_job(identity)
    result = status(client, identity).json()
    assert result["status"] == "failed" and result["background"] is None
    assert "invalid project background" in result["error"]


def test_recovery_preserves_saved_result_and_never_repeats_inference(generation, client, monkeypatch):
    interrupted, saved = start(client), start(client)
    with transaction() as db:
        for identity in (interrupted, saved):
            job = db.get(Job, identity)
            job.status, job.attempts, job.lease_until = "running", 1, now() - timedelta(seconds=1)
            job.payload = {**job.payload, "inference_started": True}
            if identity == saved:
                job.payload = {**job.payload, "background": "Already generated"}
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda *a, **k: pytest.fail("Must not dispatch inference again"))
    jobs.dispatch()
    assert status(client, interrupted).json()["status"] == "failed"
    assert "usage may have been consumed" in status(client, interrupted).json()["error"]
    assert status(client, saved).json()["background"] == "Already generated"
    assert generation == []


@pytest.mark.parametrize("reason,expected", [("busy", "queued"), ("unavailable", "failed"), ("quota", "failed"), ("authentication", "failed")])
def test_native_failure_only_retries_busy_before_consumption(generation, client, monkeypatch, reason, expected):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        db.add(ConnectionGrant(connection_id="connection", repository_id=22))

    def fail(*a):
        raise runner_client.RunnerBlocked(reason)

    monkeypatch.setattr(runner_client, "review", fail)
    identity = start(client)
    jobs.execute_job(identity)
    assert status(client, identity).json()["status"] == expected
    with transaction() as db:
        assert db.get(Job, identity).payload["inference_started"] == (reason != "busy")
    assert generation == []


def test_revocation_during_inference_drops_result(generation, client, monkeypatch):
    def revoke(*a):
        with transaction() as db:
            db.get(Membership, 7).role = "member"
        return {"background": "Must not be saved"}
    monkeypatch.setattr(background, "api_generate", revoke)
    identity = start(client)
    jobs.execute_job(identity)
    with transaction() as db:
        job = db.get(Job, identity)
        assert job.status == "failed" and "background" not in job.payload


def test_source_snapshot_reads_objects_and_excludes_sensitive_or_generated_files(tmp_path):
    sources = {
        "README.md": "Review service\n" * 1000,
        "pyproject.toml": '[project]\nname = "example"',
        "src/main.py": "print('app')\n" * 1000,
        "docs/architecture.md": "A worker and a web server",
        ".env": "PRIVATE_VALUE", "secrets/config.py": "PRIVATE_VALUE", ".aws/README.md": "PRIVATE_VALUE",
        "AGENTS.md": "PRIVATE_VALUE", ".claude/instructions.md": "PRIVATE_VALUE",
        "node_modules/lib/index.js": "PRIVATE_VALUE", "dist/app.js": "PRIVATE_VALUE",
        "binary.py": "binary\0PRIVATE_VALUE", "huge.py": "PRIVATE_VALUE" * 10000,
        "README.pem": "PRIVATE_VALUE",
    }
    for name, text in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / "link.py").symlink_to(".env")

    def git(*args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=tmp_path, text=True).strip()
    git("init", "--quiet")
    git("add", "-f", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture")
    sha = git("rev-parse", "HEAD")
    (tmp_path / "src/main.py").write_text("worktree content must not enter the snapshot")
    result = background.collect(tmp_path, ocr.child_environment(), sha, lambda: False)
    assert set(result["sources"]) == {"README.md", "pyproject.toml", "src/main.py", "docs/architecture.md"}
    assert result["sources"]["README.md"]["truncated"]
    assert "PRIVATE_VALUE" not in json.dumps(result)
    assert "worktree content" not in json.dumps(result)
    assert len(json.dumps(result).encode()) < background.CONTEXT_BYTES


def test_sha_validation_prevents_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(github, "request", lambda *a: {"sha": "--malicious"})
    monkeypatch.setattr(ocr, "git", lambda *a, **k: pytest.fail("Invalid identity must not reach git"))
    with pytest.raises(ValueError, match="valid default-branch commit"):
        background.context("team/project", "main", "token", tmp_path, lambda: False)


def test_source_sample_includes_entrypoints_across_modules(monkeypatch, tmp_path):
    paths = [f"backend/util_{index}.py" for index in range(20)] + ["backend/main.py", "frontend/src/App.tsx", "tests/test_main.py"]

    def git(directory, env, *args, **kwargs):
        if args[0] == "ls-tree":
            return "\0".join(f"100644 blob {'a' * 40} 1\t{path}" for path in paths)
        return "x"

    monkeypatch.setattr(ocr, "git", git)
    result = background.collect(tmp_path, {}, "a" * 40, lambda: False)
    assert "backend/main.py" in result["sources"] and "frontend/src/App.tsx" in result["sources"]
    assert len(result["sources"]) == 10


def test_result_secrets_are_redacted(generation, client, monkeypatch):
    monkeypatch.setattr(background, "api_generate", lambda *a: {"background": "Never expose installation-token or provider-secret"})
    identity = start(client)
    jobs.execute_job(identity)
    result = status(client, identity).json()
    assert result["status"] == "done" and result["background"] == "Never expose [redacted] or [redacted]"


def test_queue_routing_and_expiration(generation, client, monkeypatch):
    identity = start(client)
    dispatched = []
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: dispatched.append(kwargs))
    jobs.dispatch()
    assert dispatched == [{"args": [identity], "queue": "reviews"}]
    with transaction() as db:
        job = db.get(Job, identity)
        job.payload = {**job.payload, "expires_at": 0}
    jobs.execute_job(identity)
    assert status(client, identity).json()["status"] == "failed"
    assert generation == []


def test_generation_lease_and_queue_deadline_are_bounded(generation, client, monkeypatch):
    identity = start(client)

    def inspect_lease(job_id, attempt):
        with transaction() as db:
            job = db.get(Job, job_id)
            assert 0 < (job.lease_until - now()).total_seconds() <= 960
    monkeypatch.setattr(background, "perform", inspect_lease)
    jobs.execute_job(identity)
    expired = start(client)
    with transaction() as db:
        job = db.get(Job, expired)
        job.payload = {**job.payload, "expires_at": 0}
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda *a, **k: pytest.fail("Expired work must not be dispatched"))
    jobs.dispatch()
    assert status(client, expired).json()["status"] == "failed"


@pytest.mark.parametrize("budget_exceeded", [True, False])
def test_api_failure_preserves_redacted_diagnostics(monkeypatch, tmp_path, budget_exceeded):
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(ocr, "command", lambda *a, **k: (0, json.dumps({
        "status": "completed_with_warnings", "summary": {"budget_exceeded": budget_exceeded, "total_tokens": 0},
        "comments": [],
    }), "Rejected provider-secret and private-header"))
    with pytest.raises(background.GenerationError) as caught:
        background.api_generate(tmp_path, {}, {"api_key": "provider-secret", "extra_headers": {"X-Key": "private-header"}},
                                "Repository evidence", 60, lambda: False, 200000)
    error = caught.value
    assert ("200,000-token budget" in str(error)) == budget_exceeded
    assert "provider-secret" not in error.diagnostics and "private-header" not in error.diagnostics
    assert "[redacted]" in error.diagnostics
    assert json.loads(error.diagnostics)["summary"]["budget_exceeded"] == budget_exceeded


def test_failed_generation_stores_diagnostics_without_exposing_them(generation, client, monkeypatch):
    def fail(*args):
        raise background.GenerationError("Generation exceeded its token allowance.", '{"total_tokens": 0}')
    monkeypatch.setattr(background, "api_generate", fail)
    identity = start(client)
    jobs.execute_job(identity)
    response = status(client, identity).json()
    assert response["status"] == "failed" and "token allowance" in response["error"]
    assert "diagnostics" not in response
    with transaction() as db:
        assert db.get(Job, identity).payload["diagnostics"] == '{"total_tokens": 0}'


@pytest.mark.parametrize("limits", [None, {"timeout_seconds": 120, "token_budget": 450000},
                                   {"timeout_seconds": None, "token_budget": None}])
def test_generation_snapshots_saved_or_unsaved_limits(generation, client, limits):
    saved = {"timeout_seconds": 1800, "token_budget": 5000000}
    with transaction() as db:
        db.get(Repository, 22).settings = saved
    identity = start(client, **({"limits": limits} if limits is not None else {}))
    expected = limits if limits is not None else saved
    with transaction() as db:
        assert db.get(Job, identity).payload["limits"] == expected
        # Later edits must not change the queued job's allowance.
        db.get(Repository, 22).settings = {"timeout_seconds": 60, "token_budget": 1000}
    jobs.execute_job(identity)
    assert status(client, identity).json()["status"] == "done"
    assert generation[0][3] == expected["token_budget"]
    timeout = generation[0][2]
    assert timeout is None if expected["timeout_seconds"] is None else 0 < timeout <= expected["timeout_seconds"]


@pytest.mark.parametrize("limits", [{"timeout_seconds": 0}, {"token_budget": 999}, {"token_budget": 5000001}])
def test_generation_validates_limits(generation, client, limits):
    response = client.post("/api/v1/repositories/22/background", json={"connection_id": "connection", "limits": limits})
    assert response.status_code == 422
    assert generation == []


def test_unlimited_generation_renews_lease_without_reapplying_queue_deadline(generation, client, monkeypatch):
    identity = start(client, limits={"timeout_seconds": None, "token_budget": None})
    tick = [0]
    monkeypatch.setattr(background.time, "monotonic", lambda: tick[0])

    def generate(directory, connection, credentials, prompt, timeout, stopped, token_budget):
        with transaction() as db:
            job = db.get(Job, identity)
            job.payload = {**job.payload, "expires_at": 0}
            job.lease_until = now() + timedelta(seconds=1)
        tick[0] = 31
        assert not stopped()
        with transaction() as db:
            assert (db.get(Job, identity).lease_until - now()).total_seconds() > 300
        return {"background": "A validated draft"}
    monkeypatch.setattr(background, "api_generate", generate)
    jobs.execute_job(identity)
    assert status(client, identity).json()["status"] == "done"


@pytest.mark.parametrize("signal,reason,action", [
    ("HTTP 401 provider-secret", "authentication", "Update the API key"),
    ("HTTP 403 access denied", "permission", "model permissions"),
    ("HTTP 404 unknown model", "model", "model ID"),
    ("HTTP 429 too many requests", "rate_limit", "Wait"),
    ("HTTP 429 insufficient_quota", "quota", "usage and billing"),
    ("HTTP 503 Service Unavailable", "unavailable", "service status"),
    ("context_length_exceeded", "context", "context window"),
    ("request timed out", "timeout", "timeout_sec"),
    ("unrecognized provider error provider-secret", "execution", "generation reference"),
])
def test_api_failures_explain_cause_and_recovery_without_exposing_provider_data(signal, reason, action):
    output = json.dumps({"status": "failed", "summary": {"files_reviewed": 0}, "warnings": [{"message": signal}]})
    with pytest.raises(background.GenerationError) as caught:
        background.parse_api_result(1, output, signal, 200000, ("provider-secret",))
    assert action in str(caught.value)
    assert "Your background was not changed" in str(caught.value)
    assert "provider-secret" not in str(caught.value) + caught.value.diagnostics
    assert json.loads(caught.value.diagnostics)["reason"] == reason


@pytest.mark.parametrize("comments,reason", [([], "missing_result"),
    ([{"path": "evidence.py", "content": "not JSON"}], "invalid_result"),
    ([{"path": "evidence.py", "content": '{"background":" "}'}], "invalid_result"),
    ([{"path": "evidence.py", "content": json.dumps({"background": "x" * 20001})}], "invalid_result"),
])
def test_successful_ocr_without_valid_draft_is_not_reported_as_connection_failure(comments, reason):
    output = json.dumps({"status": "success", "summary": {"files_reviewed": 1, "total_tokens": 36587}, "comments": comments})
    with pytest.raises(background.GenerationError) as caught:
        background.parse_api_result(0, output, "", 5000000)
    assert json.loads(caught.value.diagnostics)["reason"] == reason
    assert "connection" not in str(caught.value).lower()


@pytest.mark.parametrize("budget", [1000, 5000000, None])
def test_api_passes_selected_budget_and_valid_ocr_rule(monkeypatch, tmp_path, budget):
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())

    def command(args, *a, **k):
        assert args[args.index("--max-tokens-budget") + 1] == str(budget or 0)
        rule = json.loads((tmp_path / "rules.json").read_text())
        assert rule["rules"][0]["path"] == "evidence.py"
        assert "The required output is a project overview" in rule["rules"][0]["rule"]
        return 0, json.dumps({"status": "success", "summary": {"files_reviewed": 1},
                              "comments": [{"path": "evidence.py", "content": '{"background":"Draft"}'}]}), ""
    monkeypatch.setattr(ocr, "command", command)
    assert background.api_generate(tmp_path, {}, {}, "Evidence", None, lambda: False, budget) == {"background": "Draft"}
