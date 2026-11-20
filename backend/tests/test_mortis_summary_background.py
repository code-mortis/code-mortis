"""Offline retained summaries/background through the durable shared gateway."""

import copy
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_llm_http import result as wire_result
from test_mortis_review import CREDENTIALS
from test_mortis_review import scenario as scenario

from code_mortis import jobs
from code_mortis.core.analysis import fingerprint
from code_mortis.core.db import now, transaction
from code_mortis.integrations import github, llm
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    Job,
    Repository,
    ReviewRun,
    SecurityFinding,
    SecurityScan,
    User,
)
from code_mortis.reviews import background
from code_mortis.reviews import summary as pr_summary
from code_mortis.schemas.common import RunLimits
from code_mortis.security_scans.engine import secure_digest

RESULT = {
    "narrative": "The application limit now returns zero.",
    "groups": [{"title": "Application", "description": "Change the limit.", "paths": ["app.py"]}],
    "diagram": {"nodes": [{"id": "app", "label": "Application", "paths": ["app.py"]}], "edges": []},
}


@pytest.fixture
def summary_case(scenario, monkeypatch):
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.config = {**run.config, "settings": {**run.config["settings"], "token_budget": 1000,
            "summary_token_budget": 60_000, "summary_diagram_enabled": True, "ai_fix_prompts_enabled": True}}
        run.progress = {**run.progress, "analysis_status": "skipped", "analysis_saved": True}
        run.summary = {"status": "pending", "tokens": 0}
        run.report = {"manifest": {"coverage": {"selected": [{"path": "app.py"}, {"path": "guide.md"}]}}}
    wire = []
    state = {"wire": wire, "results": [RESULT], "after_response": None, "usage": True, **scenario}
    state["wire"] = wire

    def handler(request):
        body = json.loads(request.content)
        wire.append(body)
        output = state["results"][min(len(wire) - 1, len(state["results"]) - 1)]
        if state["after_response"]:
            state["after_response"]()
        return httpx.Response(200, json=wire_result("openai", content=json.dumps(output), usage=state["usage"]))

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    return state


def generate_summary(case):
    pr_summary.generate("mortis-run", case["root"], {}, case["connection"], copy.deepcopy(CREDENTIALS), "mortis-job:1")


def test_summary_keeps_groups_diagram_and_independent_budget_without_ocr(summary_case):
    generate_summary(summary_case)
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.summary["status"] == "completed", run.summary
        assert run.summary["result"] == RESULT and run.summary["tokens"] == 12
        assert run.summary["settings"] == {"summary_diagram_enabled": True, "ai_fix_prompts_enabled": True}
        plan = db.scalar(select(AnalysisPlan))
        assert plan.snapshot["revisions"]["head"] == summary_case["head"]
        budget = db.get(AnalysisBudget, (plan.id, "summary"))
        assert budget.token_limit == 60_000 and budget.charged_tokens == 12
        assert db.scalar(select(AnalysisTask)).status == "completed"
        assert db.scalar(select(AnalysisAttempt)).usage_tokens == 12
    generate_summary(summary_case)
    assert len(summary_case["wire"]) == 1


def test_parallel_summary_uses_neutral_scope_after_analysis_file_limit(summary_case):
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.config = {**run.config, "pipeline_version": 2,
                      "settings": {**run.config["settings"], "max_files": 1}}
        run.progress = {**run.progress, "file_limit_exceeded": True}
        run.report = {"manifest": {"coverage": {"selected": [], "waived": []}}}
        run.stages = {"summary": {"status": "running", "execution": "summary-job:1"}}
        db.add(Job(id="summary-job", kind="summarize_review", payload={"run_id": run.id}, status="running",
                   attempts=1, lease_until=now() + timedelta(minutes=10)))
    pr_summary.generate("mortis-run", summary_case["root"], {}, summary_case["connection"],
                       copy.deepcopy(CREDENTIALS), "summary-job:1", stage="summary")
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.summary["status"] == "completed", run.summary
        plan = db.scalar(select(AnalysisPlan))
        assert plan.job_id == "summary-job" and plan.snapshot["settings"]["max_files"] == 1
        assert db.get(AnalysisBudget, (plan.id, "summary")).charged_tokens == 12
    assert len(summary_case["wire"]) == 1


def test_summary_retry_uses_its_current_mortis_snapshot_on_ocr_history(summary_case):
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        retry_config = copy.deepcopy(run.config)
        run.status, run.engine = "completed", "ocr"
        run.config = {**run.config, "analysis_engine": "ocr",
                      "settings": {**run.config["settings"], "analysis_engine": "ocr"}}
        run.progress = {**run.progress, "summary_retry": "retry-job"}
        payload = {"run_id": run.id, "actor_id": 7, "connection_id": "connection",
                   "config": retry_config, "summary_attempt_version": 1}
        db.add(Job(id="retry-job", kind="generate_summary", payload=payload, status="running", attempts=1,
                   lease_until=now() + timedelta(minutes=10)))
    pr_summary.perform_retry({**payload, "_job_id": "retry-job", "_claim": "retry-job:1"})
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.status == "completed" and run.engine == "ocr"
        assert run.summary["status"] == "completed", run.summary
        attempt = db.get(Job, "retry-job")
        assert attempt.payload["summary_attempt_result"] == run.summary
        plan = db.scalar(select(AnalysisPlan))
        assert plan.job_id == "retry-job" and plan.snapshot["settings"]["analysis_engine"] == "mortis"
        assert db.get(AnalysisBudget, (plan.id, "summary")).charged_tokens == 12
    assert len(summary_case["wire"]) == 1


def test_summary_invalid_diagram_is_omitted_without_losing_groups(summary_case):
    summary_case["results"] = [{**RESULT, "diagram": {
        "nodes": [{"id": "app", "label": "Application", "paths": ["outside.py"]}], "edges": [],
    }}]
    generate_summary(summary_case)
    with transaction() as db:
        saved = db.get(ReviewRun, "mortis-run").summary
        assert saved["status"] == "completed", saved
        assert saved["result"]["groups"] == RESULT["groups"] and "diagram" not in saved["result"]
        assert "omitted" in saved["diagram_warning"]
    assert len(summary_case["wire"]) == 1


def test_summary_correction_charges_both_requests_to_same_stage(summary_case):
    summary_case["results"] = [{**RESULT, "groups": [{**RESULT["groups"][0], "paths": ["outside.py"]}]}, RESULT]
    generate_summary(summary_case)
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.summary["status"] == "completed", run.summary
        assert run.summary["tokens"] == 24
        plan = db.scalar(select(AnalysisPlan))
        assert db.get(AnalysisBudget, (plan.id, "summary")).charged_tokens == 24
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 2
    assert len(summary_case["wire"]) == 2


def test_summary_cancellation_drops_result_and_keeps_usage(summary_case):
    def cancel():
        with transaction() as db:
            db.get(ReviewRun, "mortis-run").cancel_requested = True
    summary_case["after_response"] = cancel
    generate_summary(summary_case)
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.summary["status"] == "failed" and run.summary.get("result") is None
        assert "cancel" in run.summary["error"].lower()
        assert db.scalar(select(AnalysisAttempt)).usage_tokens == 12
    assert len(summary_case["wire"]) == 1


def test_background_uses_exact_snapshot_redaction_and_shared_ledger(seed, github_stub, monkeypatch):
    wire = []
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"default_branch": "main"})
    monkeypatch.setattr(background, "context", lambda *a, **kw: {
        "repository": "team/project", "branch": "main", "sha": "a" * 40,
        "paths": ["README.md"], "sources": {"README.md": {"text": "A service; provider-secret installation-token", "truncated": False}},
    })

    def handler(request):
        wire.append(json.loads(request.content))
        return httpx.Response(200, json=wire_result("openai", content='{"background":"A Python service."}'))

    monkeypatch.setattr(llm, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm.engine_http, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {"exclusions": "private/**", "background": "Original"}
        connection = db.get(type(seed["connection"]), "connection")
        connection.options = {"prompt_token_ceiling": 50_000, "timeout_sec": 5}
        job = background.enqueue(db, repo, db.get(User, 7), connection, "",
                                 RunLimits(token_budget=60_000, timeout_seconds=None))
        job_id = job.id
        job.payload = {**job.payload, "analysis_engine": "mortis"}
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=10)
    background.perform(job_id, 1)
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.payload["background"] == "A Python service."
        assert job.payload["usage"] == {"tokens": 12}
        assert job.payload["source_sha"] == "a" * 40
        assert db.get(Repository, 22).settings["background"] == "Original"
        plan = db.scalar(select(AnalysisPlan))
        assert plan.run_id is None and plan.snapshot["revisions"] == {"head": "a" * 40}
        assert db.get(AnalysisBudget, (plan.id, "summary")).charged_tokens == 12
    assert "provider-secret" not in json.dumps(wire) and "installation-token" not in json.dumps(wire)
    background.perform(job_id, 1)
    assert len(wire) == 1


def test_background_interrupted_attempt_never_repeats_inference(seed, github_stub):
    with transaction() as db:
        job = background.enqueue(db, seed["repo"], seed["user"], seed["connection"], "")
        job_id = job.id
        job.payload = {**job.payload, "analysis_engine": "mortis", "inference_started": True}
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=10)
    with pytest.raises(ValueError, match="usage may have been consumed"):
        background.perform(job_id, 1)


@pytest.mark.parametrize("concurrent", [None, {"background": "New context"}, {"project_summary": "Previously saved"}])
def test_repository_summary_uses_saved_context_with_durable_usage(seed, github_stub, client, monkeypatch, concurrent):
    historical_secret = "opaque historical credential 3cdb6"
    saved = f"A tool for teams to review software. Private value: {historical_secret}"
    rule = "fixture-rule"
    paragraph = "This tool helps teams review changes to their software."
    wire = []
    monkeypatch.setattr(background, "context", lambda *a, **kw: pytest.fail("Summary-only must not fetch source"))
    monkeypatch.setattr(github, "installation_token", lambda *a, **kw: pytest.fail("Summary-only needs no installation token"))
    monkeypatch.setattr(llm, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm.engine_http, "safe_endpoint", lambda url: url)

    def handler(request):
        wire.append(json.loads(request.content))
        if concurrent:
            with transaction() as db:
                repo = db.get(Repository, 22)
                repo.settings = {**repo.settings, **concurrent}
        content = json.dumps({"project_summary": f"{paragraph} {historical_secret}"})
        return httpx.Response(200, json=wire_result("openai", content=content))

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {"background": saved, "token_budget": 60000}
        db.add(SecurityScan(id="old-secret-scan", repository_id=22, actor_id=7, request_key="old-secret-scan",
                            branch="main", head_sha="a" * 40, config={}, status="completed"))
        db.flush()
        db.add(SecurityFinding(
            repository_id=22, fingerprint="a" * 64, kind="secret", severity="high", title="Historical secret",
            path="old-config", first_scan_id="old-secret-scan", last_scan_id="old-secret-scan",
            evidence={"identity": secure_digest(f"{rule}\0{historical_secret}"),
                      "match_bytes": len(historical_secret.encode()), "rule_id": rule},
        ))

    response = client.post("/api/v1/repositories/22/background", json={"mode": "summary"})
    assert response.status_code == 202, response.text
    identity = response.json()["id"]
    jobs.execute_job(identity)
    result = client.get(f"/api/v1/repositories/22/background/{identity}").json()
    assert len(wire) == 1 and historical_secret not in json.dumps(wire)
    assert "A tool for teams to review software" in json.dumps(wire)
    with transaction() as db:
        repo = db.get(Repository, 22)
        plan = db.scalar(select(AnalysisPlan))
        assert plan.snapshot["revisions"] == {"context": fingerprint(saved)}
        assert db.get(AnalysisBudget, (plan.id, "summary")).charged_tokens == 12
        assert db.get(Job, identity).payload["usage"] == {"tokens": 12}
        assert result["background"] is None
        if concurrent:
            assert result["status"] == "failed" and result["project_summary"] is None
            assert repo.settings["background"] == concurrent.get("background", saved)
            assert repo.settings.get("project_summary") == concurrent.get("project_summary")
        else:
            assert result["status"] == "done"
            assert repo.settings["background"] == saved
            assert repo.settings["project_summary"] == result["project_summary"] == f"{paragraph} [redacted]"


@pytest.mark.parametrize("saved_context", [None, "Saved evidence from repository settings."])
@pytest.mark.parametrize("mode", ["summary_draft", "summary_refresh"])
def test_regenerate_publishes_only_for_explicit_refresh(seed, github_stub, client, monkeypatch, saved_context, mode):
    settings = {"project_summary": "Published summary.", "token_budget": 60_000}
    if saved_context:
        settings["background"] = saved_context
    with transaction() as db:
        db.get(Repository, 22).settings = settings

    def source_evidence(*args, **kwargs):
        assert not saved_context
        return {"sha": "a" * 40, "sources": {"README.md": {"text": "Readme fallback evidence.", "truncated": False}}}

    calls = []
    paragraph = "The replacement summary."

    def handler(request):
        calls.append(request.content.decode())
        return httpx.Response(200, json=wire_result("openai", content=json.dumps({"project_summary": paragraph})))

    monkeypatch.setattr(background, "context", source_evidence)
    monkeypatch.setattr(llm, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm.engine_http, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    endpoint = "/api/v1/repositories/22/background"
    started = client.post(endpoint, json={"mode": mode, "generation_id": str(uuid4())})
    assert started.status_code == 202, started.text
    identity = started.json()["id"]
    with transaction() as db:
        assert db.get(Repository, 22).settings == settings
    jobs.execute_job(identity)
    result = client.get(f"{endpoint}/{identity}").json()
    assert result["status"] == "done", result
    assert result["project_summary"] == paragraph
    assert result["background"] is None
    assert len(calls) == 1
    assert (saved_context or "Readme fallback evidence.") in calls[0]
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == identity))
        assert plan.snapshot["revisions"] == ({"context": fingerprint(saved_context)} if saved_context else {"head": "a" * 40})
        repo = db.get(Repository, 22)
        if mode == "summary_refresh":
            assert repo.settings == {**settings, "project_summary": paragraph}
            return
        assert repo.settings == settings
        repo.settings = {**repo.settings, "project_summary": "Newer edit."}

    body = {"enabled": False, "expected_background": saved_context or "",
            "expected_project_summary": "Published summary.", "settings": {"project_summary": paragraph}}
    endpoint = "/api/v1/repositories/22"
    assert client.put(endpoint, json=body).status_code == 409
    body["expected_project_summary"] = "Newer edit."
    saved = client.put(endpoint, json=body)
    assert saved.status_code == 200
    assert saved.json()["settings"]["project_summary"] == paragraph
    assert saved.json()["settings"]["background"] == (saved_context or "")


@pytest.mark.parametrize("mode", ["draft", "summary_refresh"])
def test_background_cancellation_fences_a_running_provider_call(seed, github_stub, client, monkeypatch, mode):
    request = {"generation_id": str(uuid4())}
    endpoint = "/api/v1/repositories/22/background"
    monkeypatch.setattr(background, "context", lambda *args, **kwargs: {
        "sha": "a" * 40, "sources": {"README.md": {"text": "A review service", "truncated": False}},
    })
    monkeypatch.setattr(llm, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm.engine_http, "safe_endpoint", lambda url: url)
    with transaction() as db:
        db.get(Repository, 22).settings = {
            "background": "Existing context.", "project_summary": "Existing summary.", "token_budget": 60_000,
        }
    started = client.post(endpoint, json={**request, "mode": mode, "connection_id": "connection"})
    assert started.status_code == 202, started.text
    identity = started.json()["id"]
    calls = []

    def handler(_):
        calls.append(client.post(f"{endpoint}/cancel", json=request).json())
        output = {"project_summary": "Late summary."}
        if mode == "draft":
            output["background"] = "Late context."
        return httpx.Response(200, json=wire_result("openai", content=json.dumps(output)))

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    jobs.execute_job(identity)
    result = client.get(f"{endpoint}/{identity}").json()
    assert calls == [result]
    assert result["status"] == "cancelled"
    assert result["background"] is None and result["project_summary"] is None
    with transaction() as db:
        repo = db.get(Repository, 22)
        assert repo.settings["background"] == "Existing context."
        assert repo.settings["project_summary"] == "Existing summary."
        plan = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == identity))
        attempt = db.scalar(select(AnalysisAttempt))
        assert attempt.usage_state == "unknown"
        assert db.scalar(select(AnalysisTask)).status == "cancelled"
        assert db.get(AnalysisBudget, (plan.id, "summary")).charged_tokens == attempt.reserved_tokens > 0
