"""Offline prompt delivery and evidence behavior, not model-quality measurements."""

from copy import deepcopy

import pytest
from test_mortis_security import selected_result

from code_mortis.security_scans import engine, policy

SHA = "a" * 40


@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
@pytest.mark.parametrize("transport", ["callback", "api"])
@pytest.mark.parametrize("contract", [None, "security-task/v3", policy.TASK_CONTRACT])
def test_security_policy_is_delivered_only_for_pinned_new_tasks(tmp_path, monkeypatch, task, transport, contract):
    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}}
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "boundary(value)"}},
               "history": [], "diff": "changed", "original_sha": "b" * 40}
    if contract is not None:
        context["task_contract"] = contract
    original = deepcopy(context)
    delivered = []

    def respond(supplied, **kwargs):
        delivered.append(kwargs["instructions"])
        assert supplied["sources"] == original["sources"]
        assert kwargs["schema"]["$defs"]["SecurityEvidenceSelection"]["properties"]["source_id"]["enum"] == ["source_1"]
        return kwargs["validator"](selected_result(task), supplied), {"tokens": 7}

    def api_verify(*args, **kwargs):
        assert args[5] == 100
        return respond(args[4], **kwargs)

    monkeypatch.setattr(engine.verification, "api_verify", api_verify)
    result, usage = engine.ask(item, {"kind": "api"}, {}, task, context, 100, 60, lambda: False,
                               "task", infer=respond if transport == "callback" else None, source_packing=False)
    assert len(delivered) == 1
    assert delivered[0].startswith(engine.INSTRUCTIONS)
    assert (policy.instructions(task) in delivered[0]) is (contract == policy.TASK_CONTRACT)
    assert "never invent evidence or weaken a finding to bypass validation" in delivered[0]
    assert usage == {"tokens": 7} and context == original
    if task != "analysis":
        assert result["verdict"] == "uncertain"


def test_new_security_policy_preserves_requested_source_and_uncertainty(tmp_path):
    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    original_source = "# Dependency: permission_check is supplied by the module\nreturn permission_check(value)"
    item.read = lambda path, revision=None: "return external_guard(value)"
    calls = []

    def respond(supplied, **kwargs):
        calls.append(deepcopy(supplied))
        raw = selected_result("assessment", required=["guard.py"] if len(calls) == 1 else [])
        raw["explanation"] = "The external guard's behavior remains unavailable."
        return kwargs["validator"](raw, supplied), {"tokens": 5}

    result, usage = engine.ask(item, {"kind": "api"}, {}, "assessment", {
        "task_contract": policy.TASK_CONTRACT, "history": [],
        "sources": {"current": {"app.py": original_source}},
    }, 100, 60, lambda: False, "task", infer=respond, source_packing=False)
    assert len(calls) == 2 and usage == {"tokens": 10}
    assert calls[0]["available_paths"] == ["app.py", "guard.py"]
    assert calls[0]["sources"]["current"] == {"app.py": original_source}
    assert calls[1]["sources"]["current"] == {
        "app.py": original_source, "guard.py": "return external_guard(value)",
    }
    assert result["verdict"] == "uncertain" and result["required_paths"] == []
    assert result["explanation"] == "The external guard's behavior remains unavailable."


def test_new_security_policy_does_not_filter_distinct_candidates(tmp_path):
    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}}
    raw = selected_result("analysis")
    second = deepcopy(raw["findings"][0])
    second.update(title="Unsanitized redirect", category="redirect", severity="medium",
                  trigger="A request supplies a redirect target.", impact="The browser is sent to another site.")
    raw["findings"].append(second)

    def respond(supplied, **kwargs):
        assert policy.instructions("analysis") in kwargs["instructions"]
        return kwargs["validator"](deepcopy(raw), supplied), {"tokens": 5}

    result, usage = engine.ask(item, {"kind": "api"}, {}, "analysis", {
        "task_contract": policy.TASK_CONTRACT, "assigned_path": "app.py",
        "sources": {"current": {"app.py": "unsafe(value)"}},
    }, 100, 60, lambda: False, "task", infer=respond, source_packing=False)
    assert usage == {"tokens": 5}
    assert [finding["title"] for finding in result["findings"]] == [
        "Missing ownership check", "Unsanitized redirect",
    ]
    assert [finding["severity"] for finding in result["findings"]] == ["high", "medium"]
