from datetime import UTC, datetime, timedelta

from test_analytics import AT, finding, run
from test_inbox import pull
from test_security_analytics import finding as security_finding
from test_security_analytics import scan

from code_mortis.api.routers import repositories
from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import (
    FindingVerification,
    Occurrence,
    Repository,
    SecurityActivity,
    SecurityOccurrence,
)

URL = "/api/v1/repositories/22/activity"


def test_repository_activity_returns_daily_run_counts_in_the_requested_timezone(client, seed, github_stub, monkeypatch):
    end = datetime(2026, 11, 21, 2, tzinfo=UTC)
    first = datetime(2025, 11, 21, 5, tzinfo=UTC)
    midnight = datetime(2026, 11, 1, 4, tzinfo=UTC)
    monkeypatch.setattr(repositories, "now", lambda: end)
    with transaction() as db:
        db.add(Repository(id=23, installation_id=10, full_name="private/other"))
        db.flush()
        other = pull(db, "other", repo=23)
        run(db, "other-review", other.id, created_at=midnight)
        scan(db, "other-scan", repository_id=23, created_at=midnight)
        run(db, "first", created_at=first)
        run(db, "old", created_at=first - timedelta(seconds=1))
        scan(db, "previous-day", created_at=midnight - timedelta(seconds=1))
        run(db, "failed", status="failed", created_at=midnight)
        scan(db, "dst-day", status="partial", created_at=midnight)
        run(db, "today", status="running", created_at=end)
        scan(db, "future", created_at=end + timedelta(seconds=1))
    response = client.get(URL, params={"timezone": "America/New_York"})
    assert response.status_code == 200, response.text
    days = response.json()["days"]
    assert len(days) == 365
    assert days[0]["date"] == "2025-11-21"
    assert days[-1]["date"] == "2026-11-20"
    assert days[1] == {"date": "2025-11-22", "reviews": 0, "scans": 0}
    assert [(day["date"], day["reviews"], day["scans"]) for day in days if day["reviews"] or day["scans"]] == [
        ("2025-11-21", 1, 0),
        ("2026-10-31", 0, 1),
        ("2026-11-01", 1, 1),
        ("2026-11-20", 1, 0),
    ]


def test_repository_activity_returns_three_latest_saved_results(client, seed, github_stub, monkeypatch):
    monkeypatch.setattr(repositories, "now", lambda: AT + timedelta(minutes=10))
    with transaction() as db:
        pr = pull(db, "recent", at=AT)
        number = pr.number
        run(db, "review", pr.id, finished_at=AT + timedelta(minutes=5))
        run(db, "active", pr.id, status="running", finished_at=AT + timedelta(minutes=8))
        run(db, "missing-finish", pr.id, finished_at=None)
        run(db, "future", pr.id, finished_at=AT + timedelta(minutes=11))
        scan(db, "partial", status="partial", branch="release", finished_at=AT + timedelta(minutes=4))
        scan(db, "older", finished_at=AT)
        scan(db, "cancelled", status="cancelled", finished_at=AT + timedelta(minutes=7))
        for outcome, minute in [("fixed", 3), ("not_observed", 6)]:
            security_finding(db, outcome, first_scan_id="partial", last_scan_id="partial", title="Current title")
            db.add(SecurityOccurrence(scan_id="partial", finding_id=outcome, outcome=outcome,
                                      snapshot={"title": "Saved finding title"}))
            db.add(SecurityActivity(scan_id="partial", finding_id=outcome, action="finding_resolved",
                                    created_at=AT + timedelta(minutes=minute)))
        db.add(Repository(id=23, installation_id=10, full_name="private/other"))
        db.flush()
        other = pull(db, "other", repo=23, at=AT)
        run(db, "other-review", other.id, finished_at=AT + timedelta(minutes=9))
        scan(db, "other-scan", repository_id=23, finished_at=AT + timedelta(minutes=9))

    response = client.get(URL)
    assert response.status_code == 200, response.text
    rows = response.json()["recent_results"]
    assert [(row["kind"], row["run_id"], row["title"]) for row in rows] == [
        ("review", "review", "Change recent"),
        ("scan", "partial", "release"),
        ("scan_fix", "partial", "Saved finding title"),
    ]
    assert rows[0]["pr_number"] == number
    assert rows[1]["scan_status"] == "partial"
    assert rows[2]["finding_id"] == "fixed"
    assert [datetime.fromisoformat(row["at"]) for row in rows] == [AT + timedelta(minutes=minute) for minute in (5, 4, 3)]


def test_repository_activity_uses_only_saved_verified_review_resolutions(client, seed, github_stub, monkeypatch):
    monkeypatch.setattr(repositories, "now", lambda: AT + timedelta(minutes=10))
    with transaction() as db:
        run(db)
        for identity, verdict, resolution in [("verified", "fixed", "resolved"), ("pending", "fixed", "pending"),
                                              ("uncertain", "uncertain", "resolved")]:
            finding(db, identity, status="open")
            occurrence = Occurrence(id=f"occurrence-{identity}", run_id="run", finding_id=identity, snapshot={})
            db.add(occurrence)
            db.flush()
            db.add(FindingVerification(run_id="run", finding_id=identity, occurrence_id=occurrence.id,
                                       head_sha="a" * 40, finding_version=0, verdict=verdict, resolution=resolution,
                                       status="completed", finished_at=AT + timedelta(minutes=2)))
        finding(db, "manual-resolution", status="resolved")

    response = client.get(URL)
    assert response.status_code == 200, response.text
    rows = response.json()["recent_results"]
    assert [(row["kind"], row["run_id"], row["finding_id"]) for row in rows] == [
        ("review_fix", "run", "verified"), ("review", "run", None),
    ]


def test_repository_activity_rechecks_access_and_rejects_invalid_timezone(client, seed, github_stub, monkeypatch):
    assert client.get(URL, params={"timezone": "../UTC"}).status_code == 422
    assert client.get("/api/v1/repositories/999/activity").status_code == 404

    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: {"id": 23})
    assert client.get(URL).status_code == 403

    def deny(*args, **kwargs):
        raise github.GitHubError(403, "Repository access denied.")

    monkeypatch.setattr(github, "authorize_repo", deny)
    assert client.get(URL).status_code == 403
