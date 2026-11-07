"""Shared API graph context uses exact, optional evidence without OCR scope execution."""

import copy
import json

import pytest
from sqlalchemy import select
from test_code_graph_review import related as related
from test_code_graph_workflow import fixture_git, run
from test_code_graph_workflow import graph as graph

from code_mortis.code_graph import retrieval, service
from code_mortis.core import source as git_source
from code_mortis.core.db import transaction
from code_mortis.core.errors import ReviewStopped
from code_mortis.integrations import ocr
from code_mortis.models import CodeGraphRepository, CodeGraphSnapshot, Connection, Job, Repository, User
from code_mortis.reviews import graph_context, planning, verification
from code_mortis.schemas.common import RepoSettings


@pytest.fixture
def neutral(related, monkeypatch):
    options = RepoSettings(analysis_engine="mortis").model_dump()
    rules = planning.load_rules(related["source"], {}, related["first"], options, lambda: False)
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind = "api"
        related["options"] = graph_context.configuration(
            db, db.get(Repository, 22), connection, db.get(User, 7), engine={"analysis_engine": "mortis"},
        )
    monkeypatch.setattr(ocr, "command", lambda *a, **kw: pytest.fail("Mortis graph context executed OCR"))
    monkeypatch.setattr(verification, "filter_context", lambda *a, **kw: pytest.fail("Mortis used OCR scope"))
    monkeypatch.setattr(verification, "read_source", lambda *a, **kw: pytest.fail("Mortis used legacy source helper"))
    return {**related, "settings": options, "rules": rules}


def prepare(neutral, **kwargs):
    return graph_context.prepare(
        neutral["options"], kwargs.pop("path", "a.py"), kwargs.pop("sha", neutral["head"]),
        neutral["source"], {}, kwargs.pop("settings", neutral["settings"]),
        {"api_key": "private-fixture-value"}, kwargs.pop("stopped", lambda: False),
        remaining=kwargs.pop("remaining", graph_context.MAX_RUN_BYTES),
        base_sha=kwargs.pop("base_sha", neutral["first"]),
        trusted_rules=kwargs.pop("trusted_rules", neutral["rules"]), **kwargs,
    )


def test_shared_graph_is_bounded_neutral_and_redacted(neutral):
    metadata, supporting = prepare(neutral)
    assert metadata["version"] == graph_context.ENGINE_VERSION
    assert metadata["reason"] == "used" and metadata["commit_sha"] == neutral["head"]
    assert metadata["base_sha"] == neutral["first"]
    assert supporting["capabilities"]["callers"] == "unsupported" and supporting["gaps"]
    assert [source["path"] for source in supporting["sources"]] == ["one.py", "two.py", "three.py"]
    assert metadata["truncated"] and len(supporting["sources"]) <= graph_context.MAX_FILES
    assert sum(len(source["text"].encode()) for source in supporting["sources"]) <= graph_context.MAX_BYTES
    assert "private-fixture-value" not in json.dumps([metadata, supporting])
    assert "text" not in metadata and all("text" not in file for file in metadata["files"])


@pytest.mark.parametrize("engine,kind,expected", [
    (None, "api", graph_context.VERSION),
    ({"analysis_engine": "ocr"}, "api", graph_context.VERSION),
    ({"analysis_engine": "mortis"}, "api", graph_context.ENGINE_VERSION),
    ({"analysis_engine": "mortis"}, "chatgpt", None),
])
def test_eligibility_needs_explicit_shared_engine_snapshot(related, engine, kind, expected):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind = kind
        result = graph_context.configuration(db, db.get(Repository, 22), connection, db.get(User, 7), engine=engine)
        assert (result["version"] if result else None) == expected


@pytest.mark.parametrize("origin", ["pat", "mcp"])
def test_shared_engine_never_widens_token_or_mcp_graph_authority(neutral, monkeypatch, origin):
    monkeypatch.setattr(getattr(service, origin), "current_provenance", lambda: {"resource": "reviews"})
    with transaction() as db:
        assert graph_context.configuration(db, db.get(Repository, 22), db.get(Connection, "connection"),
                                           db.get(User, 7), engine={"analysis_engine": "mortis"}) is None
    metadata, supporting = prepare(neutral)
    assert metadata["reason"] == "access_unavailable" and supporting is None


@pytest.mark.parametrize("paused", [False, True])
def test_missing_graph_never_waits_and_respects_index_pause(neutral, paused):
    with transaction() as db:
        db.get(CodeGraphRepository, 22).paused = paused
    metadata, supporting = prepare(neutral, sha=neutral["first"])
    assert metadata["reason"] == "unavailable" and supporting is None
    with transaction() as db:
        queued = db.scalars(select(Job).where(Job.kind == "code_graph_refresh")).all()
        assert len(queued) == (0 if paused else 1)
        if queued:
            assert queued[0].payload["commit_sha"] == neutral["first"]


def test_partial_index_keeps_gaps_and_ready_index_can_be_read_while_paused(neutral):
    with transaction() as db:
        db.get(CodeGraphSnapshot, neutral["snapshot"]).status = "partial"
        db.get(CodeGraphRepository, 22).paused = True
    metadata, supporting = prepare(neutral)
    assert metadata["status"] == supporting["index_status"] == "partial"
    assert metadata["reason"] == "used" and supporting["gaps"]


def test_shared_retry_freezes_selection_after_index_retention(neutral, monkeypatch):
    saved, first = prepare(neutral)
    with transaction() as db:
        db.delete(db.get(CodeGraphSnapshot, neutral["snapshot"]))
    monkeypatch.setattr(retrieval, "query", lambda *a: pytest.fail("Retry reselected graph evidence"))
    repeated, second = prepare(neutral, saved=saved, remaining=0)
    assert repeated == saved and second == first


@pytest.mark.parametrize("field,value", [("repository_id", 23), ("commit_sha", "f" * 40),
                                         ("extractor_version", "older-parser")])
def test_retrieval_cannot_supply_a_different_identity(neutral, monkeypatch, field, value):
    query = retrieval.query
    def wrong(*args):
        return {**query(*args), field: value}
    monkeypatch.setattr(retrieval, "query", wrong)
    metadata, supporting = prepare(neutral)
    assert metadata["reason"] == "incompatible_context" and supporting is None


@pytest.mark.parametrize("change", ["path", "base_sha", "sha", "trusted_rules", "settings"])
def test_shared_retry_cannot_substitute_identity_or_rules(neutral, change):
    saved, _ = prepare(neutral)
    kwargs = {"path": "one.py", "base_sha": "f" * 40, "sha": neutral["first"],
              "trusted_rules": {**neutral["rules"], "digest": "changed"},
              "settings": {**neutral["settings"], "exclusions": "*.py"}}
    result, supporting = prepare(neutral, saved=saved, **{change: kwargs[change]})
    assert result["reason"] == "incompatible_context" and supporting is None


def test_scope_exclusions_and_rename_origins_are_applied_before_reading(neutral, monkeypatch):
    seen = []
    read = git_source.read_source
    monkeypatch.setattr(git_source, "read_source", lambda *a, **kw: (seen.append(a[3]), read(*a, **kw))[1])
    metadata, supporting = prepare(neutral, settings={**neutral["settings"], "exclusions": "one.py"})
    assert metadata["reason"] == "used" and "one.py" not in seen
    assert "one.py" not in {source["path"] for source in supporting["sources"]}


def test_shared_scope_honors_custom_rule_exclusions(neutral, monkeypatch):
    settings = {**neutral["settings"], "rules": {"exclude": ["one.py"]}}
    rules = planning.load_rules(neutral["source"], {}, neutral["first"], settings, lambda: False)
    read = git_source.read_source
    def source(*args, **kwargs):
        assert args[3] != "one.py", "Excluded source was read before policy admission"
        return read(*args, **kwargs)
    monkeypatch.setattr(git_source, "read_source", source)
    metadata, supporting = prepare(neutral, settings=settings, trusted_rules=rules)
    assert metadata["reason"] == "used"
    assert "one.py" not in {source["path"] for source in supporting["sources"]}


def test_policy_change_during_optional_read_prevents_delivery(neutral, monkeypatch):
    read = git_source.read_source
    def source(*args, **kwargs):
        with transaction() as db:
            db.get(Repository, 22).settings = {"exclusions": "one.py"}
        return read(*args, **kwargs)
    monkeypatch.setattr(git_source, "read_source", source)
    metadata, supporting = prepare(neutral)
    assert metadata["reason"] == "policy_changed" and supporting is None


def test_rename_from_excluded_path_cannot_become_optional_source(graph, monkeypatch):
    root = graph["source"]
    (root / "credentials.py").write_text("def value():\n    return 1\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "excluded source")
    base = fixture_git(root, "rev-parse", "HEAD")
    fixture_git(root, "mv", "credentials.py", "one.py")
    (root / "a.py").write_text("from .one import value\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "rename source")
    head = fixture_git(root, "rev-parse", "HEAD")
    graph["head"] = head
    run(service.enqueue_snapshot(22))
    settings = RepoSettings(analysis_engine="mortis").model_dump()
    with transaction() as db:
        options = graph_context.configuration(db, db.get(Repository, 22), db.get(Connection, "connection"),
                                               db.get(User, 7), engine={"analysis_engine": "mortis"})
    monkeypatch.setattr(git_source, "read_source", lambda *a, **kw: pytest.fail("Read excluded rename"))
    metadata, supporting = graph_context.prepare(
        options, "a.py", head, root, {}, settings, {}, lambda: False, remaining=graph_context.MAX_RUN_BYTES,
        base_sha=base, trusted_rules=planning.load_rules(root, {}, base, settings, lambda: False),
    )
    assert metadata["reason"] == "no_related_source" and supporting is None


@pytest.mark.parametrize("failure", [ValueError("unavailable"), TimeoutError("timeout")])
def test_retrieval_failure_is_optional(neutral, monkeypatch, failure):
    def query(*args):
        raise failure
    monkeypatch.setattr(retrieval, "query", query)
    metadata, supporting = prepare(neutral)
    assert metadata["reason"] == "unavailable" and supporting is None


def test_cancellation_still_stops_the_review(neutral):
    with pytest.raises(ReviewStopped):
        prepare(neutral, stopped=lambda: True)


def test_shared_byte_limit_omits_without_mutating_required_scope(neutral):
    before = copy.deepcopy(neutral["settings"])
    metadata, supporting = prepare(neutral, remaining=1)
    assert metadata["reason"] == "limited_or_excluded" and supporting is None
    assert neutral["settings"] == before


def test_unsupported_format_has_no_graph_source_requirement(neutral):
    metadata, supporting = prepare(neutral, path="README.md")
    assert metadata["reason"] == "no_related_source" and supporting is None


def test_shared_discovery_keeps_source_on_the_existing_evidence_request_path(neutral):
    from code_mortis.reviews import assessment, context

    evidence = {
        "finding": {"path": "a.py"}, "current_path": "a.py", "head_sha": neutral["head"],
        "original_sha": neutral["first"], "sources": {"current": {"a.py": "from .one import value\n"}},
        "history": [], "prior_candidates": [], "diff": "",
    }
    enriched = graph_context.discovery(
        evidence, neutral["options"], neutral["source"], {}, neutral["settings"], {}, lambda: False,
        {"kind": "api", "options": {}}, assessment.INSTRUCTIONS, assessment.output_schema(evidence),
        base_sha=neutral["first"],
    )
    assert enriched["code_graph"]["reason"] == "used"
    assert enriched["sources"] == evidence["sources"]
    supplied, _, _ = context.selection_context(enriched, assessment.output_schema(enriched))
    assert {item["path"] for item in supplied["citation_sources"].values()} == {"a.py"}
