"""No plaintext arguments, identifiers or credentials may enter the new diagnostic records."""

import json
from pathlib import Path

import pytest

from code_mortis import result_diagnostics as diagnostic

SESSION = "00000000-0000-4000-8000-000000000001"
KEY = bytes.fromhex("ab" * 32)
COMPLETE = '{"state":"DONE","result":{"explanation":"private-source-and-credential"}}'


def saved(tmp_path, monkeypatch, gateway, parsed):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    capture = tmp_path / "delivery.jsonl"
    capture.write_text(json.dumps({"version": 1, "attempt": diagnostic.fingerprint(KEY, "attempt", "code-mortis.delivery/v1"),
                                   "calls": [diagnostic.call_shape(gateway, KEY)]}) + "\n")
    folder = tmp_path / ".opencodereview/sessions/repo"
    folder.mkdir(parents=True)
    path = folder / f"{SESSION}.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_start", "cwd": str(root), "sessionId": SESSION},
        {"type": "llm_response", "taskType": "main_task", "sessionId": SESSION, "tool_calls": [parsed]},
    ]) + "\n")
    return root, capture, path


@pytest.mark.parametrize("case", ["gateway_empty", "ocr_lost", "preserved", "non_string", "wrapped", "unicode"])
def test_diagnostics_distinguish_the_boundary_without_exposing_contents(tmp_path, monkeypatch, case):
    arguments = {"gateway_empty": "{}", "non_string": {"private": "private-source-and-credential"},
                 "wrapped": COMPLETE + "<|end|>", "unicode": COMPLETE.replace("private-source-and-credential", "á秘密")}.get(case, COMPLETE)
    gateway = {"id": "private-call-id", "name": "task_done", "arguments": arguments}
    parsed = {**gateway, "arguments": "{}"} if case == "ocr_lost" else {**gateway, "arguments": ""} if case == "non_string" else gateway
    root, capture, _ = saved(tmp_path, monkeypatch, gateway, parsed)
    result = diagnostic.compare(root, SESSION, capture, KEY)
    assert result["origin"] == ("ocr_mismatch" if case == "ocr_lost" else
                                "gateway_incomplete" if case in {"gateway_empty", "non_string"} else "arguments_preserved")
    assert result["gateway_calls"] == result["ocr_calls"] == result["responses"] == 1
    serialized = json.dumps(result)
    assert "private" not in serialized and "秘密" not in serialized and KEY.hex() not in serialized
    assert diagnostic.explanation(result)
    if case == "ocr_lost":
        comparison = result["comparison"]
        assert comparison["gateway"]["result_object"] and not comparison["ocr"]["result_object"]
        assert comparison["gateway"]["fingerprint"] != comparison["ocr"]["fingerprint"]


@pytest.mark.parametrize("failure", ["missing", "truncated", "extra_response", "bad_fields", "wrong_session", "wrong_repo", "other_key", "oversized", "corrupt_session"])
def test_unavailable_diagnostics_never_blame_either_side(tmp_path, monkeypatch, failure):
    call = {"id": "private-call-id", "name": "task_done", "arguments": COMPLETE}
    root, capture, session = saved(tmp_path, monkeypatch, call, call)
    if failure == "missing":
        capture.unlink()
    elif failure == "truncated":
        capture.write_text(capture.read_text() + '{"version":')
    elif failure == "extra_response":
        capture.write_text(capture.read_text() * 2)
    elif failure == "bad_fields":
        value = json.loads(capture.read_text())
        value["calls"][0]["fingerprint"] = "private-source-and-credential"
        capture.write_text(json.dumps(value))
    elif failure in {"wrong_session", "wrong_repo"}:
        session.write_text(session.read_text().replace(SESSION if failure == "wrong_session" else str(root), "private-value"))
    elif failure == "oversized":
        capture.write_text("x" * 65537)
    elif failure == "corrupt_session":
        session.write_text(session.read_text() + "private-invalid-json")
    result = diagnostic.compare(root, SESSION, capture, KEY if failure != "other_key" else b"b" * 32)
    assert result == {"origin": "unavailable"}
    assert "private" not in json.dumps(result)


def test_fingerprints_cannot_be_correlated_between_attempts():
    call = {"id": "private-call-id", "name": "task_done", "arguments": "{}"}
    first = diagnostic.call_shape(call, KEY)
    second = diagnostic.call_shape(call, b"b" * 32)
    assert first["fingerprint"] != second["fingerprint"] and first["call"] != second["call"]
