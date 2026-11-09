import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from test_inbox import pull, run
from test_review_flow import new_run, perform_review, remote

from code_mortis import jobs
from code_mortis.api.routers import pulls as pull_routes
from code_mortis.api.routers import runs as run_routes
from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import Finding, Job, PullRequest, Repository, ReviewRun
from code_mortis.reviews import mortis
from code_mortis.reviews import service as reviews
from code_mortis.reviews import summary as pr_summary
from code_mortis.schemas.common import RepoSettings


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("login", ["dependabot[bot]", "automation"])
def test_bot_imports_and_automation_require_opt_in(seed, github_stub, monkeypatch, enabled, existing, login):
    data = remote()
    data["user"] = {"login": login, "type": "Bot"}
    with transaction() as db:
        db.get(Repository, 22).settings = {"include_bot_prs": enabled}
        if not existing:
            db.delete(db.get(PullRequest, "pr"))
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    monkeypatch.setattr(github, "pages", lambda *a, **k: [data])
    jobs.scan_repository(22)
    with transaction() as db:
        payload = db.scalar(select(Job).where(Job.kind == "sync_pr")).payload
    jobs.execute("sync_pr", payload)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(PullRequest)) == int(existing or enabled)
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == int(enabled)


@pytest.mark.parametrize("sender_type,author_type", [(s, a) for s in ["User", "Bot"] for a in ["User", "Bot"]])
@pytest.mark.parametrize("enabled", [False, True])
def test_bot_webhooks_keep_human_sender_rules(seed, github_stub, monkeypatch, sender_type, author_type, enabled):
    with transaction() as db:
        db.delete(db.get(PullRequest, "pr"))
        db.get(Repository, 22).settings = {"include_bot_prs": enabled}
    data = remote()
    data["user"]["type"] = author_type
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    jobs.process_webhook({"event": "pull_request", "body": {
        "action": "opened", "installation": {"id": 10}, "repository": {"id": 22},
        "sender": {"type": sender_type}, "pull_request": data,
    }})
    with transaction() as db:
        payload = db.scalar(select(Job).where(Job.kind == "sync_pr")).payload
    jobs.execute("sync_pr", payload)
    with transaction() as db:
        expected = author_type == "Bot" and enabled or author_type == sender_type == "User"
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == int(expected)
        assert db.scalar(select(func.count()).select_from(PullRequest)) == int(author_type == "User" or enabled)


@pytest.mark.parametrize("enabled", [False, True])
def test_bot_setting_schedules_import_only_when_reviews_are_enabled(seed, github_stub, client, enabled):
    assert not RepoSettings().include_bot_prs
    response = client.put("/api/v1/repositories/22", json={
        "enabled": enabled, "connection_id": "connection", "settings": {"include_bot_prs": True},
    })
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["include_bot_prs"]
    with transaction() as db:
        assert bool(db.scalar(select(Job).where(Job.kind == "scan_repository"))) is enabled


@pytest.mark.parametrize("source", ["webhook", "scan"])
@pytest.mark.parametrize("author_type", ["User", "Bot"])
def test_pausing_repository_drops_queued_new_pr_imports(seed, github_stub, client, monkeypatch, source, author_type):
    data = {**remote(), "number": 4, "user": {"login": "author", "type": author_type}}
    with transaction() as db:
        db.get(Repository, 22).settings = {"include_bot_prs": True}
    monkeypatch.setattr(github, "pages", lambda *a, **k: [data])
    if source == "webhook":
        jobs.process_webhook({"event": "pull_request", "body": {
            "action": "opened", "installation": {"id": 10}, "repository": {"id": 22},
            "sender": {"type": author_type}, "pull_request": data,
        }})
    else:
        jobs.scan_repository(22)
    with transaction() as db:
        identity = db.scalar(select(Job.id).where(Job.kind == "sync_pr", Job.payload["number"].as_integer() == 4))
        assert identity
    assert client.put("/api/v1/repositories/22", json={
        "enabled": False, "connection_id": "connection", "settings": {"include_bot_prs": True},
    }).status_code == 200
    def unexpected(*a, **k):
        pytest.fail("A paused repository must not fetch an untracked PR")
    monkeypatch.setattr(github, "installation_token", unexpected)
    monkeypatch.setattr(github, "request", unexpected)
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.get(Job, identity).status == "done"
        assert db.scalar(select(func.count()).select_from(PullRequest)) == 1
        assert db.get(PullRequest, "pr")
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 0


def test_pausing_repository_stops_already_queued_discovery(seed, github_stub, monkeypatch):
    with transaction() as db:
        identity = reviews.enqueue(db, "scan_repository", {"repository_id": 22}).id
        db.get(Repository, 22).enabled = False
    monkeypatch.setattr(github, "installation_token", lambda *a, **k: pytest.fail("Paused discovery must not access GitHub"))
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.get(Job, identity).status == "done"
        assert not db.scalar(select(Job.id).where(Job.kind == "sync_pr"))
        assert db.get(PullRequest, "pr")


@pytest.mark.parametrize("author_type", ["User", "Bot"])
@pytest.mark.parametrize("automatic", [False, True])
def test_pause_during_pr_fetch_prevents_import(seed, github_stub, client, monkeypatch, author_type, automatic):
    with transaction() as db:
        db.get(Repository, 22).settings = {"include_bot_prs": True}
    def fetch(*a, **k):
        assert client.put("/api/v1/repositories/22", json={
            "enabled": False, "connection_id": "connection", "settings": {"include_bot_prs": True},
        }).status_code == 200
        return {**remote(), "number": 4, "user": {"login": "author", "type": author_type}}
    monkeypatch.setattr(github, "request", fetch)
    jobs.sync_pr(22, 4, automatic=automatic)
    with transaction() as db:
        assert not db.get(Repository, 22).enabled
        assert db.scalar(select(func.count()).select_from(PullRequest)) == 1
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 0


@pytest.mark.parametrize("author_type", ["User", "Bot"])
def test_paused_repository_preserves_existing_pr_sync_and_review_history(seed, github_stub, monkeypatch, author_type):
    identity = new_run()
    with transaction() as db:
        db.get(ReviewRun, identity).status = "completed"
        db.get(Repository, 22).enabled = False
    data = {**remote(), "state": "closed", "merged": True, "user": {"login": "author", "type": author_type}}
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    reconciled = []
    monkeypatch.setattr(jobs, "reconcile_threads", lambda db, pr, *a: reconciled.append(pr.id))
    jobs.sync_pr(22, 3)
    with transaction() as db:
        assert db.get(PullRequest, "pr").state == "merged"
        assert db.get(ReviewRun, identity).status == "completed"
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1
    assert reconciled == ["pr"]


@pytest.mark.parametrize("author_type", ["User", "Bot"])
def test_reenabled_repository_imports_prs_without_enabling_automatic_reviews(seed, github_stub, client, monkeypatch, author_type):
    with transaction() as db:
        db.get(Repository, 22).enabled = False
    data = {**remote(), "number": 4, "user": {"login": "author", "type": author_type}}
    monkeypatch.setattr(github, "pages", lambda *a, **k: [data])
    monkeypatch.setattr(github, "request", lambda method, path, *a, **k: data if path.endswith("/4") else remote())
    assert client.put("/api/v1/repositories/22", json={
        "enabled": True, "connection_id": "connection", "settings": {"include_bot_prs": True, "automatic": False},
    }).status_code == 200
    with transaction() as db:
        identity = db.scalar(select(Job.id).where(Job.kind == "scan_repository"))
    jobs.execute_job(identity)
    with transaction() as db:
        identities = list(db.scalars(select(Job.id).where(Job.kind == "sync_pr")))
    for identity in identities:
        jobs.execute_job(identity)
    with transaction() as db:
        assert set(db.scalars(select(PullRequest.number))) == {3, 4}
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 0


def test_cancel_all_reviews_preserves_history(seed, github_stub, client):
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        for state in ["queued", "running", "blocked", "completed"]:
            run(db, pr, state, state)
    assert client.get("/api/v1/pulls/pr").json()["cancellable_run_count"] == 3
    response = client.post("/api/v1/pulls/pr/cancel")
    assert response.status_code == 200, response.text
    assert {r["id"] for r in response.json()} == {"queued", "running", "blocked"}
    assert {r["status"] for r in response.json()} == {"cancelled"}
    assert client.post("/api/v1/pulls/pr/cancel").status_code == 409
    with transaction() as db:
        assert db.get(ReviewRun, "completed").status == "completed"
        assert not db.get(PullRequest, "pr").ignored


def test_ignore_filters_counts_and_restoration_preserve_findings(seed, github_stub, client, monkeypatch):
    monkeypatch.setattr(github, "pages", lambda *a, **k: [{"id": 22}])
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        pr.author = "hidden-author"
        review = run(db, pr, "running")
        db.add(Finding(pr_id=pr.id, fingerprint="one", path="a.py", content="Bug", start_line=1,
                       end_line=1, last_seen_run_id=review.id))
        pull(db, "visible", author="visible-author")
    response = client.put("/api/v1/pulls/pr/ignore", json={"ignored": True})
    assert response.status_code == 200 and response.json()["ignored"], response.text
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": True}).status_code == 200
    for path in ["/pulls", "/repositories/22/pulls"]:
        result = client.get("/api/v1" + path).json()
        assert result["total"] == result["registered_total"] == result["state_counts"]["open"] == 1
        assert result["authors"] == ["visible-author"]
        assert client.get("/api/v1" + path + "?visibility=all").json()["total"] == 2
        ignored = client.get("/api/v1" + path + "?visibility=ignored").json()
        assert ignored["total"] == 1 and ignored["groups"][0]["items"][0]["ignored"]
    assert client.get("/api/v1/repositories").json()[0]["open_prs"] == 1
    assert client.post("/api/v1/pulls/pr/runs").status_code == 409
    with transaction() as db:
        assert db.scalar(select(Finding)).status == "open"
        assert db.get(ReviewRun, "run-pr").status == "cancelled"
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": False}).status_code == 200
    with transaction() as db:
        assert db.get(ReviewRun, "run-pr").cancel_requested
    assert client.get("/api/v1/pulls").json()["total"] == 2


@pytest.mark.parametrize("path,method,body", [
    ("/pulls/pr/ignore", "PUT", {"ignored": True}), ("/pulls/pr/cancel", "POST", None),
])
def test_pull_actions_require_current_write_access(seed, github_stub, client, monkeypatch, path, method, body):
    def denied(*a, **k):
        raise github.GitHubError(403, "Write access removed")
    monkeypatch.setattr(github, "authorize_repo", denied)
    assert client.request(method, "/api/v1" + path, json=body).status_code == 403
    with transaction() as db:
        assert not db.get(PullRequest, "pr").ignored


def test_ignore_blocks_retries_publication_and_stale_run_creation(seed, github_stub, client, monkeypatch):
    identity = new_run(overview=True)
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        review.status, review.publication = "completed", "pending"
    with transaction() as stale:
        pr, repo = stale.get(PullRequest, "pr"), stale.get(Repository, 22)
        assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": True}).status_code == 200
        with pytest.raises(ValueError, match="Restore"):
            reviews.create_run(stale, pr, repo, "manual")
    for suffix in ["retry", "publish", "summary/retry", "candidates/missing/retry"]:
        assert client.post(f"/api/v1/runs/{identity}/{suffix}").status_code == 409
    assert client.post("/api/v1/pulls/pr/summary").status_code == 409
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("Ignored work must not publish"))
    monkeypatch.setattr(mortis, "review", lambda *a, **k: pytest.fail("Ignored work must not run"))
    perform_review(identity)
    reviews.publish_run(identity)
    pr_summary.sync("pr")
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": False}).status_code == 200
    reviews.publish_run(identity)
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        assert not review.cancel_requested and review.progress["publication_stopped"]


@pytest.mark.parametrize("suffix,kind", [("publish", "publish"), ("summary/retry", "generate_summary")])
def test_retry_refreshes_cancellation_before_lock(seed, github_stub, client, monkeypatch, suffix, kind):
    identity = new_run(overview=True)
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        review.status, review.publication = "completed", "failed"
        review.summary = {"status": "failed"}
    original_access = run_routes.review_pr_access

    def cancel_before_lock(*args):
        with transaction() as db:
            db.get(ReviewRun, identity).cancel_requested = True
        return original_access(*args)

    monkeypatch.setattr(run_routes, "review_pr_access", cancel_before_lock)
    response = client.post(f"/api/v1/runs/{identity}/{suffix}")
    assert response.status_code == 409, response.text
    with transaction() as db:
        assert db.get(ReviewRun, identity).publication == "failed"
        assert not db.scalar(select(Job).where(Job.kind == kind))


@pytest.mark.parametrize("status", ["completed", "partial"])
@pytest.mark.parametrize("summary_status", ["failed", "pending", "running"])
@pytest.mark.parametrize("publication,review_ids", [("failed", []), ("pending", []), ("publishing", []), ("partially_published", [123])])
def test_restore_allows_explicit_retries_without_reviving_terminal_work(seed, github_stub, client, monkeypatch, status, summary_status, publication, review_ids):
    identity = new_run(overview=True)
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        review.status, review.publication, review.review_ids = status, publication, review_ids
        review.summary = {"status": summary_status, "error": "Interrupted", "tokens": 17}
        review.progress = {"execution": "old-review", "summary_retry": "old-summary"}
        review.stages = {"summary": {"status": summary_status, "execution": "old-stage"}}
    for ignored in [True, True, False]:
        assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": ignored}).status_code == 200
        if ignored:
            for suffix in ["publish", "summary/retry"]:
                assert client.post(f"/api/v1/runs/{identity}/{suffix}").status_code == 409
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        assert review.status == status and not review.cancel_requested
        assert review.publication == ("partially_published" if review_ids else "failed")
        assert review.summary["status"] == "failed" and review.summary["tokens"] == 17
        assert "summary_retry" not in review.progress and "execution" not in review.progress
        assert review.stages["summary"].get("execution") is None
    with monkeypatch.context() as stopped:
        stopped.setattr(github, "request", lambda *a, **k: pytest.fail("Old publication must stay stopped"))
        reviews.publish_run(identity)
    assert client.post(f"/api/v1/runs/{identity}/publish").status_code == 202
    with transaction() as db:
        assert not db.get(ReviewRun, identity).progress.get("publication_stopped")
    assert client.post(f"/api/v1/runs/{identity}/summary/retry").status_code == 202


def test_restore_does_not_enable_retries_for_cancelled_review(seed, github_stub, client):
    identity = new_run(overview=True)
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        review.status, review.publication, review.review_ids = "running", "partially_published", [123]
        review.summary = {"status": "running"}
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": True}).status_code == 200
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": False}).status_code == 200
    for suffix in ["retry", "publish", "summary/retry"]:
        assert client.post(f"/api/v1/runs/{identity}/{suffix}").status_code == 409
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        assert review.status == "cancelled" and review.cancel_requested


@pytest.mark.parametrize("interrupt_after", [1, 2, 3])
def test_ignore_and_restore_stop_inflight_publication_until_explicit_retry(seed, github_stub, client, monkeypatch, interrupt_after):
    identity = new_run()
    plan = [{"event": "COMMENT", "body": reviews.marker("run", f"{identity}:{index}")} for index in range(2)]
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        review.status, review.publication, review.publication_plan = "completed", "pending", plan
    posted, completed = [], 0

    @contextmanager
    def interrupt_between_transactions():
        nonlocal completed
        with transaction() as db:
            yield db
        completed += 1
        if completed == interrupt_after:
            assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": True}).status_code == 200
            assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": False}).status_code == 200

    def request(method, path, token, **kwargs):
        if method == "GET":
            return remote()
        posted.append({"id": len(posted) + 1, "body": kwargs["json"]["body"], "user": {"login": "mortis-test[bot]"}})
        return posted[-1]

    monkeypatch.setattr(reviews, "transaction", interrupt_between_transactions)
    monkeypatch.setattr(reviews, "reconcile_threads", lambda *a: None)
    monkeypatch.setattr(github, "pages", lambda *a: posted)
    monkeypatch.setattr(github, "request", request)
    reviews.publish_run(identity)
    assert len(posted) == interrupt_after - 1
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        assert review.progress["publication_stopped"] and not review.cancel_requested
        assert review.publication == ("partially_published" if posted else "failed")
    assert client.post(f"/api/v1/runs/{identity}/publish").status_code == 202
    reviews.publish_run(identity)
    assert len(posted) == 2
    with transaction() as db:
        review = db.get(ReviewRun, identity)
        assert review.publication == "published" and review.review_ids == [1, 2]
        assert review.publication_plan == plan


def test_ignored_pr_still_syncs_metadata_without_reviewing(seed, github_stub, client, monkeypatch):
    assert client.put("/api/v1/pulls/pr/ignore", json={"ignored": True}).status_code == 200
    data = remote("c" * 40)
    reconciled = []
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    monkeypatch.setattr(jobs, "reconcile_threads", lambda *a: reconciled.append(True))
    jobs.sync_pr(22, 3)
    with transaction() as db:
        assert db.get(PullRequest, "pr").head_sha == data["head"]["sha"]
        assert db.get(PullRequest, "pr").ignored
        assert not db.scalar(select(ReviewRun))
    assert reconciled == [True]


def test_ignore_serializes_with_concurrent_start(seed, github_stub, client, monkeypatch):
    ignoring, starting, release = threading.Event(), threading.Event(), threading.Event()
    original_cancel, original_access = pull_routes.cancel_pr_reviews, pull_routes.review_pr_access

    def cancel(*args):
        ignoring.set()
        assert release.wait(5)
        return original_cancel(*args)

    def access(*args):
        starting.set()
        return original_access(*args)

    monkeypatch.setattr(pull_routes, "cancel_pr_reviews", cancel)
    monkeypatch.setattr(pull_routes, "review_pr_access", access)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ignored = pool.submit(client.put, "/api/v1/pulls/pr/ignore", json={"ignored": True})
        try:
            assert ignoring.wait(5)
            started = pool.submit(client.post, "/api/v1/pulls/pr/runs")
            assert starting.wait(5)
        finally:
            release.set()
        assert ignored.result(5).status_code == 200
        assert started.result(5).status_code == 409
    with transaction() as db:
        assert not db.scalar(select(ReviewRun))


def test_ignore_migration_preserves_existing_prs(seed):
    config = Config("alembic.ini")
    command.downgrade(config, "20261018_verification_retries")
    command.upgrade(config, "head")
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        assert not pr.ignored and pr.title == "Fix retry logic" and pr.author == "owner"
