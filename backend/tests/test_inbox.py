from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from code_mortis import github
from code_mortis.db import engine, now, transaction
from code_mortis.models import Finding, Installation, PullRequest, Repository, ReviewRun


@pytest.fixture
def visible(seed, monkeypatch):
    monkeypatch.setattr(github, "pages", lambda *args: [{"id": 22}])


def pull(db, identity, state="open", repo=22, author="owner", at=None):
    pr = PullRequest(
        id=identity, repository_id=repo, number=100 + db.scalar(select(func.count()).select_from(PullRequest)), title=f"Change {identity}",
        author=author, url=f"https://github.com/team/project/pull/{identity}", state=state,
        head_sha="a" * 40, base_sha="b" * 40, head_ref="feature", updated_at=at or now(),
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
    assert list(groups) == ["active", "reviewed", "merged"]
    active = groups["active"]["items"]
    assert [item["review_status"] for item in active[:3]] == ["running", "running", "queued"]
    assert {item["id"] for item in active if item["review_status"] == "outdated"} == {"head_sha", "base_sha"}
    assert next(item for item in active if item["id"] == "pr")["review_status"] == "not_reviewed"
    completed = groups["reviewed"]["items"][0]
    assert completed["open_findings"] == 1
    assert completed["publication"] == "failed"
    assert "report" not in completed and "config" not in completed
    assert result["state_counts"]["closed"] == 1
    assert get(client, "?state=closed")["groups"][0]["items"][0]["id"] == "closed"
    assert len(get(client, "?state=all")["groups"]) == 4


def test_permissions_cover_rows_counts_options_and_paused_repositories(visible, seed, client, monkeypatch):
    with transaction() as db:
        db.get(Repository, 22).enabled = False
        db.add(Installation(id=11, account="inactive", active=False))
        db.flush()
        for identity, installation, active in [(23, 10, True), (24, 11, True), (25, 10, False)]:
            db.add(Repository(id=identity, installation_id=installation, full_name=f"secret/{identity}", active=active))
        db.flush()
        for identity in [23, 24, 25]:
            pull(db, f"private-{identity}", repo=identity, author="hidden")
    monkeypatch.setattr(github, "pages", lambda *args: [{"id": n} for n in [22, 24, 25]])
    result = get(client)
    assert result["total"] == result["registered_total"] == 1
    assert result["authors"] == ["owner"]
    assert result["repositories"] == [{"id": 22, "full_name": "team/project"}]
    assert get(client, "?repo=23")["total"] == 0
    assert get(client, "?author=hidden")["total"] == 0
    monkeypatch.setattr(github, "pages", lambda *args: [])
    denied = get(client)
    assert denied["total"] == 0 and denied["authors"] == [] and denied["repositories"] == []
    def unavailable(*args):
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
        first = get(client)
    finally:
        event.remove(engine(), "before_cursor_execute", record)
    reviewed = next(g for g in first["groups"] if g["group"] == "reviewed")
    assert reviewed["total"] == 42 and len(reviewed["items"]) == 20 and reviewed["has_more"]
    assert len(statements) < 20
    second = get(client, "?group=reviewed&page=2")["groups"][0]
    assert len(second["items"]) == 20
    assert not ({p["id"] for p in second["items"]} & {p["id"] for p in reviewed["items"]})
    last = get(client, "?group=reviewed&page=999")["groups"][0]
    assert last["page"] == 3 and len(last["items"]) == 2 and not last["has_more"]
    filtered = get(client, "?author=alice&review=completed&review=not_reviewed&findings=none&state=open")
    assert filtered["total"] == 21 and filtered["state_counts"]["closed"] == 1
    assert get(client, "?author=alice&author=bob&repo=22&repo=999&review=completed")["total"] == 42
    assert get(client, "?q=TEAM/PROJECT")["total"] == 43
    assert get(client, "?q=%233")["total"] == 1
    assert get(client, "?q=Change%20item-01")["total"] == 1
    assert get(client, "?q=%25")["total"] == 0
    assert get(client, "?findings=open")["total"] == 0


@pytest.mark.parametrize("query", ["?state=bad", "?repo=-1", "?review=clean", "?page=0", "?group=bad", "?findings=bad"])
def test_query_validation(visible, client, query):
    assert client.get("/api/v1/pulls" + query).status_code == 422
