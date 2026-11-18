import copy

import pytest
from sqlalchemy import select
from test_analysis_foundation import claim, planned
from test_review_flow import new_run

from code_mortis import jobs
from code_mortis.core import analysis, engine_retirement
from code_mortis.core.db import transaction
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    InferenceAdmission,
    Job,
    ReviewRun,
    SecurityScan,
)
from code_mortis.schemas.common import RepoSettings


def old_config(config):
    return {**config, "analysis_engine": "ocr", "settings": {**config.get("settings", {}), "analysis_engine": "ocr"}}


def test_new_work_defaults_to_mortis_but_saved_work_never_changes_identity():
    assert RepoSettings().analysis_engine == "mortis"
    assert analysis.new_engine_snapshot({})["analysis_engine"] == "mortis"
    for saved in ({}, {"analysis_engine": "ocr"}):
        before = copy.deepcopy(saved)
        with pytest.raises(ValueError, match="retired"):
            analysis.require_execution_engine(saved)
        assert saved == before
    with pytest.raises(ValueError, match="retired"):
        analysis.new_engine_snapshot({"analysis_engine": "ocr"})
    assert analysis.output_token_limit("ocr-completion/v1") == analysis.output_token_limit(analysis.OUTPUT_TOKEN_POLICY)
    assert analysis.output_token_limit(None) == 8192
    assert not engine_retirement.is_retired({"analysis_engine": "future"})
    with pytest.raises(ValueError, match="unsupported"):
        analysis.require_execution_engine({"analysis_engine": "future"})


@pytest.mark.parametrize("kind", sorted(engine_retirement.ENGINE_JOBS))
def test_old_ocr_delivery_stops_before_authorization_or_execution(seed, monkeypatch, kind):
    identity = new_run(parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config, run.engine = old_config(run.config), "ocr"
        run.report = {"tokens": 31, "saved": True}
        config = copy.deepcopy(run.config)
        scan = SecurityScan(id="retired-scan", repository_id=22, actor_id=7, connection_id="connection",
                            request_key="retired-scan", branch="main", head_sha="a" * 40,
                            config=config, report={"saved": True})
        db.add(scan)
        job = Job(id="retired-delivery", kind=kind, payload={**config, "run_id": identity,
                  "scan_id": scan.id, "config": config, "connection_id": "connection"})
        db.add(job)
        run.progress = {**run.progress, "summary_retry": job.id}
    monkeypatch.setattr(jobs, "execute", lambda *a, **k: pytest.fail("Retired inference executed"))
    from code_mortis.api_tokens import provenance

    monkeypatch.setattr(provenance, "job_revoked", lambda *a, **k: pytest.fail("Network authorization reached"))
    jobs.execute_job.run("retired-delivery")
    jobs.execute_job.run("retired-delivery")
    with transaction() as db:
        job = db.get(Job, "retired-delivery")
        assert job.status == "failed" and job.attempts == 0
        assert "This saved review engine is retired" in job.error
        assert job.payload["engine_retirement"]["prior_status"] == "queued"
        run = db.get(ReviewRun, identity)
        assert run.config == config and run.engine == "ocr" and run.report == {"tokens": 31, "saved": True}


def test_cutover_is_idempotent_and_preserves_history_and_new_mortis_work(seed):
    identity = new_run(parallel=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config, run.engine = old_config(run.config), "ocr"
        run.status, run.publication, run.review_ids = "running", "partially_published", [123]
        run.report = {"findings": [{"saved": True}], "tokens": 31}
        run.stages = {"ocr": {"status": "completed", "tokens": 31}, "assessment": {"status": "running"}}
        original = copy.deepcopy((run.config, run.report, run.stages))
        db.add(SecurityScan(id="retired-scan", repository_id=22, actor_id=7, connection_id="connection",
                            request_key="retired-scan", branch="main", head_sha="a" * 40,
                            config=run.config, status="running", report={"saved": True},
                            progress={"tokens": 14, "tasks": {"complete": {"status": "completed", "tokens": 14},
                                                               "active": {"status": "running"}}}))
        db.add(Job(id="new-mortis", kind="generate_background", payload={"analysis_engine": "mortis"}))
        db.add(Job(id="saved-draft", kind="generate_background", status="running",
                   payload={"analysis_engine": "ocr", "background": "Saved draft", "usage": {"tokens": 19}}))
    with transaction() as db:
        counts = engine_retirement.retire_ocr(db)
        assert counts["reviews"] == counts["scans"] == 1 and counts["jobs"] >= 2
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.status == "cancelled" and run.engine == "ocr"
        assert (run.config, run.report) == original[:2]
        assert run.progress["engine_retirement"]["prior_stages"] == original[2]
        assert run.publication == "partially_published" and run.review_ids == [123]
        scan = db.get(SecurityScan, "retired-scan")
        assert scan.status == "cancelled" and scan.progress["tokens"] is None
        assert scan.progress["tasks"]["complete"] == {"status": "completed", "tokens": 14}
        assert scan.progress["engine_retirement"]["prior_report"] == {"saved": True}
        assert scan.progress["engine_retirement"]["known_tokens_before_stop"] == 14
        assert db.get(Job, "new-mortis").status == "queued"
        draft = db.get(Job, "saved-draft")
        assert draft.status == "done" and draft.payload["background"] == "Saved draft"
        assert draft.payload["usage"] == {"tokens": 19}
        before = copy.deepcopy((run.config, run.report, run.progress, scan.progress))
        assert engine_retirement.retire_ocr(db) == {"reviews": 0, "scans": 0, "jobs": 0, "admissions": 0}
        assert (run.config, run.report, run.progress, scan.progress) == before


def test_retirement_stops_pending_tasks_without_erasing_outputs_or_consumption(seed):
    plan_id, tasks = planned(seed)
    completed = claim(tasks["a"])
    assert analysis.finish_task(completed, result={"validated": True}, usage_tokens=25)
    assert claim(tasks["b"])
    with transaction() as db:
        snapshot = copy.deepcopy(db.get(AnalysisPlan, plan_id).snapshot)
        job = db.get(Job, "analysis-job")
        job.kind, job.payload = "generate_background", {"analysis_engine": "ocr", "inference_started": True}
        engine_retirement.settle_job(db, job)
    with transaction() as db:
        assert db.get(AnalysisPlan, plan_id).snapshot == snapshot
        assert db.get(AnalysisTask, tasks["a"]).result == {"validated": True}
        assert db.get(AnalysisTask, tasks["a"]).status == "completed"
        assert db.get(AnalysisTask, tasks["b"]).status == "cancelled"
        assert db.get(AnalysisTask, tasks["summary"]).status == "cancelled"
        assert db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens == 65
        attempt = db.scalar(select(AnalysisAttempt).where(AnalysisAttempt.task_id == tasks["b"]))
        assert attempt.usage_state == "unknown" and attempt.status == "uncertain"
        assert db.get(Job, "analysis-job").payload["usage"]["tokens"] is None
        ticket = db.scalar(select(InferenceAdmission).where(InferenceAdmission.task_id == tasks["b"]))
        assert ticket.status == "active"  # A worker gate cannot prove transport termination.
        assert engine_retirement.retire_ocr(db)["admissions"] == 1
        assert ticket.status == "released"
        assert db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens == 65
        assert attempt.usage_state == "unknown"


def test_delayed_retirement_reload_preserves_new_summary_progress(seed):
    identity = new_run()
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config, run.engine, run.status = old_config(run.config), "ocr", "completed"
        db.add(Job(id="late-ocr", kind="review", payload={"run_id": identity}))
    with transaction() as stale:
        run = stale.get(ReviewRun, identity)
        job = stale.scalar(select(Job).where(Job.id == "late-ocr").with_for_update())
        with transaction() as current:
            updated = current.get(ReviewRun, identity)
            updated.progress = {**updated.progress, "summary_retry": "new-summary", "summary_attempts": ["new-summary"]}
            current.add(Job(id="new-summary", kind="generate_summary", payload={"run_id": identity,
                        "config": {"analysis_engine": "mortis"}}))
        engine_retirement.settle_job(stale, job)
        assert run.progress["summary_retry"] == "new-summary"
        assert run.progress["summary_attempts"] == ["new-summary"]
        assert run.status == "completed" and not run.cancel_requested
        assert not engine_retirement.retired_job(stale, stale.get(Job, "new-summary"))


@pytest.mark.parametrize("action", ["retry", "candidates/old-candidate/retry"])
def test_old_ocr_api_retry_rejects_without_mutating_saved_work(seed, client, github_stub, action):
    identity = new_run(assessed=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config, run.engine, run.status = old_config(run.config), "ocr", "blocked"
        run.report = {"saved": True}
        before = copy.deepcopy((run.config, run.engine, run.status, run.report, run.progress))
        job_ids = set(db.scalars(select(Job.id)))
    response = client.post(f"/api/v1/runs/{identity}/{action}")
    assert response.status_code == 410 and "This saved review engine is retired" in response.text
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert (run.config, run.engine, run.status, run.report, run.progress) == before
        assert set(db.scalars(select(Job.id))) == job_ids
