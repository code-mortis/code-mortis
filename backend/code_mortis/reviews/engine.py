"""Pinned PR analysis through the shared inference gateway."""

import asyncio
import time
from dataclasses import dataclass, replace

from mortis_engine.review import engine as native
from mortis_engine.review import source_lines

from code_mortis.core import analysis, inference_admission
from code_mortis.core import source as git_source
from code_mortis.core.errors import ReviewStopped, engine_stopped
from code_mortis.integrations import llm
from code_mortis.integrations.llm_http import InferenceError
from code_mortis.reviews import context
from code_mortis.schemas.analysis import TaskSpec
from code_mortis.schemas.common import ReviewComment
from code_mortis.schemas.inference import Message as Message
from code_mortis.schemas.inference import ToolCall

VERSION = "mortis-pr/v1"
PROMPT_VERSION = "mortis-pr-prompt/v2"
SCHEMA_VERSION = "mortis-pr-result/v1"
SOURCE_TOOL_POLICY = "bounded-reads/v2"
SOURCE_CODE_POLICY = "selected-lines/v1"
MAX_READ_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 20_000_000
MAX_READ_LINES = 400
ROUNDS = {"low": 1, "medium": 2, "high": 4}
INSTRUCTIONS = native.INSTRUCTIONS
EvidenceSelection = native.EvidenceSelection
EngineFinding = native.EngineFinding
BatchResult = native.BatchResult
ResultValidationError = native.ResultValidationError
_validation_feedback = native._validation_feedback
output_schema = native.output_schema


def read_tool(*, source_code_policy=SOURCE_CODE_POLICY, source_tool_policy=SOURCE_TOOL_POLICY):
    return native.read_tool(source_code_policy=source_code_policy, source_tool_policy=source_tool_policy)


@dataclass
class PreparedBatch(native.PreparedBatch):
    source_tool_policy: str | None = SOURCE_TOOL_POLICY

    @property
    def spec(self):
        return TaskSpec(key=self.key, stage="analysis", input_hash=llm.input_fingerprint(self.request),
                        scope=[{"unit_key": unit.key, "path": unit.path, "source_sha": unit.source_sha,
                                "start_line": unit.start_line, "end_line": unit.end_line,
                                "input_hash": unit.input_hash} for unit in self.units])


def _source(revision, path, start, end, text):
    return {"source_id": context.source_id(revision, path), "revision": revision, "path": path,
            "start_line": start, "end_line": end, "text": text}


# Keep native excerpts and validation in the same coordinates as app plans and source reads.
@source_lines.use(None)
def prepare_batches(plan, connection, options, credentials, *, graph_by_path=None, stopped=lambda: False,
                    output_token_policy=analysis.OUTPUT_TOKEN_POLICY, source_code_policy=SOURCE_CODE_POLICY,
                    prompt_version=PROMPT_VERSION, source_tool_policy=SOURCE_TOOL_POLICY):
    # Redact with the app key before entering Engine, which also serves standalone CLI clients.
    units = {unit.key: replace(unit, **context.sanitize({"source": unit.source, "patch": unit.patch,
             "rule": unit.rule, "path": unit.path, "old_path": unit.old_path}, credentials, stopped))
             for group in plan.batches for unit in group}
    redacted_plan = replace(plan, batches=[[units[unit.key] for unit in group] for group in plan.batches])
    redacted_options = context.sanitize({key: value for key, value in options.items() if key != "review_rules"},
                                        credentials, stopped)
    redacted_options["max_output_tokens"] = analysis.output_token_limit(output_token_policy)
    graphs = context.sanitize(graph_by_path, credentials, stopped) if graph_by_path else None
    engine_credentials = {key: value for key, value in credentials.items() if key != "redaction_rules"}
    engine_connection = llm.engine_connection(connection, preserve_cache_key_budget=True)
    batches = native.prepare_batches(redacted_plan, engine_connection, redacted_options, engine_credentials,
        graph_by_path=graphs, stopped=lambda: engine_stopped(stopped), output_token_policy=output_token_policy,
        source_code_policy=source_code_policy, prompt_version=prompt_version, source_tool_policy=source_tool_policy)
    result = []
    for batch in batches:
        if engine_connection is not connection:
            batch.connection = dict(connection)
        batch.options = dict(options)
        if output_token_policy == analysis.WORKFLOW_OUTPUT_POLICY:
            batch.options["max_output_tokens"] = 0
        result.append(PreparedBatch(**vars(batch)))
    return result


_diff_lines = source_lines.use(None)(native._diff_lines)


@source_lines.use(None)
def validate_result(data, batch, sources, credentials, stopped=lambda: False):
    value = BatchResult.model_validate(data, strict=True)
    # Match historical credentials before native token masking can split them.
    # Leave identity, bounds and evidence for exact validation.
    for finding in value.findings:
        for field in ("content", "suggestion_code", "trigger", "consequence", "category"):
            text = getattr(finding, field)
            if "ghs_" in text:
                setattr(finding, field, context.sanitize(text, credentials, stopped))
    safe_credentials = {key: value for key, value in credentials.items() if key != "redaction_rules"}
    result = native.validate_result(value, batch, sources, safe_credentials, lambda: engine_stopped(stopped))
    return context.sanitize(result, credentials, stopped)


def read_sources(calls, *, repo, env, plan, options, credentials, authorize, stopped,
                 source_tool_policy=SOURCE_TOOL_POLICY):
    """Only bounded, policy-approved exact-head Git blob reads; tool names are never executable."""
    from code_mortis.reviews import planning

    def check_access():
        try:
            authorize()
        except ValueError as exc:
            raise PermissionError("Supporting evidence authorization was revoked") from exc

    if not 1 <= len(calls) <= 10:
        raise ResultValidationError("source_call_limit", ["tool_calls"], "Request one to ten source reads per round.")
    bounded = source_tool_policy == "bounded-reads/v2"
    selections, sources, size = [], [], 0
    for index, call in enumerate(calls):
        field = ["tool_calls", index]
        if call.name != "read_source":
            raise ResultValidationError("source_tool", [*field, "name"], "Use only the read_source tool.")
        if set(call.arguments) != {"path", "start_line", "end_line"}:
            raise ResultValidationError("source_arguments", [*field, "arguments"],
                                        "Provide exactly path, start_line and end_line as read_source arguments.")
        path, start, end = (call.arguments[key] for key in ("path", "start_line", "end_line"))
        if not isinstance(path, str):
            raise ResultValidationError("source_path", [*field, "arguments", "path"], "Source path must be a canonical relative string.")
        try:
            git_source.repository_path(path)
        except ValueError as exc:
            raise ResultValidationError("source_path", [*field, "arguments", "path"],
                                        "Source path must stay inside the repository and be canonical.") from exc
        if (type(start) is not int or type(end) is not int or start < 1 or end < start
                or not bounded and end - start + 1 > MAX_READ_LINES):
            raise ResultValidationError("source_range", [*field, "arguments"],
                                        "Use integer line bounds with 1 <= start_line <= end_line and at most 400 lines.")
        check_access()
        permitted = path in planning.allowed_context(repo, env, plan.base_sha, plan.head_sha, [path],
                                                    options, stopped, rules=plan.rules)
        source = None
        pages, next_start, eof_line = [], None, None
        reason = "excluded"
        if permitted:
            try:
                blob_size = git_source.git(repo, env, "cat-file", "-s", f"{plan.head_sha}:{path}", stopped=stopped)
                if int(blob_size.strip()) > MAX_SOURCE_BYTES:
                    reason = "source_limit"
                else:
                    text = git_source.read_source(repo, env, plan.head_sha, path, stopped)
                    # Inspect complete content for secret material before selecting a range.
                    text = context.sanitize(text, credentials, stopped)
                    lines = text.splitlines()
                    if bounded:
                        cursor, last = start, min(end, len(lines))
                        eof_line = len(lines) if end > len(lines) else None
                        while cursor <= last and len(sources) + len(pages) < 10:
                            chunk = []
                            for line in lines[cursor - 1:min(cursor + MAX_READ_LINES - 1, last)]:
                                amount = len(line.encode()) + int(bool(chunk))
                                if size + amount > MAX_READ_BYTES:
                                    break
                                chunk.append(line)
                                size += amount
                            if not chunk:
                                break
                            pages.append(_source(plan.head_sha, path, cursor, cursor + len(chunk) - 1, "\n".join(chunk)))
                            cursor += len(chunk)
                        next_start = cursor if cursor <= last else None
                        reason = "invalid_range" if start > last else "source_limit" if next_start else "available"
                    elif end > len(lines):
                        reason = "invalid_range"
                    else:
                        selected = "\n".join(lines[start - 1:end])
                        if size + len(selected.encode()) <= MAX_READ_BYTES:
                            source = _source(plan.head_sha, path, start, end, selected)
                            size += len(selected.encode())
                        else:
                            reason = "source_limit"
            except (ValueError, OSError):
                reason = "unavailable"
        check_access()
        if stopped():
            raise ReviewStopped("Review stopped before returning supporting evidence")
        selection = {"path": path, "start_line": start, "end_line": end,
                     "source_hash": analysis.fingerprint(source) if source else None,
                     "reason": "available" if source else reason}
        if bounded:
            selection.update(source_hash=analysis.fingerprint(pages) if pages else None,
                             returned_ranges=[{"start_line": page["start_line"], "end_line": page["end_line"]} for page in pages],
                             next_start_line=next_start, eof_line=eof_line)
            sources.extend(pages)
        selections.append(selection)
        if source:
            sources.append(source)
    return {"kind": "expansion", "selections": selections}, sources


def _restore_sources(result, *, repo, env, plan, batch, credentials, authorize, stopped):
    calls = [ToolCall(id=f"restore_{index}", name="read_source", arguments={key: item[key]
             for key in ("path", "start_line", "end_line")}) for index, item in enumerate(result["selections"])]
    restored, sources = read_sources(calls, repo=repo, env=env, plan=plan, options=batch.options,
                                    credentials=credentials, authorize=authorize, stopped=stopped,
                                    source_tool_policy=batch.source_tool_policy)
    if restored != result:
        raise ValueError("Supporting evidence no longer reproduces the accepted selection")
    return sources


def _next_request(batch, sources, notices, *, remaining_source_rounds, allow_source_reads=True,
                  repair=False, validation=None):
    return native._next_request(batch, sources, notices, remaining_source_rounds=remaining_source_rounds,
        allow_source_reads=allow_source_reads, repair=repair, validation=validation)


def _followup_spec(batch, request, parent_key, round_number):
    key = f"{batch.key}:round:{round_number}:" + llm.input_fingerprint(request)[:16]
    scope = batch.spec.scope
    return TaskSpec(key=key, stage="analysis", input_hash=llm.input_fingerprint(request), scope=scope,
                    dependencies=[parent_key])


def _recovery_batches(batch, request, sources, parent_key, *, recovery_policy=analysis.OUTPUT_RECOVERY_POLICY):
    return [PreparedBatch(**vars(child)) for child in native._recovery_batches(
        batch, request, sources, parent_key, recovery_policy=recovery_policy)]


def _completed_outcomes(result, batch):
    keys = {unit.key for unit in batch.units}
    completed = result.get("completed_units")
    if (result.get("kind") != "final" or not isinstance(completed, list)
            or len(completed) != len(keys) or set(completed) != keys
            or not isinstance(result.get("comments"), list)):
        raise ValueError("Saved task completion differs from assigned source")
    by_unit = {key: [] for key in keys}
    for comment in result["comments"]:
        parsed = ReviewComment.model_validate(comment, strict=True)
        unit = next((unit for unit in batch.units if unit.path == parsed.path
                     and unit.start_line <= parsed.start_line <= parsed.end_line <= unit.end_line
                     and comment.get("source_sha") == unit.source_sha), None)
        if unit is None:
            raise ValueError("Saved finding differs from assigned source")
        by_unit[unit.key].append(comment)
    return {key: {"status": "completed", "comments": comments} for key, comments in by_unit.items()}


def _report(plan, prepared, outcomes, *, tokens, status=None):
    completed, failed, comments = [], [], []
    reused = plan.comparison.get("reused_paths", [])
    reused_paths = set(reused)
    for item in plan.selected:
        if item.path in reused_paths:
            continue
        units = item.units
        if not item.error and units and all(outcomes.get(unit.key, {}).get("status") == "completed" for unit in units):
            completed.append(item.path)
        else:
            failed.append(item.path)
        for unit in units:
            if outcomes.get(unit.key, {}).get("status") == "completed":
                comments.extend(outcomes[unit.key].get("comments", []))
    if status is None:
        status = ("skipped" if plan.limit_reason or not plan.selected
                  else "partial" if failed and (reused or any(value["status"] == "completed" for value in outcomes.values()))
                  else "failed" if failed else "completed")
    def entries(paths):
        return [{"item_id": analysis.fingerprint(path), "path": path} for path in paths]
    report = {"manifest": {"schema_version": "code-mortis.pr-analysis/v1", "terminal_state": status,
                "coverage": {"selected": entries([item.path for item in plan.selected]),
                    "completed": entries(completed), "failed": entries(failed), "reused": entries(reused), "waived": []}},
              "engine": "mortis", "engine_version": VERSION, "excluded_files": plan.exclusions,
              "errors": [{"path": item.path, "message": item.error or next((outcomes.get(unit.key, {}).get("error")
                    for unit in item.units if outcomes.get(unit.key, {}).get("error")), "incomplete_analysis")}
                    for item in plan.selected if item.path in failed],
              "warnings": [], "summary": {"findings": len(comments), "total_tokens": tokens, "budget_exceeded": any(value.get("error") == "budget" for value in outcomes.values())},
              "units": [{"key": unit.key, "path": unit.path, "start_line": unit.start_line,
                         "end_line": unit.end_line, **outcomes.get(unit.key, {"status": "incomplete"})}
                        for batch in prepared for unit in batch.units]}
    if reused:
        report["comparison"] = {"strategy": "incremental", "baseline_run_id": plan.comparison["baseline_run_id"],
                                "baseline_head_sha": plan.comparison["baseline_head_sha"]}
    if plan.limit_reason:
        selected = report["manifest"]["coverage"]["selected"]
        report["manifest"] = {"schema_version": "code-mortis.file-limit/v1", "terminal_state": "skipped",
            "coverage": {"selected": selected, "completed": [], "failed": [], "reused": [],
                         "waived": [{**item, "reason": plan.limit_reason} for item in selected]}}
        report["errors"] = []
        report["summary"].update(findings=0, total_tokens=0)
        comments = []
        report["message"] = plan.limit_reason
        report["warnings"] = [{"type": "review_limit_reached", "message": plan.limit_reason}]
    return status, report, comments


def review(repo, env, plan, prepared, *, job_id, job_attempt, credentials, authorize,
           register_task, load_task, stopped=lambda: False, progress=lambda value: None, check_admission=None,
           limit_source_tools=True, register_recovery=None, recovery_policy=analysis.OUTPUT_RECOVERY_POLICY,
           rolling_budget=False):
    """Execute or replay immutable task rows. Callbacks own plan/Job fencing and durable append.

    load_task(key) returns {id,status,result,input_hash,usage_tokens,pending_reason,error_code};
    register_task(spec, request, parent_key=...)
    appends one bounded dependent task and returns its id. For unavailable optional
    frozen context only, the adapter may supply a validated final replay_result and
    replay_usage summing its immutable chain after validating the original plan/scope.
    Unknown consumption never causes this executor to retry or replace the saved task.
    """
    reported_outcomes, task_usage = {}, {}
    waiting_ids = set()
    visited = {batch.key: set() for batch in prepared}
    outstanding = len(prepared)

    async def recover(batch, request, row, sources, client, root_key, batch_outcomes):
        if (register_recovery is None or ":unit:" in batch.key
                or recovery_policy == "split-units/v1" and len(batch.units) <= 1
                or not row or row["status"] != "failed"
                or row.get("error_code") != "output_limit" or row.get("usage_tokens") is None):
            return
        recovered = _recovery_batches(batch, request, sources, row["key"], recovery_policy=recovery_policy)
        try:
            for child in recovered:
                llm.reservation(child.connection, child.request)
        except ValueError:
            return
        await asyncio.to_thread(register_recovery, recovered, parent_key=row["key"])
        for unit in batch.units:
            batch_outcomes[unit.key] = {"status": "running"}
        for child in recovered:
            await execute_batch(child, client, batch_outcomes, root_key=root_key, final_only=True)
            await save_progress(batch_outcomes)

    async def execute_batch(batch, client, outcomes, *, root_key=None, final_only=False):
        root_key = root_key or batch.key
        if stopped():
            raise ReviewStopped("Review cancelled or superseded")
        if plan.limit_reason or batch.error:
            if not plan.limit_reason and batch.error == "frozen_context_unavailable":
                row = await asyncio.to_thread(load_task, batch.key)
                task_usage[batch.key] = row.get("replay_usage") if row else None
                replay = row.get("replay_result") if row else None
                if replay is not None:
                    try:
                        replay_batch = (replace(batch, units=[unit for unit in batch.units
                                        if unit.key in replay.get("completed_units", [])])
                                        if row.get("replay_recovery") else batch)
                        outcomes.update(_completed_outcomes(replay, replay_batch))
                        for unit in batch.units:
                            if unit.key not in outcomes:
                                outcomes[unit.key] = {"status": "failed", "error": batch.error}
                        return
                    except ValueError:
                        pass
            for unit in batch.units:
                outcomes[unit.key] = {"status": "failed", "error": batch.error or "file_limit"}
            return
        session_id = analysis.fingerprint([job_id, batch.key])
        request = batch.request.model_copy(update={"session_id": session_id})
        key, round_number, expansions, repairs = batch.key, 0, 0, 0
        sources, supporting, notices = list(batch.sources), [], []
        admission_until = (float("inf") if batch.options.get("max_output_tokens") == 0 else
                           time.monotonic() + min(60, batch.connection.get("options", {}).get("timeout_sec", 120)))
        while True:
            if stopped():
                raise ReviewStopped("Review cancelled or superseded")
            row = await asyncio.to_thread(load_task, key)
            visited[root_key].add(key)
            if not row:
                raise ValueError("A persisted analysis task is required before execution")
            if row.get("input_hash", llm.input_fingerprint(request)) != llm.input_fingerprint(request):
                raise ValueError("Saved task inputs differ from the prepared immutable request")
            normalized = row.get("result") if row["status"] == "completed" else None
            if normalized is None and row["status"] not in {"pending", "running"}:
                task_usage[key] = row.get("usage_tokens")
                for unit in batch.units:
                    outcomes[unit.key] = {"status": "failed", "error": row.get("error_code") or row["status"]}
                await recover(batch, request, {**row, "key": key}, sources, client, root_key, outcomes)
                break
            expansion_sources = []
            usage = row.get("usage_tokens")

            def validate(response, batch=batch, sources=sources, expansions=expansions, repairs=repairs):
                nonlocal expansion_sources
                try:
                    if response.tool_calls:
                        if final_only or expansions + repairs >= ROUNDS[batch.options["effort"]]:
                            return {"kind": "incomplete", "code": "source_round_limit"}
                        result, expansion_sources = read_sources(response.tool_calls, repo=repo, env=env, plan=plan,
                            options=batch.options, credentials=credentials, authorize=authorize, stopped=stopped,
                            source_tool_policy=batch.source_tool_policy)
                        return result
                    return validate_result(response.output, batch, sources, credentials, stopped)
                except ValueError as exc:
                    return {"kind": "feedback", "code": "invalid_result", "validation": _validation_feedback(exc),
                            **({"source_read_error": True} if response.tool_calls
                               and batch.source_tool_policy == "bounded-reads/v2" else {})}

            if normalized is None:
                try:
                    response = await llm.execute_task(row["id"], job_id, job_attempt, request, credentials,
                        client=client, authorize=authorize, authorize_admission=check_admission,
                        validate_result=validate, stopped=stopped,
                        **({"budget_demand": outstanding} if rolling_budget else {}))
                except InferenceError as exc:
                    failed = await asyncio.to_thread(load_task, key)
                    deadline = failed and failed.get("pending_reason") == "deadline" and not stopped()
                    if stopped() or exc.code in {"cancelled", "stale_attempt"} and not deadline:
                        raise ReviewStopped("Review cancelled, superseded or its task lease expired") from exc
                    for unit in batch.units:
                        outcomes[unit.key] = {"status": "failed", "error": "deadline" if deadline else exc.code}
                    task_usage[key] = (failed.get("usage_tokens") if failed and failed["status"] != "pending"
                                       else exc.usage.total_tokens)
                    if failed:
                        await recover(batch, request, {**failed, "key": key}, sources, client, root_key, outcomes)
                    break
                if response is None:
                    waiting_ids.add(row["id"])
                    pending = await asyncio.to_thread(load_task, key)
                    reason = pending.get("pending_reason") if pending else "owner_lost"
                    if reason == "owner_lost":
                        raise ReviewStopped("The queued analysis task lost its owner")
                    if reason == "waiting_capacity" and time.monotonic() < admission_until:
                        try:
                            await asyncio.sleep(0.2)
                        except BaseException:
                            await asyncio.to_thread(inference_admission.cancel_task_waiter, row["id"], job_id, job_attempt)
                            raise
                        continue
                    await asyncio.to_thread(inference_admission.cancel_task_waiter, row["id"], job_id, job_attempt)
                    waiting_ids.discard(row["id"])
                    if reason is None:
                        raise AnalysisPending("Inference admission, dependencies or stage budget require attention")
                    if pending.get("usage_tokens") is not None or pending["status"] != "pending":
                        task_usage[key] = pending.get("usage_tokens")
                    for unit in batch.units:
                        outcomes[unit.key] = {"status": "failed", "error": reason if reason != "waiting_capacity" else "capacity_timeout"}
                    break
                waiting_ids.discard(row["id"])
                row = await asyncio.to_thread(load_task, key)
                usage = row.get("usage_tokens") if row else response.usage.total_tokens
                normalized = row.get("result") if row and row["status"] == "completed" else None
            task_usage[key] = usage
            if not normalized:
                raise ReviewStopped("The accepted analysis task no longer belongs to this execution")
            if normalized["kind"] == "final":
                # Only results validated under this immutable request are eligible for replay.
                outcomes.update(_completed_outcomes(normalized, batch))
                break
            if usage is None:
                for unit in batch.units:
                    outcomes[unit.key] = {"status": "failed", "error": "usage_unknown"}
                    if normalized.get("validation") is not None:
                        outcomes[unit.key]["validation"] = normalized["validation"]
                break
            if normalized["kind"] == "expansion":
                expansions += 1
                if not expansion_sources:
                    expansion_sources = await asyncio.to_thread(_restore_sources, normalized, repo=repo, env=env, plan=plan, batch=batch,
                        credentials=credentials, authorize=authorize, stopped=stopped)
                supporting.extend(expansion_sources)
                notices.extend(normalized["selections"])
                sources.extend(expansion_sources)
            elif (normalized["kind"] == "feedback" and not final_only
                  and (repairs < 1 or normalized.get("source_read_error") and batch.source_tool_policy == "bounded-reads/v2")
                  and expansions + repairs < ROUNDS[batch.options["effort"]]):
                if normalized.get("source_read_error") and batch.source_tool_policy == "bounded-reads/v2":
                    expansions += 1
                else:
                    repairs += 1
            else:
                for unit in batch.units:
                    outcomes[unit.key] = {"status": "failed", "error": normalized.get("code", "invalid_result")}
                    if normalized.get("validation") is not None:
                        outcomes[unit.key]["validation"] = normalized["validation"]
                break
            request = _next_request(batch, supporting, notices,
                                    remaining_source_rounds=ROUNDS[batch.options["effort"]] - expansions - repairs,
                                    allow_source_reads=not limit_source_tools or expansions + repairs < ROUNDS[batch.options["effort"]],
                                    repair=normalized["kind"] == "feedback",
                                    validation=normalized.get("validation"))
            request = request.model_copy(update={"session_id": session_id})
            try:
                llm.reservation(batch.connection, request)
            except ValueError:
                for unit in batch.units:
                    outcomes[unit.key] = {"status": "failed", "error": "supporting_context_limit"}
                break
            round_number += 1
            spec = _followup_spec(batch, request, key, round_number)
            await asyncio.to_thread(register_task, spec, request, parent_key=key)
            key = spec.key
            admission_until = (float("inf") if batch.options.get("max_output_tokens") == 0 else
                           time.monotonic() + min(60, batch.connection.get("options", {}).get("timeout_sec", 120)))
    progress_lock = asyncio.Lock()
    reused = set(plan.comparison.get("reused_paths", ()))

    async def save_progress(batch_outcomes):
        async with progress_lock:
            reported_outcomes.update(batch_outcomes)
            items = {}
            for item in plan.selected:
                if item.path in reused:
                    items[item.path] = {"status": "reused"}
                    continue
                values = [reported_outcomes.get(unit.key) for unit in item.units]
                state = ("failed" if item.error or any(value and value["status"] == "failed" for value in values)
                         else "completed" if values and all(value and value["status"] == "completed" for value in values)
                         else "running")
                items[item.path] = {"status": state}
            await asyncio.to_thread(progress, {"phase": "analysis", "selected": len(plan.selected),
                "completed": sum(value["status"] == "completed" for value in items.values()),
                **({"reused": len(reused)} if reused else {}),
                "failed": sum(value["status"] == "failed" for value in items.values()), "items": items,
                "completed_units": sum(value["status"] == "completed" for value in reported_outcomes.values()),
                "selected_units": sum(len(batch.units) for batch in prepared)})

    async def execute():
        await save_progress({})
        queue = asyncio.Queue()
        for batch in prepared:
            queue.put_nowait(batch)
        concurrency = prepared[0].options.get("concurrency", 2) if prepared else 1
        if type(concurrency) is not int or not 1 <= concurrency <= 8:
            raise ValueError("Analysis concurrency must be between one and eight")
        async with llm.http_client() as client:
            async def worker():
                nonlocal outstanding
                while not queue.empty():
                    batch = queue.get_nowait()
                    batch_outcomes = {}
                    try:
                        try:
                            await execute_batch(batch, client, batch_outcomes)
                        except TimeoutError:
                            for key in visited[batch.key]:
                                try:
                                    row = await asyncio.to_thread(load_task, key)
                                except TimeoutError:
                                    task_usage[key] = None
                                    continue
                                if row:
                                    task_usage[key] = row.get("usage_tokens")
                            for unit in batch.units:
                                if batch_outcomes.get(unit.key, {}).get("status") != "completed":
                                    batch_outcomes[unit.key] = {"status": "failed", "error": "deadline"}
                        await save_progress(batch_outcomes)
                    finally:
                        outstanding -= 1
                        queue.task_done()

            workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(prepared)))]
            try:
                await asyncio.gather(*workers)
            finally:
                for worker_task in workers:
                    worker_task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                await asyncio.gather(*(asyncio.to_thread(inference_admission.cancel_task_waiter, identity, job_id, job_attempt)
                                       for identity in waiting_ids))

    asyncio.run(execute())
    tokens = None if any(value is None for value in task_usage.values()) else sum(task_usage.values())
    return _report(plan, prepared, reported_outcomes, tokens=tokens)


class AnalysisPending(Exception):
    """The durable task must be revisited without replacing its budget or request."""
