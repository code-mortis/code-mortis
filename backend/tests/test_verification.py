"""Verification must have positive evidence and an unchanged, bot-owned target."""

import copy
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import func, select
from test_review_flow import COMMENT, new_run, remote

from code_mortis import checks, github, jobs, ocr, pipeline, reviews, runner_client, verification
from code_mortis.db import now, transaction
from code_mortis.github import threads as github_threads
from code_mortis.models import (
    Activity,
    Connection,
    ConnectionGrant,
    Finding,
    FindingVerification,
    Job,
    Occurrence,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.schemas import RepoSettings

HEAD = "c" * 40
CONTEXT = {
    "original_sha": "a" * 40, "head_sha": HEAD,
    "sources": {"original": {"src/retry.py": "while True:\n    retry()"},
                "current": {"src/retry.py": "for attempt in range(3):\n    retry()"}},
    "diff": "-while True:\n+for attempt in range(3):",
}
RESULT = {
    "verdict": "fixed", "original_issue_present": True,
    "explanation": "The original unbounded loop could retry forever.", "change_explanation": "The infinite loop became a loop of three attempts.",
    "dismissal_recommended": False,
    "evidence": [
        {"revision": revision, "path": "src/retry.py", "start_line": 1, "end_line": 1, "quote": source["src/retry.py"].splitlines()[0]}
        for revision, source in CONTEXT["sources"].items()
    ],
}


@pytest.fixture
def scenario(seed, github_stub, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    monkeypatch.setattr(ocr, "review", lambda *a, **k: ("completed", {"summary": {"total_tokens": 100}}, [copy.deepcopy(COMMENT)]))
    first = new_run()
    reviews.perform_review(first)
    with transaction() as db:
        finding = db.scalar(select(Finding))
        finding.thread_id, finding.comment_id = "thread", 12
        fid = finding.id
        db.get(Repository, 22).settings = {"automatic": True, "auto_resolve": True}
        db.get(PullRequest, "pr").head_sha = HEAD
    thread = {"id": "thread", "isResolved": False, "isOutdated": True, "resolvedBy": None,
              "comments": {"pageInfo": {"hasNextPage": False}, "nodes": [
                  {"databaseId": 12, "body": reviews.marker("finding", fid), "url": "https://github.com/thread",
                   "author": {"login": "mortis-test[bot]"}, "updatedAt": "2026-10-07T10:00:00Z"}
              ]}}
    monkeypatch.setattr(github, "threads", lambda *a: [copy.deepcopy(thread)])
    monkeypatch.setattr(github, "request", lambda *a, **k: remote(HEAD))
    with transaction() as db:
        run = reviews.create_run(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "automatic")
        # Historical runs retain the shared-budget, sequential verification contract.
        run.config = {key: value for key, value in run.config.items() if key != "pipeline_version"}
        run.stages = {}
        db.flush()
        run_id = run.id
    calls = []

    def engine(*args, **kwargs):
        result = kwargs.get("saved_review") or ("completed", {"summary": {"total_tokens": 100}}, [])
        if not kwargs.get("saved_review"):
            calls.append("review")
        kwargs["after_review"](result, Path("/tmp/repo"), {}, {})
        return result

    monkeypatch.setattr(ocr, "review", engine)
    monkeypatch.setattr(verification, "build_context", lambda *a: copy.deepcopy(CONTEXT))

    def verify(*args):
        calls.append("verify")
        return copy.deepcopy(RESULT), {"tokens": 50}

    monkeypatch.setattr(verification, "api_verify", verify)
    mutations = []

    def set_thread(token, identity, resolved):
        assert identity == "thread"
        with transaction() as db:
            saved = db.scalar(select(FindingVerification))
            assert saved.result and saved.context
            if resolved:
                assert saved.resolution in {"applying", "superseded"}
        mutations.append(resolved)
        thread["isResolved"] = resolved
        thread["resolvedBy"] = {"login": "mortis-test[bot]"} if resolved else None
        operation = "resolveReviewThread" if resolved else "unresolveReviewThread"
        return {operation: {"thread": {"id": identity, "isResolved": resolved}}}

    monkeypatch.setattr(github, "set_thread", set_thread)
    return {"run": run_id, "finding": fid, "thread": thread, "calls": calls, "mutations": mutations, "set_thread": set_thread}


def saved():
    with transaction() as db:
        row = db.scalar(select(FindingVerification))
        finding = db.get(Finding, row.finding_id)
        return row, finding, db.get(ReviewRun, row.run_id)


def perform(scenario):
    reviews.perform_review(scenario["run"])
    return saved()


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("kind", ["api", "chatgpt"])
def test_manual_reviews_verify_and_resolve_without_automatic_review_grant(scenario, monkeypatch, automatic, kind):
    with transaction() as db:
        repo, run = db.get(Repository, 22), db.get(ReviewRun, scenario["run"])
        repo.settings = {"automatic": automatic, "auto_resolve": True}
        run.trigger, run.actor_id = "manual", 7
        if kind != "api":
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
            run.owner_id = 7
            run.config = {**run.config, "connection": reviews.connection_snapshot(connection)}
    monkeypatch.setattr(runner_client, "review", lambda *a, **k: (copy.deepcopy(RESULT), {"tokens": 50}))
    row, finding, run = perform(scenario)
    assert row.status == "completed" and row.resolution == "pending"
    reviews.synchronize_finding(finding.sync_intent)
    row, finding, _ = saved()
    assert finding.status == "resolved" and row.resolution == "resolved"
    assert scenario["mutations"] == [True]


@pytest.mark.parametrize("previous_status", ["failed", "skipped", "completed", "running"])
def test_new_manual_run_retries_unsuccessful_verification_with_current_settings(scenario, monkeypatch, previous_status):
    with transaction() as db:
        old, repo = db.get(ReviewRun, scenario["run"]), db.get(Repository, 22)
        verification.prepare(db, old, repo, "token")
        db.flush()
        previous = db.scalar(select(FindingVerification))
        previous.status, previous.error, previous.usage = previous_status, "Previous attempt", {"tokens": 19}
        previous_id = previous.id
        old.status = "completed"
        repo.settings = {"automatic": False, "auto_resolve": True, "verification_token_budget": None,
                         "timeout_seconds": None, "language": "Spanish", "model": "current-model"}
        run = reviews.create_run(db, db.get(PullRequest, "pr"), repo, "manual", db.get(User, 7))
        db.flush()
        identity = run.id
        verification.prepare(db, run, repo, "token")
        verification.prepare(db, run, repo, "token")
        db.flush()
        attempts = list(db.scalars(select(FindingVerification).where(FindingVerification.run_id == identity)))
        assert len(attempts) == (1 if previous_status in {"failed", "skipped"} else 0)
        run.status, run.started_at = "running", now()
        pipeline.enqueue_stages(db, run)
        job = db.scalar(select(Job).where(Job.kind == "verify_fixes", Job.payload["run_id"].as_string() == identity))
        job_id = job.id if job else None
    calls = []

    def verify(*args):
        calls.append((args[2]["model"], args[5], args[6]))
        return copy.deepcopy(RESULT), {"tokens": 50}

    monkeypatch.setattr(verification, "api_verify", verify)
    if job_id:
        jobs.execute_job.run(job_id)
        jobs.execute_job.run(job_id)
    assert calls == ([("current-model", None, None)] if job_id else [])
    with transaction() as db:
        previous = db.get(FindingVerification, previous_id)
        assert (previous.status, previous.error, previous.usage) == (previous_status, "Previous attempt", {"tokens": 19})
        run = db.get(ReviewRun, identity)
        assert run.config["settings"]["language"] == "Spanish"
        if job_id:
            attempt = db.scalar(select(FindingVerification).where(FindingVerification.run_id == identity))
            assert attempt.status == "completed" and attempt.usage["tokens"] == 50
            assert run.stages["verification"]["status"] == "completed"


def test_retry_migration_preserves_previous_attempt(scenario):
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        verification.prepare(db, run, db.get(Repository, 22), "token")
        db.flush()
        row = db.scalar(select(FindingVerification))
        row.status, row.error, row.usage = "failed", "Original failure", {"tokens": None, "reserved_tokens": 200000}
        identity = row.id
    config = Config("alembic.ini")
    command.downgrade(config, "20261017_parallel_stages")
    command.upgrade(config, "head")
    with transaction() as db:
        row = db.get(FindingVerification, identity)
        assert row.status == "failed" and row.error == "Original failure"
        assert row.usage == {"tokens": None, "reserved_tokens": 200000}
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 1


@pytest.mark.parametrize("verdict", ["fixed", "still_present", "uncertain"])
def test_explicit_verdict_and_immutable_history(scenario, monkeypatch, verdict):
    result = {**RESULT, "verdict": verdict}
    monkeypatch.setattr(verification, "api_verify", lambda *a: (result, {"tokens": 50}))
    row, finding, run = perform(scenario)
    assert row.verdict == verdict and row.status == "completed" and finding.status == "open"
    assert row.context == CONTEXT and run.status == "completed"
    assert finding.last_seen_run_id != run.id  # Not observed remains independent from verification.
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 1
        assert db.scalar(select(Occurrence)).snapshot["content"] == COMMENT["content"]
    if verdict == "fixed":
        assert row.resolution == "pending" and finding.desired_status == "resolved"
        reviews.synchronize_finding(finding.sync_intent)
        reviews.synchronize_finding(finding.sync_intent)
        row, finding, _ = saved()
        assert finding.status == "resolved" and row.resolution == "resolved"
        assert scenario["mutations"] == [True]
        with transaction() as db:
            assert db.scalar(select(func.count()).select_from(Activity).where(Activity.action == "automatically_resolved")) == 1
    else:
        assert finding.desired_status is None and not scenario["mutations"]


def test_run_detail_lists_only_confirmed_resolutions_from_this_run(scenario, client, monkeypatch):
    row, finding, run = perform(scenario)
    path = f"/api/v1/runs/{run.id}/resolved"
    assert row.resolution == "pending"
    assert client.get(path).json()["items"] == []
    reviews.synchronize_finding(finding.sync_intent)
    resolved = client.get(path).json()["items"]
    assert [(item["id"], item["status"]) for item in resolved] == [(finding.id, "resolved")]
    assert resolved[0]["content"] == COMMENT["content"]
    assert client.get(f"/api/v1/runs/{finding.last_seen_run_id}").json()["resolved_count"] == 0
    assert client.get(f"/api/v1/runs/{run.id}").json()["resolved_count"] == 1
    with transaction() as db:
        db.get(Finding, finding.id).status = "open"
        db.add(Finding(
            id="manual", pr_id=finding.pr_id, fingerprint="manual", status="resolved",
            path="src/manual.py", content="Manually resolved issue", start_line=1, end_line=1,
            last_seen_run_id=finding.last_seen_run_id,
        ))
    resolved = client.get(path).json()["items"]
    assert [(item["id"], item["status"]) for item in resolved] == [(finding.id, "open")]
    with transaction() as db:
        assert db.get(FindingVerification, row.id).resolution == "resolved"

    def denied(*args, **kwargs):
        raise HTTPException(403, "Repository access revoked")

    monkeypatch.setattr(github, "authorize_repo", denied)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("change", [
    {"original_issue_present": False}, {"dismissal_recommended": True},
    {"change_explanation": ""}, {"evidence": RESULT["evidence"][:1]},
])
def test_unsupported_fix_becomes_uncertain(change):
    assert verification.validate_result({**RESULT, **change}, CONTEXT)["verdict"] == "uncertain"


@pytest.mark.parametrize("change", [{"quote": "made up"}, {"path": "../secret"}, {"end_line": 20}])
def test_evidence_must_match_exact_supplied_objects(change):
    data = copy.deepcopy(RESULT)
    data["evidence"][0].update(change)
    with pytest.raises(ValueError):
        verification.validate_result(data, CONTEXT)


@pytest.mark.parametrize("quote,start_line", [("retry()", 2), ("Retry()", 2), ("retry()", 1), ("   ", 2)])
def test_partial_line_quotes_preserve_literal_text_and_source_location(quote, start_line):
    data = copy.deepcopy(RESULT)
    data["evidence"][1].update(start_line=start_line, end_line=start_line, quote=quote)
    if quote == "retry()" and start_line == 2:
        assert verification.validate_result(data, CONTEXT)["verdict"] == "fixed"
    else:
        with pytest.raises(ValueError):
            verification.validate_result(data, CONTEXT)


@pytest.mark.parametrize("mode", [
    "valid", "unrelated_empty", "unrelated_truncated", "missing", "multiple", "wrong_repo", "wrong_task",
    "malformed", "nonobject", "empty", "wrong_session", "wrong_response_session", "missing_session",
])
def test_terminal_result_is_scoped_to_one_explicit_response(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / ".opencodereview/sessions/repo"
    folder.mkdir(parents=True)
    root = tmp_path / "repo"
    session_id = "00000000-0000-4000-8000-000000000001"
    record = {"type": "llm_response", "sessionId": session_id, "taskType": "main_task", "tool_calls": [{
        "name": "task_done", "arguments": json.dumps({"state": "DONE", "result": RESULT}),
    }]}
    if mode == "multiple":
        record["tool_calls"] *= 2
    elif mode == "missing":
        record["tool_calls"][0]["arguments"] = '{"state":"DONE"}'
    elif mode == "wrong_task":
        record["taskType"] = "review_filter_task"
    elif mode == "malformed":
        record["tool_calls"][0]["arguments"] = "invalid"
    elif mode == "nonobject":
        record["tool_calls"][0]["arguments"] = "null"
    elif mode == "wrong_response_session":
        record["sessionId"] = "another-session"
    header = {"type": "session_start", "sessionId": session_id,
              "cwd": str(root) if mode != "wrong_repo" else "/another/repo"}
    if mode == "wrong_session":
        header["sessionId"] = "another-session"
    (folder / f"{session_id}.jsonl").write_text(
        "" if mode == "empty" else json.dumps(header) + "\n" + json.dumps(record) + "\n",
    )
    if mode.startswith("unrelated_"):
        (folder / "interrupted.jsonl").write_text("{" if mode == "unrelated_truncated" else "")
    if mode == "valid" or mode.startswith("unrelated_"):
        assert verification.terminal_result(root, session_id) == RESULT
    else:
        with pytest.raises(ValueError):
            verification.terminal_result(root, None if mode == "missing_session" else session_id)


def test_unchanged_code_or_suggestion_match_is_not_fix_proof():
    assert verification.validate_result(RESULT, {**CONTEXT, "diff": ""})["verdict"] == "uncertain"


@pytest.mark.parametrize("failure", [ValueError("missing context"), runner_client.RunnerBlocked("quota"), TimeoutError("time limit"), RuntimeError("provider-secret")])
def test_verification_failures_preserve_new_review(scenario, monkeypatch, failure):
    def fail(*a):
        raise failure
    monkeypatch.setattr(verification, "api_verify", fail)
    row, finding, run = perform(scenario)
    assert row.verdict == "uncertain" and row.status == "failed"
    assert finding.status == "open" and finding.desired_status is None
    assert run.status == "completed" and run.report["summary"]["total_tokens"] == 100
    assert "provider-secret" not in row.error


@pytest.mark.parametrize("mutation", ["head", "closed", "manual", "discussion", "author", "disabled", "base"])
def test_changed_target_never_resolves(scenario, monkeypatch, mutation):
    row, finding, _ = perform(scenario)
    intent = finding.sync_intent
    if mutation in {"head", "closed", "base"}:
        data = remote("d" * 40 if mutation == "head" else HEAD)
        if mutation == "closed":
            data["state"] = "closed"
        if mutation == "base":
            data["base"]["sha"] = "e" * 40
        monkeypatch.setattr(github, "request", lambda *a, **k: data)
    elif mutation == "manual":
        with transaction() as db:
            reviews.request_finding_status(db, db.get(Finding, finding.id), "open", db.get(User, 7), "Keep open")
    elif mutation == "disabled":
        with transaction() as db:
            db.get(Repository, 22).settings = {"auto_resolve": False}
    elif mutation == "author":
        scenario["thread"]["comments"]["nodes"][0]["author"]["login"] = "someone"
    else:
        scenario["thread"]["comments"]["nodes"][0]["updatedAt"] = "later"
    reviews.synchronize_finding(intent)
    assert scenario["mutations"] == []
    assert saved()[1].status == "open"


def test_github_failure_is_pending_and_retry_does_not_analyze(scenario, monkeypatch, client):
    row, finding, _ = perform(scenario)
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.kind == "resolve"))
        jid = job.id
    def fail(*a):
        raise github.GitHubError(503, "GitHub unavailable")
    monkeypatch.setattr(github, "set_thread", fail)
    jobs.execute_job(jid)
    row, finding, _ = saved()
    assert finding.status == "open" and finding.desired_status == "resolved" and finding.sync_error
    assert row.resolution == "failed"
    calls = list(scenario["calls"])
    response = client.post(f"/api/v1/findings/{finding.id}/sync")
    assert response.status_code == 202
    monkeypatch.setattr(github, "set_thread", scenario["set_thread"])
    jobs.execute_job(response.json()["job_id"])
    assert saved()[1].status == "resolved" and scenario["calls"] == calls


def test_lost_success_response_recovers_without_duplicate_mutation(scenario, monkeypatch):
    _, finding, _ = perform(scenario)
    def lost(*args):
        scenario["set_thread"](*args)
        raise github.GitHubError(502, "Response lost")
    monkeypatch.setattr(github, "set_thread", lost)
    with transaction() as db:
        jid = db.scalar(select(Job.id).where(Job.kind == "resolve"))
    jobs.execute_job(jid)
    assert saved()[1].status == "open"
    monkeypatch.setattr(github, "set_thread", lambda *a: pytest.fail("Resolved twice"))
    reviews.synchronize_finding(finding.sync_intent)
    assert saved()[1].status == "resolved" and scenario["mutations"] == [True]


def test_push_during_mutation_compensates_without_claiming_fixed(scenario, monkeypatch):
    _, finding, _ = perform(scenario)
    def changed(*args):
        response = scenario["set_thread"](*args)
        monkeypatch.setattr(github, "request", lambda *a, **k: remote("d" * 40))
        return response
    monkeypatch.setattr(github, "set_thread", changed)
    reviews.synchronize_finding(finding.sync_intent)
    assert scenario["mutations"] == [True, False]
    assert saved()[1].status == "open" and saved()[0].resolution == "superseded"


def test_signed_manual_cycle_invalidates_even_before_reconciliation(scenario, client):
    _, finding, _ = perform(scenario)
    version = finding.sync_version
    for action in ["resolved", "unresolved"]:
        body = json.dumps({"action": action, "installation": {"id": 10}, "repository": {"id": 22},
                           "pull_request": {"number": 3}, "thread": {"node_id": "thread"},
                           "sender": {"login": "owner"}}).encode()
        headers = {"x-github-event": "pull_request_review_thread", "x-github-delivery": action,
                   "x-hub-signature-256": "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()}
        assert client.post("/api/v1/webhooks/github", content=body, headers=headers).status_code == 202
        assert client.post("/api/v1/webhooks/github", content=body, headers=headers).json()["duplicate"]
    assert saved()[1].sync_version == version + 2
    reviews.synchronize_finding(finding.sync_intent)
    assert not scenario["mutations"]


def test_reopen_and_same_commit_review_cannot_reuse_verdict(scenario, client):
    _, finding, _ = perform(scenario)
    reviews.synchronize_finding(finding.sync_intent)
    assert client.patch(f"/api/v1/findings/{finding.id}", json={"status": "open"}).status_code == 202
    with transaction() as db:
        finding = db.get(Finding, finding.id)
        intent = finding.sync_intent
    reviews.synchronize_finding(intent)
    assert saved()[1].status == "open"
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        run.progress = {**run.progress, "verification_prepared": False}
        verification.prepare(db, run, db.get(Repository, 22), "token")
        assert db.scalar(select(func.count()).select_from(FindingVerification)) == 1


def test_concurrent_sync_claims_once(scenario, monkeypatch):
    _, finding, _ = perform(scenario)
    entered, release = Event(), Event()
    def waiting(*args):
        entered.set()
        assert release.wait(5)
        return scenario["set_thread"](*args)
    monkeypatch.setattr(github, "set_thread", waiting)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reviews.synchronize_finding, finding.sync_intent)
        assert entered.wait(5)
        second = pool.submit(reviews.synchronize_finding, finding.sync_intent)
        second.result(timeout=5)
        release.set()
        first.result(timeout=5)
    assert scenario["mutations"] == [True]


def test_resume_reuses_review_and_saved_verification(scenario):
    row, _, _ = perform(scenario)
    calls = list(scenario["calls"])
    with transaction() as db:
        db.get(ReviewRun, scenario["run"]).status = "running"
    reviews.perform_review(scenario["run"])
    assert scenario["calls"] == calls and saved()[0].id == row.id


def test_interrupted_verification_reservation_is_not_reissued(scenario):
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        verification.prepare(db, run, db.get(Repository, 22), "token")
        db.flush()
        row = db.scalar(select(FindingVerification))
        row.status, row.usage = "running", {"tokens": 199900}
    row, finding, _ = perform(scenario)
    assert row.status == "failed" and row.verdict == "uncertain"
    assert finding.status == "open" and scenario["calls"] == ["review"]


@pytest.mark.parametrize("limit", ["tokens", "time", "automatic"])
def test_existing_limits_never_reset(scenario, limit):
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        if limit == "tokens":
            run.config = {**run.config, "settings": {**run.config["settings"], "token_budget": 100}}
        elif limit == "time":
            run.started_at = now() - timedelta(hours=1)
        else:
            db.get(Repository, 22).settings = {"auto_resolve": True, "automatic": False}
    reviews.perform_review(scenario["run"])
    assert "verify" not in scenario["calls"] and not scenario["mutations"]


@pytest.mark.parametrize("tokens,seconds,reason", [
    (5232697, 300, "Token budget exhausted: 5,232,697 / 5,000,000 tokens used or reserved."),
    (None, 300, "Token usage is unavailable or invalid; remaining budget cannot be verified against the 5,000,000-token limit."),
    (100, 5, "Time limit reached: 5 seconds remain of the 3,600-second run limit; at least 10 seconds are required."),
])
def test_verification_reports_the_specific_limit(scenario, monkeypatch, tokens, seconds, reason):
    current_time = now()
    monkeypatch.setattr(verification, "now", lambda: current_time)
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        run.config = {**run.config, "settings": {**run.config["settings"], "token_budget": 5000000, "timeout_seconds": 3600}}
        run.started_at = current_time - timedelta(seconds=3600 - seconds)
        run.progress = {"analysis_saved": True, "analysis_status": "partial"}
        run.report = {"summary": {"total_tokens": tokens}}
    row, finding, _ = perform(scenario)
    assert row.status == "skipped" and row.error == reason + " Fix verification skipped."
    assert finding.status == "open" and "verify" not in scenario["calls"] and not scenario["mutations"]


@pytest.mark.parametrize("tokens,timeout", [(None, None), (None, 900), (200000, None)])
def test_unlimited_verification_preserves_other_allowances(scenario, monkeypatch, tokens, timeout):
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        run.config = {**run.config, "settings": {**run.config["settings"], "token_budget": tokens, "timeout_seconds": timeout}}
        run.started_at = now() - timedelta(seconds=10 if timeout else 7300)
    def verify(repo, env, connection, credentials, context, budget, remaining, stopped):
        assert budget == (None if tokens is None else tokens - 100)
        assert remaining is None if timeout is None else 0 < remaining <= 890
        assert not stopped()
        return RESULT, {"tokens": 75}
    monkeypatch.setattr(verification, "api_verify", verify)
    row, _, run = perform(scenario)
    assert row.status == "completed" and row.usage == {"tokens": 75} and run.status == "completed"


def test_unlimited_unknown_consumption_is_not_zero_or_retried(scenario, monkeypatch):
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        run.config = {**run.config, "settings": {**run.config["settings"], "token_budget": None, "timeout_seconds": None}}
    calls = []
    def fail(*args):
        calls.append(True)
        raise ValueError("Consumption unknown")
    monkeypatch.setattr(verification, "api_verify", fail)
    row, _, _ = perform(scenario)
    assert row.status == "failed" and row.usage == {"tokens": None}
    with transaction() as db:
        db.get(ReviewRun, scenario["run"]).status = "running"
    reviews.perform_review(scenario["run"])
    assert len(calls) == 1 and saved()[0].usage == {"tokens": None}


def test_file_limit_skips_verification_and_queues_notice(scenario, monkeypatch):
    result = ocr.file_limit_result([f"file{i}.py" for i in range(41)], 40)
    def skipped(*args, **kwargs):
        kwargs["after_review"](result, Path("/tmp/repo"), {}, {})
        return result
    monkeypatch.setattr(ocr, "review", skipped)
    monkeypatch.setattr(verification, "perform", lambda *a: pytest.fail("Skipped runs must not verify findings"))
    row, finding, run = perform(scenario)
    assert row.status == "skipped" and finding.status == "open" and not scenario["mutations"]
    assert run.status == "skipped" and run.publication == "pending"
    payload = checks.check_payload(run, 0)
    assert payload["conclusion"] == "skipped" and result[1]["message"] in payload["output"]["summary"]
    with transaction() as db:
        assert db.scalar(select(Job).where(Job.kind == "publish", Job.payload["run_id"].as_string() == run.id))


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("unlimited", [False, True])
def test_native_verification_uses_same_connection_and_schema(scenario, monkeypatch, kind, unlimited):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
        db.add(ConnectionGrant(connection_id=connection.id, repository_id=22, automatic=True))
        run = db.get(ReviewRun, scenario["run"])
        run.owner_id = 7
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": kind, "owner_id": 7,
                                                  "options": {"prompt_token_ceiling": 200000}}}
        if unlimited:
            run.config = {**run.config, "settings": {**run.config["settings"], "timeout_seconds": None,
                                                   "token_budget": None}}
            run.started_at = now() - timedelta(seconds=7300)
            run.report = {"summary": {"total_tokens": 200000}, "limits": {"context_bytes": 800000}}
            run.progress = {"analysis_saved": True, "analysis_status": "completed"}
    calls = []
    def native(connection, identity, payload, stopped):
        calls.append(payload)
        assert connection == "connection" and identity.startswith("verify-") and not stopped()
        assert payload["timeout"] is None if unlimited else payload["timeout"] > 0
        assert payload["token_budget"] == (None if unlimited else 199900)
        return RESULT, {"tokens": 50}
    monkeypatch.setattr(runner_client, "review", native)
    row, _, _ = perform(scenario)
    assert row.verdict == "fixed" and len(calls) == 1
    assert calls[0]["task"] == "verification" and calls[0]["output_schema"]["additionalProperties"] is False
    assert calls[0]["prompt_token_ceiling"] == 200000
    assert row.usage == {"tokens": 50}


def test_migration_preserves_findings_occurrences_and_defaults(scenario):
    with transaction() as db:
        finding = db.get(Finding, scenario["finding"])
        original = finding.content
    config = Config("alembic.ini")
    command.downgrade(config, "20261007_github_checks")
    command.upgrade(config, "head")
    with transaction() as db:
        assert db.get(Finding, scenario["finding"]).content == original
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 1
        assert db.scalar(select(func.count()).select_from(FindingVerification)) == 0
    assert RepoSettings().auto_resolve is False


@pytest.mark.parametrize("change", ["rename", "delete", "missing", "symlink", "deleted_excluded", "literal_paths"])
@pytest.mark.parametrize("byte_budget", [800000, None])
def test_context_uses_original_git_objects_and_tracks_renames(tmp_path, monkeypatch, change, byte_budget):
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args], cwd=root).decode().strip()
    git("init", "--quiet")
    (root / "original.py").write_text("while True: retry()\n" + "# context\n" * 20)
    if change == "deleted_excluded":
        (root / "private.py").write_text("excluded = 'must not reach provider'\n")
    if change == "literal_paths":
        for name in ("file*.py", "file-secret.py"):
            (root / name).write_text("old = 1\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "fixture original")
    original = git("rev-parse", "HEAD")
    if change == "rename":
        (root / "original.py").rename(root / "renamed.py")
        (root / "renamed.py").write_text("for attempt in range(3): retry()\n" + "# context\n" * 20)
    elif change == "delete":
        (root / "original.py").unlink()
    elif change == "symlink":
        (root / "original.py").unlink()
        (root / "original.py").symlink_to("/etc/passwd")
    else:
        (root / "original.py").write_text("changed\n")
        if change == "deleted_excluded":
            (root / "private.py").unlink()
        if change == "literal_paths":
            (root / "file*.py").write_text("allowed = 2\n")
            (root / "file-secret.py").write_text("excluded_secret = 3\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "fixture change")
    head = git("rev-parse", "HEAD")
    seen = []
    def select_paths(repo, env, sources, options, stopped):
        seen.append(sources)
        return set(sources) - {"private.py", "file-secret.py"}
    monkeypatch.setattr(verification, "filter_context", select_paths)
    finding = {**COMMENT, "path": "missing.py" if change == "missing" else "original.py"}
    args = (root, ocr.child_environment(), {}, finding, original, head,
            RepoSettings(delegated_context_bytes=byte_budget).model_dump(), lambda: False)
    if change in {"missing", "symlink"}:
        with pytest.raises(ValueError, match="not a regular file"):
            verification.build_context(*args)
    else:
        context = verification.build_context(*args)
        assert context["sources"]["original"]["original.py"].startswith("while True:")
        assert context["original_sha"] == original and context["head_sha"] == head
        assert len(seen) == 1
        if change == "rename":
            assert context["current_path"] == "renamed.py" and "rename from original.py" in context["diff"]
            assert context["sources"]["current"]["renamed.py"].startswith("for attempt")
        elif change == "delete":
            assert context["current_path"] is None and not context["sources"]["current"]
        elif change == "deleted_excluded":
            assert context["excluded_paths"] == ["private.py"]
            assert "excluded =" not in json.dumps(context) and "must not reach provider" not in context["diff"]
        else:
            assert "allowed = 2" in context["diff"] and "excluded_secret" not in context["diff"]


def test_manual_change_during_analysis_invalidates_fixed_result(scenario, monkeypatch):
    def changed(*args):
        with transaction() as db:
            reviews.request_finding_status(db, db.get(Finding, scenario["finding"]), "open", db.get(User, 7), "Investigating")
        return RESULT, {"tokens": 50}
    monkeypatch.setattr(verification, "api_verify", changed)
    row, finding, _ = perform(scenario)
    assert row.verdict == "fixed" and row.resolution == "skipped"
    assert finding.desired_status == "open" and scenario["mutations"] == []


def test_new_commit_during_analysis_never_resolves(scenario, monkeypatch):
    def push(*args):
        with transaction() as db:
            reviews.upsert_pr(db, db.get(Repository, 22), remote("d" * 40))
        return RESULT, {"tokens": 50}
    monkeypatch.setattr(verification, "api_verify", push)
    row, finding, run = perform(scenario)
    assert row.status == "skipped" and finding.desired_status is None
    assert run.status == "superseded"


def test_worker_attempt_fences_stale_verification_result(scenario, monkeypatch):
    def replaced(*args):
        with transaction() as db:
            run = db.get(ReviewRun, scenario["run"])
            run.progress = {**run.progress, "execution": "new-worker"}
        return RESULT, {"tokens": 50}
    monkeypatch.setattr(verification, "api_verify", replaced)
    row, finding, _ = perform(scenario)
    assert row.result is None and finding.desired_status is None


def test_unknown_or_incomplete_thread_never_resolves(scenario):
    scenario["thread"]["comments"]["pageInfo"]["hasNextPage"] = True
    row, finding, _ = perform(scenario)
    assert row.verdict == "fixed" and row.resolution == "skipped"
    assert finding.desired_status is None


def test_false_positive_retains_dismissal_workflow(scenario, monkeypatch, client):
    monkeypatch.setattr(verification, "api_verify", lambda *a: ({**RESULT, "original_issue_present": False, "dismissal_recommended": True}, {"tokens": 50}))
    row, finding, _ = perform(scenario)
    assert row.verdict == "uncertain" and finding.status == "open" and not finding.desired_status
    detail = client.get(f"/api/v1/findings/{finding.id}").json()
    assert detail["verifications"][0]["result"]["dismissal_recommended"] is True
    assert "context" not in detail["verifications"][0]  # Only cited evidence is exposed in history.


def test_native_consent_revocation_during_verification_stops_resolution(scenario, monkeypatch):
    with transaction() as db:
        c = db.get(Connection, "connection")
        c.kind, c.owner_id, c.auth_status = "chatgpt", 7, "connected"
        db.add(ConnectionGrant(connection_id=c.id, repository_id=22, automatic=True))
        run = db.get(ReviewRun, scenario["run"])
        run.owner_id = 7
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": "chatgpt", "owner_id": 7}}
    monkeypatch.setattr(runner_client, "review", lambda *args: (RESULT, {"tokens": 50}))
    _, finding, _ = perform(scenario)
    with transaction() as db:
        db.delete(db.get(ConnectionGrant, ("connection", 22)))
    reviews.synchronize_finding(finding.sync_intent)
    assert saved()[1].status == "open" and not scenario["mutations"]


def test_manual_reopen_racing_remote_write_is_restored(scenario, monkeypatch):
    _, finding, _ = perform(scenario)
    intent = finding.sync_intent
    original = scenario["set_thread"]
    def mutation(token, identity, resolved):
        if resolved:
            with transaction() as db:
                item = db.get(Finding, finding.id)
                reviews.request_finding_status(db, item, "open", db.get(User, 7), "Keep it open")
                latest = item.sync_intent
            reviews.synchronize_finding(latest)
        return original(token, identity, resolved)
    monkeypatch.setattr(github, "set_thread", mutation)
    reviews.synchronize_finding(intent)
    row, finding, _ = saved()
    assert row.resolution == "superseded" and finding.desired_status == "open"
    assert "reopen_verification_id" in finding.sync_intent
    monkeypatch.setattr(github, "set_thread", original)
    reviews.synchronize_finding(finding.sync_intent)
    assert saved()[1].status == "open" and scenario["thread"]["isResolved"] is False


@pytest.mark.parametrize("bad", ["absent", "budget", "warning", "usage", "multiple", "malformed"])
def test_api_adapter_requires_explicit_complete_output(tmp_path, monkeypatch, bad):
    from contextlib import nullcontext
    response = {"status": "success", "summary": {"files_reviewed": 1, "total_tokens": 10}, "comments": []}
    if bad == "budget":
        response["summary"]["budget_exceeded"] = True
    elif bad == "warning":
        response["warnings"] = ["incomplete"]
    elif bad == "usage":
        response["summary"].pop("total_tokens")
    def terminal(root, session_id):
        if bad in {"absent", "multiple"}:
            raise ValueError("Expected exactly one terminal result.")
        return {"verdict": "bad"} if bad == "malformed" else RESULT
    monkeypatch.setattr(verification, "terminal_result", terminal)
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    def command(args, *a, **k):
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        return 0, json.dumps(response), ""
    monkeypatch.setattr(ocr, "command", command)
    with pytest.raises(ValueError) as caught:
        verification.api_verify(tmp_path / "repo", {}, {}, {}, CONTEXT, 10000, 30, lambda: False)
    if bad != "usage":
        assert caught.value.usage == {"tokens": 10}


@pytest.mark.parametrize("session", ["valid", "unavailable", "wrong_repo"])
def test_api_process_failure_preserves_redacted_diagnostics(tmp_path, monkeypatch, session):
    from contextlib import contextmanager

    session_id = "00000000-0000-4000-8000-000000000001"
    api_secret, header_secret = 'provider-"secret', "header-\nsecret"
    active, calls = [], []
    @contextmanager
    def configuration(*args):
        active.append(True)
        try:
            yield
        finally:
            active.clear()
    monkeypatch.setattr(ocr, "configuration", configuration)
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    def command(args, root, *a, **k):
        calls.append(args)
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        if "scan" in args:
            return 1, "", f"[ocr] Session: {session_id} (retry with: --resume {session_id})\nError: scan failed: {api_secret}"
        assert active, "Session diagnostics must be read before configuration cleanup"
        assert args[1:] == ["session", "show", "--repo", str(root), "--json", session_id]
        if session == "unavailable":
            raise ValueError("Session was unavailable")
        return 0, json.dumps({
            "summary": {"session_id": session_id, "repo_dir": str(root) if session == "valid" else "/another/repo"},
            "items": [{"file_path": "evidence.py", "error": f"Provider rejected context: {header_secret} " + "x" * 5000}],
        }), ""
    monkeypatch.setattr(ocr, "command", command)
    with pytest.raises(ValueError) as caught:
        verification.api_verify(tmp_path / "repo", {}, {}, {
            "api_key": api_secret, "extra_headers": {"X-Key": header_secret},
        }, CONTEXT, 10000, 30, lambda: False)
    detail = str(caught.value)
    assert "exit_code" in detail and '"exit_code": 1' in detail
    assert "token consumption is unknown" in detail and "[redacted]" in detail
    assert "OCR verification process failed" in detail
    assert json.dumps(api_secret)[1:-1] not in detail and json.dumps(header_secret)[1:-1] not in detail
    assert ("Provider rejected context" in detail) == (session == "valid")
    assert len(detail) < 5000 and not hasattr(caught.value, "usage")
    assert sum("scan" in args and "--preview" not in args for args in calls) == 1


def test_multiple_run_jobs_do_not_start_two_analyses(scenario, monkeypatch):
    with transaction() as db:
        first = db.scalar(select(Job).where(Job.kind == "review", Job.payload["run_id"].as_string() == scenario["run"]))
        first.status, first.attempts, first.lease_until = "running", 1, now() + timedelta(hours=1)
        run = db.get(ReviewRun, scenario["run"])
        run.status, run.progress = "running", {"execution": f"{first.id}:1"}
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Concurrent analysis"))
    reviews.perform_review(scenario["run"], "different-job:1")


@pytest.mark.parametrize("lost_response", [False, True])
def test_disabling_automation_after_remote_write_reopens(scenario, monkeypatch, lost_response):
    _, finding, _ = perform(scenario)
    def changed(*args):
        result = scenario["set_thread"](*args)
        if args[2]:
            with transaction() as db:
                db.get(Repository, 22).settings = {"automatic": True, "auto_resolve": False}
            if lost_response:
                raise github.GitHubError(503, "Response lost")
        return result
    monkeypatch.setattr(github, "set_thread", changed)
    if lost_response:
        with pytest.raises(github.GitHubError):
            reviews.synchronize_finding(finding.sync_intent)
        with transaction() as db:
            db.scalar(select(FindingVerification)).resolution = "failed"
        monkeypatch.setattr(github, "set_thread", scenario["set_thread"])
    reviews.synchronize_finding(finding.sync_intent)
    assert scenario["mutations"] == [True, False]
    assert saved()[1].status == "open" and saved()[0].resolution == "superseded"


def test_worker_loss_after_racing_manual_reopening_recovers(scenario, monkeypatch):
    _, finding, _ = perform(scenario)
    intent = finding.sync_intent
    def lost(*args):
        if not args[2]:
            return scenario["set_thread"](*args)
        with transaction() as db:
            item = db.get(Finding, finding.id)
            reviews.request_finding_status(db, item, "open", db.get(User, 7), "Keep open")
            latest = item.sync_intent
        reviews.synchronize_finding(latest)
        scenario["set_thread"](*args)
        raise github.GitHubError(503, "Response lost after late automatic write")
    monkeypatch.setattr(github, "set_thread", lost)
    with pytest.raises(github.GitHubError):
        reviews.synchronize_finding(intent)
    monkeypatch.setattr(github, "set_thread", scenario["set_thread"])
    with transaction() as db:
        reviews.reconcile_threads(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "token")
    _, finding, _ = saved()
    assert finding.status == "open" and finding.desired_status == "open"
    reviews.synchronize_finding(finding.sync_intent)
    assert saved()[0].resolution == "reverted" and not scenario["thread"]["isResolved"]


@pytest.mark.parametrize("failure", ["exception", "expired_lease"])
def test_unrecoverable_worker_preserves_completed_analysis(scenario, monkeypatch, failure):
    _, finding, _ = perform(scenario)
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        run.status = "running"
        row = db.scalar(select(FindingVerification))
        row.status, row.result, row.verdict, row.resolution = "running", None, None, "none"
        row.usage = {"tokens": 199900}
        item = db.get(Finding, finding.id)
        item.desired_status, item.sync_intent = None, None
        job = db.scalar(select(Job).where(Job.kind == "review", Job.payload["run_id"].as_string() == run.id))
        jid = job.id
        if failure == "expired_lease":
            job.status, job.attempts, job.lease_until = "running", 3, now() - timedelta(seconds=1)
    if failure == "expired_lease":
        monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kw: None)
        jobs.dispatch()
    else:
        def broken(*args):
            raise RuntimeError("worker disappeared")
        monkeypatch.setattr(jobs, "execute", broken)
        jobs.execute_job(jid)
    row, finding, run = saved()
    assert run.status == "completed" and run.report["summary"]["total_tokens"] == 100
    assert row.status == "skipped" and row.verdict == "uncertain" and row.usage["tokens"] == 199900
    assert finding.status == "open" and not finding.desired_status
    with transaction() as db:
        assert db.scalar(select(Job.id).where(Job.kind == "publish", Job.payload["run_id"].as_string() == run.id))


@pytest.mark.parametrize("error", [False, True])
def test_job_attempt_fence_prevents_stale_completion(scenario, monkeypatch, error):
    with transaction() as db:
        job = reviews.enqueue(db, "scan_repository", {"repository_id": 22})
        jid = job.id
    def replaced(*args):
        with transaction() as db:
            job = db.get(Job, jid)
            job.attempts, job.status = 2, "running"
        if error:
            raise RuntimeError("late error")
    monkeypatch.setattr(jobs, "execute", replaced)
    jobs.execute_job(jid)
    with transaction() as db:
        assert db.get(Job, jid).status == "running" and db.get(Job, jid).attempts == 2


def test_manual_override_refreshes_verification_summary(scenario):
    from code_mortis import checks
    _, finding, _ = perform(scenario)
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        run.check_enabled = False
        reviews.request_finding_status(db, db.get(Finding, finding.id), "open", db.get(User, 7), "Keep open")
    checks.sync_check(scenario["run"])
    assert saved()[2].progress["verification"]["pending"] == 0


@pytest.mark.parametrize("resolution,pending,resolved,failed", [
    ("pending", 1, 0, 0), ("applying", 1, 0, 0), ("resolved", 0, 1, 0),
    ("failed", 0, 0, 1), ("superseded", 0, 0, 0),
])
def test_resolution_summary_has_exclusive_sync_counts(scenario, resolution, pending, resolved, failed):
    row, _, _ = perform(scenario)
    with transaction() as db:
        db.get(FindingVerification, row.id).resolution = resolution
        run = db.get(ReviewRun, scenario["run"])
        verification.record_summary(db, run)
        summary = run.progress["verification"]
        assert (summary["pending"], summary["resolved"], summary["sync_failed"]) == (pending, resolved, failed)


def test_reconciliation_fetches_before_locking_and_refreshes_manual_intent(scenario, monkeypatch):
    def threads(*args):
        with transaction() as db:
            finding = db.scalar(select(Finding).where(Finding.id == scenario["finding"]).with_for_update(nowait=True))
            reviews.request_finding_status(db, finding, "open", db.get(User, 7), "Keep open")
        return [{**scenario["thread"], "isResolved": True}]
    monkeypatch.setattr(github, "threads", threads)
    with transaction() as db:
        finding = db.get(Finding, scenario["finding"])
        reviews.reconcile_threads(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "token")
        assert finding.status == "open" and finding.desired_status == "open"
        assert finding.sync_intent["status"] == "open"
    with transaction() as db:
        finding = db.get(Finding, scenario["finding"])
        assert finding.status == "open" and finding.desired_status == "open"


def test_subscription_verification_does_not_reset_token_allowance(scenario, monkeypatch):
    with transaction() as db:
        c = db.get(Connection, "connection")
        c.kind, c.owner_id, c.auth_status = "chatgpt", 7, "connected"
        db.add(ConnectionGrant(connection_id=c.id, repository_id=22, automatic=True))
        run = db.get(ReviewRun, scenario["run"])
        run.owner_id = 7
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": "chatgpt", "owner_id": 7}}
        run.progress = {"analysis_saved": True, "analysis_status": "completed"}
        run.report = {"summary": {"total_tokens": run.config["settings"]["token_budget"]}}
    monkeypatch.setattr(runner_client, "review", lambda *a: pytest.fail("Token allowance was reset"))
    row, _, run = perform(scenario)
    limit = run.config["settings"]["token_budget"]
    assert row.status == "skipped"
    assert row.error == f"Token budget exhausted: {limit:,} / {limit:,} tokens used or reserved. Fix verification skipped."


def test_interrupted_primary_api_review_gets_no_new_allowance(scenario, monkeypatch):
    with transaction() as db:
        run = db.get(ReviewRun, scenario["run"])
        run.progress = {"analysis_started": True, "execution": "vanished-worker"}
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Unknown usage was given a fresh allowance"))
    reviews.perform_review(scenario["run"])
    row, finding, run = saved()
    assert run.status == "failed" and "remaining budget cannot be verified" in run.error
    assert row.status == "skipped" and finding.status == "open"


def test_verification_cancellation_keeps_finding_open(scenario, monkeypatch, client):
    def cancelled(*args):
        assert client.post(f"/api/v1/runs/{scenario['run']}/cancel").status_code == 200
        return RESULT, {"tokens": 50}
    monkeypatch.setattr(verification, "api_verify", cancelled)
    row, finding, run = perform(scenario)
    assert row.status == "skipped" and run.status == "cancelled"
    assert finding.status == "open" and not finding.desired_status


def test_late_sync_failure_does_not_overwrite_confirmed_resolution(scenario):
    _, finding, _ = perform(scenario)
    payload = finding.sync_intent
    reviews.synchronize_finding(payload)
    with transaction() as db:
        verification.sync_failed(db, payload, "late failure from another delivery")
    row, finding, _ = saved()
    assert row.resolution == "resolved" and finding.sync_error is None


def test_empty_stop_message_records_uncertain_without_discarding_review(scenario, monkeypatch):
    def stopped(*args):
        raise ocr.ReviewStopped()
    monkeypatch.setattr(verification, "api_verify", stopped)
    row, finding, run = perform(scenario)
    assert row.status == "skipped" and row.verdict == "uncertain"
    assert row.error == "Verification did not complete."
    assert run.status == "completed" and finding.status == "open" and not finding.desired_status


@pytest.mark.parametrize("actor_type,resolver_id", [("Bot", 338898914), ("Bot", 7), ("User", 338898914)])
def test_graphql_bot_identity_links_only_app_owned_threads(scenario, monkeypatch, actor_type, resolver_id):
    thread = copy.deepcopy(scenario["thread"])
    author = {"__typename": actor_type, "login": "mortis-test", "databaseId": 338898914}
    thread["comments"]["nodes"][0]["author"] = author
    thread["resolvedBy"] = {"login": "mortis-test" if resolver_id == 338898914 else "owner", "databaseId": resolver_id}
    def response(*args):
        return {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [thread], "pageInfo": {"hasNextPage": False}
        }}}}
    monkeypatch.setattr(github, "graphql", response)
    threads = github_threads("token", "team/project", 3)
    monkeypatch.setattr(github, "threads", lambda *args: threads)
    with transaction() as db:
        finding = db.get(Finding, scenario["finding"])
        assert bool(verification.owned_thread(db, finding, threads)) == (actor_type == "Bot")
        finding.thread_id, finding.comment_id = None, None
        reviews.reconcile_threads(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "token")
        assert finding.thread_id == ("thread" if actor_type == "Bot" else None)
        assert finding.status == "open"
    expected = "mortis-test[bot]" if actor_type == "Bot" and resolver_id == 338898914 else ("owner" if resolver_id == 7 else "mortis-test")
    assert threads[0]["resolvedBy"]["login"] == expected


@pytest.mark.parametrize("change", ["legacy_default", "enable", "disable"])
def test_resolution_setting_changes_do_not_repeat_same_commit_analysis(seed, change):
    with transaction() as db:
        pr, repo = db.get(PullRequest, "pr"), db.get(Repository, 22)
        repo.settings = {"automatic": True, "auto_resolve": change == "disable"}
        first = reviews.create_run(db, pr, repo, "automatic")
        db.flush()
        first.status, first.publication = "completed", "published"
        if change == "legacy_default":
            old_settings = dict(first.config["settings"])
            old_settings.pop("auto_resolve")
            first.config = {**first.config, "settings": old_settings}
            first.dedup_key = "legacy-hash"
        else:
            repo.settings = {"automatic": True, "auto_resolve": change == "enable"}
        repeated = reviews.create_run(db, pr, repo, "automatic")
        assert repeated.id == first.id
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1
        pr.head_sha = HEAD
        pushed = reviews.create_run(db, pr, repo, "automatic")
        assert pushed.id != first.id and pushed.config["settings"]["auto_resolve"] == (change == "enable")


@pytest.mark.parametrize("kind", ["api", "chatgpt"])
def test_context_failure_does_not_spend_the_next_findings_allowance(scenario, monkeypatch, kind):
    with transaction() as db:
        finding = db.get(Finding, scenario["finding"])
        other = Finding(id="second-finding", fingerprint="different", **{
            key: getattr(finding, key) for key in (
                "pr_id", "path", "content", "severity", "category", "start_line", "end_line",
                "existing_code", "suggestion", "last_seen_run_id",
            )
        })
        db.add(other)
        db.flush()
        occurrence = db.scalar(select(Occurrence))
        db.add(Occurrence(finding_id=other.id, run_id=occurrence.run_id, snapshot=occurrence.snapshot))
        if kind != "api":
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
            db.add(ConnectionGrant(connection_id=connection.id, repository_id=22, automatic=True))
            run = db.get(ReviewRun, scenario["run"])
            run.owner_id = 7
            run.config = {**run.config, "connection": {**run.config["connection"], "kind": kind, "owner_id": 7}}
    contexts = []
    def context(*args):
        contexts.append(True)
        if len(contexts) == 1:
            raise ValueError("Necessary context is unavailable")
        return copy.deepcopy(CONTEXT)
    monkeypatch.setattr(verification, "build_context", context)
    monkeypatch.setattr(runner_client, "review", lambda *a: (copy.deepcopy(RESULT), {"tokens": 50}))
    reviews.perform_review(scenario["run"])
    with transaction() as db:
        rows = list(db.scalars(select(FindingVerification)))
        failed = next(row for row in rows if row.status == "failed")
        completed = next(row for row in rows if row.status == "completed")
        assert failed.usage == {"tokens": 0}
        assert completed.verdict == "fixed" and sum(completed.usage.values()) > 0


@pytest.mark.parametrize("known_usage", [True, False])
@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
def test_invalid_provider_output_keeps_known_usage_or_unknown_reservation(scenario, monkeypatch, known_usage, kind):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
            db.add(ConnectionGrant(connection_id=connection.id, repository_id=22, automatic=True))
            run = db.get(ReviewRun, scenario["run"])
            run.owner_id = 7
            run.config = {**run.config, "connection": {**run.config["connection"], "kind": kind, "owner_id": 7}}
    def fail(*args):
        if kind != "api":
            raise runner_client.RunnerBlocked("failed", {"tokens": 25 if known_usage else None})
        if known_usage:
            raise verification.VerificationOutputError("Missing verdict", {"tokens": 25})
        raise ValueError("Unknown provider consumption")
    monkeypatch.setattr(verification, "api_verify", fail)
    monkeypatch.setattr(runner_client, "review", fail)
    row, finding, run = perform(scenario)
    assert row.usage["tokens"] == (25 if known_usage else run.config["settings"]["token_budget"] - 100 if kind == "api" else None)
    assert row.verdict == "uncertain" and finding.status == "open"


def test_paginated_discussion_skips_sync_and_recovery(scenario):
    _, finding, _ = perform(scenario)
    scenario["thread"]["comments"]["pageInfo"]["hasNextPage"] = True
    reviews.synchronize_finding(finding.sync_intent)
    row, finding, _ = saved()
    assert row.resolution == "superseded" and not scenario["mutations"]
    assert finding.status == "open" and finding.desired_status is None
    with transaction() as db:
        finding = db.get(Finding, finding.id)
        finding.sync_intent = {"status": "open", "version": finding.sync_version}
        scenario["thread"].update(isResolved=True, resolvedBy={"login": "mortis-test[bot]"})
        assert not verification.recover_reopening(db, finding, scenario["thread"])


def test_github_sync_and_compensation_release_database_connections(scenario, monkeypatch):
    from code_mortis.db import engine
    _, finding, _ = perform(scenario)
    old_request, old_threads = github.request, github.threads
    def request(*args, **kwargs):
        assert engine().pool.checkedout() == 0
        return old_request(*args, **kwargs)
    def threads(*args):
        assert engine().pool.checkedout() == 0
        return old_threads(*args)
    def token(*args, **kwargs):
        assert engine().pool.checkedout() == 0
        return "installation-token"
    def mutation(*args):
        assert engine().pool.checkedout() == 0
        response = scenario["set_thread"](*args)
        if args[2]:
            with transaction() as db:
                db.get(Repository, 22).settings = {"automatic": True, "auto_resolve": False}
        return response
    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "threads", threads)
    monkeypatch.setattr(github, "installation_token_from_jwt", token)
    monkeypatch.setattr(github, "set_thread", mutation)
    reviews.synchronize_finding(finding.sync_intent)
    assert scenario["mutations"] == [True, False] and saved()[1].status == "open"


@pytest.mark.parametrize("status", ["open", "resolved", "dismissed"])
def test_manual_change_during_remote_preflight_prevents_mutation(scenario, monkeypatch, status):
    _, finding, _ = perform(scenario)
    original = github.threads
    def changed(*args):
        with transaction() as db:
            reviews.request_finding_status(db, db.get(Finding, finding.id), status, db.get(User, 7), "Manual override")
        return original(*args)
    monkeypatch.setattr(github, "threads", changed)
    reviews.synchronize_finding(finding.sync_intent)
    assert not scenario["mutations"] and saved()[0].resolution == "superseded"
    latest = saved()[1].sync_intent
    reviews.synchronize_finding(latest)
    reviews.synchronize_finding(latest)
    row, updated, _ = saved()
    assert updated.status == status and updated.desired_status is None
    assert row.resolution == "superseded" and row.sync_attempt["expires"] > now().timestamp()
    assert scenario["mutations"] == [status != "open"]
    # Reopening intent is required to recover a late automatic write after worker loss.
    assert updated.sync_intent == (latest if status == "open" else None)


def test_lost_compensation_response_reconciles_without_duplicate_activity(scenario, monkeypatch):
    _, finding, _ = perform(scenario)
    def mutate(*args):
        response = scenario["set_thread"](*args)
        if args[2]:
            with transaction() as db:
                db.get(Repository, 22).settings = {"automatic": True, "auto_resolve": False}
        else:
            raise github.GitHubError(503, "Lost reopening response")
        return response
    monkeypatch.setattr(github, "set_thread", mutate)
    with pytest.raises(github.GitHubError):
        reviews.synchronize_finding(finding.sync_intent)
    row, finding, _ = saved()
    assert finding.desired_status == "open" and finding.sync_intent.get("reopen_verification_id") == row.id
    monkeypatch.setattr(github, "set_thread", lambda *a: pytest.fail("Reopening repeated"))
    reviews.synchronize_finding(finding.sync_intent)
    reviews.synchronize_finding(finding.sync_intent)
    assert saved()[1].status == "open" and saved()[1].desired_status is None
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Activity).where(Activity.action == "auto_resolution_reverted")) == 1


@pytest.mark.parametrize("failure", ["too_large", "invalid_preview", "cancelled"])
@pytest.mark.parametrize("ceiling", [58888, 200000])
def test_evidence_preflight_never_spends_provider_allowance(tmp_path, monkeypatch, failure, ceiling):
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "configuration", lambda *a: pytest.fail("Provider configuration entered"))
    def preview(args, *a, **k):
        assert "--preview" in args
        assert args[args.index("--max-tokens") + 1] == str(ceiling)
        if failure == "cancelled":
            raise ocr.ReviewStopped()
        return 0, "not json" if failure == "invalid_preview" else json.dumps({"files": [
            {"path": "evidence.py", "will_review": False, "exclude_reason": "too_large"}
        ]}), ""
    monkeypatch.setattr(ocr, "command", preview)
    with pytest.raises(verification.VerificationOutputError) as caught:
        verification.api_verify(tmp_path / "repo", {}, {"options": {"prompt_token_ceiling": ceiling}}, {}, CONTEXT, 10000, 30, lambda: False)
    assert caught.value.usage == {"tokens": 0}
    assert caught.value.status == ("failed" if failure == "invalid_preview" else "skipped")
    if failure == "too_large":
        assert f"{ceiling * 4 // 5:,} tokens" in str(caught.value) and f"max_tokens={ceiling:,}" in str(caught.value)
        assert "too_large" in str(caught.value) and "Evidence size:" in str(caught.value)


@pytest.mark.parametrize("options, ceiling", [({}, 58888), ({"prompt_token_ceiling": 200000}, 200000)])
def test_complete_prompt_limit_is_actionable_without_provider_usage(tmp_path, monkeypatch, options, ceiling):
    from contextlib import nullcontext

    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(verification, "terminal_result", lambda *a: None)
    def command(args, *a, **k):
        assert args[args.index("--max-tokens") + 1] == str(ceiling)
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        return 0, json.dumps({"status": "completed_with_warnings", "summary": {"total_tokens": 0},
                             "warnings": [{"type": "token_threshold_exceeded",
                                           "message": f"prompt tokens ({ceiling}) exceed 80% of max_tokens({ceiling})"}]}), ""
    monkeypatch.setattr(ocr, "command", command)
    with pytest.raises(verification.VerificationOutputError, match=f"{ceiling * 4 // 5:,}-token limit.*{ceiling}") as caught:
        verification.api_verify(tmp_path / "repo", {}, {"options": options}, {}, CONTEXT, 10000, 30, lambda: False)
    assert caught.value.status == "skipped" and caught.value.usage == {"tokens": 0}


def test_api_prompt_ceiling_preserves_full_evidence_and_total_budget(tmp_path, monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(verification, "terminal_result", lambda *a: RESULT)
    calls = []
    def command(args, root, *a, **k):
        calls.append(args)
        assert args[args.index("--max-tokens") + 1] == "200000"
        assert args[args.index("--max-tokens-budget") + 1] == "10000"
        assert (root / "evidence.py").read_text() == verification.evidence_bundle(CONTEXT)
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        return 0, json.dumps({"status": "success", "summary": {"files_reviewed": 1, "total_tokens": 10}, "comments": []}), ""
    monkeypatch.setattr(ocr, "command", command)
    result, usage = verification.api_verify(tmp_path / "repo", {}, {"options": {"prompt_token_ceiling": 200000}},
                                            {}, CONTEXT, 10000, 30, lambda: False)
    assert result == RESULT and usage == {"tokens": 10} and len(calls) == 2


def test_api_adapter_retains_structured_citation_feedback_before_truncation(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from code_mortis import assessment

    context = {**CONTEXT, "history": []}
    raw = {"verdict": "rejected", "relationship": "new", "finding_id": None,
           "explanation": "x" * 10000, "cause": "", "trigger": "", "consequence": "",
           "history_explanation": "", "contradicts_prior": False, "changed_conditions": "",
           "suggestion_supported": False, "evidence": copy.deepcopy(RESULT["evidence"]), "required_paths": []}
    raw["evidence"].append({**raw["evidence"][-1], "quote": "invented"})
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "configuration", lambda *a: nullcontext())
    monkeypatch.setattr(verification, "terminal_result", lambda *a: raw)
    def command(args, *a, **k):
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        return 0, json.dumps({"status": "success", "summary": {"total_tokens": 17, "files_reviewed": 1}}), ""
    monkeypatch.setattr(ocr, "command", command)
    with pytest.raises(verification.VerificationOutputError) as caught:
        verification.api_verify(tmp_path / "repo", {}, {}, {}, context, 10000, 30, lambda: False,
                                instructions=assessment.INSTRUCTIONS, schema=assessment.output_schema(context),
                                validator=assessment.validate_response)
    assert caught.value.usage == {"tokens": 17}
    assert caught.value.feedback["field"] == "evidence[2]"
    detail = str(caught.value).split("; response=", 1)[0]
    assert "evidence[2]" in detail and "current" in detail and "src/retry.py" in detail


def test_missing_resolution_permission_skips_analysis_but_preserves_review(scenario, monkeypatch):
    def token(*args, **kwargs):
        if kwargs.get("resolve"):
            raise github.GitHubError(403, "Contents: Read and write is required")
        return "installation-token"
    monkeypatch.setattr(github, "installation_token", token)
    row, finding, run = perform(scenario)
    assert scenario["calls"] == ["review"]
    assert row.status == "skipped" and row.verdict == "uncertain" and not row.usage
    assert "Contents" in row.error and finding.status == "open" and run.status == "completed"


def test_resolution_permission_is_explicit_and_graphql_keeps_rejection(monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: {
        "token": "test-token", "permissions": {"contents": "read", "pull_requests": "write"},
    })
    assert github.installation_token_from_jwt("jwt", 1) == "test-token"
    with pytest.raises(github.GitHubError, match="Contents: Read and write") as caught:
        github.installation_token_from_jwt("jwt", 1, resolve=True)
    assert caught.value.status == 403
    monkeypatch.setattr(github, "request", lambda *a, **k: {
        "errors": [{"type": "FORBIDDEN", "message": "Resource not accessible by integration"}],
    })
    with pytest.raises(github.GitHubError, match="Resource not accessible by integration") as caught:
        github.set_thread("token", "thread", True)
    assert caught.value.status == 403


def test_verifier_diagnostics_are_actionable_and_redacted(scenario, monkeypatch):
    raw = {**RESULT, "original_issue_present": "true"}
    with pytest.raises(ValueError, match="original_issue_present: bool_type"):
        verification.validate_result(raw, CONTEXT)
    def fail(*args):
        raise verification.VerificationOutputError("Invalid response: provider-secret", {"tokens": 17})
    monkeypatch.setattr(verification, "api_verify", fail)
    row, finding, _ = perform(scenario)
    assert "provider-secret" not in row.error and "[redacted]" in row.error
    assert row.usage == {"tokens": 17} and finding.status == "open"


def test_enabling_resolution_requires_installed_permission(seed, client, github_stub, monkeypatch):
    def token(*args, **kwargs):
        assert kwargs["resolve"] is True
        raise github.GitHubError(403, "Contents: Read and write is required")
    monkeypatch.setattr(github, "installation_token", token)
    response = client.put("/api/v1/repositories/22", json={
        "enabled": True, "connection_id": "connection", "settings": {"automatic": True, "auto_resolve": True},
    })
    assert response.status_code == 403 and "Contents" in response.json()["detail"]
    with transaction() as db:
        assert not db.get(Repository, 22).settings.get("auto_resolve")


def test_permission_revocation_keeps_saved_verdict_retryable(scenario, monkeypatch):
    row, finding, _ = perform(scenario)
    intent = finding.sync_intent
    def denied(*args, **kwargs):
        assert kwargs["resolve"] is True
        raise github.GitHubError(403, "Contents: Read and write is required")
    monkeypatch.setattr(github, "installation_token_from_jwt", denied)
    with pytest.raises(github.GitHubError) as caught:
        reviews.synchronize_finding(intent)
    with transaction() as db:
        verification.sync_failed(db, intent, str(caught.value))
    failed, finding, _ = saved()
    assert finding.status == "open" and failed.resolution == "failed" and "Contents" in finding.sync_error
    assert failed.result == row.result and failed.usage == row.usage and not scenario["mutations"]
    monkeypatch.setattr(github, "installation_token_from_jwt", lambda *a, **k: "installation-token")
    reviews.synchronize_finding(intent)
    reviews.synchronize_finding(intent)
    assert saved()[1].status == "resolved" and scenario["mutations"] == [True]
    assert scenario["calls"] == ["review", "verify"]
