"""Cross-stage regressions for the model-only review contract."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from code_mortis.reviews import assessment, verification
from code_mortis.reviews import context as review_context


@pytest.mark.parametrize("stage", ["assessment", "verification"])
def test_repeated_empty_source_produces_correctable_feedback(stage):
    context = {"sources": {"current": {"empty.py": ""}}}
    with pytest.raises(verification.ResultValidationError) as caught:
        if stage == "assessment":
            assessment.expand_context(Path("/tmp"), {}, SimpleNamespace(), context, ["empty.py"], lambda: False)
        else:
            verification.expand_context(Path("/tmp"), {}, context,
                [{"revision": "current", "path": "empty.py"}], {}, lambda: False)
    assert caught.value.feedback["kind"] == "evidence"
    assert "empty" in caught.value.feedback["reason"]


@pytest.mark.parametrize("kind,expected", [("citation_selection", True), ("delivery", True),
    ("evidence", True), ("context_limit", False), ("redaction", False)])
def test_only_correctable_feedback_retries(kind, expected):
    assert review_context.correctable({"kind": kind}) is expected


@pytest.mark.parametrize("credential", ["synthetic-credential", "雪🔑\nsecond line", 'quoted"\\value'])
def test_sanitization_covers_history_feedback_and_preserves_line_mapping(credential):
    import copy
    import json

    context = {"sources": {"current": {"app.py": f"first\n{credential}\nlast"}},
               "history": [{"explanation": credential}], "validation_feedback": {"reason": credential},
               "diff": credential, "finding": {"content": credential, "suggestion_code": credential}}
    original = copy.deepcopy(context)
    clean = review_context.sanitize(context, {"api_key": credential})
    assert credential not in json.dumps(clean, ensure_ascii=False)
    assert credential not in clean["sources"]["current"]["app.py"]
    assert clean["sources"]["current"]["app.py"].count("\n") == context["sources"]["current"]["app.py"].count("\n")
    assert original == context
    assert review_context.sanitize(clean, {"api_key": credential}) == clean


def test_sanitization_withholds_structural_identifiers():
    with pytest.raises(verification.VerificationOutputError) as caught:
        review_context.sanitize({"sources": {"current": {"synthetic-credential.py": "safe"}}}, {"api_key": "synthetic-credential"})
    assert caught.value.usage == {"tokens": 0} and caught.value.feedback["kind"] == "redaction"


def test_selection_contract_keeps_exact_quotes_and_maps_only_history_ids():
    context = {"sources": {"original": {"app.py": '雪\r\nprint("a")\r\n'},
                           "current": {"app.py": '雪\nprint("b")\n'}},
               "history": [{"id": "stored-uuid", "status": "dismissed"}], "prior_candidates": [{"id": "candidate"}]}
    supplied, schema, ids = review_context.selection_context(context, assessment.output_schema(context))
    assert supplied["history"][0]["id"] == "history_1" and context["history"][0]["id"] == "stored-uuid"
    assert schema["properties"]["finding_id"]["anyOf"][0]["enum"] == ["history_1"]
    result = review_context.selected_result({"finding_id": "history_1", "evidence": [
        {"source_id": review_context.source_id("original", "app.py"), "start_line": 1, "end_line": 2}]}, supplied, ids)
    assert result["finding_id"] == "stored-uuid" and result["evidence"][0]["quote"] == '雪\r\nprint("a")\r\n'
    for invalid in ("stored-uuid", "candidate", "history_2"):
        with pytest.raises(verification.ResultValidationError):
            review_context.selected_result({"finding_id": invalid, "evidence": []}, supplied, ids)


@pytest.mark.parametrize("selection", [
    {"source_id": "missing", "start_line": 1, "end_line": 1},
    {"source_id": review_context.source_id("current", "app.py"), "start_line": 0, "end_line": 1},
    {"source_id": review_context.source_id("current", "app.py"), "start_line": 2, "end_line": 1},
    {"source_id": review_context.source_id("current", "app.py"), "start_line": 1, "end_line": 3},
    {"source_id": review_context.source_id("current", "app.py"), "start_line": 1, "end_line": 1, "quote": "invented"},
])
def test_source_selection_rejects_unavailable_ranges_and_model_quotes(selection):
    context, _, ids = review_context.selection_context({"sources": {"current": {"app.py": "safe"}}}, verification.output_schema())
    with pytest.raises(verification.ResultValidationError):
        review_context.selected_result({"evidence": [selection]}, context, ids)


def test_unavailable_history_commit_does_not_abort_unrelated_current_assessment(monkeypatch):
    calls, renamed = [], {}
    def git(*args, **kwargs):
        calls.append(args)
        raise ValueError("historical commit unavailable")
    monkeypatch.setattr(assessment.ocr, "git", git)
    previous = {"findings": [{"id": "old", "snapshot": {"path": "unrelated.py", "existing_code": "old"},
                 "sha": "a" * 40, "fingerprint": "old"}], "prior_candidates": []}
    for _ in range(2):
        assert assessment.related_history(Path("/tmp"), {}, {}, "b" * 40,
            {"path": "current.py", "existing_code": "current", "content": "different defect"}, previous, lambda: False, renamed) == ([], [])
    assert len(calls) == 2
    assert assessment.related_history(Path("/tmp"), {}, {}, "b" * 40,
        {"path": "current.py", "existing_code": "old", "content": "same defect"}, previous, lambda: False, renamed)[0][0]["id"] == "old"


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
def test_native_transport_uses_selection_schema_and_returns_normalized_evidence(monkeypatch, kind):
    from code_mortis.integrations import runner_client

    context = {"sources": {"current": {"app.py": "safe\n"}}, "diff": ""}
    sent = []
    def request(method, path, body=None, timeout=35):
        if method == "POST":
            sent.append(body)
        return {"status": "completed", "usage": {"tokens": 7}, "result": {"evidence": [
            {"source_id": review_context.source_id("current", "app.py"), "start_line": 1, "end_line": 1}]}}
    monkeypatch.setattr(runner_client, "request", request)
    result, usage = runner_client.review("connection", "attempt", {
        "kind": kind, "_evidence_context": context, "_instructions": "Verify.",
        "prompt": "old", "output_schema": verification.output_schema()}, lambda: False)
    assert result["evidence"][0]["quote"] == "safe\n" and usage == {"tokens": 7}
    assert "_evidence_context" not in sent[0] and "_instructions" not in sent[0]
    assert review_context.source_id("current", "app.py") in sent[0]["prompt"]


def test_older_pinned_ocr_build_cannot_silently_skip_context_guard(monkeypatch):
    from code_mortis.integrations import ocr

    monkeypatch.setattr(ocr, "command", lambda *a, **k: (0, "v1.13.1 (8eb8334+cm-diagnostics)", ""))
    with pytest.raises(ValueError, match="context guard"):
        ocr.check_version(Path("/tmp"), {})
    monkeypatch.setattr(ocr, "command", lambda *a, **k: (0, "v1.13.1 (8eb8334+cm-context)", ""))
    with pytest.raises(ValueError, match="file policy"):
        ocr.check_version(Path("/tmp"), {})
    monkeypatch.setattr(ocr, "command", lambda *a, **k: (0, "v1.13.1 (8eb8334+cm-context-scope-v2)", ""))
    ocr.check_version(Path("/tmp"), {})


def test_historical_secret_matching_remains_in_worker_memory_and_keeps_usage():
    from code_mortis.security_scans import engine as security_engine

    credential = "arbitrary historical credential 雪"
    credentials = {"redaction_rules": [{"rule_id": "fixture", "match_bytes": len(credential.encode()),
        "identity": security_engine.secure_digest("fixture\0" + credential)}]}
    clean = review_context.sanitize({"history": [{"explanation": credential}]}, credentials)
    assert credential not in clean["history"][0]["explanation"]
    assert credentials["redaction_values"] == [credential]
    with pytest.raises(verification.VerificationOutputError) as caught:
        review_context.sanitize({"path": f"{credential}.py"}, credentials, usage={"tokens": 17})
    assert caught.value.usage == {"tokens": 17}


def test_source_references_remain_stable_after_evidence_expansion():
    context = {"sources": {"current": {"z.py": "source"}}}
    before, _, _ = review_context.selection_context(context, verification.output_schema())
    context["sources"]["current"]["a.py"] = "new source"
    after, _, _ = review_context.selection_context(context, verification.output_schema())
    reference = review_context.source_id("current", "z.py")
    assert before["citation_sources"][reference] == after["citation_sources"][reference]


def test_rejected_candidate_history_follows_renames_and_connects_secondary_citations(monkeypatch):
    def git(*args, **kwargs):
        return "R100\0old.py\0new.py\0" if "--name-status" in args else ""
    monkeypatch.setattr(assessment.ocr, "git", git)
    previous = {"findings": [], "prior_candidates": [{"id": "rejected", "snapshot": {
        "path": "old.py", "source_sha": "a" * 40, "existing_code": "previous"},
        "result": {"verdict": "rejected", "evidence": [{"path": "helper.py", "quote": "guard"}]}}]}
    for path in ("new.py", "helper.py"):
        findings, prior = assessment.related_history(Path("/tmp"), {}, {}, "b" * 40,
            {"path": path, "existing_code": "different", "content": "current defect"}, previous, lambda: False)
        assert findings == [] and [item["id"] for item in prior] == ["rejected"]


@pytest.mark.parametrize("stage", ["assessment", "verification"])
@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("rendered", [False, True])
def test_citations_accept_compatible_line_endings_without_changing_quotes(stage, ending, rendered):
    from test_assessment import verdict
    from test_review_flow import COMMENT
    from test_verification import RESULT

    source = ending.join(['first("雪")', 'second("literal")', 'third()']) + ending
    quote = '\n'.join(source.splitlines()[:2]) if rendered else ''.join(source.splitlines(keepends=True)[:2])
    context = {"head_sha": "b" * 40, "original_sha": "a" * 40, "finding": COMMENT,
               "history": [], "sources": {"current": {COMMENT["path"]: source}}, "diff": "changed"}
    evidence = [{"revision": "current", "path": COMMENT["path"], "start_line": 1, "end_line": 2, "quote": quote}]
    result = verdict(context, evidence=evidence) if stage == "assessment" else {**RESULT, "verdict": "uncertain", "evidence": evidence}
    validator = assessment.validate_result if stage == "assessment" else verification.validate_result
    assert validator(result, context)["evidence"][0]["quote"] == quote
    for changed in (quote.replace("literal", "different"), quote.replace("second", "SECOND")):
        with pytest.raises(verification.ResultValidationError):
            validator({**result, "evidence": [{**evidence[0], "quote": changed}]}, context)
