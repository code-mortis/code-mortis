import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError

from code_mortis.core import analysis
from code_mortis.core.db import engine, now, transaction
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityActivity,
    SecurityScan,
)
from code_mortis.schemas.analysis import PlanSpec
from code_mortis.schemas.common import RepoSettings


def specification(**updates):
    values = dict(repository_id=22, initiator_id=7, authorization={"origin": "browser", "actor_id": 7},
                  trigger="manual", connection={"id": "connection", "kind": "api", "model": "test-model"},
                  revisions={"base": "b" * 40, "merge_base": "b" * 40, "head": "a" * 40},
                  versions={name: "v1" for name in ("engine", "planner", "prompt", "rules", "schema", "file_policy")},
                  settings={"concurrency": 2, "language": "English"}, file_policy={"exclusions": []},
                  selected=[{"path": "src/a.py", "blob_sha": "c" * 40}], exclusions=[], graph={"eligible": False},
                  budgets={"analysis": 100, "assessment": None, "summary": 20}, tasks=[
                      {"key": "a", "stage": "analysis", "input_hash": "d" * 64, "scope": [{"path": "src/a.py"}]},
                      {"key": "b", "stage": "analysis", "input_hash": "e" * 64, "scope": [{"path": "src/b.py"}]},
                      {"key": "summary", "stage": "summary", "input_hash": "f" * 64, "scope": [], "dependencies": ["a"]},
                  ])
    return PlanSpec.model_validate({**values, **updates})


def planned(seed, **updates):
    with transaction() as db:
        db.add(Job(id="analysis-job", kind="review", payload={}, status="running", attempts=1,
                   lease_until=now() + timedelta(minutes=10)))
        db.flush()
        plan = analysis.persist_plan(db, "analysis-job", specification(**updates))
        tasks = {row.key: row.id for row in db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id))}
        return plan.id, tasks


def claim(identity, **updates):
    return analysis.claim_task(identity, "analysis-job", 1, input_tokens=updates.pop("input_tokens", 10),
                               output_tokens=updates.pop("output_tokens", 30), **updates)


@pytest.mark.parametrize("first_input", [100, 250])
def test_finite_rolling_reservations_split_refill_and_keep_unknown_usage(seed, first_input):
    budget = 1000
    plan, tasks = planned(seed, settings={"concurrency": 4}, budgets={"analysis": budget},
        versions={**specification().versions, "output_tokens": analysis.WORKFLOW_OUTPUT_POLICY,
                  "budget_schedule": analysis.ROLLING_BUDGET},
        tasks=[{"key": key, "stage": "analysis", "input_hash": "d" * 64, "scope": []}
               for key in ("a", "b", "c", "d", "e", "f")])
    assert claim(tasks["f"], input_tokens=budget, output_tokens=0) is None
    leases = [claim(tasks[key], input_tokens=first_input if key == "a" else 100, output_tokens=0)
              for key in ("a", "b", "c", "d")]
    assert all(lease is not None and lease.output_tokens > 0 for lease in leases)
    assert claim(tasks["e"], input_tokens=100, output_tokens=0) is None
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == budget
    assert analysis.finish_task(leases[0], usage_tokens=first_input + 1, result={"ok": True})
    replacement = claim(tasks["e"], input_tokens=100, output_tokens=0)
    assert replacement is not None
    assert analysis.finish_task(replacement, usage_tokens=None, error_code="unknown_usage")
    assert analysis.finish_task(leases[1], usage_tokens=100, result={"ok": True})
    assert claim(tasks["f"], input_tokens=20, output_tokens=0) is None
    with transaction() as db:
        attempt = db.get(AnalysisAttempt, replacement.attempt_id)
        assert attempt.usage_state == "unknown"
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens <= budget


@pytest.mark.parametrize("demand,keys", [(None, ("a",)), (1, ("a", "b"))])
def test_single_eligible_task_can_use_full_rolling_budget(seed, demand, keys):
    _, tasks = planned(seed, settings={"concurrency": 8}, budgets={"analysis": 1000},
        versions={**specification().versions, "output_tokens": analysis.WORKFLOW_OUTPUT_POLICY,
                  "budget_schedule": analysis.ROLLING_BUDGET},
        tasks=[{"key": key, "stage": "analysis", "input_hash": "d" * 64, "scope": []} for key in keys])
    lease = claim(tasks["a"], input_tokens=200, output_tokens=0, budget_demand=demand)
    assert lease is not None and lease.output_tokens == 800


@pytest.mark.parametrize("unknown", [False, True])
def test_parallel_scan_attempts_reserve_and_settle_both_token_budgets(seed, unknown):
    from test_repository_security import saved_scan

    with transaction() as db:
        scan = saved_scan(db, seed)
        db.add(Job(id="analysis-job", kind="security_scan", payload={"scan_id": scan.id}, status="running", attempts=1,
                   lease_until=now() + timedelta(minutes=10)))
        db.flush()
        spec = specification(scan_id=scan.id, connection=scan.config["connection"],
            revisions={"head": scan.head_sha}, settings={"concurrency": 2},
            versions={**specification().versions, "output_tokens": analysis.WORKFLOW_OUTPUT_POLICY,
                      "budget_schedule": analysis.ROLLING_BUDGET},
            budgets={"security": 1500, "assessment": 1000, "verification": 2000},
            tasks=[{"key": key, "stage": "security", "input_hash": "d" * 64, "scope": [{"budget_stage": stage}]}
                   for key, stage in (("a", "assessment"), ("b", "assessment"), ("v", "verification"))])
        plan_id = analysis.persist_plan(db, "analysis-job", spec).id
        tasks = {row.key: row.id for row in db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id))}

    barrier = Barrier(2)

    def reserve(key):
        barrier.wait(5)
        return claim(tasks[key], input_tokens=100, output_tokens=0, budget_demand=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(reserve, ("a", "b"))
    assert all(lease is not None and lease.output_tokens == 400 for lease in (first, second))
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan_id, "security")).charged_tokens == 1000
        assert db.get(AnalysisBudget, (plan_id, "assessment")).charged_tokens == 1000
        assert db.get(AnalysisBudget, (plan_id, "verification")).charged_tokens == 0
    assert analysis.finish_task(first, usage_tokens=150, result={"ok": True})
    if unknown:
        assert analysis.finish_task(second, usage_tokens=None, error_code="usage_unknown")
    else:
        assert analysis.finish_task(second, usage_tokens=100, result={"ok": True})
    with transaction() as db:
        for stage in ("security", "assessment"):
            assert db.get(AnalysisBudget, (plan_id, stage)).charged_tokens == (650 if unknown else 250)
    verification = claim(tasks["v"], input_tokens=100, output_tokens=0, budget_demand=1)
    if unknown:
        assert verification is None
    else:
        assert verification is not None and verification.output_tokens == 1150


def test_plan_inputs_and_task_identity_survive_restart_and_reordering(seed):
    identity, tasks = planned(seed)
    with transaction() as db:
        spec = specification()
        spec.tasks.reverse()
        assert analysis.persist_plan(db, "analysis-job", spec).id == identity
        with pytest.raises(ValueError, match="immutable"):
            analysis.persist_plan(db, "analysis-job", specification(settings={"concurrency": 3}))
    engine().dispose()
    with transaction() as db:
        assert {row.key: row.id for row in db.scalars(select(AnalysisTask))} == tasks
        assert db.get(AnalysisPlan, identity).snapshot["selected"] == specification().selected
    for sql in ("UPDATE analysis_plans SET snapshot = '{}' WHERE id = :id",
                "UPDATE analysis_tasks SET input_hash = 'changed' WHERE plan_id = :id",
                "UPDATE analysis_budgets SET token_limit = NULL WHERE plan_id = :id"):
        with pytest.raises(DBAPIError, match="immutable"), transaction() as db:
            db.execute(text(sql), {"id": identity})


@pytest.mark.parametrize("changes", [
    {"connection": {"id": "native", "kind": "chatgpt"}},
    {"connection": {"id": "shared", "kind": "api", "api_key": "never persist"}},
    {"revisions": {"head": "main"}}, {"versions": {"engine": "v1", "extra": "v1"}},
    {"budgets": {"analysis": -1}}, {"budgets": {"analysis": True}},
    {"tasks": [{"key": "a", "stage": "analysis", "input_hash": "d" * 64, "scope": [], "dependencies": ["missing"]}]},
    {"tasks": [{"key": "a", "stage": "analysis", "input_hash": "d" * 64, "scope": [], "dependencies": ["b"]},
               {"key": "b", "stage": "analysis", "input_hash": "e" * 64, "scope": [], "dependencies": ["a"]}]},
])
def test_plan_rejects_invalid_or_unreproducible_inputs(changes):
    with pytest.raises(ValidationError):
        specification(**changes)


def test_dependencies_and_independent_stage_reservations(seed):
    plan, tasks = planned(seed)
    assert claim(tasks["summary"], input_tokens=5, output_tokens=10) is None
    lease = claim(tasks["a"])
    assert lease and analysis.renew_task(lease)
    assert analysis.finish_task(lease, result={"validated": True, "findings": []}, usage_tokens=25)
    assert not analysis.finish_task(lease, result={"overwrite": True}, usage_tokens=0)
    assert claim(tasks["summary"], input_tokens=5, output_tokens=10)
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 25
        assert db.get(AnalysisBudget, (plan, "summary")).charged_tokens == 15
        assert db.get(AnalysisBudget, (plan, "assessment")).token_limit is None


@pytest.mark.parametrize("tokens", [-1, True, 1.5])
def test_request_reservations_require_nonnegative_whole_tokens(seed, tokens):
    _, tasks = planned(seed)
    with pytest.raises(ValueError, match="nonnegative whole tokens"):
        claim(tasks["a"], input_tokens=tokens)


def test_concurrent_reservations_and_duplicate_leases_are_atomic(seed):
    plan, tasks = planned(seed)
    barrier = Barrier(2)

    def reserve(identity):
        barrier.wait(timeout=3)
        return claim(identity, input_tokens=30, output_tokens=40)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(reserve, [tasks["a"], tasks["b"]]))
    assert sum(lease is not None for lease in leases) == 1
    lease = next(lease for lease in leases if lease)
    assert claim(lease.task_id, input_tokens=0, output_tokens=0) is None
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 70
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 1


@pytest.mark.parametrize("operation", ["claim", "finish", "renew", "retry"])
def test_lock_wait_cannot_use_a_timestamp_from_before_lease_expiration(seed, monkeypatch, operation):
    _, tasks = planned(seed)
    lease = claim(tasks["a"]) if operation != "claim" else None
    if operation == "retry":
        assert analysis.finish_task(lease, usage_tokens=10, error_code="invalid_result")
    started, advanced = Event(), Event()
    timestamp = now()
    monkeypatch.setattr(analysis, "now", lambda: timestamp + timedelta(seconds=2 if advanced.is_set() else 0))
    with transaction() as db:
        db.get(Job, "analysis-job").lease_until = timestamp + timedelta(seconds=1)

    def querying(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM jobs" in statement and "FOR UPDATE" in statement:
            started.set()

    def execute():
        if operation == "claim":
            return claim(tasks["a"])
        if operation == "finish":
            return analysis.finish_task(lease, usage_tokens=0, result={"late": True})
        if operation == "renew":
            return analysis.renew_task(lease)
        return analysis.retry_task(tasks["a"], "analysis-job", 1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction() as db:
            db.scalar(select(Job).where(Job.id == "analysis-job").with_for_update())
            event.listen(engine(), "before_cursor_execute", querying)
            try:
                future = pool.submit(execute)
                assert started.wait(3), "The other worker must reach the locked claim"
                advanced.set()
            finally:
                event.remove(engine(), "before_cursor_execute", querying)
        assert not future.result(timeout=3)


def test_unknown_usage_and_expired_claims_cannot_reset_budget_or_repeat_automatically(seed):
    plan, tasks = planned(seed)
    lease = claim(tasks["a"])
    with transaction() as db:
        db.get(AnalysisAttempt, lease.attempt_id).lease_until = now() - timedelta(seconds=1)
        db.get(Job, "analysis-job").attempts = 2
    assert not analysis.finish_task(lease, result={"late": True}, usage_tokens=0)
    assert analysis.claim_task(tasks["a"], "analysis-job", 2, input_tokens=10, output_tokens=30) is None
    assert not analysis.retry_task(tasks["a"], "analysis-job", 2)
    assert analysis.retry_task(tasks["a"], "analysis-job", 2, acknowledge_unknown=True)
    next_lease = analysis.claim_task(tasks["a"], "analysis-job", 2, input_tokens=10, output_tokens=30)
    assert next_lease and next_lease.number == 2
    assert not analysis.finish_task(lease, result={"late": True}, usage_tokens=0)
    assert analysis.finish_task(next_lease, usage_tokens=None, error_code="response_lost")
    assert analysis.claim_task(tasks["b"], "analysis-job", 2, input_tokens=10, output_tokens=30) is None
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 80
        attempts = list(db.scalars(select(AnalysisAttempt).order_by(AnalysisAttempt.number)))
        assert [(row.number, row.usage_state, row.usage_tokens) for row in attempts] == [(1, "unknown_acknowledged", None), (2, "unknown", None)]
        assert db.get(AnalysisTask, tasks["a"]).result is None


def test_reported_failure_charged_and_unlimited_caps_stay_unlimited(seed):
    plan, tasks = planned(seed, budgets={"analysis": None, "assessment": None, "summary": None})
    lease = claim(tasks["a"], input_tokens=10_000, output_tokens=20_000)
    assert analysis.finish_task(lease, usage_tokens=35_000, error_code="invalid_result")
    assert analysis.retry_task(tasks["a"], "analysis-job", 1)
    assert claim(tasks["a"], input_tokens=10_000, output_tokens=20_000)
    with transaction() as db:
        budget = db.get(AnalysisBudget, (plan, "analysis"))
        assert budget.token_limit is None and budget.charged_tokens == 65_000


def test_stage_deadline_prevents_new_consumption(seed):
    _, tasks = planned(seed, deadlines={"analysis": now() - timedelta(seconds=1)})
    assert claim(tasks["a"]) is None


def test_validated_unknown_usage_retains_result_and_blocks_further_stage_spend(seed):
    plan, tasks = planned(seed)
    lease = claim(tasks["a"])
    assert analysis.finish_task(lease, result={"validated": True}, usage_tokens=None)
    assert claim(tasks["b"], input_tokens=1, output_tokens=1) is None
    # Independent summary limits still apply; analysis's unknown usage does not consume that budget.
    assert claim(tasks["summary"], input_tokens=1, output_tokens=1)
    assert analysis.acknowledge_unknown_usage(tasks["a"], "analysis-job", 1)
    assert claim(tasks["b"], input_tokens=1, output_tokens=1)
    with transaction() as db:
        assert db.get(AnalysisTask, tasks["a"]).result == {"validated": True}
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 42
        attempt = db.get(AnalysisAttempt, lease.attempt_id)
        assert attempt.usage_tokens is None and attempt.usage_state == "unknown_acknowledged"


def test_cancellation_preserves_successful_tasks_and_unknown_consumption(seed):
    plan, tasks = planned(seed)
    complete, interrupted = claim(tasks["a"]), claim(tasks["b"])
    assert analysis.finish_task(complete, usage_tokens=10, result={"validated": True})
    analysis.cancel_tasks("analysis-job")
    assert not analysis.finish_task(interrupted, usage_tokens=0, result={"late": True})
    assert claim(tasks["summary"], input_tokens=1, output_tokens=1) is None
    with transaction() as db:
        assert db.get(AnalysisTask, tasks["a"]).result == {"validated": True}
        assert db.get(AnalysisTask, tasks["b"]).status == "cancelled"
        assert db.get(AnalysisAttempt, interrupted.attempt_id).usage_state == "unknown"
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 50


@pytest.mark.parametrize("change", ["cancel", "revision", "disabled"])
def test_owner_changes_fence_task_results_before_acceptance(seed, change):
    with transaction() as db:
        db.add(ReviewRun(id="owner-run", pr_id="pr", actor_id=7, trigger="manual", dedup_key="owner-run",
                         base_sha="b" * 40, head_sha="a" * 40,
                         config={"connection": specification().connection}, status="running"))
        db.add(Job(id="analysis-job", kind="review", payload={"run_id": "owner-run"}, status="running", attempts=1,
                   lease_until=now() + timedelta(minutes=10)))
        db.flush()
        plan = analysis.persist_plan(db, "analysis-job", specification(run_id="owner-run"))
        task = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id, AnalysisTask.key == "a")).id
    lease = claim(task)
    assert lease
    with transaction() as db:
        if change == "cancel":
            db.get(ReviewRun, "owner-run").cancel_requested = True
        elif change == "disabled":
            db.get(Repository, 22).enabled = False
        else:
            db.get(PullRequest, "pr").head_sha = "e" * 40
    assert not analysis.finish_task(lease, result={"late": True}, usage_tokens=0)
    assert not analysis.renew_task(lease)


def test_plan_cannot_bind_other_repositories_revisions_or_connections_to_a_saved_run(seed):
    with transaction() as db:
        db.add(ReviewRun(id="owner-run", pr_id="pr", actor_id=7, trigger="manual", dedup_key="owner-run",
                         base_sha="b" * 40, head_sha="a" * 40,
                         config={"connection": specification().connection}, status="running"))
        db.add(Job(id="analysis-job", kind="review", payload={"run_id": "owner-run"}))
        db.flush()
        for changed in ({"repository_id": 99}, {"revisions": {"base": "b" * 40, "head": "e" * 40}},
                        {"connection": {"id": "other", "kind": "api"}}):
            with pytest.raises(ValueError, match="must match"):
                analysis.persist_plan(db, "analysis-job", specification(run_id="owner-run", **changed))
        assert list(db.scalars(select(AnalysisPlan))) == []


def test_explicit_shared_summary_plan_can_claim_completed_native_history(seed):
    shared = specification().connection
    native = {"id": "retired", "kind": "chatgpt", "model": "old-model"}
    with transaction() as db:
        db.add(ReviewRun(id="owner-run", pr_id="pr", actor_id=7, trigger="manual", dedup_key="owner-run",
                         base_sha="b" * 40, head_sha="a" * 40, config={"connection": native}, engine="chatgpt",
                         status="completed", progress={"summary_retry": "analysis-job"}, summary={"status": "pending"}))
        db.add(Job(id="analysis-job", kind="generate_summary", payload={"run_id": "owner-run", "config": {"connection": shared}},
                   status="running", attempts=1, lease_until=now() + timedelta(minutes=10)))
        db.flush()
        plan = analysis.persist_plan(db, "analysis-job", specification(run_id="owner-run", tasks=[
            {"key": "summary", "stage": "summary", "input_hash": "f" * 64, "scope": []},
        ]))
        task = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id)).id
    lease = claim(task, input_tokens=1, output_tokens=10)
    assert lease
    assert analysis.finish_task(lease, usage_tokens=8, result={"validated": True})
    with transaction() as db:
        run = db.get(ReviewRun, "owner-run")
        assert run.config == {"connection": native} and run.engine == "chatgpt" and run.status == "completed"


@pytest.mark.parametrize("change", ["superseded_attempt", "closed", "revision", "cancelled"])
def test_shared_summary_plan_cannot_consume_after_its_attempt_or_parent_changes(seed, change):
    with transaction() as db:
        db.add(ReviewRun(id="owner-run", pr_id="pr", actor_id=7, trigger="manual", dedup_key="owner-run",
                         base_sha="b" * 40, head_sha="a" * 40, config={"connection": {"kind": "chatgpt"}},
                         status="completed", progress={"summary_retry": "analysis-job"}))
        db.add(Job(id="analysis-job", kind="generate_summary", payload={
            "run_id": "owner-run", "config": {"connection": specification().connection}},
            status="running", attempts=1, lease_until=now() + timedelta(minutes=10)))
        db.flush()
        plan = analysis.persist_plan(db, "analysis-job", specification(run_id="owner-run", tasks=[
            {"key": "summary", "stage": "summary", "input_hash": "f" * 64, "scope": []},
        ]))
        task = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id)).id
        if change == "superseded_attempt":
            db.get(ReviewRun, "owner-run").progress = {"summary_retry": "new-attempt"}
        elif change == "closed":
            db.get(PullRequest, "pr").state = "closed"
        elif change == "revision":
            db.get(PullRequest, "pr").head_sha = "e" * 40
        else:
            db.get(ReviewRun, "owner-run").cancel_requested = True
    assert claim(task, input_tokens=1, output_tokens=10) is None


def test_engine_selection_is_explicit_and_does_not_change_legacy_engine_semantics():
    assert RepoSettings().analysis_engine == "mortis"
    assert analysis.engine_snapshot({})["analysis_engine"] == "ocr"
    analysis.require_available_engine({"settings": {"analysis_engine": "mortis"}})
    with pytest.raises(ValueError, match="retired"):
        analysis.require_available_engine({"engine": "chatgpt"})
    with pytest.raises(ValueError, match="supported"):
        analysis.engine_snapshot({"analysis_engine": "future"})
    with pytest.raises(ValueError, match="does not match"):
        analysis.engine_snapshot({"analysis_engine": "ocr", "settings": {"analysis_engine": "mortis"}})


def test_additive_upgrade_keeps_mixed_history_and_jobs_without_inference(seed, client, github_stub):
    originals = {}
    with transaction() as db:
        for kind in ("ocr", "api", "chatgpt", "claude_code"):
            config = {"connection": {"id": "connection", "kind": kind, "owner_id": 7},
                      "settings": {"token_budget": None, "exclusions": "vendor/**"}, "legacy": True}
            values = dict(id=f"legacy-{kind}", pr_id="pr", actor_id=7, trigger="manual", dedup_key=f"legacy-{kind}",
                          base_sha="b" * 40, head_sha="a" * 40, config=config, status="partial", engine=kind,
                          owner_id=7, connection_id="connection", report={"partial": True, "tokens": None},
                          summary={"status": "failed", "tokens": None}, stages={"assessment": {"status": "completed", "tokens": 7}},
                          publication="uncertain", review_ids=[123], progress={"analysis_saved": True})
            db.add(ReviewRun(**values))
            originals[kind] = copy.deepcopy(values)
            db.add(Job(id=f"job-{kind}", kind="review", payload={"run_id": f"legacy-{kind}", "config": config}, status="done"))
            db.add(SecurityScan(id=f"scan-{kind}", repository_id=22, actor_id=7, connection_id="connection",
                                request_key=f"scan-{kind}", branch="main", head_sha="a" * 40, config=config,
                                status="partial", report={"findings": [], "unknown_usage": True},
                                coverage={"ai": "partial", "deterministic": "complete"}))
        db.flush()
        db.add(SecurityActivity(id="legacy-audit", scan_id="scan-chatgpt", actor_id=7, action="cancelled",
                                detail={"partial_publication": [123], "usage": None}))
        settings = copy.deepcopy(db.get(Repository, 22).settings)
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261107_graph_limits")
    finally:
        command.upgrade(config, "head")
    engine().dispose()
    with transaction() as db:
        assert db.get(Repository, 22).settings == {**settings, "analysis_engine": "mortis"}
        for kind, values in originals.items():
            row = db.get(ReviewRun, values["id"])
            for key, value in values.items():
                assert getattr(row, key) == value
            assert db.get(Job, f"job-{kind}").payload == {"run_id": row.id, "config": values["config"]}
            scan = db.get(SecurityScan, f"scan-{kind}")
            assert scan.config == values["config"] and scan.actor_id == 7
            assert scan.report == {"findings": [], "unknown_usage": True}
            assert scan.coverage == {"ai": "partial", "deterministic": "complete"}
        assert db.get(SecurityActivity, "legacy-audit").detail == {"partial_publication": [123], "usage": None}
        assert list(db.scalars(select(AnalysisPlan))) == []
    for kind in originals:
        response = client.get(f"/api/v1/runs/legacy-{kind}")
        assert response.status_code == 200, response.text
        assert response.json()["run"]["engine"] == kind
