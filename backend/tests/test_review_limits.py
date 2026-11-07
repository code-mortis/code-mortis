import asyncio
import copy
import fcntl
import json
import os
import sys
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from test_ocr import report
from test_review_flow import new_run

from code_mortis import jobs, native_runner
from code_mortis.core import file_policy
from code_mortis.core.db import now, transaction
from code_mortis.integrations import ocr, runner_client
from code_mortis.models import Job, PullRequest, Repository, ReviewRun
from code_mortis.reviews import policy as review_policy
from code_mortis.reviews import service as reviews
from code_mortis.schemas.common import RepoSettings


def test_settings_defaults_round_trip_and_validation(seed, github_stub, client):
    defaults = RepoSettings()
    limits = ("timeout_seconds", "token_budget", "max_files", "assessment_token_budget",
              "verification_token_budget", "summary_token_budget", "summary_timeout_seconds", "delegated_context_bytes")
    assert all(getattr(defaults, key) is None for key in limits)
    assert defaults.review_sensitivity == "balanced"
    for values in ({}, {"timeout_seconds": None}, {"token_budget": None}, {"max_files": None},
                   {"delegated_context_bytes": None},
                   {"timeout_seconds": 900, "token_budget": 200000, "max_files": 40,
                    "assessment_token_budget": 200000, "verification_token_budget": 200000,
                    "summary_token_budget": 60000, "summary_timeout_seconds": 180, "delegated_context_bytes": 800000},
                   *({"review_sensitivity": value} for value in ("low_noise", "balanced", "broad")),
                   {"timeout_seconds": None, "token_budget": None, "max_files": None, "delegated_context_bytes": None}):
        response = client.put("/api/v1/repositories/22", json={
            "enabled": True, "connection_id": "connection", "settings": values,
        })
        assert response.status_code == 200
        assert response.json()["settings"] == RepoSettings(**values).model_dump()
        with transaction() as db:
            assert db.get(Repository, 22).settings == response.json()["settings"]
    for key, values in {"timeout_seconds": [0, 59, 3601], "token_budget": [0, 999, 5000001],
                        "max_files": [0, -1, 1.5, True], "delegated_context_bytes": [0, 4095, 20000001],
                        "review_sensitivity": [None, "high", 1]}.items():
        for value in values:
            with pytest.raises(ValidationError):
                RepoSettings(**{key: value})


def test_review_sensitivity_snapshots_preserve_settings_and_legacy_runs(seed, github_stub):
    rules = {"rules": [{"path": "**/*.py", "rule": "Keep this project rule", "merge_system_rule": True}],
             "include": ["src/**"], "exclude": ["generated/**"]}
    with transaction() as db:
        repo, pr = db.get(Repository, 22), db.get(PullRequest, "pr")
        repo.settings = {"rules": rules, "background": "Project context"}
        first = reviews.create_run(db, pr, repo, "manual")
        assert first.config["settings"]["review_sensitivity"] == "balanced"
        assert all(first.config["settings"][key] is None for key in (
            "timeout_seconds", "token_budget", "max_files", "assessment_token_budget",
            "verification_token_budget", "summary_token_budget", "summary_timeout_seconds", "delegated_context_bytes",
        ))
        original = copy.deepcopy(first.config)
        repo.settings = {**repo.settings, "review_sensitivity": "low_noise"}
        second = reviews.create_run(db, pr, repo, "manual")
        assert second.config["settings"]["review_sensitivity"] == "low_noise"
        assert first.config == original
        assert repo.settings["rules"] == second.config["settings"]["rules"] == rules
        assert repo.settings["background"] == second.config["settings"]["background"] == "Project context"
        legacy = {key: value for key, value in original["settings"].items() if key != "review_sensitivity"}
        assert review_policy.instructions(legacy) == ""
        assert review_policy.background(legacy) == "Project context"
        assert review_policy.instructions({}) == review_policy.background({}) == ""


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
        assert new.id != old.id and new.config["settings"]["max_files"] is None
        assert old.config["settings"] == original


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("count,maximum", [(39, 40), (40, 40), (41, 40), (41, None)])
@pytest.mark.parametrize("sensitivity", [None, "low_noise", "balanced", "broad"])
def test_engine_file_gate_precedes_inference(kind, count, maximum, sensitivity, tmp_path, monkeypatch):
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
    rules = {"rules": [{"path": "**/*.py", "rule": "Review code", "merge_system_rule": True}],
             "include": ["src/**"], "exclude": ["generated/**"]}

    def git(root, env, *args, **kwargs):
        if args[0] == "diff" and "--name-status" in args:
            return "".join(f"M\0{path}\0" for path in paths)
        return head if args[0] == "rev-parse" else base if args[0] == "merge-base" else "value = 1\n"

    def command(args, root, env, **kwargs):
        assert "--exclude" in args and "--rule" in args
        assert json.loads(Path(args[args.index("--rule") + 1]).read_text()) == file_policy.review_rules(options)
        background = Path(args[args.index("--background-file") + 1]).read_text()
        assert background == review_policy.background(options)
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

    def after_review(result, repo, env, fetch_env):
        assert not configured, "Release OCR configuration before saving or verifying results"
        assert repo.is_dir(), "Keep the checkout available during completion"
        completed.append(result)

    monkeypatch.setattr(ocr, "git", git)
    monkeypatch.setattr(ocr, "command", command)
    monkeypatch.setattr(runner_client, "review", lambda *a, **k: pytest.fail("Subscription inference is retired"))
    options = RepoSettings(timeout_seconds=None, token_budget=None, max_files=maximum,
                           exclusions="*.lock", rules=rules, background="Project context").model_dump()
    if sensitivity is None:
        options.pop("review_sensitivity")
    else:
        options["review_sensitivity"] = sensitivity
    original = copy.deepcopy(options)
    if kind != "api":
        with pytest.raises(ValueError, match="retired"):
            ocr.review("owner/repo", 1, base, head,
                {"id": "connection", "kind": kind, "model": "model"}, {}, options, "token", lambda: False,
                after_review=after_review)
        assert options == original and not previews and not inference and not completed
        return
    status, saved, comments = ocr.review("owner/repo", 1, base, head,
        {"id": "connection", "kind": kind, "model": "model", "options": {"prompt_token_ceiling": 200000}}, {}, options, "token", lambda: False,
        after_review=after_review)
    assert completed == [(status, saved, comments)]
    assert options == original
    assert comments == []
    if maximum is not None and count > maximum:
        assert status == "skipped" and not inference
        assert saved["message"] == "41 reviewable files exceeds the maximum of 40."
        assert saved["summary"]["total_tokens"] == 0
        coverage = saved["manifest"]["coverage"]
        assert not coverage["completed"] and len(coverage["selected"]) == len(coverage["waived"]) == count
    else:
        assert status == "completed" and len(inference) == 1
    assert len(previews) == 1


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
        ocr.review("owner/repo", 1, base, head, {"kind": "api"}, {}, RepoSettings(max_files=40).model_dump(),
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
            with ocr._configuration({}, {}, {}, stopped):
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


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("byte_budget", [4096, 800000, None])
@pytest.mark.parametrize("token_budget", [1000, None])
def test_retired_native_review_limits_never_reach_preflight_or_inference(tmp_path, monkeypatch, kind, byte_budget, token_budget):
    def unexpected(*args, **kwargs):
        pytest.fail("Retired subscription work reached preflight or inference")

    monkeypatch.setattr(ocr, "check_version", unexpected)
    monkeypatch.setattr(ocr, "command", unexpected)
    monkeypatch.setattr(ocr, "git", unexpected)
    monkeypatch.setattr(runner_client, "review", unexpected)
    options = RepoSettings(delegated_context_bytes=byte_budget, token_budget=token_budget).model_dump()
    original = copy.deepcopy(options)
    with pytest.raises(ValueError, match="retired"):
        ocr.review("owner/repo", 1, "a" * 40, "b" * 40,
            {"id": "connection", "kind": kind, "model": "model", "options": {"prompt_token_ceiling": 200000}},
            {}, options, "token", lambda: False, before_analysis=unexpected, after_review=unexpected)
    assert options == original


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("budget", [1000, None])
@pytest.mark.parametrize("tokens", [None, True, -1, 830])
def test_retired_subscription_checkpoint_cannot_resume_or_reset_usage(tmp_path, monkeypatch, kind, budget, tokens):
    def unexpected(*args, **kwargs):
        pytest.fail("Retired checkpoint resumed work")

    monkeypatch.setattr(ocr, "command", unexpected)
    monkeypatch.setattr(ocr, "git", unexpected)
    monkeypatch.setattr(runner_client, "review", unexpected)
    checkpoint = {"context_bytes": 800000, "total_tokens": tokens,
                  "tasks": {"a.py": {"status": "completed", "tokens": 800},
                            "b.py": {"status": "failed", "reason": "quota", "tokens": None}}}
    original = copy.deepcopy(checkpoint)
    with pytest.raises(ValueError, match="retired"):
        ocr.review("owner/repo", 1, "a" * 40, "b" * 40,
            {"id": "connection", "kind": kind, "model": "model"}, {},
            RepoSettings(token_budget=budget).model_dump(), "token", lambda: False,
            run_id="run", checkpoint=checkpoint, progress=unexpected)
    assert checkpoint == original


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("task", ["review", "verification", "assessment", "background", "summary", "security_analysis", "security_verification"])
def test_retired_native_runner_rejects_all_prompt_sizes_before_auth(monkeypatch, kind, task):
    async def unexpected(*args):
        pytest.fail("Retired prompt authenticated or started inference")

    monkeypatch.setattr(native_runner, "locks", {})
    monkeypatch.setattr(native_runner, "auth_status", unexpected)
    monkeypatch.setattr(native_runner, "run_codex", unexpected)
    monkeypatch.setattr(native_runner, "run_claude", unexpected)
    for prompt, schema, ceiling in (("small", {}, 4096), ("🙂" * 15000, {}, 58888),
                                    ("small", {"description": "x" * 60000}, 200000)):
        body = native_runner.ReviewBody(kind=kind, task=task, model="model", effort="low",
                                       prompt=prompt, output_schema=schema, timeout=None, prompt_token_ceiling=ceiling)
        entry = {}
        asyncio.run(native_runner.execute("connection", body, entry))
        assert entry == {"status": "failed", "reason": "retired", "usage": {"tokens": 0}}
    assert native_runner.locks == {}


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
def test_retired_runner_rejects_new_work_and_still_cleans_up_legacy_tasks(tmp_path, monkeypatch, kind):
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
        entry = {"status": "running", "usage": {"tokens": None}, "created": 0, "last_polled": 0}
        lock = native_runner.locks.setdefault(identity, asyncio.Lock())

        async def legacy_task():
            async with lock:
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    entry.update(status="failed", reason="cancelled")
                    raise
                finally:
                    finished.set()

        # Existing task cleanup survives the cutover without admitting another model request.
        entry["task"] = asyncio.create_task(legacy_task())
        native_runner.tasks[identity, "run"] = entry
        await started.wait()
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
        assert entry["usage"] == {"tokens": None} and not lock.locked()
        await native_runner.review_cancel(identity, "run")
        assert (identity, "run") not in native_runner.tasks
        with pytest.raises(HTTPException) as caught:
            await native_runner.review_start(identity, "new", body)
        assert caught.value.status_code == 410 and caught.value.detail == "retired"
        assert not native_runner.tasks and not lock.locked()

    asyncio.run(check())
