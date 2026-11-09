"""A known truncation can narrow work without erasing failures or relaxing admission."""

import copy

import pytest
from sqlalchemy import select
from test_analysis_foundation import claim, planned, specification

from code_mortis.core import analysis
from code_mortis.core.db import transaction
from code_mortis.models import AnalysisAttempt, AnalysisBudget, AnalysisPlan, AnalysisTask, Job
from code_mortis.schemas.analysis import TaskSpec


def failed_parent(seed, *, code="output_limit", usage=25, enabled=True, count=3, policy=analysis.OUTPUT_RECOVERY_POLICY):
    versions = specification().versions
    if enabled:
        versions["output_recovery"] = policy
    scope = [{"unit_key": f"u{i}", "path": "app.py", "start_line": i, "end_line": i} for i in range(1, count + 1)]
    plan_id, tasks = planned(seed, versions=versions, tasks=[{
        "key": "a", "stage": "analysis", "input_hash": "d" * 64, "scope": scope}])
    lease = claim(tasks["a"])
    assert analysis.finish_task(lease, usage_tokens=usage, error_code=code)
    children = [TaskSpec(key=f"a:unit:{analysis.fingerprint(unit)[:16]}", stage="analysis",
                         input_hash=analysis.fingerprint([unit]), scope=[unit], dependencies=["a"]) for unit in scope]
    return plan_id, tasks["a"], children


def split(parent_id, children):
    with transaction() as db:
        return [task.id for task in analysis.split_task(db, "analysis-job", 1, parent_id, children)]


def test_split_preserves_failed_parent_exact_partition_charges_and_immutable_replay(seed):
    plan_id, parent_id, children = failed_parent(seed)
    with transaction() as db:
        frozen = copy.deepcopy(db.get(AnalysisPlan, plan_id).snapshot)
    ids = split(parent_id, children)
    assert split(parent_id, children) == ids
    for identity in ids:
        lease = claim(identity, input_tokens=5, output_tokens=5)
        assert lease and analysis.finish_task(lease, usage_tokens=10, result={"kind": "final"})
    with transaction() as db:
        assert db.get(AnalysisPlan, plan_id).snapshot == frozen
        assert db.get(AnalysisTask, parent_id).status == "failed"
        assert db.get(AnalysisTask, parent_id).result is None
        assert db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens == 55
        attempts = list(db.scalars(select(AnalysisAttempt)))
        assert len(attempts) == 4 and sum(row.usage_tokens for row in attempts) == 55
    with pytest.raises(ValueError, match="immutable"):
        split(parent_id, [children[0].model_copy(update={"input_hash": "f" * 64}), *children[1:]])


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign_scope", "stage", "dependency", "key"])
def test_recovery_cannot_drop_duplicate_expand_or_reassign_units(seed, change):
    _, parent_id, children = failed_parent(seed)
    if change == "missing":
        children.pop()
    elif change == "duplicate":
        children[-1] = children[0]
    else:
        updates = {"foreign_scope": {"scope": [{"unit_key": "foreign", "path": "secrets.py"}]},
                   "stage": {"stage": "summary"}, "dependency": {"dependencies": []}, "key": {"key": "arbitrary"}}
        children[0] = children[0].model_copy(update=updates[change])
    with pytest.raises(ValueError, match="exact assigned units"):
        split(parent_id, children)
    with transaction() as db:
        assert len(list(db.scalars(select(AnalysisTask)))) == 1


@pytest.mark.parametrize("options", [{"usage": None}, {"code": "provider_http_500"},
                                     {"code": "incomplete"}, {"enabled": False}])
def test_only_explicitly_enabled_known_output_limits_allow_recovery(seed, options):
    _, parent_id, children = failed_parent(seed, **options)
    with pytest.raises(ValueError, match="known output-limit"):
        split(parent_id, children)


def test_recovery_admission_keeps_failed_charge_and_rejects_exhausted_budget(seed):
    plan_id, parent_id, children = failed_parent(seed, usage=80)
    ids = split(parent_id, children)
    assert claim(ids[0]) is None
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens == 80
        assert db.get(AnalysisTask, ids[0]).status == "pending"


def test_recovery_cannot_bypass_parent_state_or_current_job_owner(seed):
    _, parent_id, children = failed_parent(seed)
    ids = split(parent_id, children)
    with transaction() as db:
        db.get(AnalysisTask, parent_id).status = "uncertain"
    assert claim(ids[0]) is None
    with transaction() as db:
        db.get(AnalysisTask, parent_id).status = "failed"
        db.get(Job, "analysis-job").attempts = 2
    assert claim(ids[0]) is None
    with pytest.raises(ValueError, match="owns its review"):
        split(parent_id, children)


@pytest.mark.parametrize("policy", ["split-units/v1", analysis.OUTPUT_RECOVERY_POLICY])
def test_single_unit_recovery_is_versioned_and_cannot_recurse(seed, policy):
    _, parent, children = failed_parent(seed, count=1, policy=policy)
    if policy == "split-units/v1":
        with pytest.raises(ValueError, match="known output-limit"):
            split(parent, children)
        return
    child = split(parent, children)[0]
    lease = claim(child, input_tokens=5, output_tokens=5)
    assert lease and analysis.finish_task(lease, usage_tokens=10, error_code="output_limit")
    with pytest.raises(ValueError, match="known output-limit"):
        split(child, [children[0].model_copy(update={
            "key": f"{children[0].key}:unit:{analysis.fingerprint(children[0].scope[0])[:16]}",
            "dependencies": [children[0].key]})])
