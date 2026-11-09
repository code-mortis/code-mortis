"""Offline PR analysis contracts; no provider or shared database is required by these tests."""

import asyncio
import json

import pytest

from code_mortis.core import analysis
from code_mortis.core.errors import ReviewStopped
from code_mortis.integrations import llm
from code_mortis.integrations.llm_http import InferenceError
from code_mortis.reviews import engine, planning
from code_mortis.schemas.inference import Response, ToolCall, Usage

CONNECTION = {"id": "test", "kind": "api", "protocol": "openai", "model": "fixture", "options": {}}
OPTIONS = {"language": "Spanish", "effort": "medium", "review_sensitivity": "balanced", "exclusions": "", "concurrency": 1}
CREDENTIALS = {"api_key": "fixture-key", "extra_headers": {}}


def unit(path="app.py", *, key="u1", start=1, source="value = 1\n"):
    return planning.ReviewUnit(key, path, None, "M", "b" * 40, start,
        max(start, start + len(source.splitlines()) - 1), source,
        f"@@ -{start},1 +{start},1 @@\n-value = 0\n+{source.rstrip()}\n", "Check defects", "c" * 64)


def plan(units=None):
    units = units or [unit()]
    return planning.ReviewPlan("a" * 40, "b" * 40, "a" * 40, "d" * 64, "e" * 64,
        {"layers": [{"rules": [], "include": [], "exclude": []}]},
        [planning.SelectedFile(path, None, "M", "b" * 40, units=[u for u in units if u.path == path])
         for path in sorted({u.path for u in units})], [], units, [units])


def prepared(value=None, *, options=None, graph=None, connection=None, source_code_policy=engine.SOURCE_CODE_POLICY,
             prompt_version=engine.PROMPT_VERSION, source_tool_policy=engine.SOURCE_TOOL_POLICY):
    value = value or plan()
    return engine.prepare_batches(value, connection or CONNECTION, options or OPTIONS, dict(CREDENTIALS),
                                  graph_by_path=graph, source_code_policy=source_code_policy, prompt_version=prompt_version,
                                  source_tool_policy=source_tool_policy)


def final(batch, findings=None):
    return {"task_key": batch.key, "completed_units": [unit.key for unit in batch.units], "findings": findings or []}


def finding(batch):
    value = batch.units[0]
    return {"unit_key": value.key, "path": value.path, "content": "Incorrect value", "start_line": value.start_line,
            "end_line": value.start_line, "category": "correctness", "severity": "medium",
            **({"existing_code": value.source.splitlines()[0]} if batch.source_code_policy is None else {}),
            "suggestion_code": "value = 0", "trigger": "Request reads value",
            "consequence": "Wrong response", "evidence": [{"source_id": batch.sources[0]["source_id"],
                "start_line": value.start_line, "end_line": value.start_line}]}


class Ledger:
    def __init__(self, batches, responses):
        self.rows = {batch.key: {"id": batch.key, "status": "pending", "result": None,
                     "input_hash": batch.spec.input_hash, "usage_tokens": None} for batch in batches}
        self.responses = list(responses)
        self.calls, self.appended = [], []

    def register(self, spec, request, *, parent_key):
        assert self.rows[parent_key]["status"] == "completed"
        assert spec.input_hash == llm.input_fingerprint(request)
        assert spec.dependencies == [parent_key]
        if spec.key not in self.rows:
            self.appended.append((spec, request))
            self.rows[spec.key] = {"id": spec.key, "status": "pending", "result": None,
                                   "input_hash": spec.input_hash, "usage_tokens": None}
        return spec.key

    async def execute(self, identity, job_id, job_attempt, request, credentials, *, validate_result, **kwargs):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if response is None:
            return None
        result = validate_result(response)
        self.rows[identity].update(status="completed", result=result, usage_tokens=response.usage.total_tokens)
        return response


def run(monkeypatch, value, batches, responses, *, ledger=None, stopped=lambda: False):
    ledger = ledger or Ledger(batches, responses)
    monkeypatch.setattr(llm, "execute_task", ledger.execute)
    monkeypatch.setattr(engine.inference_admission, "cancel_task_waiter", lambda *_args: None)
    result = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get, stopped=stopped)
    return result, ledger


def response(output, *, tokens=12):
    return Response(content=json.dumps(output), output=output, usage=Usage(total_tokens=tokens))


def test_preparation_is_deterministic_includes_all_required_input_and_preferences():
    value = plan([unit(), unit("docs/guide.md", key="u2", source="# Guide\n")])
    first, second = prepared(value)[0], prepared(value)[0]
    assert first.spec == second.spec
    assert first.request.session_id == second.request.session_id
    assert first.request.effort == "medium"
    supplied = json.loads(first.request.messages[0].content)
    assert supplied["language"] == "Spanish" and "Balanced" in supplied["reporting_policy"]
    assert supplied["remaining_source_rounds"] == engine.ROUNDS[OPTIONS["effort"]]
    assert [item["source"] for item in supplied["assigned_units"]] == [u.source for u in value.units]
    assert [item["diff"] for item in supplied["assigned_units"]] == [u.patch for u in value.units]
    assert {tool.name for tool in first.request.tools} == {"read_source"}


@pytest.mark.parametrize("mutation", ["identity", "coverage", "duplicate", "path", "range", "evidence", "code", "extra"])
def test_validation_rejects_untrusted_identity_coverage_location_and_evidence(mutation):
    batch = prepared()[0]
    value = final(batch, [finding(batch)])
    item = value["findings"][0]
    if mutation == "identity":
        value["task_key"] = "foreign"
    elif mutation == "coverage":
        value["completed_units"] = []
    elif mutation == "duplicate":
        value["completed_units"] *= 2
    elif mutation == "path":
        item["path"] = "other.py"
    elif mutation == "range":
        item["end_line"] = 2
    elif mutation == "evidence":
        item["evidence"][0]["source_id"] = "foreign"
    elif mutation == "code":
        item["existing_code"] = "not in supplied source"
    else:
        item["private"] = "not permitted"
    with pytest.raises(ValueError):
        engine.validate_result(value, batch, batch.sources, dict(CREDENTIALS))


def test_valid_finding_constructs_exact_citation_and_empty_findings_complete():
    batch = prepared()[0]
    result = engine.validate_result(final(batch, [finding(batch)]), batch, batch.sources, dict(CREDENTIALS))
    assert result["comments"][0]["evidence"] == [{"revision": "b" * 40, "path": "app.py", "start_line": 1,
                                                  "end_line": 1, "quote": "value = 1"}]
    assert result["comments"][0]["inline_line"] == 1
    assert engine.validate_result(final(batch), batch, batch.sources, dict(CREDENTIALS))["comments"] == []


@pytest.mark.parametrize("removed", ["-- SQL comment", "-- a/header-like.sql", "ordinary removed line"])
def test_removed_header_like_content_preserves_inline_finding_anchor(removed):
    value = plan([unit("query.sql", start=7, source="-- current comment\nSELECT 1;\n")])
    value.units[0].patch = (
        "diff --git a/query.sql b/query.sql\n--- a/query.sql\n+++ b/query.sql\n"
        f"@@ -7,2 +7,2 @@\n-{removed}\n+-- current comment\n SELECT 1;\n"
    )
    batch = prepared(value)[0]
    item = finding(batch)
    item.update(start_line=8, end_line=8)
    item["evidence"][0].update(start_line=8, end_line=8)
    result = engine.validate_result(final(batch, [item]), batch, batch.sources, dict(CREDENTIALS))
    assert result["comments"][0]["inline_line"] == 8
    assert result["comments"][0]["evidence"][0]["quote"] == "SELECT 1;"


def test_diff_file_headers_are_not_counted_as_hunk_lines():
    patch = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -10 +10 @@\n-old\n+++value\n"
    )
    assert engine._diff_lines(patch) == {1, 10}


def test_selected_code_is_built_from_exact_redacted_fragment_without_model_quotes():
    batch = prepared(plan([unit(start=12, source="def value():\n    return 'fixture-key'\n")]))[0]
    item = finding(batch)
    item.update(start_line=13, end_line=13)
    item["evidence"][0].update(start_line=13, end_line=13)
    schema = batch.request.output_schema["$defs"]["EngineFinding"]
    assert "existing_code" not in schema["properties"] and "existing_code" not in schema["required"]
    result = engine.validate_result(final(batch, [item]), batch, batch.sources, dict(CREDENTIALS))
    comment = result["comments"][0]
    assert comment["existing_code"] == batch.sources[0]["text"].splitlines()[1]
    assert comment["existing_code"] == comment["evidence"][0]["quote"]
    assert "fixture-key" not in json.dumps(result)


def test_selected_code_contract_finishes_nine_files_after_all_source_rounds(monkeypatch):
    install_source_reads(monkeypatch)
    value = plan([unit(f"file-{i}.py", key=f"unit-{i}") for i in range(9)])
    batches = prepared(value)
    result, ledger = run(monkeypatch, value, batches, [tool_response(), tool_response(),
        response(final(batches[0], [finding(batches[0])]))])
    assert result[0] == "completed" and len(result[1]["manifest"]["coverage"]["completed"]) == 9
    assert result[2][0]["existing_code"] == "value = 1"
    assert len(ledger.calls) == 3 and not ledger.calls[-1].tools


def test_initial_file_progress_precedes_first_inference(monkeypatch):
    value, batches = plan(), prepared()
    ledger = Ledger(batches, [response(final(batches[0]))])
    updates = []

    async def execute(*args, **kwargs):
        assert updates[0]["selected"] == 1 and updates[0]["completed"] == 0
        assert updates[0]["items"] == {"app.py": {"status": "running"}}
        return await ledger.execute(*args, **kwargs)

    monkeypatch.setattr(llm, "execute_task", execute)
    result = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get, progress=updates.append)
    assert result[0] == "completed" and updates[-1]["completed"] == 1


@pytest.mark.parametrize("source_ending,code_ending,terminal_newline,matches", [
    ("\r\n", "\r\n", True, False), ("\r\n", "\r\n", False, False),
    ("\r\n", "\n", False, True), ("\r\n", "\n", True, True),
    ("\n", "\n", True, True), ("\n", "\n", False, True),
])
def test_exact_source_code_uses_supplied_line_endings_and_terminal_newlines(source_ending, code_ending, terminal_newline, matches):
    lines = ["def choose_port():", "    return 443"]
    source = source_ending.join(lines) + source_ending
    batch = prepared(plan([unit("ports.py", start=12, source=source)]), source_code_policy=None)[0]
    item = finding(batch)
    item.update(end_line=13, existing_code=code_ending.join(lines) + (code_ending if terminal_newline else ""))
    item["evidence"][0]["end_line"] = 13
    # Preparation strips control characters, so even CRLF repositories supply LF source to the provider.
    assert batch.sources[0]["text"] == "\n".join(lines) + "\n"
    if not matches:
        with pytest.raises(engine.ResultValidationError):
            engine.validate_result(final(batch, [item]), batch, batch.sources, dict(CREDENTIALS))
        return
    result = engine.validate_result(final(batch, [item]), batch, batch.sources, dict(CREDENTIALS))
    assert result["comments"][0]["existing_code"] == item["existing_code"]
    item["existing_code"] = item["existing_code"].replace("443", "8443")
    with pytest.raises(engine.ResultValidationError) as error:
        engine.validate_result(final(batch, [item]), batch, batch.sources, dict(CREDENTIALS))
    assert error.value.issue["code"] == "existing_code_mismatch"


def test_split_fragments_need_every_result_before_file_coverage(monkeypatch):
    one, two, other = unit(), unit(key="u2", start=2), unit("readme.md", key="u3")
    value = plan([one, two, other])
    value.batches = [[one], [two], [other]]
    batches = prepared(value)
    result, ledger = run(monkeypatch, value, batches, [response(final(batches[0])),
        InferenceError("refusal", "refused", usage=Usage(total_tokens=4)), response(final(batches[2]))])
    status, report, comments = result
    assert status == "partial" and comments == []
    assert [item["path"] for item in report["manifest"]["coverage"]["completed"]] == ["readme.md"]
    assert [item["path"] for item in report["manifest"]["coverage"]["failed"]] == ["app.py"]
    assert report["summary"]["total_tokens"] == 28
    assert len(ledger.calls) == 3


def test_optional_graph_is_dropped_before_required_source():
    graph = {"app.py": {"commit_sha": "b" * 40, "sources": [{"path": "other.py", "text": "x" * 20_000}]}}
    batch = prepared(graph=graph, connection={**CONNECTION, "options": {"prompt_token_ceiling": 12_000}})[0]
    assert batch.graph_omitted == ["app.py"] and batch.error is None
    payload = json.loads(batch.request.messages[0].content)
    assert "code_graph" not in payload and payload["assigned_units"][0]["source"] == "value = 1\n"
    assert len(batch.sources) == 1


def test_required_oversize_fails_without_dispatch(monkeypatch):
    value = plan([unit(source="x" * 30_000)])
    batches = prepared(value, connection={**CONNECTION, "options": {"prompt_token_ceiling": 12_000}})
    result, ledger = run(monkeypatch, value, batches, [])
    assert result[0] == "failed" and not ledger.calls
    assert result[1]["errors"][0]["message"] == "required_context_limit"


def test_one_schema_repair_is_bounded_and_never_counts_invalid_result(monkeypatch):
    value, batches = plan(), prepared()
    invalid = final(batches[0]) | {"completed_units": []}
    result, ledger = run(monkeypatch, value, batches, [response(invalid), response(invalid)])
    assert result[0] == "failed" and not result[1]["manifest"]["coverage"]["completed"]
    assert len(ledger.calls) == 2 and len(ledger.appended) == 1
    assert "validation_feedback" in ledger.calls[1].messages[1].content
    assert all("Incorrect" not in json.dumps(row["result"]) for row in ledger.rows.values())
    validation = ledger.rows[batches[0].key]["result"]["validation"]
    assert validation["issues"][0]["code"] == "assigned_coverage"
    assert json.loads(ledger.calls[1].messages[1].content)["validation_diagnostics"] == validation
    assert result[1]["units"][0]["validation"] == validation


@pytest.mark.parametrize("case,code,field", [
    ("identity", "task_identity", ["task_key"]),
    ("coverage", "assigned_coverage", ["completed_units"]),
    ("duplicate", "assigned_coverage", ["completed_units"]),
    ("unit", "finding_scope", ["findings", 0]),
    ("path", "finding_scope", ["findings", 0]),
    ("range", "finding_scope", ["findings", 0]),
    ("trigger", "empty_explanation", ["findings", 0, "trigger"]),
    ("consequence", "empty_explanation", ["findings", 0, "consequence"]),
    ("source", "unavailable_citation", ["findings", 0, "evidence", 0]),
    ("source_range", "unavailable_citation", ["findings", 0, "evidence", 0]),
    ("blank_source", "empty_citation", ["findings", 0, "evidence", 0]),
    ("local_source", "missing_local_citation", ["findings", 0, "evidence"]),
    ("code", "unexpected_code_quote", ["findings", 0, "existing_code"]),
    ("missing", "schema_missing", ["findings", 0, "trigger"]),
    ("type", "schema_int_type", ["findings", 0, "start_line"]),
    ("extra", "schema_extra_forbidden", ["findings", 0, "unexpected_field"]),
    ("unsafe_path", "schema_value_error", ["findings", 0, "path"]),
    ("ordered_lines", "schema_value_error", ["findings", 0]),
    ("ordered_evidence", "schema_value_error", ["findings", 0, "evidence", 0]),
])
def test_independent_development_contract_rejections_have_safe_precise_diagnostics(case, code, field):
    # Synthetic development source, independent of the frozen evaluation corpus.
    batch = prepared(plan([unit("ports.py", source="def parse_port(raw):\n\n    return int(raw)\n")]))[0]
    value = final(batch, [finding(batch)])
    item, evidence = value["findings"][0], value["findings"][0]["evidence"][0]
    secret = "fixture-key PRIVATE_RESPONSE_MARKER"
    if case == "identity":
        value["task_key"] = secret
    elif case == "coverage":
        value["completed_units"] = []
    elif case == "duplicate":
        value["completed_units"] *= 2
    elif case == "unit":
        item["unit_key"] = secret
    elif case == "path":
        item["path"] = "foreign.py"
    elif case == "range":
        item["end_line"] = 4
    elif case in {"trigger", "consequence"}:
        item[case] = "   "
    elif case == "source":
        evidence["source_id"] = secret
    elif case == "source_range":
        evidence["end_line"] = 4
    elif case in {"blank_source", "local_source"}:
        evidence["start_line"] = evidence["end_line"] = 2 if case == "blank_source" else 3
    elif case == "code":
        item["existing_code"] = secret
    elif case == "missing":
        del item["trigger"]
    elif case == "type":
        item["start_line"] = secret
    elif case == "extra":
        item[secret] = secret
    elif case == "unsafe_path":
        item["path"] = "../" + secret
    elif case == "ordered_lines":
        item.update(start_line=2, end_line=1)
    else:
        evidence.update(start_line=2, end_line=1)
    with pytest.raises(ValueError) as error:
        engine.validate_result(value, batch, batch.sources, dict(CREDENTIALS))
    diagnostics = engine._validation_feedback(error.value)
    assert diagnostics["issues"][0]["code"] == code
    assert diagnostics["issues"][0]["field"] == field
    assert not diagnostics["truncated"]
    assert "fixture-key" not in json.dumps(diagnostics) and "PRIVATE_RESPONSE_MARKER" not in json.dumps(diagnostics)


def test_schema_diagnostics_are_bounded_even_for_many_hostile_extra_fields(monkeypatch):
    value, batches = plan(), prepared()
    invalid = final(batches[0]) | {f"fixture-key-{index}-" + "x" * 1000: "PRIVATE_RESPONSE_MARKER" for index in range(30)}
    result, ledger = run(monkeypatch, value, batches, [response(invalid), response(invalid)])
    diagnostics = ledger.rows[batches[0].key]["result"]["validation"]
    assert diagnostics["truncated"] and len(diagnostics["issues"]) == 8
    stored = json.dumps([row["result"] for row in ledger.rows.values()])
    assert len(stored) < 5000 and "fixture-key" not in stored and "PRIVATE_RESPONSE_MARKER" not in stored
    assert result[0] == "failed" and len(ledger.calls) == 2
    fallback = engine._validation_feedback(ValueError("fixture-key PRIVATE_EXCEPTION_MARKER"))
    assert fallback["issues"][0]["code"] == "invalid_result"
    assert "PRIVATE_EXCEPTION_MARKER" not in json.dumps(fallback)


def test_repair_can_complete_without_persisting_rejected_provider_text_and_replays(monkeypatch):
    value, batches = plan(), prepared()
    invalid = final(batches[0], [finding(batches[0])])
    invalid["findings"][0]["existing_code"] = "fixture-key PRIVATE_RESPONSE_MARKER"
    result, ledger = run(monkeypatch, value, batches, [response(invalid), response(final(batches[0]))])
    assert result[0] == "completed" and result[1]["summary"]["total_tokens"] == 24
    repair = json.loads(ledger.calls[1].messages[1].content)
    assert repair["remaining_source_rounds"] == 1
    assert repair["validation_diagnostics"]["issues"][0]["code"] == "unexpected_code_quote"
    assert "PRIVATE_RESPONSE_MARKER" not in json.dumps([repair, ledger.rows])
    again, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert again == result and len(ledger.calls) == 2 and len(ledger.appended) == 1


def test_legacy_feedback_replays_its_already_settled_repair_without_replacing_inputs(monkeypatch):
    value, batches = plan(), prepared(prompt_version="mortis-pr-prompt/v1")
    batch = batches[0]
    ledger = Ledger(batches, [])
    ledger.rows[batch.key].update(status="completed", result={"kind": "feedback", "code": "invalid_result"}, usage_tokens=7)
    # Freeze the pre-diagnostics repair exactly as historical task rows did.
    payload = {"supporting_sources": [], "source_read_results": [], "validation_feedback": (
        "The preceding result did not satisfy the required task identity, complete assigned coverage, "
        "finding scope or exact source evidence. Return a complete valid result using the supplied schema.")}
    request = batch.request.model_copy(update={"messages": [batch.request.messages[0],
        engine.Message(role="user", content=json.dumps(payload, ensure_ascii=False))]})
    spec = engine._followup_spec(batch, request, batch.key, 1)
    ledger.register(spec, request, parent_key=batch.key)
    ledger.rows[spec.key].update(status="completed", result=engine.validate_result(final(batch), batch,
                                batch.sources, dict(CREDENTIALS)), usage_tokens=13)
    ledger.appended.clear()
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "completed" and result[1]["summary"]["total_tokens"] == 20
    assert not ledger.calls and not ledger.appended


def test_unknown_usage_feedback_retains_diagnostics_without_automatic_repair(monkeypatch):
    value, batches = plan(), prepared()
    result, ledger = run(monkeypatch, value, batches, [response(final(batches[0]) | {"completed_units": []}, tokens=None)])
    assert result[0] == "failed" and result[1]["summary"]["total_tokens"] is None
    assert result[1]["units"][0]["error"] == "usage_unknown"
    assert result[1]["units"][0]["validation"]["issues"][0]["code"] == "assigned_coverage"
    assert len(ledger.calls) == 1 and not ledger.appended


@pytest.mark.parametrize("code, tokens", [("malformed_response", 12), ("transport_error", None)])
def test_provider_failure_does_not_repair_or_fallback(monkeypatch, code, tokens):
    value, batches = plan(), prepared()
    result, ledger = run(monkeypatch, value, batches, [InferenceError(code, "bad", usage=Usage(total_tokens=tokens))])
    assert result[0] == "failed" and not ledger.appended and len(ledger.calls) == 1
    assert result[1]["summary"]["total_tokens"] == tokens


def test_saved_result_replays_without_inference_and_preserves_usage(monkeypatch):
    value, batches = plan(), prepared()
    result, ledger = run(monkeypatch, value, batches, [response(final(batches[0]))])
    again, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert again == result and len(ledger.calls) == 1


def test_pending_and_cancelled_never_become_empty_complete(monkeypatch):
    value, batches = plan(), prepared()
    with pytest.raises(engine.AnalysisPending):
        run(monkeypatch, value, batches, [None])
    with pytest.raises(ReviewStopped):
        run(monkeypatch, value, batches, [], stopped=lambda: True)


def install_source_reads(monkeypatch):
    from code_mortis.core import source

    monkeypatch.setattr(planning, "allowed_context", lambda _repo, _env, _base, _head, paths, *_args, **_kwargs:
        {path for path in paths if path == "helper.py"})
    monkeypatch.setattr(source, "git", lambda *_args, **_kwargs: "32")
    monkeypatch.setattr(source, "read_source", lambda *_args, **_kwargs: "def helper():\n    return 1\n")


def tool_response(path="helper.py", *, tokens=10):
    return Response(tool_calls=[ToolCall(id="call", name="read_source", arguments={"path": path,
        "start_line": 1, "end_line": 2})], finish_reason="tool_calls", usage=Usage(total_tokens=tokens))


def test_expansion_uses_pinned_bounded_source_and_persists_only_hashes(monkeypatch):
    install_source_reads(monkeypatch)
    value, batches = plan(), prepared()
    result, ledger = run(monkeypatch, value, batches, [tool_response(), response(final(batches[0]))])
    assert result[0] == "completed" and result[1]["summary"]["total_tokens"] == 22
    stored = ledger.rows[batches[0].key]["result"]
    assert stored["kind"] == "expansion" and stored["selections"][0]["source_hash"]
    assert "def helper" not in json.dumps(stored)
    assert "def helper" in ledger.calls[1].messages[1].content
    spec, request = ledger.appended[0]
    assert spec.scope == batches[0].spec.scope and spec.input_hash == llm.input_fingerprint(request)


def test_unknown_usage_never_dispatches_expansion(monkeypatch):
    install_source_reads(monkeypatch)
    value, batches = plan(), prepared()
    result, ledger = run(monkeypatch, value, batches, [tool_response(tokens=None)])
    assert result[0] == "failed" and not ledger.appended and len(ledger.calls) == 1
    assert result[1]["summary"]["total_tokens"] is None


def test_evidence_round_limit_never_claims_complete(monkeypatch):
    install_source_reads(monkeypatch)
    value = plan()
    batches = prepared(value, options={**OPTIONS, "effort": "low"})
    result, ledger = run(monkeypatch, value, batches, [tool_response(), tool_response()])
    assert result[0] == "failed" and len(ledger.calls) == 2 and len(ledger.appended) == 1
    assert result[1]["errors"][0]["message"] == "source_round_limit"


def test_denied_context_is_reported_without_source_and_has_no_coverage(monkeypatch):
    install_source_reads(monkeypatch)
    result, sources = engine.read_sources(tool_response("AGENTS.md").tool_calls, repo=None, env={}, plan=plan(),
        options=OPTIONS, credentials=dict(CREDENTIALS), authorize=lambda: None, stopped=lambda: False)
    assert not sources and result["selections"][0]["reason"] == "excluded"
    assert result["selections"][0]["source_hash"] is None


@pytest.mark.parametrize("arguments", [{"path": "../bad", "start_line": 1, "end_line": 2},
    {"path": "helper.py", "start_line": 0, "end_line": 500},
    {"path": "helper.py", "start_line": True, "end_line": 2}])
def test_source_tool_rejects_unsafe_paths_and_unbounded_ranges(monkeypatch, arguments):
    install_source_reads(monkeypatch)
    with pytest.raises(ValueError):
        engine.read_sources([ToolCall(id="call", name="read_source", arguments=arguments)], repo=None, env={},
            plan=plan(), options=OPTIONS, credentials=dict(CREDENTIALS), authorize=lambda: None, stopped=lambda: False)


@pytest.mark.parametrize("case,code,field", [
    ("name", "source_tool", ["tool_calls", 0, "name"]),
    ("arguments", "source_arguments", ["tool_calls", 0, "arguments"]),
    ("path_type", "source_path", ["tool_calls", 0, "arguments", "path"]),
    ("path", "source_path", ["tool_calls", 0, "arguments", "path"]),
    ("range", "source_range", ["tool_calls", 0, "arguments"]),
    ("count", "source_call_limit", ["tool_calls"]),
])
def test_invalid_tool_requests_provide_actionable_bounded_repair_context(monkeypatch, case, code, field):
    install_source_reads(monkeypatch)
    value, batches = plan(), prepared()
    output = tool_response()
    call = output.tool_calls[0]
    if case == "name":
        call.name = "fixture-key PRIVATE_TOOL_MARKER"
    elif case == "arguments":
        call.arguments["fixture-key PRIVATE_TOOL_MARKER"] = "PRIVATE_TOOL_MARKER"
    elif case == "path_type":
        call.arguments["path"] = ["fixture-key PRIVATE_TOOL_MARKER"]
    elif case == "path":
        call.arguments["path"] = "../fixture-key PRIVATE_TOOL_MARKER"
    elif case == "range":
        call.arguments["start_line"] = 0
    else:
        output.tool_calls *= 11
    result, ledger = run(monkeypatch, value, batches, [output, response(final(batches[0]))])
    diagnostics = ledger.rows[batches[0].key]["result"]["validation"]
    assert diagnostics["issues"][0]["code"] == code and diagnostics["issues"][0]["field"] == field
    assert result[0] == "completed" and len(ledger.calls) == 2
    assert json.loads(ledger.calls[1].messages[1].content)["validation_diagnostics"] == diagnostics
    assert "PRIVATE_TOOL_MARKER" not in json.dumps([ledger.rows, ledger.calls[1].model_dump()])


def test_revocation_during_tool_validation_is_not_accepted_as_repair_feedback(monkeypatch):
    install_source_reads(monkeypatch)
    value, batches = plan(), prepared()
    ledger = Ledger(batches, [tool_response()])
    monkeypatch.setattr(llm, "execute_task", ledger.execute)

    def revoked():
        raise ValueError("fixture-key PRIVATE_AUTH_MARKER")

    with pytest.raises(PermissionError, match="authorization"):
        engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
            authorize=revoked, register_task=ledger.register, load_task=ledger.rows.get)
    assert ledger.rows[batches[0].key]["result"] is None and not ledger.appended


def test_resumed_expansion_refuses_changed_blob(monkeypatch):
    install_source_reads(monkeypatch)
    batch, value = prepared()[0], plan()
    result, _sources = engine.read_sources(tool_response().tool_calls, repo=None, env={}, plan=value,
        options=OPTIONS, credentials=dict(CREDENTIALS), authorize=lambda: None, stopped=lambda: False)
    result["selections"][0]["source_hash"] = "0" * 64
    with pytest.raises(ValueError, match="reproduces"):
        engine._restore_sources(result, repo=None, env={}, plan=value, batch=batch,
            credentials=dict(CREDENTIALS), authorize=lambda: None, stopped=lambda: False)


def test_bounded_workers_share_pool_and_parallelize_without_exceeding_limit(monkeypatch):
    value = plan([unit(f"file{index}.py", key=f"u{index}") for index in range(6)])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value, options={**OPTIONS, "concurrency": 2})
    ledger = Ledger(batches, [])
    active, peak, clients = 0, 0, set()

    async def execute(identity, _job, _attempt, _request, _credentials, *, client, validate_result, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        clients.add(id(client))
        try:
            await asyncio.sleep(0.01)
            batch = next(batch for batch in batches if batch.key == identity)
            result = response(final(batch))
            ledger.rows[identity].update(status="completed", result=validate_result(result), usage_tokens=12)
            return result
        finally:
            active -= 1

    monkeypatch.setattr(llm, "execute_task", execute)
    result = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get)
    assert result[0] == "completed" and peak == 2 and active == 0 and len(clients) == 1


def test_worker_failure_cancels_and_drains_inflight_tasks(monkeypatch):
    value = plan([unit("one.py", key="one"), unit("two.py", key="two")])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value, options={**OPTIONS, "concurrency": 2})
    ledger = Ledger(batches, [])
    active, drained = set(), []

    async def execute(identity, *_args, **_kwargs):
        active.add(identity)
        try:
            await asyncio.sleep(0.01)
            if identity == batches[0].key:
                raise ReviewStopped("cancelled")
            await asyncio.sleep(30)
        finally:
            active.remove(identity)
            drained.append(identity)

    monkeypatch.setattr(llm, "execute_task", execute)
    with pytest.raises(ReviewStopped):
        engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
            authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get)
    assert not active and len(drained) == 2


def test_budget_exhaustion_is_failed_coverage_without_capacity_retry(monkeypatch):
    value, batches = plan(), prepared()
    ledger = Ledger(batches, [None])
    ledger.rows[batches[0].key]["pending_reason"] = "budget"
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "failed" and len(ledger.calls) == 1
    assert result[1]["summary"]["budget_exceeded"] is True


def test_capacity_wait_retries_same_immutable_task_then_completes(monkeypatch):
    value, batches = plan(), prepared()
    ledger = Ledger(batches, [None, response(final(batches[0]))])
    ledger.rows[batches[0].key]["pending_reason"] = "waiting_capacity"
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "completed" and len(ledger.calls) == 2 and not ledger.appended
    assert llm.input_fingerprint(ledger.calls[0]) == llm.input_fingerprint(ledger.calls[1])


def test_partial_file_retains_validated_candidates_from_completed_units(monkeypatch):
    one, two = unit(), unit(key="u2", start=2)
    value = plan([one, two])
    value.batches = [[one], [two]]
    batches = prepared(value)
    result, _ledger = run(monkeypatch, value, batches, [response(final(batches[0], [finding(batches[0])])),
        InferenceError("refusal", "refused", usage=Usage(total_tokens=4))])
    assert result[0] == "partial" and not result[1]["manifest"]["coverage"]["completed"]
    assert len(result[2]) == 1 and result[2][0]["path"] == "app.py"


def test_unknown_but_valid_final_result_completes_only_its_assigned_work(monkeypatch):
    value, batches = plan(), prepared()
    result, ledger = run(monkeypatch, value, batches, [response(final(batches[0]), tokens=None)])
    assert result[0] == "completed" and result[1]["summary"]["total_tokens"] is None
    assert len(ledger.calls) == 1 and not ledger.appended


def test_failed_saved_task_keeps_consumption_without_retry(monkeypatch):
    value, batches = plan(), prepared()
    ledger = Ledger(batches, [])
    ledger.rows[batches[0].key].update(status="failed", usage_tokens=17)
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "failed" and result[1]["summary"]["total_tokens"] == 17
    assert not ledger.calls


def test_source_authorization_failure_cannot_be_normalized_into_schema_feedback(monkeypatch):
    install_source_reads(monkeypatch)

    def revoked():
        raise ValueError("The exact selected connection changed")

    with pytest.raises(PermissionError, match="authorization"):
        engine.read_sources(tool_response().tool_calls, repo=None, env={}, plan=plan(), options=OPTIONS,
            credentials=dict(CREDENTIALS), authorize=revoked, stopped=lambda: False)


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("source_code_policy", [None, engine.SOURCE_CODE_POLICY])
def test_gateway_plan_admission_and_validated_coverage(seed, monkeypatch, repair, source_code_policy):
    import httpx
    from sqlalchemy import select
    from test_analysis_foundation import planned as plan_gateway
    from test_llm_http import result as wire_result

    from code_mortis.core import analysis
    from code_mortis.core.db import transaction
    from code_mortis.integrations import llm_http
    from code_mortis.models import AnalysisAttempt, AnalysisTask, InferenceAdmission
    from code_mortis.reviews.service import connection_snapshot

    value = plan()
    snapshot = connection_snapshot(seed["connection"])
    batches = prepared(value, connection=snapshot, source_code_policy=source_code_policy)
    batch = batches[0]
    plan_id, identities = plan_gateway(seed, connection=snapshot,
        revisions={"base": value.base_sha, "head": value.head_sha, "merge_base": value.merge_base},
        tasks=[batch.spec.model_dump(mode="json")], budgets={"analysis": 100_000},
        settings={"concurrency": 1, "max_evidence_rounds": 2})
    wire, checks = [], []

    def handler(message):
        wire.append(json.loads(message.content))
        output = final(batch, [finding(batch)])
        if repair and len(wire) == 1:
            if source_code_policy:
                output["findings"][0]["evidence"][0]["source_id"] = "fixture-key PRIVATE_WIRE_MARKER"
            else:
                output["findings"][0]["existing_code"] = "fixture-key PRIVATE_WIRE_MARKER"
        return httpx.Response(200, json=wire_result("openai", content=json.dumps(output)))

    def load(key):
        with transaction() as db:
            task = db.get(AnalysisTask, identities[key])
            attempt = db.scalar(select(AnalysisAttempt).where(AnalysisAttempt.task_id == task.id,
                                                             AnalysisAttempt.number == task.attempts))
            return {"id": task.id, "status": task.status, "result": task.result,
                    "input_hash": task.input_hash, "usage_tokens": attempt.usage_tokens if attempt else None}

    def register(spec, request, *, parent_key):
        assert spec.input_hash == llm.input_fingerprint(request)
        with transaction() as db:
            task = analysis.append_task(db, "analysis-job", 1, identities[parent_key], spec)
            identities[spec.key] = task.id
            return task.id

    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    result = engine.review(None, {}, value, batches, job_id="analysis-job", job_attempt=1,
        credentials=dict(CREDENTIALS), authorize=lambda: checks.append(True),
        register_task=register, load_task=load)
    calls = 2 if repair else 1
    assert result[0] == "completed" and result[1]["summary"]["total_tokens"] == 12 * calls
    assert len(result[2]) == 1 and result[2][0]["evidence"][0]["quote"] == "value = 1"
    assert len(wire) == calls and wire[0]["model"] == snapshot["model"] and len(checks) == 3 * calls
    with transaction() as db:
        task = db.get(AnalysisTask, identities[batch.key])
        assert task.plan_id == plan_id and task.result["kind"] == ("feedback" if repair else "final") and task.status == "completed"
        if repair:
            assert task.result["validation"]["issues"][0]["code"] == (
                "unavailable_citation" if source_code_policy else "existing_code_mismatch")
            assert "PRIVATE_WIRE_MARKER" not in json.dumps(task.result)
            children = [child for child in db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id))
                        if child.dependencies == [task.id]]
            assert len(children) == 1 and children[0].result["kind"] == "final"
        ticket = db.scalar(select(InferenceAdmission).where(InferenceAdmission.task_id == task.id))
        assert ticket.status == "released"
    replay = engine.review(None, {}, value, batches, job_id="analysis-job", job_attempt=1,
        credentials=dict(CREDENTIALS), authorize=lambda: None, register_task=register, load_task=load)
    assert replay == result and len(wire) == calls


def test_file_limit_preserves_existing_manifest_waiver_contract_and_no_dispatch(monkeypatch):
    value = plan()
    value.limit_reason = "1 reviewable files exceeds the maximum of 0."
    batches = prepared(value)
    result, ledger = run(monkeypatch, value, batches, [])
    status, report, comments = result
    assert status == "skipped" and not ledger.calls and comments == []
    assert report["manifest"]["schema_version"] == "code-mortis.file-limit/v1"
    coverage = report["manifest"]["coverage"]
    assert coverage["failed"] == [] and coverage["completed"] == [] and coverage["reused"] == []
    assert coverage["waived"] == [{**coverage["selected"][0], "reason": value.limit_reason}]
    assert report["summary"]["total_tokens"] == 0


def test_transport_session_is_scoped_to_durable_job_without_changing_frozen_input(monkeypatch):
    value, batches = plan(), prepared()
    result, ledger = run(monkeypatch, value, batches, [response(final(batches[0]))])
    assert result[0] == "completed"
    assert ledger.calls[0].session_id != batches[0].request.session_id
    assert llm.input_fingerprint(ledger.calls[0]) == batches[0].spec.input_hash


def test_gateway_timeout_keeps_completed_candidates_and_known_consumption(monkeypatch):
    value = plan([unit("one.py", key="one"), unit("two.py", key="two")])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value)
    ledger = Ledger(batches, [response(final(batches[0], [finding(batches[0])])), TimeoutError("deadline")])
    original = ledger.execute

    async def timed(identity, *args, **kwargs):
        try:
            return await original(identity, *args, **kwargs)
        except TimeoutError:
            ledger.rows[identity].update(status="failed", usage_tokens=7)
            raise

    ledger.execute = timed
    result, _ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "partial" and len(result[2]) == 1
    assert result[1]["errors"] == [{"path": "two.py", "message": "deadline"}]
    assert result[1]["summary"]["total_tokens"] == 19


def test_authorization_timeout_before_dispatch_preserves_other_completed_work(monkeypatch):
    value = plan([unit("one.py", key="one"), unit("two.py", key="two")])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value)
    ledger = Ledger(batches, [response(final(batches[0], [finding(batches[0])]))])
    original = ledger.execute

    async def execute(identity, *args, authorize, **kwargs):
        if identity == batches[1].key:
            ledger.rows[identity]["usage_tokens"] = 0
            authorize()
        return await original(identity, *args, authorize=authorize, **kwargs)

    monkeypatch.setattr(llm, "execute_task", execute)
    result = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=lambda: (_ for _ in ()).throw(TimeoutError("expired")), register_task=ledger.register,
        load_task=ledger.rows.get)
    assert result[0] == "partial" and len(result[2]) == 1
    assert result[1]["summary"]["total_tokens"] == 12 and len(ledger.calls) == 1


def test_registration_timeout_keeps_feedback_usage_and_peer_candidates(monkeypatch):
    value = plan([unit("one.py", key="one"), unit("two.py", key="two")])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value)
    invalid = final(batches[1]) | {"completed_units": []}
    ledger = Ledger(batches, [response(final(batches[0], [finding(batches[0])])), response(invalid)])

    def register(*_args, **_kwargs):
        raise TimeoutError("Evidence admission deadline expired")

    ledger.register = register
    result, _ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "partial" and len(result[2]) == 1
    assert result[1]["summary"]["total_tokens"] == 24 and not ledger.appended
    assert result[1]["errors"] == [{"path": "two.py", "message": "deadline"}]


def test_concurrent_deadlines_fail_each_remaining_batch_and_drain(monkeypatch):
    value = plan([unit(f"file{index}.py", key=f"u{index}") for index in range(4)])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value, options={**OPTIONS, "concurrency": 2})
    ledger = Ledger(batches, [])
    active, finished = set(), []

    async def execute(identity, *_args, validate_result, **_kwargs):
        active.add(identity)
        try:
            if identity == batches[0].key:
                output = response(final(batches[0], [finding(batches[0])]))
                ledger.rows[identity].update(status="completed", result=validate_result(output), usage_tokens=12)
                return output
            await asyncio.sleep(0.01)
            ledger.rows[identity].update(status="failed", usage_tokens=0)
            raise TimeoutError("Shared deadline expired")
        finally:
            active.remove(identity)
            finished.append(identity)

    monkeypatch.setattr(llm, "execute_task", execute)
    result = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get)
    assert result[0] == "partial" and len(result[2]) == 1 and not active and len(finished) == 4
    assert len(result[1]["manifest"]["coverage"]["failed"]) == 3
    assert result[1]["summary"]["total_tokens"] == 12


@pytest.mark.parametrize("code", ["cancelled", "stale_attempt"])
def test_gateway_deadline_codes_preserve_partial_while_real_cancellation_still_aborts(monkeypatch, code):
    value = plan([unit("one.py", key="one"), unit("two.py", key="two")])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value)
    ledger = Ledger(batches, [response(final(batches[0], [finding(batches[0])])),
                             InferenceError(code, "expired", usage=Usage(total_tokens=4))])
    ledger.rows[batches[1].key]["pending_reason"] = "deadline"
    result, _ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "partial" and len(result[2]) == 1
    assert result[1]["summary"]["total_tokens"] == 16
    value, batches = plan(), prepared()
    with pytest.raises(ReviewStopped):
        run(monkeypatch, value, batches, [InferenceError(code, "cancelled", usage=Usage(total_tokens=4))])


@pytest.mark.parametrize("failure", [False, True])
def test_usage_includes_every_known_prior_attempt_not_only_latest_response(monkeypatch, failure):
    value, batches = plan(), prepared()
    output = (InferenceError("refusal", "refused", usage=Usage(total_tokens=12)) if failure
              else response(final(batches[0])))
    ledger = Ledger(batches, [output])
    original = ledger.execute

    async def accumulated(identity, *args, **kwargs):
        try:
            result = await original(identity, *args, **kwargs)
        except InferenceError:
            ledger.rows[identity].update(status="failed", usage_tokens=29)
            raise
        ledger.rows[identity]["usage_tokens"] = 29
        return result

    ledger.execute = accumulated
    result, _ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == ("failed" if failure else "completed")
    assert result[1]["summary"]["total_tokens"] == 29


def test_capacity_poll_uses_separate_local_admission_callback(monkeypatch):
    value, batches = plan(), prepared()
    ledger = Ledger(batches, [response(final(batches[0]))])
    original = ledger.execute
    callbacks = []

    def admission():
        pass

    def fresh_authorization():
        pass

    async def execute(identity, *args, authorize, authorize_admission, **kwargs):
        callbacks.append((authorize, authorize_admission))
        return await original(identity, *args, authorize=authorize, **kwargs)

    monkeypatch.setattr(llm, "execute_task", execute)
    result = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=fresh_authorization, check_admission=admission, register_task=ledger.register, load_task=ledger.rows.get)
    assert result[0] == "completed" and callbacks == [(fresh_authorization, admission)]


def test_unavailable_optional_graph_replays_trusted_final_and_accounts_entire_chain(monkeypatch):
    value, batches = plan(), prepared()
    batch = batches[0]
    replay = engine.validate_result(final(batch, [finding(batch)]), batch, batch.sources, dict(CREDENTIALS))
    batch.error = "frozen_context_unavailable"
    ledger = Ledger(batches, [])
    ledger.rows[batch.key].update(replay_result=replay, replay_usage=37, input_hash="frozen-original-hash")
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "completed" and len(result[2]) == 1
    assert result[1]["summary"]["total_tokens"] == 37 and not ledger.calls and not ledger.appended


def test_unavailable_optional_graph_without_final_retains_usage_and_peer_replay(monkeypatch):
    value = plan([unit("one.py", key="one"), unit("two.py", key="two")])
    value.batches = [[unit] for unit in value.units]
    batches = prepared(value)
    replay = engine.validate_result(final(batches[0], [finding(batches[0])]), batches[0],
                                   batches[0].sources, dict(CREDENTIALS))
    for batch in batches:
        batch.error = "frozen_context_unavailable"
    ledger = Ledger(batches, [])
    ledger.rows[batches[0].key].update(replay_result=replay, replay_usage=37)
    ledger.rows[batches[1].key].update(replay_result=None, replay_usage=13)
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "partial" and len(result[2]) == 1 and not ledger.calls and not ledger.appended
    assert result[1]["summary"]["total_tokens"] == 50
    assert result[1]["errors"] == [{"path": "two.py", "message": "frozen_context_unavailable"}]


def test_unavailable_graph_replay_still_requires_exact_units_and_locations(monkeypatch):
    value, batches = plan(), prepared()
    batch = batches[0]
    replay = engine.validate_result(final(batch, [finding(batch)]), batch, batch.sources, dict(CREDENTIALS))
    replay["comments"][0]["path"] = "other.py"
    batch.error = "frozen_context_unavailable"
    ledger = Ledger(batches, [])
    ledger.rows[batch.key].update(replay_result=replay, replay_usage=37)
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "failed" and result[2] == [] and not ledger.calls
    assert result[1]["summary"]["total_tokens"] == 37


def test_unavailable_graph_replay_keeps_unknown_chain_consumption_unknown(monkeypatch):
    value, batches = plan(), prepared()
    batch = batches[0]
    replay = engine.validate_result(final(batch), batch, batch.sources, dict(CREDENTIALS))
    batch.error = "frozen_context_unavailable"
    ledger = Ledger(batches, [])
    ledger.rows[batch.key].update(replay_result=replay, replay_usage=None)
    result, ledger = run(monkeypatch, value, batches, [], ledger=ledger)
    assert result[0] == "completed" and result[1]["summary"]["total_tokens"] is None and not ledger.calls


def test_wire_citation_sources_are_metadata_while_exact_redacted_text_stays_local():
    assigned_marker = "ASSIGNED_SOURCE_UNIQUE_MARKER"
    graph_marker = "GRAPH_SOURCE_UNIQUE_MARKER"
    value = plan([unit(source=assigned_marker + "\n")])
    value.units[0].patch = "@@ -1 +1 @@\n-old\n+new\n"
    graph = {"app.py": {"commit_sha": "b" * 40,
                         "sources": [{"path": "helper.py", "text": graph_marker + "\n"}]}}
    batch = prepared(value, graph=graph)[0]
    wire = batch.request.messages[0].content
    payload = json.loads(wire)
    assert wire.count(assigned_marker) == 1 and wire.count(graph_marker) == 1
    assert all("text" not in source for source in payload["citation_sources"])
    assert {source["text"].strip() for source in batch.sources} == {assigned_marker, graph_marker}
    assert {source["source_id"] for source in payload["citation_sources"]} == {source["source_id"] for source in batch.sources}


def test_prompt_ceiling_subdivides_multiunit_batches_deterministically_without_losing_source():
    units = [unit(f"file{index}.py", key=f"u{index}", source="x" * 2400 + "\n") for index in range(4)]
    for value in units:
        value.patch = "@@ -1 +1 @@\n-old\n+new\n"
    value = plan(units)
    connection = {**CONNECTION, "options": {"prompt_token_ceiling": 12_000}}
    batches, again = prepared(value, connection=connection), prepared(value, connection=connection)
    assert len(batches) > 1 and all(batch.error is None for batch in batches)
    assert [batch.spec for batch in batches] == [batch.spec for batch in again]
    assert [unit.key for batch in batches for unit in batch.units] == [unit.key for unit in units]
    assert "".join(source["text"] for batch in batches for source in batch.sources) == "".join(unit.source for unit in units)
    assert all(llm.reservation(connection, batch.request) <= 12_000 for batch in batches)


def test_prompt_subdivision_keeps_indivisible_failure_and_other_units_dispatchable():
    oversized = unit("huge.py", key="huge", source="x" * 30_000)
    regular = unit("small.py", key="small")
    value = plan([oversized, regular])
    batches = prepared(value, connection={**CONNECTION, "options": {"prompt_token_ceiling": 12_000}})
    assert len(batches) == 2
    assert batches[0].units == [oversized] and batches[0].error == "required_context_limit"
    assert batches[1].units == [regular] and batches[1].error is None


def test_optional_graph_is_omitted_before_subdividing_required_units():
    units = [unit("one.py", key="one"), unit("two.py", key="two")]
    value = plan(units)
    graph = {"one.py": {"commit_sha": "b" * 40,
                          "sources": [{"path": "helper.py", "text": "x" * 30_000}]}}
    batches = prepared(value, graph=graph, connection={**CONNECTION, "options": {"prompt_token_ceiling": 12_000}})
    assert len(batches) == 1 and batches[0].error is None and batches[0].units == units
    assert batches[0].graph_omitted == ["one.py"]


@pytest.mark.parametrize(("effort", "limit"), [("low", 1), ("medium", 2), ("high", 4)])
@pytest.mark.parametrize("complete", [False, True])
def test_final_evidence_request_withholds_unavailable_tools_without_faking_coverage(monkeypatch, complete, effort, limit):
    install_source_reads(monkeypatch)
    value = plan()
    batches = prepared(value, options={**OPTIONS, "effort": effort})
    terminal = final(batches[0])
    if not complete:
        terminal["completed_units"] = []
    responses = [tool_response() for _ in range(limit)] + [response(terminal)]
    result, ledger = run(monkeypatch, value, batches, responses)
    assert [bool(request.tools) for request in ledger.calls] == [True] * limit + [False]
    assert len(ledger.calls) == limit + 1 and len(ledger.appended) == limit
    assert all(request.effort == effort and request.max_output_tokens == 16384 for request in ledger.calls)
    supplied = json.loads(ledger.calls[-1].messages[-1].content)
    assert len(supplied["supporting_sources"]) == 1
    assert [json.loads(request.messages[-1].content)["remaining_source_rounds"]
            for request in ledger.calls] == list(range(limit, -1, -1))
    assert ledger.calls[-1].messages[0] == batches[0].request.messages[0]
    coverage = result[1]["manifest"]["coverage"]
    assert result[0] == ("completed" if complete else "failed")
    assert len(coverage["completed"]) == int(complete)
    assert len(coverage["failed"]) == int(not complete)
    assert result[1]["summary"]["total_tokens"] == 10 * limit + 12


def test_legacy_source_tool_policy_replays_unchanged_request_hashes(monkeypatch):
    install_source_reads(monkeypatch)
    value = plan()
    batches = engine.prepare_batches(value, CONNECTION, OPTIONS, CREDENTIALS, output_token_policy=None,
                                      prompt_version="mortis-pr-prompt/v1", source_tool_policy=None)
    responses = [tool_response(), tool_response(), response(final(batches[0]))]
    ledger = Ledger(batches, responses)
    monkeypatch.setattr(llm, "execute_task", ledger.execute)
    monkeypatch.setattr(engine.inference_admission, "cancel_task_waiter", lambda *_args: None)
    first = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get, limit_source_tools=False)
    replay = engine.review(None, {}, value, batches, job_id="job", job_attempt=1, credentials=dict(CREDENTIALS),
        authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get, limit_source_tools=False)
    assert first == replay and first[0] == "completed"
    assert len(ledger.calls) == 3 and all(request.tools and request.max_output_tokens == 8192 for request in ledger.calls)
    payload = json.loads(ledger.calls[-1].messages[-1].content)
    assert len(payload["supporting_sources"]) == 2 and "remaining_source_rounds" not in payload


def test_followup_omits_repeated_source_but_preserves_missing_ranges_and_read_results():
    value = plan([unit(start=1, source="one\ntwo\n"), unit(key="u2", start=3, source="three\nfour\n")])
    batch = prepared(value)[0]
    supplied = engine._source(value.head_sha, "app.py", 2, 3, "two\nthree")
    extension = engine._source(value.head_sha, "app.py", 4, 5, "four\nfive")
    other = engine._source(value.head_sha, "other.py", 2, 3, "two\nthree")
    notices = [{"path": "app.py", "start_line": 2, "end_line": 3, "reason": "available"}]
    sources = [supplied, extension, extension, other]
    request = engine._next_request(batch, sources, notices, remaining_source_rounds=0, allow_source_reads=False)
    payload = json.loads(request.messages[-1].content)
    assert payload["supporting_sources"] == [engine._source(value.head_sha, "app.py", 5, 5, "five"), other]
    assert payload["source_read_results"] == notices and payload["remaining_source_rounds"] == 0
    assert sources == [supplied, extension, extension, other] and not request.tools
    assert request.messages[0] == batch.request.messages[0]


@pytest.mark.parametrize("start,end,expected", [(1, 7, [(1, 1), (4, 4), (7, 7)]),
    (1, 2, [(1, 1)]), (3, 4, [(4, 4)]), (2, 3, []), (7, 7, [(7, 7)])])
def test_followup_sends_only_unsupplied_line_ranges(start, end, expected):
    value = plan([unit(start=2, source="two\nthree\n"), unit(key="u2", start=5, source="five\nsix\n")])
    batch = prepared(value)[0]
    lines = ["one", "two", "three", "four", "five", "six", "seven"]
    source = engine._source(value.head_sha, "app.py", start, end, "\n".join(lines[start - 1:end]))
    request = engine._next_request(batch, [source, source], [], remaining_source_rounds=0)
    assert json.loads(request.messages[-1].content)["supporting_sources"] == [
        engine._source(value.head_sha, "app.py", first, last, "\n".join(lines[first - 1:last]))
        for first, last in expected
    ]
    assert source["start_line"] == start and source["end_line"] == end
    assert request.messages[0] == batch.request.messages[0]


@pytest.mark.parametrize("policy", ["split-units/v1", analysis.OUTPUT_RECOVERY_POLICY])
def test_output_recovery_narrows_three_units_without_losing_source_diff_rules_or_citations(policy):
    value = plan([unit(key=f"u{i}", start=i, source=f"value = {i}\n") for i in range(1, 4)])
    batch = prepared(value)[0]
    original = json.loads(batch.request.messages[0].content)
    children = engine._recovery_batches(batch, batch.request, batch.sources, batch.key, recovery_policy=policy)
    assert len(children) == 3 and [child.units[0] for child in children] == value.units
    for child in children:
        payload = json.loads(child.request.messages[0].content)
        assert len(payload["assigned_units"]) == 1 and len(payload["context_units"]) == 2
        assert sorted(payload["assigned_units"] + payload["context_units"], key=lambda item: item["key"]) == original["assigned_units"]
        assert payload["citation_sources"] == original["citation_sources"] and child.sources == batch.sources
        assert child.request.max_output_tokens == (16384 if policy == "split-units/v1" else 32768)
        assert child.request.effort == batch.request.effort and not child.request.tools
        assert engine.validate_result(final(child), child, child.sources, CREDENTIALS)["completed_units"] == [child.units[0].key]
        with pytest.raises(ValueError, match="assigned"):
            engine.validate_result(final(child) | {"completed_units": [unit.key for unit in value.units]},
                                   child, child.sources, CREDENTIALS)
