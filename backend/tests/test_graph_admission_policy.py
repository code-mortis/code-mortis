import base64
from types import SimpleNamespace

import httpx
import pytest
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph

from code_mortis.code_graph import client as parser
from code_mortis.code_graph import errors, retrieval, service, store
from code_mortis.code_graph.extract import extract
from code_mortis.core import file_policy
from code_mortis.core.db import transaction
from code_mortis.models import CodeGraphRepository, Repository
from code_mortis.schemas.code_graph import CodeGraphLimits, CodeGraphRequest, blob_sha


def manifest_request(monkeypatch, entries, limits=None, custom=""):
    rules = store.policy(SimpleNamespace(settings={"exclusions": custom}, code_graph_limits=limits or {}))
    row = SimpleNamespace(repository_id=22, commit_sha="a" * 40, id="00000000-0000-0000-0000-000000000001",
                          policy=rules, policy_digest=store.policy_digest(rules), extractor_version=store.contracts.EXTRACTOR_VERSION)
    monkeypatch.setattr(store, "git", lambda *a, **k: b"a" * 40)
    monkeypatch.setattr(store, "tree", lambda *a: {path: ("100644", "blob", blob_sha(raw), len(raw)) for path, raw in entries.items()})
    loaded = []

    def read(root, blob, size, stopped, *, max_bytes):
        path, raw = next((path, raw) for path, raw in entries.items() if blob_sha(raw) == blob)
        loaded.append(path)
        assert size <= max_bytes
        return raw

    monkeypatch.setattr(store, "read_blob", read)
    return store.request(row, lambda: False), loaded


def test_media_and_unsupported_metadata_do_not_consume_source_limits(monkeypatch):
    entries = {f"images/{n}.PNG": b"media" for n in range(1100)}
    entries.update({"a.py": b"def okay():pass\n", "README.md": b"docs", "page.mdx": b"<Component />",
                    "page.astro": b"---\nconst x = 1\n---", "package.json": b"{}"})
    entries["large.MP4"] = b"x" * (9 * 1024 * 1024)
    payload, loaded = manifest_request(monkeypatch, entries, dict(max_files=1, max_file_bytes=16, max_source_bytes=16))
    assert loaded == ["a.py"]
    result = parser.validate_result(payload, extract(payload))
    assert sum(item.status == "unsupported" for item in result.files) == 4
    assert sum(item.status == "parsed" for item in result.files) == 1
    assert all(item.content_base64 is None for item in payload.files if item.path != "a.py")


@pytest.mark.parametrize("count,size,code,actual,limit", [(3, 4, "file_count", 3, 2), (2, 5, "source_size", 10, 8)])
def test_limits_fail_before_blob_reads(monkeypatch, count, size, code, actual, limit):
    with pytest.raises(errors.GraphError) as raised:
        manifest_request(monkeypatch, {f"{n}.py": b"#" * size for n in range(count)},
                         dict(max_files=2, max_file_bytes=8, max_source_bytes=8))
    assert raised.value.failure.code == code
    assert raised.value.failure.measurements == {"actual": actual, "limit": limit}


def test_exact_limits_and_oversized_warning(monkeypatch):
    payload, loaded = manifest_request(monkeypatch, {"a.py": b"#123", "b.py": b"#456", "big.py": b"#1234"},
                                      dict(max_files=2, max_file_bytes=4, max_source_bytes=8))
    result = parser.validate_result(payload, extract(payload))
    assert loaded == ["a.py", "b.py"]
    assert result.files[-1].reason == "file_too_large" and result.files[-1].size_bytes == 5


@pytest.mark.parametrize("name", [*(f"asset.{ext.upper()}" for ext in file_policy.EXTENSIONS),
                                  "nested/package-lock.json", "pnpm-lock.yaml", "bun.lockb", "x.min.CSS",
                                  "nested/node_modules/a.py", "BUILD/a.py", ".env.local", "foo/AGENTS.md"])
def test_fixed_policy_is_case_insensitive_and_cannot_be_removed(name):
    assert file_policy.excluded(name, {"exclusions": []})
    assert file_policy.excluded(name, {"exclusions": ["!**/*"]})
    rules = file_policy.review_rules({"rules": {"include": ["**/*"], "exclude": []}})
    assert set(file_policy.MANDATORY) <= set(rules["exclude"])


@pytest.mark.parametrize("ext", ["md", "mdx", "markdown", "json", "yaml", "toml", "sql", "html", "css", "astro", "py"])
def test_text_is_reviewable_independently_of_structural_support(ext):
    assert not file_policy.excluded(f"docs/example.{ext}")
    assert file_policy.excluded(f"docs/example.{ext}", file_policy.policy({"exclusions": "docs/**"}))


def test_parser_rejects_mandatory_source_and_unsupported_bodies():
    for path in ("secret.py", "README.md", "page.mdx"):
        raw = b"not parser input"
        with pytest.raises(ValueError):
            CodeGraphRequest(repository_id=22, commit_sha="a" * 40, policy_digest="b" * 64, files=[dict(
                path=path, size_bytes=len(raw), blob_sha=blob_sha(raw), content_base64=base64.b64encode(raw).decode())])


@pytest.mark.parametrize("patch", [{"max_files": 0}, {"max_files": 5001}, {"max_files": True}, {"max_files": 1.5},
                                   {"max_file_bytes": 4194305}, {"max_source_bytes": 33554433},
                                   {"max_file_bytes": 9, "max_source_bytes": 8}])
def test_limit_validation_boundaries(patch):
    with pytest.raises(ValueError):
        CodeGraphLimits(**patch)


def test_defaults_upper_bounds_and_saved_settings_preserve_pause_and_legacy_clients(graph, client):
    assert CodeGraphLimits().model_dump() == dict(max_files=1000, max_file_bytes=1048576, max_source_bytes=8388608)
    limits = dict(max_files=5000, max_file_bytes=4194304, max_source_bytes=33554432)
    client.post("/api/v1/repositories/22/code-graph/cancel")
    response = client.put("/api/v1/repositories/22", json={"enabled": False, "code_graph_limits": limits})
    assert response.status_code == 200, response.text
    assert response.json()["code_graph_limits"] == limits
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).paused
        assert service.schedule_refresh(db, 22) is None
    legacy = client.put("/api/v1/repositories/22", json={"enabled": False})
    assert legacy.status_code == 200 and legacy.json()["code_graph_limits"] == limits


def test_graph_limits_require_browser_admin(graph, client, monkeypatch):
    monkeypatch.setattr(service.mcp, "current_provenance", lambda: {"grant_id": "reviews-only"})
    response = client.put("/api/v1/repositories/22", json={"enabled": False, "code_graph_limits": {"max_files": 2000}})
    assert response.status_code == 422
    with transaction() as db:
        assert db.get(Repository, 22).code_graph_limits == {}


def test_changed_limits_refresh_identity_failure_preserves_prior_index(graph, client, monkeypatch):
    first, _ = run(service.enqueue_snapshot(22))
    client.put("/api/v1/repositories/22", json={"enabled": False, "code_graph_limits": {"max_files": 2000}})
    next_id = service.enqueue_snapshot(22)
    assert next_id != first.id
    monkeypatch.setattr(store, "request", lambda *a: (_ for _ in ()).throw(errors.measured("file_count", 2001, 2000)))
    failed, _ = run(next_id)
    assert failed.failure["code"] == "file_count"
    state = client.get("/api/v1/repositories/22/code-graph").json()
    assert state["indexed_sha"] == first.commit_sha and state["state"] == "failed"
    assert state["failure"]["attempt_reference"] and "2,001" in state["error"]


@pytest.mark.parametrize("status,code", [(401, "parser_authentication"), (403, "parser_authentication"),
                                       (409, "parser_busy"), (413, "transport_limit"),
                                       (422, "parser_invalid_request"), (504, "parser_timeout"),
                                       (502, "parser_terminated"), (507, "parser_storage_limit"), (500, "parser_invalid_result"), (200, "parser_invalid_result")])
def test_safe_parser_failures(monkeypatch, status, code):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, stream=httpx.ByteStream(b"private-worker-secret")))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=transport, **kw))
    payload = CodeGraphRequest(repository_id=22, commit_sha="a" * 40, policy_digest="b" * 64, files=[])
    from code_mortis.core.config import settings

    monkeypatch.setattr(settings(), "code_graph_parser_url", "http://parser")
    monkeypatch.setattr(settings(), "code_graph_token", "fixture")
    with pytest.raises(errors.GraphError) as raised:
        parser.extract(payload)
    assert raised.value.failure.code == code
    assert "private-worker-secret" not in str(raised.value)


def test_tree_guard_is_independent_and_errors_are_sanitized(monkeypatch, tmp_path, caplog):
    entry = b"100644 blob " + b"b" * 40 + b" 1\ta.py\0"
    monkeypatch.setattr(store, "git", lambda *a, **k: entry * 20001)
    with pytest.raises(errors.GraphError) as raised:
        store.tree(tmp_path, "a" * 40)
    assert raised.value.failure.code == "tree_limit"
    diagnostic = service.diagnostic_for(RuntimeError("private-worker-secret"), "storage", "attempt:2")
    assert diagnostic.code == "unexpected" and diagnostic.attempt_reference == "attempt:2"
    assert "private-worker-secret" not in diagnostic.model_dump_json() + caplog.text


def test_saved_file_limit_reaches_parser_and_bounded_source_navigation(graph):
    root = graph["source"]
    content = b"#" + b"a" * 1048576 + b"\n"
    (root / "large.py").write_bytes(content)
    (root / "guide.mdx").write_bytes(content)
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "larger source and documentation")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")
    with transaction() as db:
        db.get(Repository, 22).code_graph_limits = {"max_file_bytes": 2097152}
    row, _ = run(service.enqueue_snapshot(22))
    assert row.status == "ready"
    assert graph["requests"][-1].limits.max_file_bytes == 2097152
    doc = next(item for item in graph["requests"][-1].files if item.path == "guide.mdx")
    assert doc.content_base64 is None and doc.exclusion is None
    assert service.source(row.id, 7, "guide.mdx") == content
    result = retrieval.query(retrieval.GraphQuery(repository_id=22, commit_sha=graph["head"], operation="source",
                                                 paths=["large.py"], start_byte=1048576), 7)
    assert result["items"][0]["text"] == "a\n"


def test_summary_filters_legacy_selected_media_without_mutating_report(monkeypatch, tmp_path):
    from code_mortis.reviews import summary

    report = {"manifest": {"coverage": {"selected": [{"path": path} for path in ("README.md", "image.SVG", "custom.py")]}}}
    run = SimpleNamespace(report=report, config={"settings": {"language": "English", "exclusions": "custom.py"}, "connection": {}},
                          base_sha="a" * 40, head_sha="b" * 40)
    monkeypatch.setattr(summary.ocr, "git", lambda *a, **k: "a" * 40 if "merge-base" in a else "documentation change")
    context = summary.build_context(tmp_path, {}, run, lambda: False)
    assert context["paths"] == ["README.md"]
    assert len(report["manifest"]["coverage"]["selected"]) == 3
