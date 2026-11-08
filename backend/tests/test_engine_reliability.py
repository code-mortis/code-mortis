"""M5 fault evidence: real Redis/Celery, killed processes, PostgreSQL and localhost HTTP.

GitHub authorization and repository evidence are synthetic. No live provider or
GitHub writes run here; these cases establish recovery/accounting, not quality.
"""

import asyncio
import copy
import json
import multiprocessing
import signal
from contextlib import contextmanager, suppress
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from uuid import uuid4

import pytest
from redis import Redis
from sqlalchemy import select
from test_llm_execution import CREDENTIALS, planned, request
from test_llm_http import result as wire_result
from test_mortis_review import scenario as source_case  # noqa: F401
from test_mortis_workflow import workflow as workflow

from code_mortis import jobs
from code_mortis.core import analysis, inference_admission
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction
from code_mortis.integrations import github, llm
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    AnalysisTask,
    Connection,
    Finding,
    InferenceAdmission,
    Job,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.reviews import background, service
from code_mortis.schemas.common import RunLimits


@pytest.fixture
def provider(monkeypatch):
    state = {"requests": [], "status": 200, "body": wire_result("openai"), "release": Event()}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["requests"].append({"path": self.path, "body": body})
            if state.get("pause"):
                assert state["release"].wait(15)
            encoded = (
                state["body"] if isinstance(state["body"], bytes) else json.dumps(state["body"]).encode()
            )
            with suppress(BrokenPipeError, ConnectionResetError):
                self.send_response(state["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Exercise the real endpoint gate with an explicitly trusted synthetic server.
    monkeypatch.setenv("CM_TRUSTED_ENDPOINT_HOSTS", "127.0.0.1")
    monkeypatch.setattr(settings(), "trusted_endpoint_hosts", "127.0.0.1")
    state["url"] = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        state["release"].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fault_worker(queue, phase, ready, reached, proceed, delivered):
    """Spawned Celery solo worker; only external GitHub/source I/O is replaced."""
    from celery.signals import task_postrun, worker_ready

    github.authorize_repo = lambda *args, **kwargs: {"default_branch": "main"}
    github.installation_token = lambda *args, **kwargs: "synthetic-installation-token"
    background.context = lambda *args, **kwargs: {
        "repository": "team/project",
        "branch": "main",
        "sha": "a" * 40,
        "paths": ["README.md"],
        "sources": {"README.md": {"text": "A Python service.", "truncated": False}},
    }

    def pause():
        reached.set()
        if not proceed.wait(30):
            raise TimeoutError("Fault controller did not release or kill the worker")

    original_claim, original_send, original_finish = analysis.claim_task, llm.send, analysis.finish_task

    def claim(*args, **kwargs):
        if phase == "before_claim":
            pause()
        lease = original_claim(*args, **kwargs)
        if lease and phase == "before_dispatch":
            pause()
        return lease

    async def send(*args, **kwargs):
        response = await original_send(*args, **kwargs)
        if phase == "after_consumption":
            reached.set()
            while not proceed.is_set():
                await asyncio.sleep(0.01)
        return response

    def finish(*args, **kwargs):
        accepted = original_finish(*args, **kwargs)
        if accepted and kwargs.get("result") is not None and phase == "after_ledger":
            pause()
        return accepted

    analysis.claim_task, llm.send, analysis.finish_task = claim, send, finish
    worker_ready.connect(lambda **kwargs: ready.set(), weak=False)
    task_postrun.connect(lambda **kwargs: delivered.set(), weak=False)
    jobs.celery.worker_main(
        [
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--queues=" + queue,
            "--hostname=" + queue + "@%h",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            "--loglevel=ERROR",
        ]
    )


@contextmanager
def worker(queue, phase=""):
    context = multiprocessing.get_context("spawn")
    ready, reached, proceed, delivered = (context.Event() for _ in range(4))
    process = context.Process(target=_fault_worker, args=(queue, phase, ready, reached, proceed, delivered))
    process.start()
    try:
        assert ready.wait(15), f"Celery worker failed to start (exit={process.exitcode})"
        yield process, reached, proceed, delivered
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=10)
        assert not process.is_alive(), "Fault worker must be dead before recovery or database cleanup"


def clear_broker_queue(queue):
    with Redis.from_url(settings().redis_url) as broker:
        # Killed late-ack deliveries must not reappear during an unrelated test.
        for tag, raw in broker.hgetall("unacked").items():
            if json.loads(raw)[2] == queue:
                broker.hdel("unacked", tag)
                broker.zrem("unacked_index", tag)
        broker.delete(queue, "_kombu.binding." + queue)


@pytest.mark.parametrize("phase", ["before_claim", "before_dispatch", "after_consumption", "after_ledger"])
def test_killed_celery_background_never_repeats_inference_or_returns_false_success(
    seed, provider, monkeypatch, phase
):
    provider["body"] = wire_result("openai", content='{"background":"A Python service."}')
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.base_url = provider["url"]
        job = background.enqueue(
            db,
            db.get(Repository, 22),
            db.get(User, 7),
            connection,
            "",
            RunLimits(token_budget=60_000, timeout_seconds=120),
        )
        job.payload = {**job.payload, "analysis_engine": "mortis"}
        identity = job.id
    queue = "m5-fault-" + uuid4().hex
    try:
        with worker(queue, phase) as (process, reached, _proceed, _delivered):
            jobs.execute_job.apply_async(args=[identity], queue=queue)
            assert reached.wait(15), f"Worker never reached {phase} (exit={process.exitcode})"
            if phase == "before_dispatch":
                # A second real worker consumes duplicate broker delivery while
                # the first owns the job; it cannot make a second task claim.
                with worker(queue) as (_duplicate, _boundary, _resume, delivered):
                    jobs.execute_job.apply_async(args=[identity], queue=queue)
                    assert delivered.wait(10)
                    with transaction() as db:
                        assert db.get(Job, identity).attempts == 1
                        assert len(list(db.scalars(select(AnalysisAttempt)))) == 1
                    assert provider["requests"] == []
            process.kill()
            process.join(timeout=10)
            assert process.exitcode == -signal.SIGKILL

        with transaction() as db:
            job = db.get(Job, identity)
            assert job.status == "running" and "background" not in job.payload
            job.lease_until = now() - timedelta(seconds=1)
        redispatches = []
        monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: redispatches.append(kwargs))
        jobs.dispatch()
        jobs.execute_job(identity)  # An old broker delivery after recovery is inert.
        jobs.dispatch()
        assert redispatches == []
        assert len(provider["requests"]) == (1 if phase in {"after_consumption", "after_ledger"} else 0)
        with transaction() as db:
            job = db.get(Job, identity)
            assert job.status == "failed" and job.error == background.INTERRUPTED
            assert background.job_out(job).background is None
            assert db.get(Repository, 22).settings.get("background") is None
            task = db.scalar(select(AnalysisTask))
            budget = db.get(AnalysisBudget, (task.plan_id, "summary"))
            attempt = db.scalar(select(AnalysisAttempt))
            ticket = db.scalar(select(InferenceAdmission))
            if phase == "before_claim":
                assert task.status == "pending" and attempt is None and ticket is None
                assert budget.charged_tokens == 0
            elif phase == "after_ledger":
                assert (
                    task.status == "completed" and task.result["result"]["background"] == "A Python service."
                )
                assert attempt.usage_state == "known" and attempt.usage_tokens == budget.charged_tokens == 12
                assert ticket.status == "released"
            else:
                assert task.result is None and task.status == "running"
                assert attempt.usage_tokens is None and attempt.usage_state == "reserved"
                assert budget.charged_tokens == attempt.reserved_tokens > 0
                assert ticket.status == "active"
                ticket_id = ticket.id
        if phase in {"before_dispatch", "after_consumption"}:
            # Expiry alone cannot prove that transport stopped. The controller
            # has confirmed SIGKILL; only then is an explicit release safe.
            assert inference_admission.release_connection(ticket_id)
    finally:
        clear_broker_queue(queue)


def execute_http(task_id, value, *, timeout=None):
    async def run():
        async with llm.http_client() as client:
            return await llm.execute_task(
                task_id,
                "job",
                1,
                value,
                CREDENTIALS,
                client=client,
                authorize=lambda: None,
                validate_result=lambda response: response.output,
                timeout=timeout,
            )

    return asyncio.run(run())


def test_mortis_uncertain_publication_reconciles_saved_plan_without_inference(workflow, monkeypatch):
    service.perform_review("mortis-run", "mortis-job:1")
    with transaction() as db:
        db.get(Job, "mortis-job").status = "done"
        assert db.get(ReviewRun, "mortis-run").status == "completed"
    inferred = list(workflow["requests"])
    original = github.request

    def lost_response(method, path, token, **kwargs):
        response = original(method, path, token, **kwargs)
        if method == "POST" and path.endswith("/reviews"):
            raise github.GitHubError(503, "Synthetic response lost after durable remote write")
        return response

    monkeypatch.setattr(github, "request", lost_response)
    with pytest.raises(github.GitHubError):
        service.publish_run("mortis-run")
    with transaction() as db:
        saved = copy.deepcopy(db.get(ReviewRun, "mortis-run").publication_plan)
        assert saved and db.scalar(select(Finding)).status == "open"
    monkeypatch.setattr(llm, "http_client", lambda: pytest.fail("Publication cannot repeat inference"))
    monkeypatch.setattr(service, "comment_body", lambda *args, **kwargs: pytest.fail("Reuse the saved plan"))
    service.publish_run("mortis-run")
    service.publish_run("mortis-run")
    assert workflow["requests"] == inferred and len(workflow["reviews"]) == 1
    with transaction() as db:
        run = db.get(ReviewRun, "mortis-run")
        assert run.publication == "published" and run.review_ids == [1]
        assert run.publication_plan == saved
        assert db.scalar(select(Finding)).status == "open"


@pytest.mark.parametrize(
    "failure", ["rate_limit", "authentication", "unavailable", "timeout", "schema", "json", "unknown"]
)
def test_real_http_failures_preserve_consumption_and_never_fallback(seed, provider, failure):
    with transaction() as db:
        db.get(Connection, "connection").base_url = provider["url"]
    plan_id, ids, values, _ = planned(seed, requests={"a": request(), "b": request()})
    expected = {
        "rate_limit": (429, "rate_limit", 0),
        "authentication": (401, "authentication", 0),
        "unavailable": (503, "provider_error", None),
        "timeout": (200, "timeout", None),
        "schema": (200, "malformed_response", 12),
        "json": (200, "malformed_response", None),
    }
    if failure in expected:
        provider["status"], code, tokens = expected[failure]
        if failure == "schema":
            provider["body"] = wire_result("openai", content='{"ok":"wrong"}')
        elif failure == "json":
            provider["body"] = b"not valid JSON"
        elif failure == "timeout":
            provider["pause"] = True
        elif provider["status"] >= 400:
            provider["body"] = {"error": {"message": "synthetic failure"}}
        with pytest.raises(llm.InferenceError) as caught:
            execute_http(ids["a"], values["a"], timeout=0.5 if failure == "timeout" else None)
        assert caught.value.code == code
    else:
        tokens = None
        provider["body"] = wire_result("openai", usage=False)
        assert execute_http(ids["a"], values["a"]).output == {"ok": True}

    expected_calls = 3 if failure == "rate_limit" else 1
    assert len(provider["requests"]) == expected_calls
    assert all(
        row["path"] == "/v1/chat/completions" and row["body"]["model"] == "test-model"
        for row in provider["requests"]
    )
    with transaction() as db:
        task = db.get(AnalysisTask, ids["a"])
        attempts = list(db.scalars(select(AnalysisAttempt)))
        assert len(attempts) == expected_calls and all(row.usage_tokens == tokens for row in attempts)
        assert all(row.usage_state == ("known" if tokens is not None else "unknown") for row in attempts)
        assert task.status == (
            "completed" if failure == "unknown" else "failed" if tokens is not None else "uncertain"
        )
        assert (task.result is not None) == (failure == "unknown")
        budget = db.get(AnalysisBudget, (plan_id, "analysis"))
        assert budget.charged_tokens == (
            tokens * expected_calls if tokens is not None else attempts[0].reserved_tokens
        )
        assert all(row.status == "released" for row in db.scalars(select(InferenceAdmission)))
    if tokens is None:
        assert execute_http(ids["b"], values["b"]) is None
        assert not analysis.retry_task(ids["a"], "job", 1)
        assert len(provider["requests"]) == expected_calls
