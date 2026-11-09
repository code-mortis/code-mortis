"""Durable evidence rounds preserve ownership, frozen context and bounded task chains."""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from sqlalchemy import event, select
from test_analysis_foundation import claim, specification

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
)
from code_mortis.schemas.analysis import TaskSpec


@pytest.fixture
def expansion(seed):
    settings = {"concurrency": 2, "language": "English", "max_evidence_rounds": 2}
    spec = specification(run_id="expansion-run", settings=settings, budgets={"analysis": None, "summary": None})
    with transaction() as db:
        db.add(ReviewRun(id="expansion-run", pr_id="pr", actor_id=7, trigger="manual", dedup_key="expansion-run",
                         base_sha="b" * 40, head_sha="a" * 40,
                         config={"connection": spec.connection}, status="running"))
        db.add(Job(id="analysis-job", kind="review", payload={"run_id": "expansion-run"}, status="running",
                   attempts=1, lease_until=now() + timedelta(minutes=10)))
        db.flush()
        plan = analysis.persist_plan(db, "analysis-job", spec)
        parent = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id, AnalysisTask.key == "a"))
        return plan.id, parent.id


def complete(task_id, *, kind="expansion", usage_tokens=5):
    lease = claim(task_id)
    assert lease
    result = {"kind": kind, "required_sources": [{"revision": "current", "path": "src/support.py"}]}
    assert analysis.finish_task(lease, usage_tokens=usage_tokens, result=result)
    return lease


def child(parent_id, *, round=1, **changes):
    with transaction() as db:
        parent = db.get(AnalysisTask, parent_id)
        root = parent.key.split(":round:", 1)[0]
        values = {"key": f"{root}:round:{round}:context", "stage": parent.stage,
                  "scope": copy.deepcopy(parent.scope), "input_hash": analysis.fingerprint([parent_id, round]),
                  "dependencies": [parent.key]}
    return TaskSpec.model_validate({**values, **changes})


def append(parent_id, spec=None, *, job_id="analysis-job", attempt=1):
    spec = spec or child(parent_id)
    with transaction() as db:
        return analysis.append_task(db, job_id, attempt, parent_id, spec).id


@pytest.mark.parametrize("kind", ["expansion", "feedback"])
def test_completed_normalized_evidence_or_feedback_appends_exact_scope(expansion, kind):
    plan_id, parent_id = expansion
    lease = complete(parent_id, kind=kind)
    spec = child(parent_id)
    with transaction() as db:
        snapshot = copy.deepcopy(db.get(AnalysisPlan, plan_id).snapshot)
        parent_result = copy.deepcopy(db.get(AnalysisTask, parent_id).result)
        charged = db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens
    identity = append(parent_id, spec)
    with transaction() as db:
        task = db.get(AnalysisTask, identity)
        assert task.id == analysis.fingerprint([plan_id, spec.key])
        assert task.plan_id == plan_id and task.stage == "analysis" and task.status == "pending"
        assert task.scope == spec.scope and task.input_hash == spec.input_hash
        assert task.dependencies == [parent_id]
        assert db.get(AnalysisPlan, plan_id).snapshot == snapshot
        assert db.get(AnalysisTask, parent_id).result == parent_result
        assert db.get(AnalysisAttempt, lease.attempt_id).usage_tokens == 5
        assert db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens == charged
    assert claim(identity)


@pytest.mark.parametrize("status,kind", [("pending", "expansion"), ("running", "feedback"),
                                       ("failed", "expansion"), ("cancelled", "feedback"),
                                       ("completed", "completed"), ("completed", None)])
def test_only_completed_expansion_or_feedback_parents_can_append(expansion, status, kind):
    _, parent_id = expansion
    with transaction() as db:
        parent = db.get(AnalysisTask, parent_id)
        parent.status, parent.result = status, {"kind": kind}
    with pytest.raises(ValueError, match="completed parent's exact assignment"):
        append(parent_id)


@pytest.mark.parametrize("changes", [
    {"stage": "summary"}, {"scope": [{"path": "src/other.py"}]},
    {"scope": [{"path": "src/a.py", "source": "substituted"}]},
    {"dependencies": []}, {"dependencies": ["b"]}, {"dependencies": ["a", "b"]},
])
def test_round_cannot_expand_assignment_or_change_stage_or_parent(expansion, changes):
    _, parent_id = expansion
    complete(parent_id)
    with pytest.raises(ValueError, match="exact assignment"):
        append(parent_id, child(parent_id, **changes))


def test_task_id_is_not_accepted_in_place_of_parent_key(expansion):
    _, parent_id = expansion
    complete(parent_id)
    with pytest.raises(ValueError, match="exact assignment"):
        append(parent_id, child(parent_id, dependencies=[parent_id]))


def test_existing_round_is_idempotent_and_context_selection_cannot_fork(expansion):
    plan_id, parent_id = expansion
    complete(parent_id)
    frozen = child(parent_id)
    identity = append(parent_id, frozen)
    assert append(parent_id, frozen) == identity
    for changes in ({"key": "a:round:1:different-context"}, {"input_hash": "f" * 64}):
        with pytest.raises(ValueError, match="cannot change after it is frozen"):
            append(parent_id, frozen.model_copy(update=changes))
    with transaction() as db:
        children = [task for task in db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id))
                    if task.dependencies == [parent_id] and task.key.startswith("a:round:")]
        assert len(children) == 1 and children[0].id == identity


def test_evidence_rounds_follow_one_bounded_chain(expansion):
    _, parent_id = expansion
    complete(parent_id)
    first = append(parent_id)
    complete(first, kind="feedback")
    second = append(first, child(first, round=2))
    complete(second)
    with pytest.raises(ValueError, match="round limit was reached"):
        append(second, child(second, round=3))
    with transaction() as db:
        assert db.get(AnalysisTask, second).dependencies == [first]
        assert db.get(AnalysisTask, first).dependencies == [parent_id]
        assert db.get(AnalysisTask, parent_id).dependencies == []


@pytest.mark.parametrize("dependencies", [["a"], ["a", "b"], ["b"]])
def test_plan_dependencies_do_not_change_evidence_root_or_round_limit(seed, dependencies):
    from test_analysis_foundation import planned

    tasks = [task.model_dump(mode="json") for task in specification().tasks]
    tasks[1]["dependencies"] = ["a"]
    tasks[2]["dependencies"] = dependencies
    plan_id, identities = planned(seed, tasks=tasks, settings={"concurrency": 2, "max_evidence_rounds": 2},
                                  budgets={"analysis": None, "summary": None})
    complete(identities["a"])
    if "b" in dependencies:
        complete(identities["b"])
    parent_id = identities["summary"]
    complete(parent_id)
    first = append(parent_id)
    assert append(parent_id) == first
    complete(first)
    second = append(first, child(first, round=2))
    complete(second)
    with pytest.raises(ValueError, match="round limit was reached"):
        append(second, child(second, round=3))
    with transaction() as db:
        assert db.get(AnalysisTask, first).key == "summary:round:1:context"
        assert db.get(AnalysisTask, second).key == "summary:round:2:context"
        assert db.get(AnalysisTask, first).dependencies == [parent_id]
        assert db.get(AnalysisTask, parent_id).dependencies == [identities[key] for key in dependencies]
        assert db.get(AnalysisPlan, plan_id).snapshot["tasks"][2]["dependencies"] == dependencies


@pytest.mark.parametrize("key", ["a:round:0:context", "a:round:2:context", "b:round:1:context", "arbitrary",
                                "a:round:1:context:unit:forged"])
def test_evidence_round_identity_must_name_its_root_and_exact_depth(expansion, key):
    _, parent_id = expansion
    complete(parent_id)
    with pytest.raises(ValueError, match="identity does not match its parent"):
        append(parent_id, child(parent_id, key=key))


@pytest.mark.parametrize("limit", [None, True, -1, 5, 1.5])
def test_invalid_evidence_limits_never_start_another_round(seed, limit):
    from test_analysis_foundation import planned

    _, tasks = planned(seed, settings={"concurrency": 2, "max_evidence_rounds": limit})
    complete(tasks["a"])
    with pytest.raises(ValueError, match="Invalid evidence round limit"):
        append(tasks["a"])


def test_evidence_rounds_are_disabled_when_not_explicitly_enabled(seed):
    from test_analysis_foundation import planned

    _, tasks = planned(seed)
    complete(tasks["a"])
    with pytest.raises(ValueError, match="round limit was reached"):
        append(tasks["a"])


@pytest.mark.parametrize("change", ["attempt", "expired", "job_finished", "cancel", "head", "base", "disabled"])
def test_stale_jobs_and_revoked_review_ownership_cannot_append(expansion, change):
    _, parent_id = expansion
    complete(parent_id)
    with transaction() as db:
        if change == "attempt":
            db.get(Job, "analysis-job").attempts = 2
        elif change == "expired":
            db.get(Job, "analysis-job").lease_until = now() - timedelta(seconds=1)
        elif change == "job_finished":
            db.get(Job, "analysis-job").status = "done"
        elif change == "cancel":
            db.get(ReviewRun, "expansion-run").cancel_requested = True
        elif change in {"head", "base"}:
            setattr(db.get(PullRequest, "pr"), f"{change}_sha", "e" * 40)
        else:
            db.get(Repository, 22).enabled = False
    with pytest.raises(ValueError, match="no longer owns"):
        append(parent_id)


def test_round_cannot_borrow_a_parent_from_another_job_or_plan(expansion):
    _, parent_id = expansion
    complete(parent_id)
    with transaction() as db:
        db.add(Job(id="other-job", kind="review", payload={}, status="running", attempts=1,
                   lease_until=now() + timedelta(minutes=10)))
        db.flush()
        analysis.persist_plan(db, "other-job", specification(settings={"max_evidence_rounds": 2}))
    with pytest.raises(ValueError, match="no longer owns"):
        append(parent_id, job_id="other-job")
    with pytest.raises(ValueError, match="no longer owns"):
        append("missing-parent", child(parent_id))


@pytest.mark.parametrize("history", ["foreign_plan", "multiple_parents", "cycle"])
def test_malformed_history_cannot_cross_plans_or_create_non_linear_rounds(expansion, history):
    plan_id, parent_id = expansion
    complete(parent_id)
    malformed_id = analysis.fingerprint([plan_id, "malformed"])
    with transaction() as db:
        dependencies = [parent_id, parent_id] if history == "multiple_parents" else [malformed_id]
        if history == "foreign_plan":
            db.add(Job(id="other-job", kind="review", payload={}))
            db.flush()
            foreign = analysis.persist_plan(db, "other-job", specification())
            dependencies = [db.scalar(select(AnalysisTask.id).where(AnalysisTask.plan_id == foreign.id,
                                                                    AnalysisTask.key == "a"))]
        db.add(AnalysisTask(id=malformed_id, plan_id=plan_id, key="a:round:1:malformed", stage="analysis",
                            input_hash="d" * 64, scope=[{"path": "src/a.py"}], dependencies=dependencies,
                            status="completed", result={"kind": "expansion"}))
    message = "cannot cross plans" if history == "foreign_plan" else "single immutable parent chain"
    with pytest.raises(ValueError, match=message):
        append(malformed_id, child(malformed_id, round=2))


def test_unknown_consumption_requires_explicit_acknowledgement_before_expansion(expansion):
    plan_id, parent_id = expansion
    lease = complete(parent_id, usage_tokens=None)
    with pytest.raises(ValueError, match="Unknown consumption must be acknowledged"):
        append(parent_id)
    assert analysis.acknowledge_unknown_usage(parent_id, "analysis-job", 1)
    identity = append(parent_id)
    assert claim(identity)
    with transaction() as db:
        attempt = db.get(AnalysisAttempt, lease.attempt_id)
        assert attempt.usage_state == "unknown_acknowledged" and attempt.usage_tokens is None
        assert db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens == 80
        assert db.get(AnalysisTask, parent_id).result["kind"] == "expansion"


def test_concurrent_append_of_same_parent_creates_one_identical_pending_task(expansion):
    plan_id, parent_id = expansion
    complete(parent_id)
    spec, barrier = child(parent_id), Barrier(2)
    def concurrent():
        barrier.wait(timeout=3)
        return append(parent_id, spec)
    with ThreadPoolExecutor(max_workers=2) as pool:
        identities = list(pool.map(lambda _: concurrent(), range(2)))
    assert identities[0] == identities[1]
    with transaction() as db:
        children = list(db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id,
                                                             AnalysisTask.key == spec.key)))
        assert len(children) == 1 and children[0].dependencies == [parent_id]


def test_owner_lock_wait_cannot_append_after_job_lease_expires(expansion, monkeypatch):
    _, parent_id = expansion
    complete(parent_id)
    spec, reached, advanced = child(parent_id), Event(), Event()
    timestamp = now()
    monkeypatch.setattr(analysis, "now", lambda: timestamp + timedelta(seconds=2 if advanced.is_set() else 0))
    with transaction() as db:
        db.get(Job, "analysis-job").lease_until = timestamp + timedelta(seconds=1)

    def querying(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM review_runs" in statement and "FOR UPDATE" in statement:
            reached.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction() as db:
            db.scalar(select(ReviewRun).where(ReviewRun.id == "expansion-run").with_for_update())
            event.listen(engine(), "before_cursor_execute", querying)
            try:
                future = pool.submit(append, parent_id, spec)
                assert reached.wait(3), "The evidence round must wait on the current review owner"
                advanced.set()
            finally:
                event.remove(engine(), "before_cursor_execute", querying)
        with pytest.raises(ValueError, match="no longer owns"):
            future.result(timeout=3)
