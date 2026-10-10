from datetime import timedelta

import pytest
from fastapi import HTTPException
from test_inbox import pull, run

from code_mortis import github
from code_mortis.db import now, transaction
from code_mortis.models import Finding, ReviewRun


def test_repository_pages_filter_and_sort_on_server(seed, github_stub, client):
    with transaction() as db:
        for state in ["open", "merged", "closed"]:
            for index in range(12):
                pr = pull(db, f"{state}-{index:02}", state, author="reviewer", at=now() - timedelta(days=index))
                pr.head_ref = f"branch-{state}-{index}"
                run(db, pr, "completed")
    path = "/api/v1/repositories/22/pulls"
    first = client.get(path).json()
    assert first["page_size"] == 10 and first["total"] == 37
    assert first["state_counts"] == {"open": 13, "merged": 12, "closed": 12}
    assert all(len(group["items"]) == 10 and group["has_more"] for group in first["groups"])
    for group in first["groups"]:
        second = client.get(path, params={"group": group["group"], "page": 2}).json()["groups"]
        assert len(second) == 1 and not second[0]["has_more"]
        assert not {row["id"] for row in group["items"]} & {row["id"] for row in second[0]["items"]}
        assert len(group["items"]) + len(second[0]["items"]) == group["total"]
    filtered = client.get(path, params={"q": "BRANCH-MERGED-11", "author": "reviewer", "review": "completed"}).json()
    assert filtered["total"] == 1
    assert filtered["groups"][1]["items"][0]["id"] == "merged-11"
    assert filtered["groups"][1]["items"][0]["latest_run"]["status"] == "completed"
    ordered = client.get(path, params={"state": "merged", "ordering": "title"}).json()["groups"][0]
    assert [item["id"] for item in ordered["items"]] == [f"merged-{index:02}" for index in range(10)]
    assert client.get(path, params={"repo": 999}).json()["total"] == 0
    assert client.get(path, params={"group": "merged", "page": 1000000}).json()["groups"][0]["page"] == 2


def test_finding_pages_counts_filters_and_stable_order(seed, github_stub, client):
    at = now()
    with transaction() as db:
        db.add(ReviewRun(id="run", pr_id="pr", trigger="manual", dedup_key="run", config={}, head_sha="a" * 40, base_sha="b" * 40))
        db.flush()
        for status in ["open", "resolved", "dismissed"]:
            for index in range(23):
                db.add(Finding(
                    id=f"{status}-{index:02}", pr_id="pr", fingerprint=f"{status}-{index}", status=status,
                    path=f"src/{status}/handler-{index}.ts", content="Literal %_ match" if index == 0 else f"Finding {index}",
                    category="performance" if index == 22 else "bug", severity="high" if index % 2 == 0 else "low",
                    start_line=1, end_line=1, last_seen_run_id="run", created_at=at,
                ))
        other = pull(db, "other")
        other_run = run(db, other, "completed", "other-run")
        db.add(Finding(pr_id=other.id, fingerprint="other", path="secret.py", content="Other PR", start_line=1, end_line=1, last_seen_run_id=other_run.id))
    path = "/api/v1/pulls/pr/findings"
    first = client.get(path).json()
    assert first["page_size"] == 10 and first["total"] == first["registered_total"] == 69
    assert first["status_counts"] == {"open": 23, "resolved": 23, "dismissed": 23}
    for group in first["groups"]:
        assert len(group["items"]) == 10 and group["has_more"]
        ids = [row["id"] for row in group["items"]]
        for page in [2, 3]:
            result = client.get(path, params={"group": group["group"], "page": page}).json()["groups"]
            assert len(result) == 1 and result[0]["page"] == page
            assert len(result[0]["items"]) == (10 if page == 2 else 3)
            ids += [row["id"] for row in result[0]["items"]]
        assert ids == [f'{group["group"]}-{index:02}' for index in reversed(range(23))]
    result = client.get(path, params=[("status", "open"), ("status", "resolved"), ("severity", "high"), ("q", "PERFORMANCE")]).json()
    assert result["total"] == 2 and result["registered_total"] == 69
    assert {item["id"] for group in result["groups"] for item in group["items"]} == {"open-22", "resolved-22"}
    assert client.get(path, params={"q": "HANDLER-1.TS"}).json()["total"] == 3
    assert client.get(path, params={"q": "%_"}).json()["total"] == 3
    assert client.get(path, params={"q": "no-match"}).json()["total"] == 0
    assert client.get(path, params={"group": "open", "page": 1000000}).json()["groups"][0]["page"] == 3
    assert "findings" not in client.get("/api/v1/pulls/pr").json()


@pytest.mark.parametrize("path,params", [
    ("/repositories/22/pulls", {"page": 0}), ("/repositories/22/pulls", {"group": "invalid"}),
    ("/pulls/pr/findings", {"page": 0}), ("/pulls/pr/findings", {"page": 1000001}),
    ("/pulls/pr/findings", {"status": "fixed"}), ("/pulls/pr/findings", {"severity": "urgent"}),
    ("/pulls/pr/findings", {"q": "x" * 257}), ("/pulls/pr/findings", {"unknown": "ignored"}),
])
def test_list_filters_are_validated(seed, github_stub, client, path, params):
    assert client.get("/api/v1" + path, params=params).status_code == 422


@pytest.mark.parametrize("path", ["/repositories/22/pulls", "/pulls/pr/findings"])
def test_page_requests_revalidate_github_access(seed, client, monkeypatch, path):
    def denied(*args, **kwargs):
        raise HTTPException(403, "Repository access revoked")
    monkeypatch.setattr(github, "authorize_repo", denied)
    response = client.get("/api/v1" + path, params={"page": 2})
    assert response.status_code == 403 and response.json() == {"detail": "Repository access revoked"}
