"""Shared inference dispatch and durable execution; workload planning lives elsewhere."""

import asyncio
import json
import random
import time
from contextlib import suppress

import httpx

from code_mortis.core import analysis, inference_admission
from code_mortis.core.db import now, transaction
from code_mortis.integrations import llm_http
from code_mortis.integrations.llm_http import InferenceError
from code_mortis.models import AnalysisBudget, AnalysisPlan, AnalysisTask
from code_mortis.schemas.inference import Message, Request, Response, Tool


def http_client():
    """One pool per run/event loop, shared across its concurrent tasks and tool rounds."""
    return httpx.AsyncClient(trust_env=False, follow_redirects=False,
                             limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))


def input_fingerprint(request: Request):
    # Session IDs correlate transport calls; they do not change analysis inputs.
    return analysis.fingerprint(request.model_dump(mode="json", exclude={"session_id"}))


def reservation(connection, request):
    protocol = connection["protocol"]
    body = llm_http.encode_request("anthropic" if protocol == "anthropic-bedrock" else protocol,
                                   connection, request)
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > llm_http.MAX_REQUEST_BYTES:
        raise ValueError("Inference request exceeds the byte limit")
    # A conservative byte estimate plus framing; actual provider usage settles the ledger.
    tokens = len(encoded) + 1024 + 64 * (len(request.messages) + len(request.tools))
    ceiling = connection.get("options", {}).get("prompt_token_ceiling")
    if ceiling is not None and tokens > ceiling:
        raise ValueError("Inference request exceeds the connection prompt token ceiling")
    return tokens


async def send(connection, credentials, request, *, client, stopped=lambda: False, timeout=None):
    """Single attempt, exact connection/model, no implicit protocol or provider fallback."""
    if connection.get("kind") != "api":
        raise ValueError("Inference requires a shared API connection")
    if connection.get("protocol") == "anthropic-bedrock":
        from code_mortis.integrations import llm_bedrock

        adapter = llm_bedrock
    else:
        adapter = llm_http
    reservation(connection, request)
    return await adapter.send(connection, credentials, request, client=client, stopped=stopped, timeout=timeout)


def _inputs(task_id, job_id, request):
    with transaction() as db:
        task = db.get(AnalysisTask, task_id)
        plan = db.get(AnalysisPlan, task.plan_id) if task else None
        if not plan or plan.job_id != job_id or task.input_hash != input_fingerprint(request):
            raise ValueError("Inference inputs do not match the immutable task plan")
        budget = db.get(AnalysisBudget, (plan.id, task.stage))
        return plan.snapshot["connection"], budget.deadline_at


async def _claim(task_id, job_id, job_attempt, **kwargs):
    pending = asyncio.create_task(asyncio.to_thread(analysis.claim_task, task_id, job_id, job_attempt, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        async def cleanup():
            lease = await pending
            if lease:
                try:
                    await asyncio.to_thread(analysis.finish_task, lease, usage_tokens=0, error_code="cancelled")
                finally:
                    await asyncio.to_thread(inference_admission.release_connection, lease.admission_id)
            await asyncio.to_thread(inference_admission.cancel_task_waiter, task_id, job_id, job_attempt)

        await llm_http._drain(asyncio.create_task(cleanup()))
        raise


async def execute_task(task_id, job_id, job_attempt, request: Request, credentials, *, client,
                       authorize, validate_result, stopped=lambda: False) -> Response | None:
    """Execute an immutable task after fresh external authorization.

    None means admission/dependencies/budget require scheduler attention. The caller
    must reschedule or cancel its queued ticket. validate_result must enforce the
    workload's evidence and redaction rules before anything is persisted.
    """
    request = Request.model_validate(request.model_dump(mode="python"))
    connection, deadline = await asyncio.to_thread(_inputs, task_id, job_id, request)
    estimate = reservation(connection, request)
    until = time.monotonic() + connection.get("options", {}).get("timeout_sec", 120)
    if deadline:
        until = min(until, time.monotonic() + max(0, (deadline - now()).total_seconds()))
    for attempt in range(3):
        try:
            if stopped() or time.monotonic() >= until:
                raise InferenceError("cancelled", "Inference stopped before admission.", consumption_unknown=False)
            await asyncio.to_thread(authorize)
        except BaseException:
            await asyncio.to_thread(inference_admission.cancel_task_waiter, task_id, job_id, job_attempt)
            raise
        lease = await _claim(task_id, job_id, job_attempt, input_tokens=estimate,
                             output_tokens=request.max_output_tokens)
        if lease is None:
            return None
        lost, response, dispatched = asyncio.Event(), None, False

        async def heartbeat(lease=lease, lost=lost):
            interval = min(10, max(0.1, (lease.lease_until - now()).total_seconds() / 3))
            while True:
                await asyncio.sleep(interval)
                try:
                    if not await asyncio.to_thread(analysis.renew_task, lease):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        monitor = asyncio.create_task(heartbeat())
        retry = None
        try:
            await asyncio.to_thread(authorize)
            if stopped() or lost.is_set():
                raise InferenceError("cancelled", "Inference stopped before dispatch.", consumption_unknown=False)
            dispatched = True
            response = await send(connection, credentials, request, client=client,
                                  stopped=lambda lost=lost: lost.is_set() or stopped(), timeout=until - time.monotonic())
            await asyncio.to_thread(authorize)
            if stopped() or lost.is_set() or time.monotonic() >= until:
                raise InferenceError("cancelled", "Inference stopped before result acceptance.",
                                     usage=response.usage, request_id=response.request_id)
            result = validate_result(response)
            if not isinstance(result, dict):
                raise ValueError("Inference result validation must return a dictionary")
            accepted = await asyncio.to_thread(analysis.finish_task, lease, usage_tokens=response.usage.total_tokens,
                                               result=result, provider_request_id=response.request_id)
            if not accepted:
                raise InferenceError("stale_attempt", "Inference ownership expired; the result was discarded.",
                                     usage=response.usage, request_id=response.request_id)
            return response
        except InferenceError as exc:
            usage = exc.usage.total_tokens
            if usage is None and not exc.consumption_unknown:
                usage = 0
            accepted = await asyncio.to_thread(analysis.finish_task, lease, usage_tokens=usage,
                                               error_code=exc.code, provider_request_id=exc.request_id)
            if accepted and exc.retryable and usage == 0 and not exc.consumption_unknown and attempt < 2:
                retry = exc
            else:
                raise
        except BaseException:
            await asyncio.to_thread(analysis.finish_task, lease,
                                    usage_tokens=response.usage.total_tokens if response else None if dispatched else 0,
                                    error_code="cancelled" if stopped() else "invalid_result",
                                    provider_request_id=response.request_id if response else None)
            raise
        finally:
            async def cleanup(monitor=monitor, lease=lease):
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
                # Async transport cancellation has unwound before capacity is released.
                await asyncio.to_thread(inference_admission.release_connection, lease.admission_id)

            await llm_http._drain(asyncio.create_task(cleanup()))
        delay = max(retry.retry_after or 0, 2 ** attempt + random.uniform(0, 0.25))
        if time.monotonic() + delay >= until:
            raise retry
        await llm_http._bounded(asyncio.sleep(delay), stopped, until - time.monotonic())
        if not await asyncio.to_thread(analysis.retry_task, task_id, job_id, job_attempt):
            raise retry
    raise AssertionError("Bounded inference retry loop exhausted")


async def check_connection(connection, credentials, *, client, stopped=lambda: False):
    """Two synthetic requests; no repository content or arbitrary tool execution."""
    deadline = time.monotonic() + connection.get("options", {}).get("timeout_sec", 120)
    extra = connection.get("options", {}).get("extra_body", {})
    thinking = extra.get("thinking", {})
    reasoning_budget = extra.get("thinking.budget_tokens", thinking.get("budget_tokens", 0) if isinstance(thinking, dict) else 0)
    if type(reasoning_budget) is not int or reasoning_budget < 0:
        raise ValueError("Connection thinking budget must be a nonnegative whole number")
    output_limit = next((extra[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in extra),
                        max(2048, reasoning_budget + 1024))
    request = Request(messages=[Message(role="user", content="Call mortis_probe with value 17.")],
                      tools=[Tool(name="mortis_probe", parameters={"type": "object", "properties": {
                          "value": {"type": "integer", "const": 17}}, "required": ["value"],
                          "additionalProperties": False})], max_output_tokens=output_limit)
    first = await send(connection, credentials, request, client=client, stopped=stopped,
                       timeout=deadline - time.monotonic())
    if len(first.tool_calls) != 1 or first.tool_calls[0].arguments != {"value": 17}:
        raise InferenceError("tool_round_trip", "The selected model did not complete the tool probe.", usage=first.usage)
    call = first.tool_calls[0]
    followup = request.model_copy(update={"tool_choice": "auto", "messages": [*request.messages,
        Message(role="assistant", content=first.content, tool_calls=first.tool_calls, provider_data=first.provider_data),
        Message(role="tool", tool_call_id=call.id, content='{"result":"MORTIS_PROBE_OK"}'),
        Message(role="user", content="Reply with exactly MORTIS_PROBE_OK and do not call tools.")]})
    second = await send(connection, credentials, followup, client=client, stopped=stopped,
                        timeout=deadline - time.monotonic())
    if second.tool_calls or second.content.strip() != "MORTIS_PROBE_OK":
        raise InferenceError("tool_round_trip", "The selected model did not confirm the tool result.", usage=second.usage)
    return first, second


def test_connection(connection, credentials, *, stopped=lambda: False):
    async def run():
        async with http_client() as client:
            return await check_connection(connection, credentials, client=client, stopped=stopped)

    with inference_admission.reserve_connection(connection["id"], f"test:{time.time_ns()}",
                                                 stopped=stopped, lease_seconds=900):
        asyncio.run(run())
    return "Connected. Model completed a tool-call round trip."
