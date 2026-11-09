"""Production Mortis workflow and retained readers over local Git and synthetic network I/O."""

import copy
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from test_llm_http import result as wire_result
from test_mcp_auth import CALLBACK, credentials, rpc
from test_mortis_review import scenario as source_case  # noqa: F401
from test_mortis_summary_background import RESULT as SUMMARY

from code_mortis.core import source
from code_mortis.core.db import now, transaction
from code_mortis.core.security import digest
from code_mortis.integrations import comparison as github_comparison
from code_mortis.integrations import github, llm
from code_mortis.mcp.models import McpClient
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    AppConfig,
    Finding,
    FindingVerification,
    Job,
    LoginSession,
    Occurrence,
    Repository,
    ReviewCandidate,
    ReviewRun,
)
from code_mortis.reviews import analytics, assessment, pipeline, service
from code_mortis.reviews import context as review_context


@pytest.fixture
def workflow(source_case, monkeypatch):  # noqa: F811
    state = {**source_case, "requests": [], "reviews": [], "findings_enabled": True}
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        options = {**run.config["settings"], "assessment_token_budget": 200_000,
                   "verification_token_budget": 200_000, "summary_token_budget": 60_000,
                   "summary_timeout_seconds": 180, "summary_diagram_enabled": True,
                   "ai_fix_prompts_enabled": True}
        run.config = {**run.config, "assessment_policy": assessment.POLICY_VERSION,
                      "task_contract": review_context.TASK_CONTRACT, "settings": options}
        run.connection_id = "connection"
        db.get(LoginSession, digest("session")).authenticated_at = now()
        run.summary = {"status": "pending", "tokens": 0}
        db.get(Repository, 22).settings = options
        state["config"] = copy.deepcopy(run.config)

    files = []
    for path in ("app.py", "guide.md", ".env"):
        patch = source.git(state["root"], {}, "diff", "--no-ext-diff", "--no-textconv", "--unified=3",
                           state["base"], state["head"], "--", ":(literal)" + path)
        rows = patch.splitlines()
        start = next(index for index, row in enumerate(rows) if row.startswith("@@"))
        rows = rows[start:]
        files.append({"filename": path, "status": "added" if path == ".env" else "modified",
                      "additions": sum(row.startswith("+") for row in rows),
                      "deletions": sum(row.startswith("-") for row in rows), "patch": "\n".join(rows)})

    def github_request(method, path, token, **kwargs):
        if method == "GET" and "/compare/" in path:
            return {"base_commit": {"sha": state["base"]},
                    "merge_base_commit": {"sha": state["base"]}, "files": copy.deepcopy(files)}
        if method == "GET" and path.startswith("/repos/team/project/pulls/3/reviews"):
            return copy.deepcopy(state["reviews"])
        if method == "POST" and path == "/repos/team/project/pulls/3/reviews":
            # The real publication code saves/reconciles these synthetic GitHub results.
            review = {"id": len(state["reviews"]) + 1, "user": {"login": "mortis-test[bot]"},
                      **copy.deepcopy(kwargs["json"])}
            state["reviews"].append(review)
            return review
        assert (method, path) == ("GET", "/repos/team/project/pulls/3"), (method, path)
        return {"number": 3, "state": "open", "head": {"sha": state["head"], "ref": "fix/limit"},
                "base": {"sha": state["base"], "repo": {"id": 22}}}

    def handler(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]["properties"]
        if "completed_units" in schema:
            state["requests"].append("analysis")
            source_case["finding"] = state["findings_enabled"]
            # Reuse M3's real planner/executor wire fixture for discovery results.
            valid = source_case["handler"](request)
            if state.get("truncate_analysis") and state["requests"].count("analysis") == 1:
                return httpx.Response(200, json=wire_result("openai", stop="length"))
            return valid
        if "relationship" in schema:
            state["requests"].append("assessment")
            content = next(message["content"] for message in body["messages"] if message["role"] == "user")
            supplied, _ = json.JSONDecoder().raw_decode(content.split("\n\n", 1)[1])
            history = supplied["history"]
            value = {
                "verdict": "confirmed", "relationship": "duplicate" if history else "new",
                "finding_id": history[0]["id"] if history else None,
                "explanation": "The supplied limit contract previously permitted one item; returning zero prevents work.",
                "cause": "The allocation limit is zero.", "trigger": "A caller allocates work.",
                "consequence": "The caller cannot allocate an item.",
                "history_explanation": "The same current cause is already recorded." if history else "",
                "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": True,
                "required_paths": [], "evidence": [{
                    "source_id": review_context.source_id("current", "app.py"), "start_line": 2, "end_line": 2,
                }],
            }
        else:
            assert "narrative" in schema, schema
            state["requests"].append("summary")
            value = SUMMARY
        return httpx.Response(200, json=wire_result("openai", content=json.dumps(value)))

    authorize_repository = github.authorize_repo
    monkeypatch.setattr(github, "authorize_repo", lambda db, user, name, write=False, **kwargs:
        authorize_repository(db, user, name, write=write, **kwargs))
    monkeypatch.setattr(github, "request", github_request)
    monkeypatch.setattr(github, "threads", lambda *a, **kw: [])
    monkeypatch.setattr(github, "repository_read_token", lambda *a, **kw: "synthetic-github-token")
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository))))
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False))
    github_comparison.cached.cache_clear()
    yield state
    github_comparison.cached.cache_clear()


def finish_workflow(state, run_id, job_id, *, parallel):
    claim = job_id + ":1"
    service.perform_review(run_id, claim)
    with transaction() as db:
        db.get(Job, job_id).status = "completed"
        if parallel:
            summary_job = db.scalar(select(Job).where(Job.kind == "summarize_review",
                Job.payload["run_id"].as_string() == run_id))
            assert summary_job is not None
            summary_job.status, summary_job.attempts = "running", 1
            summary_job.lease_until = now() + timedelta(minutes=10)
            payload = {**summary_job.payload, "_job_id": summary_job.id, "_claim": summary_job.id + ":1"}
    if parallel:
        pipeline.perform(payload)
        with transaction() as db:
            db.get(Job, payload["_job_id"]).status = "completed"
        pipeline.finalize(run_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.status == "completed", (run.status, run.error, run.stages)
        assert run.summary["status"] == "completed", run.summary
    before = len(state["requests"])
    service.publish_run(run_id)
    service.publish_run(run_id)
    assert len(state["requests"]) == before, "Publication retries must not repeat inference"


def clone_run(state, identity, *, parallel):
    with transaction() as db:
        run = ReviewRun(id=identity, pr_id="pr", actor_id=7, trigger="manual", engine="mortis",
            connection_id="connection", dedup_key=identity, base_sha=state["base"], head_sha=state["head"],
            status="running", started_at=now(), config=copy.deepcopy(state["config"]),
            progress={"execution": identity + "-job:1"}, summary={"status": "pending", "tokens": 0},
            stages={stage: {"status": "pending", "tokens": 0} for stage in pipeline.STAGES} if parallel else {})
        db.add(run)
        db.add(Job(id=identity + "-job", kind="review", payload={"run_id": identity}, status="running",
                   attempts=1, lease_until=now() + timedelta(minutes=10)))
    return identity + "-job"


@pytest.mark.parametrize("saved_result", [False, True])
def test_interrupted_mortis_summary_reaches_terminal_stage(workflow, saved_result):
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.config = {**run.config, "pipeline_version": 2}
        run.stages = {"summary": {"status": "running", "execution": "summary-job:1"}}
        run.summary = {"status": "completed" if saved_result else "running", "tokens": 12 if saved_result else None,
                       **({"result": SUMMARY} if saved_result else {})}
        db.add(Job(id="summary-job", kind="summarize_review", payload={"run_id": run.id},
                   status="running", attempts=2, lease_until=now() + timedelta(minutes=10)))
    pipeline.perform({"run_id": "mortis-run", "_job_id": "summary-job", "_claim": "summary-job:2"})
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.stages["summary"]["status"] == ("completed" if saved_result else "failed")
        assert run.summary["status"] == ("completed" if saved_result else "failed")
        assert run.summary["tokens"] == (12 if saved_result else None)
        assert db.scalar(select(Job.id).where(Job.kind == "finalize_review"))
    assert workflow["requests"] == []


@pytest.mark.parametrize("parallel", [False, True])
def test_recovered_analysis_completes_assessment_summary_and_publication(workflow, parallel):
    workflow["truncate_analysis"] = True
    if parallel:
        workflow["config"]["pipeline_version"] = 2
        with transaction() as db:
            run = db.get(ReviewRun, "mortis-run")
            run.config = copy.deepcopy(workflow["config"])
            run.stages = {stage: {"status": "pending", "tokens": 0} for stage in pipeline.STAGES}
    finish_workflow(workflow, "mortis-run", "mortis-job", parallel=parallel)
    assert workflow["requests"].count("analysis") == 3
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.report["summary"]["total_tokens"] == 36 and analytics.run_tokens(db, run) == 60
        assert len(run.report["manifest"]["coverage"]["completed"]) == 2
        assert run.publication == "published" and db.scalar(select(ReviewCandidate)).result["verdict"] == "confirmed"
        tasks = list(db.scalars(select(AnalysisTask).where(AnalysisTask.stage == "analysis")))
        assert len(tasks) == 3 and sum(task.status == "failed" for task in tasks) == 1
        assert all(task.status == "completed" for task in tasks if ":unit:" in task.key)


@pytest.mark.parametrize("parallel", [False, True])
def test_full_mortis_workflow_retains_history_reports_and_authorized_readers(workflow, client, parallel):
    state = workflow
    if parallel:
        state["config"]["pipeline_version"] = 2
        with transaction() as db:
            run = db.get(ReviewRun, "mortis-run")
            run.config = copy.deepcopy(state["config"])
            run.stages = {stage: {"status": "pending", "tokens": 0} for stage in pipeline.STAGES}
    finish_workflow(state, "mortis-run", "mortis-job", parallel=parallel)
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        candidate = db.scalar(select(ReviewCandidate))
        finding = db.scalar(select(Finding))
        assert candidate.status == "completed" and candidate.finding_id == finding.id and candidate.is_new
        assert candidate.result["verdict"] == "confirmed" and finding.status == "open"
        assert finding.fingerprint == digest("candidate:" + candidate.id)
        assert candidate.result["evidence"][0]["quote"] == "    return 0\n"
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 1
        assert run.publication == "published" and len(state["reviews"]) == 1
        assert "Prompt to fix with AI" in json.dumps(run.publication_plan)
        assert analytics.run_tokens(db, run) == 36
        plan = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == "mortis-job"))
        assert set(plan.snapshot["budgets"]) == {"analysis", "assessment", "verification", "summary"}
        assert plan.snapshot["settings"]["workflow_contract"] == "mortis-workflow/v1"
        tasks = list(db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id)))
        assert {task.stage for task in tasks} >= {"analysis", "assessment"}
        assert all(task.status == "completed" for task in tasks)
        assert db.get(AnalysisBudget, (plan.id, "analysis")).charged_tokens == 12
        assert db.get(AnalysisBudget, (plan.id, "assessment")).charged_tokens == 12
        assert all(attempt.usage_tokens == 12 for attempt in db.scalars(select(AnalysisAttempt)))
        finding_id = finding.id

    before_reads = len(state["requests"])
    detail = client.get("/api/v1/runs/mortis-run")
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert data["run"]["engine"] == "mortis" and data["run"]["report"]["engine"] == "mortis"
    assert data["total_tokens"] == 36 and data["assessment"]["new_findings"] == 1
    assert data["occurrences"][0]["finding_id"] == finding_id
    saved = client.get("/api/v1/runs/mortis-run/findings")
    assert saved.status_code == 200, saved.text
    assert finding_id in saved.text and "The limit returns zero." in saved.text
    workspace = client.post("/api/v1/pulls/pr/code/workspace", json={"path": "app.py"})
    assert workspace.status_code == 200, workspace.text
    code = workspace.json()
    assert {item["path"] for item in code["files"]} == {"app.py", "guide.md"}
    assert all(item["reviewed"] for item in code["file_states"])
    assert code["summary_run_id"] == "mortis-run" and code["groups"][0]["title"] == "Application"
    assert code["diff"]["findings"][0]["finding_id"] == finding_id
    assert code["diff"]["findings"][0]["placement"] == "inline"

    # The OAuth transport reads the actual saved result under an independent Reviews grant.
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"reviews_enabled": True, "security_enabled": False,
            "management_enabled": False, "allowed_client_domains": []}
        db.add(McpClient(client_id="test-cli", client_name="Test CLI", redirect_uris=[CALLBACK]))
    token = credentials(client)["access_token"]
    for section in ("report", "summary"):
        response = rpc(client, token, method="tools/call", arguments={"name": "reviews_get_run_section",
            "arguments": {"params": {"run_id": "mortis-run", "section": section}}})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result.get("isError") is not True, result
        saved_section = json.loads(result["structuredContent"]["content"])
        if section == "report":
            assert saved_section["engine"] == "mortis"
        else:
            assert saved_section["status"] == "completed"

    created = client.post("/api/v1/api-tokens", json={"name": "Mortis retained history", "scopes": ["reviews:read"],
        "repository_ids": [22], "expires_in_days": 1})
    assert created.status_code == 201, created.text
    client.cookies.clear()
    pat_response = client.get("/api/v1/runs/mortis-run", headers={"Authorization": "Bearer " + created.json()["token"]})
    assert pat_response.status_code == 200, pat_response.text
    assert pat_response.json()["total_tokens"] == 36
    assert len(state["requests"]) == before_reads, "Saved browser/MCP/PAT reads must not start inference"

    state["findings_enabled"] = False
    finish_workflow(state, "absent-run", clone_run(state, "absent-run", parallel=parallel), parallel=parallel)
    with transaction() as db:
        assert db.get(Finding, finding_id).status == "open"
        assert db.scalar(select(func.count()).select_from(FindingVerification)) == 0
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 1
        assert not db.scalar(select(ReviewCandidate.id).where(ReviewCandidate.run_id == "absent-run"))
        assert analytics.run_tokens(db, db.get(ReviewRun, "absent-run")) == 24

    state["findings_enabled"] = True
    finish_workflow(state, "repeat-run", clone_run(state, "repeat-run", parallel=parallel), parallel=parallel)
    with transaction() as db:
        repeated = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == "repeat-run"))
        assert repeated.finding_id == finding_id and not repeated.is_new
        assert repeated.result["relationship"] == "duplicate" and repeated.usage == {"tokens": 0}
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 2
        assert db.get(Finding, finding_id).status == "open"
    assert state["requests"].count("assessment") == 1
