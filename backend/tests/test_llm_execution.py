"""Offline HTTP fixtures through real immutable plans, admission and usage accounting."""

import asyncio
import json
import socket
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event, select
from test_analysis_foundation import specification
from test_llm_http import SCHEMA, result

from code_mortis.core import analysis, inference_admission
from code_mortis.core.config import settings
from code_mortis.core.db import engine, now, transaction
from code_mortis.integrations import llm, llm_http
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisTask,
    Connection,
    InferenceAdmission,
    Job,
)
from code_mortis.reviews.service import connection_snapshot
from code_mortis.schemas.inference import Message, Request, Response, Usage

CREDENTIALS = {"api_key": "fixture-only-key", "extra_headers": {}}


@pytest.fixture(autouse=True)
def offline_endpoint(monkeypatch):
    monkeypatch.setattr(llm_http, "safe_endpoint", lambda url: url)


def request():
    return Request(messages=[Message(role="user", content="Review this fixed input")],
                   output_schema=SCHEMA, max_output_tokens=32)


def planned(seed, *, job="job", requests=None, stages=None, concurrency=2, budgets=None, options=None, deadlines=None,
            workflow=False):
    requests = requests or {"a": request()}
    stages = stages or {key: "analysis" for key in requests}
    with transaction() as db:
        connection = db.get(Connection, "connection")
        if options is not None:
            connection.options = options
        snapshot = connection_snapshot(connection, workflow=workflow)
        db.add(Job(id=job, kind="review", payload={}, status="running", attempts=1,
                   lease_until=now() + timedelta(minutes=10)))
        db.flush()
        versions = specification().versions
        if workflow:
            versions["output_tokens"] = analysis.WORKFLOW_OUTPUT_POLICY
        plan = analysis.persist_plan(db, job, specification(versions=versions,
            connection=snapshot, settings={"concurrency": concurrency},
            budgets=budgets or {stage: 10_000 for stage in stages.values()},
            deadlines=deadlines or {},
            tasks=[{"key": key, "stage": stages[key], "input_hash": llm.input_fingerprint(value), "scope": []}
                   for key, value in requests.items()]))
        ids = {row.key: row.id for row in db.scalars(select(AnalysisTask).where(AnalysisTask.plan_id == plan.id))}
        return plan.id, ids, requests, snapshot


def execute(identity, value, handler, *, job="job", authorize=lambda: None,
            validate_result=lambda response: response.output, stopped=lambda: False, timeout=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return await llm.execute_task(identity, job, 1, value, CREDENTIALS, client=client, authorize=authorize,
                                          validate_result=validate_result, stopped=stopped, timeout=timeout)
    return asyncio.run(run())


def rows():
    with transaction() as db:
        return (list(db.scalars(select(AnalysisTask))), list(db.scalars(select(AnalysisAttempt))),
                list(db.scalars(select(InferenceAdmission))))


@pytest.mark.parametrize("budget", [None, 2_000])
def test_package_transport_uses_only_the_new_workflow_budget(seed, monkeypatch, budget):
    monkeypatch.setattr(llm, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm.engine_http, "safe_endpoint", lambda url: url)
    value = request().model_copy(update={"max_output_tokens": 0})
    plan, ids, _, snapshot = planned(seed, requests={"a": value}, budgets={"analysis": budget}, workflow=True,
        options={"prompt_token_ceiling": 1, "timeout_sec": 5, "extra_body": {"max_tokens": 32}})
    wire = []

    def handler(message):
        wire.append(message)
        return httpx.Response(200, json=result("openai", usage=True))

    assert snapshot["options"] == {"extra_body": {}}
    assert execute(ids["a"], value, handler).output == {"ok": True}
    payload = json.loads(wire[0].content)
    assert wire[0].extensions["timeout"]["read"] is None
    if budget is None:
        assert "max_tokens" not in payload and "max_completion_tokens" not in payload
    else:
        assert payload.get("max_completion_tokens", payload.get("max_tokens")) == budget - llm.reservation(snapshot, value)
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 12


def test_package_transport_uses_trusted_hosts_from_app_config(seed, monkeypatch):
    monkeypatch.setenv("CM_TRUSTED_ENDPOINT_HOSTS", "")
    monkeypatch.delenv("CM_TRUSTED_ENDPOINT_HOSTS")
    monkeypatch.setattr(settings(), "trusted_endpoint_hosts", "127.0.0.1")
    with transaction() as db:
        db.get(Connection, "connection").base_url = "http://127.0.0.1:8131/v1"
    value = request().model_copy(update={"max_output_tokens": 0})
    _, ids, _, _ = planned(seed, requests={"a": value}, workflow=True)
    response = execute(ids["a"], value, lambda _: httpx.Response(200, json=result("openai", usage=True)))
    assert response.output == {"ok": True}


@pytest.mark.parametrize(("failure", "code", "message"), [
    ("public_http", "endpoint_policy", "require HTTPS"),
    ("trusted_dns", "endpoint_policy", "could not be found"),
    ("engine_dns", "request_validation", "could not be found"),
])
def test_endpoint_policy_failures_settle_without_provider_usage(seed, monkeypatch, failure, code, message):
    host = "provider.example"
    trusted = host if failure == "trusted_dns" else ""
    scheme = "https" if failure == "engine_dns" else "http"
    with transaction() as db:
        db.get(Connection, "connection").base_url = f"{scheme}://{host}/v1"
    plan_id, ids, values, _ = planned(seed, workflow=True)
    monkeypatch.setattr(settings(), "allow_private_endpoints", True)
    monkeypatch.setattr(settings(), "trusted_endpoint_hosts", trusted)
    resolve = socket.getaddrinfo
    lookups = 0

    def resolve_provider(hostname, port, *args, **kwargs):
        nonlocal lookups
        if hostname != host:
            return resolve(hostname, port, *args, **kwargs)
        lookups += 1
        assert port == (443 if scheme == "https" else 80)
        if trusted or (failure == "engine_dns" and lookups == 2):
            raise socket.gaierror("fixture DNS failure")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port))]

    def no_http(_):
        pytest.fail("Endpoint policy must fail before any HTTP request")

    monkeypatch.setattr(socket, "getaddrinfo", resolve_provider)
    monkeypatch.setenv("CM_ALLOW_PRIVATE_ENDPOINTS", "true")
    monkeypatch.setenv("CM_TRUSTED_ENDPOINT_HOSTS", trusted)
    with pytest.raises(llm.InferenceError, match=message) as caught:
        execute(ids["a"], values["a"], no_http)
    assert caught.value.code == code and not caught.value.consumption_unknown
    _, attempts, admissions = rows()
    assert len(attempts) == 1 and len(admissions) == 1 and admissions[0].status == "released"
    assert attempts[0].usage_state == "known" and attempts[0].usage_tokens == 0
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan_id, "analysis")).charged_tokens == 0


def test_package_transport_value_error_after_dispatch_retains_unknown_usage(seed, monkeypatch):
    monkeypatch.setattr(llm, "safe_endpoint", lambda url: url)
    monkeypatch.setattr(llm.engine_http, "safe_endpoint", lambda url: url)
    _, ids, values, _ = planned(seed, workflow=True)

    def fail_after_dispatch(_):
        raise ValueError("Fixture transport failure")

    with pytest.raises(ValueError, match="Fixture transport failure"):
        execute(ids["a"], values["a"], fail_after_dispatch)
    tasks, attempts, _ = rows()
    assert len(attempts) == 1 and attempts[0].usage_state == "unknown" and attempts[0].usage_tokens is None
    assert tasks[0].status == "uncertain"


@pytest.mark.parametrize("known_usage", [True, False])
@pytest.mark.parametrize("budget", [10_000, None])
def test_valid_result_persists_known_or_retained_unknown_usage(seed, known_usage, budget):
    plan, ids, values, snapshot = planned(seed, requests={"a": request(), "b": request()}, budgets={"analysis": budget})
    wire, authorities = [], []

    def handler(message):
        wire.append(message)
        return httpx.Response(200, json=result("openai", usage=known_usage))

    response = execute(ids["a"], values["a"], handler, authorize=lambda: authorities.append(True))
    assert response.output == {"ok": True}
    assert len(wire) == 1 and len(authorities) == 3
    tasks, attempts, tickets = rows()
    assert next(task for task in tasks if task.id == ids["a"]).result == {"ok": True}
    assert attempts[0].status == "completed" and attempts[0].provider_request_id == "request-fixture"
    assert attempts[0].usage_state == ("known" if known_usage else "unknown")
    assert tickets[0].status == "released"
    with transaction() as db:
        charge = db.get(AnalysisBudget, (plan, "analysis")).charged_tokens
    assert charge == (12 if known_usage else llm.reservation(snapshot, values["a"]) + 32)
    if not known_usage:
        assert execute(ids["b"], values["b"], handler) is None
        assert len(wire) == 1


def test_request_mutation_fails_before_reservation_or_http(seed):
    plan, ids, values, _ = planned(seed)
    mutated = values["a"].model_copy(update={"instructions": "Different immutable input"})
    with pytest.raises(ValueError, match="immutable"):
        execute(ids["a"], mutated, lambda _: pytest.fail("Must not dispatch"))
    tasks, attempts, tickets = rows()
    assert tasks[0].status == "pending" and not attempts and not tickets
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 0


@pytest.mark.parametrize("failure", ["schema", "caller"])
def test_invalid_result_retains_actual_consumption_and_provider_identity(seed, failure):
    _, ids, values, _ = planned(seed)

    def validate(response):
        if failure == "caller":
            raise ValueError("Evidence failed workload validation")
        return response.output

    with pytest.raises(llm_http.InferenceError if failure == "schema" else ValueError):
        execute(ids["a"], values["a"], lambda _: httpx.Response(200, json=result(
            "openai", content='{"ok":"wrong"}' if failure == "schema" else '{"ok":true}')),
                validate_result=validate)
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and tasks[0].status == "failed"
    assert attempts[0].usage_tokens == 12 and attempts[0].provider_request_id == "request-fixture"
    assert tickets[0].status == "released"


@pytest.mark.parametrize("deny_at", [1, 2, 3])
def test_authority_is_revalidated_before_claim_dispatch_and_accepting_evidence(seed, deny_at):
    _, ids, values, _ = planned(seed)
    checks, wire = [], []

    def authorize():
        checks.append(True)
        if len(checks) == deny_at:
            raise PermissionError("The original requester no longer has access")

    def handler(_):
        wire.append(True)
        return httpx.Response(200, json=result("openai"))

    with pytest.raises(PermissionError):
        execute(ids["a"], values["a"], handler, authorize=authorize)
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and len(wire) == (1 if deny_at == 3 else 0)
    if deny_at == 1:
        assert not attempts and not tickets
    else:
        assert attempts[0].usage_tokens == (12 if deny_at == 3 else 0)
        assert tickets[0].status == "released"


def test_stale_job_cannot_persist_a_response_and_keeps_reserved_consumption(seed):
    plan, ids, values, snapshot = planned(seed)

    def handler(_):
        with transaction() as db:
            db.get(Job, "job").attempts = 2
        return httpx.Response(200, json=result("openai"))

    with pytest.raises(llm_http.InferenceError) as caught:
        execute(ids["a"], values["a"], handler)
    assert caught.value.code == "stale_attempt"
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and tickets[0].status == "released"
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == llm.reservation(snapshot, values["a"]) + 32
    assert analysis.claim_task(ids["a"], "job", 2, input_tokens=1, output_tokens=1) is None
    assert not analysis.retry_task(ids["a"], "job", 2)
    with transaction() as db:
        assert db.get(AnalysisAttempt, attempts[0].id).usage_state == "unknown"


def test_cancellation_closes_http_before_releasing_capacity_and_preserves_unknown_usage(seed):
    plan, ids, values, snapshot = planned(seed)
    trace = []

    async def run():
        entered = asyncio.Event()

        class WaitingBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b"unreachable"

            async def aclose(self):
                trace.append("closed")
                with transaction() as db:
                    assert db.scalar(select(InferenceAdmission).where(InferenceAdmission.status == "active"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, stream=WaitingBody())), trust_env=False) as client:
            pending = asyncio.create_task(llm.execute_task(ids["a"], "job", 1, values["a"], CREDENTIALS,
                client=client, authorize=lambda: None, validate_result=lambda response: response.output))
            await asyncio.wait_for(entered.wait(), timeout=3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        trace.append("returned")

    asyncio.run(run())
    assert trace == ["closed", "returned"]
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and attempts[0].usage_state == "unknown"
    assert tickets[0].status == "released"
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == llm.reservation(snapshot, values["a"]) + 32


def test_cancelled_claim_waits_for_short_db_admission_then_releases_without_dispatch(seed):
    plan, ids, values, _ = planned(seed)
    waiting = Event()

    def querying(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM connections" in statement and "FOR UPDATE" in statement:
            waiting.set()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: pytest.fail("Cancellation must precede dispatch")), trust_env=False) as client:
            with transaction() as db:
                db.scalar(select(Connection).where(Connection.id == "connection").with_for_update())
                waiting.clear()
                pending = asyncio.create_task(llm.execute_task(ids["a"], "job", 1, values["a"], CREDENTIALS,
                    client=client, authorize=lambda: None, validate_result=lambda response: response.output))
                assert await asyncio.to_thread(waiting.wait, 5)
                pending.cancel()
                # Let cancellation reach the shielded claim before releasing the lock.
                await asyncio.sleep(0)
            with pytest.raises(asyncio.CancelledError):
                await pending

    event.listen(engine(), "before_cursor_execute", querying)
    try:
        asyncio.run(run())
    finally:
        event.remove(engine(), "before_cursor_execute", querying)
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and attempts[0].usage_tokens == 0
    assert tickets[0].status == "released"
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 0


def test_concurrent_gateway_requests_share_connection_and_saved_per_run_caps(seed):
    jobs = {}
    for index in range(5):
        job = f"job-{index}"
        count = 3 if index == 0 else 2 if index < 4 else 1
        jobs[job] = planned(seed, job=job, requests={str(i): request() for i in range(count)})
    wire, inflight = [], set()

    async def run():
        full, finish = asyncio.Event(), asyncio.Event()

        async def handler(message):
            wire.append(message)
            inflight.add(id(message))
            if len(inflight) == 8:
                full.set()
            try:
                await finish.wait()
                return httpx.Response(200, json=result("openai"))
            finally:
                inflight.remove(id(message))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            async def start(job, key):
                _, ids, values, _ = jobs[job]
                return await llm.execute_task(ids[key], job, 1, values[key], CREDENTIALS, client=client,
                    authorize=lambda: None, validate_result=lambda response: response.output)

            pending = [asyncio.create_task(start(f"job-{index}", str(key))) for index in range(4) for key in range(2)]
            try:
                await asyncio.wait_for(full.wait(), timeout=5)
                assert await start("job-0", "2") is None
                assert await start("job-4", "0") is None
                with transaction() as db:
                    active = list(db.scalars(select(InferenceAdmission).where(InferenceAdmission.status == "active")))
                    assert len(active) == 8 and all(sum(row.run_key == key for row in active) == 2
                                                  for key in {row.run_key for row in active})
            finally:
                finish.set()
                results = await asyncio.gather(*pending)
            assert all(response.output == {"ok": True} for response in results)

    asyncio.run(run())
    assert len(wire) == 8 and not inflight
    tasks, attempts, tickets = rows()
    assert len(attempts) == 8 and sum(attempt.usage_tokens for attempt in attempts) == 96
    assert not any(ticket.status == "active" for ticket in tickets)
    assert sum(task.status == "pending" for task in tasks) == 2


@pytest.mark.parametrize("kind", ["budget", "ceiling"])
def test_finite_budget_and_prompt_ceiling_fail_without_http_or_cross_stage_consumption(seed, kind):
    plan, ids, values, _ = planned(seed, requests={"a": request(), "summary": request()},
        stages={"a": "analysis", "summary": "summary"}, budgets={"analysis": 1, "summary": 10_000},
        options={"prompt_token_ceiling": 1} if kind == "ceiling" else None)
    if kind == "ceiling":
        with pytest.raises(ValueError, match="prompt token ceiling"):
            execute(ids["a"], values["a"], lambda _: pytest.fail("Must not dispatch"))
    else:
        assert execute(ids["a"], values["a"], lambda _: pytest.fail("Must not dispatch")) is None
        assert execute(ids["summary"], values["summary"], lambda _: httpx.Response(
            200, json=result("openai"))).output == {"ok": True}
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 0
        assert db.get(AnalysisBudget, (plan, "summary")).charged_tokens == (12 if kind == "budget" else 0)


@pytest.mark.parametrize("recover", [True, False])
def test_only_known_zero_consumption_rate_limits_retry_and_stop_after_three(seed, monkeypatch, recover):
    _, ids, values, _ = planned(seed)
    wire, delays = [], []
    original_bounded = llm_http._bounded

    async def bounded(awaitable, stopped, timeout):
        if getattr(awaitable, "cr_code", None) is asyncio.sleep.__code__:
            delays.append(awaitable.cr_frame.f_locals["delay"])
            awaitable.close()
            return
        return await original_bounded(awaitable, stopped, timeout)

    monkeypatch.setattr(llm_http, "_bounded", bounded)

    def handler(_):
        wire.append(True)
        if recover and len(wire) == 3:
            return httpx.Response(200, json=result("openai"))
        return httpx.Response(429, json={"error": {"code": "rate_limit"}}, headers={"retry-after": "0"})

    if recover:
        assert execute(ids["a"], values["a"], handler).output == {"ok": True}
    else:
        with pytest.raises(llm_http.InferenceError) as caught:
            execute(ids["a"], values["a"], handler)
        assert caught.value.code == "rate_limit"
    tasks, attempts, tickets = rows()
    assert len(wire) == len(attempts) == len(tickets) == 3 and len(delays) == 2
    assert attempts[0].usage_tokens == attempts[1].usage_tokens == 0
    assert all(ticket.status == "released" for ticket in tickets)
    assert tasks[0].status == ("completed" if recover else "failed")


@pytest.mark.parametrize(("options", "limit"), [({}, 120), ({"timeout_sec": 900}, 900)])
@pytest.mark.parametrize("source", ["connection", "workflow", "stage"])
@pytest.mark.parametrize("failure", ["httpx", "deadline"])
def test_timeout_names_limiting_deadline_and_never_retries_unknown_usage(seed, monkeypatch, options, limit, source, failure):
    started = now()
    plan, ids, values, snapshot = planned(seed, options=options,
        deadlines={"analysis": started + timedelta(seconds=20)} if source == "stage" else None)
    monkeypatch.setattr(llm, "now", lambda: started)
    wire = []

    def handler(message):
        wire.append(True)
        raise httpx.ReadTimeout("fixture timeout", request=message)

    original = llm_http.InferenceError("timeout", "The inference request exceeded its deadline.",
        request_id="timeout-fixture", status_code=200, retry_after=7)
    if failure == "deadline":
        async def send(*args, **kwargs):
            wire.append(True)
            raise original

        monkeypatch.setattr(llm, "send", send)
    with pytest.raises(llm_http.InferenceError) as caught:
        execute(ids["a"], values["a"], handler, timeout=30 if source in {"workflow", "stage"} else None)
    assert caught.value.code == "timeout" and len(wire) == 1
    assert caught.value.consumption_unknown and not caught.value.retryable
    if source == "connection":
        assert f"per-request timeout ({limit} seconds)" in str(caught.value)
        assert "Settings > AI connections > Advanced" in str(caught.value) and "timeout_sec" in str(caught.value)
    elif source == "workflow":
        assert "workflow inference deadline" in str(caught.value) and "30 seconds" in str(caught.value)
        assert "Start a new run" in str(caught.value) and "timeout_sec" not in str(caught.value)
    else:
        assert "saved stage deadline" in str(caught.value) and "20 seconds" in str(caught.value)
        assert "Start a new run" in str(caught.value) and "timeout_sec" not in str(caught.value)
    if failure == "deadline":
        assert caught.value is original and caught.value.request_id == "timeout-fixture"
        assert caught.value.status_code == 200 and caught.value.retry_after == 7
    tasks, attempts, tickets = rows()
    assert len(attempts) == 1 and attempts[0].error_code == "timeout"
    assert tasks[0].status == "uncertain" and attempts[0].usage_state == "unknown" and attempts[0].usage_tokens is None
    assert tickets[0].status == "released"
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == llm.reservation(snapshot, values["a"]) + 32


def test_local_transport_deadline_names_request_limit_and_closes_response(seed, monkeypatch):
    _, ids, values, _ = planned(seed)
    clock, closed = [0.0], []
    monkeypatch.setattr(llm_http, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class WaitingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            clock[0] = 121
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            closed.append(True)

    with pytest.raises(llm_http.InferenceError, match="per-request timeout \\(120 seconds\\)") as caught:
        execute(ids["a"], values["a"], lambda _: httpx.Response(200, stream=WaitingBody()))
    assert caught.value.code == "timeout" and closed == [True]
    tasks, attempts, tickets = rows()
    assert len(attempts) == 1 and attempts[0].usage_state == "unknown" and tasks[0].status == "uncertain"
    assert tickets[0].status == "released"


@pytest.mark.parametrize("limited", [False, True])
def test_provider_failures_preserve_precise_codes_and_charges_without_retry(seed, limited):
    plan, ids, values, _ = planned(seed)
    wire = []

    def handler(message):
        wire.append(True)
        return (httpx.Response(200, json=result("openai", stop="length")) if limited
                else httpx.Response(500, json={"error": {"message": "PRIVATE_PROVIDER_TEXT"}}))

    code = "output_limit" if limited else "provider_http_500"
    with pytest.raises(llm_http.InferenceError) as caught:
        execute(ids["a"], values["a"], handler)
    assert caught.value.code == code and len(wire) == 1
    tasks, attempts, tickets = rows()
    assert len(attempts) == 1 and attempts[0].error_code == code
    assert tasks[0].status == ("failed" if limited else "uncertain") and tasks[0].result is None
    assert attempts[0].usage_tokens == (12 if limited else None)
    assert attempts[0].usage_state == ("known" if limited else "unknown")
    assert tickets[0].status == "released"
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == (
            12 if limited else attempts[0].reserved_tokens)


@pytest.mark.parametrize(("options", "expected"), [({"timeout_sec": 300}, 300), ({"timeout_sec": 45}, 45), ({}, 120)])
def test_request_snapshot_timeout_preserves_legacy_and_explicit_limits(seed, monkeypatch, options, expected):
    _, ids, values, _ = planned(seed, options=options)
    with transaction() as db:
        assert db.get(Connection, "connection").options == options
    clock, durations = [0.0], []
    monkeypatch.setattr(llm, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def send(*args, timeout, **kwargs):
        durations.append(timeout)
        clock[0] += 121
        return Response(output={"ok": True}, usage=Usage(total_tokens=12))

    monkeypatch.setattr(llm, "send", send)
    if expected > 121:
        assert execute(ids["a"], values["a"], lambda _: pytest.fail("No provider calls")).output == {"ok": True}
    else:
        with pytest.raises(llm_http.InferenceError) as caught:
            execute(ids["a"], values["a"], lambda _: pytest.fail("No provider calls"))
        assert caught.value.code == "cancelled"
    assert durations == [expected]
    _, attempts, tickets = rows()
    assert attempts[0].usage_tokens == 12 and attempts[0].usage_state == "known"
    assert tickets[0].status == "released"


@pytest.mark.parametrize("reason", ["cancelled", "revoked"])
def test_queued_cancellation_or_revocation_cleans_the_exact_waiter_without_charging(seed, reason):
    plan, ids, values, _ = planned(seed)

    def denied():
        raise PermissionError("Original authority was revoked")

    with inference_admission.reserve_connection("connection", "legacy"):
        assert execute(ids["a"], values["a"], lambda _: pytest.fail("Must remain queued")) is None
        with pytest.raises(llm_http.InferenceError if reason == "cancelled" else PermissionError):
            execute(ids["a"], values["a"], lambda _: pytest.fail("Must not dispatch cancelled work"),
                    stopped=lambda: reason == "cancelled", authorize=denied)
    tasks, attempts, tickets = rows()
    assert tasks[0].status == "pending" and not attempts
    assert not any(ticket.status in {"queued", "active"} for ticket in tickets)
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == 0


def test_gateway_detaches_nested_request_before_authorization_can_mutate_the_caller_input(seed):
    import json

    original = Request.model_validate({**request().model_dump(mode="python"), "tools": [{
        "name": "read_context", "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}]})
    _, ids, _, snapshot = planned(seed, requests={"a": original})
    original_hash = llm.input_fingerprint(original)
    reservation = llm.reservation(snapshot, original) + original.max_output_tokens
    checks, wire = [], []

    def authorize():
        checks.append(True)
        if len(checks) == 1:
            original.messages[0].content = "Caller mutated the nested message"
            original.output_schema["properties"]["ok"]["type"] = "string"
            original.tools[0].parameters["properties"]["path"]["type"] = "integer"
            original.max_output_tokens = 99999

    def handler(message):
        body = json.loads(message.content)
        wire.append(body)
        assert body["messages"][0]["content"] == "Review this fixed input"
        assert body["max_tokens"] == 32
        assert body["response_format"]["json_schema"]["schema"]["properties"]["ok"]["type"] == "boolean"
        assert body["tools"][0]["function"]["parameters"]["properties"]["path"]["type"] == "string"
        return httpx.Response(200, json=result("openai"))

    response = execute(ids["a"], original, handler, authorize=authorize)
    assert response.output == {"ok": True} and len(wire) == 1
    assert llm.input_fingerprint(original) != original_hash and original.max_output_tokens == 99999
    tasks, attempts, tickets = rows()
    assert tasks[0].input_hash == original_hash and tasks[0].result == {"ok": True}
    assert attempts[0].reserved_tokens == reservation and attempts[0].usage_tokens == 12
    assert tickets[0].status == "released"


def test_authority_revoked_during_validation_discards_result_but_records_usage(seed):
    _, ids, values, _ = planned(seed)
    revoked = False

    def authorize():
        if revoked:
            raise PermissionError("Revoked while validating evidence")

    def validate(response):
        nonlocal revoked
        revoked = True
        return response.output

    with pytest.raises(PermissionError, match="Revoked"):
        execute(ids["a"], values["a"], lambda _: httpx.Response(200, json=result("openai")),
                authorize=authorize, validate_result=validate)
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and attempts[0].usage_tokens == 12
    assert tickets[0].status == "released"


def test_cancellation_drains_validator_thread_before_releasing_capacity(seed):
    _, ids, values, _ = planned(seed)
    entered, release, finished = Event(), Event(), Event()

    def validate(response):
        entered.set()
        assert release.wait(5), "The async loop must remain responsive during evidence validation"
        with transaction() as db:
            assert db.scalar(select(InferenceAdmission).where(InferenceAdmission.status == "active"))
        finished.set()
        return response.output

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=result("openai"))), trust_env=False) as client:
            pending = asyncio.create_task(llm.execute_task(ids["a"], "job", 1, values["a"], CREDENTIALS,
                client=client, authorize=lambda: None, validate_result=validate))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                pending.cancel()
                await asyncio.sleep(0)
                assert not pending.done() and not finished.is_set()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert finished.is_set()

    asyncio.run(run())
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and attempts[0].usage_tokens == 12
    assert tickets[0].status == "released"


def test_capacity_polling_can_use_local_checks_but_dispatch_and_acceptance_require_authority(seed):
    _, ids, values, _ = planned(seed)
    checks, wire = [], []

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda message: wire.append(message) or httpx.Response(200, json=result("openai"))), trust_env=False) as client:
            async def dispatch():
                return await llm.execute_task(ids["a"], "job", 1, values["a"], CREDENTIALS, client=client,
                    authorize=lambda: checks.append("external"), authorize_admission=lambda: checks.append("local"),
                    validate_result=lambda response: response.output)

            with inference_admission.reserve_connection("connection", "legacy"):
                for _ in range(3):
                    assert await dispatch() is None
                assert checks == ["local"] * 3 and not wire
            assert (await dispatch()).output == {"ok": True}

    asyncio.run(run())
    assert checks == ["local"] * 4 + ["external"] * 2 and len(wire) == 1


def test_repeated_cancellation_drains_blocked_claim_and_releases_its_reservation(monkeypatch):
    from types import SimpleNamespace

    entered, release = Event(), Event()
    trace = []

    def claim(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        trace.append("reserved")
        return SimpleNamespace(admission_id="fixture-admission")

    def finish(lease, **kwargs):
        assert kwargs == {"usage_tokens": 0, "error_code": "cancelled"}
        trace.append("finished")

    monkeypatch.setattr(analysis, "claim_task", claim)
    monkeypatch.setattr(analysis, "finish_task", finish)
    monkeypatch.setattr(inference_admission, "release_connection", lambda identity: trace.append("released"))
    monkeypatch.setattr(inference_admission, "cancel_task_waiter", lambda *args: trace.append("waiter_cancelled"))

    async def run():
        pending = asyncio.create_task(llm._claim("task", "job", 1, input_tokens=1, output_tokens=1))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(run())
    assert trace == ["reserved", "finished", "released", "waiter_cancelled"]


def test_repeated_cancellation_waits_for_http_close_before_releasing_capacity(seed):
    plan, ids, values, snapshot = planned(seed)
    trace = []

    async def run():
        entered, closing, allow_close = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class WaitingBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b"unreachable"

            async def aclose(self):
                trace.append("closing")
                closing.set()
                await allow_close.wait()
                with transaction() as db:
                    assert db.scalar(select(InferenceAdmission).where(InferenceAdmission.status == "active"))
                trace.append("closed")

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, stream=WaitingBody())), trust_env=False) as client:
            pending = asyncio.create_task(llm.execute_task(ids["a"], "job", 1, values["a"], CREDENTIALS,
                client=client, authorize=lambda: None, validate_result=lambda response: response.output))
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                pending.cancel()
                await asyncio.wait_for(closing.wait(), timeout=3)
                pending.cancel()
                await asyncio.sleep(0)
                assert not pending.done() and trace == ["closing"]
            finally:
                allow_close.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
        trace.append("returned")

    asyncio.run(run())
    assert trace == ["closing", "closed", "returned"]
    tasks, attempts, tickets = rows()
    assert tasks[0].result is None and attempts[0].usage_state == "unknown"
    assert tickets[0].status == "released"
    with transaction() as db:
        assert db.get(AnalysisBudget, (plan, "analysis")).charged_tokens == llm.reservation(snapshot, values["a"]) + 32
