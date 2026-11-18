"""Durable structured calls for existing review and security workflow state machines."""

import asyncio
import copy
import json
import time
from datetime import timedelta

from sqlalchemy import select

from code_mortis.api_tokens import provenance as pat
from code_mortis.core import analysis, file_policy, inference_admission
from code_mortis.core.db import now, transaction
from code_mortis.core.errors import InferenceOutputError, ReviewStopped
from code_mortis.core.review_results import evidence_bundle
from code_mortis.core.security import unseal
from code_mortis.integrations import github, llm
from code_mortis.mcp import provenance as mcp
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisPlan,
    AnalysisTask,
    Connection,
    Installation,
    Job,
    Membership,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityScan,
    User,
)
from code_mortis.schemas.analysis import PlanSpec, TaskSpec
from code_mortis.schemas.inference import Message, Request

CONTRACT = "mortis-workflow/v1"


def schema_for_transport(schema):
    schema = copy.deepcopy(schema)

    def strict(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            value.pop("default", None)
            for child in value.values():
                strict(child)
        elif isinstance(value, list):
            for child in value:
                strict(child)

    strict(schema)
    return schema


def output_token_policy(job_id, config=None):
    with transaction() as db:
        plan = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == job_id))
        return analysis.output_token_policy(plan.snapshot) if plan else analysis.configured_output_policy(config or {})


def execute(connection, credentials, context, *, instructions, schema, validator, claim, stage, task_key,
            run_id=None, scan_id=None, repository_id=None, revisions=None, stopped=lambda: False,
            authorize=None, effort="medium", token_budget=None, timeout=None):
    from code_mortis.reviews import context as review_context
    from code_mortis.reviews.service import authorize_connection, execution_identity, target_matches

    try:
        job_id, attempt = claim.rsplit(":", 1)
        job_attempt = int(attempt)
    except (AttributeError, ValueError) as exc:
        raise ValueError("A structured workflow requires a durable job claim") from exc
    if token_budget is not None and (type(token_budget) is not int or token_budget < 0):
        raise ValueError("The remaining token allowance must be nonnegative or unlimited")
    if timeout is not None and (type(timeout) not in {int, float} or not 0 < timeout < float("inf")):
        raise ValueError("The structured workflow timeout must be a positive finite number of seconds")
    dispatch_deadline = time.monotonic() + timeout if timeout is not None else None
    supplied = review_context.sanitize(context, credentials, stopped)
    content = evidence_bundle(supplied) if "sources" in supplied and "diff" in supplied else json.dumps(supplied, ensure_ascii=False)
    request = Request(instructions=review_context.sanitize(instructions, credentials, stopped),
        messages=[Message(role="user", content=content)],
        output_schema=schema_for_transport(schema), max_output_tokens=analysis.output_token_limit(None), effort=effort,
        session_id=analysis.fingerprint([job_id, stage, task_key]))
    key = f"workflow:{stage}:" + analysis.fingerprint(task_key)

    def owner(db):
        job = db.get(Job, job_id)
        if not analysis.job_live(job, job_attempt, now()):
            raise ReviewStopped("The workflow job lost its execution claim")
        run = db.get(ReviewRun, run_id) if run_id else None
        scan = db.get(SecurityScan, scan_id) if scan_id else None
        if run:
            if job.payload.get("run_id") != run.id or run.cancel_requested:
                raise ReviewStopped("The review no longer owns this job")
            config = job.payload["config"] if job.kind == "generate_summary" else run.config
            pr = db.get(PullRequest, run.pr_id)
            repo = db.get(Repository, pr.repository_id)
            actor_id, trigger = ((job.payload.get("actor_id"), "manual") if job.kind == "generate_summary"
                                 else execution_identity(run))
            target = {"base": run.base_sha, "head": run.head_sha}
            workflow_stages = {"assess_candidates": "assessment", "verify_fixes": "verification", "summarize_review": "summary"}
            if job.kind in workflow_stages:
                state = run.stages.get(workflow_stages[job.kind], {})
                if stage != workflow_stages[job.kind] or state.get("execution") != claim or state.get("status") != "running":
                    raise ReviewStopped("The review stage lost its execution claim")
            elif job.kind == "generate_summary":
                if stage != "summary" or run.progress.get("summary_retry") != job.id:
                    raise ReviewStopped("The summary retry was replaced")
            elif job.kind != "review" or run.progress.get("execution") != claim or stage not in {"assessment", "verification", "summary"}:
                raise ReviewStopped("The review no longer owns this stage")
            if (not target_matches(run, pr.head_sha, pr.base_sha, pr.state) or pr.ignored
                    or not repo.enabled or (job.kind != "generate_summary" and run.status != "running")):
                raise ReviewStopped("The review target is no longer available")
        elif scan:
            if (job.kind != "security_scan" or job.payload.get("scan_id") != scan.id or scan.cancel_requested
                    or scan.status != "running" or scan.progress.get("execution") != claim or stage != "security"):
                raise ReviewStopped("The security scan no longer owns this job")
            config, actor_id, trigger = scan.config, scan.actor_id, "manual"
            repo = db.get(Repository, scan.repository_id)
            target = {"head": scan.head_sha}
        elif job.kind == "generate_background" and not run_id and not scan_id:
            config = job.payload
            if stage != "summary" or config.get("repository_id") != repository_id or config.get("source_sha") != (revisions or {}).get("head"):
                raise ValueError("Background evidence must match the pinned job source")
            repo = db.get(Repository, repository_id)
            actor_id, trigger, target = config.get("actor_id"), "manual", revisions
        else:
            raise ValueError("The workflow owner is unavailable")
        if analysis.engine_snapshot(config)["analysis_engine"] != "mortis" or config["connection"] != connection:
            raise ValueError("The workflow must retain its selected Mortis shared connection")
        return job, run, scan, repo, actor_id, trigger, config, target

    def check_local():
        if stopped():
            raise ReviewStopped("The structured workflow was cancelled or superseded")
        with transaction() as db:
            job, run, scan, repo, actor_id, trigger, config, target = owner(db)
            installation = db.get(Installation, repo.installation_id) if repo else None
            if (not repo or not repo.active or not installation or not installation.active
                    or installation.archived or installation.uninstalled):
                raise ReviewStopped("The repository installation is no longer active")
            selected = db.get(Connection, connection["id"])
            if (selected is None or selected.kind != "api" or any(getattr(selected, field) != connection.get(field)
                    for field in ("kind", "owner_id", "provider", "protocol", "base_url"))):
                raise ReviewStopped("The selected shared connection changed")
            current_credentials = json.loads(unseal(selected.secret))
            if any(current_credentials.get(field, default) != credentials.get(field, default)
                   for field, default in (("api_key", ""), ("extra_headers", {}))):
                raise ReviewStopped("The shared connection credentials changed")
            actor = db.get(User, actor_id) if actor_id else None
            if actor_id and (not actor or not actor.enabled or not db.get(Membership, actor_id)):
                raise ReviewStopped("The requesting user no longer has access")
            if job.kind == "generate_background" and (not actor or db.get(Membership, actor_id).role != "admin"):
                raise ReviewStopped("Background generation requires administrator access")
            if (mcp.revoked(db, mcp.provenance_from_payload(job.payload) or (mcp.provenance_from_run(run) if run else mcp.provenance_from_scan(scan) if scan else None), repository_id=repo.id)
                    or pat.revoked(db, pat.origin_for(job.payload, run or scan), repository_id=repo.id, network=False)):
                raise ReviewStopped("The originating permission was revoked")
            authorize_connection(db, selected, repo, trigger, actor, check_repository=False)

    def fresh_authority():
        check_local()
        if authorize:
            authorize()
        with transaction() as db:
            job, run, scan, repo, actor_id, trigger, config, target = owner(db)
            actor = db.get(User, actor_id) if actor_id else None
            if actor:
                github.authorize_repo(db, actor, repo.full_name, write=True)
            token = github.installation_token(db, repo.installation_id) if run else None
            pr = db.get(PullRequest, run.pr_id) if run else None
        if run:
            remote = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
            if not target_matches(run, remote["head"]["sha"], remote["base"]["sha"], remote["state"]):
                raise ReviewStopped("The PR changed before evidence could be accepted")

    check_local()
    with transaction() as db:
        db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        job, run, scan, repo, actor_id, trigger, config, target = owner(db)
        plan = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == job_id))
        token_policy = analysis.output_token_policy(plan.snapshot) if plan else analysis.configured_output_policy(config)
        workflow_budget = token_policy == analysis.WORKFLOW_OUTPUT_POLICY
        if plan is None:
            options = dict(config.get("settings", config.get("limits", {})))
            if scan:
                budgets = {"security": options.get("token_budget")}
                duration, start = (None if workflow_budget else options.get("timeout_seconds")), scan.started_at
                policy = {"scanner_policy": config["policy"]}
            elif run:
                budgets = {"analysis": options.get("token_budget"), "assessment": options.get("assessment_token_budget"),
                           "verification": options.get("verification_token_budget"), "summary": options.get("summary_token_budget", 60000)}
                duration = options.get("summary_timeout_seconds", 180) if stage == "summary" else options.get("timeout_seconds")
                start = now() if stage == "summary" else run.started_at
                policy = file_policy.policy(options)
            else:
                budgets, duration, start = {"summary": options.get("token_budget")}, options.get("timeout_seconds"), now()
                policy = config.get("file_policy", file_policy.policy({}))
            if stage not in budgets:
                raise ValueError("The stage is not supported by this workflow")
            deadline = start + timedelta(seconds=duration) if duration is not None else None
            if timeout is not None and not (scan and workflow_budget):
                deadline = min(deadline, now() + timedelta(seconds=timeout)) if deadline else now() + timedelta(seconds=timeout)
            specification = PlanSpec(repository_id=repo.id, run_id=run_id, scan_id=scan_id,
                initiator_id=actor_id, authorization={"actor_id": actor_id, "trigger": trigger}, trigger=trigger,
                connection=connection, revisions=target,
                versions={**{key: CONTRACT for key in ("engine", "planner", "prompt", "rules", "schema", "file_policy")},
                          **({"output_tokens": token_policy} if token_policy else {})},
                settings={**options, "workflow_contract": CONTRACT}, file_policy=policy,
                selected=[], exclusions=[], graph={}, budgets=budgets,
                deadlines={stage: deadline} if deadline else {}, tasks=[])
            plan = analysis.persist_plan(db, job_id, specification)
        if (plan.snapshot["connection"] != connection or any(plan.snapshot["revisions"].get(k) != v for k, v in target.items())
                or plan.run_id != run_id or plan.scan_id != scan_id or plan.snapshot["initiator_id"] != actor_id
                or {key: value for key, value in plan.snapshot["settings"].items()
                    if key not in {"workflow_contract", "max_evidence_rounds"}} != config.get("settings", config.get("limits", {}))):
            raise ValueError("The existing workflow plan has a different owner or immutable source")
        request = request.model_copy(update={"max_output_tokens": analysis.output_token_limit(token_policy)})
        spec = TaskSpec(key=key, stage=stage, input_hash=llm.input_fingerprint(request),
                        scope=[{"identity": analysis.fingerprint(task_key), "context": analysis.fingerprint(supplied)}])
        task = analysis.workflow_task(db, plan.id, job_attempt, spec)
        plan_id, task_id = plan.id, task.id

    def state():
        with transaction() as db:
            task = db.get(AnalysisTask, task_id)
            attempts = list(db.scalars(select(AnalysisAttempt).where(AnalysisAttempt.task_id == task_id)))
            amount = sum(row.usage_tokens for row in attempts) if all(row.usage_state == "known" for row in attempts) else None
            budget = db.get(AnalysisBudget, (plan_id, stage))
            unknown = db.scalar(select(AnalysisAttempt.id).join(AnalysisTask).where(AnalysisTask.plan_id == plan_id,
                AnalysisTask.stage == stage, AnalysisAttempt.usage_state == "unknown").limit(1))
            running = db.scalar(select(AnalysisTask.id).where(AnalysisTask.plan_id == plan_id,
                AnalysisTask.stage == stage, AnalysisTask.status == "running").limit(1))
            return task.status, task.result, amount, budget, bool(unknown), bool(running)

    def validate(response):
        try:
            result = validator(response.output, supplied)
            if not isinstance(result, dict):
                raise ValueError("Workflow validation must return an object")
            return {"kind": "validated", "result": review_context.sanitize(result, credentials, stopped)}
        except ValueError as exc:
            feedback = getattr(exc, "feedback", None)
            return {"kind": "invalid", "feedback": review_context.sanitize(feedback, credentials, stopped),
                    "message": "The provider result did not satisfy the workflow evidence contract"}

    async def dispatch():
        until = dispatch_deadline if dispatch_deadline is not None else float("inf")
        if not workflow_budget:
            until = min(time.monotonic() + 60, until)
        async with llm.http_client() as client:
            try:
                while True:
                    await asyncio.to_thread(check_local)
                    status, result, amount, budget, unknown, running = await asyncio.to_thread(state)
                    if status == "completed":
                        await asyncio.to_thread(fresh_authority)
                        return result, {"tokens": amount}
                    if status not in {"pending", "running"}:
                        raise InferenceOutputError("The saved request failed or its consumption is uncertain", {"tokens": amount})
                    required = llm.reservation(connection, request) + (
                        max(1, request.max_output_tokens) if workflow_budget else request.max_output_tokens)
                    if status == "pending" and token_budget is not None and required > token_budget:
                        raise InferenceOutputError("The request exceeds the remaining workflow allowance", {"tokens": 0})
                    deadline = budget.deadline_at
                    if unknown or deadline and now() >= deadline:
                        raise InferenceOutputError("The stage deadline or unknown consumption prevents inference", {"tokens": amount})
                    if (budget.token_limit is not None and budget.charged_tokens + required > budget.token_limit and not running):
                        raise InferenceOutputError("The stage token budget cannot admit this request", {"tokens": amount})
                    response = await llm.execute_task(task_id, job_id, job_attempt, request, credentials, client=client,
                        authorize=fresh_authority, authorize_admission=check_local, validate_result=validate, stopped=stopped,
                        timeout=max(0, dispatch_deadline - time.monotonic()) if dispatch_deadline is not None else None,
                        **({"token_allowance": token_budget} if workflow_budget else {}))
                    if response is not None:
                        continue
                    if time.monotonic() >= until:
                        raise InferenceOutputError("Timed out waiting for shared inference capacity", {"tokens": amount})
                    await asyncio.sleep(0.2)
            finally:
                await asyncio.to_thread(inference_admission.cancel_task_waiter, task_id, job_id, job_attempt)

    try:
        result, usage = asyncio.run(dispatch())
    except llm.InferenceError as exc:
        amount = state()[2]
        raise InferenceOutputError(str(exc), {"tokens": amount}) from exc
    except ReviewStopped as exc:
        exc.usage = {"tokens": state()[2]}
        raise
    except (TimeoutError, github.GitHubError, ValueError) as exc:
        if isinstance(exc, InferenceOutputError):
            raise
        raise InferenceOutputError(str(exc), {"tokens": state()[2]}) from exc
    if result["kind"] != "validated":
        raise InferenceOutputError(result["message"], usage, feedback=result.get("feedback"))
    return result["result"], usage
