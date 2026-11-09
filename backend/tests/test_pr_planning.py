"""Provider-free deterministic planning checks using synthetic Git objects."""

import json
import subprocess
from dataclasses import asdict

import pytest

from code_mortis.core import source
from code_mortis.core.process import child_environment
from code_mortis.reviews import planning


def git(repo, *args):
    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo).decode().strip()


def write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text if isinstance(text, bytes) else text.encode())


def commit(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Synthetic")
    git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    write(tmp_path, "main.py", "value = 1\n")
    return tmp_path, commit(tmp_path)


def plan(repository, **options):
    repo, base = repository
    head = commit(repo)
    return planning.plan_review(repo, child_environment(), base, head, base, options, lambda: False)


@pytest.mark.parametrize(("path", "pattern", "expected"), [
    ("main.py", "**/*.py", True), ("a/Main.PY", "**/*.py", True),
    ("src/a/b.py", "src/*.py", False), ("src/a.py", "src/?.py", True),
    ("src/a/b.py", "src/?.py", False), ("main.ts", "**/*.{js,ts}", True),
    ("a/b/c.go", "**/b/**/*.go", True), ("b/c.go", "**/b/**/*.go", True),
    ("a/c.go", "**/b/**/*.go", False), (".github/a.yml", ".github/**", True),
])
def test_rule_matching(path, pattern, expected):
    assert planning.rule_matches(path, pattern) is expected


def test_scope_documents_config_unknown_language_and_binary(repository):
    repo, _base = repository
    for path in ("README.md", "config.toml", "query.sql", "module.nim", "test_main.py", "diagram.SVG", "odd.xyz"):
        write(repo, path, "value = 2\n")
    write(repo, "blob.py", b"a\0b")
    write(repo, "main.py", "value = 2\n")
    result = plan(repository)
    assert [item.path for item in result.selected] == ["README.md", "config.toml", "main.py", "module.nim", "query.sql"]
    assert any(item["path"] == "blob.py" and item["reason"] == "binary" for item in result.exclusions)
    assert result.units and not any(item.error for item in result.selected)


def test_trusted_base_order_and_merged_rules(repository):
    repo, _base = repository
    write(repo, ".opencodereview/rule.json", json.dumps({"rules": [
        {"path": "**/*.py", "rule": "base-first", "merge_system_rule": True},
        {"path": "main.py", "rule": "base-second"},
    ]}))
    repository = repo, commit(repo)
    write(repo, ".opencodereview/rule.json", json.dumps({"rules": [{"path": "**", "rule": "untrusted-head"}]}))
    write(repo, "main.py", "value = 2\n")
    result = plan(repository)
    assert "base-first" in result.units[0].rule and planning.BUILTIN_RULE in result.units[0].rule
    assert "untrusted-head" not in result.units[0].rule
    assert "base-second" not in result.units[0].rule
    custom = planning.load_rules(repo, child_environment(), repository[1], {
        "rules": {"rules": [{"path": "**/*.py", "rule": "custom"}]}}, lambda: False)
    assert planning.rule_for("main.py", custom) == "custom"


def test_rule_references_only_trusted_base(repository):
    repo, _ = repository
    write(repo, "review-guide.md", "Trusted review guide\n")
    write(repo, ".opencodereview/rule.json", json.dumps({"rules": [{"path": "**", "rule": "review-guide.md"}]}))
    repository = repo, commit(repo)
    write(repo, "review-guide.md", "Head instructions\n")
    write(repo, "main.py", "value = 2\n")
    result = plan(repository)
    assert next(item for item in result.units if item.path == "main.py").rule == "Trusted review guide"


@pytest.mark.parametrize("reference", ["../outside.md", "/tmp/outside.md", "missing.md", "AGENTS.md", ".env.md"])
def test_unavailable_required_rules_fail_closed(repository, reference):
    repo, _ = repository
    write(repo, ".opencodereview/rule.json", json.dumps({"rules": [{"path": "**", "rule": reference}]}))
    base = commit(repo)
    with pytest.raises(ValueError, match="rule reference"):
        planning.load_rules(repo, child_environment(), base, {}, lambda: False)


def test_filter_priority_and_mandatory_custom_case(repository):
    repo, base = repository
    rules = planning.load_rules(repo, child_environment(), base, {"rules": {"include": ["**/*"]}}, lambda: False)
    assert planning.allowed_path("test_main.py", {"rules": {"include": ["**/*"]}}, rules)
    assert not planning.allowed_path("image.SVG", {}, rules)
    assert not planning.allowed_path("src/password.py", {}, rules)
    assert not planning.allowed_path("src/a.py", {"exclusions": "src/**"}, rules)
    assert planning.allowed_path("SRC/a.py", {"exclusions": "src/**"}, rules)
    deny = planning.load_rules(repo, child_environment(), base, {"rules": {"exclude": ["SRC/**"]}}, lambda: False)
    assert not planning.allowed_path("src/a.py", {}, deny)


def test_file_limit_before_source_reads(repository, monkeypatch):
    repo, _ = repository
    write(repo, "main.py", "value = 2\n")
    write(repo, "other.py", "value = 2\n")
    monkeypatch.setattr(source, "read_source", lambda *args: pytest.fail("A limited review must not read payloads"))
    result = plan(repository, max_files=1)
    assert result.limit_reason == "2 reviewable files exceeds the maximum of 1."
    assert len(result.selected) == 2 and not result.units and not result.batches


def test_rename_both_sides_and_deletion(repository):
    repo, _ = repository
    write(repo, "old.py", "old = 1\n")
    write(repo, "deleted.py", "deleted = 2\n")
    repository = repo, commit(repo)
    git(repo, "mv", "old.py", "new.py")
    (repo / "deleted.py").unlink()
    result = plan(repository, exclusions="old.py")
    assert not result.selected
    assert {item["reason"] for item in result.exclusions} == {"excluded", "deleted"}
    head = git(repo, "rev-parse", "HEAD")
    rules = planning.load_rules(repo, child_environment(), repository[1], {"rules": {"exclude": ["old.py"]}}, lambda: False)
    assert not planning.allowed_context(repo, child_environment(), repository[1], head, {"new.py"}, {}, lambda: False, rules=rules)


def test_split_complete_source_diff_and_stable_identity(repository):
    repo, base = repository
    text = "".join(f"item_{number} = {number}\n" for number in range(800))
    write(repo, "main.py", text)
    head = commit(repo)
    env = child_environment()
    first = planning.plan_review(repo, env, base, head, base, {}, lambda: False, max_unit_bytes=4096, max_batch_bytes=8192)
    second = planning.plan_review(repo, env, base, head, base, {}, lambda: False, max_unit_bytes=4096, max_batch_bytes=8192)
    assert first.snapshot() == second.snapshot()
    assert len(first.units) > 1
    assert "".join(unit.source for unit in first.units) == text
    original = source.git(repo, env, "diff", "--unified=3", base, head, "--", "main.py")
    _metadata, original_rows = planning._patch_rows(original, 800)
    assigned_rows = []
    for unit in first.units:
        _metadata, rows = planning._patch_rows(unit.patch, 800)
        assigned_rows.extend(row[3] for row in rows)
        assert len((unit.source + unit.patch + unit.rule).encode()) + 512 <= 4096
    assert assigned_rows == [row[3] for row in original_rows]
    assert all(sum(len((u.source + u.patch + u.rule).encode()) + 512 for u in batch) <= 8192 for batch in first.batches)
    assert all(unit.source_sha == head for unit in first.units)
    assert asdict(first.selected[0])["blob_sha"]


def test_oversized_line_is_explicit_failure(repository):
    repo, base = repository
    write(repo, "main.py", "x" * 5000 + "\n")
    head = commit(repo)
    result = planning.plan_review(repo, child_environment(), base, head, base, {}, lambda: False, max_unit_bytes=4096, max_batch_bytes=8192)
    assert result.selected[0].error and not result.units
    assert "bounded review context" in result.selected[0].error


def test_admission_limit_cannot_be_bypassed_with_splitting(repository):
    repo, base = repository
    write(repo, "main.py", "x = 1\n" * 1000)
    head = commit(repo)
    result = planning.plan_review(repo, child_environment(), base, head, base, {}, lambda: False,
                                  max_source_bytes=2048, max_unit_bytes=4096, max_batch_bytes=8192)
    assert "splitting cannot bypass" in result.selected[0].error
    assert not result.units


def test_symlink_source_is_not_read(repository):
    repo, base = repository
    (repo / "main.py").unlink()
    (repo / "main.py").symlink_to("/etc/passwd")
    head = commit(repo)
    result = planning.plan_review(repo, child_environment(), base, head, base, {}, lambda: False)
    assert result.selected[0].error and not result.units


def test_changed_merge_base_is_rejected(repository):
    repo, base = repository
    write(repo, "main.py", "value = 2\n")
    head = commit(repo)
    with pytest.raises(ValueError, match="merge base"):
        planning.plan_review(repo, child_environment(), base, head, head, {}, lambda: False)


def test_literal_diff_path_does_not_admit_other_files(repository):
    repo, _base = repository
    write(repo, "[a].py", "value = 'literal'\n")
    write(repo, "a.py", "value = 'other'\n")
    result = plan(repository)
    unit = next(item for item in result.units if item.path == "[a].py")
    assert "+value = 'literal'" in unit.patch
    assert "other" not in unit.patch


def test_project_filters_shadowed_by_runtime_custom_layer(repository):
    repo, _base = repository
    write(repo, ".opencodereview/rule.json", json.dumps({"exclude": ["**/*.py"], "include": ["**/*"]}))
    repository = repo, commit(repo)
    write(repo, "main.py", "value = 2\n")
    write(repo, "test_other.py", "value = 2\n")
    result = plan(repository)
    assert [item.path for item in result.selected] == ["main.py"]


def test_exact_file_limit_and_unlimited_have_same_scope(repository):
    repo, base = repository
    write(repo, "main.py", "value = 2\n")
    write(repo, "other.py", "value = 2\n")
    head = commit(repo)
    results = [planning.plan_review(repo, child_environment(), base, head, base, {"max_files": maximum}, lambda: False)
               for maximum in (2, None)]
    assert all(not result.limit_reason for result in results)
    assert results[0].units == results[1].units


def test_excluded_input_has_empty_truthful_denominator(repository):
    repo, _base = repository
    write(repo, "drawing.svg", "<svg/>\n")
    result = plan(repository)
    assert not result.selected and not result.units and not result.batches
    assert result.exclusions[0]["reason"] == "excluded"


def test_legacy_native_context_setting_does_not_cap_shared_api_plan(repository):
    repo, base = repository
    write(repo, "main.py", "value = 2\n" * 400)
    head = commit(repo)
    result = planning.plan_review(repo, child_environment(), base, head, base,
                                  {"delegated_context_bytes": 4096}, lambda: False,
                                  max_unit_bytes=2048, max_batch_bytes=4096)
    assert not result.selected[0].error and result.units


def test_large_removed_hunk_splits_without_dropping_diff_rows(repository):
    repo, _base = repository
    write(repo, "main.py", "".join(f"old_{n} = {n}\n" for n in range(500)))
    base = commit(repo)
    write(repo, "main.py", "new_value = 1\n")
    head = commit(repo)
    result = planning.plan_review(repo, child_environment(), base, head, base, {}, lambda: False,
                                  max_unit_bytes=4096, max_batch_bytes=8192)
    assert not result.selected[0].error
    assert len(result.units) > 1
    removed = [row for unit in result.units for row in unit.patch.splitlines() if row.startswith("-old_")]
    assert removed == [f"-old_{n} = {n}" for n in range(500)]
    assert all(unit.source == "new_value = 1\n" for unit in result.units)
    assert all(len((unit.source + unit.patch + unit.rule).encode()) + 512 <= 4096 for unit in result.units)


def test_aggregate_source_guard_retains_failed_selected_files(repository):
    repo, base = repository
    write(repo, "main.py", "value = 2\n" * 100)
    write(repo, "other.py", "value = 3\n" * 100)
    head = commit(repo)
    result = planning.plan_review(repo, child_environment(), base, head, base, {}, lambda: False,
                                  max_source_bytes=2500, max_unit_bytes=2048, max_batch_bytes=4096)
    assert len(result.selected) == 2
    assert any(item.error and "aggregate" in item.error for item in result.selected)
    assert sum(len((unit.source + unit.patch).encode()) for unit in result.units) <= 2500


def test_reference_cannot_restore_explicitly_excluded_rule_source(repository):
    repo, _base = repository
    write(repo, ".opencodereview/guide.md", "internal instructions\n")
    write(repo, ".opencodereview/rule.json", json.dumps({"rules": [
        {"path": "**", "rule": ".opencodereview/guide.md"},
    ]}))
    base = commit(repo)
    for options in ({"exclusions": ".opencodereview/**"}, {"rules": {"exclude": ["**/guide.md"]}}):
        with pytest.raises(ValueError, match="rule reference"):
            planning.load_rules(repo, child_environment(), base, options, lambda: False)


@pytest.mark.parametrize("policy", [{"exclusions": "excluded.py"}, {"rules": {"exclude": ["excluded.py"]}}])
def test_supporting_rename_policy_uses_merge_base_when_trusted_base_advances(repository, policy):
    repo, _base = repository
    write(repo, "excluded.py", "hidden = 1\n")
    merge_base = commit(repo)
    git(repo, "mv", "excluded.py", "allowed.py")
    write(repo, "main.py", "value = 2\n")
    head = commit(repo)
    git(repo, "checkout", "-q", "--detach", merge_base)
    (repo / "excluded.py").unlink()
    base = commit(repo)
    env = child_environment()
    result = planning.plan_review(repo, env, base, head, merge_base, policy, lambda: False)
    assert [item.path for item in result.selected] == ["main.py"]
    assert any(item["path"] == "allowed.py" and item["reason"] == "excluded" for item in result.exclusions)
    assert not planning.allowed_context(repo, env, base, head, ["allowed.py"], policy,
                                        lambda: False, rules=result.rules)
