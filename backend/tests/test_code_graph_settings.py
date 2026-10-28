from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_code_graph_workflow import graph as graph
from test_code_graph_workflow import run

from code_mortis import jobs
from code_mortis.code_graph import service
from code_mortis.core.db import now, transaction
from code_mortis.integrations import github
from code_mortis.models import CodeGraphRepository, CodeGraphSnapshot, Job, Membership, Repository

BASE = "/api/v1/repositories/22/code-graph"


def save(client, enabled=None):
    body = {"enabled": False, "settings": {"background": "Keep this context", "exclusions": "skip.py"}}
    if enabled is not None:
        body["code_graph_enabled"] = enabled
    return client.put("/api/v1/repositories/22", json=body)


def refresh_job():
    with transaction() as db:
        return db.scalar(select(Job).where(Job.kind == "code_graph_refresh").order_by(Job.available_at.desc()).limit(1))


def test_activation_defaults_off_and_preserves_other_settings(graph, client):
    service.configure(22, 7, enabled=False)
    assert client.get(BASE).json()["state"] == "disabled"
    assert save(client).json()["code_graph_enabled"] is False
    assert refresh_job() is None
    result = save(client, True)
    assert result.status_code == 200, result.text
    assert result.json()["settings"]["background"] == "Keep this context"
    assert result.json()["code_graph_enabled"] is True
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).actor_id == 7
    first = refresh_job()
    assert first and client.get(BASE).json()["state"] == "queued"
    save(client, True)
    assert refresh_job().id == first.id
    jobs.execute_job(first.id)
    with transaction() as db:
        row = db.scalar(select(CodeGraphSnapshot))
        identity = row.id
    assert run(identity)[0].status == "ready"
    state = client.get(BASE).json()
    assert state["state"] == "ready" and state["commit_sha"] == graph["first"]
    assert state["indexed_sha"] == graph["first"]
    assert state["coverage"]["parsed"] == 1 and state["coverage"]["complete"]
    assert save(client).json()["code_graph_enabled"] is True
    save(client, False)
    assert client.get(BASE).json()["state"] == "disabled"


def test_cancel_survives_reconciliation_and_retry_resumes(graph, client):
    save(client, True)
    first = refresh_job()
    assert client.post(BASE + "/cancel").json()["state"] == "cancelled"
    jobs.execute_job(first.id)
    with transaction() as db:
        assert service.schedule_refresh(db, 22) is None
        assert db.scalar(select(func.count()).select_from(CodeGraphSnapshot)) == 0
    assert client.post(BASE + "/retry").status_code == 202
    jobs.execute_job(refresh_job().id)
    with transaction() as db:
        snapshot = db.scalar(select(CodeGraphSnapshot))
        assert snapshot.status == "queued"
        identity = snapshot.id
    assert client.post(BASE + "/cancel").json()["paused"]
    assert run(identity)[0].status == "cancelled"
    assert client.post(BASE + "/retry").status_code == 202
    jobs.execute_job(refresh_job().id)
    assert run(identity)[0].status == "ready"


def test_new_head_policy_and_partial_readiness(graph, client):
    first, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        db.get(CodeGraphSnapshot, first.id).status = "partial"
    assert client.get(BASE).json()["state"] == "partial"
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "exclusions": "renamed.py"}
    assert client.get(BASE).json()["state"] == "stale"
    graph["head"] = graph["second"]
    with transaction() as db:
        job_id = service.schedule_refresh(db, 22)
    jobs.execute_job(job_id)
    state = client.get(BASE).json()
    assert state["state"] == "queued" and state["commit_sha"] == graph["second"]
    assert state["indexed_sha"] == graph["first"]
    assert state["snapshot_id"] != first.id


def test_refresh_revocation_and_expired_attempt_cannot_publish(graph, monkeypatch):
    with transaction() as db:
        identity = service.schedule_refresh(db, 22)
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(403, "secret denied")))
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphSnapshot)) == 0
        assert "secret" not in db.get(CodeGraphRepository, 22).error
        job = db.get(Job, identity)
        assert job.status == "failed"
        job.status, job.attempts, job.lease_until = "running", 2, now() + timedelta(minutes=10)
        db.get(CodeGraphRepository, 22).error = "newer attempt"
    with pytest.raises(service.client.ExtractionCancelled):
        service.refresh(22, identity, 1, 7)
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).error == "newer attempt"


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("POST", "/retry"), ("POST", "/cancel")])
def test_status_actions_require_browser_admin_and_fixed_repo(graph, client, monkeypatch, method, suffix):
    assert client.request(method, BASE + suffix, headers={"Authorization": "Bearer rejected"}).status_code == 401
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert client.request(method, BASE + suffix).status_code == 403
    with transaction() as db:
        db.get(Membership, 7).role = "admin"
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 23})
    assert client.request(method, BASE + suffix).status_code == 403


def test_legacy_mcp_update_cannot_enable_or_schedule_graph(graph, client, monkeypatch):
    monkeypatch.setattr(service.mcp, "current_provenance", lambda: {"grant_id": "reviews-only"})
    assert save(client).status_code == 200
    assert refresh_job() is None
    assert save(client, True).status_code == 422
    assert client.get(BASE).status_code == 422


def test_bad_policy_rolls_back_configuration_and_disable_stops_queued_refresh(graph, client):
    response = client.put("/api/v1/repositories/22", json={
        "enabled": False, "code_graph_enabled": True, "settings": {"exclusions": "!private.py"},
    })
    assert response.status_code == 422
    with transaction() as db:
        assert db.get(Repository, 22).settings["exclusions"] == "skip.py"
    save(client, True)
    identity = refresh_job().id
    save(client, False)
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphSnapshot)) == 0


def test_reenable_explicitly_resumes_cancelled_revision(graph, client):
    identity = service.enqueue_snapshot(22)
    assert client.post(BASE + "/cancel").json()["paused"]
    assert save(client, False).status_code == 200
    assert save(client, True).status_code == 200
    jobs.execute_job(refresh_job().id)
    assert run(identity)[0].status == "ready"
