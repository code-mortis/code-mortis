from datetime import timedelta
from threading import Event

import pytest
from celery.contrib.testing.worker import start_worker
from redis import Redis

from code_mortis import jobs
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction
from code_mortis.models import Job


@pytest.mark.parametrize("kind", sorted(jobs.CODE_GRAPH_KINDS))
def test_legacy_graph_delivery_moves_without_consuming_an_attempt(seed, monkeypatch, kind):
    with transaction() as db:
        db.add(Job(id="move", kind=kind, payload={"repository_id": 22}, dispatched_at=now(), attempts=2))
    executed, sent = [], []
    monkeypatch.setattr(jobs, "execute", lambda kind, payload: executed.append(kind))
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kw: sent.append(kw))
    jobs.execute_job.push_request(delivery_info={"routing_key": "reviews"})
    try:
        jobs.execute_job.run("move")
    finally:
        jobs.execute_job.pop_request()
    with transaction() as db:
        job = db.get(Job, "move")
        assert (job.status, job.attempts, job.dispatched_at, job.lease_until) == ("queued", 2, None, None)
    assert executed == []
    jobs.dispatch()
    assert sent == [{"args": ["move"], "queue": "code-graph"}]
    jobs.execute_job.push_request(delivery_info={"routing_key": "code-graph"})
    try:
        jobs.execute_job.run("move")
        jobs.execute_job.run("move")
    finally:
        jobs.execute_job.pop_request()
    with transaction() as db:
        job = db.get(Job, "move")
        assert job.status == "done" and job.attempts == 3
    assert executed == [kind]


def test_active_graph_claim_is_not_reset_by_a_stale_delivery(seed):
    lease = now() + timedelta(minutes=10)
    with transaction() as db:
        db.add(Job(id="active", kind="code_graph", payload={}, status="running", attempts=1, lease_until=lease))
    jobs.execute_job.push_request(delivery_info={"routing_key": "reviews"})
    try:
        jobs.execute_job.run("active")
    finally:
        jobs.execute_job.pop_request()
    with transaction() as db:
        job = db.get(Job, "active")
        assert (job.status, job.attempts, job.lease_until) == ("running", 1, lease)


def test_graph_worker_refuses_a_review_delivery(seed):
    with transaction() as db:
        db.add(Job(id="review", kind="review", payload={"repository_id": 22}, dispatched_at=now()))
    jobs.execute_job.push_request(delivery_info={"routing_key": "code-graph"})
    try:
        jobs.execute_job.run("review")
    finally:
        jobs.execute_job.pop_request()
    with transaction() as db:
        job = db.get(Job, "review")
        assert (job.status, job.attempts, job.dispatched_at) == ("queued", 0, None)
    assert jobs.job_queue("review") == "reviews"


def test_real_broker_graph_work_finishes_while_review_worker_is_busy(seed, monkeypatch):
    """Real isolated Redis/Celery consumers; task bodies never call GitHub or models."""
    review_started, graph_finished, release_review = Event(), Event(), Event()

    def execute(kind, payload):
        if kind == "review":
            review_started.set()
            assert release_review.wait(20), "Synthetic review was not released"
        else:
            assert kind == "code_graph_refresh" and review_started.is_set()
            graph_finished.set()

    monkeypatch.setattr(jobs, "execute", execute)
    with transaction() as db:
        db.add_all([Job(id="review", kind="review", payload={"repository_id": 22}),
                    Job(id="graph", kind="code_graph_refresh", payload={"repository_id": 22})])
    with Redis.from_url(settings().redis_url) as broker:
        broker.delete("reviews", "code-graph")
    try:
        # In-process consumers need separate blocking loops, not Kombu's shared async hub.
        with (start_worker(jobs.celery, pool="solo", concurrency=1, queues=["reviews"], use_eventloop=False,
                           hostname="test-reviews", perform_ping_check=False, shutdown_timeout=20),
              start_worker(jobs.celery, pool="solo", concurrency=1, queues=["code-graph"], use_eventloop=False,
                           hostname="test-graph", perform_ping_check=False, shutdown_timeout=20)):
            try:
                jobs.execute_job.apply_async(args=["review"], queue=jobs.job_queue("review"))
                assert review_started.wait(10)
                jobs.execute_job.apply_async(args=["graph"], queue=jobs.job_queue("code_graph_refresh"))
                assert graph_finished.wait(10), "Graph work waited for the occupied review worker"
                with transaction() as db:
                    assert db.get(Job, "review").status == "running"
            finally:
                release_review.set()
        with transaction() as db:
            assert db.get(Job, "review").status == db.get(Job, "graph").status == "done"
    finally:
        release_review.set()
        with Redis.from_url(settings().redis_url) as broker:
            broker.delete("reviews", "code-graph")
