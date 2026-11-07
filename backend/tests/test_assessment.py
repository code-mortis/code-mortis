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

from code_mortis import jobs
from code_mortis.api.routers import members as members_routes
from code_mortis.core.db import now, transaction
from code_mortis.core.security import seal
from code_mortis.integrations import github, ocr, runner_client
from code_mortis.models import (
    Connection,
    Finding,
    Job,
    Membership,
    Occurrence,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRun,
    User,
)
from code_mortis.reviews import assessment, checks, verification
from code_mortis.reviews import pipeline as review_pipeline
from code_mortis.reviews import policy as review_policy
from code_mortis.reviews import service as reviews
from code_mortis.schemas.common import RepoSettings

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
    monkeypatch.setattr(review_pipeline.git_source, "review_snapshot",
        lambda *a, **k: nullcontext((Path("/tmp/repo"), {}, {}, "b" * 40)))
    state = {"comments": [copy.deepcopy(COMMENT)], "calls": 0, "reviews": [], "head": "a" * 40,
             "allowances": [], "native_sizes": [], "native_ids": [], "analysis_calls": 0, "instructions": []}
    calls_lock = Lock()

    def request(method, path, token, **kwargs):
        if method == "GET":
            data = remote(state["head"])
            data.update(state=state.get("pr_state", "open"), merged=state.get("merged", False))
            data["base"]["sha"] = state.get("base", data["base"]["sha"])
            return data
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
                            "review_sensitivity": run.config["settings"].get("review_sensitivity"),
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
        state["instructions"].append(kwargs["instructions"])
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
        state["instructions"].append(payload["prompt"].split("# Code Mortis verification evidence\n\n", 1)[0].rstrip())
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


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("sensitivity", [None, "low_noise", "balanced", "broad"])
def test_assessment_uses_saved_sensitivity_on_retry(pipeline, client, kind, sensitivity):
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {"review_sensitivity": sensitivity or "balanced"}
        if kind != "api":
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
            connection.secret = seal("{}")
    identity = new_run(assessed=True)
    if sensitivity is None:
        with transaction() as db:
            run = db.get(ReviewRun, identity)
            run.config = {**run.config, "settings": {key: value for key, value in run.config["settings"].items()
                                                    if key != "review_sensitivity"}}
    pipeline["failure"] = verification.VerificationOutputError("Temporary failure", {"tokens": 25})
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert candidate.status == "failed"
        candidate_id = candidate.id
        saved = copy.deepcopy(db.get(ReviewRun, identity).config)
        db.get(Repository, 22).settings = {"review_sensitivity": "broad" if sensitivity != "broad" else "low_noise"}
    pipeline.pop("failure")
    response = client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry")
    assert response.status_code == 202, response.text
    reviews.perform_review(identity)
    assert len(pipeline["instructions"]) == 2
    assert pipeline["instructions"][0] == pipeline["instructions"][1]
    if sensitivity is None:
        assert pipeline["instructions"][0] == assessment.INSTRUCTIONS
    else:
        assert review_policy.instructions(saved["settings"]) in pipeline["instructions"][0]
        assert "return rejected and explain" in pipeline["instructions"][0]
    with transaction() as db:
        assert db.get(ReviewRun, identity).config == saved
        assert db.get(ReviewCandidate, candidate_id).result["verdict"] == "confirmed"


def test_sensitivity_change_reassesses_confirmations_without_resolving_findings(pipeline):
    first = run_and_publish()
    with transaction() as db:
        original = db.scalar(select(Finding))
        finding_id, status = original.id, original.status
        first_config = copy.deepcopy(db.get(ReviewRun, first).config)
    run_and_publish()
    assert pipeline["calls"] == 1
    with transaction() as db:
        db.get(Repository, 22).settings = {"review_sensitivity": "low_noise"}
    pipeline["decision"] = {"verdict": "rejected", "explanation": "Excluded by the Low noise reporting policy."}
    third = run_and_publish()
    assert pipeline["calls"] == 2
    with transaction() as db:
        assert db.get(Finding, finding_id).status == status
        assert db.get(ReviewRun, first).config == first_config
        candidate = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == third))
        assert candidate.result["verdict"] == "rejected" and "policy" in candidate.result["explanation"]


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("kind", ["api"])
def test_retry_assesses_one_candidate_and_preserves_history_and_publication(pipeline, client, kind, merged):
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
        publication_jobs = db.scalar(select(func.count()).select_from(Job).where(Job.kind == "publish"))
        run.started_at -= timedelta(days=1)
        db.get(Repository, 22).connection_id = None
    assert len(pipeline["reviews"]) == 1
    if merged:
        pipeline.update(pr_state="closed", merged=True)
    path = f"/api/v1/runs/{identity}/candidates/{target_id}"
    response = client.post(path + "/retry")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert response.json()["assessment_only"] is merged
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
    assert len(pipeline["reviews"]) == (1 if merged else 2)
    if kind != "api":
        assert pipeline["native_ids"][-1] not in native_ids
    with transaction() as db:
        target = db.get(ReviewCandidate, target_id)
        assert target.status == "completed" and bool(target.finding_id) is not merged
        assert target.assessment_only is merged
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
        if merged:
            assert run.publication_plan == original_plan
            assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "publish")) == publication_jobs
            assert db.scalar(select(func.count()).select_from(Finding)) == 1
            assert db.scalar(select(func.count()).select_from(Occurrence)) == 1
            payload = checks.check_payload(run, 1)
            assert payload["conclusion"] == "success"
            assert payload["output"]["title"] == "Assessment complete at original commit"
            assert payload["completed_at"] == run.progress["assessment_retry"]["finished_at"]
            assert "without creating findings" in payload["output"]["summary"]
    assert client.get(path).json()["assessment_only"] is merged


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


@pytest.mark.parametrize("merged", [False, True])
def test_assessment_retry_waits_for_previous_worker_to_release_its_claim(pipeline, client, merged):
    pipeline["decision"] = {"evidence": [{"revision": "current", "path": COMMENT["path"],
        "start_line": 7, "end_line": 7, "quote": "invented"}]}
    identity = run_and_publish()
    with transaction() as db:
        candidate_id = db.scalar(select(ReviewCandidate.id))
        previous = reviews.enqueue(db, "review", {"run_id": identity})
        previous.status, previous.attempts, previous.lease_until = "running", 1, now() + timedelta(minutes=5)
        previous_id = previous.id
        run = db.get(ReviewRun, identity)
        run.progress = {**run.progress, "execution": f"{previous.id}:1"}
    pipeline.update(pr_state="closed" if merged else "open", merged=merged, decision={})
    assert client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry").status_code == 202
    with transaction() as db:
        retry_id = db.scalar(select(Job.id).where(Job.kind == "review", Job.id != previous_id))
    calls = pipeline["calls"]
    jobs.execute_job(retry_id)
    with transaction() as db:
        assert db.get(Job, retry_id).status == "queued"
        assert db.get(Job, retry_id).attempts == 0
        assert db.get(ReviewRun, identity).status == "queued"
        assert "started_at" not in db.get(ReviewRun, identity).progress["assessment_retry"]
        assert db.get(ReviewCandidate, candidate_id).status == "queued"
        assert pipeline["calls"] == calls
        db.get(Job, previous_id).status = "done"
        db.get(Job, retry_id).available_at = now()
    jobs.execute_job(retry_id)
    with transaction() as db:
        assert db.get(Job, retry_id).status == "done"
        assert db.get(ReviewCandidate, candidate_id).status == "completed"
        assert pipeline["calls"] == calls + 1
        assert pipeline["analysis_calls"] == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_history_conflict_retry_reuses_analysis_and_preserves_attempt(pipeline, client, legacy):
    pipeline["comments"] += [{**COMMENT, "path": "unrelated.py"}]
    pipeline["decision"] = {"verdict": "uncertain", "relationship": "uncertain", "finding_id": None}
    identity = run_and_publish()
    with transaction() as db:
        candidates = list(db.scalars(select(ReviewCandidate).order_by(ReviewCandidate.position)))
        candidate, untouched = candidates
        candidate.status, candidate.result = "skipped", None
        candidate.failure_reason = None if legacy else "history_changed"
        candidate.error = ("The pull request or finding history changed during assessment; this proposed finding was withheld from GitHub. Check the latest review."
                           if legacy else str(assessment.HistoryChanged()))
        candidate_id, usage, context, error = candidate.id, candidate.usage, candidate.context, candidate.error
        untouched_state = (untouched.status, untouched.result, untouched.usage)
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "pipeline_version": 2}
        run.stages = {stage: {"status": "completed", "tokens": 0} for stage in ("ocr", "assessment", "verification", "summary")}
        run.stages["assessment"] = {"status": "partial", "tokens": 50}
        run.progress = {**run.progress, "pipeline_finalized": True, "assessment_deferred": True}
    path = f"/api/v1/runs/{identity}/candidates/{candidate_id}"
    assert client.get(path).json()["retryable"]
    response = client.post(path + "/retry")
    assert response.status_code == 202, response.text
    assert response.json()["failure_reason"] is None
    assert client.post(path + "/retry").status_code == 409
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        candidate = db.get(ReviewCandidate, candidate_id)
        assert candidate.status == "completed" and candidate.failure_reason is None
        assert candidate.attempts[0]["usage"] == usage
        assert candidate.attempts[0]["context"] == context
        assert candidate.attempts[0]["error"] == error
        other = db.scalar(select(ReviewCandidate).where(ReviewCandidate.id != candidate_id))
        assert (other.status, other.result, other.usage) == untouched_state
        run = db.get(ReviewRun, identity)
        assert run.stages["assessment"]["status"] == "completed"
        assert run.stages["assessment"]["tokens"] == 75
        assert checks.check_payload(run, 0)["conclusion"] == "success"
    assert pipeline["analysis_calls"] == 1 and pipeline["calls"] == 3
    assert pipeline["reviews"] == []


@pytest.mark.parametrize("status,reason,error", [
    ("skipped", None, "The run was cancelled."),
    ("skipped", None, "Assessment exceeded the time limit."),
    ("skipped", None, "Access to the repository or AI connection changed."),
    ("queued", "history_changed", str(assessment.HistoryChanged())),
    ("running", "history_changed", str(assessment.HistoryChanged())),
    ("completed", "history_changed", str(assessment.HistoryChanged())),
])
def test_only_terminal_history_conflicts_are_retryable(pipeline, client, status, reason, error):
    pipeline["decision"] = {"verdict": "uncertain", "relationship": "uncertain", "finding_id": None}
    identity = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        row.status, row.failure_reason, row.error, row.result = status, reason, error, None
        candidate_id = row.id
    path = f"/api/v1/runs/{identity}/candidates/{candidate_id}"
    assert not client.get(path).json()["retryable"]
    assert client.post(path + "/retry").status_code == 409


@pytest.mark.parametrize("outcome", ["uncertain", "rejected", "failed", "skipped"])
def test_check_distinguishes_withheld_decisions_from_processing_errors(pipeline, outcome):
    pipeline["decision"] = {"verdict": "uncertain", "relationship": "uncertain", "finding_id": None}
    identity = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        if outcome in {"failed", "skipped"}:
            row.status, row.result = outcome, None
        else:
            row.result = {**row.result, "verdict": outcome}
        db.flush()
        run = db.get(ReviewRun, identity)
        assessment.record_summary(db, run)
        payload = checks.check_payload(run, 0)
        assert payload["conclusion"] == ("neutral" if outcome in {"failed", "skipped"} else "success")
        if outcome in {"failed", "skipped"}:
            assert payload["output"]["title"] == "Assessment incomplete"
            assert "retry eligible assessments" in payload["output"]["summary"]


@pytest.mark.parametrize("decision", ["confirmed", "uncertain", "rejected"])
def test_merged_retry_keeps_existing_finding_state_and_survives_sync(pipeline, client, monkeypatch, decision):
    original = run_and_publish()
    pipeline["comments"][0]["content"] = "Rephrased proposed issue."
    pipeline["decision"] = {"evidence": [{"revision": "current", "path": COMMENT["path"],
        "start_line": 7, "end_line": 7, "quote": "invented"}]}
    identity = new_run(assessed=True, parallel=True)
    reviews.perform_review(identity)
    # These focused fixtures do not execute asynchronous summary/verification jobs.
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status = "completed"
        run.stages = {**run.stages, "verification": {"status": "skipped", "tokens": 0}, "summary": {"status": "skipped", "tokens": 0}}
    reviews.publish_run(identity)
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        # The candidate may have been deferred behind verification, so start from the saved conflict.
        row.status, row.result, row.error, row.failure_reason = "skipped", None, str(assessment.HistoryChanged()), "history_changed"
        candidate_id = row.id
        finding = db.scalar(select(Finding))
        finding.status, finding.desired_status = "dismissed", None
        before = (finding.id, finding.last_seen_run_id, finding.sync_version, finding.status, finding.desired_status)
        assert finding.last_seen_run_id == original
    pipeline.update(pr_state="closed", merged=True)
    path = f"/api/v1/runs/{identity}/candidates/{candidate_id}"
    assert client.post(path + "/retry").status_code == 202
    pipeline["decision"] = {"verdict": decision}

    def concurrent_sync():
        with transaction() as db:
            data = github.request("GET", "/pull", "token")
            data.update(title="Merged PR", html_url="https://github.com/acme/repo/pull/1", user={"login": "owner"})
            pr = reviews.upsert_pr(db, db.get(Repository, 22), data)
            assert pr.state == "merged"
            assert not db.get(ReviewRun, identity).cancel_requested
        # A late finalizer must not take over this targeted retry.
        from code_mortis.reviews import pipeline as stages
        stages.finalize(identity)

    pipeline["during_assessment"] = concurrent_sync
    def unexpected(*a, **k):
        pytest.fail("Historical retry must not execute summary or verification")
    monkeypatch.setattr(verification, "perform", unexpected)
    monkeypatch.setattr(reviews.pr_summary, "generate", unexpected)
    calls, published = pipeline["analysis_calls"], len(pipeline["reviews"])
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        row = db.get(ReviewCandidate, candidate_id)
        assert row.status == "completed" and row.result["verdict"] == decision
        assert row.assessment_only and row.finding_id is None
        if decision == "confirmed":
            assert row.result["relationship"] == "duplicate" and row.result["finding_id"] == before[0]
        finding = db.get(Finding, before[0])
        assert (finding.id, finding.last_seen_run_id, finding.sync_version, finding.status, finding.desired_status) == before
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 1
        run = db.get(ReviewRun, identity)
        assert run.status == run.stages["assessment"]["status"] == "completed"
        assert checks.check_payload(run, 0)["conclusion"] == "success"
        # Even a stale publication delivery with pending state is fenced before remote access.
        run.publication = "pending"
    monkeypatch.setattr(github, "request", unexpected)
    monkeypatch.setattr(github, "pages", unexpected)
    reviews.publish_run(identity)
    assert pipeline["analysis_calls"] == calls and len(pipeline["reviews"]) == published


@pytest.mark.parametrize("change", ["closed_unmerged", "head", "base", "permission", "cancel", "ignored"])
def test_merged_retry_revalidates_target_and_access_at_execution(pipeline, client, monkeypatch, change):
    pipeline["decision"] = {"evidence": [{"revision": "current", "path": COMMENT["path"],
        "start_line": 7, "end_line": 7, "quote": "invented"}]}
    identity = run_and_publish()
    with transaction() as db:
        candidate_id = db.scalar(select(ReviewCandidate.id))
    pipeline.update(pr_state="closed", merged=True)
    assert client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry").status_code == 202
    calls = pipeline["calls"]
    if change == "closed_unmerged":
        pipeline["merged"] = False
    elif change == "head":
        pipeline["head"] = "c" * 40
    elif change == "base":
        pipeline["base"] = "c" * 40
    elif change == "permission":
        monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(403, "Denied")))
    else:
        with transaction() as db:
            if change == "cancel":
                reviews.cancel_review(db, db.get(ReviewRun, identity))
            else:
                db.get(PullRequest, "pr").ignored = True
    with transaction() as db:
        job_id = db.scalar(select(Job.id).where(Job.kind == "review", Job.payload["run_id"].as_string() == identity))
    jobs.execute_job(job_id)
    assert pipeline["calls"] == calls and pipeline["reviews"] == []
    with transaction() as db:
        assert db.get(ReviewCandidate, candidate_id).result is None


@pytest.mark.parametrize("merged,published", [(False, True), (True, True), (True, False)])
@pytest.mark.parametrize("stop", ["cancel_queued", "cancel_running", "sync_queued", "sync_running", "remote", "worker",
                                 "connection_queued", "installation_queued", "member_queued"])
def test_stopped_assessment_retry_finishes_stage_and_preserves_historical_publication(pipeline, client, monkeypatch, merged, published, stop):
    from code_mortis.api.routers import subscriptions
    from code_mortis.integrations import installations
    from code_mortis.reviews import pipeline as stages

    pipeline["comments"] += [{**COMMENT, "path": "src/failed.py"}]
    pipeline["respond"] = lambda context: verdict(context, **({"evidence": [{"revision": "current",
        "path": "src/failed.py", "start_line": 7, "end_line": 7, "quote": "invented"}]}
        if context["finding"]["path"] == "src/failed.py" else {"verdict": "uncertain", "relationship": "uncertain"} if not published else {}))
    identity = new_run(assessed=True, parallel=True)
    reviews.perform_review(identity)
    with transaction() as db:
        # This fixture only executes OCR/assessment; finish the independent summary stage.
        stages.update(db.get(ReviewRun, identity), "summary", "skipped", tokens=0)
    stages.finalize(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status == "completed" and run.publication == ("published" if published else "no_new_comments")
        original = (run.publication, run.finished_at, run.report, run.publication_plan, run.review_ids)
        other_stages = {key: value for key, value in run.stages.items() if key != "assessment"}
        candidate_id = db.scalar(select(ReviewCandidate.id).where(ReviewCandidate.status == "failed"))
    pipeline.update(pr_state="closed" if merged else "open", merged=merged)
    assert client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry").status_code == 202
    retried_at = now()

    def interrupt():
        if stop.startswith("cancel"):
            assert client.post(f"/api/v1/runs/{identity}/cancel").status_code == 200
        elif stop.startswith("connection"):
            with transaction() as db:
                subscriptions.cancel_connection_runs(db, "connection")
        elif stop.startswith("installation"):
            with transaction() as db:
                installations.deactivate_repositories(db, [db.get(Repository, 22)], "Installation removed.")
        elif stop.startswith("member"):
            with transaction() as db:
                admin = User(id=8, login="admin", role="admin", access_token="token")
                db.add_all([admin, Membership(github_id=8, login="admin", role="admin")])
                db.flush()
                members_routes.remove_member(7, db, admin)
        else:
            with transaction() as db:
                data = github.request("GET", "/pull", "token")
                data["head"]["sha"] = "c" * 40
                reviews.upsert_pr(db, db.get(Repository, 22), data)

    if stop.endswith("queued"):
        interrupt()
    elif stop.endswith("running"):
        pipeline["during_assessment"] = interrupt
    elif stop == "remote":
        pipeline["head"] = "c" * 40
    else:
        monkeypatch.setattr(ocr, "review", lambda *a, **k: (_ for _ in ()).throw(ocr.ReviewStopped("Stopped during checkout")))
    reviews.perform_review(identity)
    stages.finalize(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        candidate = db.get(ReviewCandidate, candidate_id)
        assert run.status == ("superseded" if stop.startswith("sync") or stop in {"remote", "worker"} else "cancelled")
        assert candidate.status == "skipped" and candidate.result is None
        assert run.stages["assessment"]["status"] == "cancelled"
        assert run.stages["assessment"]["finished_at"]
        assert {key: value for key, value in run.stages.items() if key != "assessment"} == other_stages
        assert (run.report, run.publication_plan, run.review_ids) == original[2:]
        payload = checks.check_payload(run, int(published))
        assert payload["status"] == "completed" and payload["conclusion"] == "cancelled"
        if merged:
            assert (run.publication, run.finished_at) == original[:2]
            assert payload["output"]["title"] == "Assessment cancelled or superseded"
            assert payload["completed_at"] == run.progress["assessment_retry"]["finished_at"]
            assert payload["completed_at"] >= retried_at.isoformat()
        assert db.scalar(select(func.count()).select_from(Finding)) == int(published)
        assert db.scalar(select(func.count()).select_from(Occurrence)) == int(published)
    assert len(pipeline["reviews"]) == int(published)


def test_assessment_conflict_migration_preserves_existing_candidates(pipeline):
    pipeline["decision"] = {"verdict": "uncertain", "relationship": "uncertain", "finding_id": None}
    run_and_publish()
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        identity, snapshot, result, context = candidate.id, candidate.snapshot, candidate.result, candidate.context
    config = Config("alembic.ini")
    command.downgrade(config, "20261022_installation_removal")
    command.upgrade(config, "head")
    with transaction() as db:
        candidate = db.get(ReviewCandidate, identity)
        assert (candidate.snapshot, candidate.result, candidate.context) == (snapshot, result, context)
        assert candidate.failure_reason is None


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


def test_manual_assessment_retry_cannot_resume_a_retired_automatic_run(pipeline, client):
    with transaction() as db:
        run = reviews.create_run(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "automatic")
        run.config = {key: value for key, value in run.config.items() if key != "pipeline_version"}
        run.stages = {}
        db.flush()
        identity = run.id
    pipeline["failure"] = verification.VerificationOutputError("Previous assessment failure", {"tokens": 25})
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        candidate_id, before = candidate.id, copy.deepcopy((candidate.snapshot, candidate.usage, candidate.attempts))
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "disconnected"
        run = db.get(ReviewRun, identity)
        run.engine, run.owner_id = "chatgpt", 7
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": "chatgpt", "owner_id": 7}}
    calls = pipeline["calls"]
    response = client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry")
    assert response.status_code == 410 and "retired" in response.json()["detail"]
    with transaction() as db:
        candidate = db.get(ReviewCandidate, candidate_id)
        assert (candidate.snapshot, candidate.usage, candidate.attempts) == before
        assert db.get(ReviewRun, identity).trigger == "automatic"
    assert pipeline["calls"] == calls and not pipeline["native_ids"]


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("change", ["commit", "base", "closed", "connection", "budget", "unknown_usage", "permission"])
def test_assessment_retry_rejects_unsafe_or_unfunded_execution(pipeline, client, monkeypatch, change, conflict, merged):
    pipeline["decision"] = {"evidence": [{"revision": "current", "path": COMMENT["path"],
        "start_line": 7, "end_line": 7, "quote": "invented"}]}
    identity = run_and_publish()
    if merged:
        pipeline.update(pr_state="closed", merged=True)
    with transaction() as db:
        candidate_id = db.scalar(select(ReviewCandidate.id))
        if conflict:
            candidate = db.get(ReviewCandidate, candidate_id)
            candidate.status, candidate.failure_reason, candidate.error = "skipped", "history_changed", str(assessment.HistoryChanged())
        run = db.get(ReviewRun, identity)
        if change == "connection":
            db.get(Connection, "connection").base_url = "https://changed.example.com"
        elif change == "budget":
            run.report = {"summary": {"total_tokens": run.config["settings"]["token_budget"]}}
        elif change == "unknown_usage":
            db.get(ReviewCandidate, candidate_id).usage = {"tokens": None}
    if change == "commit":
        pipeline["head"] = "c" * 40
    elif change == "base":
        pipeline["base"] = "c" * 40
    elif change == "closed":
        pipeline.update(pr_state="closed", merged=False)
    if change == "permission":
        monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(403, "Denied")))
    response = client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry")
    assert response.status_code in {403, 409}, response.text
    with transaction() as db:
        assert db.get(ReviewCandidate, candidate_id).status == ("skipped" if conflict else "failed")
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
        assert checks.check_payload(run, 0)["conclusion"] == ("neutral" if counts.failed or counts.skipped else "success")
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
        "incomplete": 2, "failed": 1, "skipped": 0, "published_comments": 0,
    }
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.publication = "completed", "published"
        payload = checks.check_payload(run, 3)
        assert (payload["status"], payload["conclusion"]) == ("completed", "neutral")
        assert payload["output"]["title"] == "Assessment incomplete"
        assert "Not supported candidates: 5. Withheld candidates: 2." in payload["output"]["summary"]
        run.progress = {**run.progress, "verification": {"uncertain": 1}}
        payload = checks.check_payload(run, 3)
        assert payload["conclusion"] == "neutral"
        assert payload["output"]["title"] == "Assessment incomplete"


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
        assert row.failure_reason == "history_changed"
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
    assert not pipeline["reviews"]


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("budget", [200000, None])
@pytest.mark.parametrize("used", [0, 200000])
def test_retired_native_clients_cannot_start_assessment(pipeline, monkeypatch, kind, budget, used):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
        db.get(Repository, 22).settings = {"token_budget": budget}
    pipeline["tokens"] = used
    monkeypatch.setattr(runner_client, "review", lambda *a, **k: pytest.fail("Retired native assessment executed"))
    with pytest.raises(ValueError, match="retired"):
        new_run(assessed=True)
    assert pipeline["calls"] == pipeline["analysis_calls"] == 0
    assert not pipeline["reviews"]
    with transaction() as db:
        assert not db.scalar(select(ReviewRun))
        assert not db.scalar(select(ReviewCandidate))


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
@pytest.mark.parametrize("known", [False, True])
def test_retired_native_assessment_retry_preserves_usage_and_history(pipeline, monkeypatch, client, kind, known):
    pipeline["failure"] = verification.VerificationOutputError("Historical assessment failure", {"tokens": 25 if known else None})
    identity = run_and_publish()
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        candidate_id = candidate.id
        candidate.usage = {"tokens": 25 if known else None}
        before = copy.deepcopy((candidate.snapshot, candidate.status, candidate.usage, candidate.attempts))
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
        run = db.get(ReviewRun, identity)
        run.engine, run.owner_id = kind, 7
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": kind, "owner_id": 7}}
    calls = pipeline["calls"]
    monkeypatch.setattr(runner_client, "review", lambda *a, **k: pytest.fail("Retired native assessment executed"))
    response = client.post(f"/api/v1/runs/{identity}/candidates/{candidate_id}/retry")
    assert response.status_code == 410 and "retired" in response.json()["detail"]
    with transaction() as db:
        candidate = db.get(ReviewCandidate, candidate_id)
        assert (candidate.snapshot, candidate.status, candidate.usage, candidate.attempts) == before
        assert candidate.usage == {"tokens": 25 if known else None}
    assert pipeline["calls"] == calls and not pipeline["native_ids"]


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


@pytest.mark.parametrize("kind", ["api"])
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


@pytest.mark.parametrize("kind", ["api"])
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


@pytest.mark.parametrize("kind", ["api"])
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


@pytest.mark.parametrize("kind", ["api"])
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
    assert [item["id"] for item in context["history"]] == ["moved-block", "different-literal"]
    # The second finding is connected through the first finding's path, despite its distinct literal.
    assert assessment.related_history(repo, {}, {}, head, {**COMMENT, "path": "new.py"},
        {"findings": previous["findings"][1:], "prior_candidates": []}, lambda: False)[0] == []
    previous["prior_candidates"], previous["findings"] = previous["findings"], []
    monkeypatch.setattr(verification, "filter_context", lambda repo, env, sources, options, stopped: set(sources) - {"another.py"})
    with pytest.raises(ValueError, match="history is excluded"):
        assessment.build_context(repo, {}, {}, run, {**COMMENT, "path": "new.py", "source_sha": head}, previous, lambda: False)


@pytest.mark.parametrize("kind", ["api"])
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
        context = payload["_evidence_context"]
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


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("outcome", ["corrected", "unavailable", "repeated"])
@pytest.mark.parametrize("parallel", [False, True])
def test_missing_requested_source_can_be_corrected_without_losing_usage(pipeline, monkeypatch, kind, outcome, parallel):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    missing, available = "frontend/tsconfig.app.json", "frontend/tsconfig.json"
    sources = {available: '{"compilerOptions":{"strict":true}}', "frontend/private.json": "excluded",
               "frontend/linked.json": "symlink", "elsewhere/unrelated.json": "unrelated"}
    monkeypatch.setattr(verification, "filter_context", lambda _r, _e, paths, *a: set(paths) - {"frontend/private.json"})
    def git(repo, env, *args, stopped):
        assert not stopped()
        if args[0] == "ls-tree":
            paths = sources if "-r" in args else [args[-1]] if args[-1] in sources else []
            return "\0".join(f"{'120000' if path.endswith('linked.json') else '100644'} blob {'a' * 40}\t{path}" for path in paths)
        if args[0] == "show":
            return sources[args[1].split(":", 1)[1]]
        assert args[0] == "diff"
        return ""
    monkeypatch.setattr(ocr, "git", git)
    def respond(context):
        if pipeline["calls"] == 1:
            return verdict(context, verdict="uncertain", required_paths=[missing])
        if outcome == "corrected" and pipeline["calls"] == 3:
            assert context["sources"]["current"][available] == sources[available]
            assert "validation_feedback" not in context
            return verdict(context, verdict="rejected", required_paths=[])
        feedback = context["validation_feedback"]
        assert feedback["field"] == "required_paths" and feedback["path"] == missing
        assert feedback["available_paths"] == [available]
        assert missing not in context["sources"]["current"]
        return verdict(context, verdict="uncertain", required_paths=
                       [available] if outcome == "corrected" else [missing] if outcome == "repeated" else [])
    pipeline["respond"] = respond
    identity = new_run(assessed=True, parallel=parallel)
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    expected_calls = 2 if outcome == "unavailable" else 3
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == "completed" and row.error is None and row.finding_id is None
        assert row.result["verdict"] == ("rejected" if outcome == "corrected" else "uncertain")
        assert row.usage == {"tokens": 25 * expected_calls}
    assert pipeline["calls"] == expected_calls and pipeline["analysis_calls"] == 1
    assert pipeline["allowances"] == [pipeline["allowances"][0] - 25 * index for index in range(expected_calls)]
    assert not pipeline["reviews"]


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("outcome", ["uncertain", "rejected", "repeated"])
@pytest.mark.parametrize("request_pass", [1, 2])
def test_excluded_requested_evidence_is_feedback_not_an_execution_failure(pipeline, monkeypatch, kind, outcome, request_pass):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    excluded = "collections/code-mortis/api/repositories/pull-requests.yml"
    allowed = "collections/code-mortis/scripts/auth.js"
    reads = []
    monkeypatch.setattr(verification, "filter_context", lambda _r, _e, paths, *a: set(paths) - {excluded})
    def read(repo, env, sha, path, stopped):
        assert path != excluded
        reads.append(path)
        return "guard = True\n"
    monkeypatch.setattr(verification, "read_source", read)
    monkeypatch.setattr(ocr, "git", lambda *a, **kw: "")
    def respond(context):
        step = pipeline["calls"]
        if step < request_pass:
            return verdict(context, verdict="uncertain", required_paths=["collections/code-mortis/README.md"])
        if step == request_pass:
            return verdict(context, verdict="uncertain", required_paths=[excluded, allowed])
        assert context["excluded_paths"] == [excluded]
        assert context["sources"]["current"][allowed] == "guard = True\n"
        assert excluded not in context["sources"]["current"]
        feedback = context["validation_feedback"]
        assert feedback["field"] == "required_paths" and feedback["required_paths"] == [excluded]
        assert "excluded or unsupported" in feedback["reason"]
        return verdict(context, verdict="rejected" if outcome == "rejected" else "uncertain",
                       explanation="The permitted source disproves the claim." if outcome == "rejected" else
                                   "Necessary evidence is excluded by review scope; the claim remains uncertain.",
                       required_paths=[excluded] if outcome == "repeated" else [])
    pipeline["respond"] = respond
    identity = new_run(assessed=True, parallel=True)
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    expected_calls = 3 if outcome == "repeated" else request_pass + 1
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == "completed" and row.error is None and row.finding_id is None
        assert row.result["verdict"] == ("rejected" if outcome == "rejected" else "uncertain")
        assert row.usage == {"tokens": 25 * expected_calls}
        counts = assessment.summary(db, db.get(ReviewRun, identity))
        assert counts.failed == 0 and counts.incomplete == int(outcome != "rejected")
    assert reads == (["collections/code-mortis/README.md"] if request_pass == 2 else []) + [allowed]
    assert pipeline["calls"] == expected_calls and not pipeline["reviews"]
    assert pipeline["allowances"] == [pipeline["allowances"][0] - 25 * index for index in range(expected_calls)]


@pytest.mark.parametrize("failure", ["limit", "unknown_usage", "scope_check", "round_limit"])
def test_followup_failure_never_publishes_or_reuses_spent_allowance(pipeline, monkeypatch, failure):
    calls = []
    def expand(repo, env, run, context, paths, stopped):
        if failure == "scope_check":
            raise ValueError("The review engine could not check which evidence files are excluded")
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


@pytest.mark.parametrize("current,previous", [(None, None), ("balanced", "balanced"),
                                             ("low_noise", "broad"), ("balanced", None), (None, "balanced")])
def test_exact_repeat_requires_matching_review_sensitivity(current, previous):
    context = {"finding": COMMENT, "head_sha": "a" * 40, "history": [],
               "sources": {"current": {COMMENT["path"]: SOURCE}}, "review_sensitivity": current}
    context["history"] = [{"id": "existing", "sha": context["head_sha"], "snapshot": COMMENT,
                           "assessment": verdict(context), "review_sensitivity": previous}]
    assert (assessment.exact_repeat(context) is not None) == (current == previous)


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


def test_saved_native_publication_needs_github_access_without_runner_or_connection(pipeline, monkeypatch):
    identity = new_run(assessed=True)
    reviews.perform_review(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.connection_id, run.engine, run.owner_id = None, "chatgpt", 7
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": "chatgpt", "owner_id": 7}}
    monkeypatch.setattr(runner_client, "request", lambda *a, **k: pytest.fail("Publication accessed runner"))
    reviews.publish_run(identity)
    assert len(pipeline["reviews"]) == 1
    with transaction() as db:
        assert db.get(ReviewRun, identity).publication == "published"


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
    (4, 1000, "api", 1),
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
        assert kwargs["instructions"].startswith(assessment.INSTRUCTIONS + "\n\n" + review_policy.instructions(args[4]))
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
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None, "timeout_seconds": 900}
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


@pytest.mark.parametrize("failure", ["citation", "identity", "expand", "scope_check", "unknown", "invalid_twice", "three_passes"])
def test_parallel_quality_checks_and_every_pass_usage_are_preserved(pipeline, monkeypatch, failure):
    pipeline["comments"] = independent_comments(2)
    with transaction() as db:
        db.get(Repository, 22).settings = {"concurrency": 2, "token_budget": None}
    counts, lock, barrier = {}, Lock(), Barrier(2)
    def expand(repo, env, run, context, paths, stopped):
        assert paths in (["helper1.py"], ["helper2.py"]) if failure == "three_passes" else paths == ["helper.py"]
        if failure == "scope_check":
            raise ValueError("The review engine could not check which evidence files are excluded")
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
                elif failure in {"expand", "scope_check", "three_passes"}:
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
        withheld = failure in {"scope_check", "unknown", "invalid_twice", "three_passes"}
        assert rows[0].status == ("failed" if failure in {"scope_check", "unknown", "invalid_twice"} else "completed")
        assert rows[0].usage == {"tokens": None if failure == "unknown" else 25 if failure == "scope_check" else 75 if failure == "three_passes" else 50}
        assert assessment.summary(db, db.get(ReviewRun, identity)).new_findings == (1 if withheld else 2)
        if failure == "three_passes":
            assert rows[0].result["verdict"] == "uncertain"
        if failure == "expand":
            assert rows[0].context["sources"]["current"]["helper.py"] == "dependency = 1\n"
    assert counts == {"file_0.py": 1 if failure in {"scope_check", "unknown"} else 3 if failure == "three_passes" else 2, "file_1.py": 1}


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


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("repeat", [False, True])
def test_repeated_supplied_evidence_gets_one_correction_without_resetting_usage(pipeline, kind, repeat):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    def respond(context):
        if pipeline["calls"] == 1 or repeat:
            return verdict(context, verdict="uncertain", required_paths=[COMMENT["path"]])
        assert context["validation_feedback"]["kind"] == "evidence"
        return verdict(context)
    pipeline["respond"] = respond
    identity = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == ("failed" if repeat else "completed")
        assert row.usage == {"tokens": 50}
    assert pipeline["calls"] == 2
    assert len(pipeline["reviews"]) == int(not repeat)


def test_context_limit_is_not_a_correctable_assessment_failure(pipeline):
    pipeline["failure"] = verification.VerificationOutputError("Necessary history exceeds prompt limit", {"tokens": 0},
        "skipped", feedback={"kind": "context_limit"})
    identity = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == "skipped" and row.usage == {"tokens": 0}
    assert pipeline["calls"] == 1 and not pipeline["reviews"]


@pytest.mark.parametrize("kind", ["api"])
def test_assessment_validates_against_redacted_request_context(pipeline, monkeypatch, kind):
    credential = "synthetic-credential"
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.secret = seal(json.dumps({"api_key": credential}))
        if kind != "api":
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    build = assessment.build_context
    def context(*args):
        supplied = build(*args)
        path = supplied["finding"]["path"]
        supplied["sources"]["current"][path] = SOURCE.replace("while True:", f"while True: # {credential}")
        return supplied
    monkeypatch.setattr(assessment, "build_context", context)
    calls = []
    def respond(supplied):
        quote = supplied["sources"]["current"][supplied["finding"]["path"]].splitlines()[6]
        assert credential not in quote and "[redacted]" in quote
        calls.append(quote)
        return verdict(supplied, evidence=[{"revision": "current", "path": supplied["finding"]["path"],
            "start_line": 7, "end_line": 7, "quote": quote}])
    pipeline["respond"] = respond
    if kind != "api":
        monkeypatch.setattr(runner_client, "review", lambda connection, identity, payload, stopped:
            (respond(payload["_evidence_context"]), {"tokens": 25}))
    identity = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert row.status == "completed" and row.result["verdict"] == "confirmed"
        assert row.usage == {"tokens": 25} and "[redacted]" in row.result["evidence"][0]["quote"]
    assert len(calls) == 1 and len(pipeline["reviews"]) == 1
    assert credential in pipeline["context"]["sources"]["current"][COMMENT["path"]]
    repeated = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == repeated))
        assert row.status == "completed" and row.result["relationship"] == "duplicate"
        assert row.usage == {"tokens": 0}
    assert len(calls) == 1 and len(pipeline["reviews"]) == 1
