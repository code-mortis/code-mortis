"""Candidate gating, canonical identity, and publication recovery across review engines."""

import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from test_review_flow import COMMENT, new_run, remote

from code_mortis import assessment, checks, github, jobs, ocr, reviews, runner_client, verification
from code_mortis.db import now, transaction
from code_mortis.models import (
    Connection,
    ConnectionGrant,
    Finding,
    Job,
    Occurrence,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRun,
    User,
)
from code_mortis.schemas import RepoSettings
from code_mortis.security import seal

SOURCE = "\n" * 6 + "while True:\n    retry()\n    logger.error(api_key)"


def verdict(context, **changes):
    previous = context["history"]
    return {
        "verdict": "confirmed", "relationship": "duplicate" if previous else "new",
        "finding_id": previous[0]["id"] if previous else None,
        "explanation": "Retry never terminates when the destination fails.",
        "cause": "An unbounded loop", "trigger": "A failing destination", "consequence": "The worker cannot finish",
        "history_explanation": "Same retry defect despite different wording." if previous else "",
        "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": False,
        "evidence": [{"revision": "current", "path": context["finding"]["path"],
                      "start_line": 7, "end_line": 7, "quote": "while True:"}],
        **changes,
    }


@pytest.fixture
def pipeline(seed, github_stub, monkeypatch):
    state = {"comments": [copy.deepcopy(COMMENT)], "calls": 0, "reviews": [], "head": "a" * 40,
             "allowances": [], "native_sizes": [], "native_ids": [], "analysis_calls": 0}
    calls_lock = Lock()

    def request(method, path, token, **kwargs):
        if method == "GET":
            return remote(state["head"])
        item = {"id": len(state["reviews"]) + 1, "body": kwargs["json"]["body"],
                "user": {"login": "mortis-test[bot]"}, "comments": kwargs["json"]["comments"]}
        state["reviews"].append(item)
        if state.get("lost_response"):
            state["lost_response"] = False
            raise github.GitHubError(503, "Response lost")
        return item

    def context(repo, env, fetch_env, run, snapshot, previous, stopped):
        anchor = snapshot.get("existing_code", "").strip()
        def related(item):
            return (item["snapshot"]["path"] == snapshot["path"]
                    or (anchor and anchor == item["snapshot"].get("existing_code", "").strip()))
        state["context"] = {"finding": snapshot, "head_sha": run.head_sha, "original_sha": run.head_sha,
                            "sources": {"current": {snapshot["path"]: SOURCE}}, "diff": "-retry()\n+while True:",
                            "history": [item for item in previous["findings"] if related(item)],
                            "prior_candidates": [item for item in previous["prior_candidates"] if related(item)]}
        return state["context"]

    def response(context):
        with calls_lock:
            state["calls"] += 1
        if state.get("during_assessment"):
            state["during_assessment"]()
        if state.get("failure"):
            raise state["failure"]
        return state["respond"](context) if "respond" in state else verdict(context, **state.get("decision", {}))

    def assess(*args, **kwargs):
        state["allowances"].append(args[5])
        raw = response(args[4])
        try:
            result = kwargs["validator"](raw, args[4])
        except verification.ResultValidationError as exc:
            raise verification.VerificationOutputError(str(exc), {"tokens": 25}, feedback=exc.feedback) from exc
        return result, {"tokens": 25}

    def native(connection, identity, payload, stopped):
        assert payload["prompt_token_ceiling"] == state.get("prompt_token_ceiling", 58888)
        state["native_sizes"].append(len(payload["prompt"].encode()) + 4096)
        state["allowances"].append(payload["token_budget"])
        state["native_ids"].append(identity)
        metadata, _ = json.JSONDecoder().raw_decode(payload["prompt"].split(
            "# Code Mortis verification evidence\n\n", 1,
        )[1])
        context = {**state["context"], **metadata}
        assert verification.evidence_bundle(context) in payload["prompt"]
        return response(context), {"tokens": 25}

    def engine(*args, **kwargs):
        if not kwargs.get("saved_review"):
            state["analysis_calls"] += 1
        comments = [{**item, "source_sha": state["head"]} for item in state["comments"]]
        result = kwargs.get("saved_review") or ("completed", {
            "summary": {"total_tokens": state.get("tokens", 100)},
            "limits": {"context_bytes": state.get("context_bytes", 0)},
        }, comments)
        kwargs["after_review"](result, Path("/tmp/repo"), {}, {})
        return result

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "pages", lambda *a: copy.deepcopy(state["reviews"]))
    monkeypatch.setattr(ocr, "review", engine)
    monkeypatch.setattr(ocr, "configuration", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(assessment, "build_context", context)
    monkeypatch.setattr(verification, "api_verify", assess)
    monkeypatch.setattr(runner_client, "review", native)
    return state


def run_and_publish():
    identity = new_run(assessed=True)
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    return identity


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
def test_retry_assesses_one_candidate_and_preserves_history_and_publication(pipeline, client, kind):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
            connection.options = {"prompt_token_ceiling": 200000}
            connection.secret = seal("{}")
        pipeline["prompt_token_ceiling"] = 200000
    pipeline["comments"] += [{**COMMENT, "path": "src/failed.py"}, {**COMMENT, "path": "src/uncertain.py"}]

    def initial(context):
        path = context["finding"]["path"]
        return verdict(context, **({"evidence": [{"revision": "current", "path": path,
            "start_line": 7, "end_line": 7, "quote": "invented"}]} if path.endswith("failed.py") else {
            "verdict": "uncertain", "relationship": "uncertain", "finding_id": None,
        } if path.endswith("uncertain.py") else {}))

    pipeline["respond"] = initial
    identity = run_and_publish()
    with transaction() as db:
        candidates = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == identity)
                                    .order_by(ReviewCandidate.position)))
        target = candidates[1]
        target_id, original_usage, original_error = target.id, target.usage, target.error
        untouched = [(item.id, item.status, item.result, item.usage) for item in [candidates[0], candidates[2]]]
        run = db.get(ReviewRun, identity)
        original_plan = copy.deepcopy(run.publication_plan)
        original_report, original_finished = run.report, run.finished_at
        run.started_at -= timedelta(days=1)
        db.get(Repository, 22).connection_id = None
    assert len(pipeline["reviews"]) == 1
    path = f"/api/v1/runs/{identity}/candidates/{target_id}"
    response = client.post(path + "/retry")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert response.json()["attempts"][0]["error"] == original_error
    assert "context" not in response.json()["attempts"][0]
    assert client.post(path + "/retry").status_code == 409
    calls, native_ids = pipeline["calls"], list(pipeline["native_ids"])
    pipeline["respond"] = lambda context: verdict(context, relationship="new", finding_id=None,
                                                 cause="Different reachable defect")
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    reviews.publish_run(identity)
    assert pipeline["analysis_calls"] == 1
    assert pipeline["calls"] == calls + 1
    if kind == "api":
        assert pipeline["allowances"][-1] == 199800
    assert len(pipeline["reviews"]) == 2
    if kind != "api":
        assert pipeline["native_ids"][-1] not in native_ids
    with transaction() as db:
        target = db.get(ReviewCandidate, target_id)
        assert target.status == "completed" and target.finding_id
        assert target.attempts[0]["usage"] == original_usage
        assert target.attempts[0]["context"] and target.attempts[0]["finished_at"]
        assert [(db.get(ReviewCandidate, key).id, db.get(ReviewCandidate, key).status,
                 db.get(ReviewCandidate, key).result, db.get(ReviewCandidate, key).usage)
                for key, *_ in untouched] == untouched
        run = db.get(ReviewRun, identity)
        assert run.connection_id == "connection" and run.config["connection"]["id"] == "connection"
        assert run.report == original_report and run.finished_at == original_finished
        assert run.publication_plan[:len(original_plan)] == original_plan
        assert run.publication == "published"
        assert db.scalar(select(Job).where(Job.kind == "review", Job.payload["run_id"].as_string() == identity))


@pytest.mark.parametrize("decision", ["uncertain", "rejected"])
def test_completed_withheld_assessment_cannot_be_retried(pipeline, client, decision):
    pipeline["decision"] = {"verdict": decision, "relationship": "uncertain", "finding_id": None}
    identity = run_and_publish()
    with transaction() as db:
        candidate_id = db.scalar(select(ReviewCandidate.id))
    path = f"/api/v1/runs/{identity}/candidates/{candidate_id}"
    assert client.get(path).json()["retryable"] is False
    assert client.post(path + "/retry").status_code == 409
    with transaction() as db:
        candidate = db.get(ReviewCandidate, candidate_id)
        assert candidate.attempts == [] and candidate.result["verdict"] == decision


def test_assessment_attempt_migration_preserves_existing_candidates(pipeline):
    pipeline["decision"] = {"verdict": "uncertain", "relationship": "uncertain", "finding_id": None}
    run_and_publish()
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        identity, snapshot, result = candidate.id, candidate.snapshot, candidate.result
    config = Config("alembic.ini")
    command.downgrade(config, "20261010_installation_accounts")
    command.upgrade(config, "head")
    with transaction() as db:
        candidate = db.get(ReviewCandidate, identity)
        assert candidate.snapshot == snapshot and candidate.result == result
        assert candidate.attempts == []


def test_manual_assessment_retry_can_resume_an_automatic_run_without_an_automatic_grant(pipeline, client):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        connection.secret = seal("{}")
        db.add(ConnectionGrant(connection_id=connection.id, repository_id=22, automatic=True))
        db.flush()
        run = reviews.create_run(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "automatic")
        run.config = {key: value for key, value in run.config.items() if key != "pipeline_version"}
        run.stages = {}
        db.flush()
        identity = run.id
    pipeline["decision"] = {"evidence": [{"revision": "current", "path": COMMENT["path"],
        "start_line": 7, "end_line": 7, "quote": "invented"}]}
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        candidate_id = db.scalar(select(ReviewCandidate.id))
        db.delete(db.get(ConnectionGrant, ("connection", 22)))
        db.get(Connection, "connection").auth_status = "disconnected"
    assert client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry").status_code == 202
    with transaction() as db:
        job_id = db.scalar(select(Job.id).where(Job.kind == "review", Job.payload["run_id"].as_string() == identity)
                           .order_by(Job.available_at.desc()).limit(1))
    jobs.execute_job(job_id)
    with transaction() as db:
        assert db.get(ReviewRun, identity).status == "blocked"
        db.get(Connection, "connection").auth_status = "connected"
    assert client.post(f"/api/v1/runs/{identity}/retry").status_code == 202
    pipeline["decision"] = {}
    reviews.perform_review(identity)
    with transaction() as db:
        assert db.get(ReviewCandidate, candidate_id).status == "completed"
        assert db.get(ReviewRun, identity).trigger == "automatic"


@pytest.mark.parametrize("change", ["commit", "connection", "budget", "unknown_usage", "permission"])
def test_assessment_retry_rejects_unsafe_or_unfunded_execution(pipeline, client, monkeypatch, change):
    pipeline["decision"] = {"evidence": [{"revision": "current", "path": COMMENT["path"],
        "start_line": 7, "end_line": 7, "quote": "invented"}]}
    identity = run_and_publish()
    with transaction() as db:
        candidate_id = db.scalar(select(ReviewCandidate.id))
        run = db.get(ReviewRun, identity)
        if change == "connection":
            db.get(Connection, "connection").base_url = "https://changed.example.com"
        elif change == "budget":
            run.report = {"summary": {"total_tokens": run.config["settings"]["token_budget"]}}
        elif change == "unknown_usage":
            db.get(ReviewCandidate, candidate_id).usage = {"tokens": None}
    if change == "commit":
        pipeline["head"] = "c" * 40
    if change == "permission":
        monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(403, "Denied")))
    response = client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry")
    assert response.status_code in {403, 409}, response.text
    with transaction() as db:
        assert db.get(ReviewCandidate, candidate_id).status == "failed"
        assert db.get(ReviewCandidate, candidate_id).attempts == []


def test_rephrasing_and_multiple_candidates_reuse_one_finding(pipeline, client):
    pipeline["comments"] += [{**COMMENT, "content": "Unbounded retries can hang the worker.", "category": "performance"}]
    first = run_and_publish()
    pipeline["head"] = "c" * 40
    with transaction() as db:
        db.get(PullRequest, "pr").head_sha = pipeline["head"]
    pipeline["comments"] = [{**COMMENT, "content": "Retry loop has no exit condition.", "start_line": 17, "end_line": 17}]
    second = run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 2
        assert db.get(ReviewRun, second).publication == "no_new_comments"
        assert assessment.summary(db, db.get(ReviewRun, first)).new_findings == 1
        assert assessment.summary(db, db.get(ReviewRun, first)).repeated_observations == 1
    assert len(pipeline["reviews"]) == 1
    first_detail = client.get(f"/api/v1/runs/{first}").json()
    assert len(first_detail["occurrences"]) == 1
    assert first_detail["occurrences"][0]["is_new"] is True
    detail = client.get(f"/api/v1/runs/{second}").json()
    assert detail["assessment"]["repeated_observations"] == 1
    assert detail["occurrences"][0]["is_new"] is False
    assert detail["occurrences"][0]["current_status"] == "open"
    assert "candidates" not in detail
    assert client.get(f"/api/v1/runs/{second}/candidates").json()["items"][0]["finding_id"]


@pytest.mark.parametrize("status", ["resolved", "dismissed"])
def test_closed_finding_is_never_reopened_or_reposted(pipeline, client, status):
    identity = run_and_publish()
    with transaction() as db:
        db.scalar(select(Finding)).status = status
    pipeline["comments"][0]["content"] = "Same retry loop problem, reworded."
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(Finding)).status == status
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
    assert len(pipeline["reviews"]) == 1
    occurrence = client.get(f"/api/v1/runs/{identity}").json()["occurrences"][0]
    assert occurrence["is_new"] is True
    assert occurrence["current_status"] == status
    with transaction() as db:
        latest = db.scalar(select(ReviewRun).order_by(ReviewRun.created_at.desc()).limit(1)).id
    repeated = client.get(f"/api/v1/runs/{latest}").json()["occurrences"][0]
    assert repeated["is_new"] is False
    assert repeated["current_status"] == status


def test_legacy_run_occurrence_does_not_guess_new_or_repeat(pipeline, client):
    identity = new_run()
    reviews.perform_review(identity)
    occurrence = client.get(f"/api/v1/runs/{identity}").json()["occurrences"][0]
    assert occurrence["is_new"] is None
    assert occurrence["current_status"] == "open"


def test_distinct_defects_on_same_code_can_both_publish(pipeline):
    run_and_publish()
    pipeline["comments"][0]["content"] = "The loop also exposes secrets in its error handler."
    pipeline["decision"] = {"relationship": "new", "finding_id": None,
                            "cause": "Logging credentials", "consequence": "Credential disclosure",
                            "evidence": [{"revision": "current", "path": COMMENT["path"],
                                          "start_line": 9, "end_line": 9, "quote": "    logger.error(api_key)"}],
                            "history_explanation": "Credential disclosure is distinct from nontermination."}
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 2
    assert len(pipeline["reviews"]) == 2


@pytest.mark.parametrize("decision", [
    {"verdict": "uncertain"}, {"verdict": "rejected"}, {"evidence": []},
    {"trigger": ""}, {"contradicts_prior": True, "changed_conditions": ""},
    {"relationship": "uncertain"},
    {"evidence": [{"revision": "current", "path": "src/retry.py", "start_line": 7, "end_line": 7, "quote": "invented"}]},
    {"verdict": "rejected", "evidence": [{"revision": "current", "path": "evidence.py", "start_line": 1,
                                        "end_line": 1, "quote": "# Code Mortis verification evidence"}]},
])
def test_unsupported_candidates_never_become_findings(pipeline, decision):
    pipeline["decision"] = decision
    identity = run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
        run = db.get(ReviewRun, identity)
        counts = assessment.summary(db, run)
        rejected = decision == {"verdict": "rejected"}
        assert counts.rejected_candidates == int(rejected)
        assert counts.withheld_candidates == int(not rejected)
        assert run.publication == "no_new_comments"
        assert checks.check_payload(run, 0)["conclusion"] == ("success" if decision == {"verdict": "rejected"} else "neutral")
    assert pipeline["reviews"] == []


def test_summary_separates_rejected_and_withheld_candidates(seed, github_stub, client):
    identity = new_run(assessed=True)
    with transaction() as db:
        for index in range(10):
            finding_id = None
            if index < 3:
                finding = Finding(pr_id="pr", fingerprint=str(index), path=COMMENT["path"],
                                  content=COMMENT["content"], start_line=7, end_line=7,
                                  last_seen_run_id=identity)
                db.add(finding)
                db.flush()
                finding_id = finding.id
            decision = "confirmed" if index < 3 else "rejected" if index < 8 else "uncertain"
            db.add(ReviewCandidate(
                run_id=identity, position=index, snapshot=COMMENT,
                status="failed" if index == 9 else "completed",
                result=None if index == 9 else {"verdict": decision},
                finding_id=finding_id, is_new=index < 3,
            ))
        db.flush()
        assessment.record_summary(db, db.get(ReviewRun, identity))
    response = client.get(f"/api/v1/runs/{identity}")
    assert response.status_code == 200
    assert response.json()["assessment"] == {
        "candidates": 10, "new_findings": 3, "repeated_observations": 0,
        "rejected_candidates": 5, "withheld_candidates": 2, "pending": 0,
        "incomplete": 2, "published_comments": 0,
    }
    with transaction() as db:
        payload = checks.check_payload(db.get(ReviewRun, identity), 3)
        assert "Not supported candidates: 5. Withheld candidates: 2." in payload["output"]["summary"]


def test_bad_suggestion_is_preserved_as_input_but_not_published(pipeline):
    pipeline["comments"][0]["suggestion_code"] = "unrelated replacement"
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(ReviewCandidate)).snapshot["suggestion_code"] == "unrelated replacement"
        assert db.scalar(select(Occurrence)).snapshot["suggestion_code"] == ""
    assert "unrelated replacement" not in pipeline["reviews"][0]["comments"][0]["body"]


def test_summary_findings_and_unknown_writes_publish_only_once(pipeline):
    pipeline["comments"][0]["inline_line"] = None
    pipeline["lost_response"] = True
    identity = new_run(assessed=True)
    reviews.perform_review(identity)
    with pytest.raises(github.GitHubError):
        reviews.publish_run(identity)
    with transaction() as db:
        saved = copy.deepcopy(db.get(ReviewRun, identity).publication_plan)
    reviews.publish_run(identity)
    pipeline["comments"][0]["content"] = "The same hang occurs on retry."
    run_and_publish()
    with transaction() as db:
        finding = db.scalar(select(Finding))
        assert finding.published_review_id == 1
        assert db.get(ReviewRun, identity).publication_plan == saved
        assert assessment.summary(db, db.get(ReviewRun, identity)).published_comments == 1
        assert reviews.marker("finding", finding.id) in pipeline["reviews"][0]["body"]
    assert len(pipeline["reviews"]) == 1


@pytest.mark.parametrize("reason", ["budget", "timeout", "provider"])
def test_incomplete_assessment_withholds_and_preserves_usage(pipeline, reason):
    if reason == "budget":
        pipeline["tokens"] = 200000
    else:
        pipeline["failure"] = TimeoutError("Timeout") if reason == "timeout" else ValueError("Unknown consumption")
    identity = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        assert row.status == "failed" and row.finding_id is None
        assert row.usage["tokens"] == (0 if reason == "budget" else 199900)
        assert assessment.summary(db, db.get(ReviewRun, identity)).incomplete == 1
    assert not pipeline["reviews"]


@pytest.mark.parametrize("tokens,reason", [
    (5232697, "Token budget exhausted: 5,232,697 / 5,000,000 tokens used or reserved."),
    (None, "Token usage is unavailable or invalid; remaining budget cannot be verified against the 5,000,000-token limit."),
])
def test_assessment_reports_exhausted_or_unknown_usage(pipeline, tokens, reason):
    pipeline["tokens"] = tokens
    identity = new_run(assessed=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "settings": {**run.config["settings"], "token_budget": 5000000}}
    reviews.perform_review(identity)
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        assert row.error == reason + " Assessment stopped."
        assert row.status == "failed" and row.finding_id is None and row.usage == {"tokens": 0}
    assert pipeline["calls"] == 0


def test_preflight_skip_retains_its_status_and_zero_usage(pipeline):
    pipeline["failure"] = verification.VerificationOutputError("Evidence exceeds scope", {"tokens": 0}, "skipped")
    run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        assert row.status == "skipped" and row.usage == {"tokens": 0} and row.finding_id is None
    assert not pipeline["reviews"]


@pytest.mark.parametrize("publication", ["failed", "publishing", "partially_published"])
def test_unacknowledged_owner_keeps_frozen_plan_until_retry(pipeline, publication):
    first = new_run(assessed=True)
    reviews.perform_review(first)
    with transaction() as db:
        run = db.get(ReviewRun, first)
        run.publication_plan = reviews.publication_plan(db, run)
        saved_plan = copy.deepcopy(run.publication_plan)
        run.publication = publication
    second = run_and_publish()
    assert not pipeline["reviews"]
    reviews.publish_run(first)
    with transaction() as db:
        assert db.get(ReviewRun, second).publication == "no_new_comments"
        assert db.get(ReviewRun, first).publication_plan == saved_plan
        assert db.scalar(select(Finding)).published_review_id == 1
    assert len(pipeline["reviews"]) == 1


def test_concurrent_history_change_invalidates_new_identity(pipeline):
    def change():
        with transaction() as db:
            run = db.scalar(select(ReviewRun))
            finding = Finding(id="concurrent", pr_id="pr", fingerprint="other", path=COMMENT["path"],
                              content=COMMENT["content"], severity="high", category="bug", start_line=7,
                              end_line=7, last_seen_run_id=run.id)
            db.add(finding)
            db.flush()
            db.add(Occurrence(run_id=run.id, finding_id=finding.id, snapshot=COMMENT))
    pipeline["during_assessment"] = change
    run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        assert row.status == "skipped" and "history changed during assessment" in row.error
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
    assert not pipeline["reviews"]


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("budget", [200000, None])
@pytest.mark.parametrize("used", [0, 200000])
def test_native_clients_use_the_same_gate(pipeline, monkeypatch, kind, budget, used):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
        db.get(Repository, 22).settings = {"token_budget": budget}
    pipeline["tokens"] = used
    calls = []

    def native(connection, identity, payload, stopped):
        calls.append(payload)
        assert payload["task"] == "assessment"
        assert payload["prompt_token_ceiling"] == 58888
        assert verification.evidence_bundle(pipeline["context"]) in payload["prompt"]
        assert payload["token_budget"] == (None if budget is None else budget - used)
        return verdict(pipeline["context"]), {"tokens": 25}
    monkeypatch.setattr(runner_client, "review", native)
    identity = run_and_publish()
    allowed = budget is None or used < budget
    assert len(pipeline["reviews"]) == len(calls) == int(allowed)
    assert pipeline["calls"] == 0
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert candidate.status == ("completed" if allowed else "failed")
        assert candidate.usage == {"tokens": 25 if allowed else 0}


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("known", [False, True])
def test_native_assessment_failure_keeps_usage_and_cannot_reset_allowance(pipeline, monkeypatch, kind, known):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
        db.get(Repository, 22).settings = {"token_budget": 1000}
    pipeline["comments"].append({**COMMENT, "path": "src/other.py"})
    allowances = []

    def native(connection, identity, payload, stopped):
        allowances.append(payload["token_budget"])
        if len(allowances) == 1:
            raise runner_client.RunnerBlocked("failed", {"tokens": 25 if known else None})
        return verdict(pipeline["context"]), {"tokens": 25}

    monkeypatch.setattr(runner_client, "review", native)
    identity = run_and_publish()
    assert allowances == ([900, 875] if known else [900])
    with transaction() as db:
        candidates = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == identity)
                                    .order_by(ReviewCandidate.position)))
        assert candidates[0].usage == {"tokens": 25 if known else None}
        assert candidates[1].status == ("completed" if known else "failed")
        assert assessment.remaining_budget(db, db.get(ReviewRun, identity)) == ((850, True) if known else (0, False))


def test_prior_candidates_are_not_finding_identities(pipeline):
    pipeline["decision"] = {"verdict": "rejected"}
    first = run_and_publish()
    second = run_and_publish()
    context = pipeline["context"]
    prior_id = context["prior_candidates"][0]["id"]
    assert context["history"] == []
    schema = assessment.output_schema(context)
    assert schema["properties"]["finding_id"]["anyOf"] == [{"type": "null"}]
    assert schema["properties"]["relationship"]["enum"] == ["new", "uncertain"]
    for finding_id in (prior_id, "invented"):
        with pytest.raises(verification.ResultValidationError, match="unavailable finding"):
            assessment.validate_result(verdict(context, verdict="rejected", relationship="duplicate",
                                                finding_id=finding_id), context)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
        assert db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == second)).result["verdict"] == "rejected"
        assert db.get(ReviewRun, first).publication == "no_new_comments"
    assert pipeline["reviews"] == []


def test_response_schema_allows_only_established_history_ids():
    context = {"finding": COMMENT, "head_sha": "a" * 40, "history": [{"id": "finding"}],
               "prior_candidates": [{"id": "candidate"}], "sources": {"current": {COMMENT["path"]: SOURCE}}}
    schema = assessment.output_schema(context)
    assert schema["properties"]["finding_id"]["anyOf"] == [{"type": "string", "enum": ["finding"]}, {"type": "null"}]
    assert "duplicate" in schema["properties"]["relationship"]["enum"]
    assert assessment.validate_result(verdict(context), context)["finding_id"] == "finding"


@pytest.mark.parametrize("change", [
    {}, {"path": "missing.py"}, {"revision": "original"}, {"end_line": 100}, {"end_line": 6},
    {"quote": 'while True:\n\tretry("pending")'}, {"quote": 'while True:\\n    retry("pending")'},
    {"quote": 'while true:\n    retry("pending")'}, {"start_line": 8, "end_line": 9},
    {"quote": 'while True:\n    retry("ready")'}, {"quote": 'while True:\n    retry(\\"pending\\")'},
])
def test_assessment_citations_remain_verbatim_and_bound_to_source(change):
    context = {"finding": COMMENT, "head_sha": "a" * 40, "history": [],
               "sources": {"current": {COMMENT["path"]: SOURCE.replace("retry()", 'retry("pending")')}}}
    citation = {"revision": "current", "path": COMMENT["path"], "start_line": 7, "end_line": 8,
                "quote": 'while True:\n    retry("pending")', **change}
    raw = verdict(context, evidence=[citation])
    if not change:
        assert assessment.validate_result(raw, context)["evidence"] == [citation]
    else:
        with pytest.raises(verification.ResultValidationError) as caught:
            assessment.validate_result(raw, context)
        assert caught.value.feedback["field"] == "evidence[0]"
        assert caught.value.feedback["path"] == citation["path"]


def test_invalid_later_citation_is_identified_before_response_truncation():
    context = {"finding": COMMENT, "head_sha": "a" * 40, "history": [],
               "sources": {"current": {COMMENT["path"]: SOURCE}}}
    raw = verdict(context, explanation="x" * 10000)
    raw["evidence"].append({**raw["evidence"][0], "start_line": 9, "end_line": 9, "quote": "invented"})
    with pytest.raises(verification.ResultValidationError) as caught:
        assessment.validate_result(raw, context)
    assert caught.value.feedback == {"field": "evidence[1]", "revision": "current", "path": COMMENT["path"],
                                     "start_line": 9, "end_line": 9,
                                     "reason": "quote is not verbatim text within the cited source lines"}
    assert "evidence[1]" in str(caught.value) and COMMENT["path"] in str(caught.value)


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("failure", ["identity", "citation", "citation_format"])
@pytest.mark.parametrize("repeat", [False, True])
def test_one_correction_uses_same_evidence_and_accounts_for_both_attempts(pipeline, kind, failure, repeat):
    pipeline["decision"] = {"verdict": "rejected"}
    run_and_publish()
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    pipeline["calls"] = 0
    pipeline["allowances"].clear()
    contexts = []
    def respond(context):
        contexts.append(copy.deepcopy(context))
        raw = verdict(context, verdict="rejected", required_paths=[],
                      history_explanation="Same prior rejected proposal, without an established finding.")
        if len(contexts) == 1 or repeat:
            if failure == "identity":
                raw.update(relationship="duplicate", finding_id=context["prior_candidates"][0]["id"])
            elif failure == "citation_format":
                raw["evidence"][0]["start_line"] = 0
            else:
                raw["evidence"].append({**raw["evidence"][0], "quote": "invented"})
        return raw
    pipeline["respond"] = respond
    identity = run_and_publish()
    assert len(contexts) == 2
    assert contexts[0]["sources"] == contexts[1]["sources"]
    assert contexts[0]["diff"] == contexts[1]["diff"]
    assert "validation_feedback" not in contexts[0]
    assert contexts[1]["validation_feedback"]["field"] == {
        "identity": "finding_id", "citation": "evidence[1]", "citation_format": "evidence[0].start_line",
    }[failure]
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == ("failed" if repeat else "completed")
        assert row.result is None if repeat else row.result["verdict"] == "rejected"
        assert row.finding_id is None and db.scalar(select(func.count()).select_from(Finding)) == 0
        assert row.usage == {"tokens": 50}
        assert row.context["sources"] == contexts[0]["sources"]
    assert pipeline["allowances"][1] == pipeline["allowances"][0] - 25
    if kind != "api":
        assert len(set(pipeline["native_ids"])) == 2
        assert pipeline["native_sizes"][1] > pipeline["native_sizes"][0]
    assert pipeline["reviews"] == []


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("sequence", ["expand_correct", "correct_expand_fail", "expand_expand_fail"])
def test_correction_and_expansion_share_three_pass_limit(pipeline, monkeypatch, kind, sequence):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    def expand(repo, env, run, context, paths, stopped):
        context["sources"]["current"][paths[0]] = "dependency = 1\n"
    monkeypatch.setattr(assessment, "expand_context", expand)
    def respond(context):
        index = pipeline["calls"] - 1
        action = {"expand_correct": ["expand", "invalid", "valid"],
                  "correct_expand_fail": ["invalid", "expand", "invalid"],
                  "expand_expand_fail": ["expand", "expand", "invalid"]}[sequence][index]
        raw = verdict(context, verdict="rejected" if action == "valid" else "uncertain", required_paths=[])
        if action == "expand":
            raw["required_paths"] = [f"helper{index}.py"]
        elif action == "invalid":
            raw["evidence"][0]["quote"] = "invented"
        return raw
    pipeline["respond"] = respond
    identity = run_and_publish()
    assert pipeline["calls"] == 3
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == ("completed" if sequence == "expand_correct" else "failed")
        assert row.usage == {"tokens": 75}
        assert row.finding_id is None
    assert pipeline["reviews"] == []


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("stop", ["cancel", "commit", "permission", "provider", "budget"])
def test_correction_never_bypasses_execution_guards(pipeline, kind, stop):
    with transaction() as db:
        if kind != "api":
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
        if stop == "budget":
            db.get(Repository, 22).settings = {"token_budget": 1000}
    def respond(context):
        if stop == "provider":
            raise ValueError("Provider authentication failed")
        with transaction() as db:
            run = db.scalar(select(ReviewRun).where(ReviewRun.status == "running"))
            if stop == "cancel":
                run.cancel_requested = True
            elif stop == "commit":
                db.get(PullRequest, "pr").head_sha = "c" * 40
            elif stop == "permission":
                db.get(User, 7).enabled = False
        return verdict(context, evidence=[{"revision": "current", "path": COMMENT["path"],
                                          "start_line": 7, "end_line": 7, "quote": "invented"}])
    pipeline["respond"] = respond
    if stop == "budget":
        pipeline["tokens"] = 975
    identity = run_and_publish()
    assert pipeline["calls"] == 1
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status in {"failed", "skipped"} and row.finding_id is None
        assert row.usage == {"tokens": 25 if stop != "provider" else 200000 - 100}
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
    assert pipeline["reviews"] == []


@pytest.mark.parametrize("budget", [None, 1000])
def test_unknown_api_usage_cannot_trigger_correction(pipeline, monkeypatch, budget):
    with transaction() as db:
        db.get(Repository, 22).settings = {"token_budget": budget}
    calls = []
    def api(*args, **kwargs):
        calls.append(args[5])
        raise verification.VerificationOutputError("Unknown usage", {"tokens": None},
                                                   feedback={"field": "finding_id", "reason": "invalid"})
    monkeypatch.setattr(verification, "api_verify", api)
    identity = run_and_publish()
    assert len(calls) == 1
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == "failed" and row.usage == {"tokens": calls[0]}
    assert pipeline["reviews"] == []


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
def test_correction_does_not_reset_the_deadline(pipeline, monkeypatch, kind):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    clock = [now()]
    monkeypatch.setattr(assessment, "now", lambda: clock[0])
    def respond(context):
        clock[0] += timedelta(hours=1)
        return verdict(context, relationship="duplicate", finding_id="unavailable")
    pipeline["respond"] = respond
    identity = run_and_publish()
    assert pipeline["calls"] == 1
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == "skipped" and row.finding_id is None
        assert row.usage == {"tokens": 25}
    assert pipeline["reviews"] == []


def test_interrupted_assessment_never_gets_a_fresh_allowance(pipeline):
    pipeline["failure"] = SystemExit("Worker terminated")
    identity = new_run(assessed=True)
    with pytest.raises(SystemExit):
        reviews.perform_review(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "running" and candidate.usage == {"tokens": 199900}
    del pipeline["failure"]
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "failed" and candidate.usage == {"tokens": 199900}
    assert pipeline["calls"] == 1 and not pipeline["reviews"]


@pytest.mark.parametrize("change", ["head", "permission", "disabled", "finding_state", "connection"])
def test_stale_or_unauthorized_decisions_are_withheld(pipeline, monkeypatch, change):
    if change == "finding_state":
        run_and_publish()
        pipeline["comments"][0]["content"] = "Reworded retry defect."

    def changed():
        with transaction() as db:
            if change == "head":
                db.get(PullRequest, "pr").head_sha = "c" * 40
            elif change == "disabled":
                db.get(Repository, 22).enabled = False
            elif change == "finding_state":
                db.scalar(select(Finding)).status = "dismissed"
            elif change == "connection":
                db.get(Connection, "connection").base_url = "https://changed.example/v1"
        if change == "permission":
            def denied(*args, **kwargs):
                raise github.GitHubError(403, "Access revoked")
            monkeypatch.setattr(github, "authorize_repo", denied)

    pipeline["during_assessment"] = changed
    identity = new_run(assessed=True)
    reviews.perform_review(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert candidate.status == "skipped" and candidate.finding_id is None
        assert assessment.summary(db, db.get(ReviewRun, identity)).incomplete == 1


def test_concurrent_assessments_serialize_canonical_assignment(pipeline):
    first = new_run(assessed=True)
    with transaction() as db:
        db.get(Repository, 22).settings = {"language": "Spanish"}
    second = new_run(assessed=True)
    barrier = Barrier(2)
    pipeline["during_assessment"] = lambda: barrier.wait(timeout=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(reviews.perform_review, [first, second]))
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
        assert sorted(db.scalars(select(ReviewCandidate.status))) == ["completed", "skipped"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(reviews.publish_run, [first, second]))
    assert len(pipeline["reviews"]) == 1


def test_concurrent_publication_claims_allow_one_comment(pipeline):
    first = new_run(assessed=True)
    reviews.perform_review(first)
    second = new_run(assessed=True)
    reviews.perform_review(second)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(reviews.publish_run, [first, second, first]))
    assert len(pipeline["reviews"]) == 1
    with transaction() as db:
        finding = db.scalar(select(Finding))
        assert finding.published_review_id == 1
        assert db.get(ReviewRun, finding.publication_run_id).publication_plan


def test_context_tracks_renames_and_preserves_literal_case(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "--quiet")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    source = 'def Retry():\n    while True:\n        send("CaseSensitive")\n'
    (repo / "old.py").write_text(source)
    (repo / "helper.py").write_text('def send(value):\n    return value == "CaseSensitive"\n')
    git("add", ".")
    git("commit", "--quiet", "-m", "original")
    original_sha = git("rev-parse", "HEAD")
    git("mv", "old.py", "new.py")
    (repo / "unrelated.py").write_text("# unrelated change\n" * 20000)
    (repo / "caller.py").write_text("from new import Retry\nRetry()\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "rename")
    head = git("rev-parse", "HEAD")
    monkeypatch.setattr(verification, "filter_context", lambda repo, env, sources, options, stopped: set(sources))
    run = SimpleNamespace(base_sha=original_sha, head_sha=head, config={"settings": RepoSettings().model_dump()})
    previous = {"findings": [{"id": "original", "sha": original_sha, "fingerprint": "legacy",
                              "snapshot": {**COMMENT, "path": "old.py"}}], "prior_candidates": []}
    context = assessment.build_context(repo, {}, {}, run, {**COMMENT, "path": "new.py", "source_sha": head}, previous, lambda: False)
    assert context["history"][0]["id"] == "original"
    assert context["sources"] == {"current": {"new.py": source}}
    assert "CaseSensitive" in context["sources"]["current"]["new.py"]
    assert "unrelated.py" in context["changed_paths"]
    assert "unrelated change" not in verification.evidence_bundle(context)
    assert "caller.py" not in context["diff"]
    assessment.expand_context(repo, {}, run, context, ["helper.py", "caller.py"], lambda: False)
    assert context["sources"]["current"]["helper.py"] == (repo / "helper.py").read_text()
    assert context["sources"]["current"]["caller.py"] == (repo / "caller.py").read_text()
    assert "caller.py" in context["diff"]
    assert "unrelated change" not in verification.evidence_bundle(context)
    previous["findings"] = [
        {"id": "moved-block", "sha": head, "fingerprint": "legacy", "snapshot": {**COMMENT, "path": "another.py"}},
        {"id": "different-literal", "sha": head, "fingerprint": "legacy2",
         "snapshot": {**COMMENT, "path": "another.py", "existing_code": COMMENT["existing_code"].upper()}},
    ]
    context = assessment.build_context(repo, {}, {}, run, {**COMMENT, "path": "new.py", "source_sha": head}, previous, lambda: False)
    assert [item["id"] for item in context["history"]] == ["moved-block"]
    previous["prior_candidates"], previous["findings"] = previous["findings"], []
    monkeypatch.setattr(verification, "filter_context", lambda repo, env, sources, options, stopped: set(sources) - {"another.py"})
    with pytest.raises(ValueError, match="history is excluded"):
        assessment.build_context(repo, {}, {}, run, {**COMMENT, "path": "new.py", "source_sha": head}, previous, lambda: False)


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
def test_assessment_requests_only_needed_context_and_accounts_for_every_pass(pipeline, monkeypatch, kind):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    calls, allowances, identities, sizes = [], [], [], []
    def expand(repo, env, run, context, paths, stopped):
        assert paths == ["helper.py"]
        context["sources"]["current"]["helper.py"] = "def retry(): return False\n"
    monkeypatch.setattr(assessment, "expand_context", expand)
    def respond(context):
        calls.append(copy.deepcopy(context))
        if len(calls) == 1:
            return verdict(context, verdict="uncertain", required_paths=["helper.py"])
        assert context["sources"]["current"]["helper.py"] == "def retry(): return False\n"
        return verdict(context, required_paths=[])
    def api(*args, **kwargs):
        allowances.append(args[5])
        return kwargs["validator"](respond(args[4]), args[4]), {"tokens": 25}
    def native(connection, identity, payload, stopped):
        identities.append(identity)
        context = pipeline["context"]
        assert verification.evidence_bundle(context) in payload["prompt"]
        sizes.append(len(payload["prompt"].encode()) + 4096)
        assert "required_paths" in payload["output_schema"]["required"]
        allowances.append(payload["token_budget"])
        return respond(context), {"tokens": 25}
    monkeypatch.setattr(verification, "api_verify", api)
    monkeypatch.setattr(runner_client, "review", native)
    run_and_publish()
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "completed"
        assert "helper.py" in candidate.context["sources"]["current"]
        assert "required_paths" not in candidate.result
        assert candidate.usage == {"tokens": 50}
    assert len(calls) == 2 and len(pipeline["reviews"]) == 1
    assert allowances[1] == allowances[0] - 25
    if kind != "api":
        assert len(set(identities)) == 2


@pytest.mark.parametrize("failure", ["limit", "unknown_usage", "excluded", "round_limit"])
def test_followup_failure_never_publishes_or_reuses_spent_allowance(pipeline, monkeypatch, failure):
    calls = []
    def expand(repo, env, run, context, paths, stopped):
        if failure == "excluded":
            raise ValueError("Necessary assessment evidence is excluded")
        context["sources"]["current"][paths[0]] = "dependency = 1\n"
    monkeypatch.setattr(assessment, "expand_context", expand)
    def api(*args, **kwargs):
        calls.append(args[5])
        if len(calls) > 1 and failure == "limit":
            raise verification.VerificationOutputError("OCR limit: 47,110 tokens", {"tokens": 0}, "skipped")
        if len(calls) > 1 and failure == "unknown_usage":
            raise ValueError("Provider failed; consumption is unknown")
        return verdict(args[4], verdict="uncertain", required_paths=[f"helper{len(calls)}.py"]), {"tokens": 25}
    monkeypatch.setattr(verification, "api_verify", api)
    run_and_publish()
    assert not pipeline["reviews"]
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.finding_id is None
        if failure == "round_limit":
            assert len(calls) == 3 and candidate.result["verdict"] == "uncertain"
            assert candidate.usage == {"tokens": 75}
        else:
            assert candidate.status in {"failed", "skipped"}
            assert candidate.usage == {"tokens": calls[0] if failure == "unknown_usage" else 25}


@pytest.mark.parametrize("path", ["", "x" * 2001, "../secret.py", "/etc/passwd", ".git/config"])
def test_evidence_requests_reject_unsafe_paths(path):
    context = {"finding": COMMENT, "history": [], "sources": {"current": {COMMENT["path"]: SOURCE}}}
    with pytest.raises(ValueError):
        assessment.validate_response(verdict(context, verdict="uncertain", required_paths=[path]), context)


def test_requested_context_keeps_scope_and_reports_byte_limit(tmp_path, monkeypatch):
    run = SimpleNamespace(base_sha="a" * 40, head_sha="b" * 40, config={"settings": RepoSettings().model_dump()})
    context = {"sources": {"current": {}}, "diff": ""}
    monkeypatch.setattr(verification, "filter_context", lambda *args: set())
    monkeypatch.setattr(verification, "read_source", lambda *args: pytest.fail("Excluded source read"))
    with pytest.raises(ValueError, match="excluded.*secret.py"):
        assessment.expand_context(tmp_path, {}, run, context, ["secret.py"], lambda: False)
    assessment.check_context_size({"sources": {"current": {"main.py": "x" * 5000}}}, {"delegated_context_bytes": 4096})
    assessment.check_context_size({"sources": {"current": {"main.py": "x" * 5000}}}, {"delegated_context_bytes": None})
    with pytest.raises(ValueError, match="limit is 1,500,000 bytes.*not truncated"):
        assessment.check_context_size({"sources": {"current": {"main.py": "x" * 1500000}}}, {"delegated_context_bytes": None})


def test_exact_repeat_requires_previously_requested_evidence():
    context = {"finding": COMMENT, "head_sha": "a" * 40, "history": [],
               "sources": {"current": {COMMENT["path"]: SOURCE}}}
    previous = verdict(context, evidence=[{"revision": "current", "path": "helper.py", "start_line": 1,
                                         "end_line": 1, "quote": "while True: retry()"}])
    context["history"] = [{"id": "existing", "sha": context["head_sha"], "snapshot": COMMENT, "assessment": previous}]
    assert assessment.exact_repeat(context) is None
    context["sources"]["current"]["helper.py"] = "while True: retry()\n"
    assert assessment.exact_repeat(context)["relationship"] == "duplicate"


def test_revocation_between_evidence_passes_stops_before_another_call(pipeline, monkeypatch):
    pipeline["decision"] = {"verdict": "uncertain", "required_paths": ["helper.py"]}
    def expand(*args):
        with transaction() as db:
            db.get(Repository, 22).enabled = False
    monkeypatch.setattr(assessment, "expand_context", expand)
    run_and_publish()
    assert pipeline["calls"] == 1 and not pipeline["reviews"]
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "skipped" and candidate.usage == {"tokens": 25}


@pytest.mark.parametrize("comment", json.loads((Path(__file__).parent / "fixtures/pr15_candidates.json").read_text()),
                         ids=lambda comment: str(comment["id"]))
def test_pr15_claim_patterns_need_current_evidence(pipeline, comment):
    content = comment["body"].split("\n\n")[1]
    pipeline["comments"][0].update(content=content, path=comment["path"],
                                   start_line=comment["original_line"], end_line=comment["original_line"])
    pipeline["decision"] = {"verdict": "uncertain", "evidence": [],
                            "explanation": "The supplied source does not establish this claim."}
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(ReviewCandidate)).snapshot["content"] == content
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
    assert not pipeline["reviews"]


def test_supported_distinct_regression_can_reverse_prior_advice(pipeline):
    run_and_publish()
    pipeline["comments"][0]["content"] = "A new error handler exposes credentials."
    pipeline["decision"] = {"relationship": "new", "finding_id": None, "contradicts_prior": True,
                            "changed_conditions": "The new error path sends credentials to logs.",
                            "history_explanation": "Retry termination does not address credential disclosure.",
                            "cause": "Logging credentials", "consequence": "Credential disclosure",
                            "evidence": [{"revision": "current", "path": COMMENT["path"],
                                          "start_line": 9, "end_line": 9, "quote": "    logger.error(api_key)"}]}
    run_and_publish()
    assert len(pipeline["reviews"]) == 2


@pytest.mark.parametrize("publication", ["pending", "failed", "publishing", "partially_published", "published"])
def test_legacy_summary_publication_is_adopted_without_changing_its_plan(pipeline, publication):
    pipeline["comments"][0]["inline_line"] = None
    old = new_run()
    reviews.perform_review(old)
    with transaction() as db:
        run = db.get(ReviewRun, old)
        run.publication_plan = reviews.publication_plan(db, run)
        saved = copy.deepcopy(run.publication_plan)
        fingerprint = db.scalar(select(Finding)).fingerprint
        if publication != "published":
            run.publication = publication
    if publication == "published":
        reviews.publish_run(old)
    current = run_and_publish()
    if publication != "published":
        reviews.publish_run(old)
    with transaction() as db:
        assert db.get(ReviewRun, old).publication_plan == saved
        assert db.get(ReviewRun, current).publication == "no_new_comments"
        assert db.scalar(select(Finding)).fingerprint == fingerprint
        assert db.scalar(select(Finding)).publication_run_id == old
        assert db.scalar(select(Finding)).published_review_id == 1
    assert len(pipeline["reviews"]) == 1


def test_publication_rechecks_permissions_after_assessment(pipeline, monkeypatch):
    identity = new_run(assessed=True)
    reviews.perform_review(identity)

    def revoked(*args, **kwargs):
        raise github.GitHubError(403, "Permission revoked")
    monkeypatch.setattr(github, "authorize_repo", revoked)
    with pytest.raises(github.GitHubError):
        reviews.publish_run(identity)
    assert not pipeline["reviews"]


def test_additive_migration_keeps_legacy_identity_occurrences_and_frozen_plan(pipeline):
    identity = new_run()
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        finding = db.scalar(select(Finding))
        before = (finding.id, finding.fingerprint, finding.content, finding.status,
                  db.scalar(select(Occurrence)).snapshot, db.get(ReviewRun, identity).publication_plan)
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261008_pull_creation_time")
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        finding = db.scalar(select(Finding))
        after = (finding.id, finding.fingerprint, finding.content, finding.status,
                 db.scalar(select(Occurrence)).snapshot, db.get(ReviewRun, identity).publication_plan)
        assert before == after
        assert assessment.summary(db, db.get(ReviewRun, identity)) is None
        assert db.scalar(select(func.count()).select_from(ReviewCandidate)) == 0
        assert finding.publication_run_id is None and finding.published_review_id is None


def test_summary_batch_counts_one_published_comment(pipeline):
    pipeline["comments"] = [
        {**COMMENT, "inline_line": None},
        {**COMMENT, "inline_line": None, "content": "Credentials enter the error log."},
    ]
    pipeline["decision"] = {"relationship": "new", "finding_id": None,
                            "history_explanation": "Credential disclosure is separate from nontermination."}
    identity = run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 2
        assert assessment.summary(db, db.get(ReviewRun, identity)).published_comments == 1
    assert len(pipeline["reviews"]) == 1


def independent_comments(count):
    return [{**COMMENT, "path": f"file_{index}.py", "existing_code": f"retry_{index}()",
             "content": f"Retry defect {index}"} for index in range(count)]


@pytest.mark.parametrize("concurrency,budget,kind,expected", [
    (1, None, "api", 1), (2, None, "api", 2), (4, None, "api", 4),
    (4, 1000, "api", 1), (4, None, "chatgpt", 1), (4, None, "claude_code", 1),
    (None, None, "api", 1),
])
def test_assessment_concurrency_uses_saved_limits_and_preserves_model(pipeline, monkeypatch, concurrency, budget, kind, expected):
    pipeline["comments"] = independent_comments(8)
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": concurrency or 2, "token_budget": budget}
        connection = db.get(Connection, "connection")
        if kind != "api":
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    identity = new_run(assessed=True)
    if concurrency is None:
        with transaction() as db:
            run = db.get(ReviewRun, identity)
            options = {key: value for key, value in run.config["settings"].items() if key != "concurrency"}
            run.config = {**run.config, "settings": options}
    barrier, lock = Barrier(expected), Lock()
    active, peak, leases = 0, 0, []

    @contextmanager
    def configuration(*args, **kwargs):
        leases.append(kwargs["session_per_task"])
        yield
        assert active == 0

    def respond(context):
        nonlocal active, peak
        with transaction() as db:
            candidate = db.scalar(select(ReviewCandidate).where(
                ReviewCandidate.run_id == identity,
                ReviewCandidate.snapshot["path"].as_string() == context["finding"]["path"],
            ))
            assert candidate.status == "running" and candidate.context["sources"] == context["sources"]
            assert candidate.usage["tokens"] is None if budget is None else candidate.usage["tokens"] > 0
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=10)
            return verdict(context, required_paths=[])
        finally:
            with lock:
                active -= 1

    original = verification.api_verify
    def api(*args, **kwargs):
        assert args[2]["model"] == "test-model" and args[2]["id"] == "connection"
        assert kwargs["instructions"] == assessment.INSTRUCTIONS
        assert kwargs["configuration_managed"] == (expected > 1)
        return original(*args, **kwargs)
    monkeypatch.setattr(verification, "api_verify", api)
    monkeypatch.setattr(ocr, "configuration", configuration)
    pipeline["respond"] = respond
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    assert peak == expected and pipeline["calls"] == 8
    assert leases == ([True] if expected > 1 else [])
    assert pipeline["allowances"] == ([None] * 8 if budget is None else [900 - 25 * index for index in range(8)])
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 8
        rows = list(db.scalars(select(ReviewCandidate).order_by(ReviewCandidate.position)))
        assert all(row.status == "completed" and row.usage == {"tokens": 25} for row in rows)


def test_independent_batches_merge_transitive_conflicts():
    rows = [SimpleNamespace(id=str(index), position=index) for index in range(4)]
    keys = {"0": {("path", "a")}, "1": {("finding", "shared")},
            "2": {("path", "a"), ("finding", "shared")}, "3": {("anchor", "independent")}}
    assert [row.id for row in assessment.independent_batch(rows, keys, 4)] == ["0", "3"]
    assert [row.id for row in assessment.independent_batch(rows[1:], keys, 4)] == ["1", "3"]


@pytest.mark.parametrize("distinct", [False, True])
def test_parallel_assessments_keep_related_candidates_ordered(pipeline, distinct):
    first, other = independent_comments(2)
    pipeline["comments"] = [first, {**first, "content": "Reworded finding"}, other]
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    barrier = Barrier(2)
    calls = []
    lock = Lock()
    def respond(context):
        snapshot = context["finding"]
        with lock:
            calls.append(snapshot["content"])
        if snapshot["content"] != "Reworded finding":
            barrier.wait(timeout=10)
        else:
            assert len(context["history"]) == 1
        changes = {"required_paths": []}
        if distinct and snapshot["content"] == "Reworded finding":
            changes.update(relationship="new", finding_id=None, cause="Credential logging",
                           consequence="Credentials exposed", history_explanation="A separate defect in the same code")
        return verdict(context, **changes)
    pipeline["respond"] = respond
    identity = run_and_publish()
    assert calls.index("Reworded finding") > calls.index(first["content"])
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == identity)
                              .order_by(ReviewCandidate.position)))
        assert all(row.status == "completed" for row in rows)
        assert (rows[0].finding_id != rows[1].finding_id) == distinct
        assert db.scalar(select(func.count()).select_from(Finding)) == (3 if distinct else 2)
        assert db.scalar(select(func.count()).select_from(Occurrence)) == (3 if distinct else 2)


@pytest.mark.parametrize("decision", ["exact", "rejected", "contradictory"])
def test_parallel_batches_retain_rejected_history_and_exact_repeat_checks(pipeline, decision):
    first, other = independent_comments(2)
    later = dict(first) if decision == "exact" else {**first, "content": "Reworded finding"}
    pipeline["comments"] = [first, later, other]
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    barrier = Barrier(2)
    def respond(context):
        if context["finding"]["content"] != "Reworded finding":
            barrier.wait(timeout=10)
            return verdict(context, verdict="rejected" if decision == "rejected" else "confirmed")
        if decision == "rejected":
            assert not context["history"] and len(context["prior_candidates"]) == 1
            return verdict(context, verdict="rejected", history_explanation="Same rejected proposal")
        assert len(context["history"]) == 1
        return verdict(context, relationship="new", finding_id=None, contradicts_prior=True)
    pipeline["respond"] = respond
    identity = run_and_publish()
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == identity)
                              .order_by(ReviewCandidate.position)))
        assert all(row.status == "completed" for row in rows)
        assert rows[1].result["verdict"] == {"exact": "confirmed", "rejected": "rejected",
                                             "contradictory": "uncertain"}[decision]
        if decision == "exact":
            assert rows[1].finding_id == rows[0].finding_id and rows[1].usage == {"tokens": 0}
        else:
            assert rows[1].finding_id is None
        assert db.scalar(select(func.count()).select_from(Finding)) == (0 if decision == "rejected" else 2)
    assert pipeline["calls"] == (2 if decision == "exact" else 3)


@pytest.mark.parametrize("change", ["history", "head", "base", "closed", "permission", "connection", "disabled", "cancel", "claim", "deadline"])
def test_parallel_batch_revalidates_history_and_execution(pipeline, monkeypatch, change):
    pipeline["comments"] = independent_comments(3)
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    identity = new_run(assessed=True)
    barrier, changed = Barrier(2), Event()
    clock = [now()]
    monkeypatch.setattr(assessment, "now", lambda: clock[0])
    calls = []
    def api(*args, **kwargs):
        context = args[4]
        path = context["finding"]["path"]
        calls.append(path)
        if path in {"file_0.py", "file_1.py"}:
            barrier.wait(timeout=10)
            if path == "file_0.py":
                with transaction() as db:
                    run = db.get(ReviewRun, identity)
                    pr = db.get(PullRequest, run.pr_id)
                    if change == "history":
                        finding = Finding(id="external", pr_id=run.pr_id, fingerprint="external",
                                          path="other.py", content="External finding", severity="high",
                                          category="bug", start_line=7, end_line=7, last_seen_run_id=identity)
                        db.add(finding)
                        db.flush()
                        db.add(Occurrence(run_id=identity, finding_id=finding.id,
                                          snapshot={**COMMENT, "path": "other.py"}))
                    elif change == "head":
                        pr.head_sha = "c" * 40
                    elif change == "base":
                        pr.base_sha = "d" * 40
                    elif change == "closed":
                        pr.state = "closed"
                    elif change == "permission":
                        db.get(User, 7).enabled = False
                    elif change == "connection":
                        db.get(Connection, "connection").base_url = "https://changed.example/v1"
                    elif change == "disabled":
                        db.get(Repository, 22).enabled = False
                    elif change == "cancel":
                        run.cancel_requested = True
                    elif change == "claim":
                        run.progress = {**run.progress, "execution": "replacement"}
                    elif change == "deadline":
                        clock[0] += timedelta(seconds=1000)
                changed.set()
            else:
                assert changed.wait(timeout=10)
        return kwargs["validator"](verdict(context, required_paths=[]), context), {"tokens": 25}
    monkeypatch.setattr(verification, "api_verify", api)
    reviews.perform_review(identity)
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate).order_by(ReviewCandidate.position)))
        if change == "claim":
            assert all(row.status == "running" and row.usage == {"tokens": None} for row in rows[:2])
            assert rows[2].status == "queued"
        else:
            assert all(row.status == "skipped" and row.finding_id is None for row in rows[:2])
            assert all(row.usage == {"tokens": 25} for row in rows[:2])
        assert db.scalar(select(func.count()).select_from(Finding)) == (2 if change == "history" else 0)
        if change == "history":
            assert rows[2].status == "completed"
    assert len(calls) == (3 if change == "history" else 2)
    assert pipeline["reviews"] == []


def test_parallel_cancellation_stops_active_calls_before_cleanup(pipeline, monkeypatch):
    pipeline["comments"] = independent_comments(4)
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    identity = new_run(assessed=True)
    barrier, lock, cancelled = Barrier(2), Lock(), Event()
    active, started, cleaned = 0, [], []
    @contextmanager
    def configuration(*args, **kwargs):
        yield
        assert active == 0
        cleaned.append(True)
    def api(*args, **kwargs):
        nonlocal active
        with lock:
            active += 1
            started.append(args[4]["finding"]["path"])
        try:
            barrier.wait(timeout=10)
            if args[4]["finding"]["path"] == "file_0.py":
                with transaction() as db:
                    db.get(ReviewRun, identity).cancel_requested = True
                cancelled.set()
            assert cancelled.wait(timeout=10)
            assert args[7]()
            raise ocr.ReviewStopped("Cancelled")
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(verification, "api_verify", api)
    monkeypatch.setattr(ocr, "configuration", configuration)
    reviews.perform_review(identity)
    assert len(started) == 2 and cleaned == [True]
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate).order_by(ReviewCandidate.position)))
        assert all(row.status == "skipped" and row.finding_id is None for row in rows)
        assert all(row.usage == {"tokens": None} for row in rows[:2])
        assert all(not row.usage for row in rows[2:])
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
    assert not pipeline["reviews"]


def test_parallel_worker_failure_joins_peers_and_never_replays_unknown_usage(pipeline, monkeypatch):
    pipeline["comments"] = independent_comments(2)
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    identity = new_run(assessed=True)
    barrier, exited = Barrier(2), Event()
    calls = []
    def api(*args, **kwargs):
        calls.append(args[4]["finding"]["path"])
        barrier.wait(timeout=10)
        if args[4]["finding"]["path"] == "file_1.py":
            raise SystemExit("Worker terminated")
        try:
            for _ in range(500):
                if args[7]():
                    raise ocr.ReviewStopped("Peer stopped")
                exited.wait(0.01)
            pytest.fail("The peer was not stopped")
        finally:
            exited.set()
    monkeypatch.setattr(verification, "api_verify", api)
    with pytest.raises(SystemExit, match="Worker terminated"):
        reviews.perform_review(identity)
    assert exited.is_set()
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate)))
        assert all(row.status == "running" and row.usage == {"tokens": None} for row in rows)
    reviews.perform_review(identity)
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate)))
        assert all(row.status == "failed" and row.usage == {"tokens": None} for row in rows)
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["citation", "identity", "expand", "excluded", "unknown", "invalid_twice", "three_passes"])
def test_parallel_quality_checks_and_every_pass_usage_are_preserved(pipeline, monkeypatch, failure):
    pipeline["comments"] = independent_comments(2)
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    counts, lock, barrier = {}, Lock(), Barrier(2)
    def expand(repo, env, run, context, paths, stopped):
        assert paths in (["helper1.py"], ["helper2.py"]) if failure == "three_passes" else paths == ["helper.py"]
        if failure == "excluded":
            raise ValueError("Necessary assessment evidence is excluded")
        context["sources"]["current"][paths[0]] = "dependency = 1\n"
    def api(*args, **kwargs):
        context = args[4]
        path = context["finding"]["path"]
        with lock:
            counts[path] = counts.get(path, 0) + 1
            attempt = counts[path]
        if attempt == 1:
            barrier.wait(timeout=10)
        raw = verdict(context, required_paths=[])
        if path == "file_0.py":
            if attempt == 1 or failure in {"invalid_twice", "three_passes"}:
                if failure == "unknown":
                    raise verification.VerificationOutputError("Unknown usage", {"tokens": None},
                                                               feedback={"field": "evidence[0]"})
                if failure in {"citation", "invalid_twice"}:
                    raw["evidence"][0]["quote"] = "invented"
                elif failure == "identity":
                    raw.update(relationship="duplicate", finding_id="not-a-finding")
                elif failure in {"expand", "excluded", "three_passes"}:
                    raw.update(verdict="uncertain", required_paths=[f"helper{attempt}.py" if failure == "three_passes" else "helper.py"])
            elif failure == "expand":
                assert context["sources"]["current"]["helper.py"] == "dependency = 1\n"
            else:
                assert context["validation_feedback"]["field"].startswith(
                    "finding_id" if failure == "identity" else "evidence",
                )
        try:
            result = kwargs["validator"](raw, context)
        except verification.ResultValidationError as exc:
            raise verification.VerificationOutputError(str(exc), {"tokens": 25}, feedback=exc.feedback) from exc
        return result, {"tokens": 25}
    monkeypatch.setattr(assessment, "expand_context", expand)
    monkeypatch.setattr(verification, "api_verify", api)
    identity = run_and_publish()
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate).order_by(ReviewCandidate.position)))
        assert rows[1].status == "completed" and rows[1].usage == {"tokens": 25}
        withheld = failure in {"excluded", "unknown", "invalid_twice", "three_passes"}
        assert rows[0].status == ("failed" if failure in {"excluded", "unknown", "invalid_twice"} else "completed")
        assert rows[0].usage == {"tokens": None if failure == "unknown" else 25 if failure == "excluded" else 75 if failure == "three_passes" else 50}
        assert assessment.summary(db, db.get(ReviewRun, identity)).new_findings == (1 if withheld else 2)
        if failure == "three_passes":
            assert rows[0].result["verdict"] == "uncertain"
        if failure == "expand":
            assert rows[0].context["sources"]["current"]["helper.py"] == "dependency = 1\n"
    assert counts == {"file_0.py": 1 if failure in {"excluded", "unknown"} else 3 if failure == "three_passes" else 2, "file_1.py": 1}


def test_parallel_response_progress_precedes_atomic_batch_acceptance(pipeline, monkeypatch):
    from time import monotonic, sleep

    pipeline["comments"] = independent_comments(2)
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    identity = new_run(assessed=True)
    started, release = Event(), Event()

    def api(*args, **kwargs):
        context = args[4]
        if context["finding"]["path"] == "file_1.py":
            started.set()
            assert release.wait(10)
        return kwargs["validator"](verdict(context, required_paths=[]), context), {"tokens": 25}

    monkeypatch.setattr(verification, "api_verify", api)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(reviews.perform_review, identity)
        try:
            assert started.wait(10)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with transaction() as db:
                    rows = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == identity).order_by(ReviewCandidate.position)))
                    if rows[0].progress_phase == "validating":
                        assert all(row.status == "running" and row.result is None and row.finding_id is None for row in rows)
                        assert rows[0].usage["tokens"] is None
                        assert db.get(ReviewRun, identity).progress["assessment_revision"] >= 3
                        assert db.scalar(select(func.count()).select_from(Finding)) == 0
                        break
                sleep(0.01)
            else:
                pytest.fail("The completed response was not exposed before its peer finished")
            assert pipeline["reviews"] == []
        finally:
            release.set()
        future.result(timeout=10)
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == identity)))
        assert all(row.status == "completed" and row.progress_phase is None and row.usage == {"tokens": 25} for row in rows)
