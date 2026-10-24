import copy
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from test_review_flow import COMMENT, new_run, remote

from code_mortis import checks, github, jobs, ocr, reviews
from code_mortis.db import now, transaction
from code_mortis.models import Connection, Job, Membership, Repository, ReviewRun, User, WebhookDelivery


@pytest.mark.parametrize(
    "state,publication,reason,status,conclusion",
    [
        ("queued", "pending", None, "queued", None),
        ("running", "pending", None, "in_progress", None),
        ("completed", "pending", None, "in_progress", None),
        ("completed", "publishing", None, "in_progress", None),
        ("completed", "published", None, "completed", "success"),
        ("partial", "published", None, "completed", "failure"),
        ("failed", "not_applicable", None, "completed", "failure"),
        ("completed", "failed", None, "completed", "failure"),
        ("completed", "partially_published", None, "completed", "failure"),
        ("failed", "not_applicable", "timeout", "completed", "timed_out"),
        ("blocked", "pending", None, "completed", "action_required"),
        ("cancelled", "not_applicable", None, "completed", "cancelled"),
        ("superseded", "not_applicable", None, "completed", "cancelled"),
        ("completed", "not_applicable", None, "completed", "cancelled"),
        ("skipped", "not_applicable", None, "completed", "skipped"),
    ],
)
def test_lifecycle_mapping_and_safe_summary(state, publication, reason, status, conclusion):
    run = ReviewRun(
        id="run",
        head_sha="a" * 40,
        status=state,
        publication=publication,
        failure_reason=reason,
        created_at=now(),
        error="Bearer secret-token",
        progress={"selected": 3, "completed": 1},
    )
    payload = checks.check_payload(run, 2)
    assert (payload["status"], payload["conclusion"]) == (status, conclusion)
    assert "Findings: 2" in payload["output"]["summary"]
    assert "1 of 3" in payload["output"]["summary"]
    assert "secret-token" not in json.dumps(payload)
    assert payload["details_url"].endswith("/runs/run")


def test_review_limit_warning_cannot_publish_a_successful_check():
    item = {"item_id": "main", "path": "main.py"}
    status, report, _ = ocr.parse_report(json.dumps({
        "manifest": {"schema_version": "ocr.run-manifest/v1", "terminal_state": "complete", "coverage": {
            "selected": [item], "completed": [item], "reused": [], "failed": [], "waived": [],
        }},
        "comments": [], "warnings": [{"type": "review_limit_reached"}],
    }), 0)
    run = ReviewRun(id="run", head_sha="a" * 40, status=status, publication="no_new_comments",
                    report=report, progress={}, created_at=now())
    payload = checks.check_payload(run, 0)
    assert payload["conclusion"] == "failure"
    assert payload["output"]["title"] == "Review incomplete"


@pytest.mark.parametrize("state,title", [
    ("running", "Assessing original commit"),
    ("completed", "Synchronizing verified fixes"),
])
def test_historical_assessment_check_preserves_pending_verification_status(state, title):
    run = ReviewRun(id="run", head_sha="a" * 40, status=state, publication="published", created_at=now(),
                    progress={"assessment_retry": {"assessment_only": True}, "verification": {"pending": 1}})
    payload = checks.check_payload(run, 0)
    assert payload["status"] == "in_progress" and payload["conclusion"] is None
    assert payload["output"]["title"] == title
    assert payload["completed_at"] is None


@pytest.fixture
def check_api(monkeypatch, github_stub):
    stored, writes = [], []
    monkeypatch.setattr(github, "installation_token", lambda *a, **k: "installation-token")
    monkeypatch.setattr(github, "pages", lambda *a, **k: stored)

    def request(method, path, token, **kwargs):
        if method == "GET":
            return remote()
        assert all(value is not None for value in kwargs["json"].values()), "GitHub write fields are not nullable"
        writes.append((method, copy.deepcopy(kwargs["json"])))
        if method == "POST":
            stored.append(
                {
                    **kwargs["json"],
                    "id": len(stored) + 100,
                    "html_url": "https://github.com/team/project/runs/100",
                    "app": {"id": 123},
                }
            )
        return stored[-1]

    monkeypatch.setattr(github, "request", request)
    return stored, writes


def test_unknown_create_response_concurrent_recovery_and_latest_state(seed, check_api, monkeypatch):
    stored, writes = check_api
    run_id = new_run()
    request = github.request

    def lose_response(*args, **kwargs):
        result = request(*args, **kwargs)
        if args[0] == "POST":
            raise github.GitHubError(503, "Response lost")
        return result

    monkeypatch.setattr(github, "request", lose_response)
    with pytest.raises(github.GitHubError):
        checks.sync_check(run_id)
    assert len(stored) == 1
    with transaction() as db:
        assert db.get(ReviewRun, run_id).check_id is None
        db.get(ReviewRun, run_id).status = "running"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(checks.sync_check, [run_id, run_id]))
    assert [method for method, _ in writes] == ["POST", "PATCH"]
    assert writes[-1][1]["status"] == "in_progress"
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.status, run.publication = "cancelled", "not_applicable"
    checks.sync_check(run_id)
    checks.sync_check(run_id)
    assert writes[-1][1]["conclusion"] == "cancelled"
    assert len(writes) == 3 and len(stored) == 1


def test_publication_gates_success_and_does_not_rerun_analysis(seed, check_api, monkeypatch):
    run_id = new_run()
    monkeypatch.setattr(ocr, "review", lambda *a, **k: ("completed", {}, [copy.deepcopy(COMMENT)]))
    reviews.perform_review(run_id)
    checks.sync_check(run_id)
    assert check_api[1][-1][1]["output"]["title"] == "Publishing comments"
    with transaction() as db:
        db.get(ReviewRun, run_id).publication = "published"
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Check synchronization cannot run OCR"))
    checks.sync_check(run_id)
    assert check_api[1][-1][1]["conclusion"] == "success"
    assert "1 finding" in check_api[1][-1][1]["output"]["title"]


@pytest.mark.parametrize("assessed", [False, True])
def test_terminal_check_jobs_drain_without_advancing_assessment_revision(seed, check_api, assessed):
    identity = new_run(assessed=assessed)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.status, run.publication = "completed", "published"
        run.progress = {"assessment_revision": 4}
    # The first sync may persist its summary, requiring one final check update.
    for _ in range(2):
        with transaction() as db:
            queued = list(db.scalars(select(Job.id).where(Job.kind == "sync_check", Job.status == "queued")))
        for job_id in queued:
            jobs.execute_job(job_id)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert run.progress["assessment_revision"] == 4
        assert not run.check_pending
        assert not db.scalar(select(Job.id).where(Job.kind == "sync_check", Job.status == "queued"))
        run.progress = {**run.progress, "assessment_revision": 5}
    with transaction() as db:
        assert not db.get(ReviewRun, identity).check_pending
        assert not db.scalar(select(Job.id).where(Job.kind == "sync_check", Job.status == "queued"))
    assert len(check_api[1]) == 1


def test_missing_permission_only_fails_check_and_reconciliation_recovers(seed, check_api, monkeypatch):
    run_id = new_run()
    token = github.installation_token

    def denied(*args, **kwargs):
        raise github.GitHubError(403, "Approve Checks: Read and write")

    monkeypatch.setattr(github, "installation_token", denied)
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.kind == "sync_check"))
        job.attempts = 2
        job_id = job.id
    jobs.execute_job(job_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.status == "queued" and run.error is None
        assert run.check_pending and "Approve Checks" in run.check_error
        assert db.get(Job, job_id).status == "failed"
    monkeypatch.setattr(github, "installation_token", token)
    jobs.schedule_reconciliation()
    with transaction() as db:
        next_id = db.scalar(select(Job.id).where(Job.kind == "sync_check", Job.status == "queued"))
    jobs.execute_job(next_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.check_id and not run.check_pending and run.check_error is None


def test_native_token_permission_check(seed, monkeypatch):
    monkeypatch.setattr(github, "app_token", lambda db: "app-token")
    monkeypatch.setattr(github, "request", lambda *a, **k: {"token": "test", "permissions": {}})
    with transaction() as db:
        assert github.installation_token(db, 10) == "test"
        with pytest.raises(github.GitHubError, match="approve the installation"):
            github.installation_token(db, 10, checks=True)


def test_timeout_and_worker_recovery_queue_final_check(seed, check_api, monkeypatch):
    run_id = new_run()

    def timeout(*args, **kwargs):
        raise TimeoutError("The operation exceeded its configured time limit.")

    monkeypatch.setattr(ocr, "review", timeout)
    with transaction() as db:
        review_id = db.scalar(select(Job.id).where(Job.kind == "review"))
    jobs.execute_job(review_id)
    checks.sync_check(run_id)
    assert check_api[1][-1][1]["conclusion"] == "timed_out"
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.failure_reason == "timeout"
        run.status, run.failure_reason = "running", None
        job = db.get(Job, review_id)
        job.status, job.attempts, job.lease_until = "running", 3, now() - timedelta(seconds=1)
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kw: None)
    jobs.dispatch()
    checks.sync_check(run_id)
    assert check_api[1][-1][1]["conclusion"] == "failure"
    with transaction() as db:
        assert db.get(ReviewRun, run_id).failure_reason == "worker_stopped"


def test_cancel_supersede_and_historical_runs(seed, check_api, client):
    run_id = new_run()
    checks.sync_check(run_id)
    assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 200
    checks.sync_check(run_id)
    assert check_api[1][-1][1]["conclusion"] == "cancelled"
    fresh = new_run()
    with transaction() as db:
        reviews.upsert_pr(db, db.get(Repository, 22), remote("c" * 40))
    checks.sync_check(fresh)
    assert check_api[1][-1][1]["conclusion"] == "cancelled"
    with transaction() as db:
        old = db.get(ReviewRun, run_id)
        old.check_enabled = False
        old.status, old.publication = "completed", "published"
    before = len(check_api[1])
    checks.sync_check(run_id)
    assert len(check_api[1]) == before


def rerun_body(run_id, check_id=100, sender=7):
    return {
        "action": "rerequested",
        "installation": {"id": 10},
        "repository": {"id": 22},
        "sender": {"id": sender},
        "check_run": {"id": check_id, "app": {"id": 123}, "external_id": run_id, "head_sha": "a" * 40},
    }


def post_rerun(client, body, delivery="rerun"):
    raw = json.dumps(body).encode()
    headers = {
        "x-github-delivery": delivery,
        "x-github-event": "check_run",
        "x-hub-signature-256": "sha256=" + hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest(),
    }
    return client.post("/api/v1/webhooks/github", content=raw, headers=headers)


def test_signed_rerun_replay_is_durable_and_keeps_connection(seed, check_api, client):
    run_id = new_run()
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.status, run.publication = "failed", "not_applicable"
    checks.sync_check(run_id)
    body = rerun_body(run_id)
    assert not post_rerun(client, body).json()["duplicate"]
    assert post_rerun(client, body).json()["duplicate"]
    with transaction() as db:
        payload = db.scalar(select(Job).where(Job.kind == "webhook")).payload
    jobs.process_webhook(payload)
    # Simulate a worker crash after committing the request but before acknowledging its job.
    with transaction() as db:
        newest = db.scalar(select(ReviewRun).where(ReviewRun.id != run_id))
        assert newest.connection_id == "connection" and newest.actor_id == 7 and newest.trigger == "manual"
        newest.status, newest.publication = "failed", "not_applicable"
    jobs.process_webhook(payload)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 2
        assert db.get(WebhookDelivery, "rerun").handled_at


@pytest.mark.parametrize(
    "scenario",
    [
        "outsider",
        "stale",
        "wrong_app",
        "wrong_id",
        "wrong_installation",
        "no_connection",
        "no_write",
        "personal",
    ],
)
def test_rerun_rejects_invalid_or_unauthorized_requests(seed, check_api, monkeypatch, scenario):
    run_id = new_run()
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.status, run.publication = "failed", "not_applicable"
    checks.sync_check(run_id)
    body = rerun_body(run_id)
    if scenario == "outsider":
        body["sender"]["id"] = 999
    elif scenario == "stale":
        monkeypatch.setattr(github, "request", lambda *a, **k: remote("c" * 40))
    elif scenario == "wrong_app":
        body["check_run"]["app"]["id"] = 999
    elif scenario == "wrong_id":
        body["check_run"]["external_id"] = "unrelated"
    elif scenario == "wrong_installation":
        body["installation"]["id"] = 999
    elif scenario == "no_write":

        def denied(*a, **k):
            raise github.GitHubError(403, "Write permission required")

        monkeypatch.setattr(github, "authorize_repo", denied)
    elif scenario == "no_connection":
        with transaction() as db:
            db.get(ReviewRun, run_id).connection_id = None
    elif scenario == "personal":
        with transaction() as db:
            db.add(User(id=8, login="subscriber", enabled=True))
            db.add(Membership(github_id=8, login="subscriber"))
            db.flush()
            connection = db.get(Connection, "connection")
            connection.kind = "chatgpt"
            connection.owner_id = 8
    with transaction() as db:
        db.add(WebhookDelivery(id="rerun", event="check_run"))
    if scenario == "wrong_app":
        with transaction() as db:
            checks.rerequest(db, body, "rerun")
    else:
        with pytest.raises((ValueError, github.GitHubError)):
            with transaction() as db:
                checks.rerequest(db, body, "rerun")
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1


def test_rerun_publication_and_blocked_resume_reuse_run(seed, check_api):
    run_id = new_run()
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.status, run.publication = "completed", "failed"
        db.add(WebhookDelivery(id="publish", event="check_run"))
    checks.sync_check(run_id)
    with transaction() as db:
        checks.rerequest(db, rerun_body(run_id), "publish")
        assert db.get(ReviewRun, run_id).publication == "pending"
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1
        assert db.scalar(select(Job).where(Job.kind == "publish"))
        db.get(ReviewRun, run_id).status = "blocked"
        db.add(WebhookDelivery(id="resume", event="check_run"))
    with transaction() as db:
        checks.rerequest(db, rerun_body(run_id), "resume")
        assert db.get(ReviewRun, run_id).status == "queued"
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1


def test_migration_preserves_history_and_enables_only_unfinished_work(seed):
    history = new_run()
    with transaction() as db:
        run = db.get(ReviewRun, history)
        run.status, run.publication = "completed", "published"
        run.report = {"message": "historical evidence"}
    pending = new_run()
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261006_connections")
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        historical = db.get(ReviewRun, history)
        active = db.get(ReviewRun, pending)
        assert not historical.check_enabled and not historical.check_pending
        assert historical.report == {"message": "historical evidence"}
        assert active.check_enabled and active.check_pending
