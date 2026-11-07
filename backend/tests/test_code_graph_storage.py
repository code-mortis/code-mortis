import copy

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from code_mortis.core.db import Base, engine, now, transaction
from code_mortis.models import (
    CodeGraphFile,
    CodeGraphImport,
    CodeGraphRepository,
    CodeGraphSnapshot,
    CodeGraphSymbol,
    Finding,
    Occurrence,
    Repository,
    ReviewRun,
)
from code_mortis.schemas import code_graph as contracts


def extraction():
    return {
        "repository_id": 22, "commit_sha": "a" * 40, "extractor_version": "structural/v1",
        "policy_digest": "f" * 64,
        "files": [
            {"path": path, "blob_sha": blob * 40, "size_bytes": 100, "language": "python",
             "status": "parsed", "line_count": 10, "unresolved_calls": 1}
            for path, blob in (("src/a.py", "b"), ("src/b.py", "c"))
        ],
        "symbols": [
            {"file_path": path, "name": "same", "kind": "function_definition",
             "start_byte": 40, "end_byte": 90, "start_line": 4, "end_line": 9}
            for path in ("src/a.py", "src/b.py")
        ],
        "imports": [
            {"file_path": "src/a.py", "start_byte": 0, "end_byte": 30, "start_line": 1, "end_line": 2,
             "specifier": ".b", "target_path": "src/b.py", "provenance": "relative_path"},
            {"file_path": "src/b.py", "start_byte": 0, "end_byte": 30, "start_line": 1, "end_line": 2,
             "specifier": ".missing", "target_path": None, "provenance": "unresolved"},
        ],
    }


def snapshot(db, **overrides):
    row = CodeGraphSnapshot(**{
        "repository_id": 22, "actor_id": 7, "commit_sha": "a" * 40,
        "extractor_version": "structural/v1", "policy_digest": "f" * 64, **overrides,
    })
    db.add(row)
    db.flush()
    return row.id


def test_contract_keeps_same_names_distinct_and_unresolved_imports_explicit():
    result = contracts.CodeGraphResult.model_validate(extraction())
    assert len({symbol.key for symbol in result.symbols}) == 2
    assert result.imports[1].target_path is None
    assert result.imports[1].provenance == "unresolved"
    assert contracts.CodeGraphResult.model_validate_json(result.model_dump_json()) == result
    moved = result.symbols[0].model_dump() | {"start_byte": 41}
    assert contracts.CodeGraphSymbol.model_validate(moved).key != result.symbols[0].key


@pytest.mark.parametrize("path", [
    "", ".", "./a.py", "../a.py", "/a.py", "src/../a.py", "src//a.py", "src/./a.py", "src/",
    "src/.git/config", "src/.GIT/config", "src\\a.py", "a\x00.py", "a\n.py", "a\x7f.py", "é" * 1001,
])
def test_contract_rejects_noncanonical_or_unsafe_paths(path):
    with pytest.raises(ValidationError):
        contracts.CodeGraphFile.model_validate(extraction()["files"][0] | {"path": path})


@pytest.mark.parametrize("patch", [
    {"repository_id": True}, {"commit_sha": "main"}, {"policy_digest": "x" * 64},
    {"extractor_version": ""}, {"schema_version": "code-graph/v1"}, {"unexpected": "data"},
])
def test_contract_rejects_invalid_snapshot_identity(patch):
    with pytest.raises(ValidationError):
        contracts.CodeGraphResult.model_validate(extraction() | patch)


@pytest.mark.parametrize("collection,field", [("symbols", "name"), ("imports", "specifier"),
                                             ("files", "language"), (None, "extractor_version")])
@pytest.mark.parametrize("value", ["invalid\0text", "invalid\ud800text"])
def test_contract_rejects_metadata_the_database_cannot_store(collection, field, value):
    data = extraction()
    (data[collection][0] if collection else data)[field] = value
    with pytest.raises(ValidationError) as error:
        contracts.CodeGraphResult.model_validate(data)
    assert error.value.errors()[0]["loc"] == ((collection, 0, field) if collection else (field,))
    assert error.value.errors()[0]["type"] in {"value_error", "string_unicode"}


@pytest.mark.parametrize("collection,patch", [
    ("symbols", {"file_path": "not-in-snapshot.py"}),
    ("symbols", {"end_byte": 101}),
    ("symbols", {"end_line": 11}),
    ("symbols", {"end_byte": 40}),
    ("symbols", {"start_byte": True}),
    ("symbols", {"start_line": 0}),
    ("symbols", {"end_line": 3}),
    ("imports", {"target_path": "another-snapshot.py"}),
    ("imports", {"target_path": "../outside.py"}),
    ("imports", {"target_path": None}),
    ("imports", {"provenance": "unresolved"}),
    ("imports", {"provenance": "inferred"}),
    ("imports", {"specifier": "unrelated_package"}),
])
def test_contract_rejects_invalid_ranges_and_relationships(collection, patch):
    data = extraction()
    data[collection][0].update(patch)
    with pytest.raises(ValidationError):
        contracts.CodeGraphResult.model_validate(data)


@pytest.mark.parametrize("collection", ["files", "symbols", "imports"])
def test_contract_rejects_duplicate_records(collection):
    data = extraction()
    data[collection].append(copy.deepcopy(data[collection][0]))
    with pytest.raises(ValidationError, match="unique within a snapshot"):
        contracts.CodeGraphResult.model_validate(data)


def test_partial_files_preserve_facts_and_unparsed_files_cannot_claim_them():
    data = extraction()
    data["files"][0].update(status="partial", reason="syntax_error")
    result = contracts.CodeGraphResult.model_validate(data)
    assert result.files[0].status == "partial" and len(result.symbols) == 2
    data["files"][0].update(status="failed", reason="parse_error", unresolved_calls=None)
    with pytest.raises(ValidationError, match="parsed source file"):
        contracts.CodeGraphResult.model_validate(data)


def test_excluded_targets_cannot_be_resolved():
    data = extraction()
    data["symbols"] = data["symbols"][:1]
    data["imports"] = data["imports"][:1]
    data["files"][1].update(status="excluded", reason="excluded", unresolved_calls=None)
    with pytest.raises(ValidationError, match="available target"):
        contracts.CodeGraphResult.model_validate(data)


def test_contract_bounds_and_skipped_file_metadata(monkeypatch):
    file = extraction()["files"][0]
    with pytest.raises(ValidationError, match="per-file source limit"):
        contracts.CodeGraphFile.model_validate(file | {"size_bytes": contracts.MAX_INDEXED_FILE_BYTES + 1})
    skipped = contracts.CodeGraphFile.model_validate(file | {
        "size_bytes": contracts.MAX_FILE_BYTES + 1, "status": "excluded",
        "reason": "file_too_large", "unresolved_calls": None,
    })
    assert skipped.reason == "file_too_large"
    data = extraction() | {"symbols": [], "imports": []}
    data["files"] = [file | {"path": f"{i}.py", "size_bytes": contracts.MAX_FILE_BYTES} for i in range(8)]
    contracts.CodeGraphResult.model_validate(data)
    data["files"].append(file | {"path": "extra.py", "size_bytes": 1})
    with pytest.raises(ValidationError, match="snapshot byte limit"):
        contracts.CodeGraphResult.model_validate(data)
    data["files"] = [file | {"path": f"{i}.py"} for i in range(1001)]
    with pytest.raises(ValidationError, match="configured file limits"):
        contracts.CodeGraphResult.model_validate(data)
    monkeypatch.setattr(contracts, "MAX_RESULT_BYTES", 1000)
    with pytest.raises(ValidationError, match="serialized byte limit"):
        contracts.CodeGraphResult.model_validate(extraction())


def test_snapshot_identity_reuses_only_the_same_repository_revision_and_policy(seed):
    with transaction() as db:
        original = snapshot(db)
    with pytest.raises(IntegrityError, match="uq_code_graph_snapshot_identity"), transaction() as db:
        snapshot(db)
    with transaction() as db:
        assert snapshot(db, commit_sha="b" * 40) != original
        assert snapshot(db, extractor_version="structural/v2") != original
        assert snapshot(db, policy_digest="e" * 64) != original
        db.add(Repository(id=23, installation_id=10, full_name="team/other"))
        db.flush()
        assert snapshot(db, repository_id=23) != original


@pytest.mark.parametrize("cross_repository", [False, True])
@pytest.mark.parametrize("foreign_endpoint", ["symbol_file", "import_source", "import_target"])
def test_database_rejects_cross_snapshot_endpoints(seed, cross_repository, foreign_endpoint):
    data = contracts.CodeGraphResult.model_validate(extraction())
    with transaction() as db:
        if cross_repository:
            db.add(Repository(id=23, installation_id=10, full_name="team/other"))
            db.flush()
        first = snapshot(db)
        second = snapshot(db, repository_id=23 if cross_repository else 22, commit_sha="b" * 40)
        db.add(CodeGraphFile(snapshot_id=first, **data.files[0].model_dump()))
        db.add(CodeGraphFile(snapshot_id=second, **data.files[1].model_dump()))
    with pytest.raises(IntegrityError, match="fk_code_graph_"), transaction() as db:
        if foreign_endpoint == "symbol_file":
            db.add(CodeGraphSymbol(snapshot_id=first, key=data.symbols[1].key, **data.symbols[1].model_dump()))
        elif foreign_endpoint == "import_source":
            db.add(CodeGraphImport(snapshot_id=first, **data.imports[1].model_dump()))
        else:
            db.add(CodeGraphImport(snapshot_id=first, **data.imports[0].model_dump()))


@pytest.mark.parametrize("patch", [{"provenance": "unresolved"}, {"end_byte": 0}])
def test_database_enforces_import_provenance_and_ranges(seed, patch):
    data = contracts.CodeGraphResult.model_validate(extraction())
    with transaction() as db:
        identity = snapshot(db)
        db.add_all(CodeGraphFile(snapshot_id=identity, **file.model_dump()) for file in data.files)
    with pytest.raises(IntegrityError, match="ck_code_graph_import_"), transaction() as db:
        db.add(CodeGraphImport(snapshot_id=identity, **(data.imports[0].model_dump() | patch)))


def test_default_order_upgrade_preserves_existing_graph_pin(seed):
    checked = now()
    with transaction() as db:
        db.add(CodeGraphRepository(repository_id=22, enabled=True, actor_id=7,
                                   default_sha="a" * 40, observed_sha="b" * 40, checked_at=checked))
        identity = snapshot(db, status="ready")
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261026_graph_readiness")
        with engine().connect() as connection:
            before = connection.execute(text("SELECT * FROM code_graph_snapshots WHERE id = :id"),
                                        {"id": identity}).mappings().one()
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        graph = db.get(CodeGraphRepository, 22)
        assert graph.default_sha == "a" * 40 and graph.observed_sha == "b" * 40
        assert graph.enabled and graph.actor_id == 7
        assert graph.checked_at == graph.default_observed_at == checked
        assert db.execute(text("SELECT * FROM code_graph_snapshots WHERE id = :id"),
                          {"id": identity}).mappings().one() == {**before, "failure": None}


def test_upgrade_and_graph_cleanup_preserve_existing_history(seed):
    with transaction() as db:
        db.add(ReviewRun(id="history", pr_id="pr", actor_id=7, trigger="manual", dedup_key="history",
                         base_sha="b" * 40, head_sha="a" * 40, config={"existing": True},
                         status="completed", publication="published", report={"saved": "original report"}))
        db.flush()
        db.add(Finding(id="finding", pr_id="pr", fingerprint="f" * 64, status="open", path="src/a.py",
                       content="Original finding", start_line=4, end_line=5, last_seen_run_id="history"))
        db.flush()
        db.add(Occurrence(id="occurrence", run_id="history", finding_id="finding", snapshot={"saved": "original evidence"}))
    config = Config("alembic.ini")
    legacy_tables = [t for t in Base.metadata.sorted_tables if not t.name.startswith(("code_graph_", "analysis_", "inference_"))]
    try:
        command.downgrade(config, "20261025_api_tokens")
        with engine().connect() as connection:
            before = {t.name: connection.execute(select(*[c for c in t.columns if c.name != "code_graph_limits"]).order_by(*t.primary_key.columns)).mappings().all()
                      for t in legacy_tables}
    finally:
        command.upgrade(config, "head")
    with engine().connect() as connection:
        after = {t.name: connection.execute(select(*[c for c in t.columns if c.name != "code_graph_limits"]).order_by(*t.primary_key.columns)).mappings().all()
                 for t in legacy_tables}
        assert before == after
        context = MigrationContext.configure(connection, opts={
            "compare_type": True, "compare_server_default": True,
            "include_object": lambda obj, name, type_, reflected, compare_to: type_ != "table" or name.startswith("code_graph_"),
        })
        assert compare_metadata(context, Base.metadata) == []
    data = contracts.CodeGraphResult.model_validate(extraction())
    with transaction() as db:
        identity = snapshot(db)
        db.add_all(CodeGraphFile(snapshot_id=identity, **file.model_dump()) for file in data.files)
        db.flush()
        db.add_all(CodeGraphSymbol(snapshot_id=identity, key=item.key, **item.model_dump()) for item in data.symbols)
        db.add_all(CodeGraphImport(snapshot_id=identity, **item.model_dump()) for item in data.imports)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphSymbol)) == 2
        assert db.scalar(select(func.count()).select_from(CodeGraphImport)) == 2
        assert db.get(CodeGraphImport, (identity, "src/b.py", 0)).target_path is None
        db.delete(db.get(CodeGraphSnapshot, identity))
    with transaction() as db:
        for model in (CodeGraphSnapshot, CodeGraphFile, CodeGraphSymbol, CodeGraphImport):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.get(ReviewRun, "history").report == {"saved": "original report"}
        assert db.get(Finding, "finding").status == "open"
        assert db.get(Occurrence, "occurrence").snapshot == {"saved": "original evidence"}
        assert db.get(Repository, 22).settings == seed["repo"].settings
