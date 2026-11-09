"""Factories for persisted review evidence used across workflow regressions."""

from code_mortis.core.security import digest


def file_limit_result(paths, maximum):
    if maximum is None or len(paths) <= maximum:
        return None
    message = f"{len(paths)} reviewable files exceeds the maximum of {maximum}."
    selected = [{"item_id": digest(path), "path": path} for path in paths]
    return "skipped", {
        "manifest": {"schema_version": "code-mortis.file-limit/v1", "terminal_state": "skipped",
            "coverage": {"selected": selected, "completed": [], "reused": [], "failed": [],
                         "waived": [{**item, "reason": message} for item in selected]}},
        "message": message, "summary": {"findings": 0, "total_tokens": 0},
    }, []


def ask_security(*args, **kwargs):
    """Exercise the security workflow with an explicit Mortis gateway callback."""
    from code_mortis.core import workflow_inference
    from code_mortis.security_scans import engine

    def infer(context, *, schema, instructions, validator, budget, timeout, round):
        if budget is not None and budget <= 0:
            from code_mortis.core.errors import InferenceOutputError

            raise InferenceOutputError("No allowance remains for new inference.", {"tokens": 0}, "skipped")
        return workflow_inference.execute(args[1], args[2], context, schema=schema, instructions=instructions,
            validator=validator, token_budget=budget, timeout=timeout, stopped=args[7],
            claim="fixture:1", stage="security", task_key=f"fixture:{round}")

    return engine.ask(*args, infer=kwargs.pop("infer", infer), **kwargs)


def perform_security(identity, claim):
    """Give workflow fixtures the same durable Job claim required in production."""
    from datetime import timedelta

    from sqlalchemy import select

    from code_mortis.core.db import now, transaction
    from code_mortis.models import Job
    from code_mortis.security_scans import service

    with transaction() as db:
        job = db.scalar(select(Job).where(Job.kind == "security_scan", Job.payload["scan_id"].as_string() == identity))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=10)
        execution = f"{job.id}:1"
    return service.perform(identity, execution)
