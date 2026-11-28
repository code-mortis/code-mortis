"""Map MCP task handles to existing durable Code Mortis operations."""

import json
from contextvars import ContextVar

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.extension import Extension, MethodBinding
from mcp.server.mcpserver.server import require_client_extension
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, InputResponses, RequestParams
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from code_mortis.api.dependencies import repo_access
from code_mortis.api.routers import runs
from code_mortis.core.db import now, transaction
from code_mortis.core.security import saved_credential_redactor
from code_mortis.integrations.github import GitHubError
from code_mortis.mcp import auth
from code_mortis.mcp.models import McpOperation, McpTask
from code_mortis.mcp.skills import MODERN
from code_mortis.models import Job, Occurrence, PullRequest, ReviewRun, SecurityScan
from code_mortis.reviews import background
from code_mortis.reviews.service import can_publish
from code_mortis.security_scans import service as scans

DOMAINS = {"reviews_start_review": "reviews", "reviews_generate_background": "reviews", "security_start_scan": "security"}
task_requested = ContextVar("mcp_task_requested", default=False)


def creation_result(db, operation, arguments, grant):
    """Called under the grant lock, in the same transaction as the original command."""
    task = db.get(McpTask, operation.id)
    if task is None:
        repo_id = arguments.get("repository_id")
        if repo_id is None:
            repo_id = db.get(PullRequest, arguments["pr_id"]).repository_id
        task = McpTask(operation_id=operation.id, repository_id=repo_id, target_id=operation.result["id"],
                       snapshot={"status": "working"}, updated_at=operation.created_at)
        db.add(task)
    return {**task_fields(operation, task, grant), "resultType": "task"}


def task_fields(operation, task, grant):
    ttl_ms = int((grant.expires_at - operation.created_at).total_seconds() * 1000)
    return {"taskId": operation.id, "createdAt": operation.created_at.isoformat(),
            "lastUpdatedAt": task.updated_at.isoformat(), "ttlMs": max(0, ttl_ms), "pollIntervalMs": 5000,
            "status": task.snapshot["status"]}


def review_result(db, run):
    report = run.report or {}
    manifest = report.get("manifest") or {}
    coverage = {key: len(values) for key, values in (manifest.get("coverage") or {}).items() if isinstance(values, list)}
    count = db.scalar(select(func.count()).select_from(Occurrence).where(Occurrence.run_id == run.id))
    return {"run_id": run.id, "base_sha": run.base_sha, "head_sha": run.head_sha, "status": run.status,
            "coverage": coverage or None, "finding_count": count, "publication": run.publication,
            "publication_error": run.publication_error, "error": run.error}


def scan_result(scan):
    report = scan.report or {}
    excluded = scan.coverage.get("excluded")
    problems = report.get("problems") or scan.coverage.get("problems") or []
    return {"scan_id": scan.id, "head_sha": scan.head_sha, "status": scan.status, "grade": scan.grade,
            "stages": {k: v.get("status") for k, v in scan.stages.items() if isinstance(v, dict)},
            "excluded_count": len(excluded) if excluded is not None else None,
            "problems": problems[:20], "problem_count": len(problems),
            "report_tool": "security_get_report", "coverage_tool": "security_get_scan_coverage", "error": scan.error}


def observe(db, name, task, user, cancel):
    if name == "reviews_start_review":
        row = db.get(ReviewRun, task.target_id)
        if row and cancel and row.status in {"queued", "running", "blocked"}:
            try:
                runs.cancel_run(row.id, db, user)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
            db.flush()
            db.refresh(row)
    elif name == "security_start_scan":
        row = db.get(SecurityScan, task.target_id, with_for_update=cancel)
        if row and cancel:
            scans.cancel(db, row, user)
    else:
        row = db.get(Job, task.target_id, with_for_update=cancel)
        if row and (row.kind != "generate_background" or row.payload.get("repository_id") != task.repository_id
                    or row.payload.get("actor_id") != user.id):
            row = None
        if row and cancel:
            background.cancel_job(db, row)
    if not row:
        return {"status": "failed", "error": {"code": -32603, "message": "The saved work is unavailable."}}
    status = row.status
    if status in {"queued", "running", "blocked"}:
        running = [k for k, v in (getattr(row, "stages", {}) or {}).items()
                   if isinstance(v, dict) and v.get("status") == "running"]
        return {"status": "working", "statusMessage": ", ".join([status, *running])}
    if status == "cancelled":
        return {"status": "cancelled"}
    if name == "reviews_start_review":
        if can_publish(row) and row.publication in {"pending", "publishing"}:
            if cancel:
                raise HTTPException(409, "Review analysis has finished. GitHub publication can no longer be cancelled.")
            return {"status": "working", "statusMessage": "GitHub publication " + row.publication}
        result = review_result(db, row)
    elif name == "security_start_scan":
        result = scan_result(row)
    else:
        result = background.job_out(row).model_dump(mode="json")
    return completed_snapshot(result, is_error=status in {"failed", "superseded"})


def completed_snapshot(result, *, is_error):
    return {"status": "completed", "result": {"resultType": "complete", "structuredContent": result,
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": is_error}}


def contains_credential(value, redact):
    # Check decoded strings as well as the text representation. JSON escaping can split a secret.
    if isinstance(value, str):
        return redact(value) != value.replace("\r\n", "\n")
    if isinstance(value, dict):
        return any(contains_credential(key, redact) or contains_credential(item, redact) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_credential(item, redact) for item in value)
    return False


def safe_completed_snapshot(db, state, task, grant, domain):
    result = state["result"]
    if len(result["content"][0]["text"].encode()) <= 262144:
        redact = saved_credential_redactor(db, auth.grant_repository_ids(grant, domain))
        if not contains_credential(result, redact):
            return state
    return completed_snapshot({"id": task.target_id, "status": result["structuredContent"]["status"],
                               "message": "Inspect the saved result in Code Mortis."}, is_error=True)


def access_task(task_id, method):
    token = get_access_token()
    with transaction() as db:
        operation = db.scalar(select(McpOperation).where(McpOperation.id == task_id,
            McpOperation.grant_id == token.claims["grant_id"]))
        if not operation or operation.tool not in DOMAINS:
            raise HTTPException(404, "Unknown task.")
        domain = DOMAINS[operation.tool]
        _, grant = auth.tool_access(db, domain, domain + ":run")
        user, grant = auth.ensure_grant(db, grant.id, domain, domain + ":run", lock=True)
        auth.tool_access(db, domain, domain + ":read")
        if operation.tool == "reviews_generate_background" and user.role != "admin":
            raise HTTPException(403, "Administrator access is required.")
        task = db.get(McpTask, task_id, with_for_update=True)
        if not task:
            raise HTTPException(404, "Unknown task.")
        cancel = method == "tasks/cancel"
        auth.rate_limit("tasks", grant.id)
        background_job = operation.tool == "reviews_generate_background"
        repo_access(db, user, task.repository_id, write=cancel or background_job)
        if method == "tasks/update":
            # All three workflows resolve their input before the job starts.
            return {}
        state = task.snapshot
        if state["status"] == "working":
            state = observe(db, operation.tool, task, user, cancel)
        if state["status"] == "completed":
            state = safe_completed_snapshot(db, state, task, grant, domain)
        if state != task.snapshot:
            task.snapshot, task.updated_at = state, now()
        return {} if cancel else {**task_fields(operation, task, grant), **task.snapshot}


class TaskParams(RequestParams):
    task_id: str


class TaskUpdateParams(TaskParams):
    input_responses: InputResponses


class Tasks(Extension):
    identifier = "io.modelcontextprotocol/tasks"

    def methods(self):
        return [MethodBinding(name, TaskUpdateParams if name == "tasks/update" else TaskParams, self.request, MODERN)
                for name in ("tasks/get", "tasks/cancel", "tasks/update")]

    async def request(self, ctx, params):
        require_client_extension(ctx, self.identifier)
        if ctx.request is not None and ctx.request.headers.get("Mcp-Name") != params.task_id:
            raise MCPError(code=-32602, message="Mcp-Name must match taskId.")
        try:
            return await run_in_threadpool(access_task, params.task_id, ctx.method)
        except HTTPException as exc:
            raise MCPError(code=-32602 if exc.status_code == 404 else -32000,
                           message=str(exc.detail)) from exc
        except GitHubError as exc:
            raise MCPError(code=-32000, message="Could not verify repository access.") from exc
        except Exception as exc:
            raise MCPError(code=-32603, message="Could not read the saved task. Retry shortly.") from exc

    async def intercept_tool_call(self, params, ctx, call_next):
        caps = ctx.session.client_capabilities
        domain = DOMAINS.get(params.name)
        token = get_access_token()
        if (not domain or ctx.protocol_version not in MODERN or not caps or self.identifier not in (caps.extensions or {})
                or not token or domain + ":read" not in token.scopes):
            return await call_next(ctx)
        marker = task_requested.set(True)
        try:
            result = await call_next(ctx)
        finally:
            task_requested.reset(marker)
        if isinstance(result, CallToolResult) and not result.is_error and result.structured_content:
            if result.structured_content.get("resultType") == "task":
                return result.structured_content
        return result
