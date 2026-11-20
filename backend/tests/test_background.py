import json
import subprocess
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from code_mortis import jobs
from code_mortis.core import source, workflow_inference
from code_mortis.core.db import now, transaction
from code_mortis.core.process import child_environment
from code_mortis.integrations import github
from code_mortis.models import Connection, ConnectionGrant, Job, Membership, Repository, ReviewRun, User
from code_mortis.reviews import background

PARAGRAPH = "This project helps teams review changes to their software."


@pytest.fixture
def generation(seed, github_stub, monkeypatch):
    calls = []
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 22, "default_branch": "main"})
    monkeypatch.setattr(background, "context", lambda *a, **k: {
        "repository": "team/project", "branch": "main", "sha": "a" * 40,
        "sources": {"README.md": {"text": "A review service", "truncated": False}},
    })

    def generate(connection, credentials, context, *, timeout, stopped, token_budget, **kwargs):
        prompt = kwargs["instructions"] + "\n\n" + json.dumps(context)
        assert not stopped()
        calls.append((connection, prompt, timeout, token_budget))
        return {"background": "A review service using Python and React.", "project_summary": PARAGRAPH}, {"tokens": 10}

    monkeypatch.setattr(workflow_inference, "execute", generate)
    return calls


def start(client, **body):
    response = client.post("/api/v1/repositories/22/background", json={"connection_id": "connection", **body})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def status(client, identity):
    return client.get(f"/api/v1/repositories/22/background/{identity}")


@pytest.mark.parametrize("cancel_before_start", [False, True])
def test_cancel_context_generation_prevents_provider_calls(generation, client, cancel_before_start):
    request = {"generation_id": str(uuid4())}
    endpoint = "/api/v1/repositories/22/background/cancel"
    identity = None if cancel_before_start else start(client, **request)
    cancelled = client.post(endpoint, json=request)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert start(client, **request) == cancelled.json()["id"]
    if identity:
        assert cancelled.json()["id"] == identity
    identity = cancelled.json()["id"]
    jobs.execute_job(identity)
    assert status(client, identity).json() == {
        "id": identity, "status": "cancelled", "background": None, "project_summary": None, "error": None,
    }
    assert client.post(endpoint, json=request).json() == cancelled.json()
    assert generation == []


def test_context_cancellation_requires_repository_admin_and_csrf(generation, client, monkeypatch):
    request = {"generation_id": str(uuid4())}
    endpoint = "/api/v1/repositories/22/background/cancel"
    identity = start(client, **request)
    assert client.post(endpoint, json=request, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert client.post(endpoint, json=request).status_code == 403
    with transaction() as db:
        db.get(Membership, 7).role = "admin"
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: {"id": 23})
    assert client.post(endpoint, json=request).status_code == 403
    with transaction() as db:
        assert db.get(Job, identity).status == "queued"


def test_cancellation_only_affects_its_request_and_preserves_completed_drafts(generation, client):
    first = {"generation_id": str(uuid4())}
    second = {"generation_id": str(uuid4())}
    endpoint = "/api/v1/repositories/22/background/cancel"
    finished = start(client, **first)
    pending = start(client, **second)
    assert finished != pending
    assert start(client, **first) == finished
    jobs.execute_job(finished)
    assert client.post(endpoint, json=first).json() == status(client, finished).json()
    assert status(client, finished).json()["project_summary"] == PARAGRAPH
    assert status(client, pending).json()["status"] == "queued"
    assert len(generation) == 1


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
    assert result == {"id": identity, "status": "done", "background": "A review service using Python and React.",
                      "project_summary": PARAGRAPH, "error": None}
    assert generation[0][0]["model"] == expected
    assert "A review service" in generation[0][1]
    assert generation[0][2:] == (None, None)
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
        db.add(ConnectionGrant(connection_id="connection", repository_id=22))
    response = client.post("/api/v1/repositories/22/background", json={"connection_id": "connection"})
    assert response.status_code == 422 and "retired" in response.text
    assert generation == []
    with transaction() as db:
        assert not list(db.scalars(select(Job)))


@pytest.mark.parametrize("revocation", ["membership", "installation", "github", "connection", "kind"])
def test_access_is_revalidated_before_execution(generation, client, monkeypatch, revocation):
    from code_mortis.models import Installation

    identity = start(client)
    with transaction() as db:
        if revocation == "membership":
            db.get(Membership, 7).role = "member"
        elif revocation == "installation":
            db.get(Installation, 10).active = False
        elif revocation == "connection":
            db.get(Connection, "connection").base_url = "https://changed.example/v1"
        elif revocation == "kind":
            db.get(Connection, "connection").kind = "chatgpt"
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


def test_status_rejects_changed_github_repository_identity(generation, client, monkeypatch):
    identity = start(client)
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 23})
    assert status(client, identity).status_code == 403


def test_overview_generation_reuses_matching_active_job_for_large_repository_id(generation, client, monkeypatch):
    identity = 3_000_000_001
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": identity})
    with transaction() as db:
        db.add(Repository(id=identity, installation_id=10, full_name="team/large", connection_id="connection",
                          settings={"background": "Context", "model": "saved-model", "timeout_seconds": 120}))
    endpoint = f"/api/v1/repositories/{identity}/background"
    first = client.post(endpoint, json={"mode": "summary"})
    second = client.post(endpoint, json={"mode": "summary"})
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    with transaction() as db:
        job = db.get(Job, first.json()["id"])
        assert job.payload["connection"]["model"] == "saved-model"
        assert job.payload["limits"]["timeout_seconds"] == 120
        repo = db.get(Repository, identity)
        repo.settings = {**repo.settings, "background": "New context"}
    third = client.post(endpoint, json={"mode": "summary"})
    assert third.status_code == 202 and third.json()["id"] != first.json()["id"]
    with transaction() as db:
        db.get(Repository, identity).connection_id = None
    missing = client.post(endpoint, json={"mode": "summary"})
    assert missing.status_code == 422 and "AI connection" in missing.text


def test_overview_context_remains_draft_and_save_respects_saved_context(generation, client):
    response = client.post("/api/v1/repositories/22/background", json={"mode": "context"})
    assert response.status_code == 202
    identity = response.json()["id"]
    jobs.execute_job(identity)
    draft = status(client, identity).json()
    with transaction() as db:
        assert "background" not in db.get(Repository, 22).settings
    body = {"enabled": False, "expected_background": "", "settings": {
        key: draft[key] for key in ("background", "project_summary")
    }}
    with transaction() as db:
        db.get(Repository, 22).settings = {"background": "A concurrent edit", "project_summary": "Another paragraph."}
    assert client.put("/api/v1/repositories/22", json=body).status_code == 409
    with transaction() as db:
        assert db.get(Repository, 22).settings["project_summary"] == "Another paragraph."
    body["expected_background"] = "A concurrent edit"
    saved = client.put("/api/v1/repositories/22", json=body)
    assert saved.status_code == 200 and saved.json()["settings"]["project_summary"] == PARAGRAPH


def test_settings_save_preserves_summary_when_context_changes_or_is_cleared(generation, client):
    with transaction() as db:
        db.get(Repository, 22).settings = {"background": "Saved context", "project_summary": PARAGRAPH}
    body = {"enabled": False, "expected_background": "Saved context", "settings": {"automatic": False}}
    saved = client.put("/api/v1/repositories/22", json=body)
    assert saved.status_code == 200
    assert saved.json()["settings"]["background"] == "Saved context"
    assert saved.json()["settings"]["project_summary"] == PARAGRAPH
    body["settings"]["background"] = "Edited context"
    changed = client.put("/api/v1/repositories/22", json=body)
    assert changed.status_code == 200
    assert changed.json()["settings"]["project_summary"] == PARAGRAPH
    body["expected_background"] = "Edited context"
    body["settings"]["background"] = ""
    cleared = client.put("/api/v1/repositories/22", json=body)
    assert cleared.status_code == 200
    assert cleared.json()["settings"]["background"] == ""
    assert cleared.json()["settings"]["project_summary"] == PARAGRAPH
    body["expected_background"] = ""
    body["settings"]["project_summary"] = "Updated summary."
    edited = client.put("/api/v1/repositories/22", json=body)
    assert edited.status_code == 200
    assert edited.json()["settings"]["project_summary"] == "Updated summary."
    body["settings"]["project_summary"] = None
    removed = client.put("/api/v1/repositories/22", json=body)
    assert removed.status_code == 200
    assert removed.json()["settings"]["project_summary"] is None


def test_regenerate_discards_stale_summary_job(generation, client):
    with transaction() as db:
        db.get(Repository, 22).settings = {"project_summary": "Published summary.", "background": "Saved context."}
    identity = start(client, mode="summary_draft", generation_id=str(uuid4()))
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "project_summary": "Newer edit."}
    jobs.execute_job(identity)
    result = status(client, identity).json()
    assert result["status"] == "failed"
    assert result["project_summary"] is None
    assert generation == []
    with transaction() as db:
        assert db.get(Repository, 22).settings["project_summary"] == "Newer edit."


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
    monkeypatch.setattr(workflow_inference, "execute", lambda *a, **kwargs: (value, {"tokens": 0}))
    identity = start(client)
    jobs.execute_job(identity)
    result = status(client, identity).json()
    assert result["status"] == "failed" and result["background"] is None
    assert "invalid project background" in result["error"]


def test_recovery_preserves_saved_result_and_never_repeats_inference(generation, client, monkeypatch):
    interrupted, legacy, summary = (start(client, model=model) for model in ("interrupted", "legacy", "summary"))
    with transaction() as db:
        for identity in (interrupted, legacy, summary):
            job = db.get(Job, identity)
            job.status, job.attempts, job.lease_until = "running", 1, now() - timedelta(seconds=1)
            job.payload = {**job.payload, "inference_started": True}
            if identity == legacy:
                job.payload = {**job.payload, "mode": None, "background": "Already generated"}
            if identity == summary:
                job.payload = {**job.payload, "mode": "summary", "project_summary": PARAGRAPH}
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda *a, **k: pytest.fail("Must not dispatch inference again"))
    jobs.dispatch()
    assert status(client, interrupted).json()["status"] == "failed"
    assert "usage may have been consumed" in status(client, interrupted).json()["error"]
    assert status(client, legacy).json()["background"] == "Already generated"
    assert status(client, legacy).json()["project_summary"] is None
    assert status(client, summary).json()["project_summary"] == PARAGRAPH
    assert generation == []


@pytest.mark.parametrize("reason,expected", [("busy", "queued"), ("unavailable", "failed"), ("quota", "failed"), ("authentication", "failed")])
def test_native_failure_only_retries_busy_before_consumption(generation, client, monkeypatch, reason, expected):
    identity = start(client)
    with transaction() as db:
        job = db.get(Job, identity)
        job.payload = {**job.payload, "connection": {**job.payload["connection"], "kind": "chatgpt"}}
    jobs.execute_job(identity)
    assert status(client, identity).json()["status"] == "failed"
    with transaction() as db:
        assert not db.get(Job, identity).payload.get("inference_started")
        assert db.get(Job, identity).attempts == 0
    assert generation == []


def test_revocation_during_inference_drops_result(generation, client, monkeypatch):
    def revoke(*a, **kwargs):
        with transaction() as db:
            db.get(Membership, 7).role = "member"
        return {"background": "Must not be saved"}, {"tokens": 10}
    monkeypatch.setattr(workflow_inference, "execute", revoke)
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
    result = background.collect(tmp_path, child_environment(), sha, lambda: False)
    assert set(result["sources"]) == {"README.md", "pyproject.toml", "src/main.py", "docs/architecture.md"}
    assert result["sources"]["README.md"]["truncated"]
    assert "PRIVATE_VALUE" not in json.dumps(result)
    assert "worktree content" not in json.dumps(result)
    assert len(json.dumps(result).encode()) < background.CONTEXT_BYTES


def test_sha_validation_prevents_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(github, "request", lambda *a: {"sha": "--malicious"})
    monkeypatch.setattr(source, "git", lambda *a, **k: pytest.fail("Invalid identity must not reach git"))
    with pytest.raises(ValueError, match="valid default-branch commit"):
        background.context("team/project", "main", "token", tmp_path, lambda: False)


def test_source_sample_includes_entrypoints_across_modules(monkeypatch, tmp_path):
    paths = [f"backend/util_{index}.py" for index in range(20)] + ["backend/main.py", "frontend/src/App.tsx", "tests/test_main.py"]

    def git(directory, env, *args, **kwargs):
        if args[0] == "ls-tree":
            return "\0".join(f"100644 blob {'a' * 40} 1\t{path}" for path in paths)
        return "x"

    monkeypatch.setattr(source, "git", git)
    result = background.collect(tmp_path, {}, "a" * 40, lambda: False)
    assert "backend/main.py" in result["sources"] and "frontend/src/App.tsx" in result["sources"]
    assert len(result["sources"]) == 10


def test_result_secrets_are_redacted(generation, client, monkeypatch):
    monkeypatch.setattr(workflow_inference, "execute", lambda *a, **kwargs: (
        {"background": "Never expose installation-token or provider-secret",
         "project_summary": "Uses provider-secret\n\nand installation-token."}, {"tokens": 10}))
    identity = start(client)
    jobs.execute_job(identity)
    result = status(client, identity).json()
    assert result["status"] == "done" and result["background"] == "Never expose [redacted] or [redacted]"
    assert result["project_summary"] == "Uses [redacted] and [redacted]."


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


def test_failed_generation_stores_diagnostics_without_exposing_them(generation, client, monkeypatch):
    def fail(*args, **kwargs):
        raise background.GenerationError("Generation exceeded its token allowance.", '{"total_tokens": 0}')
    monkeypatch.setattr(workflow_inference, "execute", fail)
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

    def generate(connection, credentials, context, *, timeout, stopped, token_budget, **kwargs):
        with transaction() as db:
            job = db.get(Job, identity)
            job.payload = {**job.payload, "expires_at": 0}
            job.lease_until = now() + timedelta(seconds=1)
        tick[0] = 31
        assert not stopped()
        with transaction() as db:
            assert (db.get(Job, identity).lease_until - now()).total_seconds() > 300
        return {"background": "A validated draft", "project_summary": PARAGRAPH}, {"tokens": 10}
    monkeypatch.setattr(workflow_inference, "execute", generate)
    jobs.execute_job(identity)
    assert status(client, identity).json()["status"] == "done"
