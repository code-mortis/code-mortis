"""Concurrent workflow plans must not upgrade competing owner foreign-key locks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, BrokenBarrierError

from sqlalchemy import event, select
from test_mortis_review import scenario as scenario  # noqa: F401
from test_workflow_inference import call, workflow  # noqa: F401

from code_mortis.core.db import engine, now, transaction
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    InferenceAdmission,
    Job,
    ReviewRun,
)


def test_concurrent_stage_plans_lock_owner_before_inserting_foreign_keys(workflow):  # noqa: F811
    jobs = {"verification": "parallel-verification", "summary": "parallel-summary"}
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        run.stages = {stage: {"status": "running", "execution": job_id + ":1"} for stage, job_id in jobs.items()}
        db.add_all([Job(id=job_id, kind="verify_fixes" if stage == "verification" else "summarize_review",
                        payload={"run_id": run.id}, status="running", attempts=1,
                        lease_until=now() + timedelta(minutes=5)) for stage, job_id in jobs.items()])

    inserted = Barrier(2)

    def synchronize_inserts(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO analysis_plans "):
            try:
                # Unlocked owners let both inserts take KEY SHARE before both request FOR UPDATE.
                inserted.wait(timeout=2)
            except BrokenBarrierError:
                pass  # A correctly prelocked owner serializes inserts instead.

    event.listen(engine(), "after_cursor_execute", synchronize_inserts)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, workflow, claim=job_id + ":1", stage=stage, task_key=stage)
                       for stage, job_id in jobs.items()]
            assert [future.result(timeout=15) for future in futures] == [({"ok": True}, {"tokens": 12})] * 2
    finally:
        event.remove(engine(), "after_cursor_execute", synchronize_inserts)

    assert len(workflow["wire"]) == 2
    with transaction() as db:
        assert len(list(db.scalars(select(AnalysisPlan)))) == 2
        assert all(row.status == "completed" for row in db.scalars(select(AnalysisTask)))
        attempts = list(db.scalars(select(AnalysisAttempt)))
        assert len(attempts) == 2 and all(row.usage_state == "known" and row.usage_tokens == 12 for row in attempts)
        assert sum(row.charged_tokens for row in db.scalars(select(AnalysisBudget))) == 24
        assert not list(db.scalars(select(InferenceAdmission).where(InferenceAdmission.status == "active")))
