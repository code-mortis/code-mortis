import copy

import pytest
from sqlalchemy import func, select
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph

from code_mortis.code_graph import explorer, retrieval, service, store
from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import (
    CodeGraphRepository,
    CodeGraphSnapshot,
    Job,
    Membership,
    PullRequest,
    Repository,
)

BASE = "/api/v1/pulls/pr/code-graph"


@pytest.fixture
def indexed(graph, monkeypatch):
    explorer.comparison_metadata.cache_clear()
    root = graph["source"]
    (root / "consumer.py").write_text("from .renamed import second\n\ndef consume():\n    return second()\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "consumer")
    graph["pr_head"] = fixture_git(root, "rev-parse", "HEAD")
    with transaction() as db:
        pr = db.get(PullRequest, "pr")
        pr.base_sha, pr.head_sha = graph["first"], graph["pr_head"]
    graph["comparison"] = {"base_commit": {"sha": graph["first"]}, "merge_base_commit": {"sha": graph["first"]},
        "files": [{"filename": "renamed.py", "previous_filename": "a.py", "status": "renamed"},
                  {"filename": "consumer.py", "status": "added"}, {"filename": "skip.py", "status": "modified"},
                  {"filename": ".env", "status": "modified"}]}
    monkeypatch.setattr(github, "request", lambda method, path, token, **kw:
                        copy.deepcopy(graph["comparison"]) if "/compare/" in path else {"sha": graph["head"]})
    run(service.enqueue_snapshot(22, graph["first"]))
    run(service.enqueue_snapshot(22, graph["pr_head"]))
    yield graph
    explorer.comparison_metadata.cache_clear()


def body(indexed, **values):
    return {"base_sha": indexed["first"], "head_sha": indexed["pr_head"], "operation": "dependencies",
            "path": "renamed.py", **values}


def test_browser_navigation_exact_revisions_and_no_implicit_jobs(indexed, client):
    with transaction() as db:
        before = db.scalar(select(func.count()).select_from(Job))
    response = client.get(BASE)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["head"]["commit_sha"] == indexed["pr_head"] and result["base"]["commit_sha"] == indexed["first"]
    assert result["files"] == [{"path": "a.py", "change": "removed"}, {"path": "consumer.py", "change": "added"},
                               {"path": "renamed.py", "change": "added"}]
    assert result["total"] == 3 and "skip.py" not in response.text
    edges = client.post(BASE + "/query", json=body(indexed)).json()["items"]
    assert [(edge["path"], edge["target_path"]) for edge in edges] == [("consumer.py", "renamed.py")]
    symbols = client.post(BASE + "/query", json=body(indexed, operation="symbols")).json()["items"]
    assert symbols[0]["name"] == "second"
    source = client.post(BASE + "/query", json=body(indexed, operation="source")).json()["items"][0]
    assert source["text"] == "def second():\n    return 3\n"
    old = client.post(BASE + "/query", json=body(indexed, operation="source", path="a.py", revision="base")).json()
    assert old["commit_sha"] == indexed["first"] and "def first" in old["items"][0]["text"]
    absent = client.post(BASE + "/query", json=body(indexed, operation="source", path="a.py")).json()
    assert absent["items"] == [] and absent["source_state"] == "unavailable"
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == before


def test_comparison_filter_pagination_and_bound(indexed, client):
    indexed["comparison"]["files"] = [{"filename": f"file{i:03}.py", "status": "modified"} for i in range(300)]
    result = client.get(BASE + "?page=2&q=file0").json()
    assert result["comparison_truncated"] and result["total"] == 100
    assert len(result["files"]) == 10 and result["files"][0]["path"] == "file010.py" and result["has_more"]
    assert client.get(BASE + "?q=missing&page=20").json()["page"] == 1


def test_comparison_cache_preserves_current_exclusions_permissions_and_revision(indexed, client, monkeypatch):
    original, calls = github.request, []

    def request(method, path, token, **kwargs):
        calls.append(path)
        return original(method, path, token, **kwargs)

    monkeypatch.setattr(github, "request", request)
    assert client.get(BASE).status_code == 200
    assert client.get(BASE + "?q=consumer&page=2").status_code == 200
    assert client.post(BASE + "/query", json=body(indexed, revision="base")).status_code == 200
    assert len(calls) == 1
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "consumer.py,skip.py"}
    result = client.get(BASE).json()
    assert "consumer.py" not in str(result) and result["head"]["status"] == "unavailable"
    assert len(calls) == 1
    with transaction() as db:
        db.get(PullRequest, "pr").head_sha = indexed["second"]
    assert client.get(BASE).json()["head_sha"] == indexed["second"]
    assert len(calls) == 2
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"id": 23})
    response = client.get(BASE)
    assert response.status_code == 403 and "renamed.py" not in response.text


@pytest.mark.parametrize("malformed", [None, {"base_commit": None}, {"merge_base_commit": []}, {"files": "bad"},
    {"files": [{"filename": "../escape", "status": "added"}]},
    {"files": [{"filename": "a.py", "status": "renamed"}]}, {"merge_base_commit": {"sha": "bad"}},
    {"base_commit": {"sha": "c" * 40}}])
def test_reject_malformed_comparison(indexed, client, malformed):
    indexed["comparison"] = {**indexed["comparison"], **malformed} if isinstance(malformed, dict) else malformed
    response = client.get(BASE)
    assert response.status_code == 422 and "escape" not in response.text


@pytest.mark.parametrize("values", [{"head_sha": "c" * 40}, {"base_sha": "c" * 40}, {"repository_id": 23},
    {"commit_sha": "c" * 40}, {"path": "../private.py"}, {"start_byte": -1}, {"operation": "text"}])
def test_request_identity_and_bounds(indexed, client, values):
    assert client.post(BASE + "/query", json=body(indexed, **values)).status_code in {409, 422}


@pytest.mark.parametrize("suffix,method", [("", "GET"), ("/query", "POST"), ("/index", "POST")])
def test_browser_routes_refuse_bearer_even_with_cookie(indexed, client, suffix, method):
    assert client.request(method, BASE + suffix, headers={"Authorization": "Bearer not-a-session"},
                          **({"json": body(indexed)} if method == "POST" else {})).status_code == 401


def test_members_read_but_only_admins_can_request_index(indexed, client):
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert client.get(BASE).status_code == 200
    assert client.post(BASE + "/query", json=body(indexed)).status_code == 200
    request = {"base_sha": indexed["first"], "head_sha": indexed["pr_head"]}
    assert client.post(BASE + "/index", json=request).status_code == 403
    with transaction() as db:
        db.get(Membership, 7).role = "admin"
    response = client.post(BASE + "/index", json=request)
    assert response.status_code == 202, response.text
    with transaction() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job.kind == "code_graph_refresh" and job.payload["commit_sha"] == indexed["pr_head"]
        assert job.payload["actor_id"] == 7


def test_disabled_missing_and_new_policy_never_use_old_snapshot(indexed, client):
    with transaction() as db:
        db.get(CodeGraphRepository, 22).enabled = False
    assert client.get(BASE).json()["enabled"] is False
    assert client.post(BASE + "/query", json=body(indexed)).status_code == 403
    with transaction() as db:
        db.get(CodeGraphRepository, 22).enabled = True
        db.get(Repository, 22).settings = {"exclusions": "consumer.py"}
    result = client.get(BASE).json()
    assert result["head"]["status"] == "unavailable" and "consumer.py" not in str(result)


@pytest.mark.parametrize("change", ["head", "policy", "disable", "membership"])
def test_changes_during_read_discard_content(indexed, client, monkeypatch, change):
    original = store.read_blob

    def read(*args):
        result = original(*args)
        with transaction() as db:
            if change == "head":
                db.get(PullRequest, "pr").head_sha = "c" * 40
            elif change == "policy":
                db.get(Repository, 22).settings = {"exclusions": "renamed.py"}
            elif change == "disable":
                db.get(CodeGraphRepository, 22).enabled = False
            else:
                db.delete(db.get(Membership, 7))
        return result

    monkeypatch.setattr(store, "read_blob", read)
    response = client.post(BASE + "/query", json=body(indexed, operation="source"))
    assert response.status_code in {403, 409} and "def second" not in response.text


def test_secret_check_precedes_source_slice_and_missing_objects(indexed, client, monkeypatch):
    original = store.read_blob
    monkeypatch.setattr(store, "read_blob", lambda *args: b"provider-secret\n" + original(*args))
    response = client.post(BASE + "/query", json=body(indexed, operation="source", start_byte=16))
    assert response.status_code == 422 and "provider-secret" not in response.text
    monkeypatch.setattr(store, "read_blob", original)
    with transaction() as db:
        snapshot = db.scalar(select(CodeGraphSnapshot).where(CodeGraphSnapshot.commit_sha == indexed["pr_head"]))
    store.git(store.directory(22), "update-ref", "-d", store.reference(snapshot.id))
    result = client.post(BASE + "/query", json=body(indexed, operation="source")).json()
    assert result["source_state"] == "unavailable" and not result["items"]


def test_fresh_github_identity_and_cross_pr_access(indexed, client, monkeypatch):
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"id": 23})
    response = client.get(BASE)
    assert response.status_code == 403 and "renamed.py" not in response.text


@pytest.mark.parametrize("operation", ["overview", "dependencies", "symbols", "source"])
def test_navigation_checks_github_once_before_and_after_read(indexed, client, monkeypatch, operation):
    indexed["fresh"].clear()
    response = client.get(BASE) if operation == "overview" else client.post(BASE + "/query", json=body(indexed, operation=operation))
    assert response.status_code == 200
    assert indexed["fresh"].count(True) == 2
    original = retrieval.read_snapshot

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"id": 23})
        return result

    monkeypatch.setattr(retrieval, "read_snapshot", revoked)
    response = client.get(BASE) if operation == "overview" else client.post(BASE + "/query", json=body(indexed, operation=operation))
    assert response.status_code == 403 and "renamed.py" not in response.text


def test_final_comparison_recheck(indexed, client, monkeypatch):
    original = retrieval.read_snapshot

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        with transaction() as db:
            db.get(PullRequest, "pr").base_sha = "c" * 40
        return result

    monkeypatch.setattr(explorer.retrieval, "read_snapshot", changed)
    response = client.get(BASE)
    assert response.status_code == 409 and "consumer.py" not in response.text
