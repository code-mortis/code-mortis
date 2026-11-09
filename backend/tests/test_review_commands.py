import copy
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from runtime_helpers import file_limit_result
from sqlalchemy import func, select
from test_checks import rerun_body
from test_review_flow import new_run, remote

from code_mortis import jobs
from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import (
    AppConfig,
    Connection,
    Installation,
    Job,
    Membership,
    Repository,
    ReviewRun,
    User,
    WebhookDelivery,
)
from code_mortis.reviews import checks, mortis
from code_mortis.reviews import service as reviews
from code_mortis.schemas.common import RepoSettings


def skipped_run(monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    result = file_limit_result([f"file{i}.py" for i in range(41)], 40)
    monkeypatch.setattr(mortis, "review", lambda *a, **k: result)
    identity = new_run()
    reviews.perform_review(identity)
    return identity


def command_body(comment_id=123):
    return {
        "action": "created", "installation": {"id": 10}, "repository": {"id": 22},
        "sender": {"id": 7, "type": "User"}, "issue": {"number": 3, "pull_request": {}},
        "comment": {"id": comment_id, "user": {"id": 7, "type": "User"},
                    "body": "@mortis-test[bot] review anyway"},
    }


def deliver(body=None, delivery="command"):
    with transaction() as db:
        db.add(WebhookDelivery(id=delivery, event="issue_comment"))
    payload = {"event": "issue_comment", "body": body or command_body(), "delivery": delivery}
    jobs.process_webhook(payload)
    return payload


def test_notice_recovers_lost_response_without_analysis(seed, github_stub, monkeypatch):
    source = skipped_run(monkeypatch)
    published = []
    monkeypatch.setattr(github, "pages", lambda *a: published)

    def request(method, path, token, **kwargs):
        if method == "GET":
            return remote()
        plan = kwargs["json"]
        assert plan["event"] == "COMMENT" and plan["comments"] == []
        assert plan["commit_id"] == "a" * 40
        assert plan["body"] == (
            "**Review skipped:** 41 reviewable files exceed the limit of 40. No files were reviewed.\n\n"
            "To bypass the file limit for one review of this revision, post:\n"
            "`@mortis-test[bot] review anyway`\n\n"
            "Requires an enabled Code Mortis member with repository write access. "
            "Choose a shared API connection. Other review limits still apply.\n\n"
            f"[View run details](http://testserver/runs/{source})\n\n" + reviews.marker("run", f"{source}:0")
        )
        with transaction() as db:
            assert db.get(ReviewRun, source).publication_plan == [plan]
        published.append({"id": 99, "body": plan["body"], "user": {"login": "mortis-test[bot]"}})
        raise github.GitHubError(503, "Response lost")

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(mortis, "review", lambda *a, **k: pytest.fail("Notice publication cannot run AI"))
    with pytest.raises(github.GitHubError):
        reviews.publish_run(source)
    monkeypatch.setattr(reviews, "publication_plan", lambda *a: pytest.fail("Must reuse the saved notice"))
    reviews.publish_run(source)
    reviews.publish_run(source)
    with transaction() as db:
        run = db.get(ReviewRun, source)
        assert run.status == "skipped" and run.publication == "published"
        assert run.review_ids == [99] and len(published) == 1
        assert checks.check_payload(run, 0)["conclusion"] == "skipped"


@pytest.mark.parametrize("change", ["head", "base", "closed"])
def test_notice_rejects_changed_or_closed_pr(seed, github_stub, monkeypatch, change):
    source = skipped_run(monkeypatch)
    data = remote("c" * 40) if change == "head" else remote()
    if change == "base":
        data["base"]["sha"] = "c" * 40
    if change == "closed":
        data["state"] = "closed"

    def request(method, *a, **k):
        assert method == "GET", "Stale notices must not be published"
        return data

    monkeypatch.setattr(github, "request", request)
    reviews.publish_run(source)
    with transaction() as db:
        run = db.get(ReviewRun, source)
        assert run.status == "skipped" and run.publication == "not_applicable"
        assert checks.check_payload(run, 0)["conclusion"] == "cancelled"


def test_saved_skip_recovers_after_worker_interruption(seed, github_stub, monkeypatch):
    source = skipped_run(monkeypatch)
    with transaction() as db:
        run = db.get(ReviewRun, source)
        run.status, run.publication = "running", "not_applicable"
        assert reviews.complete_saved_review(db, run, "Worker stopped")
        assert run.status == "skipped" and run.publication == "pending"
        run.report = {"manifest": {"coverage": {"selected": []}}}
        assert not reviews.complete_saved_review(db, run)
        assert not reviews.can_publish(run)


def test_notice_retry_api_and_check_reuse_analysis(seed, github_stub, monkeypatch, client):
    source = skipped_run(monkeypatch)
    with transaction() as db:
        run = db.get(ReviewRun, source)
        run.publication, run.check_id = "failed", 100
    assert client.post(f"/api/v1/runs/{source}/publish").status_code == 202
    with transaction() as db:
        run = db.get(ReviewRun, source)
        assert run.publication == "pending"
        run.publication = "failed"
        db.add(WebhookDelivery(id="rerun", event="check_run"))
    with transaction() as db:
        checks.rerequest(db, rerun_body(source), "rerun")
        assert db.get(ReviewRun, source).publication == "pending"
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "review")) == 1


@pytest.mark.parametrize("slug,command", [
    ("mortis-test", " \n@Mortis-Test[BOT] REVIEW ANYWAY\n "),
    ("mortis-test", "@mortis-test review anyway"),
    ("custom-app-123", "@custom-app-123[bot] review anyway"),
    ("custom-app-123", "@custom-app-123 review anyway"),
    ("mortis-test", "@codemortis review anyway"),
])
def test_command_is_one_review_and_keeps_current_limits_and_original_connection(
    seed, github_stub, monkeypatch, client, slug, command,
):
    source = skipped_run(monkeypatch)
    with transaction() as db:
        db.get(AppConfig, 1).github_slug = slug
        original = copy.deepcopy(db.get(ReviewRun, source).config)
        repo = db.get(Repository, 22)
        repo.connection_id = None
        repo.settings = RepoSettings(max_files=10, token_budget=300000, timeout_seconds=600,
                                     exclusions="*.lock", delegated_context_bytes=400000).model_dump()
        settings = copy.deepcopy(repo.settings)
    body = command_body()
    body["comment"]["body"] = command
    raw = json.dumps(body).encode()
    headers = {"x-github-delivery": "command", "x-github-event": "issue_comment",
               "x-hub-signature-256": "sha256=" + hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()}
    assert client.post("/api/v1/webhooks/github", content=raw).status_code == 401
    assert not client.post("/api/v1/webhooks/github", content=raw, headers=headers).json()["duplicate"]
    assert client.post("/api/v1/webhooks/github", content=raw, headers=headers).json()["duplicate"]
    with transaction() as db:
        payload = db.scalar(select(Job).where(Job.kind == "webhook")).payload
    jobs.process_webhook(payload)
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id != source))
        assert run.trigger == "manual" and run.actor_id == 7 and run.connection_id == "connection"
        assert run.config["settings"] == {**settings, "max_files": None}
        assert run.progress["file_limit_override"] == {"source_run_id": source, "comment_id": 123}
        assert db.get(ReviewRun, source).config == original
        assert db.get(ReviewRun, source).status == "skipped"
        assert db.get(Repository, 22).settings == settings
        run.status, run.publication = "completed", "published"
        identity = run.id
    jobs.process_webhook(payload)
    deliver(body, "new-delivery-same-comment")
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 2
        assert db.get(WebhookDelivery, "command").handled_at
        repo = db.get(Repository, 22)
        repo.connection_id = "connection"
        pr = reviews.upsert_pr(db, repo, remote("c" * 40))
        following = reviews.create_run(db, pr, repo, "automatic")
        assert following.config["settings"]["max_files"] == 10 and following.id != identity


def test_concurrent_commands_reuse_active_run_and_remember_both_comments(seed, github_stub, monkeypatch):
    source = skipped_run(monkeypatch)
    with transaction() as db:
        db.add_all([WebhookDelivery(id=f"command-{i}", event="issue_comment") for i in range(2)])
    payloads = [{"event": "issue_comment", "body": command_body(123 + i), "delivery": f"command-{i}"}
                for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(jobs.process_webhook, payloads))
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id != source))
        assert run.progress["comment_requests"] == {"issue-comment:22:123": True, "issue-comment:22:124": True}
        run.status, run.publication = "completed", "published"
    deliver(command_body(123), "replay-123")
    deliver(command_body(124), "replay-124")
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 2


@pytest.mark.parametrize("path,value", [
    (("action",), "edited"), (("action",), "deleted"), (("comment",), []),
    (("sender",), []), (("repository",), []), (("installation",), []), (("issue",), None),
    (("comment", "user"), None), (("issue", "pull_request"), None),
    (("comment", "user", "type"), "Bot"), (("sender", "type"), "Bot"),
    (("comment", "user", "id"), 8), (("comment", "id"), True), (("sender", "id"), "7"),
    (("issue", "number"), 0), (("repository", "id"), False), (("installation", "id"), -1),
    (("comment", "body"), None), (("comment", "body"), "> @codemortis review anyway"),
    (("comment", "body"), "`@codemortis review anyway`"),
    (("comment", "body"), "Please @codemortis review anyway"),
    (("comment", "body"), "@codemortis review anyway\nExtra text"),
    (("comment", "body"), "@another-app[bot] review anyway"),
    (("comment", "body"), "@another-app review anyway"),
    (("comment", "body"), "@mortis-test[bot] review anyway please"),
    (("comment", "body"), "@mortis-test[bot] review anyway\nExtra text"),
    (("comment", "body"), "> @mortis-test[bot] review anyway"),
    (("comment", "body"), "`@mortis-test[bot] review anyway`"),
    (("comment", "body"), "mortis-test[bot] review anyway"),
])
def test_irrelevant_and_malformed_comments_are_ignored(seed, github_stub, monkeypatch, path, value):
    skipped_run(monkeypatch)
    body = command_body()
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: pytest.fail("Ignored comments need no authorization"))
    deliver(body)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1


@pytest.mark.parametrize("reason", [
    "outsider", "disabled", "membership", "no_write", "installation", "inactive", "repo_disabled",
    "closed", "stale", "base", "no_skip", "other_skip", "missing_connection", "personal", "no_pr",
])
def test_command_rejects_unauthorized_or_inapplicable_requests(seed, github_stub, monkeypatch, reason):
    source = skipped_run(monkeypatch)
    body = command_body()
    with transaction() as db:
        if reason == "outsider":
            body["sender"]["id"] = body["comment"]["user"]["id"] = 999
        elif reason == "disabled":
            db.get(User, 7).enabled = False
        elif reason == "membership":
            db.delete(db.get(Membership, 7))
        elif reason == "installation":
            body["installation"]["id"] = 999
        elif reason == "inactive":
            db.get(Installation, 10).active = False
        elif reason == "repo_disabled":
            db.get(Repository, 22).enabled = False
        elif reason == "no_skip":
            db.get(ReviewRun, source).status = "completed"
        elif reason == "other_skip":
            db.get(ReviewRun, source).report = {}
        elif reason == "missing_connection":
            db.get(ReviewRun, source).connection_id = None
        elif reason == "personal":
            db.add_all([User(id=8, login="subscriber"), Membership(github_id=8, login="subscriber")])
            db.flush()
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id = "chatgpt", 8
        elif reason == "no_pr":
            body["issue"]["number"] = 4
    data = remote("c" * 40) if reason == "stale" else remote()
    if reason == "closed":
        data["state"] = "closed"
    elif reason == "base":
        data["base"]["sha"] = "c" * 40
    monkeypatch.setattr(github, "request", lambda *a, **k: data)
    if reason == "no_write":
        def denied(*a, **k):
            raise github.GitHubError(403, "Write permission required")
        monkeypatch.setattr(github, "authorize_repo", denied)
    with pytest.raises((ValueError, github.GitHubError)):
        deliver(body)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
def test_personal_connection_owner_can_override(seed, github_stub, monkeypatch, kind):
    source = skipped_run(monkeypatch)
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id = kind, 7
    with pytest.raises(ValueError, match="retired"):
        deliver()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 1
        assert db.get(ReviewRun, source).config["connection"]["kind"] == "api"


@pytest.mark.parametrize("revocation", ["membership", "disabled", "no_write"])
def test_command_rechecks_actor_before_execution(seed, github_stub, monkeypatch, revocation):
    source = skipped_run(monkeypatch)
    deliver()
    with transaction() as db:
        run_id = db.scalar(select(ReviewRun.id).where(ReviewRun.id != source))
        if revocation == "membership":
            db.delete(db.get(Membership, 7))
        elif revocation == "disabled":
            db.get(User, 7).enabled = False
    if revocation == "no_write":
        def denied(*a, **k):
            raise github.GitHubError(403, "Write permission required")
        monkeypatch.setattr(github, "authorize_repo", denied)
    monkeypatch.setattr(mortis, "review", lambda *a, **k: pytest.fail("Revoked members cannot run AI"))
    with pytest.raises((ValueError, github.GitHubError)):
        reviews.perform_review(run_id)
