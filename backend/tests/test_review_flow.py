import copy
import hashlib
import hmac
import json
from datetime import datetime, timedelta

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


def test_settings_failed_jobs_include_saved_metadata_without_payloads(seed, client):
    scheduled = now().replace(microsecond=0)
    with transaction() as db:
        db.add_all([
            Job(id="failed-job", kind="generate_background", status="failed", attempts=2,
                available_at=scheduled, error="Check the connection and retry.",
                payload={"api_key": "must-not-be-returned"}),
            Job(id="queued-job", kind="review", status="queued", payload={}),
        ])
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    failed = response.json()["failed_jobs"]
    assert len(failed) == 1
    assert failed[0]["id"] == "failed-job"
    assert failed[0]["kind"] == "generate_background"
    assert failed[0]["error"] == "Check the connection and retry."
    assert failed[0]["attempts"] == 2
    assert datetime.fromisoformat(failed[0]["available_at"]) == scheduled
    assert "payload" not in failed[0]
    assert "must-not-be-returned" not in response.text


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
    assert response.json()["manifest"]["default_permissions"] == {
        "contents": "write", "pull_requests": "write", "metadata": "read", "checks": "write", "issues": "read", "members": "read",
    }
    assert "issue_comment" in response.json()["manifest"]["default_events"]
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


@pytest.mark.parametrize("severity,icon", [("critical", "🔴"), ("high", "🟠"), ("medium", "🟡"), ("low", "🔵")])
def test_comment_header_without_suggestion(severity, icon):
    snapshot = {**COMMENT, "severity": severity}
    expected = f"**{icon} {severity.title()} · bug**\n\n{COMMENT['content']}\n\n{reviews.marker('finding', 'finding-id')}"
    assert reviews.comment_body("finding-id", snapshot) == expected
    del snapshot["suggestion_code"]
    assert reviews.comment_body("finding-id", snapshot) == expected


def test_comment_diff_preserves_indentation_blank_lines_and_multiple_hunks():
    original = ["def run():", "    retry()", "", *[f"    step_{i}()" for i in range(12)], "    return False"]
    suggested = [*original]
    suggested[1] = "    retry(limit=3)"
    suggested[-1] = "    return True"
    body = reviews.comment_body("finding-id", {
        **COMMENT, "existing_code": "\n".join(original), "suggestion_code": "\n".join(suggested),
    })
    assert "**Suggested change**\n\n```diff\n--- original\n+++ suggested\n" in body
    assert "@@ -1,5 +1,5 @@\n def run():\n-    retry()\n+    retry(limit=3)\n \n    " in body
    assert "@@ -13,4 +13,4 @@\n     step_9()\n     step_10()\n     step_11()\n-    return False\n+    return True" in body
    assert "step_5" not in body
    assert body.endswith("\n```\n\n" + reviews.marker("finding", "finding-id"))


@pytest.mark.parametrize("path,language", [
    ("src/main.py", "python"), ("types.pyi", "python"),
    ("app.js", "javascript"), ("app.mjs", "javascript"), ("app.cjs", "javascript"), ("view.jsx", "jsx"),
    ("src/app.TS", "typescript"), ("app.mts", "typescript"), ("app.cts", "typescript"), ("view.tsx", "tsx"),
    ("data.json", "json"), ("config.yaml", "yaml"), ("config.yml", "yaml"), ("config.toml", "toml"),
    ("index.html", "html"), ("index.htm", "html"), ("style.css", "css"), ("query.sql", "sql"),
    ("run.sh", "bash"), ("run.bash", "bash"), ("run.zsh", "zsh"),
    ("main.go", "go"), ("main.rs", "rust"), ("build/Dockerfile", "dockerfile"), ("build.dockerfile", "dockerfile"),
    ("data.unknown", "text"), ("README", "text"),
])
def test_comment_replacement_language(path, language):
    code = "    first\n\n    second\n"
    body = reviews.comment_body("finding-id", {
        **COMMENT, "path": path, "existing_code": "", "suggestion_code": code,
    })
    assert f"**Suggested change**\n\n```{language}\n{code}\n```\n\n" in body


@pytest.mark.parametrize("existing", ["return 1", "return 1\n"])
def test_comment_identical_lines_show_highlighted_replacement(existing):
    body = reviews.comment_body("finding-id", {
        **COMMENT, "existing_code": existing, "suggestion_code": "return 1",
    })
    assert "```python\nreturn 1\n```" in body
    assert "```diff" not in body


@pytest.mark.parametrize("existing", ["", "````\nold\n````"])
def test_comment_fences_protect_both_snippets(existing):
    suggestion = "```\nreplacement\n```"
    body = reviews.comment_body("finding-id", {
        **COMMENT, "existing_code": existing, "suggestion_code": suggestion,
    })
    fence, language = ("`````", "diff") if existing else ("````", "python")
    assert f"**Suggested change**\n\n{fence}{language}\n" in body
    assert body.endswith(f"\n{fence}\n\n" + reviews.marker("finding", "finding-id"))


@pytest.mark.parametrize("existing", ["", "old\n" * 15000])
def test_comment_oversized_code_is_omitted_whole(existing):
    body = reviews.comment_body("finding-id", {
        **COMMENT, "existing_code": existing, "suggestion_code": "new\n" * 15000,
    })
    assert len(body) <= 60000
    assert COMMENT["content"] in body
    assert "Suggested change omitted because it exceeds the comment size limit." in body
    assert "```" not in body
    assert body.endswith(reviews.marker("finding", "finding-id"))


@pytest.mark.parametrize("suggestion", ["", "return 1"])
def test_comment_oversized_explanation_has_notice_and_marker(suggestion):
    body = reviews.comment_body("finding-id", {
        **COMMENT, "content": "x" * 60000, "suggestion_code": suggestion,
    })
    assert len(body) == 60000
    assert body.startswith("**🟠 High · bug**\n\n")
    assert "Review text truncated because it exceeds the comment size limit." in body
    assert ("Suggested change omitted" in body) == bool(suggestion)
    assert "```" not in body
    assert body.endswith(reviews.marker("finding", "finding-id"))


def test_comment_exact_size_limit_keeps_complete_code_and_marker():
    snapshot = {**COMMENT, "content": "", "existing_code": "", "suggestion_code": "return 1"}
    overhead = len(reviews.comment_body("finding-id", snapshot))
    snapshot["content"] = "x" * (60000 - overhead)
    body = reviews.comment_body("finding-id", snapshot)
    assert len(body) == 60000
    assert "```python\nreturn 1\n```" in body
    assert body.endswith(reviews.marker("finding", "finding-id"))
    snapshot["content"] += "x"
    body = reviews.comment_body("finding-id", snapshot)
    assert len(body) <= 60000
    assert "Suggested change omitted" in body and "```" not in body
    assert body.endswith(reviews.marker("finding", "finding-id"))


def remote(head="a" * 40):
    return {
        "number": 3,
        "title": "Fix retry logic",
        "user": {"login": "owner", "type": "User"},
        "html_url": "https://github.com/team/project/pull/3",
        "state": "open",
        "head": {"sha": head, "ref": "fix/retries", "repo": {"id": 22}},
        "base": {"sha": "b" * 40},
    }


def new_run(*, assessed=False):
    with transaction() as db:
        run = reviews.create_run(
            db, db.get(PullRequest, "pr"), db.get(Repository, 22), "manual", db.get(User, 7)
        )
        if not assessed:
            # Existing lifecycle tests also protect pre-assessment run recovery.
            run.config = {key: value for key, value in run.config.items() if key != "assessment_policy"}
        db.flush()
        return run.id


def test_pull_creation_time_is_saved_for_new_and_existing_pulls(seed):
    created = now() - timedelta(days=7)
    data = {**remote(), "created_at": created.isoformat()}
    with transaction() as db:
        repo = db.get(Repository, 22)
        before = db.get(PullRequest, "pr").updated_at
        existing = reviews.upsert_pr(db, repo, data)
        assert existing.created_at == created
        assert existing.updated_at == before
        data["number"] = 4
        added = reviews.upsert_pr(db, repo, data)
        assert added.created_at == created
        assert added.updated_at >= before


@pytest.mark.parametrize("created", ["invalid", "2026-10-08T12:00:00", 123])
def test_pull_creation_time_requires_a_timezone(seed, created):
    with transaction() as db, pytest.raises((TypeError, ValueError)):
        reviews.upsert_pr(db, db.get(Repository, 22), {**remote(), "created_at": created})


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
    snapshot = {**COMMENT, "suggestion_code": "for attempt in range(3):"}
    monkeypatch.setattr(ocr, "review", lambda *a, **k: ("partial", {}, [copy.deepcopy(snapshot)]))
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
        body = kwargs["json"]["comments"][0]["body"]
        assert body.startswith("**🟠 High · bug**\n\n" + COMMENT["content"])
        assert "```diff\n--- original\n+++ suggested\n@@ -1 +1 @@\n-while True:\n+for attempt in range(3):\n```" in body
        with transaction() as db:
            finding = db.scalar(select(Finding))
            assert body.endswith(reviews.marker("finding", finding.id))
            assert db.get(ReviewRun, run_id).publication_plan[0] == kwargs["json"]
        raise github.GitHubError(503, "Response lost")

    monkeypatch.setattr(github, "request", request)
    with pytest.raises(github.GitHubError):
        reviews.publish_run(run_id)
    with transaction() as db:
        saved_plan = copy.deepcopy(db.get(ReviewRun, run_id).publication_plan)
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("Publication must not rerun OCR"))
    monkeypatch.setattr(reviews, "comment_body", lambda *a, **k: pytest.fail("Publication must reuse saved comments"))
    reviews.publish_run(run_id)
    assert len(published) == 1
    with transaction() as db:
        assert db.get(ReviewRun, run_id).publication == "published"
        assert db.get(ReviewRun, run_id).review_ids == [99]
        assert db.get(ReviewRun, run_id).publication_plan == saved_plan


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


@pytest.mark.parametrize("error,status", [
    (ValueError("Publication failed"), "failed"),
    (github.GitHubError(503, "GitHub unavailable"), "queued"),
])
def test_publish_failure_with_missing_run_preserves_job_error(monkeypatch, error, status):
    with transaction() as db:
        job_id = reviews.enqueue(db, "publish", {"run_id": "missing"}).id

    def fail_publication(run_id):
        assert run_id == "missing"
        raise error

    monkeypatch.setattr(jobs, "publish_run", fail_publication)
    jobs.execute_job(job_id)
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == status and job.error == str(error)
        assert job.lease_until is None and job.dispatched_at is None
        assert job.attempts == 1


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


@pytest.mark.parametrize("login", ["dependabot[bot]", "automation"])
def test_repository_scans_skip_bot_prs_but_allow_manual_reviews(seed, github_stub, monkeypatch, client, login):
    data = remote("c" * 40)
    data["user"] = {"login": login, "type": "Bot"}
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    monkeypatch.setattr(github, "pages", lambda *a, **k: [data])
    reconciled = []
    monkeypatch.setattr(jobs, "reconcile_threads", lambda db, pr, *a: reconciled.append(pr.head_sha))
    jobs.scan_repository(22)
    with transaction() as db:
        payload = db.scalar(select(Job).where(Job.kind == "sync_pr")).payload
    jobs.execute("sync_pr", payload)
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        assert pr.author == login and pr.head_sha == data["head"]["sha"]
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "review")) == 0
    assert reconciled == [data["head"]["sha"]]
    response = client.post("/api/v1/pulls/pr/runs")
    assert response.status_code == 202, response.text
    assert response.json()["trigger"] == "manual" and response.json()["actor_id"] == 7


@pytest.mark.parametrize("action", ["opened", "reopened", "ready_for_review", "synchronize"])
@pytest.mark.parametrize("sender_type,author_type,expected", [
    ("User", "User", 1), ("Bot", "User", 0), ("User", "Bot", 0), ("Bot", "Bot", 0),
])
def test_pr_webhooks_only_automatically_review_human_prs(
    seed, github_stub, monkeypatch, action, sender_type, author_type, expected,
):
    data = remote()
    data["user"]["type"] = author_type
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    jobs.process_webhook({"event": "pull_request", "body": {
        "action": action, "installation": {"id": 10}, "repository": {"id": 22},
        "sender": {"type": sender_type}, "pull_request": data,
    }})
    with transaction() as db:
        payload = db.scalar(select(Job).where(Job.kind == "sync_pr")).payload
        assert payload["automatic"] is (sender_type == "User")
    jobs.execute("sync_pr", payload)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == expected
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "review")) == expected


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
