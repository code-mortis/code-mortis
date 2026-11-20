from datetime import timedelta

from fastapi.testclient import TestClient

from code_mortis.core.db import now, transaction
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.main import app
from code_mortis.models import Job, LoginSession, Membership, Repository, ReviewRun, SecurityScan, User


def review(identity, *, status="completed", finished_at=None, pr="pr"):
    return ReviewRun(id=identity, pr_id=pr, config={}, trigger="manual", dedup_key=identity,
                     base_sha="b" * 40, head_sha="a" * 40, status=status, finished_at=finished_at)


def scan(identity, *, status="completed", finished_at=None, repository_id=22):
    return SecurityScan(id=identity, repository_id=repository_id, actor_id=7, branch="main",
                        request_key=identity, head_sha="a" * 40, config={}, status=status,
                        finished_at=finished_at)


def background(identity, status="running", *, actor_id=7, mode="context", expires_at=True):
    payload = {"actor_id": actor_id, "repository_id": 22, "mode": mode, "generation_id": identity}
    if expires_at:
        payload["expires_at"] = now().timestamp() + 600
    return Job(id=identity, kind="generate_background", status=status, payload=payload)


def visible_repos(monkeypatch, ids=(22,)):
    monkeypatch.setattr(github, "visible_repositories", lambda db, _, **kwargs: [
        repo for identity in ids if (repo := db.get(Repository, identity)) is not None
    ])


def test_workspace_activity_groups_stages_and_keeps_five_read_updates(seed, client, github_stub, monkeypatch):
    visible_repos(monkeypatch)
    with transaction() as db:
        db.add_all([review(f"old-{n}", finished_at=now() - timedelta(days=n + 1)) for n in range(8)])
        live = review("live-review", status="running")
        live.stages = {"assessment": {"status": "running"}, "verification": {"status": "running"}}
        live_scan = scan("live-scan", status="running")
        live_scan.stages = {"assessment": {"status": "running"}}
        db.add_all([live, live_scan, review("waiting", status="queued"),
                    background("active-context"), background("someone-elses-draft", actor_id=100),
                    Job(id="child-stage", kind="assess_candidates", status="running", payload={"run_id": live.id}),
                    Job(id="summary-retry", kind="generate_summary", status="running",
                        payload={"run_id": "old-0"})])
    response = client.get("/api/v1/activity")
    assert response.status_code == 200, response.text
    feed = response.json()
    running = {row["id"]: row for row in feed["running"]}
    assert feed["running_count"] == 4
    assert feed["queued_count"] == 1
    assert feed["unread_count"] == 0
    assert len(feed["updates"]) == 5
    assert set(running) == {"live-review", "live-scan", "waiting", "active-context", "summary-retry"}
    assert running["live-review"]["stages"] == ["Assessment", "Fix verification"]
    assert running["live-scan"]["href"] == "/security/scans/live-scan"
    assert running["waiting"]["stages"] == []
    assert running["active-context"]["href"] == "/repositories/22/settings?tab=context&generation=active-context"
    assert running["summary-retry"]["href"] == "/runs/old-0?summaryJob=summary-retry"


def test_activity_receipts_respect_visible_repos_session_user_and_snapshot(seed, client, github_stub, monkeypatch):
    visible_repos(monkeypatch)
    with transaction() as db:
        db.add_all([User(id=8, login="reader", activity_started_at=now() - timedelta(seconds=1)),
                    Membership(github_id=8, login="reader", role="member"),
                    Repository(id=23, installation_id=10, full_name="team/hidden")])
        db.flush()
        db.add_all([LoginSession(token_hash=digest("other-session"), user_id=8, csrf="other",
                                 expires_at=now() + timedelta(days=1)),
                    review("visible", finished_at=now()), scan("private", finished_at=now(), repository_id=23)])
    first = client.get("/api/v1/activity").json()
    assert [row["id"] for row in first["updates"]] == ["visible"]
    completed_version = first["updates"][0]["finished_at"]
    bulk = {"mode": "all", "as_of": first["as_of"], "snapshot": first["snapshot"]}
    assert client.post("/api/v1/activity/read", headers={"x-csrf-token": "invalid"}, json=bulk).status_code == 403
    assert client.post("/api/v1/activity/read", json={**bulk, "snapshot": "invalid"}).status_code == 422
    with transaction() as db:
        db.add(scan("new-scan", finished_at=now()))
    assert client.post("/api/v1/activity/read", json=bulk).status_code == 204
    after = client.get("/api/v1/activity").json()
    assert [(row["id"], row["unread"]) for row in after["updates"]] == [("new-scan", True), ("visible", False)]
    with TestClient(app) as other:
        other.cookies.set("cm_session", "other-session")
        other.headers["x-csrf-token"] = "other"
        assert other.get("/api/v1/activity").json()["unread_count"] == 2
        assert other.post("/api/v1/activity/read", json=bulk).status_code == 422
        assert other.post("/api/v1/activity/read", json={
            "mode": "one", "kind": "review", "id": "visible", "finished_at": completed_version,
        }).status_code == 204
        assert other.get("/api/v1/activity").json()["unread_count"] == 1
    assert client.get("/api/v1/activity").json()["unread_count"] == 1


def test_terminal_generation_tracks_all_modes_and_ignores_cancellation_fence(seed, client, github_stub, monkeypatch):
    visible_repos(monkeypatch)
    with transaction() as db:
        db.add_all([background("draft"), background("overview", mode="summary"),
                    background("context-refresh", mode="context_refresh"),
                    background("summary-draft", mode="summary_draft"),
                    background("summary-refresh", mode="summary_refresh"),
                    background("cancel-fence", "cancelled", expires_at=False),
                    Job(id="unrelated", kind="webhook", status="done", payload={}),
                    review("summary-target", finished_at=now()),
                    Job(id="summary-attempt", kind="generate_summary", status="queued",
                        payload={"run_id": "summary-target"})])
    snapshot = client.get("/api/v1/activity").json()
    cutoff = snapshot["as_of"]
    with transaction() as db:
        for identity, status in (("draft", "cancelled"), ("overview", "done"), ("summary-draft", "failed"),
                                 ("summary-refresh", "done"), ("context-refresh", "done"),
                                 ("summary-attempt", "done")):
            db.get(Job, identity).status = status
        db.flush()
        assert db.get(Job, "draft").finished_at is not None
        assert db.get(Job, "cancel-fence").finished_at is None
        assert db.get(Job, "unrelated").finished_at is None
    result = client.get("/api/v1/repositories/22/background/overview").json()
    assert result["mode"] == "summary"
    assert result["finished_at"]
    result = client.get("/api/v1/runs/summary-target/summary/attempts/summary-attempt")
    assert result.json()["finished_at"]
    assert client.get("/api/v1/runs/summary-target/summary/attempts/draft").status_code == 404
    assert client.post("/api/v1/activity/read", json={
        "mode": "all", "as_of": cutoff, "snapshot": snapshot["snapshot"],
    }).status_code == 204
    feed = client.get("/api/v1/activity").json()
    assert feed["unread_count"] == 6
    rows = {row["id"]: row for row in feed["updates"]}
    assert rows["overview"]["href"] == "/repositories/22/overview?generation=overview"
    assert rows["context-refresh"]["kind"] == "context"
    assert rows["context-refresh"]["href"] == "/repositories/22/settings?tab=context"
    assert rows["summary-draft"]["href"] == "/repositories/22/settings?tab=context&generation=summary-draft"
    assert rows["summary-refresh"]["kind"] == "repository_summary"
    assert rows["summary-refresh"]["href"] == "/repositories/22/overview?generation=summary-refresh"
    assert rows["draft"]["status"] == "cancelled"

    for kind in ("draft", "overview"):
        assert client.post("/api/v1/activity/read", json={
            "mode": "one", "kind": rows[kind]["kind"], "id": kind, "finished_at": cutoff,
        }).status_code == 204
    assert client.get("/api/v1/activity").json()["unread_count"] == 6

    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: {"id": 22, "permissions": {"pull": True}})
    assert {row["id"] for row in client.get("/api/v1/activity").json()["updates"]} == {
        "summary-target", "summary-attempt",
    }


def test_updates_cursor_pages_all_unread_before_recent_read_results(seed, client, github_stub, monkeypatch):
    visible_repos(monkeypatch)
    with transaction() as db:
        db.add_all([review(f"unread-{index:02d}", finished_at=now()) for index in range(51)])
        db.add_all([review(f"read-{index}", finished_at=now() - timedelta(days=index + 1)) for index in range(7)])
    first = client.get("/api/v1/activity").json()
    assert first["unread_count"] == 51
    assert len(first["updates"]) == 50
    second = client.get("/api/v1/activity", params={"updates_cursor": first["next_updates_cursor"]}).json()
    assert second["next_updates_cursor"] is None
    assert len(second["updates"]) == 6
    assert len({row["id"] for row in first["updates"] + second["updates"]}) == 56
    assert client.get("/api/v1/activity?updates_cursor=bad").status_code == 422


def test_mark_all_keeps_completions_that_commit_after_the_visible_snapshot(seed, client, github_stub, monkeypatch):
    visible_repos(monkeypatch)
    with transaction() as db:
        db.add_all([review("finished-first", finished_at=now()), review("finishing", status="running")])
    with transaction() as pending:
        finishing = pending.get(ReviewRun, "finishing")
        finishing.status = "skipped"
        finishing.finished_at = now()
        pending.flush()
        shown = client.get("/api/v1/activity").json()
        assert [row["id"] for row in shown["updates"]] == ["finished-first"]
        assert [row["id"] for row in shown["running"]] == ["finishing"]
    assert client.post("/api/v1/activity/read", json={
        "mode": "all", "as_of": shown["as_of"], "snapshot": shown["snapshot"],
    }).status_code == 204
    later = client.get("/api/v1/activity").json()
    assert [(row["id"], row["status"], row["unread"]) for row in later["updates"]] == [
        ("finishing", "skipped", True), ("finished-first", "completed", False),
    ]


def test_same_review_retry_reopens_unread_without_changing_saved_finish_time(seed, client, github_stub, monkeypatch):
    visible_repos(monkeypatch)
    original = now()
    with transaction() as db:
        db.add(review("retried", finished_at=original))
    first = client.get("/api/v1/activity").json()["updates"][0]
    stale_read = {"mode": "one", "kind": "review", "id": "retried", "finished_at": first["finished_at"]}
    assert client.post("/api/v1/activity/read", json=stale_read).status_code == 204
    with transaction() as db:
        db.get(ReviewRun, "retried").status = "running"
    assert [row["id"] for row in client.get("/api/v1/activity").json()["running"]] == ["retried"]
    with transaction() as db:
        run = db.get(ReviewRun, "retried")
        run.status = "completed"
        db.flush()
        assert run.finished_at == original
    assert client.post("/api/v1/activity/read", json=stale_read).status_code == 204
    new = client.get("/api/v1/activity").json()["updates"][0]
    assert new["unread"]
    assert new["finished_at"] != first["finished_at"]
    detail = client.get("/api/v1/runs/retried").json()["run"]
    assert detail["finished_at"] == first["finished_at"]
    assert detail["activity_finished_at"] == new["finished_at"]


def test_generation_receipts_recheck_write_access_and_preserve_throttled_counts(seed, client, github_stub, monkeypatch):
    visible_repos(monkeypatch)
    with transaction() as db:
        db.add(background("draft", "done"))
    shown = client.get("/api/v1/activity").json()
    assert shown["unread_count"] == 1

    def throttle_listing(db, user, *, fail_on_throttle=False):
        assert fail_on_throttle
        raise github.GitHubError(403, "GitHub rate limited", retry_after=6)

    with monkeypatch.context() as limited:
        limited.setattr(github, "visible_repositories", throttle_listing)
        response = client.get("/api/v1/activity")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "6"
        assert client.post("/api/v1/activity/read", json={
            "mode": "all", "as_of": shown["as_of"], "snapshot": shown["snapshot"],
        }).status_code == 429

    checks = []

    def read_only(db, user, name, write=False):
        checks.append(write)
        if write:
            raise github.GitHubError(403, "Write access revoked")
        return {"id": 22, "permissions": {"pull": True, "push": True}}

    monkeypatch.setattr(github, "authorize_repo", read_only)
    assert client.post("/api/v1/activity/read", json={
        "mode": "one", "kind": "context", "id": "draft", "finished_at": shown["updates"][0]["finished_at"],
    }).status_code == 204
    assert client.post("/api/v1/activity/read", json={
        "mode": "all", "as_of": shown["as_of"], "snapshot": shown["snapshot"],
    }).status_code == 204
    assert checks == [True, True]
    assert client.get("/api/v1/activity").json()["unread_count"] == 1
