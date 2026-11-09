"""Server-owned source paging and bounded tool-error recovery; no provider calls."""

import json

import pytest
from test_pr_engine import (
    CREDENTIALS,
    OPTIONS,
    final,
    install_source_reads,
    plan,
    prepared,
    response,
    run,
    tool_response,
)

from code_mortis.core import analysis, source
from code_mortis.reviews import engine


def read(monkeypatch, text, start, end, *, policy=engine.SOURCE_TOOL_POLICY, repeat=1):
    install_source_reads(monkeypatch)
    monkeypatch.setattr(source, "read_source", lambda *_a, **_kw: text)
    calls = tool_response().tool_calls
    calls[0].arguments.update(start_line=start, end_line=end)
    return engine.read_sources(calls * repeat, repo=None, env={}, plan=plan(), options=OPTIONS,
        credentials=CREDENTIALS, authorize=lambda: None, stopped=lambda: False, source_tool_policy=policy)


@pytest.mark.parametrize("end", [795, 999999999])
def test_large_range_is_paged_and_eof_is_explicit_without_losing_any_source(monkeypatch, end):
    text = "\n".join(f"line {i}" for i in range(1, 796))
    result, sources = read(monkeypatch, text, 1, end)
    selection = result["selections"][0]
    assert [(page["start_line"], page["end_line"]) for page in sources] == [(1, 400), (401, 795)]
    assert "\n".join(page["text"] for page in sources) == text
    assert selection["reason"] == "available" and selection["next_start_line"] is None
    assert selection["eof_line"] == (795 if end > 795 else None)
    assert selection["source_hash"] == analysis.fingerprint(sources)
    assert engine._restore_sources(result, repo=None, env={}, plan=plan(), batch=prepared()[0],
        credentials=CREDENTIALS, authorize=lambda: None, stopped=lambda: False) == sources


def test_page_ceiling_is_shared_across_calls_and_reports_unreturned_source(monkeypatch):
    result, sources = read(monkeypatch, "x\n" * 5000, 1, 5000, repeat=2)
    assert len(sources) == 10 and sources[-1]["end_line"] == 4000
    first, second = result["selections"]
    assert first["reason"] == second["reason"] == "source_limit"
    assert first["next_start_line"] == 4001 and second["next_start_line"] == 1
    assert second["returned_ranges"] == [] and second["source_hash"] is None


def test_utf8_byte_ceiling_never_splits_a_line_or_hides_remaining_source(monkeypatch):
    text = "\n".join(["ñ" * 500] * 500)
    result, sources = read(monkeypatch, text, 1, 500)
    assert 0 < sum(len(page["text"].encode()) for page in sources) <= engine.MAX_READ_BYTES
    assert len(sources) <= 10 and all(page["end_line"] - page["start_line"] < 400 for page in sources)
    assert all(line == "ñ" * 500 for page in sources for line in page["text"].splitlines())
    assert result["selections"][0]["next_start_line"] == sources[-1]["end_line"] + 1
    assert result["selections"][0]["reason"] == "source_limit"


def test_one_oversized_line_is_unavailable_and_does_not_widen_the_byte_limit(monkeypatch):
    result, sources = read(monkeypatch, "x" * (engine.MAX_READ_BYTES + 1), 1, 1)
    assert not sources and result["selections"][0]["reason"] == "source_limit"
    assert result["selections"][0]["next_start_line"] == 1


def test_start_past_eof_is_not_returned_as_available_source(monkeypatch):
    result, sources = read(monkeypatch, "one\ntwo", 3, 400)
    assert not sources and result["selections"][0]["reason"] == "invalid_range"
    assert result["selections"][0]["eof_line"] == 2


@pytest.mark.parametrize("denial", ["excluded", "oversized_blob", "revoked_after_read"])
def test_paging_keeps_policy_blob_and_fresh_authorization_guards(monkeypatch, denial):
    install_source_reads(monkeypatch)
    reads, checks = [], []
    monkeypatch.setattr(source, "read_source", lambda *_a, **_kw: reads.append(True) or "x\n" * 900)
    calls = tool_response().tool_calls
    calls[0].arguments["end_line"] = 900
    if denial == "excluded":
        calls[0].arguments["path"] = "AGENTS.md"
    elif denial == "oversized_blob":
        monkeypatch.setattr(source, "git", lambda *_a, **_kw: str(engine.MAX_SOURCE_BYTES + 1))

    def authorize():
        checks.append(True)
        if denial == "revoked_after_read" and len(checks) == 2:
            raise ValueError("revoked")

    kwargs = dict(repo=None, env={}, plan=plan(), options=OPTIONS, credentials=CREDENTIALS,
                  authorize=authorize, stopped=lambda: False)
    if denial == "revoked_after_read":
        with pytest.raises(PermissionError):
            engine.read_sources(calls, **kwargs)
        assert reads == [True]
    else:
        result, sources = engine.read_sources(calls, **kwargs)
        assert not reads and not sources
        assert result["selections"][0]["reason"] == ("excluded" if denial == "excluded" else "source_limit")
        assert result["selections"][0]["returned_ranges"] == []
    assert len(checks) == 2


def test_paging_sanitizes_complete_source_before_selecting_and_hashing(monkeypatch):
    text = "before\n" * 450 + "fixture-key\n" + "after\n" * 450
    result, sources = read(monkeypatch, text, 1, 901)
    assert "fixture-key" not in json.dumps([result, sources])
    assert sources[-1]["end_line"] == 901
    assert result["selections"][0]["source_hash"] == analysis.fingerprint(sources)


@pytest.mark.parametrize("policy", [None, "remaining-rounds/v1"])
def test_saved_source_read_policies_keep_original_rejection_and_hashes(monkeypatch, policy):
    with pytest.raises(engine.ResultValidationError, match="at most 400"):
        read(monkeypatch, "x\n" * 500, 1, 500, policy=policy)
    result, sources = read(monkeypatch, "one\ntwo", 1, 2, policy=policy)
    assert result["selections"] == [{"path": "helper.py", "start_line": 1, "end_line": 2,
        "reason": "available", "source_hash": analysis.fingerprint(sources[0])}]
    assert read(monkeypatch, "one\ntwo", 1, 3, policy=policy)[0]["selections"][0]["reason"] == "invalid_range"


@pytest.mark.parametrize("bounds", [(0, 2), (2, 1), (True, 2), (1, "PRIVATE_RANGE_MARKER")])
def test_malformed_bounds_remain_rejected_without_coercion(monkeypatch, bounds):
    with pytest.raises(engine.ResultValidationError, match="integer line bounds"):
        read(monkeypatch, "one\ntwo", *bounds)


@pytest.mark.parametrize("effort,limit", [("low", 1), ("medium", 2), ("high", 4)])
@pytest.mark.parametrize("complete", [False, True])
def test_repeated_tool_errors_reach_bounded_final_attempt_without_faking_coverage(monkeypatch, effort, limit, complete):
    install_source_reads(monkeypatch)
    batches = prepared(options={**OPTIONS, "effort": effort})
    invalid = tool_response()
    invalid.tool_calls[0].arguments["end_line"] = "PRIVATE_RANGE_MARKER"
    terminal = final(batches[0])
    if not complete:
        terminal["completed_units"] = []
    result, ledger = run(monkeypatch, plan(), batches, [invalid] * limit + [response(terminal)])
    assert result[0] == ("completed" if complete else "failed")
    assert len(ledger.calls) == limit + 1 and len(ledger.appended) == limit
    assert [bool(request.tools) for request in ledger.calls] == [True] * limit + [False]
    assert result[1]["summary"]["total_tokens"] == limit * 10 + 12
    assert len(result[1]["manifest"]["coverage"]["completed"]) == int(complete)
    assert "PRIVATE_RANGE_MARKER" not in json.dumps([ledger.rows, [r.model_dump() for r in ledger.calls]])
    assert run(monkeypatch, plan(), batches, [], ledger=ledger)[0] == result
    assert len(ledger.calls) == limit + 1


def test_unknown_usage_tool_error_never_allows_another_request(monkeypatch):
    invalid = tool_response(tokens=None)
    invalid.tool_calls[0].arguments["start_line"] = 0
    result, ledger = run(monkeypatch, plan(), prepared(), [invalid])
    assert result[0] == "failed" and result[1]["summary"]["total_tokens"] is None
    assert len(ledger.calls) == 1 and not ledger.appended
