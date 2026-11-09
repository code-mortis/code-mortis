"""Security semantic failures receive one correction and preserve consumed usage."""

import pytest

from code_mortis.reviews import verification
from code_mortis.security_scans import engine as security_engine

CONTEXT = {"diff": "", "sources": {"current": {"app.py": "safe"}}}


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
@pytest.mark.parametrize("failure", ["path", "scope", "confirmation"])
@pytest.mark.parametrize("corrected", [True, False])
def test_semantic_validation_is_corrected_once_without_aborting_other_work(
    tmp_path, kind, failure, corrected,
):
    citation = {"source_id": "source_1", "start_line": 1, "end_line": 1}
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

    def infer(context, *, validator, **kwargs):
        try:
            return validator(response(), context), {"tokens": 7}
        except verification.ResultValidationError as exc:
            raise verification.VerificationOutputError("Invalid result", {"tokens": 7}, feedback=exc.feedback) from exc

    item = security_engine.Snapshot(tmp_path, "a" * 40, {}, lambda: False)
    item.inventory = {"app.py": {}, "other.py": {}}
    context = {"assigned_path": "app.py", "history": [], **CONTEXT}
    args = (item, {"id": "connection", "kind": kind, "model": "test"}, {}, "assessment" if failure == "confirmation" else "analysis",
            context, 1000, 60, lambda: False, "semantic-smoke")
    if kind != "api":
        with pytest.raises(ValueError, match="retired"):
            security_engine.ask(*args, infer=infer)
        assert not calls
        return
    if corrected:
        result, usage = security_engine.ask(*args, infer=infer)
        assert result["required_paths"] == [] and usage == {"tokens": 14}
    else:
        with pytest.raises(verification.VerificationOutputError) as caught:
            security_engine.ask(*args, infer=infer)
        assert caught.value.usage == {"tokens": 14}
        assert caught.value.feedback["kind"] == "schema"
    assert len(calls) == 2

