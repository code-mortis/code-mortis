import copy
import json
import os
import signal
import subprocess
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_pr_planning import plan, write
from test_pr_planning import repository as repository
from test_review_flow import new_run

from code_mortis import jobs
from code_mortis.core import process
from code_mortis.core.db import now, transaction
from code_mortis.core.errors import ReviewStopped
from code_mortis.models import Job, PullRequest, Repository, ReviewRun
from code_mortis.reviews import engine, planning
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


def test_unlimited_subprocess_completes_and_remains_cancellable(tmp_path):
    assert process.command([sys.executable, "-c", "print('done')"], tmp_path, os.environ.copy(), timeout=None)[1] == "done\n"
    with pytest.raises(ReviewStopped):
        process.command([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, os.environ.copy(),
                    timeout=None, stopped=lambda: True)
    with pytest.raises(TimeoutError):
        process.command([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, os.environ.copy(), timeout=0)


@pytest.mark.parametrize("exiting_signal", [signal.SIGTERM, signal.SIGKILL])
@pytest.mark.parametrize("error", [ReviewStopped("cancelled"), TimeoutError("deadline"), ValueError("output limit")])
def test_subprocess_exit_race_preserves_original_error_and_reaps_child(tmp_path, monkeypatch, exiting_signal, error):
    waits, signals = [], []

    def wait(timeout=None):
        waits.append(timeout)
        if timeout == 3 and exiting_signal == signal.SIGKILL:
            raise subprocess.TimeoutExpired("child", timeout)
        return 0

    def killpg(pid, requested):
        signals.append(requested)
        if requested == exiting_signal:
            raise ProcessLookupError()

    def interrupted():
        raise error

    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=123, poll=lambda: None, wait=wait))
    monkeypatch.setattr(process.os, "killpg", killpg)
    with pytest.raises(type(error)) as caught:
        process.command(["child"], tmp_path, {}, timeout=None, poll=interrupted)
    assert caught.value is error
    assert waits == ([3] if exiting_signal == signal.SIGTERM else [3, None])
    assert signals == ([signal.SIGTERM] if exiting_signal == signal.SIGTERM else [signal.SIGTERM, signal.SIGKILL])


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


@pytest.mark.parametrize("count,maximum", [(39, 40), (40, 40), (41, 40), (41, None)])
@pytest.mark.parametrize("sensitivity", [None, "low_noise", "balanced", "broad"])
def test_engine_file_gate_precedes_source_and_inference(repository, count, maximum, sensitivity, monkeypatch):
    repo, _base = repository
    for number in range(count):
        write(repo, f"source{number}.py", "value = 1\n")
    write(repo, "ignored.lock", "excluded\n")
    options = RepoSettings(max_files=maximum, exclusions="*.lock", background="Project context",
                           rules={"rules": [{"path": "**/*.py", "rule": "Review code", "merge_system_rule": True}]}).model_dump()
    if sensitivity is None:
        options.pop("review_sensitivity")
    else:
        options["review_sensitivity"] = sensitivity
    original = copy.deepcopy(options)
    if maximum is not None and count > maximum:
        monkeypatch.setattr(planning.source, "read_source", lambda *a, **k: pytest.fail("Over-limit scope read source"))
    selected = plan(repository, **options)
    assert len(selected.selected) == count and options == original
    assert "ignored.lock" in {row["path"] for row in selected.exclusions}
    batches = engine.prepare_batches(selected, {"kind": "api", "model": "fixture", "protocol": "openai"}, options, {})
    if maximum is not None and count > maximum:
        assert selected.limit_reason == "41 reviewable files exceeds the maximum of 40."
        assert not selected.units and not batches
        status, saved, comments = engine._report(selected, batches, {}, tokens=0)
        coverage = saved["manifest"]["coverage"]
        assert status == "skipped" and comments == [] and saved["summary"]["total_tokens"] == 0
        assert len(coverage["selected"]) == len(coverage["waived"]) == count
        assert not coverage["completed"]
    else:
        assert not selected.limit_reason and batches
        payload = json.loads(batches[0].request.messages[0].content)
        assert payload["background"] == "Project context"
        assert all("Review code" in unit["rules"] for unit in payload["assigned_units"])
