from datetime import UTC, datetime, timedelta

import pytest

from code_mortis import github
from code_mortis.analytics import amount, recorded_usage
from code_mortis.db import transaction
from code_mortis.models import (
    Finding,
    FindingVerification,
    Job,
    Occurrence,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRun,
)

AT = datetime(2026, 10, 10, 12, tzinfo=UTC)
QUERY = "?from_at=2026-10-01T00:00:00Z&to_at=2026-11-01T00:00:00Z&timezone=UTC"


@pytest.fixture(autouse=True)
def permissions(seed, monkeypatch):
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [{"id": 22}])


def run(db, identity="run", pr="pr", **values):
    row = ReviewRun(**{
        "id": identity, "pr_id": pr, "dedup_key": identity, "trigger": "manual", "status": "completed",
        "head_sha": "a" * 40, "base_sha": "b" * 40, "created_at": AT,
        "started_at": AT, "finished_at": AT + timedelta(seconds=60), "stages": {},
        "config": {"pipeline_version": 2, "assessment_policy": "v1", "connection": {"model": "model-a"}, "settings": {"effort": "high"}},
        "report": {"summary": {"total_tokens": 100}, "manifest": {"coverage": {
            "selected": [{"item_id": "a", "path": "a.py"}, {"item_id": "b", "path": "b.py"}],
            "completed": [{"item_id": "a", "path": "a.py"}], "reused": [{"item_id": "b", "path": "b.py"}],
            "failed": [], "waived": [],
        }}}, "summary": {"status": "completed", "tokens": 17}, **values,
    })
    db.add(row)
    db.flush()
    return row


def finding(db, identity="finding", **values):
    row = Finding(**{
        "id": identity, "pr_id": "pr", "fingerprint": identity, "last_seen_run_id": "run",
        "path": "a.py", "content": "Example finding", "severity": "high", "category": "bug",
        "start_line": 1, "end_line": 1, "created_at": AT, **values,
    })
    db.add(row)
    db.flush()
    return row


def get(client, view="overview", query=QUERY):
    response = client.get("/api/v1/analytics/" + view + query)
    assert response.status_code == 200, response.text
    return response.json()


def test_distinct_prs_findings_and_current_resolution(client):
    with transaction() as db:
        run(db)
        run(db, "again", status="partial")
        run(db, "failure", status="failed")
        finding(db, status="resolved")
        finding(db, "pending", desired_status="resolved", category="custom category", severity="critical")
        finding(db, "dismissed", status="dismissed")
        finding(db, "old", status="resolved", created_at=AT - timedelta(days=40))
    result = get(client)
    assert (result["reviewed_prs"], result["findings"], result["resolved"]) == (1, 3, 1)
    assert sum(point["runs"] for point in result["trend"]) == 3
    assert sum(point["findings"] for point in result["trend"]) == 3
    assert result["repositories"][0]["reviewed_prs"] == 1
    with transaction() as db:
        db.get(Finding, "finding").status = "open"
    assert get(client)["resolved"] == 0


@pytest.mark.parametrize("state", ["open", "merged", "closed"])
def test_all_pr_states_and_ignored_history(client, state):
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        pr.state, pr.ignored = state, True
        db.get(Repository, 22).enabled = False
        run(db)
        finding(db)
    assert get(client)["findings"] == 1
    assert get(client, "reviews")["contributors"] == [{"key": "owner", "count": 1}]


@pytest.mark.parametrize("view", ["overview", "findings", "reviews", "usage"])
def test_repository_permissions_and_empty_scope(client, monkeypatch, view):
    with transaction() as db:
        run(db)
        finding(db)
    assert client.get("/api/v1/analytics/" + view + QUERY + "&repo=999").status_code == 404
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [])
    result = get(client, view)
    assert result["scope"]["repositories"] == []
    assert "Example finding" not in str(result)
    assert client.get("/api/v1/analytics/" + view + QUERY + "&repo=22").status_code == 404


def test_every_aggregate_excludes_other_repositories(client):
    with transaction() as db:
        run(db)
        finding(db)
        db.add(Repository(id=23, installation_id=10, full_name="private/secret"))
        db.flush()
        db.add(PullRequest(id="hidden", repository_id=23, number=4, title="Private", author="secret-author",
                           url="https://github.com/private/secret/pull/4", head_sha="c" * 40, base_sha="b" * 40))
        db.flush()
        run(db, "hidden-run", "hidden", config={"connection": {"model": "secret-model"}})
        finding(db, "hidden-finding", pr_id="hidden", last_seen_run_id="hidden-run", category="secret-category")
    for view in ["overview", "findings", "reviews", "usage"]:
        result = get(client, view)
        assert "secret" not in str(result)
        assert client.get("/api/v1/analytics/" + view + QUERY + "&repo=23").status_code == 404
    assert get(client)["findings"] == 1
    assert get(client, "usage")["total"]["tokens"] == 117


def test_finding_filters_apply_to_every_breakdown(client):
    with transaction() as db:
        run(db)
        finding(db, category="custom", severity="critical", status="resolved")
        finding(db, "other", category="performance")
    result = get(client, "findings", QUERY + "&category=custom&severity=critical&status=resolved")
    assert (result["total"], result["unfiltered_total"]) == (1, 2)
    assert result["category_options"] == ["custom", "performance"]
    assert result["categories"] == [{"key": "custom", "count": 1}]
    assert sum(row["count"] for row in result["severities"]) == 1
    assert sum(row["findings"] for row in result["trend"]) == 1


def test_timezone_dst_and_exclusive_end(client):
    start = datetime(2026, 3, 8, 5, tzinfo=UTC)
    end = datetime(2026, 3, 9, 4, tzinfo=UTC)
    with transaction() as db:
        run(db, created_at=start)
        finding(db, created_at=end - timedelta(seconds=1))
        finding(db, "at-end", created_at=end)
    result = get(client, query="?from_at=2026-03-08T05:00:00Z&to_at=2026-03-09T04:00:00Z&timezone=America/New_York")
    assert result["findings"] == 1
    assert [(point["date"], point["runs"], point["findings"]) for point in result["trend"]] == [("2026-03-08", 1, 1)]


@pytest.mark.parametrize("query", [
    "?timezone=../UTC", "?timezone=Nowhere", "?from_at=2026-10-01", "?repo=-1",
    "?from_at=2026-11-01T00:00:00Z&to_at=2026-10-01T00:00:00Z",
    "?all_time=true&from_at=2026-10-01T00:00:00Z",
])
def test_invalid_filters(client, query):
    assert client.get("/api/v1/analytics/overview" + query).status_code == 422


def test_all_time_and_adaptive_buckets(client):
    with transaction() as db:
        run(db, created_at=AT - timedelta(days=800))
    result = get(client, query="?all_time=true&to_at=2026-11-01T00:00:00Z")
    assert result["scope"]["bucket"] == "month"
    assert sum(point["runs"] for point in result["trend"]) == 1
    result = get(client, query="?from_at=2026-01-01T00:00:00Z&to_at=2026-11-01T00:00:00Z")
    assert result["scope"]["bucket"] == "week"


def test_usage_retries_models_and_no_stage_double_count(client):
    with transaction() as db:
        review = run(db, stages={"ocr": {"status": "completed", "tokens": 999999}})
        db.add(ReviewCandidate(
            id="candidate", run_id=review.id, position=0, snapshot={}, status="completed", usage={"tokens": 20},
            result={"verdict": "rejected"}, attempts=[{"status": "failed", "usage": {"tokens": 3}}],
        ))
        db.add(Job(id="retry", kind="generate_summary", available_at=AT, payload={
            "run_id": "run", "previous_summary": {"status": "failed", "tokens": 5},
            "config": {"connection": {"model": "model-b"}, "settings": {"effort": "low"}},
        }))
    result = get(client, "usage")
    assert result["total"]["tokens"] == 145
    assert result["total"]["mean_per_run"] == 145
    assert result["total"]["mean_per_reviewed_pr"] == 145
    models = {row["key"]: row["usage"]["tokens"] for row in result["models"]}
    assert models == {"model-a": 128, "model-b": 17}
    assert sum(point["tokens"] or 0 for point in result["trend"]) == 145
    assert get(client)["usage"]["tokens"] == 145


def test_partial_usage_excludes_incomplete_samples_and_reservations(client):
    with transaction() as db:
        run(db)
        run(db, "incomplete", report={"summary": {"total_tokens": None}}, summary={})
        db.add(ReviewCandidate(run_id="incomplete", position=0, snapshot={}, status="failed", usage={"reserved_tokens": 2000}))
    total = get(client, "usage")["total"]
    assert (total["tokens"], total["missing_reports"], total["complete_runs"]) == (117, 2, 1)
    assert total["mean_per_run"] == 117
    assert total["reviewed_pr_samples"] == 0
    assert total["mean_per_reviewed_pr"] is None


@pytest.mark.parametrize("timestamps", [True, False])
def test_summary_retry_models_ignore_mutable_dispatch_times(client, timestamps):
    with transaction() as db:
        run(db, progress={"summary_retry": "latest"})
        for identity, days, tokens, model, available in [
            ("first", 0, 5, "model-b", AT + timedelta(days=10)),
            ("latest", 1, 7, "model-c", AT + timedelta(days=1)),
        ]:
            previous = {"tokens": tokens}
            if timestamps:
                previous["inference_started_at"] = (AT + timedelta(days=days)).isoformat()
            db.add(Job(id=identity, kind="generate_summary", available_at=available, payload={
                "run_id": "run", "previous_summary": previous,
                "config": {"connection": {"model": model}, "settings": {"effort": "low"}},
            }))
    result = get(client, "usage")
    assert result["total"]["tokens"] == 129
    models = {row["key"]: row["usage"]["tokens"] for row in result["models"]}
    assert models == ({"model-a": 105, "model-b": 7, "model-c": 17} if timestamps
                      else {"model-a": 100, "Not recorded": 12, "model-c": 17})


def test_verification_consumption_and_zero_are_counted_once(client):
    with transaction() as db:
        run(db, report={"summary": {"total_tokens": 0}}, summary={"status": "skipped", "tokens": 0},
            stages={"verification": {"tokens": 9999}})
        finding(db)
        db.add(Occurrence(id="occurrence", run_id="run", finding_id="finding", snapshot={}))
        db.flush()
        db.add(FindingVerification(finding_id="finding", run_id="run", occurrence_id="occurrence",
                                   head_sha="a" * 40, finding_version=1, status="completed", verdict="fixed",
                                   usage={"tokens": 12, "reserved_tokens": 3000, "context_bytes": 999}))
        db.add(ReviewCandidate(run_id="run", position=0, snapshot={}, status="completed", usage={}))
    result = get(client, "usage")
    assert result["total"]["tokens"] == 12
    assert result["total"]["missing_reports"] == 0
    assert result["total"]["complete_runs"] == 1
    assert {row["key"]: row["usage"]["tokens"] for row in result["stages"]} == {
        "review": 0, "assessment": 0, "verification": 12, "summary": 0,
    }


@pytest.mark.parametrize("value", [None, True, -1, "10", 1.5])
def test_invalid_token_values_are_unknown(value):
    assert amount(value) is None


def test_zero_and_legacy_usage():
    assert amount(0) == 0
    assert recorded_usage({}, "failed", False, True) == 0
    assert recorded_usage({"tokens": 200000}, "failed", False, True) is None
    assert recorded_usage({"tokens": 20}, "completed", True, True) == 20
    assert recorded_usage({"context_bytes": 500}, "completed", True, False) is None


def test_review_coverage_assessments_and_duration(client):
    with transaction() as db:
        run(db)
        run(db, "legacy", config={}, report={}, finished_at=AT + timedelta(seconds=120))
        for index, (status, result, linked, new) in enumerate([
            ("completed", {}, True, True), ("completed", {}, True, False),
            ("completed", {"verdict": "rejected"}, False, False),
            ("failed", None, False, False), ("queued", None, False, False),
        ]):
            if index == 0:
                finding(db)
            db.add(ReviewCandidate(run_id="run", position=index, snapshot={}, status=status, result=result,
                                   finding_id="finding" if linked else None, is_new=new))
    result = get(client, "reviews")
    assert result["median_duration_seconds"] == 90
    assert result["duration_samples"] == 2
    assert dict((row["key"], row["count"]) for row in result["coverage"]) == {
        "selected": 2, "completed": 1, "reused": 1, "failed": 0, "waived": 0,
    }
    assert result["coverage_missing_runs"] == 1
    assert result["assessment_missing_runs"] == 1
    assert [row["count"] for row in result["assessments"]] == [1] * 5
