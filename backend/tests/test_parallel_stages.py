import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import select
from test_assessment import SOURCE, verdict
from test_assessment import pipeline as assessment_scenario  # noqa: F401
from test_pr_summary import CONTEXT, RESULT, completed_run, overview_api  # noqa: F401
from test_review_flow import COMMENT, new_run, remote
from test_verification import RESULT as FIXED
from test_verification import scenario as verification_scenario  # noqa: F401

from code_mortis import (
    assessment,
    github,
    jobs,
    ocr,
    pipeline,
    pr_summary,
    reviews,
    runner_client,
    verification,
)
from code_mortis.db import now, transaction
from code_mortis.models import (
    Activity,
    Connection,
    Finding,
    FindingVerification,
    Job,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRun,
)
from code_mortis.schemas import RepoSettings, StageState

DIAGRAM = {"nodes": [{"id": "retry", "label": "Retry", "paths": ["src/retry.py"]}], "edges": []}


def enable_parallel(identity):
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "pipeline_version": 2, "settings": {
            **run.config["settings"], "verification_token_budget": 4321, "summary_token_budget": 6543,
        }}
        run.stages = {stage: {"status": "pending", "tokens": 0} for stage in pipeline.STAGES}


def stub_summary_context(monkeypatch):
    monkeypatch.setattr(pr_summary, "build_context", lambda *a: copy.deepcopy(CONTEXT))
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "src/retry.py\0" if "--name-only" in a else "b" * 40)
    monkeypatch.setattr(verification, "filter_context", lambda _repo, _env, paths, *a: paths)


@pytest.mark.parametrize("status,metadata", [
    ("completed", {}), ("completed", {"waiting_for": None}), ("waiting", {"waiting_for": "verification"}),
])
def test_saved_stage_waiting_metadata_loads_without_changing_history(seed, github_stub, client, status, metadata):
    identity = new_run(parallel=True)
    stages = {name: {"status": status, "tokens": 123, **metadata} for name in ("assessment", "summary")}
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status = "completed" if status == "completed" else "running"
        run.stages = stages
    for url, keys in [
        (f"/api/v1/runs/{identity}", ("run",)),
        ("/api/v1/pulls/pr/runs", ("items", 0)),
        ("/api/v1/pulls/pr", ("pull_request", "latest_run")),
        ("/api/v1/repositories/22/pulls", ("groups", 0, "items", 0, "latest_run")),
    ]:
        response = client.get(url)
        assert response.status_code == 200, response.text
        output = response.json()
        for key in keys:
            output = output[key]
        assert output["id"] == identity
        for stage in stages:
            assert output["stages"][stage]["status"] == status
            assert output["stages"][stage]["tokens"] == 123
            assert output["stages"][stage]["waiting_for"] == metadata.get("waiting_for")
    with transaction() as db:
        assert db.get(ReviewRun, identity).stages == stages


@pytest.mark.parametrize("values,field,reason", [
    ({"waiting_for": "unknown"}, "waiting_for", "literal_error"),
    ({"waiting_for": None, "unexpected": None}, "unexpected", "extra_forbidden"),
])
def test_stage_waiting_compatibility_preserves_strict_validation(values, field, reason):
    with pytest.raises(ValidationError) as error:
        StageState.model_validate(values)
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [((field,), reason)]


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("summary", [None, "unknown", [100], 100])
def test_malformed_report_summary_preserves_analysis(verification_scenario, monkeypatch, parallel, summary):  # noqa: F811
    identity = verification_scenario["run"]
    if parallel:
        enable_parallel(identity)

    def engine(*args, **kwargs):
        result = ("completed", {"summary": summary}, [])
        kwargs["before_analysis"](None)
        kwargs["after_review"](result, Path("/tmp/repo"), {}, {})
        return result

    monkeypatch.setattr(ocr, "review", engine)
    monkeypatch.setattr(pr_summary, "generate", lambda *a: None)
    reviews.perform_review(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.progress["analysis_saved"] and run.progress["analysis_status"] == "completed"
        budget, known = assessment.remaining_budget(db, run)
        if parallel:
            assert run.stages["ocr"]["status"] == "completed" and run.stages["ocr"]["tokens"] is None
            assert run.stages["assessment"]["status"] == "completed"
            assert known and budget == run.config["settings"]["assessment_token_budget"]
        else:
            assert run.status == "completed"
            assert not known and budget == 0
            row = db.scalar(select(FindingVerification).where(FindingVerification.run_id == identity))
            assert row.status == "skipped" and "Token usage is unavailable" in row.error


@pytest.mark.parametrize("summary_started", [False, True])
@pytest.mark.parametrize("saved_status", [None, "completed", "partial", "failed", "skipped"])
def test_expired_pipeline_preserves_timeout_and_saved_analysis(seed, github_stub, monkeypatch, saved_status, summary_started):
    identity = new_run(assessed=True, overview=True, parallel=True)
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Expired runs must not start inference"))
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.started_at = now() - timedelta(seconds=run.config["settings"]["timeout_seconds"] + 1)
        if summary_started:
            pipeline.update(run, "summary", "running", execution="summary-claim", tokens=None)
            db.add(Job(kind="summarize_review", payload={"run_id": identity}, status="running"))
        if saved_status:
            run.progress = {"analysis_saved": True, "analysis_status": saved_status}
            run.report = ocr.file_limit_result(["src/retry.py"], 0)[1] if saved_status == "skipped" else {"summary": {"total_tokens": 100}}
            if saved_status in {"completed", "partial"}:
                db.add(ReviewCandidate(run_id=identity, position=0, snapshot=COMMENT))
    reviews.perform_review(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status == "running" and run.failure_reason == "timeout"
        assert "configured time limit" in run.error
        for name, stage in run.stages.items():
            if name == "summary" and summary_started:
                assert stage["status"] == "running" and stage["execution"] == "summary-claim"
            else:
                assert stage["status"] in pipeline.TERMINAL and "configured time limit" in stage["error"]
        for row in db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == identity)):
            assert row.status == "skipped" and "configured time limit" in row.error
    pipeline.finalize(identity)
    if summary_started:
        with transaction() as db:
            run = db.get(ReviewRun, identity)
            assert run.status == "running" and not run.progress.get("pipeline_finalized")
            pipeline.update(run, "summary", "completed", tokens=20)
        pipeline.finalize(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status == (saved_status or "failed") and run.progress["pipeline_finalized"]
        publish = db.scalar(select(Job.id).where(Job.kind == "publish", Job.payload["run_id"].as_string() == identity))
        assert bool(publish) == (saved_status in {"completed", "partial", "skipped"})


@pytest.mark.parametrize("observed_again", [False, True])
def test_three_workers_overlap_and_join_without_early_resolution(verification_scenario, monkeypatch, observed_again):  # noqa: F811
    identity = verification_scenario["run"]
    enable_parallel(identity)
    stub_summary_context(monkeypatch)
    overlap = threading.Barrier(3, timeout=10)
    started, finish = threading.Event(), threading.Event()
    calls = []

    def engine(*args, **kwargs):
        result = kwargs.get("saved_review")
        if result is None:
            kwargs["before_analysis"](None)
            started.set()
            overlap.wait()
            result = ("completed", {"summary": {"total_tokens": 100}}, [])
        kwargs["after_review"](result, Path("/tmp/repo"), {}, {})
        return result

    def infer(*args, **kwargs):
        summary = "instructions" in kwargs
        calls.append(("summary" if summary else "verification", args[5]))
        overlap.wait()
        assert finish.wait(10)
        return (copy.deepcopy(RESULT), {"tokens": 321}) if summary else (copy.deepcopy(FIXED), {"tokens": 50})

    monkeypatch.setattr(ocr, "review", engine)
    monkeypatch.setattr(verification, "api_verify", infer)
    execute = jobs.execute_job.run
    with ThreadPoolExecutor(max_workers=3) as pool:
        main = pool.submit(reviews.perform_review, identity)
        assert started.wait(10)
        with transaction() as db:
            auxiliary = list(db.scalars(select(Job).where(Job.kind.in_(pipeline.KINDS))))
            assert {job.kind for job in auxiliary} == {"verify_fixes", "summarize_review"}
        futures = [pool.submit(execute, job.id) for job in auxiliary]
        try:
            main.result(timeout=10)
            pipeline.finalize(identity)
            with transaction() as db:
                run = db.get(ReviewRun, identity)
                assert run.status == "running" and run.stages["assessment"]["status"] == "completed"
                finding = db.get(Finding, verification_scenario["finding"])
                assert finding.desired_status is None
                assert not db.scalar(select(Job.id).where(Job.kind == "publish", Job.payload["run_id"].as_string() == identity))
                if observed_again:
                    finding.last_seen_run_id = identity
        finally:
            finish.set()
            for future in futures:
                future.result(timeout=10)
    assert sorted(calls) == [("summary", 6543), ("verification", 4321)]
    with transaction() as db:
        assert db.get(Finding, verification_scenario["finding"]).desired_status is None
    pipeline.finalize(identity)
    pipeline.finalize(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status == "completed"
        assert {key: value["tokens"] for key, value in run.stages.items()} == {"ocr": 100, "assessment": 0, "verification": 50, "summary": 321}
        finding = db.get(Finding, verification_scenario["finding"])
        assert finding.desired_status == (None if observed_again else "resolved")
        assert len(list(db.scalars(select(Job).where(Job.kind == "publish", Job.payload["run_id"].as_string() == identity)))) == 1


@pytest.mark.parametrize("parallel_assessment", [False, True])
@pytest.mark.parametrize("verification_outcome", ["fixed", "still_present", "failed", "skipped"])
def test_assessment_waits_for_concurrent_verification_history(verification_scenario, monkeypatch, verification_outcome, parallel_assessment):  # noqa: F811
    identity = verification_scenario["run"]
    enable_parallel(identity)
    started, release = threading.Event(), threading.Event()
    calls = []
    assessment_barrier = threading.Barrier(2)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        pipeline.update(run, "summary", "skipped")
        if parallel_assessment:
            run.config = {**run.config, "settings": {**run.config["settings"], "concurrency": 4,
                          "assessment_token_budget": None, "token_budget": None}}

    def engine(*args, **kwargs):
        result = kwargs.get("saved_review")
        if result is None:
            calls.append("ocr")
            kwargs["before_analysis"](None)
            result = ("completed", {"summary": {"total_tokens": 100}}, [
                {**COMMENT, "source_sha": args[3]},
                {**COMMENT, "path": "other.py", "content": "A different proposal", "existing_code": "", "source_sha": args[3]},
            ])
        kwargs["after_review"](result, Path("/tmp/repo"), {}, {})
        return result

    def context(_repo, _env, _fetch, run, snapshot, previous, _stopped):
        return {"finding": snapshot, "head_sha": run.head_sha, "original_sha": run.head_sha,
                "sources": {"current": {snapshot["path"]: SOURCE}}, "diff": "",
                "history": previous["findings"], "prior_candidates": previous["prior_candidates"]}

    def infer(*args, **kwargs):
        if kwargs.get("validator") == assessment.validate_response:
            calls.append("assessment")
            if parallel_assessment:
                assessment_barrier.wait(timeout=10)
            assert release.is_set()
            supplied = args[4]
            assert any(reason.startswith("Commit " + supplied["head_sha"])
                       for item in supplied["history"] for reason in item["reasons"])
            # Both candidates remain observations of the existing finding, never new comments.
            return kwargs["validator"](verdict(supplied, required_paths=[]), supplied), {"tokens": 25}
        calls.append("verification")
        started.set()
        assert release.wait(10)
        if verification_outcome in {"failed", "skipped"}:
            raise verification.VerificationOutputError("Verification interrupted", {"tokens": 50}, verification_outcome)
        return {**copy.deepcopy(FIXED), "verdict": verification_outcome}, {"tokens": 50}

    monkeypatch.setattr(ocr, "review", engine)
    monkeypatch.setattr(ocr, "configuration", lambda *a, **k: nullcontext())
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(assessment, "build_context", context)
    monkeypatch.setattr(verification, "api_verify", infer)
    # OCR finishes first; candidates must remain queued until the other worker stops writing history.
    reviews.perform_review(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.stages["assessment"]["status"] == "waiting"
        assert run.stages["assessment"]["waiting_for"] == "verification"
        verify_job = db.scalar(select(Job).where(Job.kind == "verify_fixes"))
        verify_id = verify_job.id
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(jobs.execute_job.run, verify_id)
        try:
            assert started.wait(10)
            pipeline.finalize(identity)
            with transaction() as db:
                assert not db.scalar(select(Job.id).where(Job.kind == "assess_candidates"))
                assert list(db.scalars(select(ReviewCandidate.status))) == ["queued", "queued"]
                assert db.get(Finding, verification_scenario["finding"]).desired_status is None
            assert "assessment" not in calls
        finally:
            release.set()
            future.result(timeout=10)
    pipeline.finalize(identity)
    pipeline.finalize(identity)
    with transaction() as db:
        deferred = list(db.scalars(select(Job).where(Job.kind == "assess_candidates")))
        assert len(deferred) == 1
        assessment_job = deferred[0].id
        assert db.scalar(select(Activity.id).where(Activity.source == "verification"))
    # A recovered OCR delivery cannot bypass the deferred stage or repeat its model calls.
    reviews.perform_review(identity)
    assert calls.count("ocr") == 1 and "assessment" not in calls
    jobs.execute_job(assessment_job)
    jobs.execute_job(assessment_job)
    pipeline.finalize(identity)
    pipeline.finalize(identity)
    with transaction() as db:
        rows = list(db.scalars(select(ReviewCandidate)))
        assert len(rows) == 2 and all(row.status == "completed" and row.failure_reason is None for row in rows), [(row.status, row.error) for row in rows]
        assert all(row.finding_id == verification_scenario["finding"] for row in rows)
        run = db.get(ReviewRun, identity)
        assert run.stages["assessment"]["status"] == "completed"
        assert run.stages["assessment"]["tokens"] == 50
        assert run.stages["verification"]["tokens"] == 50
        assert run.status == "completed" and run.progress["pipeline_finalized"]
        assert db.get(Finding, verification_scenario["finding"]).desired_status is None
        assert len(list(db.scalars(select(Job.id).where(
            Job.kind == "publish", Job.payload["run_id"].as_string() == identity)))) == 1
    assert calls.count("ocr") == 1 and calls.count("verification") == 1 and calls.count("assessment") == 2


@pytest.mark.parametrize("stop", ["cancel", "head", "connection", "deadline", "worker_lost", "lost_completion", "stage_stopped"])
def test_deferred_assessment_keeps_guards_and_recovers_without_repeating_inference(assessment_scenario, monkeypatch, stop):  # noqa: F811
    identity = new_run(assessed=True, parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        run.progress = {"analysis_saved": True, "analysis_status": "completed"}
        run.report = {"summary": {"total_tokens": 100}}
        pipeline.update(run, "ocr", "completed", tokens=100)
        pipeline.update(run, "verification", "running")
        pipeline.update(run, "summary", "skipped")
        db.add(ReviewCandidate(run_id=identity, position=0, snapshot=COMMENT))
        db.flush()
        assert pipeline.defer_assessment(db, run)
        pipeline.update(run, "verification", "completed", tokens=0)
        if stop == "cancel":
            reviews.cancel_review(db, run)
        elif stop == "head":
            db.get(PullRequest, "pr").head_sha = "c" * 40
        elif stop == "connection":
            db.get(Connection, run.connection_id).base_url = "https://other.example/v1"
        elif stop == "deadline":
            run.started_at = now() - timedelta(seconds=run.config["settings"]["timeout_seconds"] + 1)
    pipeline.finalize(identity)
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.kind == "assess_candidates"))
        if stop in {"cancel", "head"}:
            assert job is None
        else:
            job_id = job.id
            if stop in {"worker_lost", "lost_completion"}:
                job.status, job.attempts, job.lease_until = "running", 1, now() - timedelta(seconds=1)
                pipeline.update(db.get(ReviewRun, identity), "assessment", "running", execution=f"{job_id}:1")
                row = db.scalar(select(ReviewCandidate))
                row.status, row.usage = "running", {"tokens": None, "reserved_tokens": 4321}
                if stop == "lost_completion":
                    row.status, row.usage = "completed", {"tokens": 25}
                    row.result = {"verdict": "uncertain", "relationship": "uncertain"}
    if stop in {"worker_lost", "lost_completion"}:
        monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: None)
        jobs.dispatch()
        jobs.execute_job(job_id)
    elif stop not in {"cancel", "head"}:
        if stop == "stage_stopped":
            def stop_stage():
                with transaction() as db:
                    pipeline.fail_stage(db, db.get(ReviewRun, identity), "assessment", "The assessment worker lost ownership.")
            assessment_scenario["during_assessment"] = stop_stage
        jobs.execute_job(job_id)
    pipeline.finalize(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        row = db.scalar(select(ReviewCandidate))
        assert row.status == ("completed" if stop == "lost_completion" else "skipped")
        assert row.failure_reason is None
        assert run.progress["pipeline_finalized"]
        stage = run.stages["assessment"]
        assert stage["status"] == ("cancelled" if stop in {"cancel", "head"} else "completed" if stop == "lost_completion" else "partial")
        assert stage["waiting_for"] is None
        if stop in {"worker_lost", "stage_stopped"}:
            assert stage["tokens"] is None
            assert assessment.remaining_budget(db, run)[1] is False
        elif stop == "lost_completion":
            assert stage["tokens"] == 25 and stage["error"] is None
        else:
            assert stage["tokens"] == 0
    assert assessment_scenario["calls"] == int(stop == "stage_stopped") and assessment_scenario["analysis_calls"] == 0


def test_default_settings_and_assessment_budget_are_independent(seed):
    identity = new_run(assessed=True, overview=True, parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.config["settings"]["summary_diagram_enabled"] is False
        assert run.config["settings"]["ai_fix_prompts_enabled"] is False
        run.report = {"summary": {"total_tokens": 900000}}
        run.config = {**run.config, "settings": {**run.config["settings"], "assessment_token_budget": 1000}}
        row = ReviewCandidate(run_id=identity, position=0, snapshot=COMMENT, usage={"tokens": 25}, attempts=[{"usage": {"tokens": 15}}])
        db.add(row)
        db.flush()
        assert assessment.remaining_budget(db, run) == (960, True)
        row.usage = {"tokens": None}
        db.flush()
        assert assessment.remaining_budget(db, run) == (0, False)
        run.config = {**run.config, "settings": {**run.config["settings"], "assessment_token_budget": None}}
        assert assessment.remaining_budget(db, run) == (None, False)


@pytest.mark.parametrize("location", ["row", "attempt"])
@pytest.mark.parametrize("saved_usage,expected", [
    ({}, 40), ({"tokens": 0}, 40), ({"tokens": 7}, 47),
    ({"tokens": None}, None), ({"reserved_tokens": 100}, None),
    ({"tokens": -1}, None), ({"tokens": True}, None), ({"tokens": "7"}, None),
])
def test_assessment_stage_totals_match_retry_budget(seed, location, saved_usage, expected):
    identity = new_run(assessed=True, parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "settings": {**run.config["settings"], "assessment_token_budget": 1000}}
        run.progress = {**run.progress, "analysis_status": "completed"}
        row = ReviewCandidate(run_id=identity, position=0, snapshot=COMMENT, status="completed",
                              usage={"tokens": 25}, attempts=[{"usage": {"tokens": 15}}])
        db.add(row)
        if location == "attempt":
            row.attempts = [*row.attempts, {"usage": saved_usage}]
        else:
            db.add(ReviewCandidate(run_id=identity, position=1, snapshot=COMMENT, status="completed",
                                   usage=saved_usage))
        db.flush()
        assert assessment.remaining_budget(db, run) == ((0, False) if expected is None else (1000 - expected, True))
        assessment.record_summary(db, run)
        assert run.stages["assessment"]["tokens"] == expected
        assert run.stages["assessment"]["status"] == "pending"
        pipeline.assessment_done(db, run)
        assert run.stages["assessment"]["tokens"] == expected
        assert run.stages["assessment"]["status"] == "completed"


def test_starting_candidate_preserves_pending_totals_and_updates_usage(seed, monkeypatch):
    identity = new_run(assessed=True, parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        row = ReviewCandidate(run_id=identity, position=0, snapshot=COMMENT, status="queued")
        db.add(row)
        db.flush()
        assessment.record_summary(db, run)
        previous = run.progress["assessment"]
        monkeypatch.setattr(assessment, "summary", lambda *args: pytest.fail("Pending totals have not changed"))
        row.status, row.usage = "running", {"tokens": None, "reserved_tokens": 1000}
        assessment.record_summary(db, run, advance_revision=True, totals_unchanged=True)
        assert run.progress["assessment"] == previous
        assert previous["pending"] == 1
        assert run.progress["assessment_revision"] == 1
        assert run.stages["assessment"]["tokens"] is None


@pytest.mark.parametrize("invalid", [False, True])
def test_diagram_validation_is_independent_and_rendering_is_safe(invalid):
    diagram = copy.deepcopy(DIAGRAM)
    diagram["nodes"][0]["label"] = 'A"] & # \' \\ \n```\n</details><script>test</script>'
    if invalid:
        diagram["edges"] = [{"source": "retry", "target": "invented"}]
    context = {**CONTEXT, "diagram_enabled": True}
    result = pr_summary.validate_result({**RESULT, "diagram": diagram}, context)
    assert result["narrative"] == RESULT["narrative"]
    if invalid:
        assert "diagram" not in result and context["diagram_warning"]
    else:
        rendered = pr_summary.diagram_section(result["diagram"])
        assert "<summary>Diagram</summary>" in rendered
        assert rendered.count("```") == 2 and "<script>" not in rendered
        assert "#34;] #38; #35; ' #92;" in rendered and "&#" not in rendered
    assert "diagram" not in pr_summary.validate_result({**RESULT, "diagram": diagram}, CONTEXT)


@pytest.mark.parametrize("enabled", [False, True])
def test_overview_and_finding_prompts_follow_one_setting(seed, enabled):
    identity = completed_run()
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "settings": {**run.config["settings"], "ai_fix_prompts_enabled": enabled,
                                                 "summary_diagram_enabled": enabled}}
        run.summary = {**run.summary, "result": {**RESULT, "diagram": DIAGRAM}}
        candidate = ReviewCandidate(run_id=identity, position=0, snapshot=COMMENT, status="completed", result={"verdict": "confirmed"})
        db.add(candidate)
        db.flush()
        assessment.accept(db, run, candidate, {"verdict": "confirmed", "suggestion_supported": False, "relationship": "new", "explanation": "Confirmed"})
        db.flush()
        body = pr_summary.render(db, db.get(PullRequest, "pr"), run)
        assert ("Prompt to fix with AI" in body) == enabled
        assert ("<summary>Diagram</summary>" in body) == enabled
        plan = reviews.assessed_publication_plan(db, run)
        assert ("Prompt to fix with AI" in str(plan)) == enabled


def test_queue_routing_and_personal_connection_wait(verification_scenario):  # noqa: F811
    identity = verification_scenario["run"]
    enable_parallel(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": "chatgpt", "owner_id": 7}}
        pipeline.start(db, run, "main")
        summary_job = db.scalar(select(Job).where(Job.kind == "summarize_review"))
        job_id = summary_job.id
    jobs.execute_job(job_id)
    with transaction() as db:
        assert db.get(Job, job_id).status == "queued"
        assert db.get(ReviewRun, identity).stages["summary"]["status"] == "waiting"
    assert jobs.job_queue("verify_fixes") == "verification"
    assert jobs.job_queue("assess_candidates") == "reviews"
    assert jobs.job_queue("summarize_review") == jobs.job_queue("generate_summary") == "summaries"
    assert jobs.job_queue("finalize_review") == "events"


def test_expired_summary_attempt_is_failed_without_new_inference(seed, monkeypatch):
    identity = new_run(assessed=True, overview=True, parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        run.summary = {"status": "running", "tokens": None, "inference_started_at": now().isoformat()}
        job = reviews.enqueue(db, "summarize_review", {"run_id": identity})
        job.status, job.attempts, job.lease_until = "running", 1, now() - timedelta(seconds=1)
        db.flush()
        job_id = job.id
        pipeline.update(run, "summary", "running", execution=f"{job_id}:1", tokens=None)
    dispatched = []
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: dispatched.append(kwargs))
    jobs.dispatch()
    with transaction() as db:
        assert db.get(Job, job_id).status == "failed"
        run = db.get(ReviewRun, identity)
        assert run.summary["status"] == "failed" and run.summary["tokens"] is None
    assert all(item["args"] != [job_id] for item in dispatched)


@pytest.mark.parametrize("budget", [None, 345678])
def test_migration_copies_limits_and_preserves_historical_runs(seed, budget):
    identity = new_run()
    with transaction() as db:
        db.get(Repository, 22).settings = {"token_budget": budget}
        original = copy.deepcopy(db.get(ReviewRun, identity).config)
    config = Config("alembic.ini")
    command.downgrade(config, "20261016_pr_summary")
    command.upgrade(config, "head")
    with transaction() as db:
        settings = RepoSettings.model_validate(db.get(Repository, 22).settings)
        assert settings.token_budget == settings.assessment_token_budget == settings.verification_token_budget == budget
        assert settings.summary_token_budget is None
        assert not settings.summary_diagram_enabled and not settings.ai_fix_prompts_enabled
        assert db.get(ReviewRun, identity).config == original


@pytest.mark.parametrize("outcome", ["completed", "partial", "file_limit", "summary_failed"])
def test_summary_publishes_before_finalization_and_refreshes_coverage(seed, overview_api, outcome):  # noqa: F811
    stored, writes, _ = overview_api
    completed_run()
    pr_summary.sync("pr")
    identity = new_run(assessed=True, overview=True, parallel=True)
    pr_summary.sync("pr")
    assert len(writes) == 1
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        run.summary = {"status": "completed", "tokens": 10, "result": {**RESULT, "narrative": "A new overview."}}
        status = "skipped" if outcome == "file_limit" else "partial" if outcome == "partial" else "completed"
        run.progress = {"analysis_saved": True, "analysis_status": status}
        run.report = ocr.file_limit_result(["src/retry.py", "src/test.py"], 1)[1] if outcome == "file_limit" else {"summary": {"total_tokens": 100}}
        for stage in pipeline.STAGES:
            pipeline.update(run, stage, "completed", tokens=10)
        if outcome == "summary_failed":
            run.summary = {"status": "failed", "tokens": None, "error": "Interrupted"}
            pipeline.update(run, "summary", "failed", tokens=None)
    pr_summary.sync("pr")
    assert len(writes) == (1 if outcome == "summary_failed" else 2)
    pipeline.finalize(identity)
    pr_summary.sync("pr")
    pr_summary.sync("pr")
    assert len(stored) == 1 and len(writes) == (1 if outcome == "summary_failed" else 3)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status == status
        assert db.scalar(select(Job.id).where(Job.kind == "publish", Job.payload["run_id"].as_string() == identity))


@pytest.mark.parametrize("reason", ["cancel", "revision", "disabled"])
def test_finalizer_does_not_publish_cancelled_or_stale_work(seed, reason):
    identity = new_run(assessed=True, overview=True, parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        run.progress = {"analysis_saved": True, "analysis_status": "completed"}
        for stage in pipeline.STAGES:
            pipeline.update(run, stage, "completed", tokens=10)
        if reason == "cancel":
            run.status, run.cancel_requested = "cancelled", True
        elif reason == "revision":
            db.get(PullRequest, "pr").head_sha = "e" * 40
        else:
            db.get(Repository, 22).enabled = False
    pipeline.finalize(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status in {"cancelled", "superseded"}
        assert not db.scalar(select(Job.id).where(Job.kind == "publish"))


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("diagram", ["off", "valid", "invalid"])
def test_summary_uses_one_call_for_optional_diagram(seed, github_stub, monkeypatch, native, diagram):
    identity = new_run(assessed=True, overview=True)
    stub_summary_context(monkeypatch)
    calls = []
    graph = copy.deepcopy(DIAGRAM)
    if diagram == "invalid":
        graph["nodes"][0]["paths"] = ["excluded-secret.env"]

    def infer(*args, **kwargs):
        calls.append((args, kwargs))
        return {**RESULT, "diagram": graph}, {"tokens": 27}

    monkeypatch.setattr(verification, "api_verify", infer)
    monkeypatch.setattr(runner_client, "review", infer)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        connection = db.get(Connection, "connection")
        if native:
            connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        run.status, run.progress = "running", {"execution": "claim", "analysis_status": "completed"}
        run.config = {**run.config, "connection": reviews.connection_snapshot(connection), "settings": {
            **run.config["settings"], "summary_diagram_enabled": diagram != "off",
        }}
        snapshot = run.config["connection"]
    for _ in range(2):
        pr_summary.generate(identity, None, {}, snapshot, {}, "claim")
    assert len(calls) == 1
    schema = calls[0][0][2]["output_schema"] if native else calls[0][1]["schema"]
    assert ("diagram" in schema["properties"]) == (diagram != "off")
    with transaction() as db:
        saved = db.get(ReviewRun, identity).summary
        assert saved["status"] == "completed" and saved["tokens"] == 27
        assert ("diagram" in saved["result"]) == (diagram == "valid")
        assert bool(saved.get("diagram_warning")) == (diagram == "invalid")


def test_file_limit_prevents_verification_but_still_generates_summary(verification_scenario, monkeypatch):  # noqa: F811
    identity = verification_scenario["run"]
    enable_parallel(identity)
    stub_summary_context(monkeypatch)
    limited = ocr.file_limit_result(["src/retry.py", "src/test.py"], 1)

    def engine(*args, **kwargs):
        result = kwargs.get("saved_review")
        if result is None:
            kwargs["before_analysis"](limited)
            result = limited
        kwargs["after_review"](result, None, {}, {})
        return result

    def summary_only(*args, **kwargs):
        assert "instructions" in kwargs
        return copy.deepcopy(RESULT), {"tokens": 15}

    monkeypatch.setattr(ocr, "review", engine)
    monkeypatch.setattr(verification, "api_verify", summary_only)
    reviews.perform_review(identity)
    with transaction() as db:
        assert not db.scalar(select(Job.id).where(Job.kind == "verify_fixes"))
        assert db.get(ReviewRun, identity).stages["verification"]["status"] == "skipped"
        summary_job = db.scalar(select(Job).where(Job.kind == "summarize_review"))
        job_id = summary_job.id
    jobs.execute_job(job_id)
    pipeline.finalize(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status == "skipped" and run.summary["status"] == "completed"
        assert run.summary["tokens"] == 15


def test_duplicate_stage_job_and_lost_completion_preserve_saved_summary(seed, monkeypatch):
    identity = new_run(assessed=True, overview=True, parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        first = reviews.enqueue(db, "summarize_review", {"run_id": identity})
        first.status, first.attempts, first.lease_until = "running", 1, now() + timedelta(hours=1)
        duplicate = reviews.enqueue(db, "summarize_review", {"run_id": identity})
        db.flush()
        first_id, duplicate_id = first.id, duplicate.id
        pipeline.update(run, "summary", "running", execution=f"{first.id}:1", tokens=None)
        run.summary = {"status": "running", "tokens": None}
    jobs.execute_job(duplicate_id)
    with transaction() as db:
        assert db.get(Job, duplicate_id).status == "done"
        run = db.get(ReviewRun, identity)
        assert run.summary["status"] == "running"
        assert run.stages["summary"]["execution"] == f"{first_id}:1"
        db.get(Job, first_id).lease_until = now() - timedelta(seconds=1)
        run.summary = {"status": "completed", "result": RESULT, "tokens": 45}
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: None)
    jobs.dispatch()
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.summary["status"] == run.stages["summary"]["status"] == "completed"
        assert run.stages["summary"]["tokens"] == 45


@pytest.mark.parametrize("historical", [False, True])
def test_verification_stage_preserves_skip_reason_for_resolved_finding(verification_scenario, client, historical):  # noqa: F811
    identity = verification_scenario["run"]
    enable_parallel(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        run.progress = {"file_limit_checked": True}
        verification.prepare(db, run, db.get(Repository, 22), "token")
        pipeline.start(db, run, "main")
        job_id = db.scalar(select(Job.id).where(Job.kind == "verify_fixes"))
        db.get(Finding, verification_scenario["finding"]).status = "resolved"

    jobs.execute_job(job_id)
    with transaction() as db:
        row = db.scalar(select(FindingVerification).where(FindingVerification.run_id == identity))
        stage = db.get(ReviewRun, identity).stages["verification"]
        assert row.status == stage["status"] == "skipped"
        assert "pull request or finding changed" in row.error
        assert stage["error"] == row.error
        assert stage["tokens"] == 0
        reason = row.error
        if historical:
            run = db.get(ReviewRun, identity)
            run.stages = {**run.stages, "verification": {**stage, "error": None}}
    response = client.get(f"/api/v1/runs/{identity}")
    assert response.status_code == 200
    assert response.json()["run"]["stages"]["verification"]["error"] == reason
    with transaction() as db:
        assert db.get(ReviewRun, identity).stages["verification"]["error"] == (None if historical else reason)
    assert "verify" not in verification_scenario["calls"]


def test_verification_keeps_unknown_usage_separate_from_its_reservation(verification_scenario, monkeypatch):  # noqa: F811
    identity = verification_scenario["run"]
    enable_parallel(identity)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.started_at = "running", now()
        run.progress = {"file_limit_checked": True}
        verification.prepare(db, run, db.get(Repository, 22), "token")
        pipeline.start(db, run, "main")
        job_id = db.scalar(select(Job.id).where(Job.kind == "verify_fixes"))

    def unknown(*args, **kwargs):
        raise ValueError("Usage unavailable")

    monkeypatch.setattr(verification, "api_verify", unknown)
    jobs.execute_job(job_id)
    with transaction() as db:
        row = db.scalar(select(FindingVerification).where(FindingVerification.run_id == identity))
        assert row.usage == {"tokens": None, "reserved_tokens": 4321}
        run = db.get(ReviewRun, identity)
        assert run.stages["verification"]["tokens"] is None
        assert run.stages["verification"]["error"] == row.error
        assert run.stages["ocr"]["status"] == "running" and run.summary["status"] == "pending"
