from sqlalchemy import func, select

from code_mortis.code_graph import service as graphs
from code_mortis.core.db import transaction
from code_mortis.core.security import seal
from code_mortis.integrations import github
from code_mortis.models import Connection, Job, Repository


def test_retired_reference_allows_remediation_and_unrelated_edits(seed, client, github_stub):
    with transaction() as db:
        db.get(Connection, "connection").kind = "chatgpt"
    saved = client.put("/api/v1/repositories/22", json={
        "enabled": True, "connection_id": "connection", "settings": {"background": "Updated context"},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["connection_requires_setup"] is True
    assert saved.json()["connection_id"] == "connection"
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    disabled = client.put("/api/v1/repositories/22", json={"enabled": False, "connection_id": "connection"})
    assert disabled.status_code == 200
    rejected = client.put("/api/v1/repositories/22", json={"enabled": True, "connection_id": "connection"})
    assert rejected.status_code == 422 and "shared API" in rejected.text


def test_new_retired_choice_rejected_but_explicit_shared_remediation_works(seed, client, github_stub):
    with transaction() as db:
        db.add(Connection(id="retired", name="Old subscription", kind="claude_code", owner_id=7,
                          provider="anthropic", protocol="native", model="old-model", secret=seal("{}")))
    rejected = client.put("/api/v1/repositories/22", json={"enabled": False, "connection_id": "retired"})
    assert rejected.status_code == 422
    with transaction() as db:
        db.get(Repository, 22).connection_id = "retired"
    saved = client.put("/api/v1/repositories/22", json={"enabled": True, "connection_id": "connection"})
    assert saved.status_code == 200 and saved.json()["connection_requires_setup"] is False
    assert saved.json()["connection_id"] == "connection"


def test_old_clients_preserve_engine_and_graph_limits(seed, client, monkeypatch):
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 22, "permissions": {"push": True}})
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.enabled = False
        repo.settings = {**repo.settings, "analysis_engine": "mortis"}
        repo.code_graph_limits = {"max_files": 2345, "max_file_bytes": 2000000, "max_source_bytes": 9000000}
    saved = client.put("/api/v1/repositories/22", json={"enabled": False, "settings": {"language": "Spanish"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["analysis_engine"] == "mortis"
    assert saved.json()["code_graph_limits"]["max_files"] == 2345
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_engine_change_requires_browser_and_exact_repository(seed, client, monkeypatch):
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 22, "permissions": {"push": True}})
    body = {"enabled": False, "settings": {"analysis_engine": "mortis"}}
    with monkeypatch.context() as scoped:
        scoped.setattr(graphs.mcp, "current_provenance", lambda: {"grant_id": "reviews-only"})
        assert client.put("/api/v1/repositories/22", json=body).status_code == 422
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 23, "permissions": {"push": True}})
    assert client.put("/api/v1/repositories/22", json=body).status_code == 403
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 22, "permissions": {"push": True}})
    saved = client.put("/api/v1/repositories/22", json=body)
    assert saved.status_code == 200 and saved.json()["settings"]["analysis_engine"] == "mortis"
