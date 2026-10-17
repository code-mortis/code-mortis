from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from test_review_flow import remote

from code_mortis import github, jobs, reviews
from code_mortis.db import now, transaction
from code_mortis.models import Job


@pytest.mark.parametrize("backlog", [0, 2834])
def test_new_webhooks_and_their_pr_sync_bypass_historical_backlog(seed, monkeypatch, backlog):
    with transaction() as db:
        for number in range(150):
            job = reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": number, "automatic": False})
            job.available_at = now() - timedelta(hours=3)
        webhook = reviews.enqueue(db, "webhook", {"event": "pull_request"}).id
        live_sync = reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": 151, "sender_type": "User"}).id
    sent = []
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: sent.append(kwargs))
    monkeypatch.setattr(jobs.Redis, "from_url", lambda *a, **k: nullcontext(SimpleNamespace(llen=lambda _: backlog)))
    jobs.dispatch()
    assert {call["args"][0] for call in sent[:2]} == {webhook, live_sync}
    assert [call["queue"] for call in sent[:2]] == ["events", "events"]
    assert len(sent) == (2 if backlog else 100)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(
            Job.kind == "sync_pr", Job.dispatched_at.is_(None),
        )) == (150 if backlog else 52)


def test_dispatch_caps_sync_messages_to_available_broker_space(seed, monkeypatch):
    with transaction() as db:
        for number in range(5):
            reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": number})
    sent = []
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: sent.append(kwargs))
    monkeypatch.setattr(jobs.Redis, "from_url", lambda *a, **k: nullcontext(SimpleNamespace(llen=lambda _: 99)))
    jobs.dispatch()
    assert len(sent) == 1 and sent[0]["queue"] == "sync"


@pytest.mark.parametrize("pending_state", ["queued", "running"])
def test_repeated_reconciliation_keeps_one_pending_job_per_target(seed, pending_state):
    jobs.schedule_reconciliation()
    with transaction() as db:
        for job in db.scalars(select(Job)):
            job.status = pending_state
    jobs.schedule_reconciliation()
    with transaction() as db:
        assert dict(db.execute(select(Job.kind, func.count()).group_by(Job.kind)).all()) == {
            "sync_repositories": 1, "scan_repository": 1, "sync_pr": 1,
        }


def test_scans_coalesce_background_work_without_coalescing_webhooks(seed, github_stub, monkeypatch):
    monkeypatch.setattr(github, "pages", lambda *a: [remote()])
    jobs.scan_repository(22)
    jobs.scan_repository(22)
    for action in ["opened", "synchronize"]:
        jobs.process_webhook({"event": "pull_request", "body": {
            "action": action, "installation": {"id": 10}, "repository": {"id": 22},
            "sender": {"type": "User"}, "pull_request": remote(),
        }})
    with transaction() as db:
        payloads = list(db.scalars(select(Job.payload).where(Job.kind == "sync_pr")))
    assert len(payloads) == 3
    assert sum(payload.get("sender_type") == "User" for payload in payloads) == 2
    assert jobs.job_queue("sync_pr", next(payload for payload in payloads if not payload.get("sender_type"))) == "sync"
    assert jobs.job_queue("sync_pr", next(payload for payload in payloads if payload.get("sender_type"))) == "events"
    assert jobs.job_queue("webhook") == jobs.celery.conf.beat_schedule["dispatch"]["options"]["queue"] == "events"
