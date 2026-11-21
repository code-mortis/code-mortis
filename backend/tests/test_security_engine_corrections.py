"""Local evidence-transfer and compatibility checks; no model-quality assertions."""

import json
from copy import deepcopy

import httpx
import pytest
from sqlalchemy import select
from test_llm_http import result as wire_result
from test_mortis_security import scan_workflow as scan_workflow
from test_mortis_security import selected_result
from test_repository_security import saved_finding, saved_scan

from code_mortis.core import file_policy, workflow_inference
from code_mortis.core.db import transaction
from code_mortis.core.errors import InferenceOutputError
from code_mortis.core.security import digest
from code_mortis.integrations import llm
from code_mortis.models import AnalysisBudget, AnalysisPlan, SecurityFinding, SecurityScan
from code_mortis.security_scans import engine, policy, service


@pytest.mark.parametrize("contract", [engine.LEGACY_TASK_CONTRACT, policy.TASK_CONTRACT])
@pytest.mark.parametrize("cite_guard", [False, True])
def test_assessment_receives_uncited_analysis_sources_only_for_new_contract(scan_workflow, monkeypatch, contract, cite_guard):
    from code_mortis.integrations import llm
    from code_mortis.schemas.inference import Response, Usage

    state = scan_workflow
    item = state["item"]
    guard = "def allowed(principal):\n    return principal.has_scope('read')"
    item.inventory["guards.py"] = {"code": False, "hash": digest(guard), "lines": 2}
    read = item.read
    item.read = lambda path, sha=None: guard if path == "guards.py" else read(path, sha)
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        scan.config = {**scan.config, "task_contract": contract}
        connection = scan.config["connection"]
        token_budget = scan.config["limits"]["token_budget"]
    assessments, calls = [], []
    original_ask = engine.ask

    def capture(*args, **kwargs):
        if args[3] == "assessment":
            assessments.append(deepcopy(args[4]))
        return original_ask(*args, **kwargs)

    async def send(connection, credentials, request, **kwargs):
        calls.append(request)
        if "findings" in request.output_schema["properties"]:
            raw = selected_result("analysis", required=["guards.py"] if len(calls) == 1 else [])
            if cite_guard and len(calls) > 1:
                raw["findings"][0]["evidence"].insert(0, {"source_id": "source_2", "start_line": 1, "end_line": 2})
        else:
            raw = selected_result("assessment")
            raw.update(verdict="confirmed", explanation="The guard is supplied for independent assessment.")
        return Response(content=json.dumps(raw), output=raw, usage=Usage(total_tokens=12))

    monkeypatch.setattr(engine, "ask", capture)
    monkeypatch.setattr(llm, "send", send)
    service.perform(state["scan_id"], state["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        assert scan.status == "completed", scan.error
        assert scan.progress["tokens"] == 36
        task = scan.progress["tasks"]["analysis:app.py"]
        if contract == policy.TASK_CONTRACT:
            assert task["context_paths"] == ["app.py", "guards.py"]
            assert task["source_navigation"]["revision"] == item.sha
        else:
            assert "context_paths" not in task and "source_navigation" not in task
    assert len(calls) == 3 and len(assessments) == 1
    for index, request in enumerate(calls):
        input_tokens = llm.reservation(connection, request.model_copy(update={"max_output_tokens": 0}))
        assert request.effort == "high"
        assert request.max_output_tokens == token_budget - index * 12 - input_tokens
    sources = assessments[0]["sources"]["current"]
    assert (sources.get("guards.py") == guard) is (cite_guard or contract == policy.TASK_CONTRACT)
    assert assessments[0]["proposal"]["evidence"][0]["path"] == ("guards.py" if cite_guard else "app.py")
    if cite_guard and contract == engine.LEGACY_TASK_CONTRACT:
        assert list(sources) == ["guards.py", "app.py"]


@pytest.mark.parametrize("contract", [engine.LEGACY_TASK_CONTRACT, policy.TASK_CONTRACT])
def test_supporting_citation_cannot_impersonate_assigned_cause_in_new_contract(tmp_path, contract):
    item = engine.Snapshot(tmp_path, "a" * 40, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}

    def infer(supplied, **kwargs):
        raw = selected_result("analysis")
        raw["findings"][0]["evidence"][0]["source_id"] = "source_2"
        return kwargs["validator"](raw, supplied), {"tokens": 7}

    def run():
        return engine.ask(item, {"kind": "api"}, {}, "analysis", {
            "task_contract": contract, "assigned_path": "app.py",
            "sources": {"current": {"app.py": "guard(value)", "guard.py": "return True"}},
        }, 100, 60, lambda: False, "test", infer=infer, source_packing=False)

    if contract == policy.TASK_CONTRACT:
        with pytest.raises(engine.verification.ResultValidationError, match="assigned-file evidence"):
            run()
    else:
        result, _ = run()
        assert result["findings"][0]["evidence"][0]["path"] == "guard.py"


def test_navigation_replay_uses_pinned_context_without_new_discovery(tmp_path, monkeypatch):
    from code_mortis.security_scans import context as navigation

    item = engine.Snapshot(tmp_path, "a" * 40, {}, lambda: False)
    item.inventory = {"app.py": {}}
    pinned = {"contract": navigation.VERSION, "revision": item.sha, "hints": [], "truncated": True}
    monkeypatch.setattr(navigation, "source_navigation", lambda *args: pytest.fail("replay rediscovered source"))

    def infer(supplied, **kwargs):
        assert supplied["source_navigation"] == pinned
        return kwargs["validator"](selected_result("analysis"), supplied), {"tokens": 7}

    _, usage = engine.ask(item, {"kind": "api"}, {}, "analysis", {
        "task_contract": policy.TASK_CONTRACT, "assigned_path": "app.py",
        "sources": {"current": {"app.py": "unsafe(value)"}},
    }, 100, 60, lambda: False, "test", infer=infer, source_packing=False,
        navigation=pinned, select_navigation=lambda value: value)
    assert usage == {"tokens": 7}


@pytest.mark.parametrize("field,first,replacement", [
    ("source_navigation", {"hints": [{"path": "guards.py"}]}, {"hints": []}),
    ("source_packing", True, False),
    ("source_packing", False, True),
])
def test_source_context_choices_are_pinned_while_checkpoint_progress_advances(scan_workflow, field, first, replacement):
    scan_id = scan_workflow["scan_id"]
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        scan.status = "running"
    service.checkpoint(scan_id, "analysis:app.py", "running", **{field: first}, context_paths=["app.py"])
    result = service.checkpoint(scan_id, "analysis:app.py", "running", **{field: replacement},
                                context_paths=["app.py", "guards.py"])
    assert result[field] == first and result["context_paths"] == ["app.py", "guards.py"]
    result = service.checkpoint(scan_id, "analysis:app.py", "completed", tokens=7, result={"findings": []})
    assert result[field] == first and result["status"] == "completed" and result["tokens"] == 7
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        assert scan.progress["tasks"]["analysis:app.py"] == result
        assert scan.progress["tokens"] == 7


@pytest.mark.parametrize("selected", ["ocr", "mortis"])
def test_new_scan_pins_mortis_policy_and_refuses_ocr(scan_workflow, seed, monkeypatch, selected):
    from code_mortis.core.db import uid
    from code_mortis.models import Repository
    from code_mortis.schemas.security import SecurityScanInput

    with transaction() as db:
        db.get(SecurityScan, scan_workflow["scan_id"]).status = "completed"
        repo = db.get(Repository, seed["repo"].id)
        repo.settings = {**repo.settings, "analysis_engine": selected}
        if selected == "ocr":
            with pytest.raises(ValueError, match="This saved review engine is retired"):
                service.create_scan(db, repo, seed["user"], SecurityScanInput(
                    connection_id="connection", request_key=uid()))
            assert len(list(db.scalars(select(SecurityScan)))) == 1
            return
        scan = service.create_scan(db, repo, seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid()))
        assert scan.config["analysis_engine"] == "mortis"
        assert scan.config["task_contract"] == policy.TASK_CONTRACT


@pytest.mark.parametrize("contract", [None, engine.LEGACY_TASK_CONTRACT, policy.TASK_CONTRACT])
def test_retry_keeps_original_policy(scan_workflow, seed, monkeypatch, contract):
    from code_mortis.core.db import uid
    from code_mortis.schemas.security import SecurityScanInput

    with transaction() as db:
        parent = db.get(SecurityScan, scan_workflow["scan_id"])
        parent.status = "partial"
        parent.config = {key: value for key, value in parent.config.items() if key != "task_contract"}
        if contract is not None:
            parent.config = {**parent.config, "task_contract": contract}
        retry = service.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=1, token_budget=200_000), parent=parent)
        assert retry.config == parent.config
        assert retry.config.get("task_contract") == contract


@pytest.mark.parametrize("cancel_at", ["navigation", "second_source_checkpoint"])
def test_context_checkpoint_cancellation_retains_known_consumption(tmp_path, cancel_at):
    from code_mortis.core.errors import ReviewStopped

    item = engine.Snapshot(tmp_path, "a" * 40, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    item.read = lambda path, sha=None: "guard(value)"
    calls, checkpoints = [], []

    def select_navigation(value):
        if cancel_at == "navigation":
            raise ReviewStopped("Permission revoked before inference")
        return value

    def record_sources(paths):
        checkpoints.append(paths)
        if len(checkpoints) == 2:
            raise ReviewStopped("Permission revoked before source follow-up")

    def infer(supplied, **kwargs):
        calls.append(supplied)
        return kwargs["validator"](selected_result("analysis", required=["guard.py"]), supplied), {"tokens": 7}

    with pytest.raises(ReviewStopped) as stopped:
        engine.ask(item, {"kind": "api"}, {}, "analysis", {
            "task_contract": policy.TASK_CONTRACT, "assigned_path": "app.py",
            "sources": {"current": {"app.py": "unsafe(value)"}},
        }, 100, 60, lambda: False, "test", infer=infer, source_packing=False,
            select_navigation=select_navigation, record_sources=record_sources)
    assert len(calls) == (0 if cancel_at == "navigation" else 1)
    assert stopped.value.usage == {"tokens": 7 * len(calls)}


@pytest.mark.parametrize(("unknown", "related"), [(False, False), (True, False), (False, True)])
def test_scan_phase_caps_charge_evidence_rounds_and_enforce_provider_output_cap(scan_workflow, seed, monkeypatch, unknown, related):
    state = scan_workflow
    with transaction() as db:
        prior = saved_scan(db, seed, status="completed", sha="b" * 40)
        old = saved_finding(db, seed, prior, path="legacy.py")
        old.path = "legacy.py"
        finding_id = old.id
        scan = db.get(SecurityScan, state["scan_id"])
        scan.config = {**scan.config, "limits": {**scan.config["limits"], "token_budget": 4000,
            "effort": "low", "max_output_tokens": 600, "assessment_token_budget": 1000, "verification_token_budget": 1100}}

    item = state["item"]
    item.inventory.update({path: {"code": False, "hash": digest("guard"), "lines": 1} for path in ("legacy.py", "guard.py")})
    item.read = lambda path, sha=None: path + (" old" if sha else " current")
    item.diff = lambda original, **kwargs: "-legacy.py old\n+legacy.py current"
    wire = []

    def handler(request):
        body = json.loads(request.content)
        properties = body["response_format"]["json_schema"]["schema"]["properties"]
        if "findings" in properties:
            phase, result, amount = "analysis", selected_result("analysis"), 50
        elif "finding_id" in properties:
            phase, result, amount = "assessment", selected_result("assessment",
                required=["legacy.py" if related else "guard.py"]), 250
        else:
            phase, amount = "verification", 50
            result = {"verdict": "fixed", "original_issue_present": True, "explanation": "Guard installed.",
                      "change_explanation": "The unauthorized request is rejected.", "required_paths": [],
                      "evidence": [{"source_id": ref, "start_line": 1, "end_line": 1} for ref in ("source_1", "source_2")]}
        wire.append((phase, body["reasoning_effort"], body["max_tokens"]))
        response = wire_result("openai", content=json.dumps(result), usage=not (unknown and phase == "assessment"))
        if "usage" in response:
            response["usage"] = {"prompt_tokens": amount - 1, "completion_tokens": 1, "total_tokens": amount}
        return httpx.Response(200, json=response)

    monkeypatch.setattr(llm, "reservation", lambda *args: 800)
    monkeypatch.setattr(llm, "send", state["send"])
    monkeypatch.setattr(llm, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm.engine_http, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    service.perform(state["scan_id"], state["claim"])

    assert wire[:2] == [("analysis", "low", 600), ("assessment", "low", 200)]
    assert wire[2:] == ([] if unknown else [("verification", "low", 300)])
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        plan = db.scalar(select(AnalysisPlan))
        budgets = {stage: db.get(AnalysisBudget, (plan.id, stage)).charged_tokens
                   for stage in ("security", "assessment", "verification")}
        if unknown:
            assert db.get(SecurityFinding, finding_id).state == "open"
            assert scan.progress["tokens"] is None
            assert budgets == {"security": 1050, "assessment": 1000, "verification": 0}
        else:
            assert scan.status == "partial" and scan.progress["tokens"] == 350
            assert scan.stages["assessment"]["failed"] == 1
            assert scan.progress["tasks"]["assessment:0"]["tokens"] == 250
            assert scan.progress["tasks"]["assessment:0"]["source_packing"] is False
            assert db.get(SecurityFinding, finding_id).state == ("open" if related else "resolved")
            assert budgets == {"security": 350, "assessment": 250, "verification": 50}


def test_legacy_scan_allowance_reserves_the_configured_output_cap(scan_workflow, monkeypatch):
    scan_id = scan_workflow["scan_id"]
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        config = {key: value for key, value in scan.config.items() if key != "execution_limits"}
        config["connection"] = {key: value for key, value in config["connection"].items() if key != "execution_limits"}
        config["limits"] = {**config["limits"], "max_output_tokens": 600}
        claim = scan_workflow["claim"]
        scan.config, scan.status, scan.progress = config, "running", {**scan.progress, "execution": claim}

    def reservation(connection, request):
        assert request.max_output_tokens == 600
        return 500

    monkeypatch.setattr(llm, "reservation", reservation)
    monkeypatch.setattr(llm, "execute_task", lambda *args, **kwargs: pytest.fail("A request exceeding the allowance reached admission"))
    with pytest.raises(InferenceOutputError, match="remaining workflow allowance") as caught:
        workflow_inference.execute(config["connection"], {"api_key": "provider-secret", "extra_headers": {}}, {},
            instructions="Inspect supplied evidence.", schema={"type": "object", "properties": {}},
            validator=lambda raw, supplied: raw, claim=claim, stage="security", task_key="analysis:app.py",
            scan_id=scan_id, token_budget=1000)
    assert caught.value.usage == {"tokens": 0}


def test_ai_exclusions_deny_retained_evidence_and_excluded_supporting_paths(tmp_path):
    ai_policy = file_policy.policy({"exclusions": "fixtures/**"})
    item = engine.Snapshot(tmp_path, "a" * 40, {}, lambda: False, ai_policy=ai_policy)
    blocked = "fixtures/guard.py"
    item.inventory = {"app.py": {}, blocked: {}}
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "unsafe(value)"}}}
    history = [{"path": "app.py", "evidence": [{"path": blocked, "quote": "excluded evidence"}]}]
    with pytest.raises(engine.verification.VerificationOutputError, match="excluded"):
        engine.ask(item, {"kind": "api"}, {}, "analysis", {**context, "history": history}, 100, None,
                   lambda: False, "test", infer=lambda *args, **kwargs: pytest.fail("Excluded history reached AI"))

    calls = []

    def infer(supplied, **kwargs):
        calls.append(supplied)
        assert blocked not in supplied["available_paths"]
        return kwargs["validator"](selected_result("analysis", required=[blocked]), supplied), {"tokens": 7}

    with pytest.raises(engine.verification.VerificationOutputError, match="excluded") as caught:
        engine.ask(item, {"kind": "api"}, {}, "analysis", context, 100, None, lambda: False, "test", infer=infer)
    assert len(calls) == 1 and caught.value.usage == {"tokens": 7}
    assert not engine.payload.reusable_source_context({"source_packing": True, "context_paths": [blocked]}, ai_policy)


@pytest.mark.parametrize("unavailable", [KeyError, TimeoutError])
def test_ai_exclusions_keep_only_revalidated_historical_evidence_in_model_context(tmp_path, unavailable):
    item = engine.Snapshot(tmp_path, "a" * 40, {}, lambda: False,
                           ai_policy=file_policy.policy({"exclusions": "fixtures/**"}))
    item.inventory = {"app.py": {}}
    def read(path, sha=None):
        if path == "unavailable.py":
            raise unavailable("Source cannot be revalidated")
        return "allowed evidence" if sha == "b" * 40 else "current source"

    item.read = read
    cite = {"path": "app.py", "revision": "current", "start_line": 1, "end_line": 1, "quote": "allowed evidence"}
    finding = {"id": "older", "path": "app.py", "kind": "code", "title": "Uncited historical prose",
               "evidence": {"source_sha": "b" * 40, "explanation": "Content from fixtures/hidden.py", "evidence": [cite]}}
    invalid = {**finding, "id": "invalid", "evidence": {**finding["evidence"], "evidence": [{**cite, "quote": "wrong"}]}}
    unreadable = {**finding, "id": "unreadable", "evidence": {**finding["evidence"],
        "evidence": [{**cite, "path": "unavailable.py"}]}}

    def infer(supplied, **kwargs):
        assert supplied["history"] == [{"id": "history_1", "path": "app.py", "kind": "code",
                                       "evidence": {"source_sha": "b" * 40, "evidence": [cite]}}]
        return kwargs["validator"](selected_result("assessment"), supplied), {"tokens": 7}

    _, usage = engine.ask(item, {"kind": "api"}, {}, "assessment", {
        "sources": {"current": {"app.py": "current source"}}, "history": [finding, invalid, unreadable],
    }, None, None, lambda: False, "test", infer=infer)
    assert usage == {"tokens": 7}


def test_historical_evidence_does_not_swallow_scan_deadline(tmp_path):
    def deadline():
        raise TimeoutError("Scan time limit reached")

    item = engine.Snapshot(tmp_path, "a" * 40, {}, deadline)
    item.read = lambda path, sha=None: item.stopped()
    citation = {"path": "app.py", "revision": "current", "start_line": 1, "end_line": 1, "quote": "saved evidence"}
    with pytest.raises(TimeoutError, match="Scan time limit reached"):
        engine.historical_evidence(item, {"evidence": {"source_sha": "b" * 40, "evidence": [citation]}})
