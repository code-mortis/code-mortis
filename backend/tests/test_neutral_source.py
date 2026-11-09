"""Source infrastructure is independent of model adapters and review exclusions do not govern scanners."""

import base64
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from code_mortis.code_graph import store
from code_mortis.core import file_policy, process, source
from code_mortis.core.config import settings
from code_mortis.core.errors import ReviewStopped, RunnerBlocked
from code_mortis.reviews import summary as pr_summary
from code_mortis.reviews import verification
from code_mortis.security_scans import engine as security


def commit(repo):
    env = {**process.child_environment(), "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    source.git(repo, env, "add", ".")
    source.git(repo, env, "commit", "-qm", "fixture")
    return source.git(repo, env, "rev-parse", "HEAD").strip()


def test_neutral_process_keeps_credentials_cancellation_and_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("CM_DATABASE_URL", "database-secret")
    monkeypatch.setenv("GH_TOKEN", "github-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-secret")
    env = process.child_environment()
    assert not {"CM_DATABASE_URL", "GH_TOKEN", "ANTHROPIC_API_KEY"} & env.keys()
    with pytest.raises(ReviewStopped):
        process.command([sys.executable, "-c", "import time;time.sleep(30)"], tmp_path, env, stopped=lambda: True)
    with pytest.raises(ValueError, match="output exceeded"):
        process.command([sys.executable, "-c", "print('x'*100)"], tmp_path, env, max_output=20)
    code, out, _ = process.command([sys.executable, "-c", "import os;print(os.getenv('CM_OCR_REDACTION_FILE', 'absent'))"], tmp_path, env)
    assert code == 0 and out.strip() == "absent"
    busy = RunnerBlocked("busy", {"tokens": 4})
    assert busy.reason == "busy" and busy.usage == {"tokens": 4}
    assert "wait" in str(busy)


@pytest.mark.parametrize("path", ["", ".", "a//b.py", "a/../b.py", "/a.py", "a\\b.py", "a\x7fb.py", ".git/config"])
def test_neutral_path_validation(path):
    with pytest.raises(ValueError):
        source.repository_path(path)


def test_review_snapshot_is_credential_scoped_checks_head_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "work_dir", tmp_path)
    calls = []
    base, head = "a" * 40, "b" * 40

    def git(repo, env, *args, **kwargs):
        calls.append((repo, env, args))
        return head if args[0] == "rev-parse" else base

    with source.review_snapshot("owner/repo", 7, base, head, "fixture-token", lambda: False, git=git,
                                environment=lambda: {"PATH": os.environ["PATH"]}) as (repo, env, fetch_env, merge_base):
        assert repo.is_dir() and merge_base == base
        assert "GIT_CONFIG_VALUE_0" not in env
        assert base64.b64decode(fetch_env["GIT_CONFIG_VALUE_0"].split(" ")[-1]).decode() == "x-access-token:fixture-token"
        assert all("GIT_CONFIG_VALUE_0" not in values for _, values, args in calls if args[0] != "fetch")
    assert not repo.exists()
    with pytest.raises(ReviewStopped):
        with source.review_snapshot("owner/repo", 7, base, "c" * 40, "token", lambda: False, git=git):
            pytest.fail("Changed heads must not yield a snapshot")
    assert not list(tmp_path.iterdir())


def test_review_and_graph_exclusions_do_not_change_scanner_inventory(tmp_path, monkeypatch):
    root = tmp_path / "security"
    root.mkdir()
    item = security.Snapshot(root, "a" * 40, process.child_environment(), lambda: False)
    item.git.mkdir()
    source.git(item.git, item.env, "init", "--quiet")
    entries = {"package-lock.json": '{"lockfileVersion":3}\n', "credentials.py": 'API_KEY = "synthetic"\n',
               "app.py": "def run(): pass\n"}
    for path, text in entries.items():
        (item.git / path).write_text(text)
    item.sha = commit(item.git)
    # Scanner snapshots read Git objects directly; no dependency on the PR verification reader.
    monkeypatch.setattr(verification, "read_source", lambda *a, **kw: pytest.fail("Scanner used PR source policy"))
    item.populate()
    assert set(item.inventory) == set(entries)
    assert (item.files / "package-lock.json").read_text() == entries["package-lock.json"]
    assert (item.files / "credentials.py").read_text() == entries["credentials.py"]
    assert not item.inventory["package-lock.json"]["code"]
    rules = store.policy(SimpleNamespace(settings={"max_files": 1, "exclusions": "app.py"},
                                         code_graph_limits={"max_files": 1}))
    assert all(file_policy.excluded(path, rules) for path in entries)
    monkeypatch.setattr(store, "directory", lambda _: item.git)
    monkeypatch.setattr(store, "git", lambda root, *args, **kwargs: item.sha.encode()
                        if args[0] == "rev-parse" else subprocess.check_output(["git", *args], cwd=root))
    payload = store.request(SimpleNamespace(repository_id=22, id="00000000-0000-0000-0000-000000000001",
        commit_sha=item.sha, policy=rules, policy_digest=store.policy_digest(rules), extractor_version=store.contracts.EXTRACTOR_VERSION), lambda: False)
    assert all(file.content_base64 is None and file.exclusion == "excluded" for file in payload.files)
    assert set(item.inventory) == set(entries)


def test_renames_cannot_restore_evidence_and_optional_diff_is_not_leaked(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = process.child_environment()
    source.git(repo, env, "init", "--quiet")
    (repo / "sensitive_credentials.py").write_text("value = 17\n")
    (repo / "app.py").write_text("value = 1\n")
    original = commit(repo)
    source.git(repo, env, "mv", "sensitive_credentials.py", "renamed.py")
    (repo / "app.py").write_text("value = 2\n")
    head = commit(repo)
    allowed = source.review_paths(repo, env, original, head, {"app.py", "renamed.py"}, {}, lambda: False)
    assert allowed == {"app.py"}
    # Even a permissive preview cannot restore the excluded rename into model evidence.
    monkeypatch.setattr(verification, "filter_context", lambda repo, env, sources, options, stopped: set(sources))
    finding = {"path": "app.py", "source_sha": original, "existing_code": "value = 1"}
    evidence = verification.build_context(repo, env, env, finding, original, head, {}, lambda: False)
    assert "renamed.py" not in evidence["diff"]
    assert "sensitive_credentials.py" not in evidence["diff"]
    assert "renamed.py" in evidence["excluded_paths"]
    with pytest.raises(verification.ResultValidationError, match="excluded"):
        verification.expand_context(repo, env, evidence, [{"revision": "current", "path": "renamed.py"}], {}, lambda: False)
    run = SimpleNamespace(base_sha=original, head_sha=head,
        config={"settings": {"language": "English"}, "connection": {}},
        report={"manifest": {"coverage": {"selected": [{"path": path} for path in ("app.py", "renamed.py")]}}})
    summary = pr_summary.build_context(repo, env, run, lambda: False)
    assert summary["paths"] == ["app.py"] and "renamed.py" not in summary["diff"]
    with pytest.raises(ValueError, match="excluded"):
        verification.read_source(repo, env, original, "sensitive_credentials.py", lambda: False)
    with pytest.raises(ValueError, match="excluded"):
        verification.build_context(repo, env, env, {"path": "sensitive_credentials.py"}, original, head, {}, lambda: False)
