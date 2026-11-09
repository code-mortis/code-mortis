import copy
import importlib.util
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_review_flow import new_run

from code_mortis import jobs
from code_mortis.core.db import now, transaction
from code_mortis.core.shared_execution import INFERENCE_JOBS, RETIRED_MESSAGE, retire_subscriptions
from code_mortis.mcp import tools
from code_mortis.models import Connection, Job, PullRequest, Repository, ReviewRun, SecurityScan, User
from code_mortis.reviews import background, pipeline, summary
from code_mortis.reviews import service as reviews


def retired_connection(db, kind="chatgpt"):
    connection = Connection(id="retired", name="Historical account", kind=kind, owner_id=7,
                            provider="openai", protocol="native", model="old-model", secret="",
                            base_url="", auth_status="connected")
    db.add(connection)
    db.flush()
    return connection


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code", "unknown"])
def test_native_and_unknown_admission_never_reach_runner(seed, client, github_stub, monkeypatch, kind):
    with transaction() as db:
        connection = retired_connection(db, kind)
        repo, actor = db.get(Repository, 22), db.get(User, 7)
        with pytest.raises(ValueError, match="retired"):
            reviews.authorize_connection(db, connection, repo, "manual", actor)
        with pytest.raises(ValueError, match="retired"):
            background.enqueue(db, repo, actor, connection, "old-model")
    assert client.post("/api/v1/connections", json={"kind": kind, "name": "No", "model": "old-model"}).status_code == 422
    assert client.get("/api/v1/connections/retired/models").status_code == 410
    assert client.post("/api/v1/connections/retired/test").status_code == 410
    assert client.post("/api/v1/connections/retired/auth").status_code == 410
    assert client.get("/api/v1/connections/retired/auth").status_code == 410
    assert client.put("/api/v1/connections/retired/grants/22", json={"automatic": True}).status_code == 410
    assert client.post("/api/v1/connections/retired/console-ticket").status_code == 410
    assert [row["id"] for row in client.get("/api/v1/connections").json()] == ["connection"]


@pytest.mark.parametrize("kind,engine", [("chatgpt", "mortis"), ("unknown", "mortis"), ("api", "ocr")])
def test_automatic_sync_keeps_pr_history_without_starting_unavailable_execution(seed, github_stub, monkeypatch, kind, engine):
    from test_review_flow import remote

    from code_mortis.api.presenters import repo_out
    from code_mortis.integrations import github

    with transaction() as db:
        repo = db.get(Repository, 22)
        if kind != "api":
            repo.connection_id = retired_connection(db, kind).id
        repo.settings = {**repo.settings, "analysis_engine": engine}
    monkeypatch.setattr(github, "request", lambda *a, **k: remote("c" * 40))
    monkeypatch.setattr(jobs, "reconcile_threads", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "create_run", lambda *a, **k: pytest.fail("Unavailable automatic inference was admitted"))
    jobs.sync_pr(22, 3)
    with transaction() as db:
        assert db.get(PullRequest, "pr").head_sha == "c" * 40
        assert not list(db.scalars(select(ReviewRun)))
        repo = db.get(Repository, 22)
        assert repo.settings["analysis_engine"] == engine
        if kind != "api":
            assert repo_out(db, repo).connection_requires_setup


@pytest.mark.parametrize("kind", sorted(INFERENCE_JOBS))
def test_old_delivery_is_terminal_before_network_authorization(seed, monkeypatch, kind):
    identity = new_run(parallel=True)
    with transaction() as db:
        connection = retired_connection(db)
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "connection": reviews.connection_snapshot(connection)}
        run.connection_id, run.engine, run.owner_id = connection.id, "chatgpt", 7
        run.status, run.review_ids, run.publication = "running", [123], "partially_published"
        run.summary = {"status": "pending", "tokens": 0}
        scan = SecurityScan(id="scan", repository_id=22, actor_id=7, connection_id=connection.id,
                            request_key="retired-scan-key", branch="main", head_sha="h" * 40,
                            config={"connection": reviews.connection_snapshot(connection)},
                            progress={"tokens": 10, "tasks": {"saved": {"status": "completed", "tokens": 10},
                                                                       "active": {"status": "running"}}})
        db.add(scan)
        job = Job(id="old-job", kind=kind, payload={"run_id": identity, "scan_id": "scan",
                  "connection_id": connection.id, "connection": run.config["connection"],
                  "config": {"connection": run.config["connection"]}, "actor_id": 7})
        db.add(job)
        run.progress = {**run.progress, "summary_retry": job.id}
    monkeypatch.setattr(jobs, "execute", lambda *a, **k: pytest.fail("retired work executed"))
    jobs.execute_job.run("old-job")
    jobs.execute_job.run("old-job")
    with transaction() as db:
        job = db.get(Job, "old-job")
        assert job.status == "failed" and job.attempts == 0 and "retired" in job.error
        if kind in {"review", *pipeline.KINDS}:
            run = db.get(ReviewRun, identity)
            assert run.cancel_requested and run.status == "cancelled"
            assert run.publication == "partially_published" and run.review_ids == [123]
        if kind == "security_scan":
            scan = db.get(SecurityScan, "scan")
            assert scan.status == "cancelled" and scan.progress["tokens"] is None
            assert scan.progress["tasks"]["saved"]["status"] == "completed"


def test_cutover_preserves_completed_history_and_shared_jobs(seed):
    identity = new_run()
    with transaction() as db:
        connection = retired_connection(db)
        run = db.get(ReviewRun, identity)
        run.status, run.report, run.summary = "completed", {"old": "report"}, {"status": "completed", "tokens": 9}
        run.config = {**run.config, "connection": reviews.connection_snapshot(connection)}
        before = copy.deepcopy((run.config, run.report, run.summary, run.engine, run.actor_id))
        shared = Job(id="shared-background", kind="generate_background", payload={"connection_id": "connection", "connection": {"id": "connection", "kind": "api"}})
        native = Job(id="native-background", kind="generate_background", status="running", attempts=1,
                     lease_until=now() + timedelta(hours=1), payload={"connection_id": "retired", "connection": {"id": "retired", "kind": "chatgpt"}, "inference_started": True})
        saved = Job(id="native-saved-draft", kind="generate_background", status="running", attempts=1,
                    payload={"connection_id": "retired", "connection": {"id": "retired", "kind": "chatgpt"},
                             "inference_started": True, "background": "Already saved draft", "usage": {"tokens": 75}})
        measured = Job(id="native-measured-failure", kind="generate_background", status="running", attempts=1,
                       payload={"connection_id": "retired", "connection": {"id": "retired", "kind": "chatgpt"},
                                "inference_started": True, "usage": {"tokens": 23}})
        db.add_all([shared, native, saved, measured])
    with transaction() as db:
        assert retire_subscriptions(db)["jobs"] == 4  # includes old queued review delivery
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        assert (run.config, run.report, run.summary, run.engine, run.actor_id) == before
        assert db.get(Job, "shared-background").status == "queued"
        assert db.get(Job, "native-background").payload["usage"]["tokens"] is None
        saved = db.get(Job, "native-saved-draft")
        assert saved.status == "done" and background.job_out(saved).background == "Already saved draft"
        assert saved.payload["usage"] == {"tokens": 75}
        assert db.get(Job, "native-measured-failure").payload["usage"] == {"tokens": 23}
        assert db.get(Connection, "retired").auth_status == "revoked"
        assert retire_subscriptions(db) == {"reviews": 0, "scans": 0, "jobs": 0}


@pytest.mark.parametrize("publication,expected", [("partially_published", "partially_published"),
                                                ("publishing", "partially_published"), ("uncertain", "partially_published")])
def test_cancellation_keeps_partial_publication_even_without_review_ids(seed, publication, expected):
    identity = new_run()
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.publication, run.publication_plan = publication, [{"body": "saved publication"}]
        reviews.cancel_review(db, run)
        assert run.publication == expected and run.review_ids == []
        assert run.publication_plan == [{"body": "saved publication"}]


def test_explicit_shared_summary_keeps_original_native_configuration_and_attempt_history(seed, github_stub):
    identity = new_run(overview=True)
    with transaction() as db:
        connection = retired_connection(db)
        run = db.get(ReviewRun, identity)
        run.status, run.engine, run.summary = "completed", "chatgpt", {"status": "failed", "tokens": 17, "error": "old"}
        run.config = {**run.config, "connection": reviews.connection_snapshot(connection)}
        original = copy.deepcopy(run.config)
        first = summary.retry_generation(db, run, db.get(Repository, 22), db.get(User, 7))
        db.flush()
        assert first.payload["config"]["connection"]["kind"] == "api"
        assert first.payload["config"]["analysis_engine"] == "mortis"
        assert first.payload["previous_summary"]["tokens"] == 17
        first.status = "failed"
        summary.fail_retry(db, first, "first shared failure")
        first_evidence = copy.deepcopy(first.payload)
        second = summary.retry_generation(db, run, db.get(Repository, 22), db.get(User, 7))
        db.flush()
        assert first.payload == first_evidence and first.id != second.id
        assert run.progress["summary_attempts"] == [first.id, second.id]
        assert run.config == original and run.engine == "chatgpt"
        assert second.payload["actor_id"] == 7 and first.payload["summary_attempt_result"]["status"] == "failed"


def test_new_summary_authority_is_independent_from_retired_history_and_rechecked(seed, github_stub):
    from code_mortis.api_tokens import provenance as pat
    from code_mortis.mcp import provenance as mcp

    identity = new_run(overview=True)
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": "chatgpt"}}
        original_pat = {"token_id": "old-deleted-token", "user_id": 7, "scope": "reviews:run"}
        original_mcp = {"grant_id": "old-deleted-grant", "domain": "reviews", "scope": "reviews:run"}
        run.progress = {**run.progress, "pat": original_pat, "mcp": original_mcp}
        run.status, run.summary = "completed", {"status": "failed", "tokens": None}
        job = summary.retry_generation(db, run, db.get(Repository, 22), db.get(User, 7))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(hours=1)
        db.flush()
        assert not pat.job_revoked(db, job) and not mcp.job_revoked(db, job)
        assert not summary.retry_stopped(db, run, job.id, f"{job.id}:1")
        assert run.progress["pat"] == original_pat and run.progress["mcp"] == original_mcp
        # A missing/malformed new origin cannot borrow the historical origin or become session work.
        job.payload = {**job.payload, "pat": None}
        assert pat.job_revoked(db, job)
        assert summary.retry_stopped(db, run, job.id, f"{job.id}:1")
        job.payload = {key: value for key, value in job.payload.items() if key != "pat"}
        job.payload = {**job.payload, "mcp": None}
        assert mcp.job_revoked(db, job)


@pytest.mark.parametrize("origin", ["pat", "mcp"])
@pytest.mark.parametrize("boundary", ["revoke_queued", "revoke_running", "delayed_delivery"])
def test_revoked_summary_attempt_settles_projection_and_allows_explicit_new_retry(seed, github_stub, monkeypatch, origin, boundary):
    from code_mortis.api_tokens import provenance as pat
    from code_mortis.mcp import provenance as mcp

    identity = new_run(overview=True)
    authority = pat if origin == "pat" else mcp
    record = ({"token_id": "new-attempt-token", "user_id": 7, "scope": "reviews:run", "repository_id": 22}
              if origin == "pat" else {"grant_id": "new-attempt-grant", "domain": "reviews", "scope": "reviews:run"})
    with transaction() as db:
        run = db.get(ReviewRun, identity)
        run.config = {**run.config, "connection": {**run.config["connection"], "kind": "chatgpt"}}
        run.engine, run.status, run.summary = "chatgpt", "completed", {"status": "failed", "tokens": 17}
        run.progress = {**run.progress, "pat": {"token_id": "historical-token", "user_id": 7, "scope": "reviews:run"},
                        "mcp": {"grant_id": "historical-grant", "domain": "reviews", "scope": "reviews:run"}}
        original = copy.deepcopy((run.config, run.engine, run.progress["pat"], run.progress["mcp"]))
        with authority.scoped(record):
            job = summary.retry_generation(db, run, db.get(Repository, 22), db.get(User, 7))
        if boundary == "revoke_running":
            job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(hours=1)
            job.payload = {**job.payload, "inference_started": True}
            run.summary = {"status": "running", "tokens": None}
        db.flush()
        attempt_id = job.id
    if boundary == "delayed_delivery":
        monkeypatch.setattr(jobs, "execute", lambda *a, **k: pytest.fail("Revoked summary reached execution"))
        jobs.execute_job(attempt_id)
    else:
        with transaction() as db:
            authority.revoke_work(db, record["token_id" if origin == "pat" else "grant_id"])
    with transaction() as db:
        run, job = db.get(ReviewRun, identity), db.get(Job, attempt_id)
        assert job.status == "failed" and run.summary["status"] == "failed"
        assert run.summary["tokens"] == (None if boundary == "revoke_running" else 0)
        assert job.payload["summary_attempt_result"]["status"] == "failed"
        assert (run.config, run.engine, run.progress["pat"], run.progress["mcp"]) == original
        assert run.status == "completed" and not run.cancel_requested
        replacement = summary.retry_generation(db, run, db.get(Repository, 22), db.get(User, 7))
        assert replacement.id != job.id and replacement.payload["previous_summary"]["status"] == "failed"
        assert run.summary["status"] == "pending"


def test_runner_and_management_tools_cannot_start_retired_inference():
    for module in ("code_mortis.native_runner", "code_mortis.integrations.runner_client", "code_mortis.integrations.ocr"):
        assert importlib.util.find_spec(module) is None
    names = {row[1] for row in tools.MANAGEMENT_TOOLS}
    assert "management_create_connection" in names and "management_test_connection" in names
    assert "management_start_connection_auth" not in names and "management_grant_repository_access" not in names
    assert RETIRED_MESSAGE
