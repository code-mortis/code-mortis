from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select, text

from code_mortis import jobs
from code_mortis.core.db import engine, now, transaction
from code_mortis.integrations import github
from code_mortis.models import Finding, Installation, Job, PullRequest, Repository, ReviewRun


@pytest.fixture
def visible(seed, monkeypatch):
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [{"id": 22}])


def pull(db, identity, state="open", repo=22, author="owner", at=None):
    at = at or now()
    pr = PullRequest(
        id=identity, repository_id=repo, number=100 + db.scalar(select(func.count()).select_from(PullRequest)), title=f"Change {identity}",
        author=author, url=f"https://github.com/team/project/pull/{identity}", state=state,
        head_sha="a" * 40, base_sha="b" * 40, head_ref="feature", created_at=at, updated_at=at,
    )
    db.add(pr)
    db.flush()
    return pr


def run(db, pr, status, identity=None, **overrides):
    identity = identity or f"run-{pr.id}"
    review = ReviewRun(
        id=identity, pr_id=pr.id, trigger="manual", dedup_key=identity, config={},
        **{"head_sha": pr.head_sha, "base_sha": pr.base_sha, "status": status, **overrides},
    )
    db.add(review)
    db.flush()
    return review


def get(client, query=""):
    response = client.get("/api/v1/pulls" + query)
    assert response.status_code == 200, response.text
    return response.json()


def test_repository_latest_review_is_scoped_completed_and_uses_reviewed_commit(visible, client):
    finished = now().replace(microsecond=0)
    with transaction() as db:
        db.get(Repository, 22).last_pr_sync_check_at = finished - timedelta(minutes=5)
        reviewed = pull(db, "reviewed")
        run(db, reviewed, "completed", "older", finished_at=finished - timedelta(minutes=1))
        run(db, reviewed, "completed", "latest-a", head_sha="c" * 40, finished_at=finished)
        run(db, reviewed, "completed", "latest-z", head_sha="d" * 40, finished_at=finished)
        run(db, reviewed, "failed", "failed", finished_at=finished + timedelta(minutes=1))
        run(db, reviewed, "partial", "partial", finished_at=finished + timedelta(minutes=2))
        run(db, reviewed, "completed", "unfinished")
        db.add(Repository(id=23, installation_id=10, full_name="other/project"))
        db.flush()
        other = pull(db, "other", repo=23)
        run(db, other, "completed", "other-run", finished_at=finished + timedelta(minutes=3))
        number = reviewed.number
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200, response.text
    repository = response.json()[0]
    assert len(response.json()) == 1
    assert repository["latest_completed_review"] == {
        "run_id": "latest-z", "pull_request_id": "reviewed", "number": number,
        "head_sha": "d" * 40, "finished_at": finished.isoformat().replace("+00:00", "Z"),
    }
    assert repository["last_pr_sync_check_at"] == (finished - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def test_repository_activity_has_no_invented_history(visible, client):
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200, response.text
    repository = response.json()[0]
    assert repository["last_pr_sync_check_at"] is None
    assert repository["latest_completed_review"] is None


@pytest.mark.parametrize("state", ["open", "merged", "closed"])
@pytest.mark.parametrize("status", ["running", "queued", "blocked", "completed", "failed"])
def test_repository_review_activity(visible, github_stub, client, state, status):
    with transaction() as db:
        db.get(Repository, 22).enabled = False
        pr = pull(db, "activity", state)
        run(db, pr, status, "older", head_sha="c" * 40, created_at=now() - timedelta(minutes=1))
        run(db, pr, "completed", "newer")
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    repository = response.json()[0]
    assert repository["has_running_review"] is (status == "running")
    assert repository["open_prs"] == 1 + (state == "open")
    assert client.get("/api/v1/runs/older").json()["repository"]["has_running_review"] is (status == "running")
    with transaction() as db:
        db.get(ReviewRun, "older").status = "completed"
    assert client.get("/api/v1/repositories").json()[0]["has_running_review"] is False


def test_groups_current_revision_reruns_and_publication(visible, client):
    with transaction() as db:
        for status in ["running", "queued", "blocked", "failed", "partial", "skipped", "cancelled", "superseded"]:
            run(db, pull(db, status), status)
        for dimension in ["head_sha", "base_sha"]:
            run(db, pull(db, dimension), "completed", **{dimension: "c" * 40})
        reviewed = pull(db, "reviewed")
        review = run(db, reviewed, "completed", publication="failed", publication_error="GitHub unavailable")
        db.add(Finding(
            pr_id=reviewed.id, fingerprint="one", path="a.py", content="Bug", start_line=1,
            end_line=1, last_seen_run_id=review.id,
        ))
        rerun = pull(db, "rerun")
        run(db, rerun, "running", created_at=now() - timedelta(minutes=1))
        run(db, rerun, "completed", "later-completed")
        newer_failed = pull(db, "newer-failed")
        run(db, newer_failed, "completed", created_at=now() - timedelta(minutes=1))
        run(db, newer_failed, "failed", "later-failed")
        pull(db, "merged", "merged")
        pull(db, "closed", "closed")
    result = get(client)
    groups = {group["group"]: group for group in result["groups"]}
    assert list(groups) == ["active"]
    active = groups["active"]["items"]
    assert len(active) == 10
    active += get(client, "?group=active&page=2")["groups"][0]["items"]
    assert [item["review_status"] for item in active[:3]] == ["running", "running", "queued"]
    assert {item["id"] for item in active if item["review_status"] == "outdated"} == {"head_sha", "base_sha"}
    assert next(item for item in active if item["id"] == "pr")["review_status"] == "not_reviewed"
    completed = next(item for item in active if item["id"] == "reviewed")
    assert completed["open_findings"] == 1
    assert completed["publication"] == "failed"
    assert "report" not in completed and "config" not in completed
    assert result["state_counts"]["closed"] == 1
    assert next(g for g in get(client, "?state=closed")["groups"] if g["group"] == "closed")["items"][0]["id"] == "closed"
    assert len(get(client, "?state=all")["groups"]) == 3


@pytest.mark.parametrize("state", ["merged", "closed"])
@pytest.mark.parametrize("revision", ["current", "head_sha", "base_sha", "newer_completed"])
def test_running_reviews_stay_visible_until_finished(visible, client, state, revision):
    with transaction() as db:
        pr = pull(db, "in-progress", state, author="runner")
        overrides = {revision: "c" * 40} if revision in ["head_sha", "base_sha"] else {}
        if revision == "newer_completed":
            overrides = {"head_sha": "c" * 40}
            run(db, pr, "completed", "current-completed")
        run(db, pr, "running", "still-running", **overrides)
    default = get(client)
    assert [g["group"] for g in default["groups"]] == ["active"]
    item = default["groups"][0]["items"][0]
    assert item["id"] == "in-progress" and item["state"] == state and item["has_running_review"]
    assert item["review_status"] == ("running" if revision == "current" else "completed" if revision == "newer_completed" else "outdated")
    assert default["state_counts"][state] == 1
    for query in ["?state=all", f"?state={state}"]:
        result = get(client, query)
        assert sum(pr["id"] == "in-progress" for g in result["groups"] for pr in g["items"]) == 1
        assert next(g for g in result["groups"] if g["group"] == state)["total"] == 0
    assert get(client, "?review=running")["total"] == 1
    for query in ["?author=someone-else", "?repo=999", "?q=does-not-match", "?findings=open", "?review=failed"]:
        assert get(client, query)["total"] == 0
    with transaction() as db:
        db.get(ReviewRun, "still-running").status = "completed"
    assert get(client)["total"] == 1
    history = get(client, f"?state={state}")
    assert [g["group"] for g in history["groups"]] == [state]
    assert history["groups"][0]["items"][0]["id"] == "in-progress"
    assert not history["groups"][0]["items"][0]["has_running_review"]


def test_permissions_cover_rows_counts_options_and_paused_repositories(visible, seed, client, monkeypatch):
    with transaction() as db:
        db.get(Repository, 22).enabled = False
        db.add(Installation(id=11, account="inactive", active=False))
        db.flush()
        for identity, installation, active in [(23, 10, True), (24, 11, True), (25, 10, False)]:
            db.add(Repository(id=identity, installation_id=installation, full_name=f"secret/{identity}", active=active))
        db.flush()
        for identity in [23, 24, 25]:
            run(db, pull(db, f"private-{identity}", "merged", repo=identity, author="hidden"), "running")
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [{"id": n} for n in [22, 24, 25]])
    result = get(client)
    assert result["total"] == result["registered_total"] == 1
    assert result["authors"] == ["owner"]
    assert result["repositories"] == [{"id": 22, "full_name": "team/project"}]
    repositories = client.get("/api/v1/repositories").json()
    assert [repo["id"] for repo in repositories] == [22]
    assert repositories[0]["has_running_review"] is False
    assert get(client, "?repo=23")["total"] == 0
    assert get(client, "?author=hidden")["total"] == 0
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [])
    denied = get(client)
    assert denied["total"] == 0 and denied["authors"] == [] and denied["repositories"] == []
    def unavailable(*args, **kwargs):
        raise github.GitHubError(503, "GitHub unavailable")
    monkeypatch.setattr(github, "pages", unavailable)
    assert client.get("/api/v1/pulls").status_code == 502


def test_filters_facets_pagination_and_batched_queries(visible, client):
    with transaction() as db:
        for i in range(42):
            pr = pull(db, f"item-{i:02}", author="alice" if i % 2 else "bob", at=now().replace(microsecond=0))
            run(db, pr, "completed")
        pull(db, "closed", "closed", author="alice")
    statements = []
    def record(*args):
        statements.append(args[2])
    event.listen(engine(), "before_cursor_execute", record)
    try:
        first = get(client, "?review=completed")
    finally:
        event.remove(engine(), "before_cursor_execute", record)
    reviewed = next(g for g in first["groups"] if g["group"] == "active")
    assert first["page_size"] == 10
    assert reviewed["total"] == 42 and len(reviewed["items"]) == 10 and reviewed["has_more"]
    assert len(statements) < 20
    second = get(client, "?review=completed&group=active&page=2")["groups"][0]
    assert len(second["items"]) == 10
    assert not ({p["id"] for p in second["items"]} & {p["id"] for p in reviewed["items"]})
    last = get(client, "?review=completed&group=active&page=999")["groups"][0]
    assert last["page"] == 5 and len(last["items"]) == 2 and not last["has_more"]
    filtered = get(client, "?author=alice&review=completed&review=not_reviewed&findings=none&state=open")
    assert filtered["total"] == 21 and filtered["state_counts"]["closed"] == 1
    assert get(client, "?author=alice&author=bob&repo=22&repo=999&review=completed")["total"] == 42
    assert get(client, "?q=TEAM/PROJECT")["total"] == 43
    assert get(client, "?q=%233")["total"] == 1
    assert get(client, "?q=Change%20item-01")["total"] == 1
    assert get(client, "?q=%25")["total"] == 0
    for query in ["?q=@", "?q=@@", "?q=%23"]:
        assert get(client, query)["total"] == 0
    assert get(client, "?q=@ALICE")["total"] == 21
    assert get(client, "?findings=open")["total"] == 0


@pytest.mark.parametrize("group,state,status", [
    ("active", "open", "failed"), ("active", "open", "completed"),
    ("merged", "merged", "completed"), ("closed", "closed", "completed"),
])
def test_unchanged_sync_preserves_inbox_order(visible, github_stub, client, monkeypatch, group, state, status):
    remote = {}
    with transaction() as db:
        for i in range(3):
            pr = pull(db, f"sync-{i}", state, author="sync-test", at=now() - timedelta(hours=i + 1))
            run(db, pr, status)
            remote[pr.number] = {
                "number": pr.number, "title": pr.title, "user": {"login": pr.author},
                "html_url": pr.url, "state": "open" if state == "open" else "closed",
                "merged": state == "merged", "draft": pr.draft, "created_at": pr.created_at.isoformat(),
                "head": {"sha": pr.head_sha, "ref": pr.head_ref, "repo": {"id": pr.repository_id}},
                "base": {"sha": pr.base_sha},
            }
    monkeypatch.setattr(github, "request", lambda method, path, token: remote[int(path.rsplit("/", 1)[1])])
    query = f"?author=sync-test&state=all&group={group}"
    before = get(client, query)
    assert [item["id"] for item in before["groups"][0]["items"]] == ["sync-0", "sync-1", "sync-2"]
    for number in remote:
        jobs.sync_pr(22, number, automatic=False)
    assert get(client, query) == before
    # Edits update the recorded change time without promoting older PRs.
    number = max(remote)
    remote[number]["title"] = "Changed title"
    jobs.sync_pr(22, number, automatic=False)
    after = get(client, query)["groups"][0]["items"]
    assert [item["id"] for item in after] == ["sync-0", "sync-1", "sync-2"]
    assert after[-1]["updated_at"] > before["groups"][0]["items"][-1]["updated_at"]
    assert after[-1]["created_at"] == before["groups"][0]["items"][-1]["created_at"]


@pytest.mark.parametrize("ordering,newest_first", [("-created_at", True), ("created_at", False), ("-updated_at", False), ("updated_at", True)])
def test_newest_pulls_across_repositories_and_pages(visible, client, monkeypatch, ordering, newest_first):
    at = now() - timedelta(days=1)
    with transaction() as db:
        db.add(Repository(id=23, installation_id=10, full_name="team/other"))
        db.flush()
        for i in range(25):
            pr = pull(db, f"newest-{i:02}", "merged", repo=22 if i % 2 else 23,
                      at=at + timedelta(minutes=i))
            pr.updated_at = at + timedelta(minutes=25 - i)
            if i == 24:
                pr.number = 1
        unknown = pull(db, "unknown", "merged")
        unknown.created_at = None
        unknown.updated_at = at - timedelta(days=1) if ordering.startswith("-") else at + timedelta(days=1)
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [{"id": 22}, {"id": 23}])
    first = get(client, f"?state=merged&ordering={ordering}")["groups"][0]
    second = get(client, f"?state=merged&ordering={ordering}&group=merged&page=2")["groups"][0]
    third = get(client, f"?state=merged&ordering={ordering}&group=merged&page=3")["groups"][0]
    assert first["total"] == 26
    expected = [f"newest-{i:02}" for i in (range(24, -1, -1) if newest_first else range(25))] + ["unknown"]
    assert [pr["id"] for pr in first["items"]] == expected[:10]
    assert [pr["id"] for pr in second["items"]] == expected[10:20]
    assert [pr["id"] for pr in third["items"]] == expected[20:]


@pytest.mark.parametrize("field", ["title", "repository", "author"])
@pytest.mark.parametrize("descending", [False, True])
def test_text_ordering_keeps_review_priority_and_stable_ties(visible, client, monkeypatch, field, descending):
    with transaction() as db:
        db.add(Repository(id=23, installation_id=10, full_name="Alpha/project"))
        db.flush()
        for identity, name, repo, status in [
            ("zulu", "zulu", 22, "failed"), ("alpha-a", "Alpha", 23, "failed"),
            ("alpha-b", "alpha", 23, "failed"), ("running", "Zulu", 22, "running"),
            ("queued", "Alpha", 23, "queued"),
        ]:
            pr = pull(db, identity, repo=repo, author=name)
            pr.title = name
            run(db, pr, status)
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [{"id": 22}, {"id": 23}])
    result = get(client, f"?review=running&review=queued&review=failed&ordering={'-' if descending else ''}{field}")
    expected = ["zulu", "alpha-b", "alpha-a"] if descending else ["alpha-b", "alpha-a", "zulu"]
    assert [pr["id"] for pr in result["groups"][0]["items"]] == ["running", "queued", *expected]
    assert result["total"] == 5


def test_unified_inbox_orders_and_paginates_across_states(visible, client):
    with transaction() as db:
        at = now()
        for index in range(21):
            pr = pull(db, f"unified-{index:02}", ["open", "merged", "closed"][index % 3], author="unified", at=at - timedelta(minutes=index))
            if index == 20:
                run(db, pr, "running")
            elif index == 18:
                run(db, pr, "queued")
        pull(db, "not-visible", repo=22, author="someone-else")
    query = "?group=all&state=all&author=unified"
    first = get(client, query)
    assert first["total"] == first["groups"][0]["total"] == 21
    assert len(first["groups"]) == 1
    assert first["groups"][0]["group"] == "all"
    second = get(client, query + "&page=2")["groups"][0]
    last = get(client, query + "&page=999")["groups"][0]
    items = first["groups"][0]["items"] + second["items"] + last["items"]
    assert [len(first["groups"][0]["items"]), len(second["items"]), len(last["items"])] == [10, 10, 1]
    assert [pr["id"] for pr in items] == ["unified-20", "unified-18", *[f"unified-{index:02}" for index in range(20) if index != 18]]
    assert last["page"] == 3 and not last["has_more"]
    ascending = get(client, query + "&ordering=title")["groups"][0]["items"]
    assert [pr["id"] for pr in ascending[:4]] == ["unified-20", "unified-18", "unified-00", "unified-01"]
    open_only = get(client, "?group=all&state=open&author=unified")["groups"][0]
    assert open_only["total"] == 8
    assert all(pr["state"] == "open" or pr["has_running_review"] for pr in open_only["items"])


@pytest.mark.parametrize("query", ["?state=bad", "?repo=-1", "?review=clean", "?page=0", "?group=bad", "?findings=bad", "?ordering=unknown", "?ordering=--title"])
def test_query_validation(visible, client, query):
    assert client.get("/api/v1/pulls" + query).status_code == 422


def test_creation_time_migration_preserves_existing_pulls(seed):
    with transaction() as db:
        before = db.execute(text("SELECT id, title, updated_at FROM pull_requests")).one()
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261007_verified_resolution")
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        after = db.execute(text("SELECT id, title, updated_at, created_at FROM pull_requests")).one()
    assert tuple(after[:3]) == tuple(before)
    assert after.created_at is None


@pytest.mark.parametrize("missing", [True, False])
def test_reconciliation_backfills_creation_dates_for_paused_repositories(seed, missing):
    with transaction() as db:
        db.get(Repository, 22).enabled = False
        db.get(PullRequest, "pr").created_at = None if missing else now()
    jobs.schedule_reconciliation()
    with transaction() as db:
        queued = list(db.scalars(select(Job).where(Job.kind == "sync_pr")))
        assert [job.payload for job in queued] == (
            [{"repository_id": 22, "number": 3, "automatic": False}] if missing else []
        )
