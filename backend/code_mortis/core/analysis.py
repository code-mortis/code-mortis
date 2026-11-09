"""Durable Mortis execution contracts and historical engine identity readers."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from code_mortis.core.db import now, transaction, uid
from code_mortis.core.inference_admission import (
    admit,
    lock_connection,
    release_in_transaction,
    renew_in_transaction,
    validate_limits,
)
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    Connection,
    InferenceAdmission,
    Installation,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityScan,
)
from code_mortis.schemas.analysis import PlanSpec, TaskSpec

OUTPUT_TOKEN_POLICY = "mortis-completion/v1"
RETIRED_ENGINE_MESSAGE = "OCR execution is retired. Its saved plan and results were preserved; explicitly start new work with Mortis."


def output_token_limit(policy):
    """Missing policy retains the original immutable 8,192-token requests."""
    if policy is None:
        return 8192
    if policy in {OUTPUT_TOKEN_POLICY, "ocr-completion/v1"}:
        return 16384
    raise ValueError("The saved output-token policy is unsupported")


def output_token_policy(snapshot=None):
    policy = OUTPUT_TOKEN_POLICY if snapshot is None else snapshot["versions"].get("output_tokens")
    output_token_limit(policy)
    return policy


def engine_snapshot(options):
    if not isinstance(options, dict) or not isinstance(options.get("settings", {}), dict):
        raise ValueError("The stored analysis engine settings are invalid.")
    settings = options.get("settings", {})
    selected = options.get("analysis_engine", settings.get("analysis_engine", "ocr"))
    if not isinstance(selected, str) or selected not in {"ocr", "mortis"}:
        raise ValueError("The stored analysis engine identity is unsupported.")
    if "analysis_engine" in settings and settings["analysis_engine"] != selected:
        raise ValueError("The stored analysis engine snapshot does not match its settings.")
    return {"analysis_engine": selected, "engine_contract": "analysis-engine/v1",
            "planner_version": "mortis-pr-plan/v1" if selected == "mortis" else "m1-foundation/v1"}


def require_available_engine(options):
    if engine_snapshot(options)["analysis_engine"] != "mortis":
        raise ValueError(RETIRED_ENGINE_MESSAGE)


def new_engine_snapshot(options):
    """Default mutable settings for new work; never apply this to a saved run or plan."""
    if not isinstance(options, dict):
        raise ValueError("The repository engine settings are invalid.")
    snapshot = engine_snapshot({"analysis_engine": "mortis", **options})
    require_available_engine(snapshot)
    return snapshot


def require_execution_engine(options):
    """Reject retired saved work without rewriting its engine identity."""
    require_available_engine(options)
    return engine_snapshot(options)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def persist_plan(db, job_id: str, specification: PlanSpec) -> AnalysisPlan:
    """Freeze one deterministic plan per existing durable Job, before any dispatch."""
    specification = PlanSpec.model_validate(specification.model_dump(mode="json"))
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise ValueError("A durable job is required before planning")
    snapshot = specification.model_dump(mode="json")
    snapshot["tasks"] = sorted(snapshot["tasks"], key=lambda task: task["key"])
    for task in snapshot["tasks"]:
        task["dependencies"] = sorted(set(task["dependencies"]))
    identity = fingerprint([job_id, snapshot])
    existing = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == job_id))
    if existing:
        if existing.id != identity or existing.snapshot != snapshot:
            raise ValueError("An existing analysis plan is immutable; create a new job for changed inputs")
        return existing
    if specification.run_id and job.payload.get("run_id") != specification.run_id:
        raise ValueError("The plan review does not match its durable job")
    if specification.scan_id and job.payload.get("scan_id") != specification.scan_id:
        raise ValueError("The plan security scan does not match its durable job")
    if specification.run_id:
        # Lock before the FK insert so concurrent stage plans cannot deadlock upgrading KEY SHARE locks.
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == specification.run_id).with_for_update())
        pr = db.get(PullRequest, run.pr_id) if run else None
        if (not pr or pr.repository_id != specification.repository_id
                or specification.revisions.get("head") != run.head_sha
                or specification.revisions.get("base") != run.base_sha):
            raise ValueError("The plan must match its review repository and pinned revisions")
        expected = (job.payload.get("config", {}) if job.kind == "generate_summary" else run.config).get("connection")
        if specification.connection != expected:
            raise ValueError("The plan connection must match the immutable execution snapshot")
    if specification.scan_id:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == specification.scan_id).with_for_update())
        if (not scan or scan.repository_id != specification.repository_id
                or specification.revisions.get("head") != scan.head_sha):
            raise ValueError("The plan must match its security repository and pinned revision")
        if specification.connection != scan.config.get("connection"):
            raise ValueError("The plan connection must match the immutable execution snapshot")
    plan = AnalysisPlan(id=identity, job_id=job_id, repository_id=specification.repository_id,
                        run_id=specification.run_id, scan_id=specification.scan_id, snapshot=snapshot)
    db.add(plan)
    db.flush()
    task_ids = {task["key"]: fingerprint([identity, task["key"]]) for task in snapshot["tasks"]}
    for task in snapshot["tasks"]:
        db.add(AnalysisTask(id=task_ids[task["key"]], plan_id=identity, key=task["key"], stage=task["stage"],
                            input_hash=task["input_hash"], scope=task["scope"],
                            dependencies=[task_ids[key] for key in task["dependencies"]]))
    for stage, limit in specification.budgets.items():
        db.add(AnalysisBudget(plan_id=identity, stage=stage, token_limit=limit,
                              deadline_at=specification.deadlines.get(stage)))
    db.flush()
    return plan


def append_task(db, job_id: str, job_attempt: int, parent_task_id: str, specification: TaskSpec) -> AnalysisTask:
    """Append a bounded evidence round without rewriting the plan or its assigned scope."""
    specification = TaskSpec.model_validate(specification.model_dump(mode="json"))
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    parent = db.scalar(select(AnalysisTask).where(AnalysisTask.id == parent_task_id).with_for_update())
    plan = db.get(AnalysisPlan, parent.plan_id) if parent else None
    if not plan or plan.job_id != job_id or not owner_live(db, plan) or not job_live(job, job_attempt, now()):
        raise ValueError("The evidence round no longer owns its review")
    if (parent.status != "completed" or not parent.result or parent.result.get("kind") not in {"expansion", "feedback"}
            or specification.stage != parent.stage or specification.scope != parent.scope
            or specification.dependencies != [parent.key]):
        raise ValueError("Evidence rounds must retain a completed parent's exact assignment")
    if db.scalar(select(AnalysisAttempt.id).where(AnalysisAttempt.task_id == parent.id,
                                                  AnalysisAttempt.usage_state == "unknown").limit(1)):
        raise ValueError("Unknown consumption must be acknowledged before another evidence round")
    limit = plan.snapshot["settings"].get("max_evidence_rounds", 0)
    if type(limit) is not int or not 0 <= limit <= 4:
        raise ValueError("Invalid evidence round limit")
    root, depth, seen = parent, 1, set()
    while root.dependencies:
        if len(root.dependencies) != 1 or root.id in seen:
            raise ValueError("Evidence rounds require a single immutable parent chain")
        seen.add(root.id)
        root = db.get(AnalysisTask, root.dependencies[0])
        if root is None or root.plan_id != plan.id:
            raise ValueError("Evidence rounds cannot cross plans")
        depth += 1
    if depth > limit or root.key not in {task["key"] for task in plan.snapshot["tasks"]}:
        raise ValueError("The evidence round limit was reached")
    prefix = f"{root.key}:round:{depth}:"
    if not specification.key.startswith(prefix):
        raise ValueError("The evidence round identity does not match its parent")
    existing = list(db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id,
                                                        AnalysisTask.key.startswith(prefix, autoescape=True))))
    identity = fingerprint([plan.id, specification.key])
    if existing:
        task = existing[0]
        if (len(existing) != 1 or task.id != identity or task.input_hash != specification.input_hash
                or task.scope != specification.scope or task.dependencies != [parent.id]):
            raise ValueError("An evidence selection cannot change after it is frozen")
        return task
    task = AnalysisTask(id=identity, plan_id=plan.id, key=specification.key, stage=parent.stage,
                        input_hash=specification.input_hash, scope=specification.scope, dependencies=[parent.id])
    db.add(task)
    db.flush()
    return task


def workflow_task(db, plan_id, job_attempt, specification: TaskSpec):
    """Freeze discovered assessment/security work without rewriting its owning plan."""
    specification = TaskSpec.model_validate(specification.model_dump(mode="json"))
    plan = db.get(AnalysisPlan, plan_id)
    job = db.scalar(select(Job).where(Job.id == plan.job_id).with_for_update()) if plan else None
    if (not plan or plan.snapshot["settings"].get("workflow_contract") != "mortis-workflow/v1"
            or specification.stage not in plan.snapshot["budgets"] or specification.dependencies
            or not specification.key.startswith("workflow:") or not owner_live(db, plan)
            or not job_live(job, job_attempt, now())):
        raise ValueError("Workflow tasks require their original live plan and stage")
    task = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id,
                                               AnalysisTask.key == specification.key))
    if task:
        if (task.stage != specification.stage or task.input_hash != specification.input_hash
                or task.scope != specification.scope or task.dependencies):
            raise ValueError("Saved workflow task inputs are immutable; start new work for changed evidence")
        return task
    task = AnalysisTask(id=fingerprint([plan.id, specification.key]), plan_id=plan.id,
                        key=specification.key, stage=specification.stage,
                        input_hash=specification.input_hash, scope=specification.scope, dependencies=[])
    db.add(task)
    db.flush()
    return task


@dataclass(frozen=True)
class TaskLease:
    task_id: str
    attempt_id: str
    number: int
    job_id: str
    job_attempt: int
    lease_until: datetime
    admission_id: str | None = None


def job_live(job, attempt, timestamp):
    return bool(job and job.status == "running" and job.attempts == attempt
                and job.lease_until and job.lease_until > timestamp)


def owner_live(db, plan):
    repo = db.get(Repository, plan.repository_id)
    installation = db.get(Installation, repo.installation_id) if repo else None
    connection = db.get(Connection, plan.snapshot["connection"]["id"])
    if (not repo or not repo.active or not installation or not installation.active
            or installation.uninstalled or installation.archived or not connection or connection.kind != "api"):
        return False
    if plan.run_id:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == plan.run_id).with_for_update())
        pr = db.get(PullRequest, run.pr_id) if run else None
        job = db.get(Job, plan.job_id)
        summary_attempt = bool(job and job.kind == "generate_summary" and run
                               and job.payload.get("run_id") == run.id
                               and run.progress.get("summary_retry") == job.id)
        if job and job.kind == "generate_summary":
            permitted_status = summary_attempt and run.status in {"completed", "partial", "failed", "skipped"}
        else:
            permitted_status = run and run.status in {"queued", "running", "blocked"}
        return bool(run and pr and repo.enabled and pr.repository_id == repo.id and not pr.ignored and pr.state == "open"
                    and pr.head_sha == run.head_sha and pr.base_sha == run.base_sha
                    and not run.cancel_requested and permitted_status)
    if plan.scan_id:
        scan = db.scalar(select(SecurityScan).where(SecurityScan.id == plan.scan_id).with_for_update())
        return bool(scan and scan.repository_id == repo.id and not scan.cancel_requested
                    and scan.status in {"queued", "running", "blocked"})
    return True


def mark_uncertain(task, attempt, timestamp):
    # A lost response may have consumed tokens; expiry never releases its reservation.
    task.status = "uncertain"
    attempt.status, attempt.usage_state = "uncertain", "unknown"
    attempt.finished_at = timestamp


def claim_task(task_id: str, job_id: str, job_attempt: int, *, input_tokens: int,
               output_tokens: int, lease_seconds: int = 60, concurrency_limit: int | None = None) -> TaskLease | None:
    """Reserve a whole request atomically. Revalidate external authorization before calling."""
    if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
        raise ValueError("Input and output reservations must be nonnegative whole tokens")
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise ValueError("Task leases must last between 1 and 3600 seconds")
    if concurrency_limit is not None:
        validate_limits(1, concurrency_limit, lease_seconds)
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        task = db.scalar(select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update())
        plan = db.get(AnalysisPlan, task.plan_id) if task else None
        if not plan or plan.job_id != job_id or not owner_live(db, plan):
            return None
        timestamp = now()
        if not job_live(job, job_attempt, timestamp):
            return None
        if task.status == "running":
            prior = db.scalar(select(AnalysisAttempt).where(
                AnalysisAttempt.task_id == task.id, AnalysisAttempt.number == task.attempts))
            if prior and (prior.lease_until <= timestamp or prior.job_attempt != job_attempt):
                mark_uncertain(task, prior, timestamp)
            return None
        if task.status != "pending":
            return None
        if task.dependencies:
            completed = set(db.scalars(select(AnalysisTask.id).where(
                AnalysisTask.id.in_(task.dependencies), AnalysisTask.status == "completed")))
            if completed != set(task.dependencies):
                return None
        budget = db.scalar(select(AnalysisBudget).where(
            AnalysisBudget.plan_id == plan.id, AnalysisBudget.stage == task.stage).with_for_update())
        unknown = db.scalar(select(AnalysisAttempt.id).join(AnalysisTask).where(
            AnalysisTask.plan_id == plan.id, AnalysisTask.stage == task.stage,
            AnalysisAttempt.usage_state == "unknown").limit(1))
        timestamp = now()
        reservation = input_tokens + output_tokens
        if (not job_live(job, job_attempt, timestamp) or unknown or budget.deadline_at and budget.deadline_at <= timestamp
                or budget.token_limit is not None and budget.charged_tokens + reservation > budget.token_limit):
            return None
        expires = min(timestamp + timedelta(seconds=lease_seconds), job.lease_until,
                      budget.deadline_at or job.lease_until)
        run_limit = plan.snapshot["settings"].get("concurrency", 4 if plan.scan_id else 2)
        validate_limits(1, run_limit, lease_seconds)
        if concurrency_limit is not None:
            run_limit = min(run_limit, concurrency_limit)
        run_key = f"review:{plan.run_id}" if plan.run_id else f"scan:{plan.scan_id}" if plan.scan_id else f"job:{job_id}"
        admission_id = fingerprint(["inference", task.id, job_attempt, task.attempts + 1])
        capacity = admit(db, identity=admission_id, connection_id=plan.snapshot["connection"]["id"],
                         run_key=run_key, run_limit=run_limit, lease_until=expires,
                         task_id=task.id, job_id=job_id, job_attempt=job_attempt)
        if not capacity:
            return None
        budget.charged_tokens += reservation
        task.status, task.attempts = "running", task.attempts + 1
        attempt = AnalysisAttempt(id=uid(), task_id=task.id, number=task.attempts, job_attempt=job_attempt,
                                  lease_until=expires, reserved_tokens=reservation)
        db.add(attempt)
        return TaskLease(task.id, attempt.id, task.attempts, job_id, job_attempt, expires, admission_id)


def owned_attempt(db, lease, *, settlement_only=False):
    job = db.scalar(select(Job).where(Job.id == lease.job_id).with_for_update())
    task = db.scalar(select(AnalysisTask).where(AnalysisTask.id == lease.task_id).with_for_update())
    attempt = db.get(AnalysisAttempt, lease.attempt_id)
    if not task or not attempt or attempt.task_id != task.id or attempt.number != lease.number:
        return None
    plan = db.get(AnalysisPlan, task.plan_id)
    live_owner = owner_live(db, plan)
    timestamp = now()
    if (plan.job_id != lease.job_id or not job_live(job, lease.job_attempt, timestamp)
            or not live_owner and not settlement_only or task.attempts != lease.number or task.status != "running"
            or attempt.status != "running" or attempt.job_attempt != lease.job_attempt
            or attempt.lease_until <= timestamp):
        return None
    return task, attempt


def finish_task(lease: TaskLease, *, usage_tokens: int | None, result: dict | None = None,
                error_code: str | None = None, provider_request_id: str | None = None) -> bool:
    """Accept only a caller-validated result; unknown consumption blocks automatic retry."""
    if usage_tokens is not None and (type(usage_tokens) is not int or usage_tokens < 0):
        raise ValueError("Reported usage must be nonnegative whole tokens or unknown")
    if error_code is not None and not re.fullmatch(r"[a-z0-9_]{1,80}", error_code):
        raise ValueError("Use a sanitized diagnostic code, never a provider response")
    if provider_request_id is not None and (len(provider_request_id) > 250 or not provider_request_id.isprintable()):
        raise ValueError("Provider request identity is invalid")
    if result is not None and (not isinstance(result, dict) or error_code is not None):
        raise ValueError("A validated result cannot be combined with a failure")
    with transaction() as db:
        owned = owned_attempt(db, lease, settlement_only=result is None)
        if not owned:
            return False
        task, attempt = owned
        budget = db.scalar(select(AnalysisBudget).where(
            AnalysisBudget.plan_id == task.plan_id, AnalysisBudget.stage == task.stage).with_for_update())
        if lease.admission_id:
            ticket = db.get(InferenceAdmission, lease.admission_id)
            if (not ticket or ticket.task_id != task.id or ticket.job_id != lease.job_id
                    or ticket.job_attempt != lease.job_attempt):
                return False
            if not lock_connection(db, ticket.connection_id, require_api=False):
                return False
            db.refresh(ticket)
            if ticket.status != "active" or ticket.lease_until <= now():
                return False
        timestamp = now()
        if not job_live(db.get(Job, lease.job_id), lease.job_attempt, timestamp) or attempt.lease_until <= timestamp:
            return False
        if usage_tokens is not None:
            budget.charged_tokens += usage_tokens - attempt.reserved_tokens
        attempt.usage_tokens, attempt.usage_state = usage_tokens, "known" if usage_tokens is not None else "unknown"
        attempt.finished_at, attempt.error_code = timestamp, error_code
        attempt.provider_request_id = provider_request_id
        task.result = result
        task.status = "completed" if result is not None else "failed" if usage_tokens is not None else "uncertain"
        attempt.status = task.status
        if lease.admission_id:
            release_in_transaction(db, lease.admission_id)
        return True


def renew_task(lease: TaskLease, *, lease_seconds: int = 60) -> bool:
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise ValueError("Task leases must last between 1 and 3600 seconds")
    with transaction() as db:
        owned = owned_attempt(db, lease)
        if not owned:
            return False
        task, attempt = owned
        budget = db.get(AnalysisBudget, (task.plan_id, task.stage))
        job = db.get(Job, lease.job_id)
        timestamp = now()
        if (not job_live(job, lease.job_attempt, timestamp) or attempt.lease_until <= timestamp
                or budget.deadline_at and budget.deadline_at <= timestamp):
            return False
        expires = min(timestamp + timedelta(seconds=lease_seconds), job.lease_until,
                      budget.deadline_at or job.lease_until)
        if lease.admission_id and not renew_in_transaction(db, lease.admission_id, expires):
            return False
        attempt.lease_until = expires
        return True


def retry_task(task_id: str, job_id: str, job_attempt: int, *, acknowledge_unknown: bool = False) -> bool:
    """An explicit recovery decision; it preserves every earlier attempt and charge."""
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        task = db.scalar(select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update())
        plan = db.get(AnalysisPlan, task.plan_id) if task else None
        if not plan or plan.job_id != job_id or not owner_live(db, plan):
            return False
        timestamp = now()
        if not job_live(job, job_attempt, timestamp):
            return False
        attempt = db.scalar(select(AnalysisAttempt).where(
            AnalysisAttempt.task_id == task.id, AnalysisAttempt.number == task.attempts))
        if task.status == "running" and attempt and (attempt.lease_until <= timestamp or attempt.job_attempt != job_attempt):
            mark_uncertain(task, attempt, timestamp)
        if task.status not in {"failed", "uncertain"} or task.status == "uncertain" and not acknowledge_unknown:
            return False
        if acknowledge_unknown:
            acknowledge_task_usage(db, task)
        task.status = "pending"
        return True


def acknowledge_task_usage(db, task):
    for attempt in db.scalars(select(AnalysisAttempt).where(
            AnalysisAttempt.task_id == task.id, AnalysisAttempt.usage_state == "unknown")):
        attempt.usage_state = "unknown_acknowledged"


def acknowledge_unknown_usage(task_id: str, job_id: str, job_attempt: int) -> bool:
    """Explicitly accept retained uncertain charges, including a completed task's response."""
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        task = db.scalar(select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update())
        plan = db.get(AnalysisPlan, task.plan_id) if task else None
        if not plan or plan.job_id != job_id or not owner_live(db, plan) or not job_live(job, job_attempt, now()):
            return False
        acknowledge_task_usage(db, task)
        return True


def cancel_tasks(job_id: str) -> None:
    """Stop future claims while retaining validated results and uncertain consumption."""
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job:
            return
        cancel_tasks_in_transaction(db, job_id)


def cancel_tasks_in_transaction(db, job_id):
    """The caller holds the job lock; completed outputs and all charges remain intact."""
    plan = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == job_id))
    if not plan:
        return
    for ticket in db.scalars(select(InferenceAdmission).where(
            InferenceAdmission.job_id == job_id, InferenceAdmission.status == "queued")):
        release_in_transaction(db, ticket.id, queued_only=True)
    for task in db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id).with_for_update()):
        if task.status == "running":
            attempt = db.scalar(select(AnalysisAttempt).where(
                AnalysisAttempt.task_id == task.id, AnalysisAttempt.number == task.attempts))
            mark_uncertain(task, attempt, now())
        if task.status != "completed":
            task.status = "cancelled"
