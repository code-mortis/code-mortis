import asyncio
import fcntl
import json
import os
import sys
from contextlib import nullcontext
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
    for values in ({"timeout_seconds": None}, {"token_budget": None}, {"max_files": None},
                   {"timeout_seconds": None, "token_budget": None, "max_files": None}):
        response = client.put("/api/v1/repositories/22", json={
            "enabled": True, "connection_id": "connection", "settings": values,
        })
        assert response.status_code == 200
        assert response.json()["settings"] == RepoSettings(**values).model_dump()
        with transaction() as db:
            assert db.get(Repository, 22).settings == response.json()["settings"]
    for key, values in {"timeout_seconds": [0, 59, 3601], "token_budget": [0, 999, 5000001],
                        "max_files": [0, -1, 1.5, True]}.items():
        for value in values:
            with pytest.raises(ValidationError):
                RepoSettings(**{key: value})


def test_old_snapshots_keep_unlimited_files_and_deduplicate_only_matching_scope(seed, github_stub):
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
        new = reviews.create_run(db, pr, repo, "automatic")
        assert new.id != old.id and new.config["settings"]["max_files"] == 40
        assert old.config["settings"] == original


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("count,maximum", [(39, 40), (40, 40), (41, 40), (41, None)])
def test_engine_file_gate_precedes_inference(kind, count, maximum, tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.settings(), "work_dir", tmp_path)
    monkeypatch.setattr(ocr, "check_version", lambda *a: None)
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    base, head = "a" * 40, "b" * 40
    paths = [f"source{index}.py" for index in range(count)]
    inference, previews = [], []

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
        inference.append(payload)
        return {"comments": []}

    monkeypatch.setattr(ocr, "git", git)
    monkeypatch.setattr(ocr, "command", command)
    monkeypatch.setattr(runner_client, "review", native)
    options = RepoSettings(timeout_seconds=None, token_budget=None, max_files=maximum,
                           exclusions="*.lock", rules={"default_rule": "Review code"}).model_dump()
    status, saved, comments = ocr.review("owner/repo", 1, base, head,
        {"id": "connection", "kind": kind, "model": "model"}, {}, options, "token", lambda: False)
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
        assert await native_runner.review_status(identity, "run") == {"status": "running"}
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
