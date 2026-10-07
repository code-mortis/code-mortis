import copy
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from code_mortis import github, jobs, ocr, reviews
from code_mortis.db import now, transaction
from code_mortis.models import (
    Activity,
    Connection,
    Finding,
    Job,
    Membership,
    OAuthState,
    Occurrence,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.security import unseal


def test_manifest_setup_requires_bootstrap_and_one_time_state(client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from code_mortis.config import settings

    assert client.post("/api/v1/setup/manifest", json={}).status_code == 403
    wrong_origin = client.post(
        "/api/v1/setup/manifest",
        json={},
        headers={"x-setup-token": settings().bootstrap_token, "origin": "http://localhost:5173"},
    )
    assert wrong_origin.status_code == 400
    assert settings().public_url in wrong_origin.json()["detail"]
    assert "set-cookie" not in wrong_origin.headers
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(OAuthState)) == 0
    response = client.post(
        "/api/v1/setup/manifest",
        json={},
        headers={"x-setup-token": settings().bootstrap_token, "origin": settings().public_url},
    )
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json()["action"]).query)["state"][0]
    assert response.json()["manifest"]["default_permissions"]["contents"] == "read"
    assert response.json()["manifest"]["request_oauth_on_install"] is False
    assert response.json()["manifest"]["setup_url"] == "http://testserver/api/v1/installations/callback"
    assert response.json()["manifest"]["hook_attributes"]["url"] == settings().github_webhook_url
    assert (
        client.get("/api/v1/setup/callback?state=wrong&code=valid", follow_redirects=False).status_code == 303
    )
    monkeypatch.setattr(
        github,
        "request",
        lambda *a, **k: {
            "id": 123,
            "slug": "mortis",
            "client_id": "id",
            "client_secret": "secret",
            "pem": "private-key",
            "webhook_secret": "webhook",
        },
    )
    assert (
        client.get(f"/api/v1/setup/callback?state={state}&code=valid", follow_redirects=False).status_code
        == 303
    )
    assert (
        client.post(
            "/api/v1/setup/manifest", json={}, headers={"x-setup-token": settings().bootstrap_token}
        ).status_code
        == 409
    )


def test_login_redirects_to_public_host_before_setting_state(seed, client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from code_mortis.config import settings

    monkeypatch.setattr(settings(), "public_url", "https://reviews.example.com")
    monkeypatch.setattr(settings(), "cookie_secure", True)
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "https://reviews.example.com/api/v1/auth/login"
    assert "set-cookie" not in response.headers
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(OAuthState)) == 0

    # The reverse proxy can forward the HTTPS origin over HTTP without redirecting in a loop.
    response = client.get("http://reviews.example.com/api/v1/auth/login", follow_redirects=False)
    assert urlsplit(response.headers["location"]).hostname == "github.com"
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    assert "cm_browser=" in response.headers["set-cookie"]
    assert "code_challenge=" in response.headers["location"]
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    monkeypatch.setattr(github, "exchange_code", lambda *a: {"access_token": "token"})
    monkeypatch.setattr(github, "request", lambda *a, **k: {"id": 7, "login": "owner", "avatar_url": ""})
    assert (
        client.get(
            f"https://reviews.example.com/api/v1/auth/callback?state={state}&code=valid",
            follow_redirects=False,
        ).status_code
        == 303
    )


def test_login_state_is_single_use_and_membership_is_required(seed, client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    response = client.get("/api/v1/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    monkeypatch.setattr(github, "exchange_code", lambda *a: {"access_token": "token"})
    monkeypatch.setattr(github, "request", lambda *a, **k: {"id": 7, "login": "owner", "avatar_url": ""})
    callback = f"/api/v1/auth/callback?state={state}&code=valid"
    assert client.get(callback, follow_redirects=False).status_code == 303
    assert client.get(callback, follow_redirects=False).status_code == 303
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    # A new account reusing the original owner's username must not bootstrap again.
    monkeypatch.setattr(github, "request", lambda *a, **k: {"id": 999, "login": "owner", "avatar_url": ""})
    assert (
        "auth_error=membership"
        in client.get(f"/api/v1/auth/callback?state={state}&code=valid", follow_redirects=False).headers[
            "location"
        ]
    )


COMMENT = {
    "path": "src/retry.py",
    "content": "Retry count is never bounded.",
    "start_line": 7,
    "end_line": 7,
    "severity": "high",
    "category": "bug",
    "existing_code": "while True:",
    "suggestion_code": "",
    "inline_line": 7,
}


def remote(head="a" * 40):
    return {
        "number": 3,
        "title": "Fix retry logic",
        "user": {"login": "owner"},
        "html_url": "https://github.com/team/project/pull/3",
        "state": "open",
        "head": {"sha": head, "ref": "fix/retries", "repo": {"id": 22}},
        "base": {"sha": "b" * 40},
    }


def new_run():
    with transaction() as db:
        run = reviews.create_run(
            db, db.get(PullRequest, "pr"), db.get(Repository, 22), "manual", db.get(User, 7)
        )
        db.flush()
        return run.id


def test_auth_csrf_roles_and_repository_boundary(seed, client, monkeypatch):
    assert client.get("/api/v1/auth/me").json()["csrf"] == "csrf"
    response = client.post("/api/v1/members", json={"login": "alice"}, headers={"x-csrf-token": "wrong"})
    assert response.status_code == 403
    assert (
        client.post(
            "/api/v1/members", json={"login": "alice"}, headers={"Origin": "https://attacker.example"}
        ).status_code
        == 403
    )
    monkeypatch.setattr(
        github,
        "authorize_repo",
        lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(404, "Repository inaccessible")),
    )
    assert client.get("/api/v1/pulls/pr").status_code == 404
    with transaction() as db:
        db.get(User, 7).role = "member"
        db.get(Membership, 7).role = "member"
    assert client.get("/api/v1/connections").status_code == 200
    assert client.post("/api/v1/connections/connection/test").status_code == 403
    with transaction() as db:
        db.delete(db.get(Membership, 7))
    assert client.get("/api/v1/auth/me").status_code == 403


def test_api_credentials_encrypted_and_never_returned(seed, client, monkeypatch):
    monkeypatch.setattr("code_mortis.api.safe_endpoint", lambda value: value)
    body = {
        "name": "Private",
        "provider": "custom",
        "protocol": "openai-responses",
        "model": "model",
        "base_url": "https://example.com/v1",
        "api_key": "secret-only-here",
        "extra_headers": {"X-Token": "header-secret"},
    }
    response = client.post("/api/v1/connections", json=body)
    assert response.status_code == 201, response.text
    assert "secret-only-here" not in response.text and "header-secret" not in response.text
    with transaction() as db:
        stored = db.get(Connection, response.json()["id"])
        assert "secret-only-here" not in stored.secret
        assert json.loads(unseal(stored.secret))["api_key"] == "secret-only-here"
    assert "provider-secret" not in client.get("/api/v1/connections").text
    body["api_key"] = ["secret-only-here"]
    response = client.post("/api/v1/connections", json=body)
    assert response.status_code == 422 and "secret-only-here" not in response.text


def test_signed_webhooks_are_durable_and_deduplicated(seed, client):
    body = json.dumps(
        {
            "action": "synchronize",
            "installation": {"id": 10},
            "repository": {"id": 22},
            "pull_request": {"number": 3},
        }
    ).encode()
    headers = {
        "x-github-delivery": "delivery",
        "x-github-event": "pull_request",
        "x-hub-signature-256": "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest(),
    }
    assert client.post("/api/v1/webhooks/github", content=body).status_code == 401
    assert client.post("/api/v1/webhooks/github", content=body, headers=headers).json()["duplicate"] is False
    assert client.post("/api/v1/webhooks/github", content=body, headers=headers).json()["duplicate"] is True
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_runs_deduplicate_and_new_commits_supersede(seed):
    with transaction() as db:
        repo, pr = db.get(Repository, 22), db.get(PullRequest, "pr")
        first = reviews.create_run(db, pr, repo, "automatic")
        db.flush()
        assert reviews.create_run(db, pr, repo, "automatic").id == first.id
        reviews.upsert_pr(db, repo, remote("c" * 40))
        second = reviews.create_run(db, pr, repo, "automatic")
        assert first.status == "superseded" and first.cancel_requested
        assert first.publication == "not_applicable" and second.id != first.id


def test_review_snapshots_survive_later_runs(seed, github_stub, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    monkeypatch.setattr(
        ocr, "review", lambda *a, **k: ("completed", {"message": "complete"}, [copy.deepcopy(COMMENT)])
    )
    first = new_run()
    reviews.perform_review(first)
    with transaction() as db:
        finding = db.scalar(select(Finding))
        snapshot = copy.deepcopy(db.scalar(select(Occurrence)).snapshot)
        assert finding.status == "open"
        assert db.get(ReviewRun, first).status == "completed"
    monkeypatch.setattr(ocr, "review", lambda *a, **k: ("completed", {}, []))
    second = new_run()
    reviews.perform_review(second)
    with transaction() as db:
        assert db.scalar(select(Finding)).status == "open"
        assert db.scalar(select(Finding)).last_seen_run_id == first
        assert db.scalar(select(Occurrence)).snapshot == snapshot
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 2


def test_publication_recovers_unknown_response_without_duplicate_or_review(seed, github_stub, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    monkeypatch.setattr(ocr, "review", lambda *a, **k: ("partial", {}, [copy.deepcopy(COMMENT)]))
    run_id = new_run()
    reviews.perform_review(run_id)
    published = []
    monkeypatch.setattr(github, "pages", lambda *a: published)

    def request(method, path, token, **kwargs):
        if method == "GET":
            return remote()
        published.append({"id": 99, "body": kwargs["json"]["body"], "user": {"login": "mortis-test[bot]"}})
        assert kwargs["json"]["event"] == "COMMENT"
        assert kwargs["json"]["comments"][0]["line"] == 7
        raise github.GitHubError(503, "Response lost")

    monkeypatch.setattr(github, "request", request)
    with pytest.raises(github.GitHubError):
        reviews.publish_run(run_id)
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Publication must not rerun OCR"))
    reviews.publish_run(run_id)
    assert len(published) == 1
    with transaction() as db:
        assert db.get(ReviewRun, run_id).publication == "published"
        assert db.get(ReviewRun, run_id).review_ids == [99]


def test_resolution_sync_is_canonical_and_versioned(seed, github_stub, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    monkeypatch.setattr(ocr, "review", lambda *a, **k: ("completed", {}, [copy.deepcopy(COMMENT)]))
    run_id = new_run()
    reviews.perform_review(run_id)
    with transaction() as db:
        finding = db.scalar(select(Finding))
        fid = finding.id
    thread = {
        "id": "thread",
        "isResolved": True,
        "resolvedBy": {"login": "alice"},
        "comments": {
            "nodes": [
                {
                    "author": {"login": "mortis-test[bot]"},
                    "body": reviews.marker("finding", fid),
                    "databaseId": 99,
                    "url": "https://github.com/team/project/pull/3#discussion",
                }
            ]
        },
    }
    monkeypatch.setattr(github, "threads", lambda *a: [thread])
    with transaction() as db:
        reviews.reconcile_threads(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "token")
        finding = db.get(Finding, fid)
        assert finding.status == "resolved" and finding.thread_id == "thread"
        reviews.request_finding_status(db, finding, "open", db.get(User, 7), "")
        db.flush()
        old = db.scalar(select(Job).where(Job.kind == "resolve")).payload
        reviews.request_finding_status(db, finding, "dismissed", db.get(User, 7), "Intentional")
        db.flush()
        latest = db.scalars(select(Job).where(Job.kind == "resolve")).all()[-1].payload
    mutations = []
    monkeypatch.setattr(github, "set_thread", lambda *args: mutations.append(args))
    reviews.synchronize_finding(old)
    assert mutations == []
    reviews.synchronize_finding(latest)
    assert mutations[-1][-1] is True
    with transaction() as db:
        assert db.get(Finding, fid).status == "dismissed" and db.get(Finding, fid).desired_status is None
        thread["isResolved"] = False
        reviews.reconcile_threads(db, db.get(PullRequest, "pr"), db.get(Repository, 22), "token")
        assert db.get(Finding, fid).status == "open"
        assert db.scalar(select(func.count()).select_from(Activity)) >= 3


def test_stale_publication_and_cancelled_review_do_no_work(seed, github_stub, monkeypatch):
    run_id = new_run()
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.status = "cancelled"
        run.cancel_requested = True
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Cancelled run executed"))
    reviews.perform_review(run_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.status = "completed"
        run.cancel_requested = False
    monkeypatch.setattr(github, "request", lambda *a, **k: remote("c" * 40))
    reviews.publish_run(run_id)
    with transaction() as db:
        assert db.get(ReviewRun, run_id).publication == "not_applicable"


def test_jobs_claim_once_and_recover_lost_dispatch(seed, monkeypatch):
    with transaction() as db:
        job = reviews.enqueue(db, "scan_repository", {"repository_id": 22})
        job_id = job.id
    executed = []
    monkeypatch.setattr(jobs, "execute", lambda *args: executed.append(args))
    jobs.execute_job(job_id)
    jobs.execute_job(job_id)
    assert len(executed) == 1
    with transaction() as db:
        job = db.get(Job, job_id)
        job.status = "running"
        job.lease_until = now() - timedelta(seconds=1)
    sent = []
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda **kwargs: sent.append(kwargs))
    jobs.dispatch()
    assert len(sent) == 1
    with transaction() as db:
        assert db.get(Job, job_id).status == "queued"


def test_automatic_reviews_skip_forks_and_drafts(seed, github_stub, monkeypatch):
    data = remote()
    data["head"]["repo"]["id"] = 999
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    jobs.sync_pr(22, 3)
    data["head"]["repo"]["id"] = 22
    data["draft"] = True
    jobs.sync_pr(22, 3)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 0
    data["draft"] = False
    jobs.sync_pr(22, 3)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1


def test_queued_review_cannot_send_rotated_credentials_to_old_endpoint(seed, github_stub, monkeypatch):
    run_id = new_run()
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Changed endpoint must not execute"))
    with transaction() as db:
        db.get(Connection, "connection").base_url = "https://new-provider.example/v1"
    with pytest.raises(ValueError, match="endpoint changed"):
        reviews.perform_review(run_id)


def test_rotated_oauth_credentials_survive_later_request_failure(seed, monkeypatch):
    from code_mortis.security import seal

    with transaction() as db:
        user = db.get(User, 7)
        user.refresh_token = seal("old-refresh")
        user.token_expires = now() - timedelta(seconds=1)
    monkeypatch.setattr(
        github,
        "oauth_request",
        lambda data: {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
    )
    with pytest.raises(github.GitHubError):
        with transaction() as db:
            assert github.user_token(db, db.get(User, 7)) == "new-access"
            raise github.GitHubError(404, "Repository unavailable")
    with transaction() as db:
        assert unseal(db.get(User, 7).refresh_token) == "new-refresh"
        assert unseal(db.get(User, 7).access_token) == "new-access"
