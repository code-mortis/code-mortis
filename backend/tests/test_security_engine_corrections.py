"""Local evidence-transfer and compatibility checks; no model-quality assertions."""

import json
from copy import deepcopy

import pytest
from test_mortis_security import scan_workflow as scan_workflow
from test_mortis_security import selected_result

from code_mortis.core.db import transaction
from code_mortis.core.security import digest
from code_mortis.models import SecurityScan
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
    assert all(request.effort == "high" and request.max_output_tokens == 16384 for request in calls)
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


def test_source_navigation_checkpoint_is_immutable(scan_workflow):
    scan_id = scan_workflow["scan_id"]
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        scan.status = "running"
    first = {"hints": [{"path": "guards.py"}]}
    service.checkpoint(scan_id, "analysis:app.py", "running", source_navigation=first)
    result = service.checkpoint(scan_id, "analysis:app.py", "running", source_navigation={"hints": []})
    assert result["source_navigation"] == first


@pytest.mark.parametrize("selected", ["ocr", "mortis"])
def test_new_scan_pins_policy_without_changing_ocr(scan_workflow, seed, monkeypatch, selected):
    from code_mortis.core.db import uid
    from code_mortis.models import Repository
    from code_mortis.schemas.security import SecurityScanInput

    monkeypatch.setattr(service, "require_available_engine", lambda options: None)
    with transaction() as db:
        db.get(SecurityScan, scan_workflow["scan_id"]).status = "completed"
        repo = db.get(Repository, seed["repo"].id)
        repo.settings = {**repo.settings, "analysis_engine": selected}
        scan = service.create_scan(db, repo, seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid()))
        expected = policy.TASK_CONTRACT if selected == "mortis" else engine.LEGACY_TASK_CONTRACT
        assert scan.config["task_contract"] == expected


@pytest.mark.parametrize("contract", [None, engine.LEGACY_TASK_CONTRACT, policy.TASK_CONTRACT])
def test_retry_keeps_original_policy(scan_workflow, seed, monkeypatch, contract):
    from code_mortis.core.db import uid
    from code_mortis.schemas.security import SecurityScanInput

    monkeypatch.setattr(service, "require_available_engine", lambda options: None)
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
