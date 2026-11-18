"""Real durable workflow admission, replay and rejection with synthetic provider HTTP."""

import copy
import json

import httpx
import pytest
from sqlalchemy import select
from test_llm_http import result as wire_result
from test_mortis_review import CREDENTIALS
from test_mortis_review import scenario as scenario

from code_mortis.core import workflow_inference
from code_mortis.core.db import transaction
from code_mortis.core.errors import InferenceOutputError, ReviewStopped
from code_mortis.core.security import seal
from code_mortis.integrations import llm
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    Connection,
    Job,
    ReviewRun,
)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


@pytest.fixture
def workflow(scenario, monkeypatch):
    state = {**scenario, "wire": [], "usage": True, "after_response": None}

    def handler(request):
        state["wire"].append(json.loads(request.content))
        if state["after_response"]:
            state["after_response"]()
        return httpx.Response(200, json=wire_result("openai", usage=state["usage"], content='{"ok":true}'))

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    state["original"] = scenario
    return state


def call(case, **kwargs):
    options = {"instructions": "Inspect supplied evidence only", "schema": SCHEMA,
               "validator": lambda raw, supplied: raw, "claim": "mortis-job:1", "run_id": "mortis-run",
               "stage": "assessment", "task_key": "candidate:0"}
    options.update(kwargs)
    return workflow_inference.execute(case["connection"], copy.deepcopy(CREDENTIALS),
                                      options.pop("context", {"source": "safe source"}), **options)


@pytest.mark.parametrize("timeout", [0, -1, True, False, "1", float("nan"), float("inf"), float("-inf")])
def test_invalid_timeout_rejected_before_work_starts(workflow, timeout):
    with pytest.raises(ValueError, match="timeout must be a positive finite number"):
        call(workflow, timeout=timeout)
    assert workflow["wire"] == []
    with transaction() as db:
        assert db.scalar(select(AnalysisPlan)) is None
        assert db.scalar(select(AnalysisAttempt)) is None


@pytest.mark.parametrize("legacy", [False, True])
def test_completed_result_replays_after_worker_restart_without_new_consumption(workflow, monkeypatch, legacy):
    with monkeypatch.context() as patcher:
        if legacy:
            patcher.setattr(workflow_inference.analysis, "configured_output_policy", lambda config: None)
        assert call(workflow) == ({"ok": True}, {"tokens": 12})
    assert workflow["wire"][0]["max_tokens"] == (8192 if legacy else 16384)
    with transaction() as db:
        db.get(Job, "mortis-job").attempts = 2
        run = db.get(ReviewRun, "mortis-run")
        run.progress = {**run.progress, "execution": "mortis-job:2"}
    assert call(workflow, claim="mortis-job:2", token_budget=0) == ({"ok": True}, {"tokens": 12})
    assert len(workflow["wire"]) == 1
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan))
        assert plan.snapshot["versions"].get("output_tokens") == (None if legacy else workflow_inference.analysis.OUTPUT_TOKEN_POLICY)
        assert db.get(AnalysisBudget, (plan.id, "assessment")).charged_tokens == 12
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 1
        assert "safe source" not in json.dumps(plan.snapshot)


def test_request_identity_cannot_reuse_a_different_context_or_settings(workflow):
    call(workflow)
    with pytest.raises(ValueError, match="immutable"):
        call(workflow, context={"source": "changed source"})
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.config = {**run.config, "settings": {**run.config["settings"], "language": "Spanish"}}
    with pytest.raises(ValueError, match="immutable"):
        call(workflow, task_key="another:0")
    assert len(workflow["wire"]) == 1


@pytest.mark.parametrize("change", ["claim", "job_kind", "credentials", "cancelled"])
def test_changed_owner_or_credentials_fail_before_consumption(workflow, change):
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        if change == "claim":
            run.progress = {"execution": "replacement:1"}
        elif change == "job_kind":
            db.get(Job, "mortis-job").kind = "publish"
        elif change == "credentials":
            db.get(Connection, "connection").secret = seal(json.dumps({"api_key": "changed-key"}))
        else:
            run.cancel_requested = True
    with pytest.raises(ReviewStopped):
        call(workflow)
    assert not workflow["wire"]
    with transaction() as db:
        assert db.scalar(select(AnalysisAttempt)) is None


@pytest.mark.parametrize("failure", ["cancel", "github", "timeout"])
def test_post_response_failure_preserves_consumption_but_never_accepts_evidence(workflow, failure):
    def after():
        if failure == "cancel":
            with transaction() as db:
                db.get(ReviewRun, "mortis-run").cancel_requested = True
        elif failure == "github":
            workflow["original"]["revoked"] = True

    def authorize():
        if failure == "timeout" and workflow["wire"]:
            raise TimeoutError("Stage expired during evidence validation")

    workflow["after_response"] = after
    with pytest.raises((ReviewStopped, InferenceOutputError)) as caught:
        call(workflow, authorize=authorize)
    assert caught.value.usage == {"tokens": 12}
    with transaction() as db:
        task, attempt = db.scalar(select(AnalysisTask)), db.scalar(select(AnalysisAttempt))
        assert task.result is None and task.status == "failed"
        assert attempt.usage_tokens == 12 and attempt.usage_state == "known"
        assert db.get(AnalysisBudget, (task.plan_id, "assessment")).charged_tokens == 12


def test_unknown_consumption_retains_completed_result_but_blocks_new_tasks(workflow):
    workflow["usage"] = False
    assert call(workflow) == ({"ok": True}, {"tokens": None})
    assert call(workflow) == ({"ok": True}, {"tokens": None})
    with pytest.raises(InferenceOutputError, match="unknown consumption"):
        call(workflow, task_key="second:0")
    assert len(workflow["wire"]) == 1


def test_failed_validation_can_correct_once_under_same_stage_without_saving_raw_output(workflow):
    def invalid(raw, supplied):
        error = ValueError("Untrusted provider-secret detail")
        error.feedback = {"kind": "citation_selection", "reason": "Use supplied ranges"}
        raise error

    with pytest.raises(InferenceOutputError) as caught:
        call(workflow, validator=invalid)
    assert caught.value.usage == {"tokens": 12} and caught.value.feedback["kind"] == "citation_selection"
    assert call(workflow, task_key="candidate:1") == ({"ok": True}, {"tokens": 12})
    with transaction() as db:
        tasks = list(db.scalars(select(AnalysisTask)))
        assert "provider-secret" not in json.dumps([row.result for row in tasks])
        assert db.get(AnalysisBudget, (tasks[0].plan_id, "assessment")).charged_tokens == 24


def test_smaller_remaining_allowance_cannot_borrow_from_total_stage_budget(workflow):
    with pytest.raises(InferenceOutputError, match="remaining workflow allowance"):
        call(workflow, token_budget=1)
    assert not workflow["wire"]
    with transaction() as db:
        assert db.scalar(select(AnalysisAttempt)) is None


def test_expired_stage_cannot_be_renewed_by_a_fresh_request(workflow):
    from datetime import timedelta

    from code_mortis.core.db import now

    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.started_at = now() - timedelta(seconds=901)
    with pytest.raises(InferenceOutputError, match="deadline"):
        call(workflow)
    assert not workflow["wire"]


def test_inline_summary_dispatch_respects_timeout_with_precreated_unbounded_stage(workflow, monkeypatch):
    call(workflow)
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan))
        assert db.get(AnalysisBudget, (plan.id, "summary")).deadline_at is None
    durations = []

    def handler(request):
        durations.extend(request.extensions["timeout"].values())
        return httpx.Response(200, json=wire_result("openai", content='{"ok":true}'))

    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False))
    assert call(workflow, stage="summary", task_key="inline-summary:0", timeout=2) == ({"ok": True}, {"tokens": 12})
    assert durations and all(0 < duration <= 2 for duration in durations)
    with transaction() as db:
        budget = db.get(AnalysisBudget, (plan.id, "summary"))
        assert budget.deadline_at is None and budget.charged_tokens == 12
