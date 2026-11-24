"""PR snapshot and ledger integration; public rollout stays gated until workflow parity."""

import json
from datetime import timedelta

from sqlalchemy import select

from code_mortis.api_tokens import provenance as pat
from code_mortis.core import analysis, file_policy, source
from code_mortis.core.db import now, transaction
from code_mortis.core.errors import ReviewStopped
from code_mortis.core.security import unseal
from code_mortis.integrations import github, llm
from code_mortis.mcp import provenance as mcp
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisPlan,
    AnalysisTask,
    Connection,
    Job,
    Membership,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.reviews import comparison, engine, graph_context, planning
from code_mortis.schemas.analysis import PlanSpec, TaskSpec


def review(full_name, number, base_sha, head_sha, connection, credentials, options, token, *,
           run_id, claim, stopped=lambda: False, progress=lambda value: None, checkpoint=None,
           saved_review=None, after_review=None, before_analysis=None, graph_options=None):
    from code_mortis.reviews.service import (
        authorize_connection,
        execution_identity,
        review_stopped,
        target_matches,
    )

    if not isinstance(claim, str) or ":" not in claim:
        raise ValueError("Mortis analysis requires a durable job attempt")
    job_id, attempt = claim.rsplit(":", 1)
    job_attempt = int(attempt)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        job = db.get(Job, job_id)
        pr = db.get(PullRequest, run.pr_id) if run else None
        repo = db.get(Repository, pr.repository_id) if pr else None
        current = db.get(Connection, connection.get("id"))
        if (not run or not repo or not current or current.kind != "api"
                or run.engine != "mortis" or run.config.get("analysis_engine") != "mortis"
                or job is None or job.payload.get("run_id") != run.id
                or not analysis.job_live(job, job_attempt, now())
                or (repo.full_name, pr.number, run.base_sha, run.head_sha) != (full_name, number, base_sha, head_sha)
                or run.config["connection"] != connection):
            raise ValueError("Mortis inputs must match an active immutable PR job")
        saved_credentials = json.loads(unseal(current.secret))
        if any(saved_credentials.get(key, default) != credentials.get(key, default)
               for key, default in (("api_key", ""), ("extra_headers", {}))):
            raise ValueError("The shared connection credentials changed before analysis")
        secret = current.secret
        immutable_options = dict(run.config["settings"])
        configured_output_policy = analysis.configured_output_policy(run.config)
        configured_budget_schedule = run.config.get("budget_schedule")
        deadline = (run.started_at + timedelta(seconds=immutable_options["timeout_seconds"])
                    if immutable_options.get("timeout_seconds") is not None else None)
        repository_id, actor_id = repo.id, run.actor_id
        authority = {"actor_id": actor_id, "trigger": run.trigger,
                     "pat": pat.provenance_from_run(run), "mcp": mcp.provenance_from_run(run)}
        trigger = run.trigger
        prior = db.scalar(select(AnalysisPlan).where(AnalysisPlan.job_id == job_id))
        frozen_plan = prior.snapshot if prior else None
        frozen_graph = prior.snapshot.get("graph", {}) if prior else None
        planner_version = (frozen_plan["versions"]["planner"] if frozen_plan
                           else run.config.get("planner_version", planning.LEGACY_VERSION))

    def cancelled():
        return stopped() or review_stopped(run_id, claim)

    def check_admission(*, check_deadline=True):
        if cancelled():
            raise ReviewStopped("The PR analysis was cancelled or superseded")
        if check_deadline and deadline and now() >= deadline:
            raise TimeoutError("The PR analysis reached its configured deadline")

    def authorize(*, check_deadline=True):
        check_admission(check_deadline=check_deadline)
        with transaction() as db:
            current_run = db.get(ReviewRun, run_id)
            current_repo = db.get(Repository, repository_id)
            selected = db.get(Connection, connection["id"])
            if (selected is None or selected.secret != secret
                    or any(getattr(selected, key) != connection[key] for key in ("kind", "provider", "protocol", "base_url"))):
                raise ValueError("The shared connection changed during analysis; start a new review")
            if mcp.run_revoked(db, current_run) or pat.run_revoked(db, current_run):
                raise ReviewStopped("The originating permission was revoked")
            current_actor, current_trigger = execution_identity(current_run)
            actor = db.get(User, current_actor) if current_actor else None
            if current_actor and (not actor or not actor.enabled or not db.get(Membership, current_actor)):
                raise ReviewStopped("The requesting user no longer has access")
            authorize_connection(db, selected, current_repo, current_trigger, actor)
            if current_actor:
                github.authorize_repo(db, actor, full_name, write=True)
            fresh_token = github.installation_token(db, current_repo.installation_id)
        remote = github.request("GET", f"/repos/{full_name}/pulls/{number}", fresh_token)
        if not target_matches(current_run, remote["head"]["sha"], remote["base"]["sha"], remote["state"]):
            raise ReviewStopped("The PR changed during analysis")

    authorize()
    with source.review_snapshot(full_name, number, base_sha, head_sha, token, cancelled,
                                timeout_seconds=options.get("timeout_seconds")) as (path, env, fetch_env, merge_base):
        if saved_review is not None:
            if after_review:
                after_review(saved_review, path, env, fetch_env)
            return saved_review
        ceiling = connection.get("options", {}).get("prompt_token_ceiling")
        selection = planning.plan_review(path, env, base_sha, head_sha, merge_base, immutable_options, cancelled,
                                         max_unit_bytes=planning.unit_byte_limit(ceiling), planner_version=planner_version)
        limited = engine._report(selection, [], {}, tokens=0)[1] if selection.limit_reason else None
        if before_analysis:
            before_analysis(limited)
        if not selection.limit_reason:
            progress({"phase": "analysis", "selected": len(selection.selected), "completed": 0, "failed": 0})
        graph_records, graph_sources, remaining = {}, {}, graph_context.MAX_RUN_BYTES
        if graph_options and not selection.limit_reason:
            for item in selection.selected:
                if item.error or not item.units:
                    continue
                saved = frozen_graph.get("selections", {}).get(item.path) if frozen_graph is not None else None
                if frozen_graph is not None and saved is None:
                    raise ValueError("A resumed graph selection cannot expand its original scope")
                record, supporting = graph_context.prepare(graph_options, item.path, head_sha, path, env,
                    immutable_options, credentials, cancelled, remaining=remaining, saved=saved,
                    base_sha=base_sha, trusted_rules=selection.rules)
                graph_records[item.path] = record
                remaining -= record.get("reserved_bytes", 0)
                if supporting:
                    graph_sources[item.path] = supporting
        output_token_policy = (analysis.output_token_policy(frozen_plan) if frozen_plan
                               else configured_output_policy)
        budget_schedule = (frozen_plan["versions"].get("budget_schedule") if frozen_plan else
                           configured_budget_schedule if output_token_policy == analysis.WORKFLOW_OUTPUT_POLICY else None)
        source_code_policy = frozen_plan["versions"].get("source_code") if frozen_plan else engine.SOURCE_CODE_POLICY
        prompt_version = frozen_plan["versions"]["prompt"] if frozen_plan else engine.PROMPT_VERSION
        source_tool_policy = frozen_plan["versions"].get("source_tools") if frozen_plan else engine.SOURCE_TOOL_POLICY
        recovery_policy = frozen_plan["versions"].get("output_recovery") if frozen_plan else analysis.OUTPUT_RECOVERY_POLICY
        if recovery_policy not in {None, "split-units/v1", analysis.OUTPUT_RECOVERY_POLICY}:
            raise ValueError("The saved output-recovery policy is unsupported")
        versions = {"engine": engine.VERSION, "planner": planner_version, "prompt": prompt_version,
                    "rules": selection.rules_digest, "schema": engine.SCHEMA_VERSION, "file_policy": str(file_policy.VERSION),
                    **({"source_tools": source_tool_policy} if source_tool_policy else {}),
                    **({"source_code": source_code_policy} if source_code_policy else {}),
                    **({"output_recovery": recovery_policy} if recovery_policy else {}),
                    **({"budget_schedule": budget_schedule} if budget_schedule else {}),
                    **({"output_tokens": output_token_policy} if output_token_policy else {})}
        execution_settings = {**immutable_options, "max_evidence_rounds": engine.ROUNDS[immutable_options["effort"]],
                              "workflow_contract": "mortis-workflow/v1"}
        if frozen_plan is not None:
            selection.comparison = frozen_plan["settings"].get("comparison", {})
        elif immutable_options.get("incremental_reviews") and not graph_options and planner_version == planning.VERSION:
            signature = comparison.signature(selection, execution_settings, versions, connection, secret,
                                             credentials.get("redaction_rules", ()))
            selection.comparison = comparison.select_baseline(run_id, signature, selection, path, env, cancelled)
        if selection.comparison:
            execution_settings["comparison"] = selection.comparison
            reused = set(selection.comparison.get("reused_paths", ()))
            # The frozen planner assigns one file per batch. Keep the full scope for coverage and source bounds.
            selection.batches = [batch for batch in selection.batches if batch[0].path not in reused]
        prepared = engine.prepare_batches(selection, connection, immutable_options, credentials,
                                           graph_by_path=graph_sources, stopped=cancelled,
                                           output_token_policy=output_token_policy, source_code_policy=source_code_policy,
                                           prompt_version=prompt_version, source_tool_policy=source_tool_policy)
        graph_omissions = {batch.key: batch.graph_omitted for batch in prepared if batch.graph_omitted}
        graph = {"options": graph_options, "selections": graph_records, "prompt_omissions": graph_omissions}
        specification = PlanSpec(repository_id=repository_id, run_id=run_id, initiator_id=actor_id,
            authorization=authority, trigger=trigger, connection=connection,
            revisions={"base": base_sha, "head": head_sha, "merge_base": merge_base},
            versions=versions, settings=execution_settings,
            file_policy={**file_policy.policy(immutable_options), "digest": selection.policy_digest},
            selected=[{"path": item.path, "old_path": item.old_path, "status": item.status,
                       "blob_sha": item.blob_sha, "error": item.error,
                       "units": [{"key": unit.key, "input_hash": unit.input_hash, "start_line": unit.start_line,
                                  "end_line": unit.end_line} for unit in item.units]} for item in selection.selected],
            exclusions=selection.exclusions, graph=graph,
            budgets={"analysis": immutable_options.get("token_budget"),
                     "assessment": immutable_options.get("assessment_token_budget"),
                     "verification": immutable_options.get("verification_token_budget"),
                     "summary": immutable_options.get("summary_token_budget", 60000)},
            deadlines={stage: deadline for stage in ("analysis", "assessment", "verification")} if deadline else {},
            tasks=[batch.spec for batch in prepared])
        if frozen_plan is not None:
            # Lost optional context cannot rewrite a request or erase already validated work.
            candidate = specification.model_dump(mode="json", exclude={"graph", "tasks"})
            if candidate != {key: value for key, value in frozen_plan.items() if key not in {"graph", "tasks"}}:
                raise ValueError("The original analysis scope or execution settings changed")
            original_tasks = {item["key"]: TaskSpec.model_validate(item) for item in frozen_plan["tasks"]}
            if set(original_tasks) != {batch.key for batch in prepared}:
                raise ValueError("The original analysis assignments changed")
            for batch in prepared:
                original = original_tasks[batch.key]
                if batch.spec.model_dump(exclude={"input_hash"}) != original.model_dump(exclude={"input_hash"}):
                    raise ValueError("The original analysis assignment scope changed")
                if batch.spec.input_hash != original.input_hash:
                    batch.error = "frozen_context_unavailable"
            specification.graph = frozen_graph
            specification.tasks = list(original_tasks.values())
        authorize()
        with transaction() as db:
            plan = analysis.persist_plan(db, job_id, specification)
            plan_id = plan.id
        progress({"analysis_plan_id": plan_id, "code_graph": graph_records,
                  "code_graph_prompt_omissions": graph_omissions})

        def load_task(key):
            with transaction() as db:
                task = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id, AnalysisTask.key == key))
                if not task:
                    return None
                attempts = list(db.scalars(select(AnalysisAttempt).where(AnalysisAttempt.task_id == task.id)
                                           .order_by(AnalysisAttempt.number)))
                known = all(row.usage_state == "known" for row in attempts)
                usage = sum(row.usage_tokens for row in attempts) if known else None
                budget, unknown, running = analysis.budget_state(db, plan_id, task.stage)
                latest_job = db.get(Job, job_id)
                reason = "waiting_capacity"
                if not analysis.job_live(latest_job, job_attempt, now()):
                    reason = "owner_lost"
                elif budget.deadline_at and now() >= budget.deadline_at:
                    reason = "deadline"
                elif unknown:
                    reason = "usage_unknown"
                elif budget.token_limit is not None:
                    request = requests.get(key)
                    if request and budget.charged_tokens + llm.reservation(connection, request) + (max(1, request.max_output_tokens)
                            if output_token_policy == analysis.WORKFLOW_OUTPUT_POLICY else request.max_output_tokens) > budget.token_limit:
                        reason = "waiting_capacity" if running else "budget"
                result = {"id": task.id, "status": task.status, "result": task.result, "input_hash": task.input_hash,
                          "usage_tokens": usage, "pending_reason": reason,
                          "error_code": attempts[-1].error_code if attempts else None}
                if key in unavailable:
                    chain = [task]
                    children = list(db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id,
                        AnalysisTask.key.startswith(key + ":round:", autoescape=True),
                        AnalysisTask.key.not_like("%:unit:%"))))
                    while children:
                        child = next((row for row in children if row.dependencies == [chain[-1].id]), None)
                        if child is None or child.scope != task.scope or child.stage != task.stage:
                            raise ValueError("The saved evidence chain is inconsistent")
                        chain.append(child)
                        children.remove(child)
                    recovery = list(db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id,
                        AnalysisTask.key.startswith(chain[-1].key + ":unit:", autoescape=True))))
                    consumed = list(db.scalars(select(AnalysisAttempt).where(
                        AnalysisAttempt.task_id.in_([row.id for row in [*chain, *recovery]]))))
                    result["replay_usage"] = (sum(row.usage_tokens for row in consumed)
                        if all(row.usage_state == "known" for row in consumed) else None)
                    if (all(row.status == "completed" for row in chain)
                            and chain[-1].result and chain[-1].result.get("kind") == "final"):
                        result["replay_result"] = chain[-1].result
                    elif (all(row.status == "completed" for row in chain[:-1])
                          and analysis.output_limited(db, plan, chain[-1])
                          and len(recovery) == len(task.scope)
                          and {analysis.fingerprint(row.scope) for row in recovery}
                              == {analysis.fingerprint([scope]) for scope in task.scope}
                          and all(row.dependencies == [chain[-1].id] and row.stage == task.stage
                                  and row.key == f"{chain[-1].key}:unit:{analysis.fingerprint(row.scope[0])[:16]}"
                                  for row in recovery)):
                        completed = [row for row in recovery if row.status == "completed" and row.result
                                     and row.result.get("kind") == "final"
                                     and row.result.get("completed_units") == [row.scope[0]["unit_key"]]]
                        result["replay_recovery"] = True
                        result["replay_result"] = {"kind": "final",
                            "completed_units": [row.scope[0]["unit_key"] for row in completed],
                            "comments": [comment for row in completed for comment in row.result["comments"]]}
                return result

        requests = {batch.key: batch.request for batch in prepared}
        unavailable = {batch.key for batch in prepared if batch.error == "frozen_context_unavailable"}

        def register_task(spec, request, *, parent_key):
            if llm.input_fingerprint(request) != spec.input_hash:
                raise ValueError("The evidence request does not match its frozen task")
            authorize()
            with transaction() as db:
                parent = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id, AnalysisTask.key == parent_key))
                if parent is None:
                    raise ValueError("The evidence parent is unavailable")
                task = analysis.append_task(db, job_id, job_attempt, parent.id, spec)
                identity = task.id
            requests[spec.key] = request
            return identity

        def register_recovery(batches, *, parent_key):
            specifications = [batch.spec.model_copy(update={"dependencies": [parent_key]}) for batch in batches]
            authorize()
            with transaction() as db:
                parent = db.scalar(select(AnalysisTask).where(AnalysisTask.plan_id == plan_id, AnalysisTask.key == parent_key))
                if parent is None:
                    raise ValueError("The output-limited parent is unavailable")
                analysis.split_task(db, job_id, job_attempt, parent.id, specifications)
            requests.update({batch.key: batch.request for batch in batches})

        result = engine.review(path, env, selection, prepared, job_id=job_id, job_attempt=job_attempt,
            credentials=credentials, authorize=authorize, register_task=register_task, load_task=load_task,
            stopped=cancelled, progress=progress, check_admission=check_admission,
            limit_source_tools=source_tool_policy is not None,
            register_recovery=register_recovery if recovery_policy else None, recovery_policy=recovery_policy,
            rolling_budget=budget_schedule == analysis.ROLLING_BUDGET and immutable_options.get("token_budget") is not None)
        authorize(check_deadline=False)
        if after_review:
            after_review(result, path, env, fetch_env)
        return result
