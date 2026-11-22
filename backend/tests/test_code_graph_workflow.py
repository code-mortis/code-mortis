import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from code_mortis import jobs
from code_mortis.code_graph import client, service, store
from code_mortis.code_graph.extract import extract
from code_mortis.core import process
from code_mortis.core.config import settings
from code_mortis.core.db import engine, now, transaction
from code_mortis.integrations import github
from code_mortis.models import (
    CodeGraphFile,
    CodeGraphImport,
    CodeGraphRepository,
    CodeGraphSnapshot,
    CodeGraphSymbol,
    Installation,
    Job,
    Membership,
    Repository,
    User,
)


def fixture_git(root, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root, env=env).strip().decode()


@pytest.fixture
def graph(seed, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "code_graph_store_dir", tmp_path / "objects")
    root = tmp_path / "fixture"
    root.mkdir()
    fixture_git(root, "init", "-q")
    fixture_git(root, "config", "user.name", "Fixture")
    fixture_git(root, "config", "user.email", "fixture@example.invalid")
    (root / "a.py").write_text("def first():\n    return 1\n")
    (root / "skip.py").write_text("def hidden():\n    return 2\n")
    (root / ".env").write_text("SECRET=never-parser")
    (root / "link.py").symlink_to(".env")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "first")
    first = fixture_git(root, "rev-parse", "HEAD")
    (root / "a.py").unlink()
    (root / "renamed.py").write_text("def second():\n    return 3\n")
    fixture_git(root, "add", "-A")
    fixture_git(root, "commit", "-qm", "second")
    second = fixture_git(root, "rev-parse", "HEAD")
    state = {"head": first, "source": root, "first": first, "second": second, "requests": [], "fresh": []}

    def permission(db, user, name, write=False, *, fresh=False):
        state["fresh"].append(fresh)
        return {"id": 22, "default_branch": "main", "permissions": {"pull": True}}

    monkeypatch.setattr(github, "authorize_repo", permission)
    monkeypatch.setattr(github, "repository_read_token", lambda *args, **kwargs: "fixture-token")
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: {"sha": state["head"]})

    def acquire(repository_id, name, sha, snapshot_id, token, stopped):
        stopped()
        target = store.directory(repository_id)
        if not target.exists():
            fixture_git(root, "clone", "-q", "--bare", str(root), str(target))
        store.git(target, "update-ref", store.reference(snapshot_id), sha)

    monkeypatch.setattr(store, "acquire", acquire)

    def parser(payload, stopped):
        stopped()
        state["requests"].append(payload)
        return extract(payload)

    monkeypatch.setattr(client, "extract", parser)
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "exclusions": "skip.py"}
    service.configure(22, 7, enabled=True)
    return state


def run(snapshot_id):
    with transaction() as db:
        job_id = db.get(CodeGraphSnapshot, snapshot_id).job_id
    jobs.execute_job(job_id)
    with transaction() as db:
        return db.get(CodeGraphSnapshot, snapshot_id), db.get(Job, job_id)


def claim(snapshot_id):
    with transaction() as db:
        job = db.get(Job, db.get(CodeGraphSnapshot, snapshot_id).job_id)
        job.status, job.lease_until = "running", now() + timedelta(seconds=600)
        job.attempts += 1
        return job.id, job.attempts


def test_index_publishes_python_absolute_imports(graph):
    root = graph["source"]
    package = root / "backend/code_mortis"
    (package / "schemas").mkdir(parents=True)
    for name in ("__init__.py", "schemas/__init__.py", "schemas/code_graph.py"):
        (package / name).touch()
    (package / "worker.py").write_text("from code_mortis.schemas.code_graph import EXTRACTOR_VERSION\n")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "repository-local Python module")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")

    row, job = run(service.enqueue_snapshot(22))

    assert (row.status, job.status) == ("ready", "done")
    with transaction() as db:
        edge = db.scalar(select(CodeGraphImport).where(CodeGraphImport.snapshot_id == row.id))
        assert (edge.file_path, edge.target_path, edge.provenance) == (
            "backend/code_mortis/worker.py", "backend/code_mortis/schemas/code_graph.py", "absolute_path",
        )


def test_default_off_and_existing_grants_do_not_enable_code(seed, monkeypatch):
    with pytest.raises(service.AccessLost):
        service.enqueue_snapshot(22, "a" * 40)
    monkeypatch.setattr(service.pat, "current_provenance", lambda: {"token_id": "old-review-token"})
    with pytest.raises(service.AccessLost, match="do not authorize"):
        service.configure(22, 7, enabled=True)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphRepository)) == 0


def test_concurrent_admission_and_immutable_success(graph):
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(lambda _: service.enqueue_snapshot(22), range(4)))
    assert len(set(identities)) == 1
    row, job = run(identities[0])
    assert (row.status, job.status, job.attempts) == ("ready", "done", 1)
    assert service.enqueue_snapshot(22, retry=True) == row.id
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "code_graph")) == 1
        assert list(db.scalars(select(CodeGraphSymbol.name))) == ["first"]
    assert all(graph["fresh"]) and len(graph["fresh"]) >= 7
    manifest = {f.path: f for f in graph["requests"][0].files}
    for name in (".env", "skip.py", "link.py"):
        assert manifest[name].content_base64 is None and manifest[name].exclusion == "excluded"
    assert service.source(row.id, 7, "a.py") == b"def first():\n    return 1\n"
    with pytest.raises(ValueError, match="unavailable"):
        service.source(row.id, 7, "skip.py")


def test_full_rebuild_preserves_exact_old_revision_and_handles_deletion(graph):
    first, _ = run(service.enqueue_snapshot(22))
    graph["head"] = graph["second"]
    second, _ = run(service.enqueue_snapshot(22))
    assert second.status == "ready"
    assert service.source(first.id, 7, "a.py").startswith(b"def first")
    assert service.source(second.id, 7, "renamed.py").startswith(b"def second")
    with pytest.raises(ValueError, match="unavailable"):
        service.source(second.id, 7, "a.py")


def test_changed_policy_creates_identity_and_blocks_old_source(graph):
    first, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "exclusions": "a.py,skip.py"}
    second, _ = run(service.enqueue_snapshot(22))
    assert second.id != first.id and second.policy_digest != first.policy_digest
    with pytest.raises(ValueError, match="current policy"):
        service.source(first.id, 7, "a.py")


@pytest.mark.parametrize("loss", ["user", "membership", "installation", "repository", "disable", "policy", "github"])
def test_access_loss_during_extraction_prevents_publication(graph, monkeypatch, loss):
    identity = service.enqueue_snapshot(22)

    def parser(payload, stopped):
        result = extract(payload)
        if loss == "disable":
            service.configure(22, 7, enabled=False)
        elif loss == "github":
            def denied(*args, **kwargs):
                raise github.GitHubError(403, "denied")
            monkeypatch.setattr(github, "authorize_repo", denied)
        else:
            with transaction() as db:
                if loss == "user":
                    db.get(User, 7).enabled = False
                elif loss == "membership":
                    db.delete(db.get(Membership, 7))
                elif loss == "installation":
                    db.get(Installation, 10).active = False
                elif loss == "repository":
                    db.get(Repository, 22).active = False
                else:
                    db.get(Repository, 22).settings = {"exclusions": "**/*.py"}
        return result

    monkeypatch.setattr(client, "extract", parser)
    row, job = run(identity)
    assert row.status == "cancelled" and job.status == "failed"
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphFile)) == 0
    assert not store.git(store.directory(22), "for-each-ref", "--format=%(refname)", "refs/code-mortis/").strip()


@pytest.mark.parametrize("stage", ["fetch", "parse", "validation"])
def test_failure_preserves_prior_success_and_retry_is_explicit(graph, monkeypatch, stage):
    first, _ = run(service.enqueue_snapshot(22))
    graph["head"] = graph["second"]
    identity = service.enqueue_snapshot(22)
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(jobs.sentry_sdk, "capture_exception", lambda exc, **kwargs: captured.append(exc))
        def broken(*args, **kwargs):
            raise ValueError("fixture interrupted")
        if stage == "fetch":
            patch.setattr(store, "acquire", broken)
        elif stage == "parse":
            patch.setattr(client, "extract", broken)
        else:
            patch.setattr(client, "extract", lambda payload, **kw: extract(payload).model_copy(update={"commit_sha": "a" * 40}))
        row, job = run(identity)
    assert row.status == "failed" and job.status == "failed"
    assert len(captured) == 1 and captured[0].__cause__ is None and captured[0].__suppress_context__
    assert service.enqueue_snapshot(22) == identity
    assert service.source(first.id, 7, "a.py").startswith(b"def first")
    with transaction() as db:
        assert db.get(CodeGraphSnapshot, identity).job_id == job.id
        assert db.scalar(select(func.count()).select_from(CodeGraphFile).where(CodeGraphFile.snapshot_id == identity)) == 0
    assert service.enqueue_snapshot(22, retry=True) == identity
    row, retry_job = run(identity)
    assert row.status == "ready" and retry_job.id != job.id


def test_expired_lease_recovery_fences_old_worker(graph, monkeypatch):
    identity = service.enqueue_snapshot(22)
    job_id, attempt = claim(identity)

    def expired(payload, stopped):
        with transaction() as db:
            db.get(Job, job_id).lease_until = now() - timedelta(seconds=1)
        monkeypatch.setattr(jobs.execute_job, "apply_async", lambda *args, **kwargs: None)
        jobs.dispatch()
        return extract(payload)

    with monkeypatch.context() as patch:
        patch.setattr(client, "extract", expired)
        with pytest.raises(ValueError, match="did not finish"):
            service.perform(identity, job_id, attempt)
    with transaction() as db:
        assert db.get(CodeGraphSnapshot, identity).status == "queued"
        assert db.scalar(select(func.count()).select_from(CodeGraphSymbol)) == 0
    row, job = run(identity)
    assert row.status == "ready" and job.attempts == 2
    with pytest.raises(ValueError):
        service.perform(identity, job_id, attempt)
    assert service.source(identity, 7, "a.py").startswith(b"def first")


def test_cancellation_and_policy_change_before_acquisition(graph, monkeypatch):
    identity = service.enqueue_snapshot(22)
    service.cancel(identity, 7)
    row, _ = run(identity)
    assert row.status == "cancelled" and not graph["requests"]
    service.enqueue_snapshot(22, retry=True)
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "a.py"}
    row, _ = run(identity)
    assert row.status == "cancelled" and not graph["requests"]


def test_instance_serialization_requeues_without_consuming_attempt(graph):
    identity = service.enqueue_snapshot(22)
    with store.locked():
        row, job = run(identity)
    assert row.status == "queued" and job.status == "queued" and job.attempts == 0
    assert jobs.job_queue("code_graph") == jobs.job_queue("code_graph_cleanup") == "code-graph"


def test_retention_expiry_and_consumer_lock(graph):
    first, _ = run(service.enqueue_snapshot(22))
    graph["head"] = graph["second"]
    second, _ = run(service.enqueue_snapshot(22))
    with store.locked(), pytest.raises(store.RunnerBlocked):
        service.cleanup()
    with transaction() as db:
        db.get(CodeGraphSnapshot, first.id).finished_at = now() - timedelta(days=8)
    service.cleanup()
    with transaction() as db:
        assert db.get(CodeGraphSnapshot, first.id) is None
        assert db.get(CodeGraphSnapshot, second.id).status == "ready"
    refs = store.git(store.directory(22), "for-each-ref", "--format=%(refname)", "refs/code-mortis/").decode()
    assert first.id not in refs and second.id in refs


def test_missing_or_wrong_retained_objects_never_fall_back(graph):
    row, _ = run(service.enqueue_snapshot(22))
    store.git(store.directory(22), "update-ref", store.reference(row.id), graph["second"])
    with pytest.raises(ValueError, match="revision"):
        service.source(row.id, 7, "a.py")
    shutil.rmtree(store.directory(22))
    with pytest.raises((ValueError, FileNotFoundError)):
        service.source(row.id, 7, "a.py")


def test_fixed_identity_is_checked_before_job_admission(graph, monkeypatch):
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: {"id": 999, "default_branch": "main"})
    with pytest.raises(service.AccessLost, match="identity"):
        service.enqueue_snapshot(22)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphSnapshot)) == 0


def test_acquisition_uses_only_fixed_https_credentials_and_safe_git_env(seed, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "code_graph_store_dir", tmp_path)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-inherit")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "99")
    calls = []

    def command(args, root, env, **kwargs):
        calls.append((args, env))
        return 0, b"commit\n" if "cat-file" in args else b"", b""

    monkeypatch.setattr(store.process, "command", command)
    store.acquire(22, "team/project", "a" * 40, "00000000-0000-0000-0000-000000000001", "fixture-secret", lambda: False)
    args, env = next(c for c in calls if "fetch" in c[0])
    assert "https://github.com/team/project.git" in args and "--no-write-fetch-head" in args
    assert "fixture-secret" not in " ".join(args)
    assert "AWS_SECRET_ACCESS_KEY" not in env and env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert "http.followRedirects=false" in args and "core.hooksPath=/dev/null" in args
    assert not any("checkout" in c[0] for c in calls)


def test_binary_command_preserves_source_bytes(tmp_path):
    code, raw, _ = process.command([sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes([255,13,10,0]))"],
                               tmp_path, os.environ.copy(), binary=True)
    assert code == 0 and raw == b"\xff\r\n\0"


@pytest.mark.parametrize("pulls", [False, True])
def test_graph_token_is_scoped_to_one_repository_and_read_permissions(seed, monkeypatch, pulls):
    calls = []
    monkeypatch.setattr(github, "app_token", lambda db: "fixture-jwt")
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: calls.append((args, kwargs)) or {"token": "fixture"})
    with transaction() as db:
        assert github.repository_read_token(db, 10, 22, pulls=pulls) == "fixture"
    permissions = {"contents": "read", "metadata": "read"}
    if pulls:
        permissions["pull_requests"] = "read"
    assert calls == [(("POST", "/app/installations/10/access_tokens", "fixture-jwt"), {
        "json": {"repository_ids": [22], "permissions": permissions},
    })]


def test_publication_is_one_transaction(graph):
    identity = service.enqueue_snapshot(22)

    def fail_symbols(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO code_graph_symbols"):
            raise RuntimeError("Interrupted atomic insert")

    event.listen(engine(), "before_cursor_execute", fail_symbols)
    try:
        row, job = run(identity)
    finally:
        event.remove(engine(), "before_cursor_execute", fail_symbols)
    assert row.status == job.status == "failed"
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphFile)) == 0
        assert db.scalar(select(func.count()).select_from(CodeGraphSymbol)) == 0


@pytest.mark.parametrize("published", [False, True])
def test_recovery_finishes_published_work_and_bounds_restarts(graph, monkeypatch, published):
    identity = service.enqueue_snapshot(22)
    if published:
        row, job = run(identity)
        job_id = job.id
    else:
        job_id, _ = claim(identity)
    with transaction() as db:
        job = db.get(Job, job_id)
        job.status, job.attempts, job.lease_until = "running", 3, now() - timedelta(seconds=1)
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda *args, **kwargs: None)
    jobs.dispatch()
    with transaction() as db:
        assert db.get(Job, job_id).status == ("done" if published else "failed")
        assert db.get(CodeGraphSnapshot, identity).status == ("ready" if published else "failed")


def test_pending_capacity_and_extractor_version_changes(graph, monkeypatch):
    first = service.enqueue_snapshot(22)
    monkeypatch.setattr(service, "EXTRACTOR_VERSION", "tree-sitter/test-v2")
    second = service.enqueue_snapshot(22)
    assert second != first
    with pytest.raises(ValueError, match="capacity"):
        service.enqueue_snapshot(22, graph["second"])


def test_retention_keeps_recent_revisions_and_prunes_orphan_refs(graph):
    default, _ = run(service.enqueue_snapshot(22))
    other, _ = run(service.enqueue_snapshot(22, graph["second"]))
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "skip.py,missing.py"}
    newest, _ = run(service.enqueue_snapshot(22, graph["second"]))
    with transaction() as db:
        assert db.get(CodeGraphSnapshot, default.id) is not None
        assert db.get(CodeGraphSnapshot, other.id) is not None
        assert db.get(CodeGraphSnapshot, newest.id) is not None
        db.delete(db.get(CodeGraphRepository, 22))
        for row in db.scalars(select(CodeGraphSnapshot)):
            db.delete(row)
    service.cleanup()
    assert not store.git(store.directory(22), "for-each-ref", "--format=%(refname)", "refs/code-mortis/").strip()


@pytest.mark.parametrize("old_first", [True, False])
def test_default_retention_follows_observations_not_completion_order(graph, old_first):
    old = service.enqueue_snapshot(22)
    graph["head"] = graph["second"]
    current = service.enqueue_snapshot(22)
    order = [old, current] if old_first else [current, old]
    assert run(order[0])[0].status == "ready"
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).default_sha == (graph["first"] if old_first else graph["second"])
    assert run(order[1])[0].status == "ready"
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).default_sha == graph["second"]
        for identity in order:
            db.get(CodeGraphSnapshot, identity).finished_at = now() - timedelta(days=8)
    service.cleanup()
    with transaction() as db:
        assert db.get(CodeGraphSnapshot, old) is None
        assert db.get(CodeGraphSnapshot, current).status == "ready"


def test_reused_default_pin_survives_old_completion_and_new_head_failure(graph, monkeypatch):
    # This snapshot was first built for a PR, before it became the default branch.
    current, _ = run(service.enqueue_snapshot(22, graph["second"]))
    old = service.enqueue_snapshot(22)
    graph["head"] = graph["second"]
    assert service.enqueue_snapshot(22) == current.id
    (graph["source"] / "renamed.py").write_text("def third():\n    pass\n")
    fixture_git(graph["source"], "commit", "-qam", "third default")
    graph["head"] = fixture_git(graph["source"], "rev-parse", "HEAD")
    pending = service.enqueue_snapshot(22)
    assert run(old)[0].status == "ready"
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).default_sha == graph["second"]
    with monkeypatch.context() as patch:
        def failed(*args, **kwargs):
            raise ValueError("parser unavailable")
        patch.setattr(client, "extract", failed)
        assert run(pending)[0].status == "failed"
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).default_sha == graph["second"]
    # An explicit rollback to an already indexed commit is a newer observation.
    graph["head"] = graph["first"]
    assert service.enqueue_snapshot(22) == old
    with transaction() as db:
        assert db.get(CodeGraphRepository, 22).default_sha == graph["first"]


def test_retention_caps_recent_revisions_without_evicting_default(graph):
    root, commits = graph["source"], [graph["second"]]
    for index in range(service.MAX_RECENT_SNAPSHOTS):
        (root / "renamed.py").write_text(f"def second():\n    return {index + 4}\n")
        fixture_git(root, "commit", "-qam", f"revision {index}")
        commits.append(fixture_git(root, "rev-parse", "HEAD"))
    default, _ = run(service.enqueue_snapshot(22))
    revisions = [run(service.enqueue_snapshot(22, sha))[0] for sha in commits]
    with transaction() as db:
        assert db.get(CodeGraphSnapshot, revisions[0].id) is None
        assert db.get(CodeGraphSnapshot, default.id) is not None
        assert db.scalar(select(func.count()).select_from(CodeGraphSnapshot)) == service.MAX_RECENT_SNAPSHOTS + 1
        assert all(db.get(CodeGraphSnapshot, row.id) is not None for row in revisions[1:])
    assert service.source(revisions[1].id, 7, "renamed.py") == b"def second():\n    return 4\n"
    refs = store.git(store.directory(22), "for-each-ref", "--format=%(refname)", "refs/code-mortis/").decode()
    assert revisions[0].id not in refs and default.id in refs
    assert all(row.id in refs for row in revisions[1:])


def test_cancellation_while_publication_waits_for_row_lock(graph):
    identity = service.enqueue_snapshot(22)
    cancelled = False

    def cancel_before_lock(conn, cursor, statement, parameters, context, executemany):
        nonlocal cancelled
        if not cancelled and statement.startswith("SELECT code_graph_snapshots.") and "FOR UPDATE" in statement:
            cancelled = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(service.cancel, identity, 7).result(timeout=5)

    event.listen(engine(), "before_cursor_execute", cancel_before_lock)
    try:
        row, _ = run(identity)
    finally:
        event.remove(engine(), "before_cursor_execute", cancel_before_lock)
    assert cancelled and row.status == "cancelled"
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(CodeGraphFile)) == 0


def test_ready_snapshot_reports_unsupported_formats_without_claiming_complete_coverage(graph):
    root = graph["source"]
    (root / "README.txt").write_text("Unsupported syntax is still recorded honestly.")
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "unsupported")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")
    row, job = run(service.enqueue_snapshot(22))
    assert row.status == "ready" and job.status == "done"
    with transaction() as db:
        assert db.get(CodeGraphFile, (row.id, "README.txt")).status == "unsupported"
        coverage = service.coverage(db, row)
        assert coverage.unsupported == 1 and not coverage.complete and coverage.visible_files == 2
        assert coverage.reasons == {"unsupported_language": 1}


@pytest.mark.parametrize("source,reason", [(b"def broken(\n", "syntax_error"), (b"\xff", "invalid_encoding"),
                                          (b"x" * (1024 * 1024 + 1), "file_too_large")], ids=["syntax", "encoding", "size"])
def test_eligible_file_gaps_produce_partial_snapshot_and_reason_counts(graph, source, reason):
    root = graph["source"]
    (root / "problem.py").write_bytes(source)
    fixture_git(root, "add", ".")
    fixture_git(root, "commit", "-qm", "warning")
    graph["head"] = fixture_git(root, "rev-parse", "HEAD")
    row, job = run(service.enqueue_snapshot(22))
    assert row.status == "partial" and job.status == "done"
    with transaction() as db:
        coverage = service.coverage(db, row)
        assert coverage.reasons == {reason: 1} and not coverage.complete
        assert coverage.visible_files == 2 and coverage.parsed == 1
        if reason != "file_too_large":
            assert [(file.path, file.reason) for file in coverage.warning_files] == [("problem.py", reason)]
            assert bool(coverage.warning_files[0].diagnostic) == (reason == "syntax_error")


def test_coverage_warning_files_are_bounded_sorted_and_obey_exclusions(graph):
    row, _ = run(service.enqueue_snapshot(22))
    with transaction() as db:
        current = db.get(CodeGraphSnapshot, row.id)
        for index in reversed(range(25)):
            db.add(CodeGraphFile(snapshot_id=row.id, path=f"problem-{index:02}.py", blob_sha="a" * 40,
                                 size_bytes=10, language="python", status="partial", reason="syntax_error", line_count=1))
        hidden = db.get(CodeGraphFile, (row.id, "skip.py"))
        hidden.language, hidden.status, hidden.reason, hidden.line_count = "python", "partial", "syntax_error", 1
        db.flush()
        result = service.coverage(db, current)
        assert result.partial == 25
        assert [file.path for file in result.warning_files] == [f"problem-{index:02}.py" for index in range(20)]
        assert all(file.diagnostic is None for file in result.warning_files)


def test_disk_monitor_tolerates_git_pack_rename_and_enforces_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "code_graph_store_dir", tmp_path)
    pack = tmp_path / "pack.tmp"
    pack.write_bytes(b"1234")
    original, calls = Path.stat, 0

    def moving(path, *args, **kwargs):
        nonlocal calls
        if path == pack:
            calls += 1
            if calls == 2:
                raise FileNotFoundError("Git renamed the temporary pack")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", moving)
    store.disk_limit(tmp_path)
    monkeypatch.setattr(store, "MAX_REPOSITORY_BYTES", 3)
    with pytest.raises(ValueError, match="disk limit"):
        store.disk_limit(tmp_path)
