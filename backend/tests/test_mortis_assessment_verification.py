"""Mortis retained workflows use the gateway without the legacy OCR runtime."""

import copy
from pathlib import Path

import pytest
from sqlalchemy import select
from test_assessment import pipeline, verdict  # noqa: F401
from test_code_graph_workflow import fixture_git
from test_review_flow import COMMENT, new_run
from test_verification import CONTEXT, RESULT
from test_verification import scenario as verification_scenario  # noqa: F401

from code_mortis.core.db import now, transaction
from code_mortis.models import Finding, FindingVerification, Repository, ReviewCandidate, ReviewRun
from code_mortis.reviews import assessment, verification
from code_mortis.reviews import context as review_context
from code_mortis.reviews.verification import mortis_verify as real_mortis_verify
from code_mortis.schemas.common import RepoSettings


def fail_ocr(*args, **kwargs):
    raise AssertionError("Mortis must not execute OCR")


def test_mortis_selection_reconstructs_verbatim_evidence_and_history(monkeypatch):
    from code_mortis.core import workflow_inference

    context = {**copy.deepcopy(CONTEXT), "task_contract": review_context.TASK_CONTRACT,
               "history": [{"id": "established-id"}]}
    captured = {}

    def execute(connection, credentials, supplied, **kwargs):
        captured.update(kwargs)
        assert supplied["history"][0]["id"] == "history_1"
        raw = {**RESULT, "evidence": [
            {"source_id": source_id, "start_line": 1, "end_line": 1}
            for source_id in supplied["citation_sources"]
        ]}
        return kwargs["validator"](raw, supplied), {"tokens": 31}

    monkeypatch.setattr(verification, "mortis_verify", real_mortis_verify)
    monkeypatch.setattr(workflow_inference, "execute", execute)
    result, usage = verification.mortis_verify(
        {"kind": "api"}, {}, context, 100, 30, lambda: False,
        run_id="run", claim="job:1", stage="verification", task_key="finding", round=1,
    )
    assert result["verdict"] == "fixed"
    for item in result["evidence"]:
        assert item["quote"] == CONTEXT["sources"][item["revision"]][item["path"]].splitlines(keepends=True)[0]
    assert usage == {"tokens": 31}
    assert captured["task_key"] == "finding:1" and captured["stage"] == "verification"
    assert captured["token_budget"] == 100 and captured["timeout"] == 30


def test_mortis_context_uses_exact_git_policy_without_ocr(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    fixture_git(root, "init", "-q")
    fixture_git(root, "config", "user.name", "Fixture")
    fixture_git(root, "config", "user.email", "fixture@example.invalid")
    (root / "app.py").write_text("while True:\n    retry()\n")
    (root / "helper.py").write_text("def retry():\n    return False\n")
    (root / "guide.md").write_text("# Evidence\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "original")
    base = fixture_git(root, "rev-parse", "HEAD")
    (root / "app.py").write_text("for attempt in range(3):\n    retry()\n")
    fixture_git(root, "commit", "-qam", "bounded retry")
    head = fixture_git(root, "rev-parse", "HEAD")
    options = RepoSettings(analysis_engine="mortis", exclusions="helper.py").model_dump()
    context = verification.build_context(
        root, {}, {}, {"path": "app.py"}, base, head, options, lambda: False, base_sha=base,
    )
    assert context["base_sha"] == base
    assert context["sources"]["current"]["app.py"] == (root / "app.py").read_text()
    assert verification.filter_context(
        root, {}, {"guide.md": "", "helper.py": "", "AGENTS.md": ""}, options,
        lambda: False, base_sha=base, head_sha=head,
    ) == {"guide.md"}
    with pytest.raises(verification.ResultValidationError, match="excluded"):
        verification.expand_context(root, {}, context,
            [{"revision": "current", "path": "helper.py"}], options, lambda: False)
    assert "helper.py" not in context["sources"]["current"]


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("round_type", ["expand", "repair", "unknown"])
def test_mortis_assessment_preserves_evidence_expansion_and_identity(pipeline, monkeypatch, recovered, round_type):  # noqa: F811
    from code_mortis.core import workflow_inference

    run_id = new_run(assessed=True)
    claim = "assessment-owner"
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.engine, run.status, run.started_at = "mortis", "running", now()
        run.config = {**run.config, "analysis_engine": "mortis", "settings": {
            **run.config["settings"], "analysis_engine": "mortis"}}
        run.progress = {"execution": claim, "analysis_status": "completed"}
        run.report = {"summary": {"total_tokens": 50}}
        db.add(ReviewCandidate(id="candidate", run_id=run_id, position=0,
            snapshot={**COMMENT, "source_sha": run.head_sha},
            status="running" if recovered else "queued",
            usage={"tokens": None, "reserved_tokens": 1000} if recovered else {}))
        connection = run.config["connection"]

    calls = []

    def execute(selected, credentials, supplied, **kwargs):
        calls.append(kwargs["task_key"])
        if len(calls) == 1 and round_type != "expand":
            raise verification.VerificationOutputError("Correct the selected source.",
                {"tokens": None if round_type == "unknown" else 25},
                feedback={"kind": "citation_selection", "reason": "Select a supplied source."})
        raw = verdict(supplied, verdict="uncertain", required_paths=["helper.py"])
        if len(calls) == 2:
            if round_type == "expand":
                assert supplied["sources"]["current"]["helper.py"] == "def retry(): return False\n"
            else:
                assert supplied["validation_feedback"]["kind"] == "citation_selection"
            raw = verdict(supplied, required_paths=[])
        raw["evidence"] = [{"source_id": review_context.source_id("current", COMMENT["path"]),
                            "start_line": 7, "end_line": 7}]
        return kwargs["validator"](raw, supplied), {"tokens": 25}

    def expand(repo, env, run, context, paths, stopped):
        assert paths == ["helper.py"]
        context["sources"]["current"]["helper.py"] = "def retry(): return False\n"

    monkeypatch.setattr(verification, "mortis_verify", real_mortis_verify)
    monkeypatch.setattr(workflow_inference, "execute", execute)
    monkeypatch.setattr(assessment, "expand_context", expand)
    assessment.perform(run_id, Path("/tmp/repo"), {}, {}, connection, {}, claim)
    with transaction() as db:
        candidate = db.get(ReviewCandidate, "candidate")
        if round_type == "unknown":
            assert candidate.status == "failed" and candidate.usage == {"tokens": None}
            assert db.scalar(select(Finding)) is None and len(calls) == 1
            return
        assert candidate.status == "completed", candidate.error
        assert candidate.usage == {"tokens": 50}
        finding = db.scalar(select(Finding))
        assert candidate.finding_id == finding.id and candidate.is_new
        assert candidate.result["verdict"] == "confirmed"
        assert candidate.result["evidence"][0]["quote"] == "while True:\n"
    assert calls == ["assess-candidate:0", "assess-candidate:1"]


@pytest.mark.parametrize("recovered", [False, True])
def test_mortis_verification_retains_explicit_resolution_evidence(verification_scenario, monkeypatch, recovered):  # noqa: F811
    from code_mortis.core import workflow_inference

    run_id, claim = verification_scenario["run"], "verification-owner"
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.engine, run.status, run.started_at = "mortis", "running", now()
        run.config = {**run.config, "analysis_engine": "mortis", "settings": {
            **run.config["settings"], "analysis_engine": "mortis"}}
        run.progress = {"execution": claim, "analysis_status": "completed"}
        run.report = {"summary": {"total_tokens": 50}}
        verification.prepare(db, run, db.get(Repository, 22), "synthetic-github-token")
        db.flush()
        row = db.scalar(select(FindingVerification))
        assert row is not None
        if recovered:
            row.status, row.usage = "running", {"tokens": None, "reserved_tokens": 1000}
        connection = run.config["connection"]

    calls = []

    def execute(selected, credentials, supplied, **kwargs):
        calls.append(kwargs["task_key"])
        assert kwargs["stage"] == "verification"
        raw = {**RESULT, "evidence": [
            {"source_id": source_id, "start_line": 1, "end_line": 1}
            for source_id in supplied["citation_sources"]
        ]}
        return kwargs["validator"](raw, supplied), {"tokens": 37}

    monkeypatch.setattr(verification, "mortis_verify", real_mortis_verify)
    monkeypatch.setattr(workflow_inference, "execute", execute)
    monkeypatch.setattr(verification, "build_context", lambda *a, **k: copy.deepcopy(CONTEXT))
    verification.perform(run_id, Path("/tmp/repo"), {}, {}, connection, {}, claim)
    with transaction() as db:
        row = db.scalar(select(FindingVerification))
        assert row.status == "completed", row.error
        assert row.usage == {"tokens": 37}
        assert row.verdict == "fixed" and row.resolution == "pending"
        assert {item["revision"] for item in row.result["evidence"]} == {"original", "current"}
    assert len(calls) == 1
