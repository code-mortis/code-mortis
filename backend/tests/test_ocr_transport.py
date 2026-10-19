"""Exercise the pinned OCR boundary without model calls or repository credentials."""

import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_mortis import github, ocr, security_engine, verification

SESSION = "00000000-0000-4000-8000-000000000001"
RESULT = {"findings": [], "required_paths": []}
CONTEXT = {"diff": "", "sources": {"current": {"app.py": "safe"}}}


@pytest.fixture
def transport(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ocr, "git", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "configuration", lambda *a, **k: nullcontext())
    calls = []
    report = {"session_id": SESSION, "status": "success", "summary": {"files_reviewed": 1, "total_tokens": 7}}
    rows = [{"type": "llm_response", "sessionId": SESSION, "taskType": "main_task", "filePath": "evidence.py",
             "tool_calls": [{"name": "task_done", "arguments": json.dumps({"state": "DONE", "result": RESULT})}]}]

    def command(args, root, *a, **kwargs):
        calls.append((args, kwargs))
        if "--preview" in args:
            return 0, json.dumps({"files": [{"path": "evidence.py", "will_review": True}]}), ""
        folder = tmp_path / ".opencodereview/sessions/repo"
        folder.mkdir(parents=True, exist_ok=True)
        header = {"type": "session_start", "sessionId": SESSION, "cwd": str(root)}
        (folder / f"{SESSION}.jsonl").write_text("\n".join(json.dumps(row) for row in [header, *rows]) + "\n")
        return 0, json.dumps(report), ""

    monkeypatch.setattr(ocr, "command", command)

    def invoke(timeout=30):
        return verification.api_verify(tmp_path / "repo", {}, {}, {}, CONTEXT, 1000, timeout, lambda: False,
                                       validator=lambda raw, supplied: raw)

    return SimpleNamespace(invoke=invoke, report=report, rows=rows, calls=calls, command=command)


@pytest.mark.parametrize("field,value", [
    ("comments", {}), ("comments", ""), ("comments", False), ("comments", 0),
    ("warnings", {}), ("warnings", ""), ("warnings", False), ("warnings", 0),
    ("files_reviewed", True), ("files_reviewed", 1.0), ("files_reviewed", "1"),
    ("budget_exceeded", 0), ("budget_exceeded", []), ("budget_exceeded", "false"),
])
def test_malformed_ocr_reports_never_complete_and_keep_known_usage(transport, field, value):
    target = transport.report["summary"] if field in {"files_reviewed", "budget_exceeded"} else transport.report
    target[field] = value
    with pytest.raises(verification.VerificationOutputError) as caught:
        transport.invoke()
    assert caught.value.usage == {"tokens": 7}
    assert caught.value.feedback is None


@pytest.mark.parametrize("empty", [None, []])
def test_ocr_empty_slices_are_accepted(transport, empty):
    transport.report.update(comments=empty, warnings=empty)
    assert transport.invoke() == (RESULT, {"tokens": 7})


@pytest.mark.parametrize("malformed", [None, [], "invalid", 7])
def test_corrupt_session_rows_preserve_known_usage(transport, malformed):
    transport.rows.insert(0, malformed)
    with pytest.raises(verification.VerificationOutputError) as caught:
        transport.invoke()
    assert caught.value.usage == {"tokens": 7}
    assert caught.value.feedback is None


@pytest.mark.parametrize("malformed", [{}, "invalid", [None], [{"name": "task_done", "arguments": 7}]])
def test_corrupt_tool_calls_return_controlled_delivery_errors(transport, malformed):
    transport.rows[0]["tool_calls"] = malformed
    with pytest.raises(verification.VerificationOutputError) as caught:
        transport.invoke()
    assert caught.value.usage == {"tokens": 7}
    assert caught.value.feedback["kind"] == "delivery"


@pytest.mark.parametrize("arguments", ['{"state":"INVALID"}', '{"state":', '{"state":"FAILED"}'])
def test_rejected_ocr_round_does_not_hide_later_terminal_result(transport, arguments):
    transport.rows.insert(0, {**transport.rows[0], "tool_calls": [{"name": "task_done", "arguments": arguments}]})
    assert transport.invoke() == (RESULT, {"tokens": 7})


@pytest.mark.parametrize("prefix,suffix", [("", "<|start|><|channel|>final<|message|>Done."), ("```json\n", "\n```")])
def test_terminal_arguments_match_ocrs_balanced_json_contract(transport, prefix, suffix):
    call = transport.rows[0]["tool_calls"][0]
    call["arguments"] = prefix + call["arguments"] + suffix
    assert transport.invoke() == (RESULT, {"tokens": 7})


@pytest.mark.parametrize("elapsed", [20, 31])
def test_preflight_and_execution_share_one_deadline(transport, monkeypatch, elapsed):
    clock = [0]
    monkeypatch.setattr(verification, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)

    def command(args, *a, **kwargs):
        if "--preview" in args:
            clock[0] = elapsed
        return transport.command(args, *a, **kwargs)

    monkeypatch.setattr(ocr, "command", command)
    if elapsed < 30:
        assert transport.invoke() == (RESULT, {"tokens": 7})
        assert transport.calls[1][1]["timeout"] <= 10
    else:
        with pytest.raises(verification.VerificationOutputError) as caught:
            transport.invoke()
        assert caught.value.usage == {"tokens": 0}
        assert caught.value.status == "skipped"
        assert len(transport.calls) == 1


@pytest.mark.parametrize("failure", [ValueError("Unsafe configuration"), ocr.ReviewStopped("Cancelled"), TimeoutError("Timed out")])
def test_configuration_failures_before_dispatch_do_not_erase_known_usage(transport, monkeypatch, failure):
    @contextmanager
    def configuration(*args, **kwargs):
        raise failure
        yield

    monkeypatch.setattr(ocr, "configuration", configuration)
    with pytest.raises(verification.VerificationOutputError) as caught:
        transport.invoke()
    assert caught.value.usage == {"tokens": 0}
    assert len(transport.calls) == 1


@pytest.mark.parametrize("failure", [github.GitHubError(403, "Access revoked"), TimeoutError("Timed out"), ocr.ReviewStopped("Cancelled")])
def test_optional_diagnostics_cannot_replace_the_original_failure(tmp_path, failure):
    def stopped():
        raise failure

    diagnostics, overflow = verification.scan_diagnostics(
        tmp_path, {}, 1, f"provider failed\n[ocr] Session: {SESSION}", (), stopped)
    assert "provider failed" in diagnostics
    assert overflow is None


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("failure", ["path", "scope", "confirmation"])
@pytest.mark.parametrize("corrected", [True, False])
def test_semantic_validation_is_corrected_once_without_aborting_other_work(
    transport, tmp_path, monkeypatch, kind, failure, corrected,
):
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "safe"}
    finding = {"title": "Synthetic", "category": "test", "severity": "low", "path": "app.py",
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
        "verification": "Test", "evidence": [citation]}
    calls = []

    def response():
        calls.append(1)
        invalid = len(calls) == 1 or not corrected
        if failure == "confirmation":
            return {"verdict": "confirmed", "finding_id": None, "explanation": "Synthetic",
                    "evidence": [] if invalid else [citation], "required_paths": []}
        return {"findings": [{**finding, "path": "other.py" if failure == "scope" and invalid else "app.py"}],
                "required_paths": ["../secret"] if failure == "path" and invalid else []}

    def command(args, *a, **kwargs):
        if "--preview" not in args:
            transport.rows[0]["tool_calls"][0]["arguments"] = json.dumps({"state": "DONE", "result": response()})
        return transport.command(args, *a, **kwargs)

    monkeypatch.setattr(ocr, "command", command)
    monkeypatch.setattr(security_engine.runner_client, "review", lambda *a: (response(), {"tokens": 7}))
    item = security_engine.Snapshot(tmp_path, "a" * 40, {}, lambda: False)
    item.inventory = {"app.py": {}, "other.py": {}}
    context = {"assigned_path": "app.py", "history": [], **CONTEXT}
    args = (item, {"id": "connection", "kind": kind, "model": "test"}, {}, "assessment" if failure == "confirmation" else "analysis",
            context, 1000, 60, lambda: False, "semantic-smoke")
    if corrected:
        result, usage = security_engine.ask(*args)
        assert result["required_paths"] == [] and usage == {"tokens": 14}
    else:
        with pytest.raises(verification.VerificationOutputError) as caught:
            security_engine.ask(*args)
        assert caught.value.usage == {"tokens": 14}
        assert caught.value.feedback["kind"] == "schema"
    assert len(calls) == 2
