import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph

from code_mortis.code_graph import retrieval, service, store
from code_mortis.core.db import transaction
from code_mortis.models import (
    CodeGraphFile,
    CodeGraphImport,
    CodeGraphSnapshot,
    CodeGraphSymbol,
    Membership,
    Repository,
    User,
)


def query(graph, operation="symbols", **kwargs):
    actor_id = kwargs.pop("actor_id", 7)
    return retrieval.query(retrieval.GraphQuery(repository_id=22, commit_sha=graph["head"], operation=operation,
                                               **kwargs), actor_id)


def test_exact_revision_symbols_and_independent_member_access(graph):
    row, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        db.add_all([User(id=8, login="reader"), Membership(github_id=8, login="reader")])
    result = query(graph, query="first", actor_id=8)
    assert result["snapshot_id"] == row.id and result["commit_sha"] == graph["first"]
    assert [item["name"] for item in result["items"]] == ["first"]
    assert query(graph, query=result["items"][0]["key"])["items"] == result["items"]
    assert query(graph, "defines", paths=["skip.py"])["items"] == []
    graph["head"] = graph["second"]
    unavailable = query(graph)
    assert unavailable["status"] == "unavailable" and not unavailable["items"]


def test_repository_and_extractor_versions_cannot_supply_another_snapshot(graph, monkeypatch):
    row, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        db.add(Repository(id=23, installation_id=10, full_name="team/other"))
    monkeypatch.setattr(service.github, "authorize_repo", lambda db, actor, name, **kwargs: {"id": 23 if name == "team/other" else 22})
    service.configure(23, 7, enabled=True)
    result = retrieval.query(retrieval.GraphQuery(repository_id=23, commit_sha=graph["first"], operation="symbols"), 7)
    assert result["status"] == "unavailable" and not result["items"]
    with transaction() as db:
        db.get(CodeGraphSnapshot, row.id).extractor_version = "structural/old"
    assert query(graph)["status"] == "unavailable"


@pytest.mark.parametrize("operation", ["callers", "references"])
def test_unsupported_relationships_are_explicit(graph, operation):
    run(service.enqueue_snapshot(22))
    result = query(graph, operation)
    assert result["relationship_state"] == result["capabilities"][operation] == "unsupported"
    assert result["gaps"] and not result["items"]


def test_authorization_precedes_lookup_and_returns_nothing_on_revocation(graph, monkeypatch):
    run(service.enqueue_snapshot(22))
    called = []
    original = retrieval.snapshot
    monkeypatch.setattr(retrieval, "snapshot", lambda *args: called.append(True) or original(*args))
    with transaction() as db:
        db.get(User, 7).enabled = False
    with pytest.raises(service.AccessLost):
        query(graph)
    assert not called
    with transaction() as db:
        db.get(User, 7).enabled = True
    monkeypatch.setattr(service.mcp, "current_provenance", lambda: {"resource": "reviews"})
    with pytest.raises(service.AccessLost):
        query(graph)
    assert not called


def test_policy_change_blocks_old_facts_and_hidden_neighbors(graph):
    row, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        # Even malicious/stale rows pointing to an excluded file cannot affect traversal or counts.
        db.add(CodeGraphImport(snapshot_id=row.id, file_path="a.py", start_byte=0, end_byte=1,
                               start_line=1, end_line=1, specifier="./skip", target_path="skip.py", provenance="relative_path"))
    result = query(graph, "dependencies", paths=["a.py"])
    assert not result["items"] and not result["truncated"]
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "a.py,skip.py"}
    assert query(graph)["status"] == "unavailable"


def test_cycles_depth_and_reverse_impact_candidates(graph):
    row, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        for path in ["b.py", "c.py", "d.py"]:
            db.add(CodeGraphFile(snapshot_id=row.id, path=path, blob_sha="b" * 40, size_bytes=1,
                                 status="parsed", language="python", line_count=1))
        db.flush()
        for source, target in [("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "a.py"), ("c.py", "d.py")]:
            db.add(CodeGraphImport(snapshot_id=row.id, file_path=source, start_byte=0 if target != "d.py" else 1,
                                   end_byte=2, start_line=1, end_line=1, specifier="./" + target,
                                   target_path=target, provenance="relative_path"))
    result = query(graph, "dependencies", paths=["a.py"])
    assert len(result["items"]) == 2 and result["truncated"]
    impact = query(graph, "impact", paths=["c.py"])
    assert {i["path"] for i in impact["items"]} == {"a.py", "b.py"}
    assert all(i["kind"] == "impact_candidate" for i in impact["items"])
    assert len(query(graph, "dependencies", paths=["a.py"], limit=1)["items"]) == 1


def test_source_and_literal_matches_have_exact_citations(graph):
    run(service.enqueue_snapshot(22))
    result = query(graph, "source", paths=["a.py"])
    source = result["items"][0]
    assert source["text"] == "def first():\n    return 1\n" and result["source_state"] == "available"
    match = query(graph, "text", query="return", paths=["a.py"])["items"][0]
    assert source["text"].encode()[match["start_byte"]:match["end_byte"]] == b"return"
    assert match["start_line"] == 2
    unavailable = query(graph, "source", paths=["a.py", "skip.py", "missing.py"])
    assert unavailable["source_state"] == "partial" and unavailable["unavailable_sources"] == ["missing.py", "skip.py"]
    assert len(unavailable["items"]) == 1
    store.git(store.directory(22), "update-ref", store.reference(result["snapshot_id"]), graph["second"])
    assert query(graph, "source", paths=["a.py"])["source_state"] == "unavailable"


def test_source_scan_and_response_budgets(graph):
    root = graph["source"]
    for index in range(12):
        (root / f"file{index:02}.py").write_text("# " + "á" * 40000)
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "bounded sources")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")
    run(service.enqueue_snapshot(22))
    result = query(graph, "source")
    assert result["truncated"] and len(result["searched_files"]) <= 10
    assert sum(len(item["text"].encode()) for item in result["items"]) <= 65536
    result = query(graph, "text", query="absent")
    assert result["truncated"] and not result["items"]


def test_revocation_during_read_does_not_return_content(graph, monkeypatch):
    run(service.enqueue_snapshot(22))
    original = store.read_blob

    def revoked(*args):
        raw = original(*args)
        with transaction() as db:
            db.get(User, 7).enabled = False
        return raw

    monkeypatch.setattr(store, "read_blob", revoked)
    with pytest.raises(service.AccessLost):
        query(graph, "source", paths=["a.py"])


@pytest.mark.parametrize("patch", [{"limit": 101}, {"depth": 3}, {"paths": ["a.py"] * 11}, {"commit_sha": "main"}])
def test_query_limits_are_validated(patch):
    with pytest.raises(ValidationError):
        retrieval.GraphQuery.model_validate({"repository_id": 22, "commit_sha": "a" * 40, "operation": "symbols", **patch})


def test_filtered_symbol_limit_does_not_count_hidden_rows(graph):
    row, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        symbol = db.scalar(select(CodeGraphSymbol).where(CodeGraphSymbol.snapshot_id == row.id))
        db.add(CodeGraphSymbol(snapshot_id=row.id, key="a" * 64, file_path="skip.py", name="hidden", kind=symbol.kind,
                               start_byte=0, end_byte=1, start_line=1, end_line=1))
    result = query(graph, limit=1)
    assert len(result["items"]) == 1 and result["items"][0]["name"] == "first" and not result["truncated"]


def test_source_and_literal_pages_preserve_utf8_and_boundary_matches(graph):
    root = graph["source"]
    raw = ("#" + "á" * 32765 + "needle" + "á" * 100 + "\n").encode()
    (root / "large.py").write_bytes(raw)
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "page boundary")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")
    run(service.enqueue_snapshot(22))
    first = query(graph, "source", paths=["large.py"])
    second = query(graph, "source", paths=["large.py"], **first["next_cursor"])
    assert (first["items"][0]["text"] + second["items"][0]["text"]).encode() == raw
    matches = query(graph, "text", query="needle", paths=["large.py"])
    assert len(matches["items"]) == 1
    item = matches["items"][0]
    assert raw[item["start_byte"]:item["end_byte"]] == b"needle"
    assert not query(graph, "text", query="needle", paths=["large.py"], **matches["next_cursor"])["items"]
    with pytest.raises(UnicodeDecodeError):
        query(graph, "source", paths=["large.py"], end_byte=2)


def test_read_only_missing_lock_and_busy_writer_keep_metadata_available(graph):
    run(service.enqueue_snapshot(22))
    with store.locked():
        assert query(graph, "status")["status"] in service.USABLE
        assert query(graph, "source", paths=["a.py"])["source_state"] == "unavailable"
    from code_mortis.core.config import settings

    (settings().code_graph_store_dir / ".lock").unlink()
    assert query(graph, "source", paths=["a.py"])["source_state"] == "unavailable"
    assert not (settings().code_graph_store_dir / ".lock").exists()
