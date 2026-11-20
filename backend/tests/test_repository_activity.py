from datetime import UTC, datetime, timedelta

from test_analytics import run
from test_inbox import pull
from test_security_analytics import scan

from code_mortis.api.routers import repositories
from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import Repository

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


def test_repository_activity_rechecks_access_and_rejects_invalid_timezone(client, seed, github_stub, monkeypatch):
    assert client.get(URL, params={"timezone": "../UTC"}).status_code == 422
    assert client.get("/api/v1/repositories/999/activity").status_code == 404

    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: {"id": 23})
    assert client.get(URL).status_code == 403

    def deny(*args, **kwargs):
        raise github.GitHubError(403, "Repository access denied.")

    monkeypatch.setattr(github, "authorize_repo", deny)
    assert client.get(URL).status_code == 403
