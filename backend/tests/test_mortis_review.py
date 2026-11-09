"""Offline PR adapter integration through real planning, ledger, gateway and validation."""

import asyncio
import copy
import json
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from test_code_graph_workflow import fixture_git
from test_llm_http import result as wire_result

from code_mortis.core import analysis, source
from code_mortis.core.db import now, transaction
from code_mortis.core.errors import ReviewStopped
from code_mortis.core.security import seal
from code_mortis.integrations import github, llm, llm_http
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    CodeGraphRepository,
    Connection,
    InferenceAdmission,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.reviews import engine, graph_context, mortis, planning
from code_mortis.reviews.service import connection_snapshot
from code_mortis.schemas.code_graph import EXTRACTOR_VERSION, blob_sha
from code_mortis.schemas.common import RepoSettings
from code_mortis.schemas.inference import ToolCall

CREDENTIALS = {"api_key": "synthetic-provider-value", "extra_headers": {}}


@pytest.mark.parametrize(("selected", "label"), [("mortis", "Mortis"), ("ocr", "OCR")])
def test_failed_analysis_names_selected_engine(scenario, selected, label):
    from code_mortis.reviews.service import save_analysis

    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.config = {**run.config, "analysis_engine": selected}
    save_analysis("mortis-run", "mortis-job:1", "failed", {"errors": ["invalid_result"]}, [])
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.error == f"{label} did not complete the selected review scope. Inspect coverage and retry the review."
        assert run.publication == "not_applicable"
        assert run.progress["analysis_status"] == "failed"


@pytest.fixture
def scenario(seed, tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    root.mkdir()
    fixture_git(root, "init", "-q")
    fixture_git(root, "config", "user.name", "Fixture")
    fixture_git(root, "config", "user.email", "fixture@example.invalid")
    (root / "app.py").write_text("def limit():\n    return 1\n")
    (root / "guide.md").write_text("# Guide\nOld behavior.\n")
    (root / "support.py").write_text("def maximum():\n    return 1\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "base")
    base = fixture_git(root, "rev-parse", "HEAD")
    (root / "app.py").write_text("def limit():\n    return 0\n")
    (root / "guide.md").write_text("# Guide\nNew behavior.\n")
    (root / ".env").write_text("TOKEN=synthetic-unreviewable-value\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "proposed change")
    head = fixture_git(root, "rev-parse", "HEAD")
    fixture_git(root, "checkout", "--detach", "-q", base)
    options = RepoSettings(analysis_engine="mortis", token_budget=200_000, timeout_seconds=900,
                           concurrency=2).model_dump()
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.secret = seal(json.dumps(CREDENTIALS))
        snapshot = connection_snapshot(connection)
        repo, pr = db.get(Repository, 22), db.get(PullRequest, "pr")
        repo.settings, pr.base_sha, pr.head_sha = options, base, head
        db.add(ReviewRun(id="mortis-run", pr_id="pr", actor_id=7, trigger="manual", engine="mortis",
                         dedup_key="mortis-run", base_sha=base, head_sha=head, status="running", started_at=now(),
                         config={"analysis_engine": "mortis", "settings": options, "connection": snapshot},
                         progress={"execution": "mortis-job:1"}))
        db.add(Job(id="mortis-job", kind="review", payload={"run_id": "mortis-run"}, status="running",
                   attempts=1, lease_until=now() + timedelta(minutes=10)))
    state = {"root": root, "base": base, "head": head, "connection": snapshot, "options": options,
             "wire": [], "authorizations": [], "snapshots": [], "progress": [], "remote_head": head,
             "remote_base": base, "remote_state": "open", "revoked": False, "finding": True,
             "expand": False, "after_wire": None}

    @contextmanager
    def snapshot_repo(name, number, base_sha, head_sha, token, stopped, **kwargs):
        assert (name, number, base_sha, head_sha, token) == ("team/project", 3, base, head, "synthetic-github-token")
        assert not stopped()
        state["snapshots"].append((base_sha, head_sha))
        yield root, {}, {}, base

    def authorize(db, actor, name, **kwargs):
        state["authorizations"].append((actor.id, name, kwargs))
        if state["revoked"]:
            raise github.GitHubError(403, "Synthetic permission revocation")
        return {"id": 22, "permissions": {"pull": True, "push": True}}

    def remote(method, path, token, **kwargs):
        assert (method, path, token) == ("GET", "/repos/team/project/pulls/3", "synthetic-github-token")
        return {"state": state["remote_state"], "head": {"sha": state["remote_head"]},
                "base": {"sha": state["remote_base"]}}

    def handler(request):
        body = json.loads(request.content)
        state["wire"].append(body)
        supplied = next(json.loads(message["content"]) for message in body["messages"]
                        if message["role"] == "user" and "assigned_units" in message["content"])
        units = supplied["assigned_units"]
        findings = []
        if state["finding"]:
            unit = next((unit for unit in units if unit["path"] == "app.py"), None)
            if unit:
                findings = [{"unit_key": unit["key"], "path": "app.py", "start_line": 2, "end_line": 2,
                    "severity": "medium", "category": "correctness", "content": "The limit returns zero.",
                    "suggestion_code": "    return 1",
                    "trigger": "A caller asks for the permitted limit.", "consequence": "No work can be allocated.",
                    "evidence": [{"source_id": next(source["source_id"] for source in supplied["citation_sources"]
                                                   if source["path"] == "app.py"), "start_line": 2, "end_line": 2}]}]
                properties = body["response_format"]["json_schema"]["schema"]["$defs"]["EngineFinding"]["properties"]
                if "existing_code" in properties:
                    findings[0]["existing_code"] = "    return 0"
        if state["after_wire"]:
            state["after_wire"]()
        if state["expand"] and len(state["wire"]) == 1:
            return httpx.Response(200, json=wire_result("openai", content=None, call=ToolCall(
                id="source-1", name="read_source", arguments={"path": "support.py", "start_line": 1, "end_line": 2})))
        output = {"task_key": supplied["task_key"], "completed_units": [unit["key"] for unit in units],
                  "findings": findings}
        return httpx.Response(200, json=wire_result("openai", content=json.dumps(output)))

    monkeypatch.setattr(source, "review_snapshot", snapshot_repo)
    monkeypatch.setattr(github, "authorize_repo", authorize)
    monkeypatch.setattr(github, "installation_token", lambda *a, **kw: "synthetic-github-token")
    monkeypatch.setattr(github, "request", remote)
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    state["handler"] = handler
    return state


def review(scenario, **changes):
    options = {"run_id": "mortis-run", "claim": "mortis-job:1", "progress": scenario["progress"].append}
    options.update(changes)
    return mortis.review("team/project", 3, scenario["base"], scenario["head"], scenario["connection"],
                         copy.deepcopy(CREDENTIALS), copy.deepcopy(scenario["options"]), "synthetic-github-token", **options)


def settings(scenario, **changes):
    scenario["options"] = {**scenario["options"], **changes}
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.config = {**run.config, "settings": scenario["options"]}
        db.get(Repository, 22).settings = scenario["options"]


def test_production_adapter_plans_persists_validates_and_replays_without_inference(scenario):
    first = review(scenario)
    status, report, comments = first
    assert status == "completed" and report["summary"]["total_tokens"] == 12
    coverage = report["manifest"]["coverage"]
    assert {entry["path"] for entry in coverage["selected"]} == {"app.py", "guide.md"}
    assert coverage["completed"] == coverage["selected"] and not coverage["failed"]
    assert len(comments) == 1 and comments[0]["source_sha"] == scenario["head"]
    assert comments[0]["existing_code"] == "    return 0"
    assert scenario["progress"][0] == {"phase": "analysis", "selected": 2, "completed": 0, "failed": 0}
    assert comments[0]["evidence"] == [{"revision": scenario["head"], "path": "app.py", "start_line": 2,
                                         "end_line": 2, "quote": "    return 0"}]
    assert len(scenario["wire"]) == 1 and scenario["wire"][0]["model"] == scenario["connection"]["model"]
    assert scenario["snapshots"] == [(scenario["base"], scenario["head"])]
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan))
        assert plan.run_id == "mortis-run" and plan.job_id == "mortis-job"
        assert plan.snapshot["versions"]["source_code"] == engine.SOURCE_CODE_POLICY
        assert plan.snapshot["revisions"] == {"base": scenario["base"], "head": scenario["head"], "merge_base": scenario["base"]}
        assert {entry["path"] for entry in plan.snapshot["selected"]} == {"app.py", "guide.md"}
        assert any(entry["path"] == ".env" for entry in plan.snapshot["exclusions"])
        task = db.scalar(select(AnalysisTask))
        assert task.status == "completed" and task.result["kind"] == "final"
        attempt = db.scalar(select(AnalysisAttempt))
        assert attempt.usage_tokens == 12 and attempt.usage_state == "known"
        assert db.scalar(select(InferenceAdmission)).status == "released"
        assert db.get(AnalysisBudget, (plan.id, "analysis")).charged_tokens == 12
        frozen = copy.deepcopy(plan.snapshot)
    assert review(scenario) == first
    assert len(scenario["wire"]) == 1
    with transaction() as db:
        assert db.scalar(select(AnalysisPlan)).snapshot == frozen
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 1


def test_legacy_source_code_plan_replays_without_rewriting_contract(scenario, monkeypatch):
    with monkeypatch.context() as legacy:
        legacy.setattr(engine, "SOURCE_CODE_POLICY", None)
        original = review(scenario)
    assert original[0] == "completed"
    with transaction() as db:
        frozen = copy.deepcopy(db.scalar(select(AnalysisPlan)).snapshot)
        assert "source_code" not in frozen["versions"]
    assert review(scenario) == original
    assert len(scenario["wire"]) == 1
    with transaction() as db:
        assert db.scalar(select(AnalysisPlan)).snapshot == frozen


def test_file_limit_keeps_waived_denominator_and_never_dispatches(scenario):
    settings(scenario, max_files=1)
    before = []
    status, report, comments = review(scenario, before_analysis=before.append)
    assert status == "skipped" and comments == [] and not scenario["wire"]
    coverage = report["manifest"]["coverage"]
    assert {entry["path"] for entry in coverage["selected"]} == {"app.py", "guide.md"}
    assert len(coverage["waived"]) == 2 and not coverage["completed"] and not coverage["failed"]
    assert before[0]["manifest"]["terminal_state"] == "skipped"
    with transaction() as db:
        assert db.scalar(select(AnalysisPlan)) is not None
        assert list(db.scalars(select(AnalysisAttempt))) == []


def test_finite_budget_failure_is_explicit_and_has_no_http_calls(scenario):
    settings(scenario, token_budget=1000)
    status, report, _ = review(scenario)
    assert status == "failed" and report["summary"]["budget_exceeded"]
    assert not scenario["wire"] and len(report["manifest"]["coverage"]["failed"]) == 2
    with transaction() as db:
        assert list(db.scalars(select(AnalysisAttempt))) == []
        assert db.scalar(select(AnalysisBudget).where(AnalysisBudget.stage == "analysis")).charged_tokens == 0
        assert not list(db.scalars(select(InferenceAdmission).where(InferenceAdmission.status == "active")))


@pytest.mark.parametrize("change", ["head", "base", "closed", "local_head", "cancel", "revoked", "disabled_actor"])
def test_changed_target_or_lost_authority_stops_before_inference(scenario, change):
    if change in {"head", "base"}:
        scenario[f"remote_{change}"] = "f" * 40
    elif change == "closed":
        scenario["remote_state"] = "closed"
    elif change == "revoked":
        scenario["revoked"] = True
    else:
        with transaction() as db:
            if change == "local_head":
                db.get(PullRequest, "pr").head_sha = "f" * 40
            elif change == "cancel":
                db.get(ReviewRun, "mortis-run").cancel_requested = True
            else:
                db.get(User, 7).enabled = False
    with pytest.raises((ReviewStopped, github.GitHubError)):
        review(scenario)
    assert not scenario["wire"]
    with transaction() as db:
        assert list(db.scalars(select(AnalysisPlan))) == []
        assert list(db.scalars(select(AnalysisAttempt))) == []


@pytest.mark.parametrize("change", ["revision", "revoked"])
def test_authority_is_checked_again_after_paid_response_before_acceptance(scenario, change):
    def revoke():
        scenario["remote_head" if change == "revision" else "revoked"] = "f" * 40 if change == "revision" else True
    scenario["after_wire"] = revoke
    with pytest.raises((ReviewStopped, github.GitHubError)):
        review(scenario)
    assert len(scenario["wire"]) == 1
    with transaction() as db:
        assert db.scalar(select(AnalysisTask)).result is None
        assert db.scalar(select(AnalysisAttempt)).usage_tokens == 12
        assert db.scalar(select(InferenceAdmission)).status == "released"


def test_changed_credentials_cannot_use_frozen_snapshot(scenario):
    with transaction() as db:
        db.get(Connection, "connection").secret = seal(json.dumps({**CREDENTIALS, "api_key": "changed-synthetic-value"}))
    with pytest.raises(ValueError, match="credentials changed"):
        review(scenario)
    assert not scenario["wire"]


def test_missing_paused_graph_is_optional_and_keeps_ordinary_coverage(scenario):
    with transaction() as db:
        db.add(CodeGraphRepository(repository_id=22, enabled=True, actor_id=7, paused=True))
        db.flush()
        options = graph_context.configuration(db, db.get(Repository, 22), db.get(Connection, "connection"),
                                              db.get(User, 7), engine={"analysis_engine": "mortis"})
    status, report, _ = review(scenario, graph_options=options)
    assert status == "completed" and len(report["manifest"]["coverage"]["completed"]) == 2
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan))
        selections = plan.snapshot["graph"]["selections"]
        assert set(selections) == {"app.py", "guide.md"}
        assert all(record["reason"] == "unavailable" for record in selections.values())
        assert list(db.scalars(select(Job).where(Job.kind == "code_graph_refresh"))) == []
    assigned = next(json.loads(message["content"]) for message in scenario["wire"][0]["messages"]
                    if message["role"] == "user" and "assigned_units" in message["content"])
    assert "code_graph" not in assigned


def test_source_expansion_uses_real_pinned_git_and_replays_saved_dynamic_round(scenario):
    scenario["expand"] = True
    first = review(scenario)
    assert first[0] == "completed" and first[1]["summary"]["total_tokens"] == 24
    assert len(scenario["wire"]) == 2
    second_payload = json.loads(scenario["wire"][1]["messages"][-1]["content"])
    assert second_payload["supporting_sources"][0]["path"] == "support.py"
    assert second_payload["supporting_sources"][0]["revision"] == scenario["head"]
    assert second_payload["supporting_sources"][0]["text"] == "def maximum():\n    return 1"
    with transaction() as db:
        tasks = list(db.scalars(select(AnalysisTask)))
        root = next(task for task in tasks if not task.dependencies)
        child = next(task for task in tasks if task.dependencies)
        assert root.result["kind"] == "expansion" and child.result["kind"] == "final"
        assert child.dependencies == [root.id] and child.scope == root.scope
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 2
    assert review(scenario) == first
    assert len(scenario["wire"]) == 2


def test_two_batches_can_use_a_finite_budget_after_first_reservation_settles(scenario, monkeypatch):
    # Admit one 16,384-token response reservation, but require its settlement before the second.
    settings(scenario, token_budget=30_000)
    reserve, blocked = analysis.claim_task, []
    def claim(*args, **kwargs):
        lease = reserve(*args, **kwargs)
        if lease is None:
            blocked.append(True)
        return lease
    monkeypatch.setattr(analysis, "claim_task", claim)
    original = planning.plan_review
    def split(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan.batches = [[unit] for unit in plan.units]
        return plan
    monkeypatch.setattr(planning, "plan_review", split)
    async def delayed(request):
        await asyncio.sleep(0.05)
        return scenario["handler"](request)
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(delayed), trust_env=False))
    status, report, _ = review(scenario)
    assert status == "completed" and len(scenario["wire"]) == 2
    assert blocked, "The second batch must wait for the first reservation to settle"
    assert report["summary"]["total_tokens"] == 24
    assert len(report["manifest"]["coverage"]["completed"]) == 2
    with transaction() as db:
        budget = db.scalar(select(AnalysisBudget).where(AnalysisBudget.stage == "analysis"))
        assert budget.token_limit == 30_000 and budget.charged_tokens == 24
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 2


@pytest.fixture
def optional_graph(scenario, monkeypatch):
    state = {"available": True, "text": "def maximum():\n    return 1\n", "calls": []}
    options = {"version": graph_context.ENGINE_VERSION, "repository_id": 22, "actor_id": 7,
               "policy_digest": "e" * 64}

    def prepare(config, path, sha, _repo, _env, _settings, _credentials, _stopped, *, remaining,
                saved=None, base_sha=None, trusted_rules=None):
        state["calls"].append((path, copy.deepcopy(saved)))
        file = {"path": "support.py", "blob_sha": blob_sha(state["text"].encode()),
                "size_bytes": len(state["text"].encode()), "relationship": "imports_file"}
        record = copy.deepcopy(saved) if saved is not None else {
            "version": graph_context.ENGINE_VERSION, "repository_id": 22, "commit_sha": sha,
            "base_sha": base_sha, "extractor_version": EXTRACTOR_VERSION,
            "policy_digest": config["policy_digest"], "snapshot_id": "synthetic-graph-snapshot",
            "status": "ready", "reason": "used", "files": [file], "source_bytes": file["size_bytes"],
            "reserved_bytes": file["size_bytes"], "truncated": False,
        }
        if not state["available"]:
            return graph_context.omitted(record, "source_unavailable")
        return record, {"commit_sha": sha, "snapshot_id": record["snapshot_id"], "index_status": "ready",
                        "sources": [{**file, "text": state["text"]}]}

    monkeypatch.setattr(graph_context, "prepare", prepare)
    return state, options


@pytest.mark.parametrize("expanded", [False, True])
def test_lost_graph_replays_saved_final_chain_without_rejecting_immutable_plan(scenario, optional_graph, expanded):
    graph, options = optional_graph
    scenario["expand"] = expanded
    first = review(scenario, graph_options=options)
    assert first[0] == "completed"
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan))
        identity, frozen = plan.id, copy.deepcopy(plan.snapshot)
        before_attempts = len(list(db.scalars(select(AnalysisAttempt))))
    paid = len(scenario["wire"])
    graph["available"] = False
    second = review(scenario, graph_options=options)
    assert second == first
    assert len(scenario["wire"]) == paid == (2 if expanded else 1)
    assert second[1]["summary"]["total_tokens"] == (24 if expanded else 12)
    assert all(saved is not None for _, saved in graph["calls"][-2:])
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan))
        assert plan.id == identity and plan.snapshot == frozen
        assert all(record["reason"] == "used" for record in plan.snapshot["graph"]["selections"].values())
        assert len(list(db.scalars(select(AnalysisAttempt)))) == before_attempts
    graph_progress = [value for value in scenario["progress"] if "analysis_plan_id" in value][-1]
    assert all(record["reason"] == "source_unavailable" for record in graph_progress["code_graph"].values())


def test_lost_graph_preserves_completed_batch_and_fences_pending_frozen_request(scenario, optional_graph, monkeypatch):
    graph, options = optional_graph
    settings(scenario, concurrency=1)
    original = planning.plan_review
    def split(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan.batches = [[unit] for unit in plan.units]
        return plan
    monkeypatch.setattr(planning, "plan_review", split)
    execute, dispatches = llm.execute_task, []
    async def interrupt_second(*args, **kwargs):
        dispatches.append(args[0])
        if len(dispatches) == 2:
            raise ReviewStopped("Synthetic worker interruption before the second task")
        return await execute(*args, **kwargs)
    monkeypatch.setattr(llm, "execute_task", interrupt_second)
    with pytest.raises(ReviewStopped, match="worker interruption"):
        review(scenario, graph_options=options)
    with transaction() as db:
        tasks = list(db.scalars(select(AnalysisTask)))
        assert sorted(task.status for task in tasks) == ["completed", "pending"]
        frozen = copy.deepcopy(db.scalar(select(AnalysisPlan)).snapshot)
    assert len(scenario["wire"]) == 1
    monkeypatch.setattr(llm, "execute_task", execute)
    graph["available"] = False
    status, report, comments = review(scenario, graph_options=options)
    assert status == "partial" and len(scenario["wire"]) == 1
    coverage = report["manifest"]["coverage"]
    assert {item["path"] for item in coverage["selected"]} == {"app.py", "guide.md"}
    assert [item["path"] for item in coverage["completed"]] == ["app.py"]
    assert [item["path"] for item in coverage["failed"]] == ["guide.md"]
    assert report["errors"] == [{"path": "guide.md", "message": "frozen_context_unavailable"}]
    assert len(comments) == 1 and comments[0]["path"] == "app.py"
    assert report["summary"]["total_tokens"] == 12
    with transaction() as db:
        assert db.scalar(select(AnalysisPlan)).snapshot == frozen
        tasks = list(db.scalars(select(AnalysisTask)))
        assert sorted(task.status for task in tasks) == ["completed", "pending"]
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 1


def test_prompt_omissions_preserve_graph_selection_provenance_and_replay(scenario, optional_graph):
    graph, options = optional_graph
    graph["text"] = "x" * graph_context.MAX_BYTES
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.options = {"prompt_token_ceiling": 14_000}
        scenario["connection"] = connection_snapshot(connection)
        run = db.get(ReviewRun, "mortis-run")
        run.config = {**run.config, "connection": scenario["connection"]}
    first = review(scenario, graph_options=options)
    assert first[0] == "completed" and len(scenario["wire"]) == 1
    payload = next(json.loads(message["content"]) for message in scenario["wire"][0]["messages"]
                   if message["role"] == "user" and "assigned_units" in message["content"])
    assert "code_graph" not in payload
    assert {source["path"] for source in payload["citation_sources"]} == {"app.py", "guide.md"}
    with transaction() as db:
        frozen_graph = db.scalar(select(AnalysisPlan)).snapshot["graph"]
        assert all(record["reason"] == "used" for record in frozen_graph["selections"].values())
        assert list(frozen_graph["prompt_omissions"].values()) == [["app.py", "guide.md"]]
    progress = next(value for value in scenario["progress"] if "analysis_plan_id" in value)
    assert progress["code_graph_prompt_omissions"] == frozen_graph["prompt_omissions"]
    assert review(scenario, graph_options=options) == first
    assert len(scenario["wire"]) == 1


def test_small_prompt_ceiling_splits_required_source_and_diff_into_complete_batches(scenario, monkeypatch):
    root, base = scenario["root"], scenario["base"]
    fixture_git(root, "checkout", "--detach", "-q", scenario["head"])
    text = "def limit():\n    return 0\n" + "".join(f"value_{index:04} = 1\n" for index in range(240))
    (root / "app.py").write_text(text)
    fixture_git(root, "add", "app.py")
    fixture_git(root, "commit", "-qm", "many required source lines")
    head = fixture_git(root, "rev-parse", "HEAD")
    fixture_git(root, "checkout", "--detach", "-q", base)
    scenario.update(head=head, remote_head=head, finding=False)
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.options = {"prompt_token_ceiling": 12_000}
        scenario["connection"] = connection_snapshot(connection)
        run = db.get(ReviewRun, "mortis-run")
        run.head_sha = db.get(PullRequest, "pr").head_sha = head
        run.config = {**run.config, "connection": scenario["connection"]}

    @contextmanager
    def snapshot_repo(_name, _number, base_sha, head_sha, _token, stopped, **kwargs):
        assert (base_sha, head_sha) == (base, head) and not stopped()
        yield root, {}, {}, base

    original, observed = planning.plan_review, {}
    def plan(*args, **kwargs):
        assert kwargs["max_unit_bytes"] == (12_000 - 8192) // 2
        observed["plan"] = original(*args, **kwargs)
        return observed["plan"]
    monkeypatch.setattr(source, "review_snapshot", snapshot_repo)
    monkeypatch.setattr(planning, "plan_review", plan)
    status, report, comments = review(scenario)
    assert status == "completed" and comments == [] and len(scenario["wire"]) > 1
    assert {item["path"] for item in report["manifest"]["coverage"]["completed"]} == {"app.py", "guide.md"}
    assert not report["manifest"]["coverage"]["failed"] and not report["errors"]
    payloads = [next(json.loads(message["content"]) for message in request["messages"]
                     if message["role"] == "user" and "assigned_units" in message["content"])
                for request in scenario["wire"]]
    assigned = [unit for payload in payloads for unit in payload["assigned_units"]]
    assert len({unit["key"] for unit in assigned}) == len(assigned) == len(observed["plan"].units)
    assert {unit["key"] for unit in assigned} == {unit.key for unit in observed["plan"].units}
    app = sorted((unit for unit in assigned if unit["path"] == "app.py"), key=lambda unit: unit["start_line"])
    assert len(app) > 1 and "".join(unit["source"] for unit in app) == text
    assert [line for unit in app for line in range(unit["start_line"], unit["end_line"] + 1)] == list(range(1, 243))
    expected_diff = source.git(root, {}, "diff", "--no-ext-diff", "--no-textconv", "--unified=3",
                               base, head, "--", "app.py")
    def rows(patch):
        return [line for line in patch.splitlines(keepends=True)
                if line[:1] in {"+", "-", " ", "\\"} and not line.startswith(("+++", "---"))]
    assert [line for unit in app for line in rows(unit["diff"])] == rows(expected_diff)
    with transaction() as db:
        frozen = db.scalar(select(AnalysisPlan)).snapshot
        assert len(frozen["tasks"]) == len(scenario["wire"])
        assert {entry["path"] for entry in frozen["selected"]} == {"app.py", "guide.md"}
        assert all(task.status == "completed" for task in db.scalars(select(AnalysisTask)))


@pytest.mark.parametrize("policy", [None, "remaining-rounds/v1", engine.SOURCE_TOOL_POLICY])
def test_source_tool_policy_is_pinned_for_new_and_legacy_plan_replay(scenario, monkeypatch, policy):
    from code_mortis.reviews import engine

    settings(scenario, effort="low")
    scenario["expand"] = True
    with monkeypatch.context() as patcher:
        patcher.setattr(engine, "SOURCE_TOOL_POLICY", policy)
        if policy is None:
            patcher.setattr(engine.analysis, "output_token_policy", lambda snapshot=None: None)
        first = review(scenario)
    assert first[0] == "completed" and len(scenario["wire"]) == 2
    with transaction() as db:
        saved = db.scalar(select(AnalysisPlan)).snapshot
        assert saved["versions"].get("source_tools") == policy
        assert saved["versions"].get("output_tokens") == (None if policy is None else engine.analysis.OUTPUT_TOKEN_POLICY)
    assert all(request["max_tokens"] == (8192 if policy is None else 16384) for request in scenario["wire"])
    assert review(scenario) == first
    assert len(scenario["wire"]) == 2


@pytest.mark.parametrize("policy", ["remaining-rounds/v1", engine.SOURCE_TOOL_POLICY])
@pytest.mark.parametrize("bounds", [{"start_line": 2, "end_line": 1}, {"start_line": 1, "end_line": 2.0}])
def test_repeated_source_range_failures_preserve_final_attempt_and_durable_accounting(scenario, monkeypatch, policy, bounds):
    original = scenario["handler"]

    def handler(request):
        valid = original(request)
        if len(scenario["wire"]) <= 2:
            return httpx.Response(200, json=wire_result("openai", content=None, call=ToolCall(
                id="bad-range", name="read_source", arguments={"path": "support.py", **bounds})))
        return valid

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    with monkeypatch.context() as patcher:
        patcher.setattr(engine, "SOURCE_TOOL_POLICY", policy)
        first = review(scenario)
    bounded = policy == engine.SOURCE_TOOL_POLICY
    assert first[0] == ("completed" if bounded else "failed")
    assert len(scenario["wire"]) == (3 if bounded else 2)
    assert first[1]["summary"]["total_tokens"] == (36 if bounded else 24)
    if bounded:
        assert "tools" not in scenario["wire"][-1]
    with transaction() as db:
        attempts = list(db.scalars(select(AnalysisAttempt)))
        assert all(attempt.usage_state == "known" for attempt in attempts)
        assert sum(attempt.usage_tokens for attempt in attempts) == first[1]["summary"]["total_tokens"]
        assert all(ticket.status == "released" for ticket in db.scalars(select(InferenceAdmission)))
    assert review(scenario) == first and len(scenario["wire"]) == (3 if bounded else 2)


@pytest.mark.parametrize("legacy", [False, True])
def test_prompt_version_preserves_frozen_evidence_requests_across_upgrade(scenario, monkeypatch, legacy):
    settings(scenario, effort="low")
    scenario["expand"] = True
    with monkeypatch.context() as patcher:
        if legacy:
            patcher.setattr(engine, "PROMPT_VERSION", "mortis-pr-prompt/v1")
        first = review(scenario)
    assert first[0] == "completed" and len(scenario["wire"]) == 2
    payload = json.loads(scenario["wire"][-1]["messages"][-1]["content"])
    assert ("remaining_source_rounds" in payload) is not legacy
    with transaction() as db:
        saved = db.scalar(select(AnalysisPlan)).snapshot
        assert saved["versions"]["prompt"] == ("mortis-pr-prompt/v1" if legacy else engine.PROMPT_VERSION)
    assert review(scenario) == first and len(scenario["wire"]) == 2


@pytest.mark.parametrize("limited", [False, True])
def test_incomplete_provider_results_remain_uncovered_and_replay_their_diagnostic(scenario, monkeypatch, limited):
    # Historical plans retain their original terminal-failure behavior.
    calls = []

    def handler(request):
        calls.append(True)
        return (httpx.Response(200, json=wire_result("openai", stop="length")) if limited
                else httpx.Response(500, json={"error": {"message": "PRIVATE_PROVIDER_TEXT"}}))

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    with monkeypatch.context() as legacy:
        legacy.setattr(analysis, "OUTPUT_RECOVERY_POLICY", None)
        first = review(scenario)
    assert first[0] == "failed" and not first[1]["manifest"]["coverage"]["completed"] and not first[2]
    assert len(first[1]["manifest"]["coverage"]["failed"]) == 2
    assert first[1]["summary"]["total_tokens"] == (12 if limited else None)
    assert {unit["error"] for unit in first[1]["units"]} == {"output_limit" if limited else "provider_http_500"}
    assert review(scenario) == first and len(calls) == 1


@pytest.mark.parametrize("failure", ["output_limit", "unknown_usage", "server_error", "child_output_limit"])
def test_output_limit_after_exhausted_evidence_rounds_recovers_exact_units(scenario, monkeypatch, failure):
    original = scenario["handler"]

    def handler(request):
        valid = original(request)
        number = len(scenario["wire"])
        if number <= 2:
            call = ToolCall(id=f"read-{number}", name="read_source", arguments={"path": "support.py",
                            "start_line": 1, "end_line": 401 if number == 1 else 2})
            return httpx.Response(200, json=wire_result("openai", content=None, call=call))
        if number == 3 or number == 4 and failure == "child_output_limit":
            if failure == "server_error":
                return httpx.Response(500, json={"error": {"message": "PRIVATE_PROVIDER_TEXT"}})
            return httpx.Response(200, json=wire_result("openai", stop="length", usage=failure != "unknown_usage"))
        return valid

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    first = review(scenario)
    recovered = failure in {"output_limit", "child_output_limit"}
    assert len(scenario["wire"]) == (5 if recovered else 3)
    assert first[0] == {"output_limit": "completed", "child_output_limit": "partial"}.get(failure, "failed")
    assert len(first[1]["manifest"]["coverage"]["completed"]) == (
        2 if failure == "output_limit" else 1 if failure == "child_output_limit" else 0)
    assert first[1]["summary"]["total_tokens"] == (60 if recovered else None)
    assert bool(first[2]) is (failure == "output_limit")
    if failure == "output_limit":
        assert all(progress.get("failed", 0) == 0 for progress in scenario["progress"])
    if recovered:
        assignments = []
        for request in scenario["wire"][3:]:
            assert "tools" not in request and request["max_tokens"] == 32768
            user = [json.loads(message["content"]) for message in request["messages"] if message["role"] == "user"]
            assert len(user[0]["assigned_units"]) == len(user[0]["context_units"]) == 1
            assert user[-1]["supporting_sources"][0]["path"] == "support.py"
            assert user[0]["remaining_source_rounds"] == user[-1]["remaining_source_rounds"] == 0
            assignments.extend(user[0]["assigned_units"])
        original_units = next(json.loads(message["content"])["assigned_units"]
                              for message in scenario["wire"][0]["messages"] if message["role"] == "user")
        assert assignments == original_units
    with transaction() as db:
        tasks = list(db.scalars(select(AnalysisTask)))
        attempts = list(db.scalars(select(AnalysisAttempt)))
        assert len(tasks) == len(attempts) == len(scenario["wire"])
        parent = next(task for task in tasks if ":round:2:" in task.key and ":unit:" not in task.key)
        assert parent.result is None and parent.status == ("failed" if recovered else "uncertain")
        children = [task for task in tasks if ":unit:" in task.key]
        assert len(children) == (2 if recovered else 0)
        assert all(task.dependencies == [parent.id] for task in children)
        assert all(ticket.status == "released" for ticket in db.scalars(select(InferenceAdmission)))
        expected = sum(attempt.usage_tokens if attempt.usage_state == "known" else attempt.reserved_tokens
                       for attempt in attempts)
        assert db.get(AnalysisBudget, (parent.plan_id, "analysis")).charged_tokens == expected
    assert review(scenario) == first and len(scenario["wire"]) == (5 if recovered else 3)


@pytest.mark.parametrize("policy", ["split-units/v1", analysis.OUTPUT_RECOVERY_POLICY])
def test_settled_recovery_survives_worker_loss_without_repeating_completed_units(scenario, monkeypatch, policy):
    original = scenario["handler"]

    def handler(request):
        valid = original(request)
        return (httpx.Response(200, json=wire_result("openai", stop="length"))
                if len(scenario["wire"]) == 1 else valid)

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    validate = engine._completed_outcomes

    def crash(result, batch):
        if ":unit:" in batch.key:
            raise RuntimeError("worker lost after durable recovery result")
        return validate(result, batch)

    with monkeypatch.context() as patcher:
        patcher.setattr(analysis, "OUTPUT_RECOVERY_POLICY", policy)
        patcher.setattr(engine, "_completed_outcomes", crash)
        with pytest.raises(RuntimeError, match="worker lost"):
            review(scenario)
    assert len(scenario["wire"]) == 2
    completed = review(scenario)
    assert completed[0] == "completed" and completed[1]["summary"]["total_tokens"] == 36
    assert len(scenario["wire"]) == 3
    assert review(scenario) == completed and len(scenario["wire"]) == 3
    assert [wire["max_tokens"] for wire in scenario["wire"]] == [
        16384, *([16384 if policy == "split-units/v1" else 32768] * 2)]


@pytest.mark.parametrize("policy", ["split-units/v1", analysis.OUTPUT_RECOVERY_POLICY])
@pytest.mark.parametrize("truncated_again", [False, True])
def test_single_unit_truncation_gets_one_larger_attempt_only_for_new_plans(scenario, monkeypatch, policy, truncated_again):
    settings(scenario, exclusions="guide.md")
    original = scenario["handler"]

    def handler(request):
        valid = original(request)
        return (httpx.Response(200, json=wire_result("openai", stop="length"))
                if len(scenario["wire"]) == 1 or truncated_again else valid)

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    with monkeypatch.context() as patcher:
        patcher.setattr(analysis, "OUTPUT_RECOVERY_POLICY", policy)
        first = review(scenario)
    new = policy != "split-units/v1"
    assert first[0] == ("completed" if new and not truncated_again else "failed")
    assert [wire["max_tokens"] for wire in scenario["wire"]] == ([16384, 32768] if new else [16384])
    assert first[1]["summary"]["total_tokens"] == (24 if new else 12)
    assert review(scenario) == first and len(scenario["wire"]) == (2 if new else 1)


def test_recovery_reserves_the_larger_allowance_before_dispatch(scenario, monkeypatch):
    settings(scenario, exclusions="guide.md", token_budget=30000)
    original = scenario["handler"]

    def handler(request):
        original(request)
        return httpx.Response(200, json=wire_result("openai", stop="length"))

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    result = review(scenario)
    assert result[0] == "failed" and len(scenario["wire"]) == 1
    assert result[1]["units"][0]["error"] == "budget"
    assert result[1]["summary"]["total_tokens"] == 12


@pytest.mark.parametrize("partial", [False, True])
def test_recovery_replays_validated_units_when_optional_graph_disappears(scenario, optional_graph, monkeypatch, partial):
    graph, options = optional_graph
    original = scenario["handler"]

    def handler(request):
        valid = original(request)
        return (httpx.Response(200, json=wire_result("openai", stop="length"))
                if len(scenario["wire"]) == 1 or partial and len(scenario["wire"]) == 3 else valid)

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    first = review(scenario, graph_options=options)
    assert first[0] == ("partial" if partial else "completed") and len(scenario["wire"]) == 3
    graph["available"] = False
    replay = review(scenario, graph_options=options)
    assert replay[0] == first[0] and replay[2] == first[2]
    assert replay[1]["summary"]["total_tokens"] == first[1]["summary"]["total_tokens"] == 36
    assert replay[1]["manifest"]["coverage"] == first[1]["manifest"]["coverage"]
    assert len(scenario["wire"]) == 3


def test_invalid_recovery_result_never_counts_coverage_or_starts_unbounded_repairs(scenario, monkeypatch):
    original = scenario["handler"]

    def handler(request):
        valid = original(request)
        if len(scenario["wire"]) == 1:
            return httpx.Response(200, json=wire_result("openai", stop="length"))
        if len(scenario["wire"]) == 2:
            payload = next(json.loads(message["content"]) for message in json.loads(request.content)["messages"]
                           if message["role"] == "user")
            return httpx.Response(200, json=wire_result("openai", content=json.dumps({
                "task_key": payload["task_key"], "completed_units": [], "findings": []})))
        return valid

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    first = review(scenario)
    assert first[0] == "partial" and len(first[1]["manifest"]["coverage"]["completed"]) == 1
    assert len(scenario["wire"]) == 3 and first[1]["summary"]["total_tokens"] == 36
    assert review(scenario) == first and len(scenario["wire"]) == 3


def test_graph_context_reaches_the_actual_shared_provider_payload(scenario, optional_graph):
    graph, options = optional_graph
    result = review(scenario, graph_options=options)
    assert result[0] == "completed"
    assert len(scenario["wire"]) == 1
    payload = next(json.loads(message["content"]) for message in scenario["wire"][0]["messages"]
                   if message["role"] == "user" and "assigned_units" in message["content"])
    assert payload["head_sha"] == scenario["head"]
    records = payload["code_graph"]
    assert records and all(record["commit_sha"] == scenario["head"] for record in records.values())
    supporting = [source for record in records.values() for source in record["sources"]]
    assert supporting and all(source["path"] == "support.py" for source in supporting)
    assert all(source["text"] == graph["text"] for source in supporting)
    assert {unit["path"] for unit in payload["assigned_units"]} == {"app.py", "guide.md"}
    assert all(unit["source"] and unit["diff"] for unit in payload["assigned_units"])
