from datetime import timedelta

import pytest
from fastapi import HTTPException
from test_inbox import pull, run

from code_mortis import github
from code_mortis.db import now, transaction
from code_mortis.models import (
    Finding,
    FindingVerification,
    Occurrence,
    PullRequest,
    ReviewCandidate,
    ReviewRun,
)


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
                    category="performance" if index == 22 else "custom_rule" if index == 21 else "bug", severity="high" if index % 2 == 0 else "low",
                    start_line=1, end_line=1, last_seen_run_id="run", created_at=at,
                ))
        other = pull(db, "other")
        other_run = run(db, other, "completed", "other-run")
        db.add(Finding(pr_id=other.id, fingerprint="other", path="secret.py", content="Other PR", start_line=1, end_line=1, last_seen_run_id=other_run.id))
    path = "/api/v1/pulls/pr/findings"
    first = client.get(path).json()
    assert first["page_size"] == 10 and first["total"] == first["registered_total"] == 69
    assert first["status_counts"] == {"open": 23, "resolved": 23, "dismissed": 23}
    assert first["category_counts"] == {"bug": 63, "custom_rule": 3, "performance": 3}
    open_page = client.get(path, params={"status": "open"}).json()
    assert open_page["page_size"] == 10 and open_page["total"] == 23
    assert [group["group"] for group in open_page["groups"]] == ["open"]
    assert len(open_page["groups"][0]["items"]) == 10
    open_next = client.get(path, params={"status": "open", "group": "open", "page": 2}).json()["groups"][0]
    assert open_next["page"] == 2 and len(open_next["items"]) == 10 and open_next["has_more"]
    assert {item["id"] for item in open_page["groups"][0]["items"]}.isdisjoint(item["id"] for item in open_next["items"])
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
    typed = client.get(path, params={"category": "custom_rule", "group": "resolved", "page": 2}).json()
    assert typed["total"] == 3 and typed["registered_total"] == 69
    assert typed["category_counts"] == first["category_counts"]
    assert typed["groups"][0]["page"] == 1 and not typed["groups"][0]["has_more"]
    assert [item["id"] for item in typed["groups"][0]["items"]] == ["resolved-21"]
    combined = client.get(path, params=[("category", "custom_rule"), ("category", "performance"), ("status", "open"), ("severity", "high")]).json()
    assert combined["total"] == 1 and combined["groups"][0]["items"][0]["id"] == "open-22"
    assert client.get(path, params={"category": "absent"}).json()["total"] == 0
    assert client.get(path, params={"q": "HANDLER-1.TS"}).json()["total"] == 3
    assert client.get(path, params={"q": "%_"}).json()["total"] == 3
    assert client.get(path, params={"q": "no-match"}).json()["total"] == 0
    assert client.get(path, params={"group": "open", "page": 1000000}).json()["groups"][0]["page"] == 3
    assert "findings" not in client.get("/api/v1/pulls/pr").json()


def test_run_pages_and_summary_include_history_beyond_first_page(seed, github_stub, client):
    at = now()
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        for index in range(23):
            status = "queued" if index == 0 else "completed" if index == 1 else "failed"
            run(db, pr, status, f"run-{index:02}", created_at=at)
        run(db, pull(db, "other"), "running", "other-run")
    detail = client.get("/api/v1/pulls/pr").json()
    assert "runs" not in detail
    assert detail["run_count"] == 23
    assert detail["has_active_run"] is True
    assert detail["latest_observed_run_id"] == "run-01"
    assert detail["pull_request"]["latest_run"]["id"] == "run-22"
    ids = []
    path = "/api/v1/pulls/pr/runs"
    for page in [1, 2, 3]:
        response = client.get(path, params={"page": page})
        assert response.status_code == 200
        result = response.json()
        assert result["page"] == page and result["total"] == 23 and result["page_size"] == 10
        assert len(result["items"]) == (3 if page == 3 else 10)
        assert result["has_more"] is (page < 3)
        ids += [item["id"] for item in result["items"]]
    assert ids == [f"run-{index:02}" for index in reversed(range(23))]
    assert client.get(path, params={"page": 1000000}).json()["page"] == 3
    with transaction() as db:
        db.get(ReviewRun, "run-00").status = "cancelled"
    assert client.get("/api/v1/pulls/pr").json()["has_active_run"] is False


def test_empty_run_history(seed, github_stub, client):
    assert client.get("/api/v1/pulls/pr/runs?page=2").json() == {
        "items": [], "total": 0, "page": 1, "has_more": False, "page_size": 10,
    }
    detail = client.get("/api/v1/pulls/pr").json()
    assert detail["run_count"] == 0 and detail["has_active_run"] is False
    assert detail["latest_observed_run_id"] is None and detail["pull_request"]["latest_run"] is None
    assert client.get("/api/v1/pulls/missing/runs").status_code == 404


def test_resolved_pages_filter_confirmed_run_history_on_server(seed, github_stub, client):
    at = now()
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        original = run(db, pr, "completed", "original")
        current = run(db, pr, "completed", "run")
        other = run(db, pr, "completed", "other-run")
        foreign = pull(db, "foreign")
        examples = [(f"finding-{index:02}", pr.id, current.id, "resolved") for index in range(25)]
        examples += [("pending", pr.id, current.id, "pending"), ("failed", pr.id, current.id, "failed"),
                     ("manual", pr.id, None, None), ("other", pr.id, other.id, "resolved"),
                     ("foreign", foreign.id, current.id, "resolved")]
        for index, (identity, pr_id, run_id, resolution) in enumerate(examples):
            finding = Finding(
                id=identity, pr_id=pr_id, fingerprint=identity, status="open" if index == 24 else "resolved",
                path=f"src/handler-{index}.py", content="Literal %_ match" if index == 24 else f"Finding {index}",
                category="performance" if index == 24 else "custom_rule" if index == 23 else "bug",
                severity="high" if index % 2 == 0 else "low", start_line=1, end_line=1,
                last_seen_run_id=original.id, created_at=at,
            )
            db.add(finding)
            db.flush()
            if run_id:
                db.add(Occurrence(id=identity, finding_id=identity, run_id=original.id, snapshot={}))
                db.flush()
                db.add(FindingVerification(
                    finding_id=identity, run_id=run_id, occurrence_id=identity, head_sha=current.head_sha,
                    finding_version=0, status="completed", verdict="fixed", resolution=resolution,
                ))
    path = "/api/v1/runs/run/resolved"
    first = client.get(path).json()
    assert first["total"] == first["registered_total"] == 25 and first["page_size"] == 10
    assert first["severity_counts"] == {"critical": 0, "high": 13, "medium": 0, "low": 12}
    assert first["category_counts"] == {"bug": 23, "custom_rule": 1, "performance": 1}
    ids = []
    for page in [1, 2, 3]:
        result = client.get(path, params={"page": page}).json()
        assert result["page"] == page and result["has_more"] is (page < 3)
        assert len(result["items"]) == (5 if page == 3 else 10)
        ids += [item["id"] for item in result["items"]]
    assert ids == [f"finding-{index:02}" for index in reversed(range(25))]
    assert first["items"][0]["status"] == "open"
    assert client.get(path, params={"page": 1000000}).json()["page"] == 3
    filtered = client.get(path, params=[("category", "custom_rule"), ("category", "performance"),
                                       ("severity", "high"), ("q", "PERFORMANCE"), ("page", 3)]).json()
    assert filtered["total"] == 1 and filtered["registered_total"] == 25
    assert filtered["items"][0]["id"] == "finding-24" and filtered["page"] == 1
    assert filtered["severity_counts"] == first["severity_counts"]
    assert filtered["category_counts"] == first["category_counts"]
    assert client.get(path, params={"q": "%_"}).json()["total"] == 1
    assert client.get(path, params={"q": "HANDLER-23.PY"}).json()["items"][0]["id"] == "finding-23"
    assert client.get(path, params={"q": "no-match"}).json()["total"] == 0
    detail = client.get("/api/v1/runs/run").json()
    assert detail["resolved_count"] == 25 and "resolved" not in detail
    assert client.get("/api/v1/runs/original/resolved").json()["registered_total"] == 0
    assert client.get("/api/v1/runs/missing/resolved").status_code == 404


def test_resolved_pages_revalidate_github_access(seed, client, monkeypatch):
    with transaction() as db:
        run(db, db.get(PullRequest, "pr"), "completed", "run")

    def denied(*args, **kwargs):
        raise HTTPException(403, "Repository access revoked")

    monkeypatch.setattr(github, "authorize_repo", denied)
    response = client.get("/api/v1/runs/run/resolved?page=2")
    assert response.status_code == 403 and response.json() == {"detail": "Repository access revoked"}


def test_candidate_pages_filter_full_run_and_preserve_evidence(seed, github_stub, client):
    decision = {
        "verdict": "uncertain", "relationship": "new", "finding_id": None,
        "explanation": "Full saved explanation", "cause": "", "trigger": "", "consequence": "",
        "history_explanation": "Full saved history", "contradicts_prior": False,
        "changed_conditions": "", "suggestion_supported": False,
        "evidence": [{"revision": "current", "path": "main.py", "start_line": 1, "end_line": 1, "quote": "source = 1"}],
    }
    with transaction() as db:
        current = run(db, db.get(PullRequest, "pr"), "completed", "run")
        current.config = {"assessment_policy": 1}
        db.add(Finding(id="finding", pr_id="pr", fingerprint="f", path="main.py", content="Saved finding",
                       start_line=1, end_line=1, last_seen_run_id="run"))
        db.flush()
        for index in range(25):
            db.add(ReviewCandidate(
                id=f"candidate-{index:02}", run_id="run", position=index,
                snapshot={"path": f"src/handler-{index}.py", "content": "Literal %_ match" if index == 24 else f"Proposal {index}",
                          "category": "performance" if index == 24 else "custom_rule" if index == 23 else "bug", "severity": "high" if index % 2 == 0 else "low"},
                status="running" if index == 3 else "failed" if index == 4 else "completed",
                result=None if index in {3, 4} else {**decision, "verdict": "confirmed" if index < 2 else "rejected" if index == 2 else "uncertain"},
                finding_id="finding" if index < 2 else None, is_new=index == 0,
                error="Exact failure" if index == 4 else None,
            ))
        other_run = run(db, pull(db, "other"), "completed", "other-run")
        db.add(ReviewCandidate(id="other-candidate", run_id=other_run.id, position=0, snapshot={"path": "secret.py"}))
    path = "/api/v1/runs/run/candidates"
    first = client.get(path).json()
    assert first["total"] == first["registered_total"] == 25 and first["page_size"] == 10
    assert first["outcome_counts"] == {"new": 1, "repeated": 1, "rejected": 1, "withheld": 21, "pending": 1}
    assert first["severity_counts"] == {"critical": 0, "high": 13, "medium": 0, "low": 12}
    assert first["category_counts"] == {"bug": 23, "custom_rule": 1, "performance": 1}
    ids = []
    for page in [1, 2, 3]:
        result = client.get(path, params={"page": page}).json()
        assert result["page"] == page and result["has_more"] is (page < 3)
        assert len(result["items"]) == (5 if page == 3 else 10)
        ids += [item["id"] for item in result["items"]]
    assert ids == [f"candidate-{index:02}" for index in range(25)]
    assert client.get(path, params={"page": 1000000}).json()["page"] == 3
    filtered = client.get(path, params=[("outcome", "withheld"), ("outcome", "rejected"), ("severity", "high"), ("q", "PERFORMANCE")]).json()
    assert filtered["total"] == 1 and filtered["registered_total"] == 25
    assert filtered["items"][0]["id"] == "candidate-24"
    assert filtered["items"][0]["result"] == decision
    typed = client.get(path, params={"category": "custom_rule", "page": 3}).json()
    assert typed["total"] == 1 and typed["registered_total"] == 25
    assert typed["category_counts"] == first["category_counts"]
    assert typed["page"] == 1 and not typed["has_more"] and typed["items"][0]["id"] == "candidate-23"
    combined = client.get(path, params=[("category", "custom_rule"), ("category", "performance"), ("outcome", "withheld"), ("severity", "high")]).json()
    assert combined["total"] == 1 and combined["items"][0]["id"] == "candidate-24"
    assert client.get(path, params={"category": "absent"}).json()["total"] == 0
    assert client.get(path, params={"q": "%_"}).json()["total"] == 1
    assert client.get(path, params={"q": "HANDLER-23.PY"}).json()["items"][0]["id"] == "candidate-23"
    assert client.get(path, params={"q": "no-match"}).json()["total"] == 0
    assert client.get(path, params={"outcome": "pending"}).json()["items"][0]["id"] == "candidate-03"
    assert "candidates" not in client.get("/api/v1/runs/run").json()
    assert client.get(path + "/candidate-24").json()["result"] == decision
    assert client.get(path + "/candidate-04").json()["error"] == "Exact failure"
    assert client.get(path + "/other-candidate").status_code == 404
    assert client.get("/api/v1/runs/missing/candidates").status_code == 404


@pytest.mark.parametrize("suffix", ["?page=2", "/candidate"])
def test_candidate_requests_revalidate_github_access(seed, client, monkeypatch, suffix):
    with transaction() as db:
        run(db, db.get(PullRequest, "pr"), "completed", "run")
        db.add(ReviewCandidate(id="candidate", run_id="run", position=0, snapshot={}))
    def denied(*args, **kwargs):
        raise HTTPException(403, "Repository access revoked")
    monkeypatch.setattr(github, "authorize_repo", denied)
    response = client.get("/api/v1/runs/run/candidates" + suffix)
    assert response.status_code == 403 and response.json() == {"detail": "Repository access revoked"}


@pytest.mark.parametrize("path,params", [
    ("/pulls/pr/runs", {"page": 0}), ("/pulls/pr/runs", {"page": 1000001}),
    ("/repositories/22/pulls", {"page": 0}), ("/repositories/22/pulls", {"group": "invalid"}),
    ("/pulls/pr/findings", {"page": 0}), ("/pulls/pr/findings", {"page": 1000001}),
    ("/pulls/pr/findings", {"status": "fixed"}), ("/pulls/pr/findings", {"severity": "urgent"}),
    ("/pulls/pr/findings", {"q": "x" * 257}), ("/pulls/pr/findings", {"unknown": "ignored"}),
    ("/pulls/pr/findings", {"category": ""}), ("/pulls/pr/findings", {"category": "x" * 51}),
    ("/pulls/pr/findings", {"category": ["bug"] * 101}),
    ("/runs/run/candidates", {"page": 0}), ("/runs/run/candidates", {"page": 1000001}),
    ("/runs/run/candidates", {"outcome": "fixed"}), ("/runs/run/candidates", {"severity": "urgent"}),
    ("/runs/run/candidates", {"q": "x" * 257}), ("/runs/run/candidates", {"unknown": "ignored"}),
    ("/runs/run/candidates", {"category": ""}), ("/runs/run/candidates", {"category": "x" * 51}),
    ("/runs/run/candidates", {"category": ["bug"] * 101}),
    ("/runs/run/resolved", {"page": 0}), ("/runs/run/resolved", {"page": 1000001}),
    ("/runs/run/resolved", {"severity": "urgent"}), ("/runs/run/resolved", {"q": "x" * 257}),
    ("/runs/run/resolved", {"category": ""}), ("/runs/run/resolved", {"category": "x" * 51}),
    ("/runs/run/resolved", {"category": ["bug"] * 101}), ("/runs/run/resolved", {"unknown": "ignored"}),
])
def test_list_filters_are_validated(seed, github_stub, client, path, params):
    assert client.get("/api/v1" + path, params=params).status_code == 422


@pytest.mark.parametrize("path", ["/repositories/22/pulls", "/pulls/pr/findings", "/pulls/pr/runs"])
def test_page_requests_revalidate_github_access(seed, client, monkeypatch, path):
    def denied(*args, **kwargs):
        raise HTTPException(403, "Repository access revoked")
    monkeypatch.setattr(github, "authorize_repo", denied)
    response = client.get("/api/v1" + path, params={"page": 2})
    assert response.status_code == 403 and response.json() == {"detail": "Repository access revoked"}


def test_run_resource_revisions_track_changes_without_count_changes(seed, github_stub, client):
    from code_mortis import assessment

    with transaction() as db:
        current = run(db, db.get(PullRequest, "pr"), "running", "run")
        current.config = {"assessment_policy": 1}
        db.add(ReviewCandidate(id="candidate", run_id="run", position=0, snapshot={}, status="queued"))
        finding = Finding(id="finding", pr_id="pr", fingerprint="f", status="resolved", path="a.py",
                          content="Finding", start_line=1, end_line=1, last_seen_run_id="run")
        db.add(finding)
        db.flush()
        db.add(Occurrence(id="occurrence", finding_id="finding", run_id="run", snapshot={}))
        db.flush()
        db.add(FindingVerification(finding_id="finding", run_id="run", occurrence_id="occurrence",
                                  head_sha=current.head_sha, finding_version=0, resolution="resolved"))
        assessment.record_summary(db, current, advance_revision=True)
    before = client.get("/api/v1/runs/run").json()
    assert before["assessment_revision"] > 0
    assert client.get("/api/v1/runs/run/candidates").json()["revision"] == before["assessment_revision"]
    assert client.get("/api/v1/runs/run/candidates/candidate").json()["revision"] == before["assessment_revision"]
    assert client.get("/api/v1/runs/run/resolved").json()["revision"] == before["resolved_revision"]
    with transaction() as db:
        current = db.get(ReviewRun, "run")
        candidate = db.get(ReviewCandidate, "candidate")
        candidate.status, candidate.usage = "running", {"tokens": None, "response_received": True}
        assessment.record_summary(db, current, advance_revision=True)
        db.get(Finding, "finding").status = "open"
    after = client.get("/api/v1/runs/run").json()
    assert before["assessment"] == after["assessment"]
    assert before["resolved_count"] == after["resolved_count"] == 1
    assert after["assessment_revision"] > before["assessment_revision"]
    assert after["resolved_revision"] != before["resolved_revision"]
    detail = client.get("/api/v1/runs/run/candidates/candidate").json()
    assert detail["progress_phase"] == "validating" and detail["result"] is None
    assert client.get("/api/v1/runs/run/candidates").json()["items"][0]["progress_phase"] == "validating"
    with transaction() as db:
        assessment.withhold_pending(db, db.get(ReviewRun, "run"), "Cancelled")
    final = client.get("/api/v1/runs/run/candidates/candidate").json()
    assert final["status"] == "skipped" and final["progress_phase"] is None
    assert final["revision"] > detail["revision"]


@pytest.mark.parametrize("size", [10, 50, 100])
def test_run_lists_fetch_loaded_window_in_one_request(seed, github_stub, client, size):
    with transaction() as db:
        current = run(db, db.get(PullRequest, "pr"), "completed", "run")
        for index in range(101):
            identity = f"item-{index:03}"
            db.add(ReviewCandidate(id=identity, run_id="run", position=index, snapshot={}))
            db.add(Finding(id=identity, pr_id="pr", fingerprint=identity, status="resolved", path="a.py",
                           content="Finding", start_line=1, end_line=1, last_seen_run_id="run"))
            db.flush()
            db.add(Occurrence(id=identity, finding_id=identity, run_id="run", snapshot={}))
            db.flush()
            db.add(FindingVerification(finding_id=identity, run_id="run", occurrence_id=identity,
                                      head_sha=current.head_sha, finding_version=0, resolution="resolved"))
    for resource in ["candidates", "resolved"]:
        path = f"/api/v1/runs/run/{resource}"
        page = client.get(path, params={"page_size": size}).json()
        assert len(page["items"]) == page["page_size"] == size
        assert page["has_more"] and page["page"] == 1 and page["registered_total"] == 101
        next_page = client.get(path, params={"page_size": size, "page": 2}).json()
        assert not {item["id"] for item in page["items"]} & {item["id"] for item in next_page["items"]}
        for invalid in [0, 9, 101, 100000]:
            assert client.get(path, params={"page_size": invalid}).status_code == 422
