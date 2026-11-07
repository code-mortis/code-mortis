from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from sqlalchemy import event, select
from test_analysis_foundation import specification

from code_mortis.core import analysis
from code_mortis.core import inference_admission as admission
from code_mortis.core.db import engine, now, transaction
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisTask,
    Connection,
    InferenceAdmission,
    Job,
)


def planned(seed, job_id="job", *, concurrency=2, budgets=None, tasks=None):
    changes = {"settings": {"concurrency": concurrency}}
    if budgets is not None:
        changes["budgets"] = budgets
    if tasks is not None:
        changes["tasks"] = tasks
    with transaction() as db:
        db.add(Job(id=job_id, kind="review", payload={}, status="running", attempts=1,
                   lease_until=now() + timedelta(minutes=10)))
        db.flush()
        plan = analysis.persist_plan(db, job_id, specification(**changes))
        return plan.id, {row.key: row.id for row in db.scalars(select(AnalysisTask).where(
            AnalysisTask.plan_id == plan.id))}


def claim(task, job="job", **changes):
    return analysis.claim_task(task, job, 1, input_tokens=changes.pop("input_tokens", 10),
                               output_tokens=changes.pop("output_tokens", 30), **changes)


def ticket(identity, run, *, slots=1, run_limit=8, seconds=60):
    with transaction() as db:
        return admission.admit(db, identity=identity, connection_id="connection", run_key=run, slots=slots,
                               run_limit=run_limit, lease_until=now() + timedelta(seconds=seconds))


def test_connection_capacity_is_atomic_across_sixteen_jobs(seed):
    owners = [(f"job-{i}", planned(seed, f"job-{i}")[1]["a"]) for i in range(16)]
    barrier = Barrier(len(owners))

    def start(owner):
        barrier.wait(timeout=10)
        return claim(owner[1], owner[0])

    with ThreadPoolExecutor(max_workers=len(owners)) as workers:
        leases = list(workers.map(start, owners))
    assert sum(lease is not None for lease in leases) == 8
    with transaction() as db:
        assert sum(row.slots for row in db.scalars(select(InferenceAdmission).where(
            InferenceAdmission.status == "active"))) == 8
        assert len(list(db.scalars(select(AnalysisAttempt)))) == 8
        assert sum(row.charged_tokens for row in db.scalars(select(AnalysisBudget))) == 320
    lease = next(value for value in leases if value)
    assert claim(lease.task_id, lease.job_id) is None
    assert analysis.finish_task(lease, usage_tokens=20, result={"ok": True})
    # A released slot survives process/connection-pool restart and goes to a waiter.
    engine().dispose()
    # The oldest waiter owns the next turn, regardless of the polling order.
    with transaction() as db:
        first = db.scalar(select(InferenceAdmission).where(InferenceAdmission.status == "queued").order_by(
            InferenceAdmission.created_at, InferenceAdmission.id))
        waiting = next(owner for owner in owners if owner[0] == first.job_id)
    assert claim(waiting[1], waiting[0])


@pytest.mark.parametrize("operation", ["claim", "finish", "renew"])
def test_connection_lock_wait_cannot_admit_or_settle_after_job_deadline(seed, monkeypatch, operation):
    plan, ids = planned(seed)
    lease = claim(ids["a"]) if operation != "claim" else None
    timestamp, waiting, advanced = now(), Event(), Event()
    def clock():
        return timestamp + timedelta(seconds=2 if advanced.is_set() else 0)

    monkeypatch.setattr(analysis, "now", clock)
    monkeypatch.setattr(admission, "now", clock)
    with transaction() as db:
        db.get(Job, "job").lease_until = timestamp + timedelta(seconds=1)

    def querying(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM connections" in statement and "FOR UPDATE" in statement:
            waiting.set()

    def execute():
        if operation == "finish":
            return analysis.finish_task(lease, usage_tokens=0, result={"late": True})
        if operation == "renew":
            return analysis.renew_task(lease)
        return claim(ids["a"])

    with ThreadPoolExecutor(max_workers=1) as workers:
        event.listen(engine(), "before_cursor_execute", querying)
        try:
            with transaction() as db:
                db.scalar(select(Connection).where(Connection.id == "connection").with_for_update())
                waiting.clear()
                result = workers.submit(execute)
                assert waiting.wait(timeout=5)
                advanced.set()
            assert not result.result(timeout=5)
        finally:
            event.remove(engine(), "before_cursor_execute", querying)
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == (40 if lease else 0)
        assert len(list(db.scalars(select(AnalysisAttempt)))) == (1 if lease else 0)
        assert len(list(db.scalars(select(InferenceAdmission)))) == (1 if lease else 0)
        assert db.get(AnalysisTask, ids["a"]).result is None


def test_per_run_limit_spans_stages_without_sharing_their_token_budgets(seed):
    tasks = [dict(key=key, stage=stage, input_hash="a" * 64, scope=[]) for key, stage in (
        ("a", "analysis"), ("b", "analysis"), ("summary", "summary"))]
    plan, ids = planned(seed, tasks=tasks)
    a, b = claim(ids["a"]), claim(ids["b"])
    assert a and b
    assert claim(ids["summary"], input_tokens=5, output_tokens=10, concurrency_limit=8) is None
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 80
        assert db.get(AnalysisBudget, (plan, "summary")).charged_tokens == 0
    assert analysis.finish_task(a, usage_tokens=10, result={"ok": True})
    assert claim(ids["summary"], input_tokens=5, output_tokens=10)
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 50
        assert db.get(AnalysisBudget, (plan, "summary")).charged_tokens == 15


def test_all_five_stage_budgets_are_independent_and_exhaustion_reserves_no_capacity(seed):
    budgets = {"analysis": 0, "assessment": 30, "verification": 40, "summary": None, "security": 50}
    tasks = [dict(key=stage, stage=stage, input_hash="b" * 64, scope=[]) for stage in budgets]
    plan, ids = planned(seed, concurrency=8, budgets=budgets, tasks=tasks)
    assert claim(ids["analysis"], input_tokens=1, output_tokens=0) is None
    for stage in ("assessment", "verification", "summary", "security"):
        assert claim(ids[stage], input_tokens=10, output_tokens=20)
    with transaction() as db:
        assert {row.stage: row.charged_tokens for row in db.scalars(select(AnalysisBudget).where(
            AnalysisBudget.plan_id == plan))} == {stage: 0 if stage == "analysis" else 30 for stage in budgets}
        assert len(list(db.scalars(select(InferenceAdmission)))) == 4


def test_fair_turns_rotate_runs_instead_of_draining_one_runs_backlog(seed):
    assert ticket("holder", "holder", slots=8)
    assert ticket("a1", "a") is None
    assert ticket("a2", "a") is None
    assert ticket("b1", "b") is None
    assert admission.release_connection("holder")
    assert ticket("a1", "a")
    assert admission.release_connection("a1")
    assert ticket("a2", "a") is None
    assert ticket("b1", "b")
    assert ticket("a2", "a")


def test_saturated_run_does_not_block_other_runs(seed):
    assert ticket("a1", "a", run_limit=1)
    assert ticket("a2", "a", run_limit=1) is None
    assert ticket("b1", "b", run_limit=1)


def test_active_stricter_run_limit_cannot_be_widened_by_another_stage(seed):
    assert ticket("a1", "a", run_limit=1)
    assert ticket("a2", "a", run_limit=8) is None
    admission.release_connection("a1")
    assert ticket("a2", "a", run_limit=8)


def test_weighted_legacy_head_drains_connection_without_starvation(seed):
    assert ticket("holder", "holder", slots=7)
    assert ticket("legacy", "legacy", slots=8) is None
    assert ticket("small", "small") is None
    assert admission.release_connection("holder")
    assert ticket("small", "small") is None
    assert ticket("legacy", "legacy", slots=8)
    assert ticket("small", "small") is None
    assert admission.release_connection("legacy")
    assert ticket("small", "small")


def test_expiry_cannot_reclaim_a_potentially_live_request(seed):
    assert ticket("lost-worker", "old", slots=8)
    with transaction() as db:
        db.get(InferenceAdmission, "lost-worker").lease_until = now() - timedelta(seconds=1)
    engine().dispose()
    assert not admission.renew_connection("lost-worker")
    assert ticket("new", "new") is None
    # Explicit recovery after externally establishing that the worker stopped.
    assert admission.release_connection("lost-worker")
    assert ticket("new", "new")


def test_expired_and_cancelled_waiters_do_not_keep_their_turn(seed):
    assert ticket("holder", "holder", slots=8)
    assert ticket("expired", "a") is None
    assert ticket("cancelled", "b") is None
    with transaction() as db:
        db.get(InferenceAdmission, "expired").lease_until = now() - timedelta(seconds=1)
    admission.cancel_waiter("cancelled")
    admission.release_connection("holder")
    assert ticket("new", "new")
    with transaction() as db:
        assert db.get(InferenceAdmission, "expired").status == "cancelled"
        assert db.get(InferenceAdmission, "cancelled").status == "cancelled"


def test_stale_job_release_preserves_unknown_stage_consumption(seed):
    plan, ids = planned(seed)
    lease = claim(ids["a"])
    with transaction() as db:
        db.get(Job, "job").attempts = 2
    assert not analysis.renew_task(lease)
    assert not analysis.finish_task(lease, usage_tokens=0, result={"late": True})
    assert analysis.claim_task(ids["a"], "job", 2, input_tokens=10, output_tokens=30) is None
    assert admission.release_connection(lease.admission_id)
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 40
        assert db.get(AnalysisAttempt, lease.attempt_id).usage_state == "unknown"
    assert not analysis.retry_task(ids["a"], "job", 2)
    assert analysis.retry_task(ids["a"], "job", 2, acknowledge_unknown=True)
    assert analysis.claim_task(ids["a"], "job", 2, input_tokens=10, output_tokens=30)


def test_task_cancellation_removes_queued_turn_but_retains_running_capacity(seed):
    _, ids = planned(seed, concurrency=1)
    lease = claim(ids["a"])
    assert claim(ids["b"]) is None
    analysis.cancel_tasks("job")
    with transaction() as db:
        assert db.get(InferenceAdmission, lease.admission_id).status == "active"
        assert [row.status for row in db.scalars(select(InferenceAdmission).where(
            InferenceAdmission.task_id == ids["b"]))] == ["cancelled"]
    assert admission.release_connection(lease.admission_id)


def test_legacy_context_holds_whole_ceiling_and_heartbeats_without_long_db_transaction(seed):
    with admission.reserve_connection("connection", "legacy") as lease:
        assert lease.heartbeat()
        assert ticket("other", "other") is None
        with transaction() as db:
            assert db.get(InferenceAdmission, lease.admission_id).slots == 8
    assert ticket("other", "other")


def test_legacy_wait_timeout_and_cancellation_remove_tickets(seed):
    assert ticket("holder", "holder", slots=8)
    with pytest.raises(TimeoutError), admission.reserve_connection("connection", "legacy", wait_seconds=0):
        pytest.fail("Cannot enter while the ceiling is occupied")
    with pytest.raises(admission.AdmissionStopped), admission.reserve_connection(
            "connection", "cancelled", stopped=lambda: True):
        pytest.fail("Cannot enter cancelled work")
    with transaction() as db:
        assert list(db.scalars(select(InferenceAdmission).where(InferenceAdmission.status == "queued"))) == []


def test_cancellation_after_capacity_acquisition_releases_before_dispatch(seed):
    checks = iter([False, True])
    with pytest.raises(admission.AdmissionStopped), admission.reserve_connection(
            "connection", "legacy", stopped=lambda: next(checks)):
        pytest.fail("Must recheck cancellation before entering inference")
    assert ticket("next", "next", slots=8)


def test_abandoning_task_waiter_is_scoped_to_exact_job_attempt(seed):
    _, ids = planned(seed)
    assert ticket("holder", "holder", slots=8)
    assert claim(ids["a"]) is None
    assert admission.cancel_task_waiter(ids["a"], "job", 2) == 0
    assert admission.cancel_task_waiter(ids["a"], "job", 1) == 1
    assert admission.cancel_task_waiter(ids["a"], "job", 1) == 0
    with transaction() as db:
        assert list(db.scalars(select(AnalysisAttempt))) == []


def test_waiter_cancellation_cannot_release_a_concurrently_admitted_request(seed, monkeypatch):
    _, ids = planned(seed)
    assert ticket("holder", "holder", slots=8)
    assert claim(ids["a"]) is None
    selected, admitted = Event(), Event()
    release = admission.release_in_transaction

    def paused_release(db, identity, *, queued_only=False):
        if queued_only:
            selected.set()
            assert admitted.wait(timeout=5)
        return release(db, identity, queued_only=queued_only)

    monkeypatch.setattr(admission, "release_in_transaction", paused_release)
    with ThreadPoolExecutor(max_workers=1) as workers:
        cancellation = workers.submit(admission.cancel_task_waiter, ids["a"], "job", 1)
        assert selected.wait(timeout=5)
        admission.release_connection("holder")
        lease = claim(ids["a"])
        assert lease
        admitted.set()
        assert cancellation.result(timeout=5) == 0
    with transaction() as db:
        assert db.get(InferenceAdmission, lease.admission_id).status == "active"


def test_connection_retirement_stops_renewal_without_preventing_confirmed_release(seed):
    assert ticket("running", "run")
    with transaction() as db:
        db.get(Connection, "connection").kind = "claude"
    assert not admission.renew_connection("running")
    assert admission.release_connection("running")


@pytest.mark.parametrize("limit", [0, 9, True, 1.5])
def test_request_cannot_override_saved_concurrency_with_invalid_limits(seed, limit):
    _, ids = planned(seed)
    with pytest.raises(ValueError, match="between 1 and 8"):
        claim(ids["a"], concurrency_limit=limit)
