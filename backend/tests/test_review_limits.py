import asyncio
import fcntl
import json
import os
import sys
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_ocr import report
from test_review_flow import new_run

from code_mortis import jobs, native_runner, ocr, reviews, runner_client
from code_mortis.db import now, transaction
from code_mortis.models import Job, PullRequest, Repository, ReviewRun
from code_mortis.schemas import RepoSettings


def test_settings_defaults_round_trip_and_validation(seed, github_stub, client):
    defaults = RepoSettings()
    assert (defaults.timeout_seconds, defaults.token_budget, defaults.max_files) == (900, 200000, 40)
    assert defaults.delegated_context_bytes == 800000
    for values in ({"timeout_seconds": None}, {"token_budget": None}, {"max_files": None},
                   {"delegated_context_bytes": None},
                   {"timeout_seconds": None, "token_budget": None, "max_files": None, "delegated_context_bytes": None}):
        response = client.put("/api/v1/repositories/22", json={
            "enabled": True, "connection_id": "connection", "settings": values,
        })
        assert response.status_code == 200
        assert response.json()["settings"] == RepoSettings(**values).model_dump()
        with transaction() as db:
            assert db.get(Repository, 22).settings == response.json()["settings"]
    for key, values in {"timeout_seconds": [0, 59, 3601], "token_budget": [0, 999, 5000001],
                        "max_files": [0, -1, 1.5, True], "delegated_context_bytes": [0, 4095, 20000001]}.items():
        for value in values:
            with pytest.raises(ValidationError):
                RepoSettings(**{key: value})


@pytest.mark.parametrize("language,expected", [("  Mapudungun  ", "Mapudungun"), ("\tEnglish\n", "English"),
                                              (" ", None), ("\t\n", None)])
def test_repository_language_is_trimmed_and_cannot_be_blank(seed, github_stub, client, language, expected):
    response = client.put("/api/v1/repositories/22", json={
        "enabled": True, "connection_id": "connection", "settings": {"language": language},
    })
    assert response.status_code == (200 if expected else 422)
    if expected:
        assert response.json()["settings"]["language"] == expected
        with transaction() as db:
            assert db.get(Repository, 22).settings["language"] == expected


def test_old_snapshots_keep_unlimited_files_and_automatic_edits_do_not_rerun(seed, github_stub):
    with transaction() as db:
        repo, pr = db.get(Repository, 22), db.get(PullRequest, "pr")
        old = reviews.create_run(db, pr, repo, "automatic")
        db.flush()
        original = {key: value for key, value in old.config["settings"].items() if key != "max_files"}
        old.config = {**old.config, "settings": original}
        old.dedup_key, old.status, old.publication = "legacy", "completed", "published"
        repo.settings = {"max_files": None}
        assert reviews.create_run(db, pr, repo, "automatic").id == old.id
        repo.settings = {}
        assert reviews.create_run(db, pr, repo, "automatic").id == old.id
        pr.head_sha = "c" * 40
        new = reviews.create_run(db, pr, repo, "automatic")
        assert new.id != old.id and new.config["settings"]["max_files"] == 40
        assert old.config["settings"] == original


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("count,maximum", [(39, 40), (40, 40), (41, 40), (41, None)])
def test_engine_file_gate_precedes_inference(kind, count, maximum, tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings(), "work_dir", tmp_path)
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    configured = False

    @contextmanager
    def configuration(*args):
        nonlocal configured
        configured = True
        try:
            yield
        finally:
            configured = False

    monkeypatch.setattr(ocr, "configuration", configuration)
    base, head = "a" * 40, "b" * 40
    paths = [f"source{index}.py" for index in range(count)]
    inference, previews, completed = [], [], []

    def git(root, env, *args, **kwargs):
        return head if args[0] == "rev-parse" else base if args[0] == "merge-base" else "value = 1\n"

    def command(args, root, env, **kwargs):
        assert "--exclude" in args and "--rule" in args
        if "--preview" in args or "preview" in args:
            previews.append(args)
            data = {"schema_version": "1", "reviewable_count": count,
                    "reviewable_files": [{"path": path} for path in paths],
                    "files": [{"path": path, "will_review": True} for path in paths]
                             + [{"path": "ignored.lock", "will_review": False}]}
        elif "rule" in args:
            data = {"schema_version": "1", "groups": [{"files": [args[-1]], "rule": "Review code"}]}
        else:
            assert args[args.index("--max-tokens-budget") + 1] == "0"
            assert args[args.index("--timeout") + 1] == "0" and kwargs["timeout"] is None
            assert not kwargs["stopped"]()
            inference.append(args)
            data = report()
            data["comments"] = []
        return 0, json.dumps(data), ""

    def native(connection, identity, payload, stopped):
        assert payload["timeout"] is None and not stopped()
        assert payload["prompt_token_ceiling"] == 200000
        inference.append(payload)
        assert payload["token_budget"] is None
        return {"comments": []}, {"tokens": 25}

    def after_review(result, repo, env, fetch_env):
        assert not configured, "Release OCR configuration before saving or verifying results"
        assert repo.is_dir(), "Keep the checkout available during completion"
        completed.append(result)

    monkeypatch.setattr(ocr, "git", git)
    monkeypatch.setattr(ocr, "command", command)
    monkeypatch.setattr(runner_client, "review", native)
    options = RepoSettings(timeout_seconds=None, token_budget=None, max_files=maximum,
                           exclusions="*.lock", rules={"default_rule": "Review code"}).model_dump()
    status, saved, comments = ocr.review("owner/repo", 1, base, head,
        {"id": "connection", "kind": kind, "model": "model", "options": {"prompt_token_ceiling": 200000}}, {}, options, "token", lambda: False,
        after_review=after_review)
    assert completed == [(status, saved, comments)]
    assert comments == []
    if maximum is not None and count > maximum:
        assert status == "skipped" and not inference
        assert saved["message"] == "41 reviewable files exceeds the maximum of 40."
        assert saved["summary"]["total_tokens"] == 0
        coverage = saved["manifest"]["coverage"]
        assert not coverage["completed"] and len(coverage["selected"]) == len(coverage["waived"]) == count
    else:
        assert status == "completed" and len(inference) == (1 if kind == "api" else count)
    assert len(previews) == (0 if kind == "api" and maximum is None else 1)


@pytest.mark.parametrize("files,count", [(None, 0), ([{}], 1), ([{"path": "../secret"}], 1),
                                         ([{"path": "a.py"}] * 2, 2), ([], 1), ([], False)])
def test_invalid_scope_never_passes_file_gate(files, count):
    with pytest.raises(ValueError):
        ocr.preview_paths(files, count)


@pytest.mark.parametrize("path", [None, "", 123, "../secret", "/secret", ".git/config", "src\\secret", "bad\0path"])
def test_invalid_preview_path_has_a_uniform_error(path):
    with pytest.raises(ValueError, match=r"^The review engine selected an invalid file path\. The review was stopped because that path cannot be read safely\. Ask the administrator to check the worker logs\.$"):
        ocr.preview_paths([{"path": path}], 1)


@pytest.mark.parametrize("code,output", [
    (0, ""), (0, "not JSON"), (0, '{"files":'), (0, "null"), (0, "[]"),
    (1, "not JSON"), (1, '{"files": [], "reviewable_count": 0}'),
])
def test_invalid_api_preview_stops_with_a_uniform_error(code, output, tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings(), "work_dir", tmp_path)
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    base, head = "a" * 40, "b" * 40
    monkeypatch.setattr(ocr, "git", lambda root, env, *args, **kwargs: head if args[0] == "rev-parse" else base)

    def command(args, *a, **kwargs):
        assert "--preview" in args, "Invalid previews must never reach inference"
        return code, output, ""

    def after_review(*args):
        pytest.fail("Invalid previews must never reach completion or verification")

    monkeypatch.setattr(ocr, "command", command)
    with pytest.raises(ValueError, match=r"^The review engine could not prepare the files and rules for review\. Check file exclusions and review rules, then start a new review\.$"):
        ocr.review("owner/repo", 1, base, head, {"kind": "api"}, {}, RepoSettings().model_dump(),
                   "token", lambda: False, after_review=after_review)


def test_unlimited_subprocess_completes_and_remains_cancellable(tmp_path):
    assert ocr.command([sys.executable, "-c", "print('done')"], tmp_path, os.environ.copy(), timeout=None)[1] == "done\n"
    with pytest.raises(ocr.ReviewStopped):
        ocr.command([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, os.environ.copy(),
                    timeout=None, stopped=lambda: True)
    with pytest.raises(TimeoutError):
        ocr.command([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, os.environ.copy(), timeout=0)


def test_waiting_for_ocr_configuration_keeps_polling_cancellation(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings(), "ocr_isolated_runtime", True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / ".opencodereview"
    folder.mkdir()
    polls = []
    def stopped():
        polls.append(True)
        return len(polls) == 2
    with (folder / ".mortis.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(ocr.ReviewStopped):
            with ocr.configuration({}, {}, {}, stopped):
                pytest.fail("Must not acquire another invocation's configuration")
    assert len(polls) == 2 and not (folder / "config.json").exists()


def test_long_reviews_renew_only_the_current_job_lease(seed, github_stub, monkeypatch):
    identity = new_run()
    start = now()
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.kind == "review"))
        job.status, job.attempts, job.lease_until = "running", 1, start + timedelta(seconds=7200)
        claim, job_id = f"{job.id}:1", job.id
        run = db.get(ReviewRun, identity)
        run.status, run.progress = "running", {"execution": claim}
    for seconds in (59, 60, 3600, 7100, 7250):
        monkeypatch.setattr(reviews, "now", lambda seconds=seconds: start + timedelta(seconds=seconds))
        assert not reviews.review_stopped(identity, claim)
        with transaction() as db:
            assert db.get(Job, job_id).lease_until == start + timedelta(seconds=7200 + (0 if seconds == 59 else seconds))
    monkeypatch.setattr(jobs, "now", reviews.now)
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: None)
    jobs.dispatch()
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == "running" and job.attempts == 1
        job.attempts = 2
        lease = job.lease_until
    assert reviews.review_stopped(identity, claim)
    with transaction() as db:
        assert db.get(Job, job_id).lease_until == lease


@pytest.mark.parametrize("byte_budget", [4096, 800000, None])
def test_native_prompt_limit_preserves_token_budget_and_ignores_legacy_byte_limit(tmp_path, monkeypatch, byte_budget):
    from code_mortis import delegated
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    def command(args, *a, **kw):
        data = ({"schema_version": "1", "reviewable_count": 2,
                 "reviewable_files": [{"path": "a.py"}, {"path": "b.py"}]}
                if "preview" in args else
                {"schema_version": "1", "groups": [{"files": [args[-1]], "rule": "Review code"}]})
        return 0, json.dumps(data), ""
    monkeypatch.setattr(ocr, "command", command)
    monkeypatch.setattr(ocr, "git", lambda *a, **kw: "value = 1\n")
    calls = []
    def native(connection, identity, payload, stopped):
        calls.append(payload)
        assert payload["prompt_token_ceiling"] == 200000
        assert payload["token_budget"] == 200000
        if len(calls) == 1:
            raise runner_client.RunnerBlocked("prompt_limit", {"tokens": 0})
        return {"comments": []}, {"tokens": 25}
    monkeypatch.setattr(runner_client, "review", native)
    progress = []
    used = 800000
    status, report, comments = delegated.review(tmp_path, {}, "a" * 40, "b" * 40, "a" * 40,
        {"id": "connection", "kind": "chatgpt", "model": "model", "options": {"prompt_token_ceiling": 200000}},
        RepoSettings(delegated_context_bytes=byte_budget).model_dump(), "run", lambda: False, progress.append,
        {"context_bytes": used, "total_tokens": 0})
    assert not comments and report["limits"]["token_budget"] == 200000
    assert status == "partial" and len(calls) == 2
    assert report["errors"] == [{"path": "a.py", "message": str(runner_client.RunnerBlocked("prompt_limit"))}]
    assert report["summary"]["total_tokens"] == 25
    assert report["limits"]["context_bytes"] == used + sum(len(call["prompt"].encode()) + 4096 for call in calls)
    assert progress[-1]["completed"] == 1 and progress[-1]["failed"] == 1


@pytest.mark.parametrize("budget", [1000, None])
@pytest.mark.parametrize("failure", [None, "unknown", "quota"])
def test_subscription_token_budget_counts_failed_calls_and_survives_resume(tmp_path, monkeypatch, budget, failure):
    from code_mortis import delegated
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    paths = ["a.py", "b.py", "c.py"]

    def command(args, *a, **kw):
        data = ({"schema_version": "1", "reviewable_count": len(paths),
                 "reviewable_files": [{"path": path} for path in paths]}
                if "preview" in args else
                {"schema_version": "1", "groups": [{"files": [args[-1]], "rule": "Review code"}]})
        return 0, json.dumps(data), ""

    monkeypatch.setattr(ocr, "command", command)
    monkeypatch.setattr(ocr, "git", lambda *a, **kw: "value = 1\n")
    calls, checkpoints = [], []

    def native(connection, identity, payload, stopped):
        calls.append(payload)
        if len(calls) == 2:
            if failure == "unknown":
                raise runner_client.RunnerBlocked("failed")
            if failure == "quota":
                raise runner_client.RunnerBlocked("quota", {"tokens": 30})
            if budget is not None:
                raise runner_client.RunnerBlocked("token_limit", {"tokens": 300})
        return {"comments": []}, {"tokens": 800 if len(calls) == 1 else 300 if len(calls) == 2 else 10}

    monkeypatch.setattr(runner_client, "review", native)
    args = (tmp_path, {}, "a" * 40, "b" * 40, "a" * 40,
            {"id": "connection", "kind": "chatgpt", "model": "model"},
            RepoSettings(token_budget=budget).model_dump(), "run", lambda: False, checkpoints.append)
    if failure == "quota":
        with pytest.raises(runner_client.RunnerBlocked):
            delegated.review(*args, {})
        assert checkpoints[-1]["total_tokens"] == 830
        status, report, _ = delegated.review(*args, checkpoints[-1])
        assert status == "completed" and len(calls) == 4
        assert report["summary"]["total_tokens"] == 850
        assert calls[2]["token_budget"] == (None if budget is None else 170)
    else:
        status, report, _ = delegated.review(*args, {})
        assert status == ("completed" if failure is None and budget is None else "partial")
        assert report["summary"]["total_tokens"] == (None if failure else 1100 if budget else 1110)
        assert len(calls) == (2 if budget is not None else 3)
        assert report["summary"]["budget_exceeded"] == (failure is None and budget is not None)
        assert report["manifest"]["coverage"]["completed"][0]["path"] == "a.py"
        if failure == "unknown" and budget is not None:
            delegated.review(*args, checkpoints[-1])
            assert len(calls) == 2, "Unknown consumption must not grant a fresh allowance"
    assert calls[0]["token_budget"] == budget
    assert calls[1]["token_budget"] == (None if budget is None else 200)


@pytest.mark.parametrize("tokens", [None, True, -1])
def test_subscription_legacy_or_invalid_usage_cannot_reset_finite_budget(tmp_path, monkeypatch, tokens):
    from code_mortis import delegated
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    monkeypatch.setattr(ocr, "command", lambda *a, **kw: (0, json.dumps({
        "schema_version": "1", "reviewable_count": 1, "reviewable_files": [{"path": "a.py"}],
    }), ""))
    monkeypatch.setattr(runner_client, "review", lambda *a: pytest.fail("Unknown usage was treated as free"))
    checkpoint = {"context_bytes": 800000}
    if tokens is not None:
        checkpoint["total_tokens"] = tokens
    status, report, _ = delegated.review(tmp_path, {}, "a" * 40, "b" * 40, "a" * 40,
        {"id": "connection", "kind": "chatgpt", "model": "model"}, RepoSettings().model_dump(),
        "run", lambda: False, lambda *a: None, checkpoint)
    assert status == "failed" and report["summary"]["total_tokens"] is None
    assert "remaining budget cannot be verified" in report["errors"][0]["message"]


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("task", ["review", "verification", "assessment", "background"])
def test_native_prompt_limit_rejects_unicode_and_schema_before_auth(monkeypatch, kind, task):
    monkeypatch.setattr(native_runner, "locks", {})
    async def unexpected(*args):
        pytest.fail("An oversized prompt must not authenticate or start inference")
    monkeypatch.setattr(native_runner, "auth_status", unexpected)
    monkeypatch.setattr(native_runner, "run_codex", unexpected)
    monkeypatch.setattr(native_runner, "run_claude", unexpected)
    for prompt, schema in (("🙂" * 15000, {}), ("small", {"description": "x" * 60000})):
        body = native_runner.ReviewBody(kind=kind, task=task, model="model", effort="low",
                                       prompt=prompt, output_schema=schema, timeout=None)
        entry = {}
        asyncio.run(native_runner.execute("connection", body, entry))
        assert entry == {"status": "failed", "reason": "prompt_limit", "usage": {"tokens": 0}}


@pytest.mark.parametrize("kind,runner", [("chatgpt", "run_codex"), ("claude_code", "run_claude")])
def test_native_prompt_limit_boundary_includes_instructions_and_custom_ceiling(monkeypatch, kind, runner):
    monkeypatch.setattr(native_runner, "locks", {})
    calls = []
    async def auth(*args):
        calls.append("auth")
        return {"status": "connected"}
    async def inference(*args):
        calls.append("inference")
        args[3]["tokens"] = 25
        return {"comments": []}
    monkeypatch.setattr(native_runner, "auth_status", auth)
    monkeypatch.setattr(native_runner, runner, inference)
    body = native_runner.ReviewBody(kind=kind, model="model", effort="low", prompt="a", output_schema={},
                                   timeout=None, prompt_token_ceiling=4096)
    overhead = len(native_runner.task_instructions(body).encode()) + 2
    body.prompt = "a" * (4096 * 4 // 5 - overhead)
    entry = {}
    asyncio.run(native_runner.execute("connection", body, entry))
    assert entry == {"status": "completed", "result": {"comments": []}, "usage": {"tokens": 25}}
    assert calls == ["auth", "inference"]
    calls.clear()
    body.prompt += "a"
    entry = {}
    asyncio.run(native_runner.execute("connection", body, entry))
    assert entry["status"] == "failed" and entry["reason"] == "prompt_limit" and not calls
    body.prompt_token_ceiling = 8192
    entry = {}
    asyncio.run(native_runner.execute("connection", body, entry))
    assert entry["status"] == "completed" and calls == ["auth", "inference"]


@pytest.mark.parametrize("kind,runner", [("chatgpt", "run_codex"), ("claude_code", "run_claude")])
def test_unlimited_native_deadline_polling_and_orphan_cleanup(tmp_path, monkeypatch, kind, runner):
    monkeypatch.setattr(native_runner, "ROOT", tmp_path)
    monkeypatch.setattr(native_runner, "tasks", {})
    monkeypatch.setattr(native_runner, "locks", {})
    clock = [0.0]
    monkeypatch.setattr(native_runner.time, "monotonic", lambda: clock[0])
    identity = "00000000-0000-4000-8000-000000000001"
    body = native_runner.ReviewBody(kind=kind, model="model", effort="low", prompt="review",
                                    output_schema={}, timeout=None)

    async def check():
        started, finished = asyncio.Event(), asyncio.Event()
        async def auth(*args):
            return {"status": "connected"}
        async def inference(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        monkeypatch.setattr(native_runner, "auth_status", auth)
        monkeypatch.setattr(native_runner, runner, inference)
        await native_runner.review_start(identity, "run", body)
        await started.wait()
        entry = native_runner.tasks[identity, "run"]
        # Poll beyond the old two-hour lifetime, then lose the worker.
        clock[0] = 7300
        assert await native_runner.review_status(identity, "run") == {"status": "running", "usage": {"tokens": None}}
        assert entry["last_polled"] == 7300
        ticks = 0
        real_sleep = asyncio.sleep
        async def tick(seconds):
            nonlocal ticks
            assert seconds == 30
            ticks += 1
            clock[0] = 7419 if ticks == 1 else 7420
            await real_sleep(0)
            if ticks == 2:
                assert not entry["task"].done()
            if ticks == 3:
                raise asyncio.CancelledError
        monkeypatch.setattr(native_runner.asyncio, "sleep", tick)
        with pytest.raises(asyncio.CancelledError):
            await native_runner.cancel_orphaned_reviews()
        await asyncio.gather(entry["task"], return_exceptions=True)
        assert finished.is_set() and entry["status"] == "failed" and entry["reason"] == "cancelled"
        assert not native_runner.locks[identity].locked()
        started.clear()
        await native_runner.review_start(identity, "cancel", body)
        await started.wait()
        await native_runner.review_cancel(identity, "cancel")
        assert (identity, "cancel") not in native_runner.tasks and not native_runner.locks[identity].locked()
    asyncio.run(check())
