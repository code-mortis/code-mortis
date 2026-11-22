from time import perf_counter

from sqlalchemy import func, insert, select
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph

from code_mortis.code_graph import repository, service
from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import (
    CodeGraphFile,
    CodeGraphImport,
    CodeGraphRepository,
    Job,
    Membership,
    Repository,
)

BASE = "/api/v1/repositories/22/code-graph"


def indexed_file(snapshot_id, path):
    return {"snapshot_id": snapshot_id, "path": path, "blob_sha": "b" * 40, "size_bytes": 1,
            "status": "parsed", "language": "python", "line_count": 1}


def stored_import(snapshot_id, source, target, offset=0):
    return {"snapshot_id": snapshot_id, "file_path": source, "target_path": target, "start_byte": offset,
            "end_byte": offset + 1, "start_line": 1, "end_line": 1, "specifier": f"./{target or 'missing'}",
            "provenance": "relative_path" if target else "unresolved"}


def test_repository_graph_pins_default_revision_and_bounds_focus(graph, client):
    row, _ = run(service.enqueue_snapshot(22))
    newer, _ = run(service.enqueue_snapshot(22, graph["second"]))
    with transaction() as db:
        db.get(Membership, 7).role = "member"
        db.execute(insert(CodeGraphFile), [indexed_file(row.id, path) for path in
                                         ("pkg/b.py", "pkg/c.py", "pkg/d.py", "pkg/unlinked.py")])
        db.execute(insert(CodeGraphImport), [stored_import(row.id, a, b, i) for i, (a, b) in enumerate([
            ("a.py", "pkg/b.py"), ("a.py", "pkg/b.py"), ("pkg/b.py", "pkg/c.py"), ("pkg/d.py", "a.py"),
            ("a.py", "skip.py"), ("a.py", None),
        ])])
        jobs = db.scalar(select(func.count()).select_from(Job))
    graph["fresh"].clear()

    response = client.get(BASE + "/view")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert graph["fresh"] == [True, True]
    view = response.json()
    assert (view["commit_sha"], view["snapshot_id"], view["branch"]) == (graph["first"], row.id, "main")
    assert newer.id != view["snapshot_id"]
    assert view["total_links"] == 3
    root = next(node for node in view["nodes"] if node["path"] == "a.py")
    assert (root["incoming"], root["outgoing"]) == (1, 1)
    assert "skip.py" not in response.text and "pkg/unlinked.py" in response.text

    one = client.get(BASE + "/view", params={"focus": "a.py", "direction": "outgoing"}).json()
    assert [node["path"] for node in one["nodes"]] == ["a.py", "pkg/b.py"]
    two = client.get(BASE + "/view", params={"focus": "a.py", "direction": "outgoing", "depth": 2}).json()
    assert [node["path"] for node in two["nodes"]] == ["a.py", "pkg/b.py", "pkg/c.py"]
    assert len(two["edges"]) == 2
    filtered = client.get(BASE + "/view", params={"folder": "pkg", "hide_unlinked": True}).json()
    assert {node["path"] for node in filtered["nodes"]} == {"pkg/b.py", "pkg/c.py"}
    assert filtered["edges"] == [{"source": "file:pkg/b.py", "target": "file:pkg/c.py"}]

    read = client.get(BASE + "/source", params={"commit_sha": graph["first"], "path": "a.py"})
    assert read.status_code == 200 and read.headers["cache-control"] == "no-store"
    assert read.json()["items"][0]["text"] == "def first():\n    return 1\n"
    absent = client.get(BASE + "/source", params={"commit_sha": graph["second"], "path": "a.py"}).json()
    assert absent["items"] == [] and absent["source_state"] == "unavailable"
    future = client.get(BASE + "/view", params={"commit_sha": "f" * 40}).json()
    assert future["status"] == "unavailable" and future["nodes"] == []
    with transaction() as db:
        config = db.get(CodeGraphRepository, 22)
        config.observed_sha = graph["second"]
        assert db.scalar(select(func.count()).select_from(Job)) == jobs
    view = client.get(BASE + "/view").json()
    assert (view["commit_sha"], view["observed_sha"]) == (graph["first"], graph["second"])


def test_repository_graph_enforces_browser_policy_and_rechecks_permission(graph, client, monkeypatch):
    run(service.enqueue_snapshot(22))
    assert client.get(BASE + "/view", headers={"Authorization": "Bearer outside"}).status_code == 401
    assert client.get(BASE + "/view", params={"folder": "../invalid"}).status_code == 422
    assert client.get(BASE + "/source", params={"commit_sha": graph["first"], "path": "../invalid"}).status_code == 422
    real_authorize = github.authorize_repo
    calls = []

    def revoked(*args, **kwargs):
        calls.append(kwargs["fresh"])
        if len(calls) == 2:
            with transaction() as db:
                db.get(CodeGraphRepository, 22).enabled = False
            raise service.AccessLost("Permission revoked before the read response.")
        return real_authorize(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(github, "authorize_repo", revoked)
        response = client.get(BASE + "/view")
    assert calls == [True, True] and response.status_code == 403
    assert "a.py" not in response.text
    assert client.get(BASE + "/view").json()["status"] == "disabled"
    assert client.get(BASE + "/files", params={"commit_sha": graph["first"], "query": "a"}).status_code == 403
    with transaction() as db:
        db.get(CodeGraphRepository, 22).enabled = True
        db.get(Repository, 22).settings = {"exclusions": "skip.py,a.py"}
    assert client.get(BASE + "/view").json()["status"] == "unavailable"
    assert client.get(BASE + "/files", params={"commit_sha": graph["first"], "query": "a"}).status_code == 409


def test_repository_graph_checks_sensitive_metadata_and_the_entire_source(graph, client):
    root = graph["source"]
    (root / "module.py").write_text("# " + "a" * 70_000 + "\n# provider-secret\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "source screening")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")
    row, _ = run(service.enqueue_snapshot(22))
    sha = row.commit_sha
    response = client.get(BASE + "/source", params={"commit_sha": sha, "path": "module.py"})
    assert response.status_code == 422 and "contains credential material" in response.json()["detail"]
    assert "provider-secret" not in response.text

    with transaction() as db:
        db.add(CodeGraphFile(**indexed_file(row.id, "pkg/github-user-token.py")))
    response = client.get(BASE + "/view")
    assert response.status_code == 422 and "contains credential material" in response.json()["detail"]
    assert "github-user-token" not in response.text
    response = client.get(BASE + "/files", params={"commit_sha": sha, "query": "github-user"})
    assert response.status_code == 422 and "contains credential material" in response.json()["detail"]
    assert "github-user-token" not in response.text


def test_repository_graph_caps_and_searches_a_5000_file_snapshot(graph, client):
    row, _ = run(service.enqueue_snapshot(22))
    paths = [f"pkg{i % 64:02}/sub/{i:04}.py" for i in range(4999)]
    with transaction() as db:
        db.execute(insert(CodeGraphFile), [indexed_file(row.id, path) for path in paths])
        db.execute(insert(CodeGraphImport), [
            import_row for i, path in enumerate(paths)
            for import_row in (stored_import(row.id, path, paths[(i + 1 + (i // 64) % 63) % 64]),
                               stored_import(row.id, path, "a.py", 1))
        ])
    start = perf_counter()
    response = client.get(BASE + "/view")
    print(f"5,000-file repository overview with stubbed GitHub checks: {(perf_counter() - start) * 1000:.0f} ms")
    assert response.status_code == 200, response.text
    overview = response.json()
    assert overview["mode"] == "folders" and overview["total_files"] == 5000
    assert len(overview["nodes"]) == 65 and len(overview["edges"]) == repository.MAX_LINKS and overview["truncated"]
    assert client.get(BASE + "/view").json() == overview

    focused = client.get(BASE + "/view", params={"focus": "a.py", "direction": "incoming"}).json()
    assert focused["total_files"] == 5000 and focused["truncated"]
    assert len(focused["nodes"]) == repository.MAX_NODES and focused["nodes"][0]["path"] == "a.py"
    assert focused["nodes"][0]["incoming"] == 4999
    path = paths[-1]
    assert not any(node["path"] == path for node in focused["nodes"])
    search = client.get(BASE + "/files", params={"commit_sha": row.commit_sha, "query": path}).json()
    assert [node["path"] for node in search["files"]] == [path]
    assert not search["truncated"]
