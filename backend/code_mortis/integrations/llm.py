"""Shared inference dispatch and durable execution; workload planning lives elsewhere."""

import asyncio
import json
import os
import random
import time
from contextlib import suppress
from urllib.parse import quote, urlsplit

import httpx
from mortis_engine import gateway as engine_gateway
from mortis_engine import llm_http as engine_http
from mortis_engine.schemas import Request as EngineRequest

from code_mortis.core import analysis, inference_admission
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction
from code_mortis.core.security import safe_endpoint
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


def workflow_connection(connection):
    """Use the provider settings retained in new review and scan plans."""
    options = {key: value for key, value in connection.get("options", {}).items()
               if key not in {"timeout_sec", "prompt_token_ceiling"}}
    if "extra_body" in options:
        options["extra_body"] = {key: value for key, value in options["extra_body"].items()
                                 if key not in {"max_tokens", "max_completion_tokens", "max_output_tokens"}}
    return {**connection, "execution_limits": analysis.EXECUTION_LIMITS, "options": options}


def engine_connection(connection, *, preserve_cache_key_budget=False):
    """Let Engine manage the prompt cache key without changing the saved connection."""
    protocol = connection.get("protocol")
    if protocol not in {"openai", "openai-responses", "anthropic"}:
        return connection
    options = connection.get("options", {})
    extra = options.get("extra_body")
    if not isinstance(extra, dict):
        return connection
    filtered = {key: value for key, value in extra.items()
                if not (isinstance(key, str) and key.partition(".")[0] == "prompt_cache_key")}
    if len(filtered) == len(extra):
        return connection
    options = {**options, "extra_body": filtered}
    ceiling = options.get("prompt_token_ceiling")
    if preserve_cache_key_budget and ceiling is not None and "prompt_cache_key" in extra:
        cache_key = extra["prompt_cache_key"]
        if cache_key is None or isinstance(cache_key, str):
            value_bytes = len(json.dumps(cache_key, ensure_ascii=False).encode())
            # Native planning sees a quoted 64-digit hash for Responses and no field for other protocols.
            delta = 66 - value_bytes if protocol == "openai-responses" else -len(',"prompt_cache_key":') - value_bytes
            options["prompt_token_ceiling"] = ceiling + delta
    return {**connection, "options": options}


def _engine_request(request):
    if isinstance(request, EngineRequest):
        return request
    return EngineRequest.model_validate(request.model_dump(mode="python"))


def reservation(connection, request):
    if connection.get("execution_limits") == analysis.EXECUTION_LIMITS:
        estimate = request if request.max_output_tokens else request.model_copy(update={"max_output_tokens": 5_000_000})
        return engine_gateway.reservation(engine_connection(connection), _engine_request(estimate))
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


class _EngineClient:
    """Track when Engine hands a request to HTTPX for transport."""

    def __init__(self, client):
        self.client = client
        self.build_request = client.build_request
        self.dispatched = False

    async def send(self, request, **kwargs):
        self.dispatched = True
        return await self.client.send(request, **kwargs)


async def send(connection, credentials, request, *, client, stopped=lambda: False, timeout=None):
    """Single attempt, exact connection/model, no implicit protocol or provider fallback."""
    if connection.get("kind") != "api":
        raise ValueError("Inference requires a shared API connection")
    if connection.get("execution_limits") == analysis.EXECUTION_LIMITS:
        try:
            started = time.monotonic()
            # The package reads endpoint policy from the environment. The app also accepts .env settings.
            config = settings()
            if config.allow_private_endpoints:
                os.environ.setdefault("CM_ALLOW_PRIVATE_ENDPOINTS", "true")
            if config.trusted_endpoint_hosts:
                os.environ.setdefault("CM_TRUSTED_ENDPOINT_HOSTS", config.trusted_endpoint_hosts)
            if connection.get("base_url"):
                try:
                    await engine_http._bounded(asyncio.to_thread(safe_endpoint, connection["base_url"]), stopped, timeout)
                except ValueError as exc:
                    raise InferenceError("endpoint_policy", str(exc), consumption_unknown=False) from exc
                except engine_http.InferenceError as exc:
                    exc.consumption_unknown = False
                    raise
            if (request.max_output_tokens and connection["protocol"] == "anthropic"
                    and urlsplit(connection["base_url"]).hostname == "api.anthropic.com"):
                base = connection["base_url"].rstrip("/")
                if not base.endswith("/v1"):
                    base += "/v1"
                options = connection["options"]
                headers = engine_http.request_headers("anthropic", options, credentials)
                try:
                    model, identity = await engine_http.post_json(client,
                        f"{base}/models/{quote(connection['model'], safe='')}", headers, b"", method="GET",
                        stopped=stopped, retry_codes=options.get("retry_codes"),
                        timeout=timeout - (time.monotonic() - started) if timeout is not None else None)
                except engine_http.InferenceError as exc:
                    exc.consumption_unknown = False
                    raise
                capacity = model.get("max_tokens")
                if type(capacity) is not int or capacity <= 0:
                    raise engine_http.InferenceError("model_capacity", "Anthropic did not report the model's output capacity.",
                                                     request_id=identity, consumption_unknown=False)
                request = request.model_copy(update={"max_output_tokens": min(request.max_output_tokens, capacity)})
            transport = _EngineClient(client)
            try:
                return await engine_gateway.send(engine_connection(connection), credentials, _engine_request(request),
                    client=transport, stopped=stopped,
                    timeout=timeout - (time.monotonic() - started) if timeout is not None else None)
            except ValueError as exc:
                if transport.dispatched:
                    raise
                raise InferenceError("request_validation", str(exc), consumption_unknown=False) from exc
        except engine_http.InferenceError as exc:
            raise InferenceError(exc.code, str(exc), usage=exc.usage, request_id=exc.request_id, retryable=exc.retryable,
                                 consumption_unknown=exc.consumption_unknown, status_code=exc.status_code,
                                 retry_after=exc.retry_after) from exc
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
        return plan.snapshot["connection"], analysis.budget_deadline(plan, budget)


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
                       authorize, validate_result, stopped=lambda: False, authorize_admission=None, timeout=None,
                       token_allowance=None, budget_demand=None) -> Response | None:
    """Execute an immutable task after fresh external authorization.

    None means admission/dependencies/budget require scheduler attention. The caller
    must reschedule or cancel its queued ticket. validate_result must enforce the
    workload's evidence and redaction rules before anything is persisted.
    """
    if timeout is not None and (type(timeout) not in {int, float} or not 0 < timeout < float("inf")):
        raise InferenceError("cancelled", "The inference deadline expired before admission.", consumption_unknown=False)
    dispatch_deadline = time.monotonic() + timeout if timeout is not None else None
    request = Request.model_validate(request.model_dump(mode="python"))
    connection, deadline = await asyncio.to_thread(_inputs, task_id, job_id, request)
    estimate = reservation(connection, request)
    workflow = connection.get("execution_limits") == analysis.EXECUTION_LIMITS
    request_timeout = None if workflow else connection.get("options", {}).get("timeout_sec", 120)
    until = time.monotonic() + request_timeout if request_timeout is not None else float("inf")
    timeout_message = ("The selected provider timed out while this workflow had no configured deadline."
                       if workflow else
                       f"AI request timed out. Active limit: the AI connection's per-request timeout ({request_timeout:g} seconds). "
                       "Configure timeout_sec in Settings > AI connections > Advanced. Overall workflow time limits are separate.")
    if dispatch_deadline is not None and dispatch_deadline < until:
        until = dispatch_deadline
        timeout_message = (f"AI request timed out. Active limit: the workflow inference deadline ({round(timeout, 1):g} seconds available for this request). "
                           "Start a new run with a larger workflow time limit.")
    if deadline:
        remaining = max(0, (deadline - now()).total_seconds())
        stage_deadline = time.monotonic() + remaining
        if stage_deadline < until:
            until = stage_deadline
            timeout_message = (f"AI request timed out. Active limit: the saved stage deadline ({round(remaining, 1):g} seconds remained when this request started). "
                               "Start a new run with a larger stage or workflow time limit.")
    for attempt in range(3):
        try:
            if stopped() or time.monotonic() >= until:
                raise InferenceError("cancelled", "Inference stopped before admission.", consumption_unknown=False)
            await asyncio.to_thread(authorize_admission or authorize)
        except BaseException:
            await asyncio.to_thread(inference_admission.cancel_task_waiter, task_id, job_id, job_attempt)
            raise
        lease = await _claim(task_id, job_id, job_attempt, input_tokens=estimate,
                             output_tokens=request.max_output_tokens,
                             **({"token_allowance": token_allowance} if token_allowance is not None else {}),
                             **({"budget_demand": budget_demand} if budget_demand is not None else {}))
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
            if stopped() or lost.is_set() or time.monotonic() >= until:
                raise InferenceError("cancelled", "Inference stopped before dispatch.", consumption_unknown=False)
            wire_request = (request.model_copy(update={"max_output_tokens": lease.output_tokens})
                            if lease.output_tokens is not None else request)
            dispatched = True
            response = await send(connection, credentials, wire_request, client=client,
                                  stopped=lambda lost=lost: lost.is_set() or stopped(),
                                  timeout=until - time.monotonic() if until != float("inf") else None)
            if stopped() or lost.is_set() or time.monotonic() >= until:
                raise InferenceError("cancelled", "Inference stopped before result acceptance.",
                                     usage=response.usage, request_id=response.request_id)
            validation = asyncio.create_task(asyncio.to_thread(validate_result, response))
            try:
                result = await asyncio.shield(validation)
            except asyncio.CancelledError:
                with suppress(Exception, asyncio.CancelledError):
                    await llm_http._drain(validation)
                raise
            if not isinstance(result, dict):
                raise ValueError("Inference result validation must return a dictionary")
            await asyncio.to_thread(authorize)
            if stopped() or lost.is_set() or time.monotonic() >= until:
                raise InferenceError("cancelled", "Inference stopped during result validation.",
                                     usage=response.usage, request_id=response.request_id)
            accepted = await asyncio.to_thread(analysis.finish_task, lease, usage_tokens=response.usage.total_tokens,
                                               result=result, provider_request_id=response.request_id)
            if not accepted:
                raise InferenceError("stale_attempt", "Inference ownership expired; the result was discarded.",
                                     usage=response.usage, request_id=response.request_id)
            return response
        except InferenceError as exc:
            if exc.code == "timeout":
                exc.args = (timeout_message,)
            usage = exc.usage.total_tokens
            if usage is None and not exc.consumption_unknown:
                usage = 0
            accepted = await asyncio.to_thread(analysis.finish_task, lease, usage_tokens=usage,
                                               error_code=exc.code, provider_request_id=exc.request_id)
            if accepted and exc.retryable and usage == 0 and not exc.consumption_unknown and attempt < 2:
                retry = exc
            else:
                raise
        except BaseException as exc:
            await asyncio.to_thread(analysis.finish_task, lease,
                                    usage_tokens=response.usage.total_tokens if response else None if dispatched else 0,
                                    error_code="cancelled" if isinstance(exc, asyncio.CancelledError) else "invalid_result",
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
        await llm_http._bounded(asyncio.sleep(delay), stopped, until - time.monotonic() if until != float("inf") else None)
        if not await asyncio.to_thread(analysis.retry_task, task_id, job_id, job_attempt):
            raise retry
    raise AssertionError("Bounded inference retry loop exhausted")


async def check_connection(connection, credentials, *, client, stopped=lambda: False):
    """Two synthetic requests; no repository content or arbitrary tool execution."""
    options = connection.get("options", {})
    deadline = time.monotonic() + options.get("timeout_sec", 120)
    extra = options.get("extra_body", {})
    thinking = extra.get("thinking", {})
    reasoning_budget = extra.get("thinking.budget_tokens", thinking.get("budget_tokens", 0) if isinstance(thinking, dict) else 0)
    if type(reasoning_budget) is not int or reasoning_budget < 0:
        raise ValueError("Connection thinking budget must be a nonnegative whole number")
    output_limit = next((extra[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in extra),
                        max(2048, reasoning_budget + 1024))
    connection = workflow_connection(connection)
    if "prompt_token_ceiling" in options:
        connection["options"]["prompt_token_ceiling"] = options["prompt_token_ceiling"]
    request = Request(messages=[Message(role="user", content="Call mortis_probe with value 17.")],
                      tools=[Tool(name="mortis_probe", parameters={"type": "object", "properties": {
                          "value": {"type": "integer", "const": 17}}, "required": ["value"],
                          "additionalProperties": False})], max_output_tokens=output_limit)

    async def probe(request):
        try:
            reservation(connection, request)
        except ValueError as exc:
            raise InferenceError("request_validation", str(exc), consumption_unknown=False) from exc
        result = await send(connection, credentials, request, client=client, stopped=stopped,
                            timeout=deadline - time.monotonic())
        return Response.model_validate(result.model_dump(mode="python"))

    first = await probe(request)
    if len(first.tool_calls) != 1 or first.tool_calls[0].arguments != {"value": 17}:
        raise InferenceError("tool_round_trip", "The selected model did not complete the tool probe.", usage=first.usage)
    call = first.tool_calls[0]
    followup = request.model_copy(update={"tool_choice": "auto", "messages": [*request.messages,
        Message(role="assistant", content=first.content, tool_calls=first.tool_calls, provider_data=first.provider_data),
        Message(role="tool", tool_call_id=call.id, content='{"result":"MORTIS_PROBE_OK"}'),
        Message(role="user", content="Reply with exactly MORTIS_PROBE_OK and do not call tools.")]})
    second = await probe(followup)
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
