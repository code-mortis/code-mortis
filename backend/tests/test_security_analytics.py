from datetime import UTC, datetime, timedelta

import pytest
from test_analytics import AT, QUERY, get, run

from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import Repository, SecurityFinding, SecurityOccurrence, SecurityScan


@pytest.fixture(autouse=True)
def permissions(seed, monkeypatch):
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [{"id": 22}])


def scan(db, identity="scan", **values):
    row = SecurityScan(**{
        "id": identity, "repository_id": 22, "actor_id": 7, "request_key": identity,
        "branch": "main", "head_sha": "a" * 40, "config": {"private": "not-for-analytics"},
        "status": "completed", "created_at": AT, **values,
    })
    db.add(row)
    db.flush()
    return row


def finding(db, identity="security-finding", **values):
    row = SecurityFinding(**{
        "id": identity, "repository_id": 22, "fingerprint": identity, "state": "open",
        "kind": "code", "severity": "high", "title": "Private finding evidence", "path": "a.py",
        "first_scan_id": "scan", "last_scan_id": "scan", "evidence": {}, "created_at": AT, **values,
    })
    db.add(row)
    db.flush()
    return row


def test_unique_security_findings_and_current_states(client):
    with transaction() as db:
        scan(db)
        scan(db, "repeat", status="partial")
        scan(db, "failed", status="failed")
        issue = finding(db, last_scan_id="repeat")
        for identity in ("scan", "repeat"):
            db.add(SecurityOccurrence(scan_id=identity, finding_id=issue.id, outcome="present", snapshot={}))
        finding(db, "resolved", state="resolved", kind="dependency", severity="critical")
        finding(db, "false-positive", state="false_positive", kind="secret", severity="low")
        finding(db, "accepted", state="accepted_risk", kind="infrastructure", severity="medium")
        finding(db, "old", created_at=AT - timedelta(days=40))
        run(db)
    result = get(client, "security")
    assert (result["scans"], result["findings"], result["open"], result["resolved"]) == (3, 4, 1, 1)
    assert sum(point["runs"] for point in result["trend"]) == 3
    assert sum(point["findings"] for point in result["trend"]) == 4
    for key in ("severities", "kinds", "states"):
        assert sum(row["count"] for row in result[key]) == 4
    assert {row["key"]: row["count"] for row in result["outcomes"]} == {"completed": 1, "partial": 1, "failed": 1}
    assert "Private finding evidence" not in str(result)
    assert "not-for-analytics" not in str(result)
    with transaction() as db:
        db.get(SecurityFinding, "resolved").state = "open"
    result = get(client, "security")
    assert (result["open"], result["resolved"], result["findings"]) == (2, 0, 4)


def test_security_scope_excludes_inaccessible_and_unselected_history(client, monkeypatch):
    with transaction() as db:
        scan(db)
        finding(db)
        db.add(Repository(id=23, installation_id=10, full_name="private/hidden"))
        db.flush()
        scan(db, "hidden", repository_id=23, created_at=AT - timedelta(days=800))
        finding(db, "hidden", repository_id=23, first_scan_id="hidden", last_scan_id="hidden", kind="hidden-kind")
    result = get(client, "security", "?all_time=true&to_at=2026-11-01T00:00:00Z")
    assert (result["scans"], result["findings"]) == (1, 1)
    assert result["scope"]["from_at"].startswith("2026-10-10")
    assert "hidden" not in str(result)
    assert client.get("/api/v1/analytics/security" + QUERY + "&repo=23").status_code == 404
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [{"id": 22}, {"id": 23}])
    assert get(client, "security", QUERY + "&repo=22")["findings"] == 1
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [])
    result = get(client, "security")
    assert (result["scans"], result["findings"], result["scope"]["repositories"]) == (0, 0, [])


def test_security_all_time_uses_security_history_and_orders_breakdowns(client):
    old = AT - timedelta(days=800)
    with transaction() as db:
        scan(db, created_at=old)
        finding(db, created_at=old, kind="secret")
        finding(db, "recent", kind="code")
        run(db, created_at=old - timedelta(days=900))
    result = get(client, "security", "?all_time=true&to_at=2026-11-01T00:00:00Z&ordering=-key")
    assert result["scope"]["bucket"] == "month"
    assert datetime.fromisoformat(result["scope"]["from_at"]) == old
    assert [row["key"] for row in result["kinds"]] == ["secret", "code"]
    assert sum(point["findings"] for point in result["trend"]) == 2
    assert sum(point["runs"] for point in result["trend"]) == 1
    assert [point["date"] for point in result["trend"]] == sorted(point["date"] for point in result["trend"])


def test_security_timezone_dst_and_exclusive_end(client):
    start = datetime(2026, 3, 8, 5, tzinfo=UTC)
    end = datetime(2026, 3, 9, 4, tzinfo=UTC)
    with transaction() as db:
        scan(db, created_at=start)
        scan(db, "at-end", created_at=end)
        finding(db, created_at=end - timedelta(seconds=1))
        finding(db, "at-end", created_at=end)
    result = get(client, "security", "?from_at=2026-03-08T05:00:00Z&to_at=2026-03-09T04:00:00Z&timezone=America/New_York")
    assert [(point["date"], point["runs"], point["findings"]) for point in result["trend"]] == [("2026-03-08", 1, 1)]


@pytest.mark.parametrize("query", ["?timezone=../UTC", "?repo=-1", "?from_at=2026-11-01T00:00:00Z&to_at=2026-10-01T00:00:00Z"])
def test_security_rejects_invalid_filters(client, query):
    assert client.get("/api/v1/analytics/security" + query).status_code == 422
