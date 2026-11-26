import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from runtime_helpers import ask_security, perform_security
from sqlalchemy import event, select

from code_mortis import jobs
from code_mortis.api_tokens import provenance as pat_provenance
from code_mortis.core import workflow_inference
from code_mortis.core.db import engine, now, transaction, uid
from code_mortis.core.errors import RunnerBlocked
from code_mortis.core.security import digest
from code_mortis.integrations import github, llm
from code_mortis.models import (
    Connection,
    ConnectionGrant,
    Job,
    Membership,
    Repository,
    SecurityActivity,
    SecurityFinding,
    SecurityOccurrence,
    SecurityScan,
    User,
)
from code_mortis.reviews.service import connection_snapshot
from code_mortis.schemas.inference import Message, Request
from code_mortis.schemas.security import SecurityScanInput
from code_mortis.security_scans import engine as security_engine
from code_mortis.security_scans import issues as security_issues
from code_mortis.security_scans import service as security

SHA = "a" * 40
OLD = "b" * 40
SCAN_CITATIONS = json.loads((Path(__file__).parent / "fixtures/security_citation_failures.json").read_text())["cases"]
DEPENDENCY_ADVISORY = json.loads((Path(__file__).parent / "fixtures/security_dependency_advisory.json").read_text())


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("credential", ["synthetic-雪-credential", "synthetic-first\nsecond-line"])
def test_security_outgoing_history_is_redacted_without_mutating_saved_context(tmp_path, monkeypatch, kind, credential):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.secret_values.append(credential)
    item.inventory = {"app.py": {}}
    sent = []
    response = {"verdict": "uncertain", "finding_id": None, "explanation": "Missing caller", "evidence": [], "required_paths": []}

    def api(*args, **kwargs):
        sent.append(security_engine.verification.evidence_bundle(args[2]))
        if len(sent) == 1:
            raise security_engine.verification.VerificationOutputError("Correction needed", {"tokens": 2},
                feedback={"kind": "schema", "errors": [{"msg": credential}]})
        return kwargs["validator"](response, args[2]), {"tokens": 1}


    monkeypatch.setattr(workflow_inference, "execute", api)
    context = {"sources": {"current": {"app.py": "safe"}}, "diff": credential,
        "proposal": {"path": "app.py", "explanation": credential},
        "history": [{"id": "existing", "path": "app.py", "evidence": {"quote": credential}}]}
    original = deepcopy(context)
    _, usage = ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {},
        "assessment", context, 1000, 60, lambda: False, uid())
    assert context == original
    assert sent and all(credential not in text and json.dumps(credential)[1:-1] not in text for text in sent)
    assert usage["tokens"] == (3 if kind == "api" else 1)


def test_historical_secret_fingerprint_redaction_preserves_lines_and_stops(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    credential = "雪é-first\nsecond-line"
    item.secret_rules = [{"rule_id": "fixture", "match_bytes": len(credential.encode()),
                          "identity": security_engine.secure_digest("fixture\0" + credential)}]
    item.read = lambda *args: "before\n" + credential + "\nafter"
    assert item.source("app.py") == "before\n[redacted]\n[redacted]\nafter"
    assert credential in item.secret_values
    item.secret_values.clear()
    item.stopped = lambda: True
    with pytest.raises(security.ReviewStopped):
        item.redact("x" * 10000)


def test_secret_in_structural_path_withholds_dispatch(tmp_path, monkeypatch):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.secret_values = ["synthetic-credential"]
    path = "synthetic-credential.py"
    item.inventory = {path: {}}
    monkeypatch.setattr(workflow_inference, "execute", lambda *args, **kwargs: pytest.fail("Must not dispatch"))
    with pytest.raises(security.verification.VerificationOutputError) as error:
        ask_security(item, {"id": "connection", "kind": "api", "model": "test"}, {}, "analysis",
            {"assigned_path": path, "sources": {"current": {path: "safe"}}, "diff": ""}, 1000, 60, lambda: False, uid())
    assert error.value.usage["tokens"] == 0
    assert error.value.feedback["kind"] == "redaction"


@pytest.mark.parametrize("state, expected", [("false_positive", "completed"), ("open", "partial")])
def test_missing_historical_secret_commit_does_not_abort_current_analysis(seed, security_github, monkeypatch, tmp_path, state, expected):
    credential = "synthetic-old-雪-credential"
    source = "prefix " + credential + "\nsafe"
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {"code": True, "hash": digest(source), "lines": 2}}

    def read(path, sha=None):
        if sha == OLD:
            raise ValueError("Original commit unavailable")
        return source

    item.read = read
    item.renamed = lambda sha, path: path
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    calls = []

    def ask(*args, **kwargs):
        calls.append(args[4])
        assert credential not in json.dumps(args[4], ensure_ascii=False)
        return {"findings": [], "required_paths": []}, {"tokens": 1}

    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        earlier = saved_scan(db, seed, status="completed")
        finding_id = saved_finding(db, seed, earlier, kind="secret", state=state, match_offset=0,
            match_bytes=len(credential.encode()), rule_id="fixture", identity=security_engine.secure_digest("fixture\0" + credential)).id
        scan_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid())).id
    perform_security(scan_id, "audit:1")
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        assert scan.status == expected, scan.error
        assert calls and scan.progress["tokens"] == 1
        assert db.get(SecurityFinding, finding_id).state == state


@pytest.mark.parametrize("tokens", [123, None])
def test_freshness_refresh_serializes_with_checkpoint(seed, security_github, monkeypatch, tokens):
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan.progress = {**scan.progress, "execution": "worker:1"}
        scan_id = scan.id
    loaded, recorded = Event(), Event()
    real_transaction = security.transaction
    refresh_thread = get_ident()

    @contextmanager
    def interleaved_transaction():
        with real_transaction() as db:
            if get_ident() != refresh_thread:
                yield db
                return
            class Session:
                def __getattr__(self, name):
                    return getattr(db, name)

                def get(self, model, identity):
                    row = db.get(model, identity)
                    if model is SecurityScan:
                        loaded.set()
                        assert recorded.wait(5)
                    return row

                def scalar(self, statement):
                    # Pause before a locking read so the completed checkpoint is visible.
                    if statement._for_update_arg is not None:
                        loaded.set()
                        assert recorded.wait(5)
                    return db.scalar(statement)
            yield Session()

    def checkpoint():
        assert loaded.wait(5)
        security.checkpoint(scan_id, "analysis:app.py", "completed", tokens=tokens, result={"findings": []})
        recorded.set()

    monkeypatch.setattr(security, "transaction", interleaved_transaction)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(checkpoint)
        security.refresh_repository(seed["repo"].id)
        future.result()
    with real_transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        assert scan.progress["tokens"] == tokens
        assert scan.progress["tasks"]["analysis:app.py"]["status"] == "completed"
        assert scan.progress["execution"] == "worker:1"


def test_older_freshness_refresh_cannot_replace_newer_completion(seed, security_github, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan_id = scan.id
    newer = {**freshness(), "checked_at": (now() + timedelta(seconds=5)).isoformat()}

    def head(*args):
        with transaction() as db:
            row = db.get(SecurityScan, scan_id)
            row.progress = {**row.progress, "freshness": newer, "tokens": None}
            row.status = "completed"
        return freshness("c" * 40)

    monkeypatch.setattr(security, "branch_head", head)
    security.refresh_repository(seed["repo"].id)
    with transaction() as db:
        row = db.get(SecurityScan, scan_id)
        assert row.status == "completed" and row.progress["tokens"] is None
        assert row.progress["freshness"] == newer


@pytest.mark.parametrize("directory", [".", "infra"])
def test_trivy_terraform_directory_summaries_are_separate_from_file_evidence(tmp_path, directory):
    path = "main.tf" if directory == "." else "infra/main.tf"
    item = SimpleNamespace(files=tmp_path, inventory={path: {"lines": 1, "hash": digest("source")}},
        sha=SHA, redact=lambda text: text)
    report = {"Results": [
        {"Target": directory, "Class": "config", "Type": "terraform", "MisconfSummary": {"Successes": 1},
         "Misconfigurations": [{"ID": "fixture", "Status": "PASS"}]},
        {"Target": path, "Class": "config", "Type": "terraform", "Misconfigurations": [
            {"ID": "fixture", "Status": "FAIL", "Title": "Unsafe group", "CauseMetadata": {"Resource": "group.unsafe"}}]},
    ]}
    result = security_engine.normalize_trivy(item, report)
    assert not result["problems"]
    assert result["directory_summaries"][0]["path"] == directory
    assert result["findings"][0]["path"] == path
    assert result["targets"][path]["checks"] == [{"id": "fixture", "status": "FAIL", "resource": "group.unsafe"}]
    assert directory not in result["targets"]


@pytest.mark.parametrize("directory", ["../outside", "/outside", "missing", "infra/../infra"])
def test_trivy_directory_summaries_do_not_loosen_path_validation(tmp_path, directory):
    item = SimpleNamespace(files=tmp_path, inventory={"infra/main.tf": {}}, sha=SHA, redact=lambda text: text)
    with pytest.raises(ValueError):
        security_engine.normalize_trivy(item, {"Results": [{"Target": directory, "Class": "config", "Type": "terraform"}]})


def test_unmapped_directory_failure_is_explicitly_incomplete(tmp_path):
    item = SimpleNamespace(files=tmp_path, inventory={"main.tf": {"hash": "hash"}}, sha=SHA, redact=lambda text: text)
    result = security_engine.normalize_trivy(item, {"Results": [{"Target": ".", "Class": "config", "Type": "terraform",
        "Misconfigurations": [{"ID": "fixture", "Status": "FAIL"}]}]})
    assert result["problems"] and result["directory_summaries"]
    assert result["findings"] == []


def test_other_resource_pass_cannot_resolve_infrastructure_finding(monkeypatch):
    item = SimpleNamespace(sha=SHA, git=None, env={}, stopped=lambda: False, renamed=lambda sha, path: path,
        diff=lambda sha, **kwargs: "changed", read=lambda path, sha=None: "unsafe" if sha else "unsafe\nunrelated change")
    monkeypatch.setattr(security.git_source, "git", lambda *args, **kwargs: "100644 blob object\tmain.tf")
    old = {"path": "main.tf", "evidence": {"kind": "infrastructure", "source_sha": OLD,
        "rule_id": "fixture", "resource": "group.unsafe"}}
    result = security.deterministic_fix(item, old, {"targets": {"main.tf": {
        "checks": [{"id": "fixture", "status": "PASS", "resource": "group.safe"}]}}})
    assert result["verdict"] == "uncertain"


@pytest.mark.parametrize("budget", [None, 1200])
@pytest.mark.parametrize("cancel", [False, True])
def test_live_scan_counts_each_checkpoint_before_its_batch_settles(seed, security_github, monkeypatch, tmp_path, budget, cancel):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {path: {"code": True, "hash": digest("safe"), "lines": 1} for path in ("fast.py", "slow.py")}
    item.read = lambda *args: "safe"
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {
        "findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True},
    })
    slow_started, fast_saved, release = Event(), Event(), Event()

    def ask(*args, **kwargs):
        if args[4]["assigned_path"] == "slow.py":
            slow_started.set()
            assert release.wait(10)
        return {"findings": [], "required_paths": []}, {"tokens": 12}

    original_checkpoint = security.checkpoint

    def checkpoint(scan_id, key, state, **values):
        task = original_checkpoint(scan_id, key, state, **values)
        if (key, state) == ("analysis:fast.py", "completed"):
            fast_saved.set()
            assert release.wait(10)
        return task

    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(security, "checkpoint", checkpoint)
    with transaction() as db:
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=2, token_budget=budget,
        )).id
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(perform_security, identity, "test:1")
        try:
            assert slow_started.wait(10) and fast_saved.wait(10)
            with transaction() as db:
                scan = db.get(SecurityScan, identity)
                assert scan.stages["analysis"]["completed"] == 0
                public = security.public_scan(scan)
                assert public.stages["analysis"]["completed"] == public.progress["work"]["analysis_completed"] == 1
                assert scan.stages["analysis"]["completed"] == 0
                if cancel:
                    security.cancel(db, scan)
                    public = security.public_scan(scan)
                    assert public.stages["analysis"] == {**scan.stages["analysis"], "status": "cancelled", "completed": 1}
        finally:
            release.set()
        worker.result(timeout=10)
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == ("cancelled" if cancel else "completed"), scan.error
        assert security.public_scan(scan).stages["analysis"]["completed"] == (1 if cancel else 2)
        assert scan.progress["tasks"]["analysis:fast.py"]["status"] == "completed"


@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("obsolete", [False, True])
@pytest.mark.parametrize("denied_context", [None, "context_paths", "source_navigation", "unproven_packing", "proven_packing"])
def test_security_retry_reuses_cached_remainder_at_exhausted_budget(
    seed, security_github, monkeypatch, tmp_path, concurrency, obsolete, denied_context,
):
    paths = ["fresh.py", "cached.py", "cached2.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest("safe"), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda *args: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    calls = []

    def ask(*args, **kwargs):
        assert kwargs.get("source_packing") is None
        assert kwargs.get("navigation") is None
        calls.append((args[4]["assigned_path"], args[5]))
        return {"findings": [], "required_paths": []}, {"tokens": 1000}

    monkeypatch.setattr(security_engine, "ask", ask)
    body = {"connection_id": "connection", "token_budget": 1000, "concurrency": concurrency}
    with transaction() as db:
        parent = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(**body, request_key=uid()))
        config = {**parent.config, "limits": {key: value for key, value in parent.config["limits"].items() if key != "concurrency"}}
        tasks = {}
        for path in paths[1:]:
            context = {"assigned_path": path, "head_sha": SHA, "diff": "", "sources": {"current": {path: "safe"}},
                "language": "English", "task_contract": "obsolete" if obsolete else config.get("task_contract", "security-task/v2")}
            tasks["analysis:" + path] = {"status": "completed", "tokens": 1,
                "input_hash": digest(json.dumps([config, context], sort_keys=True)), "result": {"findings": [], "required_paths": []}}
            if denied_context in {"unproven_packing", "proven_packing"}:
                tasks["analysis:" + path]["source_packing"] = True
                if denied_context == "proven_packing":
                    tasks["analysis:" + path]["context_paths"] = [path]
            elif denied_context:
                tasks["analysis:" + path].update(source_packing=True, **{
                    denied_context: [path, "uv.lock"] if denied_context == "context_paths" else {"hints": [{"path": "uv.lock"}]}})
        parent.status, parent.progress = "partial", {"tokens": 2, "tasks": tasks}
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(**body, request_key=uid()), parent=parent).id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        if obsolete or denied_context in {"context_paths", "source_navigation", "unproven_packing"}:
            assert not any(task.get("reused") for task in scan.progress["tasks"].values())
        else:
            assert scan.status == "completed", scan.error
            assert calls == [("fresh.py", 1000)]
            assert scan.progress["tokens"] == 1000
            assert all(scan.progress["tasks"]["analysis:" + path]["reused"] for path in paths[1:])


def test_related_security_history_retains_transitive_renames_and_dismissals():
    proposal = {"path": "new.py", "evidence": [{"path": "new.py", "quote": "shared"}]}
    history = [
        {"path": "old.py", "state": "false_positive", "evidence": {"source_sha": OLD, "locations": [{"path": "bridge.py"}]}},
        {"path": "another.py", "state": "resolved", "evidence": {"source_sha": SHA, "locations": [{"path": "bridge.py"}]}},
        {"path": "unrelated.py", "state": "false_positive", "evidence": {"source_sha": SHA}},
    ]
    item = SimpleNamespace(sha=SHA, renamed=lambda sha, path: "new.py" if path == "old.py" else path)
    assert security.assessment_history(proposal, history, item) == history[:2]


@pytest.mark.parametrize("kind", ["api"])
def test_security_context_limit_is_local_to_the_task(tmp_path, monkeypatch, kind):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}}
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        raise security.verification.VerificationOutputError("Too large", {"tokens": 0}, "skipped",
            feedback={"kind": "context_limit"})


    monkeypatch.setattr(workflow_inference, "execute", api)
    with pytest.raises(security.verification.VerificationOutputError) as error:
        ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, "assessment",
            {"history": [], "sources": {"current": {"app.py": "safe"}}, "diff": ""}, 1000, 60, lambda: False, uid())
    assert error.value.feedback["kind"] == "context_limit" and error.value.usage["tokens"] == 0
    assert len(calls) == 1


@pytest.mark.parametrize("large_advisory", [False, True])
def test_oversized_issue_preview_fits_the_publication_contract(seed, security_github, client, large_advisory):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        finding = saved_finding(db, seed, scan, evidence=[{"revision": "current", "path": f"source{i}.py",
            "start_line": 1, "end_line": 1, "quote": "雪" * 20000} for i in range(3)],
            **({"title": "Long advisory title " * 5000, "explanation": "Long advisory description\n" * 5000} if large_advisory else {}))
        identity = finding.id
        evidence = deepcopy(finding.evidence)
    path = f"/api/v1/security/findings/{identity}/github-issue"
    response = client.get(path)
    assert response.status_code == 200
    preview = response.json()
    assert len(preview["body"]) <= 50000
    assert "Preview shortened" in preview["body"] and OLD in preview["body"] and "team/project/blob/" in preview["body"]
    assert client.post(path, json={key: preview[key] for key in ("title", "body", "version", "public")}).status_code == 202
    with transaction() as db:
        assert db.get(SecurityFinding, identity).evidence == evidence


@pytest.mark.parametrize("invalid", [None, "version", "operation", "revision", "unpublished", "read_only"])
def test_issue_sync_reapproval_is_explicit_versioned_and_writer_only(seed, security_github, client, monkeypatch, invalid):
    with transaction() as db:
        db.add(User(id=99, login="departed", enabled=False))
        db.flush()
        scan = saved_scan(db, seed, status="completed")
        finding = saved_finding(db, seed, scan)
        identity, operation = finding.id, uid()
        finding.issue = {"operation_id": operation, "actor_id": 99, "number": 9, "id": 99,
            "node_id": "issue-node", "url": "https://github.com/team/project/issues/9", "sync_status": "failed"}
        if invalid == "unpublished":
            finding.issue = {"operation_id": operation, "actor_id": 99, "started": True}
    path = f"/api/v1/security/findings/{identity}/github-issue"
    body = {"version": 0, "operation_id": operation, "approval_revision": 0, "reason": "I reviewed this issue and will manage synchronization."}
    if invalid == "version":
        body["version"] = 1
    elif invalid == "operation":
        body["operation_id"] = uid()
    elif invalid == "revision":
        body["approval_revision"] = 1
    elif invalid == "read_only":
        monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(403, "Read-only")))
    response = client.post(path + "/reapprove", json=body)
    assert response.status_code == (403 if invalid == "read_only" else 409 if invalid else 202), response.text
    if invalid:
        return
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.version == 0 and finding.issue["actor_id"] == 99
        assert finding.issue["operation_id"] == operation and finding.issue["number"] == 9
        assert finding.issue["sync_approval"]["actor_id"] == 7 and finding.issue["sync_approval"]["revision"] == 1
        assert db.scalar(select(SecurityActivity).where(SecurityActivity.action == "issue_sync_reapproved")).detail["previous_actor_id"] == 99
    assert client.post(path + "/reapprove", json=body).status_code == 409
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert security_issues.identity(db, finding)[0] == "team/project"
        db.delete(db.get(Membership, 7))
    security_issues.sync(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.issue["sync_status"] == "failed" and "no longer has access" in finding.issue["error"]


@pytest.mark.parametrize("related", [False, True])
def test_large_history_only_withholds_candidates_that_need_it(seed, security_github, monkeypatch, tmp_path, related):
    paths = ["app.py", "independent.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest("safe"), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda *args: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={}, renamed=lambda sha, path: path)
    histories = []
    real_ask = security_engine.ask

    def api(*args, **kwargs):
        if args[2].get("history"):
            raise security.verification.VerificationOutputError("Necessary context exceeds the prompt limit", {"tokens": 0},
                feedback={"kind": "context_limit"})
        result = {"verdict": "rejected", "finding_id": None, "explanation": "Guarded", "evidence": [], "required_paths": []}
        return kwargs["validator"](result, args[2]), {"tokens": 1}

    def ask(item, connection, credentials, task, context, *args, **kwargs):
        if task == "analysis":
            path = context["assigned_path"]
            proposal = {"title": path, "category": "test", "severity": "low", "path": path,
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                "verification": "Test", "evidence": [{"revision": "current", "path": path, "start_line": 1, "end_line": 1, "quote": "safe"}]}
            return {"findings": [proposal], "required_paths": []}, {"tokens": 1}
        histories.append((context["proposal"]["path"], len(context["history"])))
        return real_ask(item, connection, credentials, task, context, *args, **kwargs)

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(workflow_inference, "execute", api)
    with transaction() as db:
        earlier = saved_scan(db, seed, status="completed")
        for index in range(60):
            saved_finding(db, seed, earlier, state="false_positive", source_sha=SHA,
                path="app.py" if related else f"unrelated/{index}.py", explanation="e" * 1000)
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid())).id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == ("partial" if related else "completed"), scan.error
        assert sorted(histories) == [("app.py", 60 if related else 0), ("independent.py", 0)]
        assert scan.stages["assessment"]["failed"] == int(related)
        assert scan.stages["assessment"]["completed"] == (1 if related else 2)
        assert scan.progress["tokens"] == (3 if related else 4)


@pytest.mark.parametrize("change", ["approval", "permission"])
@pytest.mark.parametrize("partial", [False, True])
def test_sync_rechecks_approval_and_permission_before_comment_mutation(seed, security_github, monkeypatch, change, partial):
    with transaction() as db:
        scan = saved_scan(db, seed, status="partial" if partial else "completed")
        finding = saved_finding(db, seed, scan, state="resolved")
        if partial:
            db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="fixed",
                snapshot={"version": 0, "state": "resolved", "verification": {"verdict": "fixed"}}))
        identity, operation = finding.id, uid()
        finding.issue = {"number": 9, "id": 99, "node_id": "issue-node", "operation_id": operation,
            "actor_id": 7, "desired_state": "closed", "intent_version": 0, "verification_scan_id": scan.id,
            "sync_approval": {"actor_id": 7, "revision": 1}}
        marker = security_issues.marker(finding, operation)
    writes = []
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "open", "body": marker,
        "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
        "user": {"login": "mortis-test[bot]", "type": "Bot"}}

    def request(method, path, token, **kwargs):
        if method != "GET":
            writes.append(method)
        return remote if "/issues/9" in path else {"sha": SHA} if "/commits/" in path else security_github

    def comments(*args, **kwargs):
        with transaction() as db:
            finding = db.get(SecurityFinding, identity)
            if change == "approval":
                finding.issue = {**finding.issue, "sync_approval": {"actor_id": 7, "revision": 2}}
            else:
                db.get(User, 7).enabled = False
        return []

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "pages", comments)
    security_issues.sync(identity)
    assert writes == []
    with transaction() as db:
        assert db.get(SecurityFinding, identity).issue["sync_status"] == "failed"


@pytest.mark.parametrize("proof", ["fixed", "missing", "uncertain", "not_observed", "stale", "other_finding", "other_scan", "failed_scan"])
def test_partial_scan_issue_close_requires_this_findings_applied_verification(seed, security_github, monkeypatch, proof):
    with transaction() as db:
        scan = saved_scan(db, seed, status="failed" if proof == "failed_scan" else "partial")
        finding = saved_finding(db, seed, scan, state="resolved")
        finding.version = 1
        identity, operation = finding.id, uid()
        finding.issue = {"number": 9, "id": 99, "node_id": "issue-node", "operation_id": operation,
            "actor_id": 7, "desired_state": "closed", "intent_version": 1, "verification_scan_id": scan.id}
        if proof != "missing":
            other = saved_finding(db, seed, scan)
            db.add(SecurityOccurrence(scan_id=saved_scan(db, seed).id if proof == "other_scan" else scan.id,
                finding_id=other.id if proof == "other_finding" else finding.id,
                outcome=proof if proof in {"uncertain", "not_observed"} else "fixed",
                snapshot={"version": 0 if proof == "stale" else 1, "state": "resolved",
                          "verification": {"verdict": "fixed"}}))
        marker = security_issues.marker(finding, operation)
    bot = {"login": "mortis-test[bot]", "type": "Bot"}
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "open", "body": marker,
        "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
        "user": bot, "closed_by": None}
    writes = []

    def request(method, path, token, **kwargs):
        if method == "GET":
            return dict(remote) if "/issues/9" in path else {"sha": SHA} if "/commits/" in path else security_github
        writes.append(method)
        if method == "PATCH":
            remote.update(state=kwargs["json"]["state"], closed_by=bot)
        return dict(remote)

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [])
    security_issues.sync(identity)
    assert writes == (["POST", "PATCH"] if proof == "fixed" else [])
    with transaction() as db:
        issue = db.get(SecurityFinding, identity).issue
        assert issue["sync_status"] == ("synced" if proof == "fixed" else "failed")
        if proof == "fixed":
            assert issue["closed_by_app"] and issue["remote_state"] == "closed"


def test_redaction_failure_after_provider_result_retains_paid_usage(tmp_path, monkeypatch):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}}

    def api(*args, **kwargs):
        item.redact = lambda text: (_ for _ in ()).throw(TimeoutError("Redaction deadline reached"))
        return {"verdict": "uncertain", "finding_id": None, "explanation": "Checked", "evidence": [], "required_paths": []}, {"tokens": 17}

    monkeypatch.setattr(workflow_inference, "execute", api)
    with pytest.raises(security.verification.VerificationOutputError) as error:
        ask_security(item, {"id": "connection", "kind": "api", "model": "test"}, {}, "assessment",
            {"history": [], "sources": {"current": {"app.py": "safe"}}, "diff": ""}, 1000, 60, lambda: False, uid())
    assert error.value.usage["tokens"] == 17 and error.value.feedback["kind"] == "redaction"


@pytest.fixture
def security_github(github_stub, monkeypatch):
    remote = {"id": 22, "private": True, "default_branch": "main", "permissions": {"pull": True, "push": True}}
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: remote)
    monkeypatch.setattr(github, "request", lambda method, path, token, **kwargs: {"sha": SHA} if "/commits/" in path else remote)
    return remote


def test_repository_scan_settings_default_independently_and_retry_keeps_saved_limits(seed, client, security_github):
    path = "/api/v1/repositories/22"
    scan_path = "/api/v1/security/repositories/22/scans"
    body = {"enabled": False, "connection_id": "connection", "settings": seed["repo"].settings}
    original = client.put(path, json=body)
    assert original.status_code == 200, original.text
    defaults = {"effort": "high", "exclusions": "", "max_output_tokens": None,
                "assessment_token_budget": None, "verification_token_budget": None}
    assert original.json()["scan_settings"] == {**defaults, "token_budget": None, "timeout_seconds": None, "concurrency": 4}

    options = {"effort": "medium", "exclusions": "fixtures/**,\nexample.py", "max_output_tokens": 4000,
               "assessment_token_budget": 10000, "verification_token_budget": 12000}
    limits = {"token_budget": 45000, "timeout_seconds": 600, "concurrency": 8, **options}
    saved = client.put(path, json={**body, "scan_settings": limits})
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["token_budget"] == 200000
    assert saved.json()["scan_settings"] == limits
    review_update = client.put(path, json={**body, "settings": {**body["settings"], "token_budget": 90000}})
    assert review_update.status_code == 200, review_update.text
    assert review_update.json()["scan_settings"] == limits

    updates = {"effort": "low", "timeout_seconds": None}
    partial_update = client.put(path, json={**body, "scan_settings": updates})
    limits = {**limits, **updates}
    assert partial_update.status_code == 200, partial_update.text
    assert partial_update.json()["scan_settings"] == limits

    scan_body = {"connection_id": "connection", "request_key": uid()}
    started = client.post(scan_path, json=scan_body)
    assert started.status_code == 202, started.text
    scan = started.json()
    assert scan["config"]["limits"] == limits

    with transaction() as db:
        db.get(SecurityScan, scan["id"]).status = "partial"
    changed_limits = {"token_budget": 60000, "timeout_seconds": None, "concurrency": 3}
    changed = client.put(path, json={**body, "scan_settings": {**changed_limits, **defaults}})
    assert changed.status_code == 200, changed.text
    retried = client.post(f"/api/v1/security/scans/{scan['id']}/retry", json={**scan_body, "request_key": uid()})
    assert retried.status_code == 202, retried.text
    assert retried.json()["config"]["limits"] == limits
    with transaction() as db:
        assert db.get(SecurityScan, scan["id"]).config["limits"] == limits
        db.get(SecurityScan, retried.json()["id"]).status = "completed"

    legacy = client.post(scan_path, json={**scan_body, "request_key": uid(), "token_budget": None})
    assert legacy.status_code == 202, legacy.text
    assert legacy.json()["config"]["limits"] == {**changed_limits, "token_budget": None}


@pytest.mark.parametrize("invalid", [{"effort": "maximum"}, {"exclusions": "fixtures/**,!tests/**"},
    {"max_output_tokens": 0}, {"max_output_tokens": 1_000_001}, {"assessment_token_budget": 999},
    {"verification_token_budget": 5000001}, {"assessment_token_budget": "1000"}])
def test_repository_rejects_invalid_security_scan_settings(seed, client, security_github, invalid):
    body = {"enabled": False, "connection_id": "connection", "settings": seed["repo"].settings, "scan_settings": invalid}
    response = client.put("/api/v1/repositories/22", json=body)
    assert response.status_code == 422, response.text


def saved_scan(db, seed, status="running", sha=SHA, **values):
    scan = SecurityScan(id=uid(), repository_id=seed["repo"].id, actor_id=seed["user"].id,
        connection_id=seed["connection"].id, request_key=uid(), branch="main", head_sha=sha,
        config={"analysis_engine": "mortis", "connection": security.connection_snapshot(seed["connection"]), "limits": {"token_budget": 100000, "timeout_seconds": 900}},
        status=status, progress={"tasks": {}, "tokens": 0}, stages={}, **values)
    db.add(scan)
    db.flush()
    return scan


def saved_finding(db, seed, scan, kind="code", severity="high", state="open", **extra):
    evidence = {"kind": kind, "fingerprint": digest(uid()), "title": "Missing ownership check", "path": "app.py",
                "severity": severity, "source_sha": OLD, "source_hash": digest("unsafe"), "sources": ["ocr"],
                "explanation": "Another account's resource can be read.", "remediation": "Check ownership.",
                "verification": "Test access using another account.", **extra}
    finding = SecurityFinding(id=uid(), repository_id=seed["repo"].id, fingerprint=evidence["fingerprint"],
        state=state, kind=kind, severity=severity, title=evidence["title"], path=evidence["path"],
        first_scan_id=scan.id, last_scan_id=scan.id, evidence=evidence, version=0)
    db.add(finding)
    db.flush()
    return finding


def freshness(sha=SHA):
    return {"head_sha": sha, "default_branch": "main", "checked_at": now().isoformat()}


@pytest.mark.parametrize("gate", ["ai", "checkpoint_start", "checkpoint_result", "finish", "provider"])
def test_security_auth_outage_preserves_usage_before_retry(seed, security_github, monkeypatch, tmp_path, gate):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {"code": True, "hash": digest("safe"), "lines": 1}}
    item.read = lambda *args: "safe"
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    paid_calls, checks = [], []

    def ask(*args, **kwargs):
        paid_calls.append(args[4])
        if gate == "provider":
            raise pat_provenance.TransientAuthorizationError("Authorization interrupted the provider call")
        return {"findings": [], "required_paths": []}, {"tokens": 7}

    def scan_revoked(db, scan, **kwargs):
        if kwargs.get("network") is not False:
            checks.append(scan.status)
            if len(checks) == {"ai": 2, "checkpoint_start": 3, "checkpoint_result": 4, "finish": 5}.get(gate):
                raise pat_provenance.TransientAuthorizationError("Temporary authorization outage")
        return False

    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(pat_provenance, "scan_revoked", scan_revoked)
    with transaction() as db:
        scan_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=1)).id
        job_id = db.scalar(select(Job.id).where(Job.kind == "security_scan"))
    jobs.execute_job(job_id)
    with transaction() as db:
        scan, job = db.get(SecurityScan, scan_id), db.get(Job, job_id)
        assert job.status == "queued" and scan.status == "queued"
        assert scan.cancel_requested is False and scan.error is None and scan.report == {}
        assert scan.progress["tokens"] == (None if gate == "provider" else 7 if gate in {"checkpoint_result", "finish"} else 0)
        job.available_at = now()
    jobs.execute_job(job_id)
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        assert scan.status == ("failed" if gate == "provider" else "completed"), scan.error
        assert db.get(Job, job_id).status == "done"
        assert scan.progress["tokens"] == (None if gate == "provider" else 7)
        if gate == "provider":
            assert "Token usage is unknown" in scan.error
        else:
            assert scan.progress["tasks"]["analysis:app.py"]["tokens"] == 7
    assert len(paid_calls) == 1


@pytest.mark.parametrize("kind, gate", [("security_issue_publish", 1), ("security_issue_publish", 2), ("security_issue_sync", 1)])
def test_issue_auth_outage_requeues_without_recording_failed_publication(seed, security_github, monkeypatch, kind, gate):
    calls = []

    def mapping_revoked(*args, **kwargs):
        calls.append(1)
        if len(calls) == gate:
            raise pat_provenance.TransientAuthorizationError("Temporary authorization outage")
        return False

    monkeypatch.setattr(pat_provenance, "mapping_revoked", mapping_revoked)
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("outage must prevent publication"))
    monkeypatch.setattr(security_issues, "identity", lambda *a, **k: ("team/project", "token", "mortis-test[bot]"))
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        finding = saved_finding(db, seed, scan)
        finding.issue = {"operation_id": uid(), "version": 0, "started": False,
                         "publication_status": "pending", "sync_status": "pending"}
        finding_id = finding.id
        job_id = security.enqueue(db, kind, {"finding_id": finding_id}).id
    jobs.execute_job(job_id)
    with transaction() as db:
        assert db.get(Job, job_id).status == "queued"
        issue = db.get(SecurityFinding, finding_id).issue
        assert issue["sync_status"] == "pending" and issue["publication_status"] == "pending"
        assert issue["started"] is False and not issue.get("error")


def test_security_detail_counts_include_empty_lists(seed, security_github, client):
    detail = client.get("/api/v1/security/repositories/22").json()
    assert detail["finding_count"] == detail["scan_count"] == 0
    assert detail["finding_state_counts"] == {}
    assert detail["open_finding_severity_counts"] == {}
    assert detail["last_completed_scan_at"] is None
    with transaction() as db:
        scan_id = saved_scan(db, seed, status="completed").id
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["finding_count"] == detail["activity_count"] == 0


def test_security_detail_counts_are_scoped_totals_independent_of_pages_and_filters(seed, security_github, client):
    completed_at = now().replace(microsecond=0) - timedelta(days=2)
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed", created_at=completed_at - timedelta(minutes=1), finished_at=completed_at)
        other = saved_scan(db, seed, status="partial", created_at=completed_at + timedelta(days=1),
                           finished_at=completed_at + timedelta(days=1, minutes=1))
        saved_scan(db, seed, status="cancelled", finished_at=now())
        scan_id = scan.id
        for index in range(25):
            finding = saved_finding(db, seed, scan, severity=["critical", "high", "low"][index % 3],
                                    state=["open", "resolved", "false_positive", "accepted_risk"][index % 4])
            db.add(SecurityOccurrence(scan_id=scan.id if index < 21 else other.id, finding_id=finding.id,
                                      outcome="present", snapshot=security.finding_snapshot(finding)))
        db.add_all([SecurityActivity(scan_id=scan.id, action="scan_stage") for _ in range(205)])
        db.add(SecurityActivity(scan_id=other.id, action="scan_created"))
        repo = Repository(id=23, installation_id=10, full_name="team/other", enabled=True)
        db.add(repo)
        db.flush()
        unrelated = saved_scan(db, {**seed, "repo": repo}, status="completed", finished_at=now())
        saved_finding(db, {**seed, "repo": repo}, unrelated)
    detail = client.get("/api/v1/security/repositories/22").json()
    assert detail["finding_count"] == 25 and detail["scan_count"] == 3
    assert detail["last_completed_scan_at"] == completed_at.isoformat().replace("+00:00", "Z")
    assert detail["summary"]["latest_status"] == "cancelled"
    assert detail["finding_state_counts"] == {"open": 7, "resolved": 6, "false_positive": 6, "accepted_risk": 6}
    assert detail["open_finding_severity_counts"] == {"critical": 3, "high": 2, "low": 2}
    path = "/api/v1/security/repositories/22/findings"
    page = client.get(path, params={"state": "open"}).json()
    assert page["total"] == 7
    for severity in ("critical", "high"):
        filtered = client.get(path, params={"state": "open", "severity": severity}).json()
        assert filtered["total"] == detail["open_finding_severity_counts"][severity]
    assert client.get("/api/v1/security/repositories/22").json() == detail
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["finding_count"] == 21 and detail["activity_count"] == 205
    assert detail["finding_state_counts"] == {"open": 6, "resolved": 5, "false_positive": 5, "accepted_risk": 5}
    assert len(detail["activity"]) == 205
    assert len({event["id"] for event in detail["activity"]}) == 205
    page = client.get(f"/api/v1/security/scans/{scan_id}/findings").json()
    assert page["total"] == 21 and len(page["items"]) == 10


def test_report_findings_are_filtered_and_paginated_from_saved_evidence(seed, security_github, client, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        finding = saved_finding(db, seed, scan)
        rows = [{"id": finding.id if index == 0 else uid(), "finding_id": finding.id if index == 0 else uid(),
                 "state": "open" if index < 21 else "resolved" if index < 23 else "false_positive" if index == 23 else "unknown",
                 "title": f"Original source {index}", "path": "query%_test.py" if index == 5 else f"src/{index}.py",
                 "outcome": "present" if index < 21 else "fixed" if index < 23 else "not_observed" if index == 23 else None,
                 "severity": "high" if index < 21 else "medium" if index < 23 else "low" if index == 23 else None,
                 "kind": "code" if index < 21 else "infrastructure" if index < 23 else "custom_policy" if index == 23 else None,
                 "evidence": {"explanation": f"Saved evidence {index}"}} for index in range(25)]
        scan.report = {"findings": rows, "problems": ["Saved warning"]}
        finding.title, finding.state = "Changed live title", "false_positive"
        scan_id, finding_id = scan.id, finding.id
    path = f"/api/v1/security/scans/{scan_id}/report/findings"
    first = client.get(path).json()
    second = client.get(path, params={"page": 3}).json()
    middle = client.get(path, params={"page": 2}).json()
    assert first["total"] == first["registered_total"] == 25 and first["page_size"] == 10
    assert len(first["items"]) == 10 and len(second["items"]) == 5
    assert first["items"] + middle["items"] + second["items"] == rows
    assert first["state_counts"] == {"open": 21, "resolved": 2, "false_positive": 1, "unknown": 1}
    assert client.get(path, params={"state": "resolved"}).json()["items"] == rows[21:23]
    assert client.get(path, params=[("state", "open"), ("state", "resolved")]).json()["total"] == 23
    assert client.get(path, params={"state": "open", "page": 3}).json()["items"] == rows[20:21]
    assert client.get(path, params={"state": "unknown"}).json()["items"] == rows[24:]
    filtered = client.get(path, params={"kind": "infrastructure", "severity": "medium", "outcome": "fixed"}).json()
    assert filtered["items"] == rows[21:23] and filtered["total"] == 2
    assert filtered["registered_total"] == 25 and filtered["outcome_counts"] == first["outcome_counts"]
    assert filtered["kind_counts"] == {"code": 21, "infrastructure": 2, "custom_policy": 1, "unknown": 1}
    assert client.get(path, params=[("outcome", "present"), ("outcome", "fixed")]).json()["total"] == 23
    assert client.get(path, params={"outcome": "present", "page": 3}).json()["items"] == rows[20:21]
    assert client.get(path, params={"q": "%_"}).json()["items"] == rows[5:6]
    assert client.get(path, params={"q": "SAVED EVIDENCE 24"}).json()["items"] == rows[24:]
    assert client.get(path, params={"outcome": "unknown", "severity": "unknown", "kind": "unknown"}).json()["items"] == rows[24:]
    assert client.get(path, params={"finding_id": finding_id}).json()["items"] == rows[:1]
    assert client.get(path, params={"finding_id": uid()}).json()["items"] == []
    assert client.get(path, params={"page": 4}).json()["items"] == []
    for params in ({"page": 0}, {"q": "x" * 257}, {"severity": "invalid"}, {"state": "invalid"}, {"kind": "x" * 51}):
        assert client.get(path, params=params).status_code == 422
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["report_count"] == 25 and "findings" not in detail["scan"]["report"]
    assert detail["scan"]["report"]["problems"] == ["Saved warning"]
    assert client.get(f"/api/v1/security/scans/{scan_id}/report").json()["findings"] == rows

    def denied(*args, **kwargs):
        raise HTTPException(403, "Repository access revoked")

    monkeypatch.setattr(github, "authorize_repo", denied)
    assert client.get(path, params={"page": 2}).status_code == 403


def test_scan_finding_results_distinguish_new_repeats_and_verification_before_pagination(seed, security_github, client):
    expected = {"new": 2, "open_repeat": 21, "resolved_repeat": 1, "false_positive_repeat": 1,
                "accepted_risk_repeat": 1, "fixed": 1, "not_observed": 1, "uncertain": 1, "recorded": 1, "repeated": 1}
    identities = {key: [] for key in expected}
    with transaction() as db:
        earlier, scan = saved_scan(db, seed, status="completed"), saved_scan(db, seed, status="completed")
        scan_id = scan.id
        for state in ("open", "resolved"):
            finding = saved_finding(db, seed, scan, state=state)
            identities["new"].append(finding.id)
            db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        for state in ("open", "resolved", "false_positive", "accepted_risk", "legacy"):
            key = f"{state}_repeat" if state != "legacy" else "repeated"
            for index in range(21 if state == "open" else 1):
                finding = saved_finding(db, seed, earlier, state=state, title=f"Earlier defect {index}",
                    severity="medium" if index == 20 else "high", kind="infrastructure" if index == 20 else "code")
                identities[key].append(finding.id)
                db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        for outcome in ("fixed", "not_observed", "uncertain", "legacy"):
            finding = saved_finding(db, seed, earlier)
            identities[outcome if outcome != "legacy" else "recorded"].append(finding.id)
            db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome=outcome, snapshot=security.finding_snapshot(finding)))
        saved_finding(db, seed, earlier)
        scan.report = security.report_data(db, scan, [])
        report = deepcopy(scan.report)
        repo = Repository(id=23, installation_id=10, full_name="team/other", enabled=True)
        db.add(repo)
        db.flush()
        foreign = saved_finding(db, {**seed, "repo": repo}, earlier)
        db.add(SecurityOccurrence(scan_id=scan.id, finding_id=foreign.id, outcome="present", snapshot=security.finding_snapshot(foreign)))
    path = f"/api/v1/security/scans/{scan_id}/findings"
    page = client.get(path).json()
    assert page["total"] == 31 and len(page["items"]) == 10
    assert page["result_counts"] == expected
    for outcome, ids in identities.items():
        page = client.get(path, params={"outcome": outcome}).json()
        assert page["total"] == len(ids) and page["result_counts"] == expected
        rows = page["items"] + [row for number in (2, 3) for row in client.get(path, params={"outcome": outcome, "page": number}).json()["items"]]
        assert {row["id"] for row in rows} == set(ids)
    response = client.get(path + "?outcome=new&outcome=open_repeat&severity=medium&kind=infrastructure&search=Earlier")
    assert response.status_code == 200 and response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == identities["open_repeat"][-1]
    assert response.json()["result_counts"] == expected
    assert client.get(path + "?outcome=invalid").status_code == 422
    with transaction() as db:
        db.get(SecurityFinding, identities["resolved_repeat"][0]).state = "open"
    page = client.get(path, params={"outcome": "resolved_repeat"}).json()
    assert page["total"] == 1 and page["result_counts"]["open_repeat"] == 21
    resolved = client.get(path, params={"resolved": True}).json()
    assert resolved["total"] == resolved["registered_total"] == 1
    assert resolved["items"][0]["id"] == identities["fixed"][0]
    assert resolved["items"][0]["state"] == "open"  # Reopened later, still verified by this scan.
    assert resolved["result_counts"] == {"fixed": 1}
    assert resolved["state_counts"] == {"open": 1}
    remaining = client.get(path, params={"resolved": False}).json()
    assert remaining["total"] == remaining["registered_total"] == 30
    assert "fixed" not in remaining["result_counts"]
    assert client.get(path, params={"resolved": False, "outcome": "not_observed"}).json()["total"] == 1
    assert client.get(path, params={"resolved": True, "outcome": "not_observed"}).json()["total"] == 0
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["finding_count"] == 31 and detail["resolved_count"] == 1
    with transaction() as db:
        assert db.get(SecurityScan, scan_id).report == report
        assert db.scalar(select(SecurityOccurrence.snapshot).where(SecurityOccurrence.finding_id == identities["new"][0]))["state"] == "open"


def test_unfinished_scan_exposes_saved_observation_without_claiming_a_report(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        scan_id, finding_id = scan.id, finding.id
        snapshot = {"title": "Original finding", "path": "app.py", "state": "open"}
        db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=snapshot))
        finding.title, finding.state = "Later title", "resolved"
    rows = client.get(f"/api/v1/security/scans/{scan_id}/report/findings", params={"finding_id": finding_id}).json()
    assert rows["items"] == [{**snapshot, "finding_id": finding_id, "outcome": "present"}]
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["finding_count"] == 1 and detail["resolved_count"] == detail["report_count"] == 0
    assert detail["finding_state_counts"] == {"open": 1}
    assert client.get(f"/api/v1/security/scans/{scan_id}/report").status_code == 409


@pytest.mark.parametrize(("severity", "grade"), [(None, "A"), ("low", "B"), ("medium", "C"), ("high", "D"), ("critical", "F"), ("unknown", None)])
def test_rating_uses_highest_unresolved_severity(severity, grade):
    assert security.grade_for([severity] if severity else []) == grade


def test_create_scan_is_independent_of_pr_reviews_and_idempotent(seed, security_github, client):
    from code_mortis.models import Repository

    with transaction() as db:
        db.get(Repository, 22).enabled = False
    body = {"connection_id": "connection", "request_key": uid(), "token_budget": 100000, "timeout_seconds": 900}
    first = client.post("/api/v1/security/repositories/22/scans", json=body)
    assert first.status_code == 202, first.text
    scan = first.json()
    assert scan["head_sha"] == SHA and scan["branch"] == "main" and scan["status"] == "queued"
    assert "tasks" not in scan["progress"]
    assert client.post("/api/v1/security/repositories/22/scans", json=body).json()["id"] == scan["id"]
    assert client.post("/api/v1/security/repositories/22/scans", json={**body, "request_key": uid()}).status_code == 409
    with transaction() as db:
        assert db.scalar(select(Job.kind)) == "security_scan"
    assert jobs.job_queue("security_scan") == "security"
    assert jobs.job_queue("security_issue_publish") == "sync"


def test_scan_reads_and_writes_recheck_github_access(seed, security_github, client, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed)
        identity = scan.id
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: (_ for _ in ()).throw(github.GitHubError(403, "Denied")))
    assert client.get(f"/api/v1/security/scans/{identity}").status_code == 403
    assert client.post(f"/api/v1/security/scans/{identity}/cancel").status_code == 403
    assert client.get(f"/api/v1/security/scans/{identity}/assessments").status_code == 403
    assert client.get(f"/api/v1/security/scans/{identity}/assessments/0").status_code == 403


def test_security_assessment_history_is_live_scoped_filtered_and_read_only(seed, security_github, client):
    proposals = [{"title": f"Candidate {number}", "category": "Access Control", "severity": "high", "path": "app.py",
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix", "verification": "Test",
        "evidence": [{"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": f"source {number}"}]}
        for number in range(25)]
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan.stages = {"analysis": {"status": "completed"}, "assessment": {"status": "running"}}
        scan.coverage = {"inventory": {"app.py": {"code": True}}}
        tasks = {"analysis:app.py": {"status": "completed", "result": {"findings": proposals}}}
        for number, verdict in enumerate(["confirmed", "rejected", "uncertain"]):
            tasks[f"assessment:{number}"] = {"status": "completed", "proposal": proposals[number], "tokens": 12,
                "result": {"verdict": verdict, "finding_id": None, "explanation": verdict,
                           "evidence": proposals[number]["evidence"], "required_paths": [], "api_key": "internal-secret"},
                "input_hash": "internal-hash", "credentials": "internal-secret"}
        tasks["assessment:3"] = {"status": "failed", "proposal": proposals[3], "tokens": None, "error": "Invalid citation"}
        tasks["assessment:4"] = {"status": "running", "proposal": proposals[4]}
        tasks["assessment:5"] = {**tasks["assessment:0"], "proposal": proposals[5]}
        scan.progress = {"tasks": tasks, "tokens": None, "execution": "untouched:1"}
        fingerprint = digest(json.dumps(["code", "app.py", "Access Control", "source 0", "Candidate 0"]))
        finding = saved_finding(db, seed, scan, kind="infrastructure", ai_analysis={"fingerprint": fingerprint})
        db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        identity, finding_id = scan.id, finding.id
        original = json.dumps([scan.progress, scan.stages, scan.coverage], sort_keys=True)
        other = saved_scan(db, seed, status="completed")
        other_id = other.id
    detail = client.get(f"/api/v1/security/scans/{identity}").json()
    assert detail["assessment"]["total"] == 25
    assert detail["assessment"]["status_counts"] == {"completed": 3, "failed": 1, "running": 2, "queued": 19}
    assert detail["finding_count"] == 1
    page = client.get(f"/api/v1/security/scans/{identity}/assessments").json()
    assert page["total"] == page["registered_total"] == 25 and len(page["items"]) == 10
    assert page["outcome_counts"] == {"confirmed": 1, "rejected": 1, "uncertain": 1, "failed": 1, "pending": 21}
    assert page["revision"] == detail["assessment"]["revision"]
    assert page["items"][0]["finding_id"] == finding_id
    assert page["items"][5]["progress_phase"] == "validating" and page["items"][5]["finding_id"] is None
    assert all(value not in json.dumps(page) for value in ("internal-secret", "internal-hash", "credentials"))
    second = client.get(f"/api/v1/security/scans/{identity}/assessments?page=2").json()
    assert second["page_size"] == 10
    assert [row["id"] for row in second["items"]] == [str(number) for number in range(10, 20)]
    filtered = client.get(f"/api/v1/security/scans/{identity}/assessments?outcome=rejected&severity=high&category=Access%20Control&q=candidate%201").json()
    assert filtered["total"] == 1 and filtered["registered_total"] == 25 and filtered["items"][0]["id"] == "1"
    assert filtered["outcome_counts"] == page["outcome_counts"]
    assert client.get(f"/api/v1/security/scans/{identity}/assessments/3").json()["tokens"] is None
    assert client.get(f"/api/v1/security/scans/{identity}/assessments/99").status_code == 404
    assert client.get(f"/api/v1/security/scans/{other_id}/assessments/0").status_code == 404
    for query in ("page=0", "page_size=101", "outcome=invented", "severity=bad", "q=" + "x" * 201):
        assert client.get(f"/api/v1/security/scans/{identity}/assessments?{query}").status_code == 422
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert original == json.dumps([scan.progress, scan.stages, scan.coverage], sort_keys=True)
        scan.status = "partial"
    ended = client.get(f"/api/v1/security/scans/{identity}/assessments").json()
    assert ended["revision"] != page["revision"]
    assert ended["items"][4]["status"] == "interrupted"
    assert ended["items"][5]["outcome"] == "withheld"
    assert "pending" not in ended["outcome_counts"]


def test_security_assessments_support_legacy_checkpoints_and_do_not_renumber_during_analysis(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan.stages = {"analysis": {"status": "running"}, "assessment": {"status": "pending"}}
        scan.coverage = {"inventory": {"a.py": {"code": True}, "b.py": {"code": True}}}
        scan.progress = {"tasks": {"analysis:b.py": {"status": "completed", "result": {"findings": [{"title": "B", "path": "b.py"}]}},
            "analysis:a.py": {"status": "completed", "result": {"findings": [{"title": "A", "path": "a.py"}]}}}, "tokens": 0}
        identity = scan.id
    assert client.get(f"/api/v1/security/scans/{identity}/assessments").json()["registered_total"] == 0
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        scan.stages = {"analysis": {"status": "completed"}, "assessment": {"status": "running"}}
        scan.progress = {**scan.progress, "tasks": {**scan.progress["tasks"], "assessment:0": {
            "status": "completed", "result": {"verdict": "rejected", "explanation": "Not supported", "finding_id": None}}}}
    rows = client.get(f"/api/v1/security/scans/{identity}/assessments").json()["items"]
    assert [(row["id"], row["proposal"]["title"], row["outcome"]) for row in rows] == [("0", "A", "rejected"), ("1", "B", "pending")]
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        scan.coverage, scan.progress = {}, {"tasks": {"assessment:7": {"status": "failed", "error": "Legacy interrupted result"}}}
    rows = client.get(f"/api/v1/security/scans/{identity}/assessments").json()["items"]
    assert len(rows) == 1 and rows[0]["id"] == "7" and rows[0]["proposal"] == {}


def test_personal_scans_require_owner_and_repository_grant(seed, security_github):
    with transaction() as db:
        connection = db.get(type(seed["connection"]), "connection")
        connection.kind, connection.owner_id, connection.auth_status = "chatgpt", 7, "connected"
        body = SecurityScanInput(connection_id="connection", request_key=uid(), concurrency=8)
        with pytest.raises(ValueError, match="retired"):
            security.create_scan(db, seed["repo"], seed["user"], body)
        db.add(ConnectionGrant(connection_id="connection", repository_id=22, automatic=False))
        db.flush()
        with pytest.raises(ValueError, match="retired"):
            security.create_scan(db, seed["repo"], seed["user"], body)
        assert not list(db.scalars(select(SecurityScan)))


@pytest.mark.parametrize(("complete", "current", "resolved"), [(True, True, True), (False, True, False), (True, False, False)])
def test_resolution_requires_complete_current_evidence(seed, security_github, complete, current, resolved):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        scan_id, finding_id = scan.id, finding.id
    security.finish(scan_id, {finding_id: {"verdict": "fixed", "explanation": "Ownership is now checked.", "finding_version": 0}},
                    [] if complete else ["Other source was not analyzed"], freshness(SHA if current else "c" * 40), True)
    with transaction() as db:
        scan, finding = db.get(SecurityScan, scan_id), db.get(SecurityFinding, finding_id)
        assert (finding.state == "resolved") == resolved
        assert scan.grade == ("A" if resolved else "D")
        assert scan.report["commit"] == SHA


@pytest.mark.parametrize("advisory_available", [False, True])
def test_uncertain_dependency_does_not_block_forty_independently_verified_fixes(seed, security_github, monkeypatch, tmp_path, advisory_available):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"bun.lock": {"code": False, "hash": digest("updated"), "lines": 1}}
    item.read = lambda path, sha=None: "original" if sha else "updated"
    item.renamed = lambda sha, path: path
    item.diff = lambda sha: "lockfile updated"
    packages = [{"name": f"dependency-{index}", "version": "1.1.0"} for index in range(40)]
    packages.append({"name": "http-cache-semantics", "version": "4.3.0"})
    monkeypatch.setattr(security.git_source, "git", lambda *args, **kwargs: "bun.lock")
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "problems": [], "metadata": {},
        "targets": {"bun.lock": {"packages": packages}}})
    monkeypatch.setattr(security_engine, "ask", lambda *args: pytest.fail("Dependency verification must not dispatch inference"))
    request = github.request
    lookups, tokens = [], []

    def installation_token(db, installation_id):
        tokens.append(f"installation-{len(tokens)}")
        return tokens[-1]

    def advisory_request(method, path, token, **kwargs):
        if path.startswith("/advisories?"):
            lookups.append(path)
            assert token == tokens[-1] and token != tokens[1]
            assert token in item.secret_values
            return [deepcopy(DEPENDENCY_ADVISORY)] if advisory_available else []
        return request(method, path, token, **kwargs)

    monkeypatch.setattr(github, "installation_token", installation_token)
    monkeypatch.setattr(github, "request", advisory_request)
    with transaction() as db:
        prior = saved_scan(db, seed, status="completed")
        fixed = [saved_finding(db, seed, prior, kind="dependency", path="bun.lock", package=p["name"],
            installed_version="1.0.0", fixed_version="1.1.0").id for p in packages[:-1]]
        unknown = saved_finding(db, seed, prior, kind="dependency", title="Disputed cache advisory", path="bun.lock",
            package="http-cache-semantics", installed_version="4.2.0", fixed_version="", package_type="bun", rule_id="CVE-2026-93748").id
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid())).id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == ("completed" if advisory_available else "partial")
        assert scan.stages["verification"]["status"] == ("completed" if advisory_available else "partial")
        assert scan.stages["verification"]["completed"] == scan.stages["verification"]["total"] == 41
        assert all(db.get(SecurityFinding, key).state == "resolved" for key in fixed)
        assert db.get(SecurityFinding, unknown).state == ("resolved" if advisory_available else "open")
        assert {row["outcome"] for row in scan.report["findings"] if row["finding_id"] in fixed} == {"fixed"}
        if advisory_available:
            assert scan.error is None and scan.report["problems"] == []
            outcome = next(row for row in scan.report["findings"] if row["finding_id"] == unknown)
            assert outcome["verification"]["advisory"]["affected_range"] == "<= 4.2.0"
            assert outcome["verification"]["original_hash"] == digest("original")
            assert outcome["verification"]["current_hash"] == digest("updated")
        else:
            assert "Disputed cache advisory" in scan.error and "bun.lock" in scan.error
            assert "safe version" in scan.error and "Previous findings still require verification" not in scan.error
            assert scan.report["problems"] == [scan.error]
        assert scan.progress["tokens"] == 0
        assert scan.grade == ("A" if advisory_available else "D")
        assert security.summaries(db, [22])[22].state == ("current" if advisory_available else "incomplete")
        assert len(list(db.scalars(select(SecurityActivity).where(SecurityActivity.action == "finding_resolved")))) == (41 if advisory_available else 40)
        markdown = security.markdown_report(scan.report)
        assert "### Fix verification" in markdown and ("affected range" if advisory_available else "safe version") in markdown
    assert len(lookups) == 1


@pytest.mark.parametrize("blocker", ["coverage", "outdated", "branch", "unsupported", "cancelled", "permission", "version", "triage"])
def test_independent_resolution_preserves_global_guards_and_finding_versions(seed, security_github, monkeypatch, blocker):
    with transaction() as db:
        scan = saved_scan(db, seed)
        first, second, unknown = [saved_finding(db, seed, scan) for _ in range(3)]
        identity, ids = scan.id, [row.id for row in (first, second, unknown)]
        verdicts = {key: {"verdict": "fixed" if key != unknown.id else "uncertain", "explanation": "Checked", "finding_version": 0} for key in ids}
        if blocker in {"version", "triage"}:
            first.version += 1
        if blocker == "triage":
            first.state = "false_positive"
        if blocker == "cancelled":
            scan.cancel_requested = True
    fresh = {**freshness("c" * 40 if blocker == "outdated" else SHA), "default_branch": "other" if blocker == "branch" else "main"}
    if blocker == "permission":
        monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("Access revoked")))
        with pytest.raises(ValueError, match="Access revoked"):
            security.finish(identity, verdicts, [], fresh, True)
    else:
        security.finish(identity, verdicts, ["Source coverage incomplete"] if blocker == "coverage" else [], fresh, blocker != "unsupported")
    with transaction() as db:
        assert db.get(SecurityFinding, ids[0]).state == ("false_positive" if blocker == "triage" else "open")
        assert db.get(SecurityFinding, ids[1]).state == ("resolved" if blocker in {"version", "triage"} else "open")
        assert db.get(SecurityFinding, ids[2]).state == "open"
        if blocker not in {"cancelled", "permission"}:
            scan = db.get(SecurityScan, identity)
            assert scan.status == "partial"
            if blocker in {"version", "triage"}:
                assert {row["finding_id"]: row["outcome"] for row in scan.report["findings"]} == {
                    ids[0]: "not_observed", ids[1]: "fixed", ids[2]: "uncertain"}


@pytest.mark.parametrize("condition", [
    "version", "cancel", "unknown_usage", "replacement", "job_replaced", "transient", "finite_budget",
    "finite_refill", "finite_skip",
])
def test_security_fix_verification_refills_and_keeps_resolution_guards(
    seed, security_github, monkeypatch, tmp_path, condition,
):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {"code": True, "hash": digest("current"), "lines": 1}}
    item.read = lambda path, sha=None: "previous" if sha else "current"
    item.renamed = lambda sha, path: path
    item.diff = lambda sha, **kwargs: "Source changed"
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {
        "findings": [], "targets": {}, "problems": [], "metadata": {},
    })
    budget = 1000 if condition.startswith("finite_") else None
    legacy_budget = condition == "finite_budget"
    with transaction() as db:
        prior = saved_scan(db, seed, status="completed")
        finding_ids = {saved_finding(db, seed, prior).id for _ in range(5)}
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=4, token_budget=budget,
        ))
        if legacy_budget:
            scan.config = {key: value for key, value in scan.config.items() if key != "budget_schedule"}
        identity = scan.id

    barrier, lock = Barrier(4), Lock()
    refilled, skipped, release = Event(), Event(), Event()
    auth_failed, auth_handled = Event(), Event()
    calls, allowances = [], []
    active = peak = 0

    def ask(*args, **kwargs):
        nonlocal active, peak
        task, context, allowance = args[3:6]
        if task == "analysis":
            return {"findings": [], "required_paths": []}, {"tokens": 1}
        assert task == "verification"
        with lock:
            index = len(calls)
            calls.append(context["finding"]["id"])
            allowances.append(allowance)
            active += 1
            peak = max(peak, active)
        try:
            if not legacy_budget:
                if index < 4:
                    barrier.wait(timeout=10)
                if index == 4:
                    refilled.set()
                if index:
                    assert release.wait(10)
                if condition == "transient" and args[7]():
                    raise security.ReviewStopped("Interrupted before inference")
            if condition == "unknown_usage" and index == 0:
                raise security.verification.VerificationOutputError("Usage unavailable", {"tokens": None})
            if condition == "finite_skip" and index == 0:
                raise security.InferenceBudgetError("Request does not fit the remaining budget.", {"tokens": 0})
            return {"verdict": "fixed", "explanation": "Pinned fix verified", "evidence": []}, {"tokens": 2}
        finally:
            with lock:
                active -= 1

    original_stage = security.stage

    def stage(scan_id, name, status, **values):
        original_stage(scan_id, name, status, **values)
        if name == "verification" and values.get("completed") == 2:
            skipped.set()

    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(security, "stage", stage)
    if condition == "transient":
        original_checkpoint, original_rolling = security.checkpoint, security.rolling_analysis

        def checkpoint(scan_id, key, state, **values):
            saved = original_checkpoint(scan_id, key, state, **values)
            if state == "completed" and calls and key == "verification:" + calls[0] and not auth_failed.is_set():
                auth_failed.set()
                raise pat_provenance.TransientAuthorizationError("Temporary authorization outage")
            return saved

        def rolling(*args):
            for entry in original_rolling(*args):
                yield entry
                if isinstance(entry[1].exception(), pat_provenance.TransientAuthorizationError):
                    auth_handled.set()

        monkeypatch.setattr(security, "checkpoint", checkpoint)
        monkeypatch.setattr(security, "rolling_analysis", rolling)

    if legacy_budget:
        perform_security(identity, "test:1")
    else:
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(perform_security, identity, "test:1")
            try:
                unknown = condition == "unknown_usage"
                transient = condition == "transient"
                if transient:
                    assert auth_handled.wait(10)
                else:
                    assert (skipped if unknown else refilled).wait(10), "Verification waited for a complete batch."
                with lock:
                    assert len(calls) == (4 if unknown or transient else 5)
                    assert active == (3 if unknown or transient else 4)
                    fast = calls[0]
                with transaction() as db:
                    scan = db.get(SecurityScan, identity)
                    assert scan.stages["verification"]["completed"] == {
                        "transient": 0, "unknown_usage": 2, "finite_skip": 0,
                    }.get(condition, 1)
                    assert db.get(SecurityFinding, fast).state == "open"
                    if condition == "version":
                        db.get(SecurityFinding, fast).version += 1
                    elif condition == "cancel":
                        security.cancel(db, scan)
                    elif condition == "replacement":
                        scan.progress = {**scan.progress, "execution": "replacement:2"}
                    elif condition == "job_replaced":
                        db.get(Job, scan.progress["execution"].rsplit(":", 1)[0]).attempts += 1
            finally:
                release.set()
            if condition == "transient":
                with pytest.raises(pat_provenance.TransientAuthorizationError):
                    worker.result(timeout=10)
                with transaction() as db:
                    scan = db.get(SecurityScan, identity)
                    assert scan.status == "queued" and scan.progress["tokens"] == 9
                    assert all(scan.progress["tasks"]["verification:" + key]["status"] == "completed" for key in calls)
                perform_security(identity, "retry:2")
            else:
                worker.result(timeout=10)

    assert peak == (1 if legacy_budget else 4)
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        findings = {key: db.get(SecurityFinding, key).state for key in finding_ids}
        if condition in {"cancel", "replacement", "job_replaced"}:
            assert set(findings.values()) == {"open"}
            assert scan.status == ("cancelled" if condition == "cancel" else "running")
            assert scan.stages["verification"]["completed"] == 1
            assert not db.scalar(select(SecurityOccurrence.id).where(SecurityOccurrence.scan_id == identity))
            if condition == "replacement":
                assert scan.progress["execution"] == "replacement:2"
            if condition != "cancel":
                assert scan.progress["tokens"] == 3
            return
        assert scan.stages["verification"]["total"] == 5
        assert scan.stages["verification"]["completed"] == (4 if condition == "finite_skip" else 5)
        assert scan.stages["verification"]["failed"] == int(condition == "finite_skip")
        if legacy_budget:
            assert allowances == [999, 997, 995, 993, 991]
            assert scan.status == "completed", scan.error
            assert set(findings.values()) == {"resolved"}
        elif condition in {"transient", "finite_refill"}:
            assert scan.status == "completed", scan.error
            assert set(findings.values()) == {"resolved"}
            assert scan.progress["tokens"] == 11 and len(calls) == 5
        else:
            assert scan.status == "partial", scan.error
            assert findings[fast] == "open"
            if condition == "unknown_usage":
                assert scan.progress["tokens"] is None
                assert findings[finding_ids.difference(calls).pop()] == "open"
                assert all(findings[key] == "resolved" for key in calls[1:])
            else:
                assert all(findings[key] == "resolved" for key in finding_ids - {fast})
                if condition == "finite_skip":
                    assert scan.progress["tokens"] == 9


def test_missing_later_observation_is_not_a_fix(seed, security_github, monkeypatch):
    monkeypatch.setattr(security.git_source, "git", lambda *args, **kwargs: "regular file")
    item = SimpleNamespace(renamed=lambda sha, path: path, read=lambda path, sha=None: "unsafe", diff=lambda sha, **kwargs: "",
                           git=None, env={}, sha=SHA, stopped=lambda: False)
    old = {"evidence": {"kind": "dependency", "source_sha": OLD}, "path": "requirements.txt"}
    assert security.deterministic_fix(item, old, {"targets": {}})["verdict"] == "uncertain"


def test_summaries_separate_current_grade_coverage_and_freshness(seed):
    with transaction() as db:
        prior = saved_scan(db, seed, "completed", grade="A", finished_at=now() - timedelta(minutes=5))
        prior.created_at = now() - timedelta(minutes=5)
        latest = saved_scan(db, seed, "partial")
        latest.progress = {"freshness": freshness()}
        saved_finding(db, seed, latest, severity="critical")
        summary = security.summaries(db, [22])[22]
        assert summary.grade == "F" and summary.state == "incomplete"
        assert summary.coverage == "incomplete" and summary.freshness == "current"
        assert prior.grade == "A"
        assert summary.counts == {"critical": 1}
        latest.progress = {"freshness": freshness("c" * 40)}
        summary = security.summaries(db, [22])[22]
        assert summary.state == summary.freshness == "outdated"
        assert summary.grade == "F" and summary.coverage == "incomplete"
        latest.progress = {"freshness": {"checked_at": now().isoformat(), "error": "Unavailable"}}
        summary = security.summaries(db, [22])[22]
        assert summary.state == summary.freshness == "unavailable"
        assert summary.grade == "F" and summary.coverage == "incomplete"


@pytest.mark.parametrize("scan_status", ["completed", "partial", "running", "failed", "cancelled"])
@pytest.mark.parametrize("fresh_state", ["current", "outdated", "unavailable"])
def test_triage_updates_live_grades_without_changing_scan_history_or_coverage(seed, security_github, client, monkeypatch, scan_status, fresh_state):
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository))))
    with transaction() as db:
        scan = saved_scan(db, seed, "completed", grade="F", finished_at=now())
        critical = saved_finding(db, seed, scan, severity="critical")
        accepted = saved_finding(db, seed, scan, severity="high", state="accepted_risk")
        saved_finding(db, seed, scan, severity="critical", state="resolved")
        for finding in (critical, accepted):
            db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        scan.report = security.report_data(db, scan, [])
        report = deepcopy(scan.report)
        latest = scan if scan_status == "completed" else saved_scan(db, seed, scan_status)
        latest.progress = {"freshness": freshness(OLD if fresh_state == "outdated" else SHA)}
        if fresh_state == "unavailable":
            latest.progress["freshness"]["checked_at"] = (now() - timedelta(minutes=11)).isoformat()
        scan_id, critical_id, accepted_id = scan.id, critical.id, accepted.id
    current_states = {critical_id: "open", accepted_id: "accepted_risk"}
    expected_coverage = "complete" if scan_status == "completed" else "incomplete"
    for identity, state, version, grade in [
        (critical_id, "false_positive", 0, "D"),
        (accepted_id, "false_positive", 0, "A"),
        (critical_id, "open", 1, "F"),
        (critical_id, "accepted_risk", 2, "F"),
    ]:
        response = client.patch(f"/api/v1/security/findings/{identity}", json={"state": state, "reason": "Reviewed the source evidence", "version": version})
        assert response.status_code == 200
        assert response.json()["state"] == state
        summary = client.get("/api/v1/security/repositories/22").json()["summary"]
        assert summary["grade"] == grade
        assert summary["coverage"] == expected_coverage and summary["freshness"] == fresh_state
        assert summary["grade_scan_id"] == scan_id
        assert summary["state"] == (fresh_state if fresh_state != "current" else "current" if scan_status == "completed" else "incomplete")
        overview = client.get("/api/v1/security/repositories", params={"grade": grade}).json()
        assert overview["total"] == overview["grade_counts"][grade] == 1
        assert overview["items"][0]["summary"] == summary
        assert client.get("/api/v1/repositories").json()[0]["security"] == summary
        current_states[identity] = state
        detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
        assert detail["finding_state_counts"] == {"open": 1, "accepted_risk": 1}
        repository = client.get("/api/v1/security/repositories/22").json()
        assert repository["finding_state_counts"] == {**{value: list(current_states.values()).count(value) for value in set(current_states.values())}, "resolved": 1}
        saved = detail["scan"]
        assert saved["grade"] == saved["report"]["grade"] == "F"
        assert client.get(f"/api/v1/security/scans/{scan_id}/report").json() == report
    with transaction() as db:
        assert not db.scalar(select(SecurityActivity.id).where(SecurityActivity.action == "finding_resolved"))


def test_live_grade_stays_unrated_without_report_or_with_unknown_severity(seed):
    with transaction() as db:
        assert security.summaries(db, [22])[22].grade is None
        scan = saved_scan(db, seed, "running")
        finding = saved_finding(db, seed, scan, severity="critical")
        assert security.summaries(db, [22])[22].grade is None
        scan.status, scan.grade = "completed", "F"
        finding.severity = "unknown"
        assert security.summaries(db, [22])[22].grade is None
        finding.state = "false_positive"
        assert security.summaries(db, [22])[22].grade == "A"


def test_finished_partial_report_is_graded_without_claiming_complete_coverage(seed, security_github):
    with transaction() as db:
        scan = saved_scan(db, seed)
        saved_finding(db, seed, scan, severity="high")
        identity = scan.id
    security.finish(identity, {}, ["Source analysis could not be validated: docker-compose.yml."], freshness(), True)
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.grade == scan.report["grade"] == "D"
        assert scan.status == scan.report["status"] == "partial"
        assert scan.stages["report"]["status"] == "completed"
        assert "docker-compose.yml" in scan.error
        summary = security.summaries(db, [22])[22]
        assert summary.grade == "D" and summary.state == "incomplete"
        assert summary.grade_scan_id == summary.latest_scan_id == identity


@pytest.mark.parametrize("task", ["analysis", "assessment"])
def test_legacy_citation_failure_explains_mismatch_without_rewriting_saved_evidence(seed, task):
    error = ("Proposed finding" if task == "assessment" else "Source analysis") + " could not be validated: docker-compose.yml."
    with transaction() as db:
        scan = saved_scan(db, seed, "partial", error=error)
        scan.report = {"problems": [error]}
        key = "assessment:2" if task == "assessment" else "analysis:docker-compose.yml"
        scan.progress = {"tasks": {key: {"status": "failed", "error": "Invalid citation", "proposal": {"path": "docker-compose.yml"},
            "validation_feedback": {"quote": "synthetic", "reason": "quote is not verbatim text within the cited source lines"}}}}
        public = security.public_scan(scan)
        assert public.error.startswith("AI citation mismatch in docker-compose.yml:")
        assert public.report["problems"] == [public.error]
        assert scan.error == error and scan.report["problems"] == [error]


@pytest.mark.parametrize("task", ["analysis", "assessment"])
@pytest.mark.parametrize("origin", [None, "gateway_incomplete", "ocr_mismatch", "unavailable"])
def test_delivery_failure_names_the_completion_contract_without_rewriting_history(seed, task, origin):
    path = "src/scriptSchemaValidator.ts"
    error = ("Proposed finding" if task == "assessment" else "Source analysis") + f" could not be validated: {path}."
    feedback = {"kind": "delivery", "reason": "task_done did not include the required DONE state and result object."}
    if origin:
        feedback["transport"] = {"origin": origin}
    with transaction() as db:
        scan = saved_scan(db, seed, "partial", error=error)
        scan.report = {"problems": [error]}
        key = "assessment:82" if task == "assessment" else "analysis:" + path
        scan.progress = {"tasks": {key: {"status": "failed", "proposal": {"path": path}, "validation_feedback": feedback}}}
        public = security.public_scan(scan)
        assert public.error == security.validation_problem(path, feedback, task=task)
        expected = f"AI result delivery failed for {path}: {feedback['reason']}"
        if origin:
            expected += " " + security.result_diagnostics.explanation(feedback["transport"])
        assert public.error == expected
        assert public.report["problems"] == [public.error]
        assert scan.error == error and scan.status == "partial" and scan.report["problems"] == [error]


def test_finding_recurrence_reuses_identity_and_invalidates_rotation(seed):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan, kind="secret", state="resolved")
        finding.rotation = {"identity": "old"}
        finding.issue = {"number": 9, "closed_by_app": True}
        evidence = {**finding.evidence, "source_sha": SHA}
        found = security.observe(db, scan, evidence)
        assert found.id == finding.id and found.state == "open" and found.rotation is None
        assert found.issue["desired_state"] == "open"
        assert found.issue["intent_version"] == found.version


def test_reworded_or_renamed_confirmed_finding_updates_canonical_location_without_duplication(seed):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        found = security.observe(db, scan, {**finding.evidence, "path": "renamed.py", "title": "Ownership guard missing"}, finding.id)
        assert found.id == finding.id and found.path == "renamed.py" and found.title == "Ownership guard missing"
        assert len(list(db.scalars(select(SecurityFinding)))) == 1


def test_reports_preserve_snapshots_after_triage(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed, "completed")
        finding = saved_finding(db, seed, scan)
        db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        scan.report = security.report_data(db, scan, [])
        scan_id, finding_id = scan.id, finding.id
    assert client.patch(f"/api/v1/security/findings/{finding_id}", json={"state": "false_positive", "reason": "Protected by middleware", "version": 0}).status_code == 200
    report = client.get(f"/api/v1/security/scans/{scan_id}/report").json()
    assert report["findings"][0]["state"] == "open"
    assert client.get(f"/api/v1/security/scans/{scan_id}/report?format=markdown").headers["content-type"].startswith("text/markdown")
    assert client.get(f"/api/v1/security/findings/{finding_id}").json()["finding"]["state"] == "false_positive"


@pytest.mark.parametrize(("current", "fixed", "safe"), [
    ("1.2.4", "1.2.3", True), ("1.2.2", "1.2.3", False), ("1.1.0", "1.0.7, 1.1.4", False),
    ("1.0.8", "1.0.7, 1.1.4", True), ("2.0.0", "1.0.7, 1.1.4", True),
    ("1.2.3-rc.1", "1.2.3", False), ("1!2.0", "1!2.0", True), ("1.0", "", False),
])
def test_dependency_resolution_does_not_guess_version_schemes(current, fixed, safe):
    assert security.safe_dependency_version(current, fixed) == safe


@pytest.mark.parametrize("case", ["valid", "other_package", "affected", "mixed", "prerelease", "original_unaffected",
    "complex_range", "missing_range", "wrong_package", "wrong_cve", "withdrawn", "unreviewed", "wrong_url",
    "missing_updated", "wrong_ecosystem", "unsupported_ecosystem", "malformed", "multiple", "short_version", "v_prefix", "leading_zero"])
def test_dependency_advisory_range_requires_current_matching_reviewed_evidence(monkeypatch, case):
    evidence = {"package_type": "bun", "rule_id": "CVE-2026-93748", "package": "http-cache-semantics", "installed_version": "4.2.0"}
    advisory = deepcopy(DEPENDENCY_ADVISORY)
    packages = [{"name": evidence["package"], "version": "4.3.0"}]
    entry = advisory["vulnerabilities"][0]
    if case == "other_package":
        evidence["package"] = entry["package"]["name"] = packages[0]["name"] = "another-dependency"
    if case == "affected":
        packages[0]["version"] = "4.1.0"
    if case == "mixed":
        packages.append({"name": evidence["package"], "version": "4.2.0"})
    if case == "prerelease":
        packages[0]["version"] = "4.3.0-rc.1"
    if case in {"short_version", "v_prefix", "leading_zero"}:
        packages[0]["version"] = {"short_version": "4.3", "v_prefix": "v4.3.0", "leading_zero": "04.3.0"}[case]
    if case == "original_unaffected":
        evidence["installed_version"] = "4.3.0"
    if case in {"complex_range", "missing_range"}:
        entry["vulnerable_version_range"] = ">= 1.0.0, < 4.2.0 || >= 4.3.0" if case == "complex_range" else None
    if case == "wrong_package":
        entry["package"]["name"] = "different-package"
    if case == "wrong_cve":
        advisory["cve_id"] = "CVE-2026-12345"
    if case == "withdrawn":
        advisory["withdrawn_at"] = "2026-10-03T00:00:00Z"
    if case == "unreviewed":
        advisory["type"] = "unreviewed"
    if case == "wrong_url":
        advisory["html_url"] = "https://untrusted.example/advisory"
    if case == "missing_updated":
        advisory.pop("updated_at")
    if case == "wrong_ecosystem":
        entry["package"]["ecosystem"] = "pip"
    if case == "unsupported_ecosystem":
        evidence["package_type"] = "pip"
    response = {} if case == "malformed" else [advisory, advisory] if case == "multiple" else [advisory]
    calls = []

    def request(method, path, token):
        calls.append((method, path, token))
        return response

    monkeypatch.setattr(github, "request", request)
    cache = {}
    proof = security.dependency_advisory(evidence, packages, lambda: "token", lambda: False, cache)
    assert bool(proof) == (case in {"valid", "other_package"})
    if proof:
        assert proof["affected_range"] == "<= 4.2.0" and proof["current_versions"] == ["4.3.0"]
        assert proof["url"] == advisory["html_url"] and proof["updated_at"] == advisory["updated_at"]
        assert security.dependency_advisory(evidence, packages, lambda: "token", lambda: False, cache) == proof
    assert len(calls) == (0 if case == "unsupported_ecosystem" else 1)


@pytest.mark.parametrize("failure", ["network", "cancel_before", "cancel_after", "timeout"])
def test_dependency_advisory_lookup_stops_safely_without_manufacturing_a_fix(monkeypatch, failure):
    evidence = {"package_type": "bun", "rule_id": "CVE-2026-93748", "package": "http-cache-semantics", "installed_version": "4.2.0"}
    calls = []

    def request(*args):
        calls.append(1)
        if failure == "network":
            raise github.GitHubError(503, "Unavailable")
        return []

    def stopped():
        if failure == "timeout":
            raise TimeoutError("Deadline reached")
        return failure == "cancel_before" or failure == "cancel_after" and bool(calls)

    monkeypatch.setattr(github, "request", request)
    error = ValueError if failure == "network" else TimeoutError if failure == "timeout" else security.ReviewStopped
    with pytest.raises(error):
        security.dependency_advisory(evidence, [{"name": evidence["package"], "version": "4.3.0"}], lambda: "token", stopped, {})
    assert len(calls) == int(failure in {"network", "cancel_after"})


def test_trivy_secret_values_are_hmac_identified_and_removed_from_evidence(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    raw = "API_TOKEN = 'synthetic-credential-for-tests'\n"
    value = "synthetic-credential-for-tests"
    item.read = lambda path, sha=None: raw
    item.inventory = {"app.py": {"hash": digest(raw), "lines": 1}}
    report = {"Results": [{"Target": str(item.files / "app.py"), "Class": "secret", "Secrets": [
        {"RuleID": "test-token", "Title": "Exposed token", "Severity": "HIGH", "StartLine": 1, "EndLine": 1,
         "Offset": raw.index(value), "Match": raw.strip().replace(value, "*" * len(value))}]}]}
    result = security_engine.normalize_trivy(item, report)
    assert value not in json.dumps(result)
    assert value not in item.source("app.py")
    finding = result["findings"][0]
    assert finding["identity"] == security_engine.secure_digest("test-token\0" + value)
    assert raw.encode()[finding["match_offset"]:finding["match_offset"] + finding["match_bytes"]].decode() == value
    assert security_engine.secure_digest(value) != digest(value)


def test_trivy_refuses_targets_outside_prepared_snapshot(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    with pytest.raises(ValueError):
        security_engine.normalize_trivy(item, {"Results": [{"Target": "../outside", "Secrets": []}]})


def test_snapshot_never_follows_symlinks_or_executes_hooks(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, security_engine.process.child_environment(), lambda: False)
    item.git.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=item.git, check=True)
    (item.git / "app.py").write_text("print('fixture')\n")
    (item.git / "escape").symlink_to("/etc/passwd")
    subprocess.run(["git", "add", "app.py", "escape"], cwd=item.git, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture", "--no-verify"], cwd=item.git, check=True)
    item.sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=item.git, text=True).strip()
    (item.git / ".git/hooks/post-checkout").write_text("#!/bin/sh\ntouch ../executed\n")
    (item.git / ".git/hooks/post-checkout").chmod(0o700)
    item.populate()
    assert list(item.inventory) == ["app.py"]
    assert not (item.files / "escape").exists() and not (tmp_path / "executed").exists()
    assert item.excluded[0]["reason"] == "Symlink or submodule is not followed"


def test_snapshot_analyzes_astro_without_loading_protected_generated_or_documentation_files(tmp_path, monkeypatch):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    paths = ["src/page.astro", "src/App.vue", "src/App.svelte", "src/generated.generated.astro",
             ".agents/skill/page.astro", "README.md", "src/content/docs/article.mdx", "public/index.html",
             "src/credentials.py", "agents.md", ".env"]
    source = "<script>throw new Error('repository code must never execute')</script>\n"
    entries = "\0".join(f"100644 blob {'c' * 40} {len(source)}\t{path}" for path in paths)
    monkeypatch.setattr(security_engine.git_source, "git", lambda *args, **kwargs: entries)
    item.read = lambda path, sha=None: source
    item.populate()
    assert {path for path, value in item.inventory.items() if value["code"]} == set(paths[:3])
    assert all((item.files / path).read_text() == source for path in paths)
    assert {row["path"] for row in item.excluded} == set(paths[3:])
    with pytest.raises(ValueError, match="agent instructions"):
        item.source(".agents/skill/page.astro")


def test_snapshot_and_model_diff_withhold_unsupported_git_paths(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, security_engine.process.child_environment(), lambda: False)
    item.git.mkdir()

    def git(*args):
        return security_engine.git_source.git(item.git, item.env, *args)

    git("init", "--quiet")
    for path, source in {"app.py": "original()", "before.py": "move_to_unsupported()",
                         "original\x7f.py": "move_from_unsupported()"}.items():
        (item.git / path).write_text(source + "\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "original")
    original = git("rev-parse", "HEAD").strip()
    (item.git / "app.py").write_text("current()\n")
    unsupported = "current\x7f.py"
    (item.git / "before.py").rename(item.git / unsupported)
    (item.git / "original\x7f.py").rename(item.git / "after.py")
    generated = item.git / "node_modules" / "generated\x7f.py"
    generated.parent.mkdir()
    generated.write_text("generated()\n")
    git("add", "-A")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "current")
    item.sha = git("rev-parse", "HEAD").strip()

    item.populate()
    assert set(item.inventory) == {"app.py", "after.py"}
    assert [problem["path"] for problem in item.problems] == [unsupported]
    assert [entry["path"] for entry in item.excluded] == ["node_modules/generated\x7f.py"]
    assert {path.name for path in item.files.iterdir()} == set(item.inventory)
    assert item.renamed(original, "before.py") == "before.py"
    with pytest.raises(ValueError, match="invalid repository path"):
        item.read(unsupported)
    diff = item.diff(original, model_context=True)
    assert "current()" in diff
    assert "move_to_unsupported()" not in diff and "move_from_unsupported()" not in diff


def test_model_verification_diff_fits_prompt_without_excluded_files_or_losing_evidence(tmp_path):
    from code_mortis.core import file_policy

    item = security_engine.Snapshot(tmp_path, SHA, security_engine.process.child_environment(), lambda: False,
        ai_policy=file_policy.policy({"exclusions": "fixtures/**,to_dir.py/private.py,to_file.py/private.py"}))
    item.git.mkdir()

    def git(*args):
        return security_engine.git_source.git(item.git, item.env, *args)

    git("init", "--quiet")
    sources = {"app.py": "unsafe()\n", "caller.py": "old_caller()\n", "deleted.py": "deleted_helper()\n",
        'café "name".py': "old_unicode()\n", "uv.lock": "original_lock\n", "AGENTS.md": "old_agent\n",
        "renamed.lock": "excluded_rename_from_lock()\n", "before.py": "excluded_rename_to_secret()\n",
        "fixture_before.py": "excluded_rename_to_fixture()\n", "to_dir.py": "allow_parent()\n"}
    for path, source in sources.items():
        (item.git / path).write_text(source)
    (item.git / "to_file.py").mkdir()
    (item.git / "to_file.py" / "private.py").write_text("exclude_old_child()\n")
    (item.git / "image.png").write_bytes(b"\0old image")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "original")
    original = git("rev-parse", "HEAD").strip()
    (item.git / "app.py").write_text("safe()\n")
    (item.git / "caller.py").write_text("guarded_caller()\n")
    (item.git / 'café "name".py').write_text("new_unicode()\n")
    (item.git / "uv.lock").write_text("EXCLUDED_LOCK_DATA\n" * 30_000)
    (item.git / "AGENTS.md").write_text("EXCLUDED_AGENT_DATA\n")
    (item.git / "deleted.py").unlink()
    (item.git / "renamed.lock").rename(item.git / "renamed.py")
    (item.git / "before.py").rename(item.git / "secret.py")
    (item.git / "fixtures").mkdir()
    (item.git / "fixture_before.py").rename(item.git / "fixtures" / "example.py")
    (item.git / "to_dir.py").unlink()
    (item.git / "to_dir.py").mkdir()
    (item.git / "to_dir.py" / "private.py").write_text("exclude_new_child()\n")
    (item.git / "to_dir.py" / "public.py").write_text("allow_new_child()\n")
    (item.git / "to_file.py" / "private.py").unlink()
    (item.git / "to_file.py").rmdir()
    (item.git / "to_file.py").write_text("allow_new_parent()\n")
    (item.git / "image.png").write_bytes(b"\0new image")
    git("add", "-A")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "current")
    item.sha = git("rev-parse", "HEAD").strip()
    item.populate()
    excluded = "fixtures/example.py"
    assert not item.inventory[excluded]["code"] and (item.files / excluded).is_file()
    assert next(entry for entry in item.excluded if entry["path"] == excluded)["reason"].startswith("Excluded from AI")
    raw, filtered = item.diff(original), item.diff(original, model_context=True)
    assert len(raw.encode()) > 500_000 and "EXCLUDED_LOCK_DATA" in raw
    assert all(value in raw for value in ("exclude_old_child()", "exclude_new_child()"))
    assert all(value not in filtered for value in ("EXCLUDED_LOCK_DATA", "EXCLUDED_AGENT_DATA", "image.png",
        "excluded_rename_from_lock", "excluded_rename_to_secret", "renamed.py", "before.py",
        "fixture_before.py", excluded, "excluded_rename_to_fixture", "exclude_old_child()", "exclude_new_child()"))
    assert all(value in filtered for value in ("unsafe()", "safe()", "guarded_caller()", "deleted_helper()",
                                               "new_unicode()", "allow_new_child()"))
    context = {"finding": {"path": "app.py", "title": "Synthetic", "evidence": {"source_sha": original,
                   "evidence": [{"path": "app.py", "revision": "current", "start_line": 1, "end_line": 1, "quote": "unsafe()"}]}},
        "task_contract": security_engine.TASK_CONTRACT,
        "original_sha": original, "head_sha": item.sha, "diff": raw,
        "sources": {"original": {"app.py": item.source("app.py", original)}, "current": {"app.py": item.source("app.py")}}}
    connection = {"id": "connection", "kind": "api", "protocol": "openai", "model": "test",
                  "options": {"prompt_token_ceiling": 500_000}}
    reservations, supplied = [], []

    def infer(value, **kwargs):
        request = Request(instructions=kwargs["instructions"], messages=[Message(role="user",
            content=security_engine.verification.evidence_bundle(value))],
            output_schema=workflow_inference.schema_for_transport(kwargs["schema"]), max_output_tokens=16384, effort="high")
        reservations.append(llm.reservation(connection, request))
        supplied.append(value)
        evidence = [{"source_id": reference, "start_line": 1, "end_line": 1}
                    for reference in value["citation_sources"]]
        return kwargs["validator"]({"verdict": "fixed", "original_issue_present": True, "explanation": "Original defect checked.",
            "change_explanation": "The guard changed.", "evidence": evidence, "required_paths": []}, value), {"tokens": 12}

    with pytest.raises(ValueError, match="prompt token ceiling"):
        security_engine.ask(item, connection, {}, "verification", context, None, 60, lambda: False, "raw", infer=infer)
    result, usage = security_engine.ask(item, connection, {}, "verification", {**context, "diff": filtered},
        None, 60, lambda: False, "filtered", infer=infer)
    assert result["verdict"] == "fixed" and usage == {"tokens": 12}
    assert len(reservations) == 1 and reservations[0] < 500_000
    assert supplied[0]["sources"] == context["sources"] and supplied[0]["diff"] == filtered


@pytest.mark.parametrize("names", ["uv.lock\0AGENTS.md\0", "../outside.py\0", "bad\npath.py\0"])
def test_model_diff_never_falls_back_to_unfiltered_paths(tmp_path, monkeypatch, names):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)

    def git(*args, **kwargs):
        if "--name-only" in args:
            return names
        if "--name-status" in args:
            return "M\0uv.lock\0M\0AGENTS.md\0"
        pytest.fail("Excluded or unsafe paths reached the source diff")

    monkeypatch.setattr(security_engine.git_source, "git", git)
    if names.startswith("uv.lock"):
        assert item.diff(OLD, model_context=True) == ""
    else:
        with pytest.raises(ValueError, match="path"):
            item.diff(OLD, model_context=True)


def test_scanner_environment_excludes_all_application_and_provider_credentials(tmp_path, monkeypatch):
    for key in ("AWS_SECRET_ACCESS_KEY", "CM_DATABASE_URL", "CM_RUNNER_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.setenv(key, "private-value")
    env = security_engine.scanner_environment(tmp_path)
    assert "private-value" not in env.values()
    assert env["HOME"] == str(tmp_path)


def test_security_citations_require_exact_supplied_source():
    evidence = [{"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}]
    assert security_engine.validate_citations(evidence, {"current": {"app.py": "unsafe\n"}}) == {"current"}
    with pytest.raises(ValueError):
        security_engine.validate_citations(evidence, {"current": {"app.py": "safe\n"}})


def test_complete_pipeline_uses_existing_ai_transport_and_persists_grade(seed, security_github, monkeypatch, tmp_path):
    item = SimpleNamespace(inventory={"app.py": {"code": True, "hash": digest("safe"), "lines": 1}},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})

    @contextmanager
    def snapshot(*args):
        yield item

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}})
    calls = []

    def ask(*args, **kwargs):
        calls.append(args[3])
        return {"findings": [], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid()))
        identity = scan.id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "completed", scan.error
        assert scan.grade == "A" and scan.progress["tokens"] == 12
        assert scan.report["schema_version"] == security_engine.POLICY
    assert calls == ["analysis"]


@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("budget", [None, 1200])
@pytest.mark.parametrize("failure", [None, "citation", "evidence", "usage", "quota", "cancel"])
def test_security_parallel_analysis_accounts_for_every_file_and_stops_on_fatal_errors(
    seed, security_github, monkeypatch, tmp_path, concurrency, budget, failure,
):
    paths = ["a.py", "bad.py", "c.py", "d.py", "e.py", "f.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest("safe"), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    calls, configurations, active = [], [], [0, 0]
    original_ask = security_engine.ask
    lock, barrier = Lock(), Barrier(concurrency)

    @contextmanager
    def snapshot(*args):
        yield item


    def ask(*args, **kwargs):
        path, allowance = args[4]["assigned_path"], args[5]
        with lock:
            active[0] += 1
            active[1] = max(active)
            calls.append((path, allowance))
        barrier.wait(timeout=10)
        with lock:
            active[0] -= 1
        assert callable(kwargs["infer"])
        if path == "bad.py":
            if failure == "cancel":
                with transaction() as db:
                    security.cancel(db, db.get(SecurityScan, identity))
            if failure in {"citation", "usage"}:
                raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": None if failure == "usage" else 12},
                    feedback={"errors": [{"reason": "invented quote"}]})
            if failure == "quota":
                raise RunnerBlocked("quota")
            if failure == "evidence":
                return original_ask(*args, **kwargs)
        return {"findings": [], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}})
    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(workflow_inference, "execute", lambda *args, **kwargs: (
        {"findings": [], "required_paths": ["bad.py"]}, {"tokens": 12}))
    with transaction() as db:
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=concurrency, token_budget=budget))
        if budget is not None:
            scan.config = {key: value for key, value in scan.config.items() if key != "budget_schedule"}
        identity = scan.id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        if failure in {"usage", "quota", "cancel"}:
            assert scan.status == {"usage": "failed", "quota": "blocked", "cancel": "cancelled"}[failure], scan.error
            assert scan.progress["tokens"] is None and scan.grade is None
            assert len(calls) == (3 if concurrency == 3 else 2)
        else:
            assert scan.status == ("partial" if failure else "completed"), scan.error
            assert scan.grade == "A"
            assert scan.progress["tokens"] == (84 if failure == "evidence" else 72) and len(calls) == 6
            assert scan.stages["analysis"]["completed"] == (5 if failure else 6)
            assert scan.stages["analysis"]["failed"] == (1 if failure else 0)
            assert scan.stages["assessment"]["status"] == "completed"
            assert scan.stages["verification"]["status"] == "completed"
            assert scan.stages["report"]["status"] == "completed"
            if failure:
                task = scan.progress["tasks"]["analysis:bad.py"]
                if failure == "evidence":
                    assert task["validation_feedback"]["required_paths"] == ["bad.py"]
                    assert task["tokens"] == 24
                else:
                    assert task["error"] == "Invalid citation"
                assert scan.report["problems"] == ["Source analysis could not be validated: bad.py."]
        if budget:
            assert all(allowance > 0 for _, allowance in calls)
            assert sum(allowance for _, allowance in calls[:concurrency]) <= budget
            if len(calls) > concurrency:
                assert sum(allowance for _, allowance in calls[concurrency:2 * concurrency]) <= budget - 12 * concurrency
    assert active[1] == concurrency
    assert configurations == []


@pytest.mark.parametrize("value", [0, 9, 1.5, True, "4"])
def test_security_concurrency_is_validated_at_the_api_boundary(seed, security_github, client, value):
    response = client.post("/api/v1/security/repositories/22/scans", json={
        "connection_id": "connection", "request_key": uid(), "concurrency": value})
    assert response.status_code == 422


@pytest.mark.parametrize("failure", ["citation", "history_selection"])
def test_invalid_assessment_preserves_other_confirmed_findings(seed, security_github, monkeypatch, tmp_path, failure):
    item = SimpleNamespace(inventory={"app.py": {"code": True, "hash": digest("unsafe"), "lines": 1}},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "unsafe", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    finding = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
               "explanation": "Synthetic test", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
               "verification": "Test", "evidence": [citation]}
    original_ask = security_engine.ask

    def api(*args, **kwargs):
        try:
            return kwargs["validator"]({"verdict": "confirmed", "finding_id": "history_999", "explanation": "Confirmed",
                "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 1}], "required_paths": []}, args[2]), {"tokens": 12}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid history selection", {"tokens": 12}, feedback=exc.feedback) from exc

    @contextmanager
    def snapshot(*args):
        yield item

    def ask(*args, **kwargs):
        if args[3] == "analysis":
            return {"findings": [{**finding, "title": "Invalid candidate"}, finding], "required_paths": []}, {"tokens": 12}
        if args[4]["proposal"]["title"] == "Invalid candidate":
            if failure == "history_selection":
                return original_ask(*args, **kwargs)
            raise security_engine.verification.VerificationOutputError("Invented source", {"tokens": 12},
                feedback={"errors": [{"reason": "invented quote"}]})
        return {"verdict": "confirmed", "finding_id": None, "explanation": "Confirmed", "evidence": [citation], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(workflow_inference, "execute", api)
    with transaction() as db:
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid()))
        identity = scan.id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "partial" and scan.grade == "B", scan.error
        assert scan.progress["tokens"] == (48 if failure == "history_selection" else 36)
        assert scan.stages["assessment"]["completed"] == scan.stages["assessment"]["failed"] == 1
        assert scan.stages["assessment"]["status"] == "partial"
        assert scan.stages["verification"]["status"] == "completed"
        feedback = scan.progress["tasks"]["assessment:0"]["validation_feedback"]
        if failure == "history_selection":
            assert feedback == {"kind": "history_selection", "field": "finding_id", "allowed_values": [None]}
        else:
            assert feedback["errors"] == [{"reason": "invented quote"}]
        assert db.scalar(select(SecurityFinding.title)) == "Synthetic defect"
        assert len(scan.report["findings"]) == 1


@pytest.mark.parametrize("relationship", ["rename", "requested_source", "fix_evidence", "incomplete_trivy"])
def test_partial_assessment_closes_only_independent_fixes(seed, security_github, monkeypatch, tmp_path, relationship):
    related_path = "old.py" if relationship == "rename" else "dependent.py"
    paths = ("candidate.py", "dependent.py", "independent.py")
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {path: {"code": True, "hash": digest(path), "lines": 1} for path in paths}
    item.source = lambda path, sha=None: path
    item.renamed = lambda sha, path: "candidate.py" if path == "old.py" else path
    item.diff = lambda sha, **kwargs: "Source changed"

    def citation(path, revision="current"):
        return {"revision": revision, "path": path, "start_line": 1, "end_line": 1, "quote": path}

    proposal = {"title": "Pending candidate", "category": "test", "severity": "low", "path": "candidate.py",
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                "verification": "Test", "evidence": [citation("candidate.py")]}

    def ask(*args, **kwargs):
        task, context = args[3:5]
        if task == "analysis":
            return {"findings": [proposal] if context["assigned_path"] == "candidate.py" else [], "required_paths": []}, {"tokens": 1}
        if task == "assessment":
            if relationship == "requested_source":
                kwargs["record_required_paths"]([related_path])
            return {"verdict": "uncertain", "finding_id": None, "explanation": "Still needs evaluation",
                    "evidence": [citation("candidate.py")], "required_paths": []}, {"tokens": 1}
        evidence = [citation(context["finding"]["path"], "original"), citation(next(iter(context["sources"]["current"])))]
        if relationship == "fix_evidence" and context["finding"]["path"] == related_path:
            evidence.append(citation("candidate.py"))
        return {"verdict": "fixed", "explanation": "Correction verified", "evidence": evidence}, {"tokens": 1}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {},
        "problems": ["Coverage incomplete"] if relationship == "incomplete_trivy" else [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        prior = saved_scan(db, seed, status="completed")
        related = saved_finding(db, seed, prior, path=related_path).id
        independent = saved_finding(db, seed, prior, path="independent.py").id
        scan_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid())).id
    perform_security(scan_id, "test:1")

    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        assert scan.status == scan.report["status"] == "partial", scan.error
        assert scan.stages["assessment"]["status"] == "partial"
        assert scan.stages["verification"]["completed"] == 2
        assert scan.report["unsettled_candidates"][0]["proposal"]["path"] == "candidate.py"
        assert db.get(SecurityFinding, related).state == "open"
        blocked = relationship == "incomplete_trivy"
        assert db.get(SecurityFinding, independent).state == ("open" if blocked else "resolved")
        assert {row["finding_id"]: row["outcome"] for row in scan.report["findings"]} == {
            related: "not_observed", independent: "not_observed" if blocked else "fixed",
        }
        assert next(row for row in scan.report["findings"] if row["finding_id"] == related)["verification"]["verdict"] == "fixed"


@pytest.mark.parametrize(("earlier_error", "assessment_result", "verification_result"), [
    (None, "rejected", "still_present"),
    ("analysis", "rejected", "still_present"),
    ("trivy", "rejected", "still_present"),
    (None, "uncertain", "still_present"),
    (None, "error", "still_present"),
    ("analysis", "rejected", "uncertain"),
    (None, "rejected", "error"),
])
def test_security_stage_status_only_reflects_its_own_results(
    seed, security_github, monkeypatch, tmp_path, earlier_error, assessment_result, verification_result,
):
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest("unsafe"), "lines": 1}
                                     for path in ["app.py", "bad.py"]},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "unsafe",
        renamed=lambda sha, path: path, diff=lambda sha, **kwargs: "", redact=lambda text: text, sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    proposal = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
        "verification": "Test", "evidence": [citation]}

    def ask(*args, **kwargs):
        task, context = args[3:5]
        if task == "analysis":
            if context["assigned_path"] == "bad.py":
                if earlier_error == "analysis":
                    raise security_engine.verification.VerificationOutputError("Invalid analysis citation", {"tokens": 12},
                        feedback={"errors": [{"reason": "invented quote"}]})
                return {"findings": [], "required_paths": []}, {"tokens": 12}
            return {"findings": [proposal], "required_paths": []}, {"tokens": 12}
        verdict = assessment_result if task == "assessment" else verification_result
        if verdict == "error":
            if task == "verification":
                raise ValueError("Original evidence unavailable")
            raise security_engine.verification.VerificationOutputError("Invalid assessment citation", {"tokens": 12},
                feedback={"errors": [{"reason": "invented quote"}]})
        return {"verdict": verdict, "finding_id": None, "explanation": "Checked", "evidence": [citation],
                "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {},
        "problems": ["Dependency scan incomplete"] if earlier_error == "trivy" else [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        finding = saved_finding(db, seed, saved_scan(db, seed, status="completed"))
        finding_id = finding.id
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid())).id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        partial = bool(earlier_error or assessment_result != "rejected" or verification_result != "still_present")
        assert scan.status == ("partial" if partial else "completed"), scan.error
        assert scan.stages["assessment"]["status"] == ("completed" if assessment_result == "rejected" else "partial")
        assert scan.stages["verification"]["status"] == ("completed" if verification_result == "still_present" else "partial")
        assert scan.stages["verification"]["completed"] == scan.stages["verification"]["total"] == 1
        assert bool(scan.error) == bool(scan.report["problems"]) == partial
        assert scan.stages["report"]["status"] == "completed" and scan.grade == "D"
        assert db.get(SecurityFinding, finding_id).state == "open"


@pytest.mark.parametrize("concurrency", [1, 4])
@pytest.mark.parametrize("budget", [None, 1200])
@pytest.mark.parametrize("failure", [None, "citation", "evidence", "usage", "quota", "cancel", "timeout"])
def test_security_assessment_uses_configured_concurrency_and_preserves_limits(
    seed, security_github, monkeypatch, tmp_path, concurrency, budget, failure,
):
    paths = ["a.py", "bad.py", "c.py", "d.py", "e.py", "f.py", "g.py", "h.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest(path), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: path,
        redact=lambda text: text, sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    calls, configurations, active = [], [], [0, 0]
    original_ask = security_engine.ask
    lock, barrier = Lock(), Barrier(concurrency)
    gate_failure = concurrency > 1 and budget is None and failure in {"usage", "quota", "cancel", "timeout"}
    failure_collected = Event()
    original_stage = security.stage
    clock = [0]
    if failure == "timeout":
        monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])

    def stage(scan_id, name, status, **values):
        original_stage(scan_id, name, status, **values)
        if name == "assessment" and status == "running" and "failed" in values and "total" not in values:
            failure_collected.set()

    def ask(*args, **kwargs):
        task, context, allowance = args[3:6]
        if task == "analysis":
            path = context["assigned_path"]
            citation = {"revision": "current", "path": path, "start_line": 1, "end_line": 1, "quote": path}
            return {"findings": [{"title": path, "category": "test", "severity": "low", "path": path,
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                "verification": "Test", "evidence": [citation]}], "required_paths": []}, {"tokens": 12}
        assert task == "assessment"
        path = context["proposal"]["path"]
        with lock:
            active[0] += 1
            active[1] = max(active)
            calls.append((path, allowance))
        barrier.wait(timeout=10)
        if gate_failure and path != "bad.py":
            assert failure_collected.wait(10), "A fault result must be collected before other slots free up."
        with lock:
            active[0] -= 1
        assert callable(kwargs["infer"])
        if path == "bad.py":
            if failure == "cancel":
                with transaction() as db:
                    security.cancel(db, db.get(SecurityScan, identity))
            if failure in {"citation", "usage"}:
                raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": None if failure == "usage" else 12},
                    feedback={"errors": [{"reason": "invented quote"}]})
            if failure == "quota":
                raise RunnerBlocked("quota")
            if failure == "timeout":
                clock[0] = 61
            if failure == "evidence":
                return original_ask(*args, **kwargs)
        return {"verdict": "confirmed", "finding_id": None, "explanation": "Confirmed",
                "evidence": context["proposal"]["evidence"], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    if gate_failure:
        monkeypatch.setattr(security, "stage", stage)
    monkeypatch.setattr(workflow_inference, "execute", lambda *args, **kwargs: (
        {"verdict": "uncertain", "finding_id": None, "explanation": "Need the guard", "evidence": [], "required_paths": ["missing_guard.py"]}, {"tokens": 12}))
    with transaction() as db:
        scan = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=concurrency, token_budget=budget,
            timeout_seconds=60 if failure == "timeout" else None))
        if budget is not None:
            scan.config = {key: value for key, value in scan.config.items() if key != "budget_schedule"}
        identity = scan.id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        if failure in {"usage", "quota", "cancel", "timeout"}:
            assert scan.status == {"usage": "partial" if concurrency == 1 else "failed", "quota": "blocked", "cancel": "cancelled",
                                   "timeout": "partial" if concurrency == 1 else "failed"}[failure], scan.error
            assert scan.grade is None
            assert scan.progress["tokens"] == (96 + 12 * len(calls) if failure == "timeout" else None)
            assert len(calls) == (4 if concurrency == 4 else 2)
        else:
            assert scan.status == ("partial" if failure else "completed"), scan.error
            assert scan.grade == "B"
            assert scan.progress["tokens"] == (204 if failure == "evidence" else 192) and len(calls) == 8
            assert scan.stages["assessment"]["completed"] == (7 if failure == "citation" else 8)
            assert scan.stages["assessment"]["failed"] == (1 if failure == "citation" else 0)
            assert len(scan.report["findings"]) == (7 if failure else 8)
            if failure:
                task = scan.progress["tasks"]["assessment:1"]
                if failure == "evidence":
                    assert task["status"] == "completed" and not task.get("error")
                    assert task["result"]["verdict"] == "uncertain" and task["result"]["required_paths"] == []
                    assert "missing_guard.py" in task["result"]["explanation"] and "prepared snapshot" in task["result"]["explanation"]
                    assert len(scan.report["unsettled_candidates"]) == 1
                    assert scan.report["unsettled_candidates"][0]["assessment"] == task["result"]
                else:
                    assert task["error"] == "Invalid citation"
        if budget:
            assert sum(allowance for _, allowance in calls[:concurrency]) <= budget - 96
            if len(calls) > concurrency:
                assert sum(allowance for _, allowance in calls[concurrency:2 * concurrency]) <= budget - 96 - 12 * concurrency
    assert active[1] == concurrency
    assert configurations == []


def test_security_assessment_batches_share_paths_anchors_and_renamed_history():
    def proposal(path, quote):
        return {"path": path, "evidence": [{"path": path, "quote": quote}]}

    proposals = [proposal("new.py", "first"), proposal("same.py", "shared"), proposal("same.py", "other"),
                 proposal("another.py", "shared"), proposal("bridge.py", "last"), proposal("independent.py", "independent")]
    rows = [SimpleNamespace(id=number, position=number, proposal=value) for number, value in enumerate(proposals)]
    history = [{"path": "old.py", "evidence": {"source_sha": OLD, "evidence": [{"path": "bridge.py", "quote": "old"}]}},
               {"path": "bridge.py", "evidence": {"source_sha": SHA, "locations": [{"path": "same.py"}]}}]
    item = SimpleNamespace(sha=SHA, renamed=lambda sha, path: "new.py" if path == "old.py" else path)
    assert [row.id for row in security.assessment_batch(rows, history, item, 4)] == [0, 5]
    assert [row.id for row in security.assessment_batch(rows[1:], history, item, 4)] == [1, 5]


def test_security_assessment_frontier_allows_independent_candidates_before_a_shared_target():
    rows = [SimpleNamespace(id=number) for number in range(4)]
    keys = {0: {"left"}, 1: {"right"}, 2: {"left", "right"}, 3: {"right"}}
    assert security.assessment_frontier(rows, keys) == rows[:2]
    assert security.assessment_frontier(rows, keys, admitted={0}) == rows[1:2]
    assert security.assessment_frontier(rows[2:], keys) == rows[2:3]


def test_security_assessment_ignores_supporting_path_bridges_but_orders_finding_updates(
    seed, security_github, monkeypatch, tmp_path,
):
    paths = ("first.py", "independent.py", "second.py")
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {path: {"code": True, "hash": digest(path), "lines": 1} for path in paths}
    item.source = lambda path, sha=None: path
    item.renamed = lambda sha, path: path
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {
        "findings": [], "targets": {}, "problems": [], "metadata": {},
    })

    def citation(path, quote=None):
        return {"revision": "current", "path": path, "start_line": 1, "end_line": 1, "quote": quote or path}

    def proposal(path):
        return {"title": path, "category": "test", "severity": "low", "path": path,
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact",
                "remediation": "Fix", "verification": "Test", "evidence": [citation(path)]}

    with transaction() as db:
        earlier = saved_scan(db, seed, status="completed")
        target = saved_finding(db, seed, earlier, path="second.py",
            fingerprint=security.assessment_fingerprint(proposal("first.py")),
            evidence=[citation("second.py"), citation("shared.py", "first usage")])
        target.evidence = {**target.evidence, "fingerprint": digest("later observation")}
        target_id = target.id
        independent_id = saved_finding(db, seed, earlier, path="independent.py",
            evidence=[citation("independent.py"), citation("shared.py", "second usage")]).id
        scan_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=3)).id

    barrier = Barrier(2)

    def ask(*args, **kwargs):
        task, context = args[3:5]
        if task == "analysis":
            return {"findings": [proposal(context["assigned_path"])], "required_paths": []}, {"tokens": 12}
        assert task == "assessment"
        candidate = context["proposal"]
        path = candidate["path"]
        historical, = context["history"]
        assert historical["id"] == (independent_id if path == "independent.py" else target_id)
        evidence = candidate["evidence"]
        finding_id = historical["id"]
        if path in paths[:2]:
            barrier.wait(timeout=10)
            if path == "first.py":
                # Implicit fingerprint lookup still updates the same finding as an explicit ID.
                finding_id = None
                evidence = [*evidence, citation("second.py")]
        else:
            assert historical["path"] == "first.py" and historical["version"] == 1
        return {"verdict": "confirmed", "finding_id": finding_id, "explanation": "Checked",
                "evidence": evidence, "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "ask", ask)
    perform_security(scan_id, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        assert scan.status == "completed", scan.error
        assert scan.stages["assessment"]["completed"] == 3
        assert db.get(SecurityFinding, target_id).version == 2
        assert db.get(SecurityFinding, independent_id).version == 1


@pytest.mark.parametrize("discovery", [
    "queued", "in_flight", "same_wave", "late_later", "shared_expansion", "external_history", "late_quota", "late_unknown",
])
@pytest.mark.parametrize("budget", [None, 200_000])
def test_security_assessment_refills_free_slots_and_waits_for_saved_history(
    seed, security_github, monkeypatch, tmp_path, discovery, budget,
):
    paths = ("first.py", "slow1.py", "slow2.py", "slow3.py", "linked.py", "fifth.py")
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {path: {"code": True, "hash": digest(path), "lines": 1} for path in paths}
    item.source = lambda path, sha=None: path
    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {
        "findings": [], "targets": {}, "problems": [], "metadata": {},
    })

    def citation(path):
        return {"revision": "current", "path": path, "start_line": 1, "end_line": 1, "quote": path}

    initial = set(paths[:4])
    late_failure = discovery in {"late_quota", "late_unknown"}
    same_wave = discovery in {"same_wave", "late_later", "shared_expansion"}
    refilled_path = "linked.py" if discovery == "in_flight" or same_wave or late_failure else "fifth.py"
    barrier, lock = Barrier(4), Lock()
    release, refilled, first_collected = Event(), Event(), Event()
    initial_collected = Event()
    started = []
    retrying, retry_matches = False, []
    active = peak = 0
    original_stage = security.stage

    def stage(scan_id, name, status, **values):
        original_stage(scan_id, name, status, **values)
        if name == "assessment":
            if values.get("completed") == 1:
                first_collected.set()
            if values.get("completed") == 4:
                initial_collected.set()

    def ask(*args, **kwargs):
        nonlocal active, peak
        task, context = args[3:5]
        if task == "analysis":
            path = context["assigned_path"]
            proposal = {"title": path, "category": "test", "severity": "low", "path": path,
                        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact",
                        "remediation": "Fix", "verification": "Test", "evidence": [citation(path)]}
            proposals = [proposal, {**proposal, "title": "Related"}] if path == "first.py" else [proposal]
            return {"findings": proposals, "required_paths": []}, {"tokens": 12}
        assert task == "assessment"
        proposal = context["proposal"]
        title = proposal["title"]
        if retrying:
            match = None
            if title in {"Related", "slow1.py"}:
                match, = [entry["id"] for entry in context["history"] if entry["id"] == first_id]
            if title == "slow1.py":
                retry_matches.append(match)
            return {"verdict": "confirmed", "finding_id": match, "explanation": "Checked",
                    "evidence": proposal["evidence"], "required_paths": []}, {"tokens": 12}
        with lock:
            started.append(title)
            active += 1
            peak = max(peak, active)
        try:
            if title in initial:
                barrier.wait(timeout=10)
            if title == refilled_path:
                refilled.set()
                assert release.wait(10)
                if late_failure:
                    assert initial_collected.wait(10)
                    if discovery == "late_quota":
                        raise RunnerBlocked("quota", {"tokens": 12})
                    raise security_engine.verification.VerificationOutputError("Unknown usage", {"tokens": None})
            if title in initial - {"first.py"}:
                assert release.wait(10)
            if late_failure and title == "fifth.py":
                assert initial_collected.wait(10)
            if title == "first.py" and discovery == "external_history":
                with transaction() as db:
                    saved_finding(db, seed, db.get(SecurityScan, identity), path="external.py")
            finding_id = None
            if title == "Related" or discovery == "queued" and title == "linked.py":
                accepted, = context["history"]
                assert accepted["path"] == "first.py"
                with transaction() as db:
                    assert db.scalar(select(SecurityOccurrence.id).where(
                        SecurityOccurrence.scan_id == identity, SecurityOccurrence.finding_id == accepted["id"]))
                if title == "Related":
                    finding_id = accepted["id"]
            evidence = proposal["evidence"]
            if (discovery == "queued" and title in {"first.py", "Related"}
                    or discovery == "in_flight" and title == "slow1.py"):
                evidence = [*evidence, citation("linked.py")]
            elif discovery == "same_wave" and title == "first.py":
                evidence = [*evidence, citation("slow1.py")]
            elif discovery == "late_later" and title == "slow1.py":
                evidence = [*evidence, citation("first.py")]
            elif discovery == "shared_expansion" and title in {"first.py", "slow1.py"}:
                evidence = [*evidence, citation("support.py")]
            return {"verdict": "confirmed", "finding_id": finding_id, "explanation": "Checked",
                    "evidence": evidence, "required_paths": []}, {"tokens": 12}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(security, "stage", stage)
    with transaction() as db:
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=4, token_budget=budget)).id

    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(perform_security, identity, "test:1")
        try:
            if discovery == "external_history":
                assert first_collected.wait(10)
            else:
                assert refilled.wait(10), "The next independent candidate waited for the whole batch."
                with lock:
                    assert set(started) == initial | {refilled_path} and active == 4
                with transaction() as db:
                    scan = db.get(SecurityScan, identity)
                    assert scan.stages["assessment"]["completed"] == 1
                    assert not db.scalar(select(SecurityOccurrence.id).where(SecurityOccurrence.scan_id == identity))
        finally:
            release.set()
        worker.result(timeout=10)

    assert peak == 4
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        if discovery == "external_history":
            assert len(started) == 4 and set(started) == initial
            assert scan.status == "failed" and "history changed" in scan.error
            assert scan.progress["tokens"] == 120
            assert not db.scalar(select(SecurityOccurrence.id).where(SecurityOccurrence.scan_id == identity))
        elif late_failure:
            assert scan.status == ("blocked" if discovery == "late_quota" else "partial"), scan.error
            assert ("selected provider limit" if discovery == "late_quota" else "Unknown usage") in scan.error
            assert scan.stages["assessment"]["status"] == "failed"
            assert set(db.scalars(select(SecurityFinding.path))) == initial
            assert {finding["path"] for finding in scan.report["findings"]} == initial
            assert "Related" not in started
            assert scan.progress["tokens"] == (72 + 12 * len(started) if discovery == "late_quota" else None)
        else:
            assert len(started) == scan.stages["assessment"]["completed"] == 7
            assert scan.progress["tokens"] == 156
            if discovery == "queued":
                assert scan.status == "completed", scan.error
                assert started[-2:] == ["Related", "linked.py"]
            else:
                assert scan.status == "partial"
                position, withheld = (2, "slow1.py") if same_wave else (5, "linked.py")
                task = scan.progress["tasks"][f"assessment:{position}"]
                assert task["tokens"] == 12 and task["result"]["verdict"] == "uncertain"
                assert task["stale_history"] is True
                assert "history changed" in task["result"]["explanation"]
                assert not db.scalar(select(SecurityFinding.id).where(SecurityFinding.path == withheld))
                assert scan.report["unsettled_candidates"][0]["assessment"] == task["result"]
        if discovery == "late_later":
            first_id = db.scalar(select(SecurityFinding.id).where(SecurityFinding.path == "first.py"))
            retry_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
                connection_id="connection", request_key=uid(), concurrency=4, token_budget=budget), parent=scan).id

    if discovery == "late_later":
        retrying = True
        perform_security(retry_id, "retry:1")
        with transaction() as db:
            retry = db.get(SecurityScan, retry_id)
            assert retry.status == "completed", retry.error
            assert retry_matches == [first_id]
            assert db.get(SecurityFinding, first_id).path == "slow1.py"


@pytest.mark.parametrize("history_changed", [False, True])
def test_parallel_security_assessment_keeps_related_findings_ordered_and_rejects_stale_history(
    seed, security_github, monkeypatch, tmp_path, history_changed,
):
    paths = ["same.py", "independent.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest(path), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: path,
        redact=lambda text: text, sha=SHA, git=tmp_path, files=tmp_path / "files", env={}, renamed=lambda sha, path: path)
    barrier, calls = Barrier(2), []
    first_assessed = Event()
    original_stage = security.stage

    def stage(scan_id, name, status, **values):
        original_stage(scan_id, name, status, **values)
        if name == "assessment" and values.get("completed") == 1:
            first_assessed.set()

    def ask(*args, **kwargs):
        task, context = args[3:5]
        if task == "analysis":
            path = context["assigned_path"]
            citation = {"revision": "current", "path": path, "start_line": 1, "end_line": 1, "quote": path}
            proposal = {"title": path, "category": "test", "severity": "low", "path": path,
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                "verification": "Test", "evidence": [citation]}
            return {"findings": [proposal, {**proposal, "title": "Reworded"}] if path == "same.py" else [proposal],
                    "required_paths": []}, {"tokens": 12}
        assert task == "assessment"
        proposal = context["proposal"]
        calls.append(proposal["title"])
        if proposal["title"] != "Reworded":
            barrier.wait(timeout=10)
            if history_changed and proposal["path"] == "same.py":
                with transaction() as db:
                    saved_finding(db, seed, db.get(SecurityScan, identity), path="external.py")
            if not history_changed and proposal["path"] == "independent.py":
                assert first_assessed.wait(timeout=10), "Assessment progress waited for the entire batch."
                with transaction() as db:
                    assessment_stage = db.get(SecurityScan, identity).stages["assessment"]
                    assert assessment_stage["completed"] == 1 and assessment_stage["failed"] == 0
                    assert not list(db.scalars(select(SecurityOccurrence)))
            finding_id = None
        else:
            matches = [row for row in context["history"] if row["path"] == "same.py"]
            assert len(matches) == 1, "A related proposal must see the preceding accepted finding."
            finding_id = matches[0]["id"]
        return {"verdict": "confirmed", "finding_id": finding_id, "explanation": "Confirmed",
                "evidence": proposal["evidence"], "required_paths": []}, {"tokens": 12}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    monkeypatch.setattr(security, "stage", stage)
    with transaction() as db:
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=4)).id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        findings = list(db.scalars(select(SecurityFinding)))
        if history_changed:
            assert scan.status == "failed" and scan.grade is None
            assert "history changed" in scan.error
            assert len(findings) == 1 and findings[0].path == "external.py"
            assert scan.progress["tokens"] == 48 and len(calls) == 2
            assert not list(db.scalars(select(SecurityOccurrence)))
        else:
            assert scan.status == "completed", scan.error
            assert len(findings) == 2 and len(list(db.scalars(select(SecurityOccurrence)))) == 2
            assert scan.progress["tokens"] == 60
            assert calls[-1] == "Reworded"
            assert scan.stages["assessment"]["completed"] == 3


@pytest.mark.parametrize("task", ["assessment", "verification"])
@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("concurrency", [1, 2])
@pytest.mark.parametrize("budget", [None, 1000])
@pytest.mark.parametrize("policy_changed", [False, True])
def test_explicit_security_retry_rechecks_uncertainty_and_reuses_settled_work(
    seed, security_github, monkeypatch, tmp_path, task, uncertain, concurrency, budget, policy_changed,
):
    paths = ["app.py", "other.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest("unsafe"), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "unsafe",
        renamed=lambda sha, path: path, diff=lambda sha, **kwargs: "source changed", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    proposals = [{"title": path, "category": "test", "severity": "low", "path": path,
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
        "verification": "Test", "evidence": [{"revision": "current", "path": path,
            "start_line": 1, "end_line": 1, "quote": "unsafe"}]} for path in paths]
    calls, retrying = [], False

    def ask(item, connection, credentials, current_task, context, allowance, timeout, stopped, identity, **kwargs):
        path = context["assigned_path"] if current_task == "analysis" else context["proposal" if current_task == "assessment" else "finding"]["path"]
        calls.append((current_task, path, allowance))
        assert not stopped()
        if current_task == "analysis":
            return {"findings": [proposals[paths.index(path)]] if task == "assessment" else [], "required_paths": []}, {"tokens": 7}
        verdict = "uncertain" if path == paths[0] and (not retrying or uncertain) else "rejected" if task == "assessment" else "still_present"
        return {"verdict": verdict, "finding_id": None, "explanation": "Checked",
                "evidence": proposals[paths.index(path)]["evidence"], "required_paths": []}, {"tokens": 7}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    body = {"connection_id": "connection", "token_budget": budget, "concurrency": concurrency}
    with transaction() as db:
        if task == "verification":
            original = saved_scan(db, seed, status="completed")
            for path in paths:
                saved_finding(db, seed, original, path=path)
        parent_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(**body, request_key=uid())).id
    perform_security(parent_id, "test:1")
    with transaction() as db:
        parent = db.get(SecurityScan, parent_id)
        assert parent.status == "partial" and parent.progress["tokens"] == 28
        original_progress = json.loads(json.dumps(parent.progress))
        retry_id = security.create_scan(db, seed["repo"], seed["user"],
            SecurityScanInput(**body, request_key=uid()), parent=parent).id
    calls.clear()
    retrying = True
    if policy_changed:
        monkeypatch.setattr(security_engine, "ASSESSMENT_INSTRUCTIONS", security_engine.ASSESSMENT_INSTRUCTIONS + " Revised decision criteria.")
    perform_security(retry_id, "retry:1")
    with transaction() as db:
        retry = db.get(SecurityScan, retry_id)
        expected = [(task, path) for path in paths] if task == "assessment" and policy_changed else [(task, paths[0])]
        assert sorted((kind, path) for kind, path, allowance in calls) == expected
        assert calls[0][2] is None if budget is None else 0 < calls[0][2] <= budget
        assert retry.progress["tokens"] == 7 * len(expected)
        assert retry.status == ("partial" if uncertain else "completed"), retry.error
        assert retry.stages[task]["status"] == ("partial" if uncertain else "completed")
        tasks = retry.progress["tasks"]
        assert sum(bool(value.get("reused")) for value in tasks.values()) == 4 - len(expected)
        assert all(value["citation_contract"] == security_engine.CITATION_CONTRACT for value in tasks.values())
        assert db.get(SecurityScan, parent_id).progress == original_progress
        assert db.get(SecurityScan, parent_id).status == "partial"
        assert all(finding.state == "open" for finding in db.scalars(select(SecurityFinding)))


@pytest.mark.parametrize("retry_change", ["none", "triage", "cancelled_child", "job_resume", "redaction"])
@pytest.mark.parametrize("trivy_observed", [True, False])
@pytest.mark.parametrize("concurrency", [1, 4])
def test_security_retry_after_final_github_error_reuses_completed_inference(
    seed, security_github, monkeypatch, tmp_path, retry_change, trivy_observed, concurrency,
):
    paths = ["app.py", "repeat.py", "old.py", "merge.py", "pending.py"]
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest(path), "lines": 1} for path in paths},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: path,
        renamed=lambda sha, path: path, diff=lambda sha, **kwargs: "source changed", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    proposals = {path: [{"title": f"{path} {index}", "category": "test", "severity": "low", "path": path,
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
        "verification": "Test", "evidence": [{"revision": "current", "path": path,
            "start_line": 1, "end_line": 1, "quote": path}]} for index in range(count)]
        for path, count in zip(paths, (3, 1, 0, 1, 1), strict=True)}
    credential = "synthetic private supplementary evidence"
    if retry_change == "redaction":
        proposals["pending.py"][0]["remediation"] = credential
    item.redact = lambda text: text.replace(credential, "[redacted]") if retrying and retry_change == "redaction" else text
    calls, issued = [], []
    retrying, expired = False, False
    snapshot_token = None
    request = github.request

    def installation_token(db, installation_id):
        issued.append(f"installation-{len(issued)}")
        return issued[-1]

    def github_request(method, path, token, **kwargs):
        if expired:
            assert token == issued[-1] and token != snapshot_token
            assert token in item.secret_values
            if not retrying:
                raise github.GitHubError(401, "GitHub authorization expired. Sign in again.")
        return request(method, path, token, **kwargs)

    def ask(item, connection, credentials, task, context, allowance, timeout, stopped, identity, **kwargs):
        nonlocal expired
        if task == "analysis":
            path = context["assigned_path"]
            calls.append((task, path))
            validated = security_engine.redacted_context(item, proposals[path])
            return {"findings": validated, "required_paths": []}, {"tokens": 7}
        if task == "verification":
            calls.append((task, context["finding"]["path"]))
            expired = True
            return {"verdict": "still_present", "explanation": "Checked", "evidence": []}, {"tokens": 7}
        proposal = context["proposal"]
        title = proposal["title"]
        calls.append((task, title))
        if title == "pending.py 0" and not retrying:
            raise security.verification.VerificationOutputError("Invalid evidence", {"tokens": 7},
                feedback={"kind": "citation_selection", "reason": "invalid source line range"})
        verdict = "confirmed" if title in {"app.py 1", "repeat.py 0", "merge.py 0"} else "rejected"
        return {"verdict": verdict, "finding_id": {"repeat.py 0": existing_id, "merge.py 0": merged_id}.get(title),
                "explanation": "Checked", "evidence": proposal["evidence"], "required_paths": []}, {"tokens": 7}

    def snapshot(name, sha, previous_shas, token, stopped):
        nonlocal snapshot_token
        snapshot_token = token
        return nullcontext(item)

    monkeypatch.setattr(github, "installation_token", installation_token)
    monkeypatch.setattr(github, "request", github_request)
    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {
        "findings": [deterministic] if trivy_observed else [], "targets": {}, "problems": [], "metadata": {},
    })
    monkeypatch.setattr(security_engine, "ask", ask)
    body = {"connection_id": "connection", "concurrency": concurrency}
    with transaction() as db:
        prior = saved_scan(db, seed, status="completed")
        saved_finding(db, seed, prior, path="old.py")
        existing = saved_finding(db, seed, prior, path="repeat.py")
        existing_id = existing.id
        merged = saved_finding(db, seed, prior, kind="infrastructure", path="merge.py", rule_id="synthetic",
                               sources=["trivy"], source_sha=SHA, source_hash=digest("merge.py"))
        merged_id, deterministic = merged.id, merged.evidence
        db.add(SecurityOccurrence(scan_id=prior.id, finding_id=merged_id, outcome="present",
            snapshot=security.finding_snapshot(merged)))
        db.add(SecurityOccurrence(scan_id=prior.id, finding_id=existing.id, outcome="present",
            snapshot={**security.finding_snapshot(existing), "verification": {"verdict": "still_present"}}))
        parent_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(**body, request_key=uid())).id
    perform_security(parent_id, "test:1")
    assert expired
    expired = False
    with transaction() as db:
        parent = db.get(SecurityScan, parent_id)
        assert parent.status == "partial" and parent.error == "GitHub authorization expired. Sign in again."
        assert parent.stages["verification"]["status"] == "completed"
        assert parent.progress["tokens"] == 84
        original = deepcopy((parent.progress, parent.report))
        if retry_change == "triage":
            changed = db.get(SecurityFinding, existing_id)
            changed.state, changed.version = "accepted_risk", changed.version + 1
        retry_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(**body, request_key=uid()), parent=parent).id

    if retry_change == "cancelled_child":
        stage = security.stage

        def stop_after_trivy(scan_id, name, status, **kwargs):
            stage(scan_id, name, status, **kwargs)
            if name == "trivy" and status == "completed":
                raise security.ReviewStopped("Interrupted after Trivy")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(security, "stage", stop_after_trivy)
            perform_security(retry_id, "cancelled-child:1")
        with transaction() as db:
            child = db.get(SecurityScan, retry_id)
            assert child.status == "cancelled" and child.progress["tasks"] == {}
            if trivy_observed:
                assert "ai_analysis" not in db.get(SecurityFinding, merged_id).evidence
            retry_id = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(**body, request_key=uid()), parent=child).id

    calls.clear()
    retrying = True
    if retry_change == "job_resume":
        finish = security.finish
        interruptions = 2
        def interrupted_finish(*args, **kwargs):
            nonlocal interruptions
            if interruptions:
                interruptions -= 1
                raise pat_provenance.TransientAuthorizationError("Temporary authorization outage")
            return finish(*args, **kwargs)
        monkeypatch.setattr(security, "finish", interrupted_finish)
        with transaction() as db:
            job_id = db.scalar(select(Job.id).where(Job.kind == "security_scan",
                Job.payload["scan_id"].as_string() == retry_id))
        for status in ("queued", "queued", "completed"):
            jobs.execute_job(job_id)
            with transaction() as db:
                db.get(Job, job_id).available_at = now()
                assert db.get(SecurityScan, retry_id).status == status
    else:
        perform_security(retry_id, "retry:1")
    with transaction() as db:
        retry, parent = db.get(SecurityScan, retry_id), db.get(SecurityScan, parent_id)
        assert retry.status == "completed", retry.error
        expected = [("assessment", "pending.py 0")]
        if retry_change == "triage":
            expected.append(("assessment", "repeat.py 0"))
            assert db.get(SecurityFinding, existing_id).state == "accepted_risk"
        if retry_change == "redaction":
            expected.insert(0, ("analysis", "pending.py"))
            assert retry.progress["tasks"]["analysis:pending.py"]["result"]["findings"][0]["remediation"] == "[redacted]"
        assert sorted(calls) == expected
        assert retry.progress["tokens"] == 7 * len(expected)
        assert sum(bool(task.get("reused")) for task in retry.progress["tasks"].values()) == 12 - len(expected)
        assert (parent.progress, parent.report) == original
        assert db.get(SecurityFinding, merged_id).evidence["ai_analysis"]["assessment"]["verdict"] == "confirmed"
        assert issued[-1] != snapshot_token and issued[-1] in item.secret_values


@pytest.mark.parametrize("context_changed", [False, True])
@pytest.mark.parametrize("original_timeout", [None, 45])
def test_security_retry_changes_request_timeout_without_losing_completed_work(
    seed, security_github, monkeypatch, tmp_path, original_timeout, context_changed,
):
    sources = {"done.py": "safe", "failed.py": "safe"}
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest(source), "lines": 1}
        for path, source in sources.items()}, excluded=[], problems=[], secret_values=[], secret_lines={},
        source=lambda path, sha=None: sources[path], redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    calls, retrying = [], False

    def ask(item, connection, credentials, task, context, *args, **kwargs):
        assert task == "analysis"
        calls.append(context["assigned_path"])
        if not retrying and context["assigned_path"] == "failed.py":
            raise security.verification.VerificationOutputError("The inference request exceeded its deadline.", {"tokens": None})
        assert connection["options"].get("timeout_sec") == (900 if retrying else original_timeout)
        return {"findings": [], "required_paths": []}, {"tokens": 7}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", ask)
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.options = {**connection.options, **({"timeout_sec": original_timeout} if original_timeout else {})}
        parent = security.create_scan(db, seed["repo"], seed["user"],
            SecurityScanInput(connection_id="connection", request_key=uid()))
        parent.config = {
            **{key: value for key, value in parent.config.items() if key not in {"execution_limits", "budget_schedule"}},
            "connection": connection_snapshot(connection),
        }
        parent_id = parent.id
    perform_security(parent_id, "test:1")
    with transaction() as db:
        parent = db.get(SecurityScan, parent_id)
        assert parent.status == "failed" and parent.progress["tokens"] is None
        assert parent.progress["tasks"]["analysis:done.py"]["status"] == "completed"
        original = deepcopy((parent.config, parent.progress, parent.report))
        connection = db.get(Connection, "connection")
        connection.options = {**connection.options, "timeout_sec": 900}
        retry_id = security.create_scan(db, seed["repo"], seed["user"],
            SecurityScanInput(connection_id="connection", request_key=uid()), parent=parent).id
    if context_changed:
        sources["done.py"] = "changed"
        item.inventory["done.py"]["hash"] = digest("changed")
    calls.clear()
    retrying = True
    perform_security(retry_id, "retry:1")
    with transaction() as db:
        retry, parent = db.get(SecurityScan, retry_id), db.get(SecurityScan, parent_id)
        assert retry.status == "completed", retry.error
        assert retry.config["connection"]["options"]["timeout_sec"] == 900
        assert calls == (["done.py", "failed.py"] if context_changed else ["failed.py"])
        assert retry.progress["tokens"] == 7 * len(calls)
        assert bool(retry.progress["tasks"]["analysis:done.py"].get("reused")) != context_changed
        assert parent.status == "failed" and (parent.config, parent.progress, parent.report) == original


@pytest.mark.parametrize("change", ["extra_body", "retry_codes", "model", "base_url", "timeout_seconds", "token_budget", "language"])
def test_security_timeout_retry_still_rejects_other_configuration_changes(seed, security_github, change):
    with transaction() as db:
        parent = security.create_scan(db, seed["repo"], seed["user"],
            SecurityScanInput(connection_id="connection", request_key=uid()))
        parent.status = "failed"
        original = deepcopy(parent.config)
        connection = db.get(Connection, "connection")
        connection.options = {**connection.options, "timeout_sec": 900}
        body = {"connection_id": "connection", "request_key": uid()}
        if change in {"extra_body", "retry_codes"}:
            connection.options = {**connection.options, change: {"temperature": 0} if change == "extra_body" else [429]}
        elif change in {"model", "base_url"}:
            setattr(connection, change, "different-model" if change == "model" else "https://other.example.com/v1")
        elif change == "language":
            seed["repo"].settings = {**seed["repo"].settings, "language": "Spanish"}
        else:
            body[change] = 60 if change == "timeout_seconds" else 1000
        with pytest.raises(HTTPException, match="Retry must use the original"):
            security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(**body), parent=parent)
        assert parent.config == original


@pytest.mark.parametrize("concurrency", [None, 3])
@pytest.mark.parametrize("timeout_changed", [False, True])
def test_legacy_security_retry_preserves_completed_file_checkpoints(
    seed, security_github, monkeypatch, tmp_path, concurrency, timeout_changed,
):
    item = SimpleNamespace(inventory={"app.py": {"code": True, "hash": digest("safe"), "lines": 1}},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: "safe", redact=lambda text: text,
        sha=SHA, git=tmp_path, files=tmp_path / "files", env={})

    @contextmanager
    def snapshot(*args):
        yield item

    monkeypatch.setattr(security_engine, "snapshot", snapshot)
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(security_engine, "ask", lambda *args: pytest.fail("Completed legacy analysis must be reused"))
    with transaction() as db:
        parent = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid()))
        config = {key: value for key, value in parent.config.items()
                  if key not in {"task_contract", "execution_limits", "budget_schedule", "assessment_targets"}}
        config["connection"] = connection_snapshot(db.get(Connection, "connection"))
        config["limits"] = {"token_budget": 100000, "timeout_seconds": 900}
        context = {"assigned_path": "app.py", "head_sha": SHA, "diff": "", "sources": {"current": {"app.py": "safe"}},
                   "language": "English", "task_contract": "security-task/v2"}
        parent.config, parent.status = config, "partial"
        parent.progress = {"tokens": 12, "tasks": {"analysis:app.py": {"status": "completed", "tokens": 12,
            "input_hash": digest(json.dumps([config, context], sort_keys=True)), "result": {"findings": [], "required_paths": []}}}}
        with pytest.raises(security.HTTPException, match="Retry must use the original"):
            security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid(), token_budget=1000), parent=parent)
        if timeout_changed:
            connection = db.get(Connection, "connection")
            connection.options = {**connection.options, "timeout_sec": 900}
        retry = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(connection_id="connection", request_key=uid(),
            **({"concurrency": concurrency} if concurrency else {})), parent=parent)
        identity = retry.id
        assert "task_contract" not in retry.config
        assert "assessment_targets" not in retry.config
        assert retry.config["limits"] == {**config["limits"], "concurrency": concurrency or 1}
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "completed", scan.error
        assert scan.progress["tokens"] == 0 and scan.progress["tasks"]["analysis:app.py"]["reused"]


@pytest.mark.parametrize("revision", ["original", "current"])
@pytest.mark.parametrize("trailing_newline", [False, True])
def test_security_citations_reanchor_only_unchanged_unique_source_text(revision, trailing_newline):
    source = "def list_installations(db, user):\n    remote = {}\n    visible = user_installations(db, user)\n    results = {}\n"
    quote = "    remote = {}\n    visible = user_installations(db, user)\n    results = {}" + ("\n" if trailing_newline else "")
    citation = {"revision": revision, "path": "app.py", "start_line": 1, "end_line": 3, "quote": quote}
    assert security_engine.validate_citations([citation], {revision: {"app.py": source}}) == {revision}
    assert citation["start_line"] == 2 and citation["end_line"] == 4 and citation["quote"] == quote


@pytest.mark.parametrize("revision", ["original", "current"])
@pytest.mark.parametrize(("claimed", "expected"), [(75, 74), (56, 55), (34, 33), (44, None)])
def test_security_recovers_repeated_exact_quotes_only_at_a_unique_nearest_line(revision, claimed, expected):
    lines = ["# filler"] * 91
    for number in (33, 55, 74):
        lines[number - 1:number + 1] = ["    volumes:", "      - .:/app"]
    source = "\n".join(lines)
    quote = "    volumes:\n      - .:/app"
    citation = {"revision": revision, "path": "docker-compose.yml", "start_line": claimed, "end_line": claimed + 1, "quote": quote}
    if expected is None:
        with pytest.raises(security_engine.verification.ResultValidationError):
            security_engine.validate_citations([citation], {revision: {"docker-compose.yml": source}})
        assert citation["start_line"] == claimed
    else:
        assert security_engine.validate_citations([citation], {revision: {"docker-compose.yml": source}}) == {revision}
        assert citation["start_line"] == expected and citation["end_line"] == expected + 1
    assert citation["quote"] == quote


def test_security_recovers_multiple_exact_matches_on_the_same_source_line():
    citation = {"revision": "current", "path": "app.py", "start_line": 2, "end_line": 2, "quote": "safe"}
    assert security_engine.validate_citations([citation], {"current": {"app.py": "safe safe\nother"}}) == {"current"}
    assert citation["start_line"] == citation["end_line"] == 1


@pytest.mark.parametrize("quote", ["invented", "safe", 'value = \\"synthetic\\"', "safe\n...\nsafe"])
def test_security_citations_never_reanchor_invented_escaped_or_ambiguous_quotes(quote):
    citation = {"revision": "current", "path": "app.py", "start_line": 2, "end_line": 2, "quote": quote}
    with pytest.raises(security_engine.verification.ResultValidationError):
        security_engine.validate_citations([citation], {"current": {"app.py": 'safe\nvalue = "synthetic"\nsafe'}})
    assert citation["start_line"] == citation["end_line"] == 2


@pytest.mark.parametrize("case", SCAN_CITATIONS, ids=lambda case: case["task"])
@pytest.mark.parametrize("revision", ["original", "current"])
def test_legacy_citation_validation_keeps_recorded_nonverbatim_quotes_invalid(case, revision):
    citation = {**case["citation"], "revision": revision}
    source = "\n" * (citation["start_line"] - 1) + case["source_lines"]
    with pytest.raises(security_engine.verification.ResultValidationError):
        security_engine.validate_citations([citation], {revision: {citation["path"]: source}})
    assert citation == {**case["citation"], "revision": revision}


@pytest.mark.parametrize(("source", "quote", "path"), [
    ('if a:\n    if b:\n        allow()\n    deny()\n', 'if a:\n    if b:\n        allow()\n        deny()', "app.py"),
    ('message = """first\n    allowed\nlast"""\n', 'message = """first\n allowed\nlast"""', "app.py"),
    ('message = f"""first\n    {value}\nlast"""\n', 'message = f"""first\n {value}\nlast"""', "app.py"),
    ('value = " allowed"\n', '    value = "allowed"', "app.py"),
    ('value = 150\n', '    value = 151', "app.py"),
    ('if a:\n    allow()\n', 'if not a:\n    allow()', "app.py"),
    ('if a:\n    allow()  # authenticated\n', 'if a:\n        allow()  # unauthenticated', "app.py"),
    ('value = "quoted"\n', '    value = \\"quoted\\"', "app.py"),
    ('    elif allowed:\n        run()\n', 'elif allowed:\n    run()', "app.py"),
    ('    a:\n        b: true\n', 'a:\n    b: true', "config.yml"),
    ('{\n  "a": "b"\n}\n', '{\n"a": "b"\n}', "config.json"),
    ('value = "' + "x" * 19990 + '"\n', '    value = "' + "x" * 19990 + '"', "app.py"),
], ids=["block-structure", "multiline-string", "f-string", "string-value", "number", "condition", "comment", "escaping",
        "invalid-fragment", "yaml", "json", "source-limit"])
def test_legacy_citation_validation_rejects_changed_code_literals_comments_and_formatting(source, quote, path):
    citation = {"revision": "current", "path": path, "start_line": 1, "end_line": len(source.splitlines()), "quote": quote}
    original = citation.copy()
    with pytest.raises(security_engine.verification.ResultValidationError):
        security_engine.validate_citations([citation], {"current": {path: source}})
    assert citation == original


@pytest.mark.parametrize("case", SCAN_CITATIONS, ids=lambda case: case["task"])
@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
def test_security_source_selections_cover_recorded_failures_in_every_stage_and_transport(tmp_path, monkeypatch, case, kind, task):
    citation, calls = case["citation"], []
    source = "\n" * (citation["start_line"] - 1) + case["source_lines"]
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    selected = {"source_id": "source_1", "start_line": citation["start_line"], "end_line": citation["end_line"]}
    proposal = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": citation["path"],
        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
        "verification": "Test", "evidence": [selected]}
    response = {"findings": [proposal], "required_paths": []} if task == "analysis" else {
        "verdict": "confirmed" if task == "assessment" else "fixed", "explanation": "Checked", "required_paths": [],
        "evidence": [selected, {**selected, "source_id": "source_2"}],
        **({"finding_id": "history_1"} if task == "assessment" else {"original_issue_present": True, "change_explanation": "Changed"})}

    def api(*args, **kwargs):
        calls.append(1)
        return kwargs["validator"](response, args[2]), {"tokens": 7}


    monkeypatch.setattr(workflow_inference, "execute", api)
    result, usage = ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, task,
        {"assigned_path": citation["path"], "diff": "a source change", "history": [{"id": "stored-finding"}],
         "sources": {revision: {citation["path"]: source} for revision in ("current", "original")}},
        100, 60, lambda: False, uid())
    assert calls == [1] and usage["tokens"] == 7
    evidence = result["findings"][0]["evidence"] if task == "analysis" else result["evidence"]
    assert all(value["quote"] == case["source_lines"] for value in evidence)
    assert usage == {"tokens": 7}
    assert all(set(value) == {"source_id", "start_line", "end_line"} for value in (response["findings"][0]["evidence"] if task == "analysis" else response["evidence"]))
    assert [value["revision"] for value in evidence] == (["current"] if task == "analysis" else ["current", "original"])
    if task == "assessment":
        assert result["finding_id"] == "stored-finding"
    elif task == "verification":
        assert result["verdict"] == "fixed"


@pytest.mark.parametrize("concurrency", [1, 2])
@pytest.mark.parametrize("uncertain", [False, True])
def test_security_scan_persists_selected_citations_and_preserves_uncertain_and_rejected_results(
    seed, security_github, tmp_path, monkeypatch, concurrency, uncertain,
):
    cases = {case["citation"]["path"]: case for case in SCAN_CITATIONS}
    sources = {path: "\n" * (case["citation"]["start_line"] - 1) + case["source_lines"] for path, case in cases.items()}
    item = SimpleNamespace(inventory={path: {"code": True, "hash": digest(source), "lines": len(source.splitlines())}
                                      for path, source in sources.items()},
        excluded=[], problems=[], secret_values=[], secret_lines={}, source=lambda path, sha=None: sources[path],
        redact=lambda text: text, sha=SHA, git=tmp_path, files=tmp_path / "files", env={})
    calls = []

    def api(*args, **kwargs):
        context = args[2]
        path = context.get("assigned_path") or context["proposal"]["path"]
        calls.append(path)
        citation = cases[path]["citation"]
        reference = next(key for key, value in context["citation_sources"].items() if value["path"] == path and value["revision"] == "current")
        selected = {"source_id": reference, "start_line": citation["start_line"], "end_line": citation["end_line"]}
        response = {"findings": [{"title": path, "category": "test", "severity": "low", "path": path,
            "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
            "verification": "Test", "evidence": [selected]}], "required_paths": []} if "assigned_path" in context else {
                "verdict": "rejected" if cases[path].get("verdict") == "rejected" else
                    "uncertain" if uncertain and cases[path].get("verdict") == "uncertain" else "confirmed",
                "finding_id": None, "explanation": "Checked", "evidence": [selected], "required_paths": []}
        return kwargs["validator"](response, context), {"tokens": 7}

    monkeypatch.setattr(security_engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(security_engine, "trivy", lambda *args, **kwargs: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    monkeypatch.setattr(workflow_inference, "execute", api)
    with transaction() as db:
        identity = security.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=concurrency)).id
    perform_security(identity, "test:1")
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == ("partial" if uncertain else "completed"), scan.error
        assert scan.report["problems"] == (["A proposed security finding requires additional verification."] if uncertain else [])
        assert scan.grade == "B"
        assert all(scan.stages[name]["status"] == ("partial" if uncertain and name == "assessment" else "completed") for name in security.STAGES)
        assert scan.stages["assessment"]["completed"] == 3 and scan.stages["assessment"]["failed"] == 0
        assert len(calls) == 6 and scan.progress["tokens"] == 42
        assert len(scan.report["findings"]) == (1 if uncertain else 2)
        assert len(scan.report["unsettled_candidates"]) == int(uncertain)
        if uncertain:
            assert scan.report["unsettled_candidates"][0]["assessment"]["verdict"] == "uncertain"
        for finding in scan.report["findings"]:
            assert finding["evidence"]["evidence"][0]["quote"] == cases[finding["path"]]["source_lines"]
        assert all(unit["citation_contract"] == security_engine.CITATION_CONTRACT for unit in scan.coverage["units"])
        rejected = next(task for key, task in scan.progress["tasks"].items() if key.startswith("assessment:") and task["proposal"]["path"] == "frontend/src/reviews.tsx")
        assert rejected["status"] == "completed" and rejected["result"]["verdict"] == "rejected"


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("invalid", ["unknown", "zero", "reversed", "overflow", "string_line", "boolean_line",
    "quote", "path", "revision", "empty", "blank", "oversized"])
def test_security_source_selection_cannot_forge_evidence(tmp_path, monkeypatch, kind, invalid):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    sources = {"app.tsx": "line one\nline two\n", "long.tsx": "more\n" * 10}
    if invalid in {"empty", "blank", "oversized"}:
        sources["app.tsx"] = {"empty": "", "blank": " \t\n", "oversized": "x" * 20001}[invalid]
    selected = {"source_id": "source_1", "start_line": 1, "end_line": 1}
    selected.update({"unknown": {"source_id": "source_999"}, "zero": {"start_line": 0},
        "reversed": {"start_line": 2, "end_line": 1}, "overflow": {"end_line": 3}, "string_line": {"start_line": "1"},
        "boolean_line": {"start_line": True}, "quote": {"quote": "invented"}, "path": {"path": "../private"},
        "revision": {"revision": "original"}}.get(invalid, {}))
    response = {"verdict": "confirmed", "finding_id": None, "explanation": "Untrusted", "evidence": [selected], "required_paths": []}
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        try:
            return kwargs["validator"](response, args[2]), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid selection", {"tokens": 7}, feedback=exc.feedback) from exc


    monkeypatch.setattr(workflow_inference, "execute", api)
    with pytest.raises(security_engine.verification.VerificationOutputError) as error:
        ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, "assessment",
            {"diff": "", "history": [], "sources": {"current": sources}}, 100, 60, lambda: False, uid())
    assert calls == [1, 1] and error.value.usage == {"tokens": 14}
    assert error.value.feedback["kind"] == ("schema" if invalid in {"zero", "string_line", "boolean_line", "quote", "path", "revision"} else "citation_selection")
    if invalid == "overflow":
        assert error.value.feedback["line_count"] == 2


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
def test_security_source_ids_stay_bound_when_evidence_expands(tmp_path, monkeypatch, kind, task):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"z.tsx": {}, "a.tsx": {}}
    item.read = lambda path, sha=None: "original helper" if sha else "current helper"
    calls, contexts = [], []
    bundle = security_engine.verification.evidence_bundle

    def evidence_bundle(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)

    def response(context):
        selected = [{"source_id": "source_1", "start_line": 1, "end_line": 1}]
        if task == "analysis":
            return {"findings": [] if len(calls) == 1 else [{"title": "Synthetic", "category": "test", "severity": "low", "path": "z.tsx",
                "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix", "verification": "Test",
                "evidence": selected}], "required_paths": ["a.tsx"] if len(calls) == 1 else []}
        return {"verdict": "uncertain", "explanation": "Checked", "evidence": selected, "required_paths": ["a.tsx"] if len(calls) == 1 else [],
            **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": True})}

    def api(*args, **kwargs):
        evidence_bundle(args[2])
        calls.append(1)
        return kwargs["validator"](response(args[2]), args[2]), {"tokens": 7}


    monkeypatch.setattr(workflow_inference, "execute", api)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    context = {"assigned_path": "z.tsx", "original_sha": OLD, "diff": "", "history": [], "sources": {"current": {"z.tsx": "preserve this"}}}
    result, usage = ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, task,
        context, 100, 60, lambda: False, uid())
    assert usage == {"tokens": 14} and calls == [1, 1]
    assert contexts[0]["citation_sources"]["source_1"] == contexts[1]["citation_sources"]["source_1"] == {
        "revision": "current", "path": "z.tsx", "line_count": 1}
    assert contexts[1]["citation_sources"]["source_2"]["path"] == "a.tsx"
    evidence = result["findings"][0]["evidence"] if task == "analysis" else result["evidence"]
    assert evidence == [{"revision": "current", "path": "z.tsx", "start_line": 1, "end_line": 1, "quote": "preserve this"}]
    assert "citation_sources" not in context


@pytest.mark.parametrize("size", [19999, 20000])
def test_selected_citations_preserve_exact_content_revision_redaction_and_limit(size):
    current, original = "x" * (size - 1) + "\n", "old [redacted]\t\r\n"
    context = {"sources": {"current": {"app.yaml": current}, "original": {"app.yaml": original}},
        "citation_sources": {"source_1": {"revision": "current", "path": "app.yaml"},
                             "source_2": {"revision": "original", "path": "app.yaml"}}}
    selected = [{"source_id": source, "start_line": 1, "end_line": 1} for source in ("source_1", "source_2")]
    result = security_engine.selected_citations(selected, context)
    assert [value["quote"] for value in result] == [current, original]
    assert [value["revision"] for value in result] == ["current", "original"]
    assert all("quote" not in value for value in selected)


def test_github_issue_creation_is_explicit_and_reconciles_unknown_delivery(seed, security_github, monkeypatch, client):
    security_github["private"] = False
    with transaction() as db:
        db.get(Repository, 22).private = False
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        identity = finding.id
    draft = client.get(f"/api/v1/security/findings/{identity}/github-issue").json()
    assert draft["public"] is True
    approved = client.post(f"/api/v1/security/findings/{identity}/github-issue", json={"title": draft["title"], "body": draft["body"], "version": 0, "public": True})
    assert approved.status_code == 202
    posted = []

    def request(method, path, token, **kwargs):
        assert method == "POST" and path.endswith("/issues")
        posted.append(kwargs["json"])
        raise github.GitHubError(503, "Unknown outcome")

    monkeypatch.setattr(github, "request", request)
    security_issues.publish(identity)
    assert len(posted) == 1
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [])
    security_issues.sync(identity)
    with transaction() as db:
        issue = db.get(SecurityFinding, identity).issue
        assert issue["started"] is True and issue["sync_status"] == "uncertain"
    path = f"/api/v1/security/findings/{identity}/github-issue"
    recovery_preview = client.get(path)
    assert recovery_preview.status_code == 200
    assert recovery_preview.json()["recovery_token"] == issue["attempt_id"]
    assert client.post(path, json={"title": "Another issue", "body": draft["body"], "version": 0, "public": True}).status_code == 409
    assert client.post(path + "/sync").status_code == 202
    security_issues.sync(identity)
    assert len(posted) == 1
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "open",
              "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
              "user": {"login": "mortis-test[bot]", "type": "Bot"}, "body": posted[0]["body"]}
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [remote])
    security_issues.publish(identity)
    security_issues.publish(identity)
    assert len(posted) == 1
    with transaction() as db:
        assert db.get(SecurityFinding, identity).issue["number"] == 9


def test_issue_permission_upgrade_is_actionable(monkeypatch):
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: {"token": "test", "permissions": {"issues": "read"}})
    with pytest.raises(github.GitHubError, match="Issues: Read and write"):
        github.installation_token_from_jwt("jwt", 10, issues=True)


@pytest.mark.parametrize("remote_appears", [False, True])
def test_issue_recovery_requires_current_explicit_approval_and_reconciles_first(seed, security_github, monkeypatch, client, remote_appears):
    with transaction() as db:
        finding_id = saved_finding(db, seed, saved_scan(db, seed)).id
    path = f"/api/v1/security/findings/{finding_id}/github-issue"
    draft = client.get(path).json()
    body = {key: draft[key] for key in ("title", "body", "version", "public")}
    original = client.post(path, json=body).json()["issue"]
    with transaction() as db:
        finding = db.get(SecurityFinding, finding_id)
        finding.issue = {**finding.issue, "started": True, "publication_status": "creating"}
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [])
    posted = []
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "open",
              "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
              "user": {"login": "mortis-test[bot]", "type": "Bot"}, "body": original["body"]}

    def request(method, endpoint, token, **kwargs):
        assert method == "POST" and endpoint == "/repos/team/project/issues"
        posted.append(kwargs["json"])
        return {**remote, "body": kwargs["json"]["body"]}

    monkeypatch.setattr(github, "request", request)
    security_issues.publish(finding_id)
    assert not posted
    token = client.get(path).json()["recovery_token"]
    assert token == original["operation_id"]
    assert client.post(path, json={**body, "recovery_token": uid()}).status_code == 409
    approval = {**body, "recovery_token": token}
    assert client.post("/api/v1/security/repositories/22/github-issues", json={"issues": [{"finding_id": finding_id, **approval}]}).status_code == 409
    approved = client.post(path, json=approval)
    assert approved.status_code == 202, approved.text
    assert client.post(path, json=approval).status_code == 202
    assert client.get(path).status_code == 409
    assert approved.json()["issue"]["approved_at"] == original["approved_at"]
    if remote_appears:
        monkeypatch.setattr(github, "pages", lambda *args, **kwargs: [remote])
    security_issues.publish(finding_id)
    security_issues.publish(finding_id)
    assert len(posted) == int(not remote_appears)
    with transaction() as db:
        issue = db.get(SecurityFinding, finding_id).issue
        assert issue["operation_id"] == original["operation_id"]
        assert issue["number"] == 9 and issue["sync_status"] == "synced"
    assert client.post(path, json=approval).status_code == 409


@pytest.mark.parametrize("failure", ["timeout", "permission", "permission_after_claim", "version", "multiple"])
def test_issue_recovery_approval_does_not_authorize_unbounded_or_unsafe_retries(seed, security_github, monkeypatch, client, failure):
    with transaction() as db:
        finding_id = saved_finding(db, seed, saved_scan(db, seed)).id
    path = f"/api/v1/security/findings/{finding_id}/github-issue"
    body = {"title": "Security finding", "body": "Check ownership.", "version": 0, "public": False}
    original = client.post(path, json=body).json()["issue"]
    with transaction() as db:
        finding = db.get(SecurityFinding, finding_id)
        finding.issue = {**finding.issue, "started": True, "sync_status": "uncertain"}
    approval = {**body, "recovery_token": original["operation_id"]}
    assert client.post(path, json=approval).status_code == 202
    matches = [{"body": original["body"]}] * 2 if failure == "multiple" else []
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: matches)
    posted = []

    def request(method, endpoint, token, **kwargs):
        posted.append(kwargs["json"])
        raise github.GitHubError(503, "Unknown outcome")

    monkeypatch.setattr(github, "request", request)
    if failure == "version":
        with transaction() as db:
            db.get(SecurityFinding, finding_id).version += 1
    elif failure == "permission":
        def denied(*args, **kwargs):
            raise github.GitHubError(403, "Access denied")
        monkeypatch.setattr(github, "authorize_repo", denied)
    elif failure == "permission_after_claim":
        original_identity = security_issues.identity
        def denied_before_post(db, finding, *, publishing=False):
            if publishing:
                raise github.GitHubError(403, "Access denied before POST")
            return original_identity(db, finding, publishing=publishing)
        monkeypatch.setattr(security_issues, "identity", denied_before_post)
    security_issues.publish(finding_id)
    security_issues.publish(finding_id)
    assert len(posted) == int(failure == "timeout")
    with transaction() as db:
        issue = db.get(SecurityFinding, finding_id).issue
        assert issue["started"] is True and not issue.get("number")
        if failure in {"timeout", "permission_after_claim"}:
            assert issue["attempt_id"] != approval["recovery_token"]
            assert issue["recovery_approved"] is None
    if failure in {"timeout", "permission_after_claim"}:
        assert client.post(path, json=approval).status_code == 409


def test_issue_sync_does_not_infer_a_fix_from_human_closure(seed, security_github, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed, "completed")
        finding = saved_finding(db, seed, scan)
        identity = finding.id
        operation = uid()
        finding.issue = {"number": 9, "id": 99, "node_id": "issue-node", "operation_id": operation,
                         "actor_id": 7, "closed_by_app": False}
        body = security_issues.marker(finding, operation)
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "closed", "body": body,
              "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
              "user": {"login": "mortis-test[bot]", "type": "Bot"}, "closed_by": {"login": "owner"}}
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: remote)
    security_issues.sync(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.state == "open" and finding.issue["remote_state"] == "closed"
        assert finding.issue["closed_by_app"] is False


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("case", ["first", "second", "new", "empty", "corrected", "repeat", "uuid", "empty_invalid", "number", "expand"])
def test_security_assessment_selects_history_references_and_server_assigns_uuid(tmp_path, monkeypatch, kind, case):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    item.read = lambda *args: "safe guard"
    identities = ["228f3fbd-bb86-4143-a810-4b2622b1cc19", "1226544f-2cf5-4104-bb24-d7c151182064"]
    history = [{"id": identity, "path": "app.py", "title": f"Finding {index}"} for index, identity in enumerate(identities)]
    if case in {"empty", "empty_invalid"}:
        history = []
    original = json.dumps(history, sort_keys=True)
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    calls, contexts, schemas = [], [], []
    bundle = security_engine.verification.evidence_bundle

    def evidence_bundle(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)

    def response():
        selected = "history_2" if case in {"second", "corrected"} else "history_1"
        if case in {"new", "empty"}:
            selected = None
        elif case in {"repeat", "empty_invalid"} or (case == "corrected" and len(calls) == 1):
            selected = "history_999"
        elif case == "uuid":
            selected = identities[0]
        elif case == "number":
            selected = 1
        expand = case == "expand" and len(calls) == 1
        return {"verdict": "uncertain" if expand else "confirmed", "finding_id": selected, "explanation": "Checked source",
                "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 1}], "required_paths": ["guard.py"] if expand else []}

    def api(*args, **kwargs):
        evidence_bundle(args[2])
        calls.append(kwargs["token_budget"])
        schemas.append(kwargs["schema"])
        try:
            return kwargs["validator"](response(), args[2]), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid selection", {"tokens": 7}, feedback=exc.feedback) from exc


    monkeypatch.setattr(workflow_inference, "execute", api)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    context = {"history": history, "sources": {"current": {"app.py": "unsafe"}}, "diff": ""}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    invalid = case in {"repeat", "uuid", "empty_invalid", "number"}
    if invalid:
        with pytest.raises(security_engine.verification.VerificationOutputError) as caught:
            ask_security(item, connection, {}, "assessment", context, 100, 60, lambda: False, uid())
        assert caught.value.usage == {"tokens": 14}
        assert caught.value.feedback["kind"] == ("schema" if case == "number" else "history_selection")
    else:
        result, usage = ask_security(item, connection, {}, "assessment", context, 100, 60, lambda: False, uid())
        expected_id = None if case in {"new", "empty"} else identities[1 if case in {"second", "corrected"} else 0]
        assert result["finding_id"] == expected_id
        assert usage == {"tokens": 7 * len(calls)}
        assert result["evidence"] == [citation]
    references = [f"history_{index + 1}" for index in range(len(history))]
    assert all(schema["properties"]["finding_id"]["enum"] == [*references, None] for schema in schemas)
    assert all([finding["id"] for finding in supplied["history"]] == references for supplied in contexts)
    assert json.dumps(history, sort_keys=True) == original
    assert calls == ([100, 93] if invalid or case in {"corrected", "expand"} else [100])
    if invalid or case == "corrected":
        feedback = contexts[1]["validation_feedback"]
        if case != "number":
            assert feedback == {"kind": "history_selection", "field": "finding_id", "allowed_values": [*references, None]}


@pytest.mark.parametrize("kind", ["api"])
def test_security_transports_validate_terminal_empty_result_and_evidence_expansion(tmp_path, monkeypatch, kind):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    item.read = lambda path, sha=None: "safe guard"
    calls = []

    def api(*args, **kwargs):
        calls.append(args[2])
        raw = {"findings": [], "required_paths": ["guard.py"] if len(calls) == 1 else []}
        assert kwargs["schema"]["additionalProperties"] is False
        return kwargs["validator"](raw, args[2]), {"tokens": 7}


    monkeypatch.setattr(workflow_inference, "execute", api)
    result, usage = ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, "analysis",
        {"assigned_path": "app.py", "diff": "", "sources": {"current": {"app.py": "safe"}}}, 100, 60, lambda: False, uid())
    assert result == {"findings": [], "required_paths": []} and usage == {"tokens": 14}
    assert len(calls) == 2


def test_failed_expansion_keeps_already_consumed_tokens(tmp_path, monkeypatch):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"guard.py": {}}
    item.read = lambda *args: "safe"
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise security_engine.verification.VerificationOutputError("Invalid result", {"tokens": 5})
        return {"findings": [], "required_paths": ["guard.py"]}, {"tokens": 7}

    monkeypatch.setattr(workflow_inference, "execute", api)
    with pytest.raises(security_engine.verification.VerificationOutputError) as error:
        ask_security(item, {"kind": "api"}, {}, "analysis", {"diff": "", "sources": {"current": {}}}, 100, 60, lambda: False, uid())
    assert error.value.usage["tokens"] == 12


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("failure,reason", [
    ("missing", "not in the prepared snapshot"),
    ("excluded", "Generated directory"),
    ("oversized", "4 MB evidence limit"),
    ("supplied", "already supplied"),
    ("protected", "not permitted as supporting source"),
    ("file_limit", "100-source-file evidence limit"),
    ("pass_limit", "four evidence passes"),
])
def test_unavailable_security_evidence_reports_path_reason_and_usage(tmp_path, monkeypatch, kind, failure, reason):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.read = lambda *args: "safe"
    path = "AGENTS.md" if failure == "protected" else "guard.py"
    sources = {"app.py": "safe"}
    item.inventory = {path: {}}
    if failure in {"missing", "excluded", "oversized"}:
        item.inventory = {}
    if failure == "excluded":
        item.excluded = [{"path": path, "reason": "Generated directory"}]
    if failure == "oversized":
        item.problems = [{"path": path, "reason": "File exceeds the 4 MB evidence limit"}]
    if failure == "supplied":
        sources[path] = "safe"
    if failure == "file_limit":
        sources = {f"source{number}.py": "safe" for number in range(100)}
    if failure == "pass_limit":
        item.inventory = {f"guard{number}.py": {} for number in range(1, 5)}
        path = "guard4.py"
    calls = []

    def response():
        calls.append(1)
        return {"findings": [], "required_paths": [f"guard{len(calls)}.py" if failure == "pass_limit" else path]}

    def api(*args, **kwargs):
        return kwargs["validator"](response(), args[2]), {"tokens": 7}

    monkeypatch.setattr(workflow_inference, "execute", api)
    with pytest.raises(security_engine.verification.VerificationOutputError) as error:
        ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, "analysis",
            {"assigned_path": "app.py", "sources": {"current": sources}, "diff": ""}, 100, 60, lambda: False, uid())
    assert len(calls) == (4 if failure == "pass_limit" else 1 if failure == "file_limit" else 2)
    assert error.value.usage == {"tokens": 7 * len(calls)}
    assert path in str(error.value) and reason in str(error.value)
    assert error.value.feedback["kind"] == "evidence"
    assert error.value.feedback["required_paths"] == [path]
    assert reason in error.value.feedback["reason"]


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
@pytest.mark.parametrize("case", ["corrected", "mixed", "repeat", "uncertain", "after_schema_error"])
def test_security_missing_evidence_can_use_real_inventory_before_failing(tmp_path, monkeypatch, kind, task, case):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {path: {} for path in ["app.py", "conf/settings.py", "README.md", "AGENTS.md",
        ".codex/config.toml", ".agents/skills/test/SKILL.md", ".aws/credentials"]}
    calls, contexts, reads, clock = [], [], [], [0]
    missing = ["config/settings.py", "api-collection/README.md"]
    bundle = security_engine.verification.evidence_bundle
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def read(path, sha=None):
        assert path in {"conf/settings.py", "README.md"}
        reads.append((path, sha))
        return "safe guard"

    def evidence_bundle(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)

    def response():
        clock[0] += 10
        step = len(calls) - int(case == "after_schema_error")
        paths = missing if step == 1 or case == "repeat" else ["conf/settings.py"] if step == 2 and case != "uncertain" else []
        if case == "mixed" and step == 1:
            paths = [*paths, "README.md", "README.md"]
        raw = {"findings": [], "required_paths": paths} if task == "analysis" else {
            "verdict": "uncertain", "explanation": "Required evidence is unavailable; no issue or fix is confirmed.",
            "evidence": [], "required_paths": paths,
            **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": False})}
        if case == "after_schema_error" and len(calls) == 1:
            raw["unexpected_field"] = True
        return raw

    def api(*args, **kwargs):
        evidence_bundle(args[2])
        calls.append((kwargs["token_budget"], kwargs["timeout"]))
        try:
            return kwargs["validator"](response(), args[2]), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid schema", {"tokens": 7}, feedback=exc.feedback) from exc


    item.read = read
    monkeypatch.setattr(workflow_inference, "execute", api)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "safe"}}, "diff": "", "original_sha": OLD,
               "available_paths": ["invented.py"]}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    if case == "repeat" and task == "analysis":
        with pytest.raises(security_engine.verification.VerificationOutputError) as error:
            ask_security(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert error.value.usage == {"tokens": 14}
        assert error.value.feedback["required_paths"] == missing
    else:
        result, usage = ask_security(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert result["required_paths"] == [] and usage == {"tokens": 7 * len(calls)}
        if task != "analysis":
            assert result["verdict"] == "uncertain"
    expected_calls = 4 if case == "after_schema_error" else 2 if case in {"repeat", "uncertain"} else 3
    assert calls == [(100 - 7 * step, 60 - 10 * step) for step in range(expected_calls)]
    assert all(context["available_paths"] == ["README.md", "app.py", "conf/settings.py"] for context in contexts)
    correction = contexts[2 if case == "after_schema_error" else 1]["validation_feedback"]
    assert correction["kind"] == "evidence" and correction["required_paths"] == missing
    assert "prepared snapshot" in correction["reason"]
    expected_reads = [("README.md", None)] if case == "mixed" else []
    if case == "mixed" and task == "verification":
        expected_reads.append(("README.md", OLD))
    if case not in {"repeat", "uncertain"}:
        expected_reads.append(("conf/settings.py", None))
        if task == "verification":
            expected_reads.append(("conf/settings.py", OLD))
    assert reads == expected_reads


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["assessment", "verification"])
@pytest.mark.parametrize("case", ["excluded", "protected", "repeated"])
def test_security_unavailable_evidence_preserves_uncertainty_and_scope(tmp_path, monkeypatch, kind, task, case):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    blocked = "AGENTS.md" if case == "protected" else "vendor/guard.py"
    item.inventory = {"app.py": {}, "guard.py": {}, **({blocked: {}} if case == "protected" else {})}
    item.excluded = [{"path": blocked, "reason": "Not permitted as supporting source"}]
    contexts, reads, calls = [], [], []
    bundle = security_engine.verification.evidence_bundle
    def capture(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)
    def read(path, sha=None):
        assert path == "guard.py"
        reads.append((path, sha))
        return "guard = True"
    item.read = read
    def response():
        return {"verdict": "uncertain", "explanation": "The needed guard is unavailable.", "evidence": [],
                "required_paths": [blocked, "guard.py"] if len(calls) == 1 else [blocked] if case == "repeated" else [],
                **({"finding_id": None} if task == "assessment" else
                   {"change_explanation": "", "original_issue_present": False})}
    def api(*args, **kwargs):
        capture(args[2])
        calls.append(kwargs["token_budget"])
        return kwargs["validator"](response(), args[2]), {"tokens": 7}
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", capture)
    monkeypatch.setattr(workflow_inference, "execute", api)
    result, usage = ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, task,
        {"sources": {"current": {"app.py": "safe"}}, "original_sha": OLD, "diff": ""},
        100, 60, lambda: False, uid())
    assert result["verdict"] == "uncertain" and result["required_paths"] == []
    assert result.get("finding_id") is None and usage == {"tokens": 14} and calls == [100, 93]
    assert reads == [("guard.py", None)] + ([("guard.py", OLD)] if task == "verification" else [])
    assert all(blocked not in context["available_paths"] and blocked not in context["sources"]["current"] for context in contexts)
    assert contexts[1]["validation_feedback"]["required_paths"] == [blocked]
    assert "guard.py" in contexts[1]["sources"]["current"]
    if case == "repeated":
        assert blocked in result["explanation"]


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["assessment", "verification"])
@pytest.mark.parametrize("redaction_failure", [False, True])
def test_security_evidence_pass_limit_keeps_uncertainty_identity_and_usage(tmp_path, monkeypatch, kind, task, redaction_failure):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {f"guard{i}.py": {} for i in range(1, 5)}
    calls, reads, requested = [], [], []
    def read(path, sha=None):
        reads.append((path, sha))
        return "guard = True"
    item.read = read
    redact = item.redact
    def redact_result(value):
        if redaction_failure and value.startswith("Required security evidence remains unavailable:"):
            raise ValueError("Synthetic redaction failure")
        return redact(value)
    item.redact = redact_result
    def response():
        return {"verdict": "uncertain", "explanation": "Need another guard", "evidence": [],
                "required_paths": [f"guard{len(calls)}.py"],
                **({"finding_id": "history_1"} if task == "assessment" else
                   {"change_explanation": "", "original_issue_present": True})}
    def api(*args, **kwargs):
        calls.append(kwargs["token_budget"])
        return kwargs["validator"](response(), args[2]), {"tokens": 7}
    monkeypatch.setattr(workflow_inference, "execute", api)
    context = {"sources": {"current": {"app.py": "safe"}}, "original_sha": OLD, "diff": "",
               "history": [{"id": "existing-finding"}]}
    args = (item, {"id": "connection", "kind": kind, "model": "test"}, {}, task, context, 100, 60, lambda: False, uid())
    if redaction_failure:
        with pytest.raises(security_engine.verification.VerificationOutputError) as error:
            ask_security(*args)
        assert error.value.usage == {"tokens": 28} and error.value.feedback["kind"] == "redaction"
    else:
        result, usage = ask_security(*args, record_required_paths=requested.extend)
        assert result["verdict"] == "uncertain" and result["required_paths"] == []
        assert result.get("finding_id") is None and usage == {"tokens": 28}
        assert "guard4.py" in result["explanation"] and "four evidence passes" in result["explanation"]
        assert requested == [f"guard{i}.py" for i in range(1, 5)]
    assert calls == [100, 93, 86, 79]
    assert reads == [(f"guard{i}.py", revision) for i in range(1, 4)
                     for revision in ([None, OLD] if task == "verification" else [None])]


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("limit", ["budget", "timeout", "cancel", "unknown_usage"])
def test_security_missing_path_correction_respects_execution_limits(tmp_path, monkeypatch, kind, limit):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    calls, clock = [], [0]
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def response(*args, **kwargs):
        calls.append(1)
        if limit == "timeout":
            clock[0] = 60
        return {"verdict": "uncertain", "finding_id": None, "explanation": "Need settings", "evidence": [],
                "required_paths": ["config/settings.py"]}, {"tokens": None if limit == "unknown_usage" else 7}

    monkeypatch.setattr(workflow_inference, "execute", response)
    context = {"sources": {"current": {}}, "diff": ""}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    def stopped():
        return bool(calls) and limit == "cancel"

    error_type = security_engine.ReviewStopped if limit == "cancel" else security_engine.verification.VerificationOutputError
    with pytest.raises(error_type) as error:
        ask_security(item, connection, {}, "assessment", context, 7 if limit == "budget" else 100, 60, stopped, uid())
    assert calls == [1]
    if limit != "cancel":
        assert error.value.usage == {"tokens": None if limit == "unknown_usage" else 7}


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
@pytest.mark.parametrize("case", ["empty", "mixed", "repeat", "budget", "unknown_usage"])
def test_security_repeated_evidence_gets_one_correction(tmp_path, monkeypatch, kind, task, case):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}, "guard.py": {}}
    reads, calls, contexts = [], [], []
    bundle = security_engine.verification.evidence_bundle

    def read(path, sha=None):
        reads.append((path, sha))
        return "safe guard"

    def evidence_bundle(context):
        contexts.append(json.loads(json.dumps(context)))
        return bundle(context)

    def response():
        paths = ["app.py"] if len(calls) == 1 or case == "repeat" else []
        if case == "mixed" and len(calls) == 1:
            paths += ["guard.py", "guard.py"]
        if task == "analysis":
            return {"findings": [], "required_paths": paths}
        return {"verdict": "uncertain", "explanation": "Synthetic test", "evidence": [], "required_paths": paths,
                **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": False})}

    def api(*args, **kwargs):
        evidence_bundle(args[2])
        calls.append(kwargs["token_budget"])
        return kwargs["validator"](response(), args[2]), {"tokens": None if case == "unknown_usage" else 7}


    item.read = read
    monkeypatch.setattr(workflow_inference, "execute", api)
    monkeypatch.setattr(security_engine.verification, "evidence_bundle", evidence_bundle)
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": ""}}, "diff": "", "original_sha": OLD}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    budget = 7 if case == "budget" else 100
    if case in {"repeat", "budget", "unknown_usage"}:
        with pytest.raises(security_engine.verification.VerificationOutputError) as error:
            ask_security(item, connection, {}, task, context, budget, 60, lambda: False, uid())
        assert error.value.usage == {"tokens": None if case == "unknown_usage" else 14 if case == "repeat" else 7}
        if case == "repeat":
            assert error.value.feedback["required_paths"] == ["app.py"]
            assert error.value.feedback["kind"] == "evidence"
    else:
        result, usage = ask_security(item, connection, {}, task, context, budget, 60, lambda: False, uid())
        assert result["required_paths"] == [] and usage == {"tokens": 14}
    assert calls == ([budget] if case in {"budget", "unknown_usage"} else [100, 93])
    assert "validation_feedback" not in contexts[0]
    if len(calls) == 2:
        assert contexts[1]["validation_feedback"]["kind"] == "evidence"
        assert contexts[1]["validation_feedback"]["required_paths"] == ["app.py"]
        assert "empty" in contexts[1]["validation_feedback"]["reason"]
        assert contexts[1]["sources"]["current"]["app.py"] == ""
    expected_reads = [("guard.py", None)] if case == "mixed" else []
    if case == "mixed" and task == "verification":
        expected_reads.append(("guard.py", OLD))
    assert reads == expected_reads


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
@pytest.mark.parametrize("corrected", [True, False])
def test_security_schema_errors_get_one_strict_correction(tmp_path, monkeypatch, kind, task, corrected):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    citation = {"revision": "current", "path": "app.py", "start_line": 1, "end_line": 1, "quote": "unsafe"}
    calls, contexts = [], []
    bundle = security_engine.verification.evidence_bundle

    def evidence_bundle(context):
        contexts.append(context)
        return bundle(context)

    def response():
        evidence = [{"source_id": "source_1", "start_line": 1, "end_line": 1}]
        if task == "analysis":
            finding = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
                       "explanation": "Synthetic test", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                       "verification": "Test", "evidence": evidence}
            raw = {"findings": [finding, dict(finding)], "required_paths": []}
            target = raw["findings"][1]
        else:
            raw = {"verdict": "uncertain", "explanation": "Synthetic test", "evidence": evidence, "required_paths": [],
                   **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": True})}
            target = raw
        if len(calls) == 1 or not corrected:
            target.update(start_line=1, end_line=1, quote="unsafe")
        return raw

    def infer(connection, credentials, context, **kwargs):
        calls.append(kwargs["token_budget"])
        contexts.append(context)
        try:
            return kwargs["validator"](response(), context), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            raise security_engine.verification.VerificationOutputError("Invalid schema", {"tokens": 7}, feedback=exc.feedback) from exc

    monkeypatch.setattr(workflow_inference, "execute", infer)
    context = {"assigned_path": "app.py", "sources": {"current": {"app.py": "unsafe"}}, "diff": "", "history": []}
    connection = {"id": "connection", "kind": kind, "model": "test"}
    if corrected:
        result, usage = ask_security(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert usage == {"tokens": 14}
        assert (result["findings"][1] if task == "analysis" else result)["evidence"] == [citation]
    else:
        with pytest.raises(security_engine.verification.VerificationOutputError) as error:
            ask_security(item, connection, {}, task, context, 100, 60, lambda: False, uid())
        assert error.value.usage == {"tokens": 14}
    assert calls == [100, 93]
    assert "validation_feedback" not in contexts[0]
    feedback = contexts[1]["validation_feedback"]
    assert feedback["kind"] == "schema"
    assert [problem["loc"][-1] for problem in feedback["errors"]] == ["start_line", "end_line", "quote"]
    assert all(problem["type"] == "extra_forbidden" and "input" not in problem for problem in feedback["errors"])
    if task == "analysis":
        assert all(problem["loc"][:2] == ("findings", 1) for problem in feedback["errors"])


@pytest.mark.parametrize("kind", ["api"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
def test_security_corrects_invalid_source_selections_once_with_known_usage(tmp_path, monkeypatch, kind, task):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    source = 'url = "synthetic"'
    calls, identities, clock = [], [], [0]
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def response():
        evidence = [{"source_id": "invented" if len(calls) == 1 else "source_1", "start_line": 1, "end_line": 1}]
        if task == "analysis":
            finding = {"title": "Synthetic defect", "category": "test", "severity": "low", "path": "app.py",
                       "explanation": "Synthetic test", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                       "verification": "Test", "evidence": evidence}
            return {"findings": [finding, finding], "required_paths": []}
        return {"verdict": "uncertain", "explanation": "Synthetic test", "evidence": evidence, "required_paths": [],
                **({"finding_id": None} if task == "assessment" else {"change_explanation": "", "original_issue_present": True})}

    def api(*args, **kwargs):
        calls.append((args[2], kwargs["token_budget"], kwargs["timeout"]))
        clock[0] += 10
        try:
            return kwargs["validator"](response(), args[2]), {"tokens": 7}
        except security_engine.verification.ResultValidationError as exc:
            assert len(exc.feedback["errors"]) == (2 if task == "analysis" else 1)
            raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": 7}, feedback=exc.feedback) from exc


    monkeypatch.setattr(workflow_inference, "execute", api)
    result, usage = ask_security(item, {"id": "connection", "kind": kind, "model": "test"}, {}, task,
        {"assigned_path": "app.py", "sources": {"current": {"app.py": source}}, "diff": "", "history": []},
        100, 60, lambda: False, uid())
    assert usage == {"tokens": 14} and len(calls) == 2
    assert [call[1:] for call in calls] == [(100, 60), (93, 50)]
    if kind == "api":
        feedback = calls[1][0]["validation_feedback"]
        assert feedback["kind"] == "citation_selection" and feedback["allowed_sources"] == ["source_1"]
        assert feedback["source_id"] == "invented"
        if task == "analysis":
            assert [error["field"] for error in feedback["errors"]] == ["findings[0].evidence[0]", "findings[1].evidence[0]"]
        assert "validation_feedback" not in calls[0][0]
    else:
        assert len(set(identities)) == 2
        assert '"allowed_sources": [' in calls[1][0]["prompt"]
    assert (result["findings"][0]["evidence"] if task == "analysis" else result["evidence"])[0]["quote"] == source


@pytest.mark.parametrize(("tokens", "budget", "expected_calls", "expected_usage"), [
    (5, 100, 2, 10), (None, 100, 1, None), (5, 5, 1, 5),
])
def test_security_citation_recovery_is_bounded_and_never_repeats_unknown_usage(
    tmp_path, monkeypatch, tokens, budget, expected_calls, expected_usage,
):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": tokens},
            feedback={"field": "evidence[0]", "errors": [{"reason": "invalid source line range"}]})

    monkeypatch.setattr(workflow_inference, "execute", api)
    with pytest.raises(security_engine.verification.VerificationOutputError) as error:
        ask_security(item, {"kind": "api"}, {}, "analysis", {"sources": {"current": {}}}, budget, 60, lambda: False, uid())
    assert len(calls) == expected_calls and error.value.usage["tokens"] == expected_usage


def test_security_citation_feedback_keeps_missing_source_ranges_and_quote_mismatches_distinct():
    evidence = [{"revision": "current", "path": "app.py", "start_line": start, "end_line": start, "quote": quote}
                for start, quote in [(1, "safe}"), (5, "missing"), (1, " ")]]
    evidence.append({**evidence[0], "path": "missing.py"})
    with pytest.raises(security_engine.verification.ResultValidationError) as error:
        security_engine.validate_citations(evidence, {"current": {"app.py": "safe\nsafe"}})
    problems = error.value.feedback["errors"]
    assert [problem["reason"] for problem in problems] == [
        "quote is not verbatim text within the cited source lines", "invalid source line range",
        "quote is not verbatim text within the cited source lines", "unavailable source",
    ]
    assert problems[0]["source_lines"] == problems[2]["source_lines"] == "safe\n"
    assert "source_lines" not in problems[1] and "source_lines" not in problems[3]


@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
def test_security_does_not_start_correction_after_cancellation_or_timeout(tmp_path, monkeypatch, interruption):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    clock, calls = [0], []
    monkeypatch.setattr(security_engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def api(*args, **kwargs):
        calls.append(1)
        clock[0] = 60
        raise security_engine.verification.VerificationOutputError("Invalid citation", {"tokens": 7},
            feedback={"field": "evidence[0]", "errors": [{"reason": "invalid source line range"}]})

    monkeypatch.setattr(workflow_inference, "execute", api)
    error_type = security_engine.ReviewStopped if interruption == "cancel" else security_engine.verification.VerificationOutputError
    with pytest.raises(error_type) as error:
        ask_security(item, {"kind": "api"}, {}, "analysis", {"sources": {"current": {}}}, 100, 60,
            lambda: bool(calls) and interruption == "cancel", uid())
    assert len(calls) == 1
    if interruption == "timeout":
        assert error.value.usage == {"tokens": 7}


def test_secret_censored_multiline_identity_is_recovered_without_persisting_material(tmp_path):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    source = "key = '''BEGIN\nsynthetic-private-material\nEND'''\n"
    material = "BEGIN\nsynthetic-private-material\nEND"
    item.read = lambda *args: source
    raw = {"Offset": source.index("BEGIN"), "StartLine": 1, "EndLine": 3,
           "Match": "key = '''*****", "Code": {"Lines": [{"Number": 3, "Content": "***'''"}]}}
    value, offset = security_engine.secret_material(item, "app.py", raw)
    assert value == material and source[offset:offset + len(value)] == value
    item.secret_values.append(value)
    assert material not in item.redact(source)
    assert "synthetic-private-material" not in item.redact(json.dumps({"source": source}))


@pytest.mark.parametrize(("acknowledged", "present", "outcome"), [(False, False, "uncertain"), (True, False, "fixed"), (True, True, "still_present")])
def test_secret_resolution_requires_rotation_and_repository_wide_removal(monkeypatch, acknowledged, present, outcome):
    material = "synthetic-private-material"
    evidence = {"kind": "secret", "source_sha": OLD, "rule_id": "fixture", "match_offset": 0, "match_bytes": len(material),
                "identity": security_engine.secure_digest("fixture\0" + material)}
    old = {"path": "secret.txt", "evidence": evidence, "rotation": {"identity": evidence["identity"]} if acknowledged else None}
    item = SimpleNamespace(sha=SHA, git=None, env={}, stopped=lambda: False, inventory={"elsewhere.txt": {}},
        renamed=lambda sha, path: path, diff=lambda sha, **kwargs: "removed", read=lambda path, sha=None: material if sha or present else "safe")
    monkeypatch.setattr(security.git_source, "git", lambda *args, **kwargs: "")
    assert security.deterministic_fix(item, old, {"targets": {}})["verdict"] == outcome


@pytest.mark.parametrize("data_age", [None, 1, 25])
def test_scanner_database_freshness_and_trusted_options(tmp_path, monkeypatch, data_age):
    cache = tmp_path / "cache"
    if data_age is not None:
        (cache / "db").mkdir(parents=True)
        (cache / "db/metadata.json").write_text(json.dumps({"UpdatedAt": (now() - timedelta(hours=data_age)).isoformat()}))
    config = SimpleNamespace(trivy_cache_dir=cache, trivy_binary="trivy", trivy_version="0.76.0",
                             encryption_key="synthetic-scanner-test-key")
    monkeypatch.setattr(security_engine, "settings", lambda: config)
    calls = []

    def command(args, *rest, **kwargs):
        calls.append(args)
        if "--version" in args:
            return 0, "Version: 0.76.0", ""
        if "--format" in args:
            return 0, json.dumps({"SchemaVersion": 2, "Results": []}), ""
        return 1, "", "unavailable"

    monkeypatch.setattr(security_engine.process, "command", command)
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    result = security_engine.trivy(item, 30, lambda: False)
    assert result["metadata"]["database_fresh"] == (data_age == 1)
    assert bool(result["problems"]) == (data_age != 1)
    scan = next(args for args in calls if "--format" in args)
    assert "--offline-scan" in scan and "--skip-check-update" in scan and "--skip-java-db-update" in scan
    assert "code_mortis.security_scans.scanner" in scan and "--show-secrets" not in scan


def test_unknown_severity_prevents_completion_and_grade(seed, security_github):
    with transaction() as db:
        scan = saved_scan(db, seed)
        saved_finding(db, seed, scan, severity="unknown")
        identity = scan.id
    security.finish(identity, {}, [], freshness(), True)
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "partial" and scan.grade is None and "unclassified" in scan.error


def test_cancel_saves_report_and_explicit_retry_creates_a_new_history_entry(seed, security_github, client):
    body = {"connection_id": "connection", "request_key": uid(), "token_budget": 100000, "timeout_seconds": 900}
    created = client.post("/api/v1/security/repositories/22/scans", json=body).json()
    cancelled = client.post(f"/api/v1/security/scans/{created['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/v1/security/scans/{created['id']}/report").status_code == 200
    changed = client.post(f"/api/v1/security/scans/{created['id']}/retry",
                          json={**body, "request_key": uid(), "concurrency": 2})
    assert changed.status_code == 409
    retry = client.post(f"/api/v1/security/scans/{created['id']}/retry", json={**body, "request_key": uid()})
    assert retry.status_code == 202 and retry.json()["parent_id"] == created["id"] and retry.json()["id"] != created["id"]


def test_busy_checks_cover_running_background_and_summary_jobs(seed):
    with transaction() as db:
        job = Job(id=uid(), kind="generate_background", payload={"connection_id": "connection"}, status="running")
        db.add(job)
        db.flush()
        assert security.connection_busy(db, "connection")
        assert not security.connection_busy(db, "connection", job_id=job.id)
        job.kind, job.payload = "generate_summary", {"config": {"connection": {"id": "connection"}}, "run_id": "run"}
        assert security.connection_busy(db, "connection") and not security.connection_busy(db, "connection", run_id="run")


def test_security_migration_preserves_existing_workspace(seed):
    from alembic import command
    from alembic.config import Config

    from code_mortis.models import Connection, PullRequest, User

    with transaction() as db:
        original = (db.get(User, 7).access_token, db.get(Connection, "connection").secret, db.get(PullRequest, "pr").title)
    command.downgrade(Config("alembic.ini"), "20261019_ignored_pulls")
    command.upgrade(Config("alembic.ini"), "head")
    with transaction() as db:
        assert (db.get(User, 7).access_token, db.get(Connection, "connection").secret, db.get(PullRequest, "pr").title) == original


def test_finished_report_grades_migration_uses_saved_findings_and_preserves_failures(seed):
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    cases = [("partial", "high", True, None, "D"), ("completed", "medium", True, None, "C"),
             ("partial", "unknown", True, None, None), ("partial", "high", False, None, None),
             ("cancelled", "high", True, None, None), ("partial", "high", True, "B", "B"),
             ("partial", None, True, None, "A")]
    identities = []
    with transaction() as db:
        for status, severity, supported, grade, expected in cases:
            scan = saved_scan(db, seed, status, grade=grade, finished_at=now(), error="Saved failure")
            scan.stages = {"report": {"status": "completed"}}
            scan.coverage = {"inventory": {"app.py": {"code": True}}} if supported else {}
            scan.report = {"schema_version": security_engine.POLICY, "grade": grade, "problems": ["Saved failure"],
                "findings": [{"state": "open", "severity": severity}] if severity else []}
            identities.append((scan.id, status, expected))
        saved_finding(db, seed, scan, severity="critical")  # Current state must not replace historical report evidence.
    try:
        command.downgrade(config, "20261020_security_scans")
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        for identity, status, expected in identities:
            scan = db.get(SecurityScan, identity)
            assert scan.grade == scan.report["grade"] == expected
            assert scan.status == status and scan.error == "Saved failure"
            assert scan.report["problems"] == ["Saved failure"]


@pytest.mark.parametrize("lost_response", [False, True])
def test_verified_issue_close_and_recurrence_reopen_reconcile_without_inference(seed, security_github, monkeypatch, lost_response):
    with transaction() as db:
        scan = saved_scan(db, seed, "completed")
        finding = saved_finding(db, seed, scan, state="resolved")
        identity = finding.id
        operation = uid()
        finding.issue = {"number": 9, "id": 99, "node_id": "issue-node", "operation_id": operation, "actor_id": 7,
            "closed_by_app": False, "desired_state": "closed", "intent_version": 0, "verification_scan_id": scan.id}
        body = security_issues.marker(finding, operation)
    bot = {"login": "mortis-test[bot]", "type": "Bot"}
    remote = {"id": 99, "node_id": "issue-node", "number": 9, "state": "open", "body": body,
        "html_url": "https://github.com/team/project/issues/9", "repository_url": "https://api.github.com/repos/team/project",
        "user": bot, "closed_by": None}
    comments, mutations = [], []

    def request(method, path, token, **kwargs):
        if method == "GET":
            if "/issues/9" in path:
                return dict(remote)
            return {"sha": SHA} if "/commits/" in path else security_github
        if method == "POST":
            assert path.endswith("/comments")
            comments.append({"body": kwargs["json"]["body"], "user": bot})
            return comments[-1]
        assert method == "PATCH"
        remote.update(state=kwargs["json"]["state"], closed_by=bot if kwargs["json"]["state"] == "closed" else None)
        mutations.append(kwargs["json"]["state"])
        if lost_response and len(mutations) == 1:
            raise github.GitHubError(503, "Connection closed after mutation")
        return dict(remote)

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: list(comments))
    monkeypatch.setattr(security_engine, "ask", lambda *args: pytest.fail("Issue synchronization must never repeat inference"))
    security_issues.sync(identity)
    if lost_response:
        security_issues.sync(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.state == "resolved" and finding.issue["closed_by_app"] and finding.issue["sync_status"] == "synced", finding.issue
        finding.state, finding.version = "open", 1
        finding.issue = {**finding.issue, "desired_state": "open", "intent_version": 1}
    security_issues.sync(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert finding.issue["remote_state"] == "open" and finding.issue["sync_status"] == "synced", finding.issue
        assert finding.issue["closed_by_app"] is False
    assert mutations == ["closed", "open"] and len(comments) == 2


def test_secret_material_and_configured_credentials_cannot_enter_issue_payload(seed, security_github, client):
    material = "synthetic-private-material"
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan, kind="secret", rule_id="fixture", match_bytes=len(material),
                                identity=security_engine.secure_digest("fixture\0" + material))
        identity = finding.id
    for secret in (material, "provider-secret"):
        response = client.post(f"/api/v1/security/findings/{identity}/github-issue", json={"title": "Security", "body": f"A credential: {secret}", "version": 0, "public": False})
        assert response.status_code == 422, response.text
    with transaction() as db:
        assert not db.get(SecurityFinding, identity).issue


def test_issue_double_submission_is_idempotent_and_stale_unsent_payload_can_be_reapproved(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        identity = finding.id
    path = f"/api/v1/security/findings/{identity}/github-issue"
    body = {"title": "Security finding", "body": "Apply an ownership guard.", "version": 0, "public": False}
    first = client.post(path, json=body)
    assert first.status_code == 202
    assert client.post(path, json=body).json()["issue"]["operation_id"] == first.json()["issue"]["operation_id"]
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        finding.version += 1
    security_issues.publish(identity)
    assert client.get(path).status_code == 200
    second = client.post(path, json={**body, "version": 1})
    assert second.status_code == 202
    assert second.json()["issue"]["operation_id"] != first.json()["issue"]["operation_id"]


def test_bulk_issue_previews_exclude_ineligible_findings_and_do_not_approve_anything(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        eligible = [saved_finding(db, seed, scan).id for _ in range(20)]
        eligible.append(saved_finding(db, seed, scan, state="accepted_risk").id)
        for state in ("resolved", "false_positive"):
            saved_finding(db, seed, scan, state=state)
        for issue in ({"number": 31}, {"publication_status": "pending"}, {"publication_status": "failed"}):
            saved_finding(db, seed, scan).issue = issue
        repo = Repository(id=23, installation_id=10, full_name="team/other", enabled=True)
        db.add(repo)
        db.flush()
        other_seed = {**seed, "repo": repo}
        saved_finding(db, other_seed, saved_scan(db, other_seed))
        # Saved visibility is deliberately stale; previews must use GitHub's current value.
        db.get(Repository, 22).private = False
    path = "/api/v1/security/repositories/22/github-issues"
    first, second = client.get(path).json(), client.get(path + "?page=3").json()
    middle = client.get(path + "?page=2").json()
    assert first["public"] is False and first["repository"] == "team/project"
    assert first["total"] == second["total"] == 21 and len(first["items"]) == 10
    assert {item["id"] for item in first["items"] + middle["items"] + second["items"]} == set(eligible)
    assert all(item["public"] is False and item["version"] == 0 and item["body"] for item in first["items"])
    with transaction() as db:
        assert not db.scalar(select(Job.id)) and not db.scalar(select(SecurityActivity.id))
        assert all(not db.get(SecurityFinding, identity).issue for identity in eligible)


def test_bulk_issue_approval_is_explicit_and_idempotent(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        identities = [saved_finding(db, seed, scan, state=state).id for state in ("open", "accepted_risk")]
    path = "/api/v1/security/repositories/22/github-issues"
    previews = client.get(path).json()["items"]
    drafts = [{key: item[key] for key in ("title", "body", "version", "public")} | {"finding_id": item["id"]} for item in previews]
    drafts[0].update(title="Reviewed security finding", body="Apply the reviewed ownership guard.")
    response = client.post(path, json={"issues": drafts})
    assert response.status_code == 202, response.text
    approved = response.json()
    assert [row["id"] for row in approved] == [draft["finding_id"] for draft in drafts]
    assert approved[0]["issue"]["title"] == drafts[0]["title"]
    assert approved[0]["issue"]["body"].startswith(drafts[0]["body"] + "\n\n<!--")
    assert all(row["issue"]["publication_status"] == "pending" for row in approved)
    retry = client.post(path, json={"issues": drafts})
    assert retry.status_code == 202 and retry.json() == approved
    assert client.get(path).json()["total"] == 0
    with transaction() as db:
        assert len(list(db.scalars(select(Job).where(Job.kind == "security_issue_publish")))) == 2
        assert len(list(db.scalars(select(SecurityActivity).where(SecurityActivity.action == "issue_approved")))) == 2
        assert {db.get(SecurityFinding, identity).state for identity in identities} == {"open", "accepted_risk"}


@pytest.mark.parametrize(("change", "status"), [
    ("version", 409), ("resolved", 409), ("false_positive", 409), ("linked", 409), ("pending", 409),
    ("missing", 404), ("foreign", 404), ("visibility", 409), ("duplicate", 422), ("secret", 422), ("blank", 422),
])
def test_bulk_issue_approval_revalidates_the_whole_selection_atomically(seed, security_github, client, change, status):
    with transaction() as db:
        scan = saved_scan(db, seed)
        first, second = saved_finding(db, seed, scan), saved_finding(db, seed, scan)
        identities = [first.id, second.id]
    drafts = [{"finding_id": identity, "title": "Security finding", "body": "Check ownership.", "version": 0, "public": False} for identity in identities]
    with transaction() as db:
        second = db.get(SecurityFinding, identities[1])
        if change == "version":
            second.version += 1
        elif change in {"resolved", "false_positive"}:
            second.state = change
        elif change in {"linked", "pending"}:
            second.issue = {"operation_id": uid(), "publication_status": "pending", **({"number": 42} if change == "linked" else {})}
        elif change == "foreign":
            db.add(Repository(id=23, installation_id=10, full_name="team/other", enabled=True))
            db.flush()
            second.repository_id = 23
    if change == "missing":
        drafts[1]["finding_id"] = uid()
    elif change == "visibility":
        security_github["private"] = False
    elif change == "duplicate":
        drafts[1] = drafts[0]
    elif change == "secret":
        drafts[1]["body"] = "Exposed credential: provider-secret"
    elif change == "blank":
        drafts[1]["title"] = " "
    response = client.post("/api/v1/security/repositories/22/github-issues", json={"issues": drafts})
    assert response.status_code == status, response.text
    with transaction() as db:
        assert not db.get(SecurityFinding, identities[0]).issue
        assert not db.scalar(select(Job.id)) and not db.scalar(select(SecurityActivity.id))


def test_bulk_issues_require_write_access_and_a_bounded_selection(seed, security_github, client, monkeypatch):
    path = "/api/v1/security/repositories/22/github-issues"
    draft = {"finding_id": uid(), "title": "Security", "body": "Fix the finding.", "version": 0, "public": False}
    for issues in ([], [draft] * 101):
        assert client.post(path, json={"issues": issues}).status_code == 422
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: (_ for _ in ()).throw(github.GitHubError(403, "Denied")))
    assert client.get(path).status_code == 403
    assert client.post(path, json={"issues": [draft]}).status_code == 403


def test_cancelled_scan_cannot_write_late_observations_and_retains_unknown_consumption(seed):
    with transaction() as db:
        scan = saved_scan(db, seed)
        finding = saved_finding(db, seed, scan)
        scan.progress = {"tokens": 20, "tasks": {"analysis:app.py": {"status": "running"}}}
        security.cancel(db, scan)
        assert scan.progress["tokens"] is None and scan.report["tokens"] is None
        with pytest.raises(security.ReviewStopped):
            security.observe(db, scan, finding.evidence)


def test_expired_security_job_preserves_interruption_without_automatic_inference_retry(seed, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed)
        scan.progress = {"tokens": 20, "tasks": {"analysis:app.py": {"status": "running"}}}
        identity = scan.id
        job = Job(id=uid(), kind="security_scan", payload={"scan_id": identity}, status="running", lease_until=now() - timedelta(minutes=1))
        db.add(job)
    monkeypatch.setattr(jobs.execute_job, "apply_async", lambda *args, **kwargs: pytest.fail("Interrupted inference must not be dispatched again"))
    jobs.dispatch()
    with transaction() as db:
        scan = db.get(SecurityScan, identity)
        assert scan.status == "partial" and scan.progress["tokens"] is None and "Retry explicitly" in scan.error


def test_issue_visibility_is_bound_to_the_reviewed_preview(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        identity = saved_finding(db, seed, scan).id
    path = f"/api/v1/security/findings/{identity}/github-issue"
    draft = client.get(path).json()
    assert draft["public"] is False
    body = {"title": draft["title"], "body": draft["body"], "version": draft["version"], "public": draft["public"]}
    security_github["private"] = False
    assert client.post(path, json=body).status_code == 409
    security_github["private"] = True
    assert client.post(path, json=body).status_code == 202
    security_github["private"] = False
    security_issues.publish(identity)
    with transaction() as db:
        finding = db.get(SecurityFinding, identity)
        assert not finding.issue["started"] and not finding.issue.get("number")
        assert "visibility changed" in finding.issue["error"]
    assert client.get(path).json()["public"] is True
    assert client.post(path, json={**body, "public": True}).status_code == 202


def test_security_overview_filters_all_visible_repositories_before_pagination(seed, security_github, client, monkeypatch):
    with transaction() as db:
        db.add_all([Repository(id=100 + index, installation_id=10, full_name=f"team/app-{index:02}", enabled=True)
                    for index in range(25)])
        db.add(Repository(id=999, installation_id=10, full_name="private/hidden", enabled=True))
        scan = saved_scan(db, seed, status="completed", grade="A", finished_at=now())
        scan.progress = {"tasks": {}, "freshness": freshness()}
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository).where(Repository.id != 999))))
    path = "/api/v1/security/repositories"
    first = client.get(path).json()
    second = client.get(path, params={"page": 3}).json()
    middle = client.get(path, params={"page": 2}).json()
    assert first["total"] == first["registered_total"] == 26
    assert len(first["items"]) == first["page_size"] == 10 and len(second["items"]) == 6
    assert len({row["repository"]["id"] for row in first["items"] + middle["items"] + second["items"]}) == 26
    assert first["grade_counts"] == {"A": 1, "unrated": 25}
    assert first["status_counts"] == {"current": 1, "not_scanned": 25}
    union = client.get(path, params=[("grade", "A"), ("grade", "unrated"), ("status", "current"), ("status", "not_scanned")]).json()
    assert union["total"] == 26
    filtered = client.get(path, params={"search": "APP-24", "grade": "unrated", "status": "not_scanned"}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["repository"]["id"] == 124
    assert filtered["grade_counts"] == first["grade_counts"] and filtered["registered_total"] == 26
    assert client.get(path, params={"page": 4}).json()["items"] == []
    for params in ({"page": 0}, {"grade": "Z"}, {"status": "invalid"}, {"search": "x" * 201}):
        assert client.get(path, params=params).status_code == 422


def test_security_finding_multiselect_uses_union_within_filters_and_intersection_between_filters(seed, security_github, client, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        scan_id = scan.id
        for index in range(26):
            severity, kind, state = (("high", "code", "open") if index < 21 else
                ("medium", "secret", "resolved") if index < 23 else
                ("low", "infrastructure", "accepted_risk") if index < 25 else ("unknown", "dependency", "false_positive"))
            finding = saved_finding(db, seed, scan, severity=severity, kind=kind, state=state)
            db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        unrelated = saved_scan(db, seed, status="completed")
        saved_finding(db, seed, unrelated, severity="critical", kind="dependency")
    repository_path = "/api/v1/security/repositories/22/findings"
    scan_path = f"/api/v1/security/scans/{scan_id}/findings"
    filters = [("severity", "high"), ("severity", "medium"), ("kind", "code"), ("kind", "secret")]
    for path in (repository_path, scan_path):
        params = filters + ([("state", "open"), ("state", "resolved")] if path == repository_path else [("outcome", "new")])
        first = client.get(path, params=params).json()
        second = client.get(path, params=params + [("page", "3")]).json()
        middle = client.get(path, params=params + [("page", "2")]).json()
        assert first["total"] == second["total"] == 23
        assert len(first["items"]) == 10 and len(second["items"]) == 3
        assert len({row["id"] for row in first["items"] + middle["items"] + second["items"]}) == 23
        assert first["severity_counts"]["low"] == 2 and first["kind_counts"]["infrastructure"] == 2
        assert first["registered_total"] == (27 if path == repository_path else 26)
        assert second["severity_counts"] == first["severity_counts"]
        assert client.get(path, params=filters + [("page", "4")]).json()["page"] == (4 if path == repository_path else 3)
        for invalid in ([("severity", "high"), ("severity", "invalid")], [("kind", "code"), ("kind", "invalid")], [("page", "0")]):
            assert client.get(path, params=invalid).status_code == 422
    assert client.get(repository_path, params=[("state", "open"), ("state", "resolved")]).json()["total"] == 24

    def denied(*args, **kwargs):
        raise HTTPException(403, "Access revoked")

    monkeypatch.setattr(github, "authorize_repo", denied)
    assert client.get(scan_path, params=filters + [("page", "2")]).status_code == 403
    assert client.get(repository_path, params=filters).status_code == 403


def test_repository_finding_facets_share_one_query_and_keep_unfiltered_counts(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        saved_finding(db, seed, scan)
        target = saved_finding(db, seed, scan, kind="dependency", state="open", path="target.py").id
        saved_finding(db, seed, scan, kind="dependency", severity="low", state="resolved")

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "FROM security_findings" in statement:
            statements.append(statement)

    path = "/api/v1/security/repositories/22/findings"
    event.listen(engine(), "after_cursor_execute", record)
    try:
        unfiltered = client.get(path).json()
        assert len(statements) == 2
        statements.clear()
        filtered = client.get(path, params={"state": "open", "search": "TARGET"}).json()
        assert len(statements) == 3
    finally:
        event.remove(engine(), "after_cursor_execute", record)
    assert unfiltered["total"] == unfiltered["registered_total"] == filtered["registered_total"] == 3
    assert filtered["total"] == 1 and [row["id"] for row in filtered["items"]] == [target]
    for key, expected in {
        "state_counts": {"open": 2, "resolved": 1},
        "severity_counts": {"high": 2, "low": 1},
        "kind_counts": {"code": 1, "dependency": 2},
    }.items():
        assert unfiltered[key] == filtered[key] == expected


def test_scan_findings_lookup_only_snapshot_ids_and_preserve_history(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        earlier = saved_scan(db, seed, status="completed")
        new = saved_finding(db, seed, scan)
        repeat = saved_finding(db, seed, earlier)
        fixed = saved_finding(db, seed, earlier, state="resolved")
        saved_finding(db, seed, earlier)
        foreign_repo = Repository(id=23, installation_id=10, full_name="team/other")
        db.add(foreign_repo)
        db.flush()
        foreign_seed = {**seed, "repo": foreign_repo}
        foreign_scan = saved_scan(db, foreign_seed, status="completed")
        foreign = saved_finding(db, foreign_seed, foreign_scan)
        missing_id = uid()
        scan.report = {"findings": [
            {**security.finding_snapshot(row), "finding_id": row.id, "outcome": "present"}
            for row in (new, repeat, foreign)
        ] + [
            {**security.finding_snapshot(repeat), "finding_id": missing_id, "outcome": "present"},
            {**security.finding_snapshot(fixed), "finding_id": fixed.id, "outcome": "fixed"},
        ]}
        scan_id, earlier_id = scan.id, earlier.id
        new_id, repeat_id, fixed_id, foreign_id = new.id, repeat.id, fixed.id, foreign.id

    returned_rows = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "SELECT security_findings.id, security_findings.first_scan_id" in statement:
            returned_rows.append(cursor.rowcount)

    path = f"/api/v1/security/scans/{scan_id}/findings"
    event.listen(engine(), "after_cursor_execute", record)
    try:
        present = client.get(path, params={"ordering": "position", "resolved": "false"}).json()
        resolved = client.get(path, params={"resolved": "true"}).json()
    finally:
        event.remove(engine(), "after_cursor_execute", record)

    assert returned_rows == [2, 1]
    assert present["registered_total"] == present["total"] == 4
    assert present["result_counts"] == {"new": 1, "open_repeat": 3}
    assert [(row["id"], row["first_scan_id"]) for row in present["items"]] == [
        (new_id, scan_id), (repeat_id, earlier_id), (foreign_id, scan_id), (missing_id, scan_id),
    ]
    assert resolved["result_counts"] == {"fixed": 1}
    assert [(row["id"], row["first_scan_id"]) for row in resolved["items"]] == [(fixed_id, earlier_id)]


def test_scan_activity_filters_findings_before_counts_and_pagination_preserving_finding_history(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        other = saved_scan(db, seed, status="completed")
        finding = saved_finding(db, seed, scan, title="Current title", path="current.py")
        db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present",
                                  snapshot={"title": "Title at this scan", "path": "original.py"}))
        for index in range(13):
            db.add(SecurityActivity(scan_id=scan.id, action="scan_stage", detail={"stage": "analysis", "status": "completed"},
                                    created_at=now() - timedelta(seconds=index * 2)))
            db.add(SecurityActivity(scan_id=scan.id, finding_id=finding.id, action="finding_created",
                                    created_at=now() - timedelta(seconds=index * 2 + 1)))
        db.add_all([
            SecurityActivity(scan_id=scan.id, finding_id=finding.id, action="finding_resolved"),
            SecurityActivity(scan_id=scan.id, finding_id=finding.id, action="ai_evidence_merged"),
            SecurityActivity(scan_id=scan.id, action="finding_created"),
            SecurityActivity(scan_id=other.id, action="scan_completed"),
        ])
        scan_id, finding_id = scan.id, finding.id
    path = f"/api/v1/security/scans/{scan_id}/activity"
    first, second = client.get(path).json(), client.get(path, params={"page": 2}).json()
    assert first["total"] == second["total"] == 13
    assert len(first["items"]) == 10 and len(second["items"]) == 3
    milestones = first["items"] + second["items"]
    assert len({row["id"] for row in milestones}) == 13
    assert all(row["action"] == "scan_stage" and row["finding_id"] is None for row in milestones)
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["activity_count"] == 13 and detail["activity"] == milestones
    assert client.get(path, params={"page": 3}).json()["items"] == []
    history = client.get(f"/api/v1/security/findings/{finding_id}/activity").json()
    assert history["total"] == 15
    assert all(row["finding_title"] == "Title at this scan" and row["finding_path"] == "original.py" for row in history["items"])
    assert client.get(f"/api/v1/security/findings/{finding_id}").json()["activity_count"] == 15


def test_compact_scan_history_and_repository_summary_skip_large_scan_records(seed, security_github, client, monkeypatch):
    created = now()
    fields = {"id", "branch", "head_sha", "status", "grade", "created_at", "started_at", "finished_at"}
    evidence = {"path": "app.py", "explanation": "Saved finding evidence. " * 5000}
    inventory = {f"src/{index}.py": {"code": True} for index in range(300)}
    with transaction() as db:
        scan_ids = []
        for _ in range(4):
            scan = saved_scan(db, seed, status="completed", grade="A", created_at=created,
                              started_at=created, finished_at=created + timedelta(seconds=42))
            scan.progress = {"freshness": freshness(), "tasks": {
                f"analysis:{path}": {"status": "completed"} for path in inventory}}
            scan.coverage = {"inventory": inventory}
            scan.report = {"findings": [evidence]}
            scan_ids.append(scan.id)
    path = "/api/v1/security/repositories/22"
    scans = f"{path}/scans"
    projections = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "FROM security_scans" in statement:
            projections.append({column.name for column in cursor.description})

    event.listen(engine(), "after_cursor_execute", record)
    try:
        compact = client.get(scans, params={"compact": "true"})
        metadata = client.get(path)
    finally:
        event.remove(engine(), "after_cursor_execute", record)
    assert compact.status_code == metadata.status_code == 200
    assert projections and all(
        not row.intersection({"report", "coverage", "progress", "config", "stages"}) for row in projections)
    assert fields in projections and any("freshness" in row for row in projections)
    assert metadata.json()["summary"]["state"] == "current"
    assert metadata.json()["summary"]["grade"] == "A"
    assert metadata.json()["summary"]["latest_scan_id"] == max(scan_ids)
    assert len(compact.content) < 2000
    response = client.get(scans)
    assert response.status_code == 200
    assert len(response.content) > 400_000
    full = response.json()
    history = compact.json()
    assert history == {**full, "items": [{key: row[key] for key in fields} for row in full["items"]]}
    assert history["total"] == 4
    assert [item["id"] for item in history["items"]] == sorted(scan_ids, reverse=True)
    assert client.get(scans, params={"compact": "true", "page": 2}).json()["items"] == []
    detail = f"/api/v1/security/scans/{max(scan_ids)}"
    assert client.get(detail).json()["scan"]["coverage"]["inventory_count"] == len(inventory)
    assert client.get(f"{detail}/report").json()["findings"] == [evidence]

    def denied(*args, **kwargs):
        raise HTTPException(403, "Access revoked")

    monkeypatch.setattr(github, "authorize_repo", denied)
    assert client.get(scans, params={"compact": "true"}).status_code == 403


def test_security_histories_and_coverage_have_backend_pages_and_recheck_access(seed, security_github, client, monkeypatch):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        scan.coverage = {"inventory": {f"src/{index:03}.py": {} for index in range(25)},
            "excluded": [{"path": f"dist/{index}.js", "reason": "Generated"} for index in range(25)],
            "units": [{"task": f"analysis:{index}", "status": "completed"} for index in range(25)],
            "problems": [{"path": f"source/{index}.py", "reason": "Unsupported"} for index in range(25)]}
        finding = saved_finding(db, seed, scan)
        scan_id, finding_id = scan.id, finding.id
        for index in range(25):
            observation_scan = scan if index == 0 else saved_scan(db, seed, status="completed")
            db.add(SecurityOccurrence(scan_id=observation_scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
            db.add(SecurityActivity(scan_id=scan.id, finding_id=finding.id, action="finding_observed", created_at=now() - timedelta(seconds=index)))
            db.add(SecurityActivity(scan_id=scan.id, action="scan_stage", created_at=now() - timedelta(seconds=index)))
        other = saved_scan(db, seed, status="completed")
        db.add(SecurityActivity(scan_id=other.id, action="scan_created"))
    paths = [f"/api/v1/security/scans/{scan_id}/activity", f"/api/v1/security/findings/{finding_id}/activity",
             f"/api/v1/security/findings/{finding_id}/observations",
             *[f"/api/v1/security/scans/{scan_id}/coverage/{section}" for section in ("inventory", "excluded", "units", "problems")]]
    for path in paths:
        first, second = client.get(path).json(), client.get(path, params={"page": 3}).json()
        middle = client.get(path, params={"page": 2}).json()
        assert first["total"] == second["total"] == 25
        assert len(first["items"]) == first["page_size"] == 10 and len(second["items"]) == 5
        assert len({row["id"] for row in first["items"] + middle["items"] + second["items"]}) == 25
        assert client.get(path, params={"page": 4}).json()["items"] == []
        assert client.get(path, params={"page": 0}).status_code == 422
    detail = client.get(f"/api/v1/security/scans/{scan_id}").json()
    assert detail["scan"]["coverage"]["inventory_count"] == 25
    history_path = "/api/v1/security/repositories/22/scans"
    history = client.get(history_path).json()
    assert history["page_size"] == len(history["items"]) == 10 and history["total"] == 26
    compact_pages = [client.get(history_path, params={"compact": "true", "page": page}).json() for page in (1, 2, 3)]
    assert [len(page["items"]) for page in compact_pages] == [10, 10, 6]
    assert len({row["id"] for page in compact_pages for row in page["items"]}) == 26
    assert [row["id"] for row in compact_pages[0]["items"]] == [row["id"] for row in history["items"]]
    assert not {"inventory", "excluded", "units", "problems"}.intersection(detail["scan"]["coverage"])
    detail = client.get(f"/api/v1/security/findings/{finding_id}").json()
    assert detail["observation_count"] == detail["activity_count"] == 25

    def denied(*args, **kwargs):
        raise HTTPException(403, "Access revoked")

    monkeypatch.setattr(github, "authorize_repo", denied)
    for path in paths:
        assert client.get(path, params={"page": 2}).status_code == 403


def test_security_repository_ordering_uses_latest_security_changes_before_pagination(seed, security_github, client, monkeypatch):
    base = now() - timedelta(days=10)
    with transaction() as db:
        scans = []
        for index in range(25):
            repo = Repository(id=100 + index, installation_id=10, full_name=f"team/app-{index:02}", enabled=True)
            db.add(repo)
            db.flush()
            scan = saved_scan(db, {**seed, "repo": repo}, status="completed")
            scan.created_at = base + timedelta(hours=index)
            scans.append(scan)
        scans[0].finished_at = base + timedelta(hours=30)
        finding = saved_finding(db, {**seed, "repo": db.get(Repository, 101)}, scans[1])
        finding.updated_at = base + timedelta(hours=31)
        db.add(SecurityActivity(scan_id=scans[2].id, action="scan_stage", created_at=base + timedelta(hours=32)))
        finding = saved_finding(db, {**seed, "repo": db.get(Repository, 103)}, scans[3])
        finding.updated_at = base + timedelta(hours=3)
        db.add(SecurityActivity(finding_id=finding.id, action="issue_state_synced", created_at=base + timedelta(hours=33)))
        scans[4].created_at = base + timedelta(hours=33)
        scans[5].progress = {"tasks": {}, "freshness": freshness()}
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: list(db.scalars(select(Repository))))
    path = "/api/v1/security/repositories"

    def identities(params=None):
        first = client.get(path, params=params or {}).json()
        second = client.get(path, params={**(params or {}), "page": 3}).json()
        middle = client.get(path, params={**(params or {}), "page": 2}).json()
        assert first["total"] == second["total"] == 26
        assert len(first["items"]) == 10 and len(second["items"]) == 6
        return [row["repository"]["id"] for row in first["items"] + middle["items"] + second["items"]]

    expected = [103, 104, 102, 101, 100, *range(124, 104, -1), 22]
    assert identities() == expected
    assert identities({"ordering": "-updated_at"}) == expected
    assert identities({"ordering": "updated_at"}) == [*range(105, 125), 100, 101, 102, 103, 104, 22]
    assert identities({"ordering": "repository"}) == [*range(100, 125), 22]
    assert identities({"ordering": "-repository"}) == [22, *range(124, 99, -1)]
    assert client.get(path, params={"search": "app-0", "ordering": "-updated_at"}).json()["items"][0]["repository"]["id"] == 103
    assert client.get(path, params={"ordering": "invalid"}).status_code == 422


def test_security_ordering_sorts_full_lists_and_preserves_saved_evidence(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed)
        rows = []
        for index in range(21):
            severity = "unknown" if index == 0 else ["low", "medium", "high", "critical"][index % 4]
            finding = saved_finding(db, seed, scan, severity=severity, path=f"src/{20 - index:02}.py")
            db.flush()
            rows.append({"id": finding.id, "finding_id": finding.id, "severity": severity,
                         "path": finding.path, "title": f"Saved title {index}"})
            db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        scan.report = {"findings": rows}
        scan.stages = {"analysis": {"status": "completed"}}
        scan.coverage = {"inventory": {"app.py": {}}}
        scan.progress = {"tasks": {"analysis:app.py": {"status": "completed", "result": {"findings": rows}}}}
        scan_id = scan.id
    paths = ["/repositories/22/findings", f"/scans/{scan_id}/findings", f"/scans/{scan_id}/report/findings", f"/scans/{scan_id}/assessments"]
    ranks = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    for path in paths:
        assert client.get("/api/v1/security" + path).json() == client.get("/api/v1/security" + path, params={"ordering": "-severity"}).json()
        for ordering in ("severity", "-severity", "path", "-path"):
            pages = [client.get("/api/v1/security" + path, params={"ordering": ordering, "page": page}) for page in (1, 2, 3)]
            assert all(page.status_code == 200 for page in pages), [page.text for page in pages]
            items = [row.get("proposal", row) for page in pages for row in page.json()["items"]]
            assert len(items) == 21
            if "severity" in ordering:
                assert items[-1]["severity"] == "unknown"
                values = [ranks[row["severity"]] for row in items[:-1]]
            else:
                values = [row["path"] for row in items]
            assert values == sorted(values, reverse=ordering.startswith("-"))
        assert client.get("/api/v1/security" + path, params={"ordering": "invalid"}).status_code == 422
        if path.startswith("/scans/"):
            for ordering in ("created_at", "-created_at", "updated_at", "-updated_at"):
                assert client.get("/api/v1/security" + path, params={"ordering": ordering}).status_code == 422
    with transaction() as db:
        assert db.get(SecurityScan, scan_id).report["findings"] == rows


@pytest.mark.parametrize("scope", ["repository", "scan"])
def test_security_current_state_filters_and_default_severity_sort_before_paging(seed, security_github, client, scope):
    with transaction() as db:
        scan = saved_scan(db, seed)
        for state, total in [("open", 13), ("resolved", 15), ("false_positive", 1)]:
            for index in range(total):
                finding = saved_finding(db, seed, scan, state=state,
                    severity="critical" if index == total - 1 else "low", path=f"{state}/{index}.py")
                db.flush()
                db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id, outcome="present", snapshot=security.finding_snapshot(finding)))
        scan_id = scan.id
    path = "/api/v1/security/repositories/22/findings" if scope == "repository" else f"/api/v1/security/scans/{scan_id}/findings"
    for state, total in [("open", 13), ("resolved", 15)]:
        first = client.get(path, params={"state": state}).json()
        second = client.get(path, params={"state": state, "page": 2}).json()
        assert first["total"] == second["total"] == total
        assert len(first["items"]) == 10 and len(second["items"]) == total - 10
        assert first["items"][0]["severity"] == "critical"
        assert {item["state"] for item in first["items"] + second["items"]} == {state}
        assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}
        assert first["state_counts"] == {"open": 13, "resolved": 15, "false_positive": 1}
    assert client.get(path, params={"state": "invalid"}).status_code == 422


def test_report_state_groups_use_saved_states_and_preserve_unknown_history(seed, security_github, client):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed")
        live = saved_finding(db, seed, scan, state="open")
        rows = [{"id": f"{state}-{index}", "finding_id": live.id if state == "resolved" and index == 12 else f"{state}-{index}",
                 "state": state, "severity": "critical" if index == 12 else "low", "title": f"Saved {state} {index}",
                 "path": f"src/{index}.py", "outcome": "uncertain" if state == "open" else "fixed"}
                for state in ("open", "resolved") for index in range(13)]
        rows += [{"finding_id": "legacy", "outcome": "fixed"}, {"finding_id": "not-observed", "outcome": "not_observed"}]
        scan.report = {"findings": rows}
        scan_id = scan.id
    path = f"/api/v1/security/scans/{scan_id}/report/findings"
    for state in ("open", "resolved"):
        first = client.get(path, params={"state": state}).json()
        second = client.get(path, params={"state": state, "page": 2}).json()
        assert first["total"] == second["total"] == 13 and first["registered_total"] == 28
        assert len(first["items"]) == 10 and len(second["items"]) == 3
        assert first["items"][0]["severity"] == "critical"
        assert {row["state"] for row in first["items"] + second["items"]} == {state}
        assert first["state_counts"] == {"open": 13, "resolved": 13, "unknown": 2}
    legacy = client.get(path, params={"state": "unknown"}).json()
    assert [row["state"] for row in legacy["items"]] == ["unknown", "unknown"]
    assert [row["outcome"] for row in legacy["items"]] == ["fixed", "not_observed"]
    assert client.get(f"/api/v1/security/scans/{scan_id}/report").json()["findings"] == rows


@pytest.mark.parametrize("reported", [True, False])
def test_scan_findings_and_counts_are_historical_while_repository_is_current(seed, security_github, client, reported):
    with transaction() as db:
        scan = saved_scan(db, seed, status="completed" if reported else "running")
        for state in ("open", "resolved", "accepted_risk", "false_positive"):
            for index in range(12):
                finding = saved_finding(db, seed, scan, state=state, title=f"Saved {state} {index}", path=f"{state}/{index}.py")
                db.add(SecurityOccurrence(scan_id=scan.id, finding_id=finding.id,
                    outcome="fixed" if state == "resolved" else "present", snapshot=security.finding_snapshot(finding)))
        db.flush()
        if reported:
            scan.report = security.report_data(db, scan, [])
        scan_id = scan.id
    path = f"/api/v1/security/scans/{scan_id}"
    before = client.get(path).json()
    pages = {(state, page): client.get(path + "/findings", params={"state": state, "page": page}).json()
             for state in ("open", "resolved", "accepted_risk", "false_positive") for page in (1, 2)}
    assert before["finding_state_counts"] == {state: 12 for state in ("open", "resolved", "accepted_risk", "false_positive")}
    for (state, page), result in pages.items():
        assert result["total"] == 12 and len(result["items"]) == (10 if page == 1 else 2)
        assert all(item["state"] == state and item["title"].startswith("Saved") for item in result["items"])
    with transaction() as db:
        for finding in db.scalars(select(SecurityFinding).where(SecurityFinding.repository_id == 22)):
            finding.state = "resolved" if finding.state == "open" else "open"
            finding.title, finding.path, finding.severity = "Changed later", "later.py", "critical"
            finding.version += 1
    after = client.get(path).json()
    assert after["finding_state_counts"] == before["finding_state_counts"]
    assert after["finding_count"] == before["finding_count"]
    for (state, page), expected in pages.items():
        assert client.get(path + "/findings", params={"state": state, "page": page}).json() == expected
    current = client.get("/api/v1/security/repositories/22").json()
    assert current["finding_state_counts"] == {"open": 36, "resolved": 12}
    assert client.get("/api/v1/security/repositories/22/findings", params={"state": "open"}).json()["items"][0]["title"] == "Changed later"


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_security_repository_updated_order_accepts_nullable_scan_times(seed, security_github, client, monkeypatch, status):
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: [db.get(Repository, 22)])
    with transaction() as db:
        saved_scan(db, seed, status=status, created_at=now(), started_at=None, finished_at=None)
    response = client.get("/api/v1/security/repositories?ordering=-updated_at")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["repository"]["id"] == 22


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code", "unknown"])
@pytest.mark.parametrize("task", ["analysis", "assessment", "verification"])
def test_retired_security_transports_stop_before_source_or_provider_dispatch(tmp_path, monkeypatch, kind, task):
    item = security_engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {"app.py": {}}
    item.source = lambda *a, **k: pytest.fail("Retired security execution read source")
    monkeypatch.setattr(workflow_inference, "execute", lambda *a, **k: pytest.fail("Silent shared fallback"))
    with pytest.raises(ValueError, match="retired"):
        ask_security(item, {"id": "old", "kind": kind, "model": "old-model"}, {}, task,
                            {"sources": {"current": {}}, "diff": ""}, 1000, 60, lambda: False, uid())


def test_retry_of_mortis_history_keeps_inert_ocr_metadata(seed, security_github):
    with transaction() as db:
        repo, actor = db.get(Repository, 22), db.get(User, 7)
        initial = security.create_scan(db, repo, actor, SecurityScanInput(connection_id="connection", request_key=uid()))
        initial.status = "partial"
        initial.config = {**initial.config, "connection": {**initial.config["connection"], "ocr_version": "1.13.1"}}
        original = deepcopy(initial.config)
        retried = security.create_scan(db, repo, actor,
            SecurityScanInput(connection_id="connection", request_key=uid()), parent=initial)
        assert retried.parent_id == initial.id and retried.config == original
        assert initial.config == original and retried.config["analysis_engine"] == "mortis"


def test_rolling_admission_failure_keeps_pending_provider_block_reason():
    from concurrent.futures import Future

    future = Future()
    future.set_exception(RunnerBlocked("quota"))
    stopped, submitted = Event(), []

    def submit(path):
        submitted.append(path)
        if len(submitted) > 1:
            raise ValueError("Token usage is unknown")
        return future

    results = list(security.rolling_analysis(["first.py", "second.py", "third.py"], 3, submit, lambda: False, stopped))
    assert results == [("first.py", future)]
    assert isinstance(results[0][1].exception(), RunnerBlocked)
    assert results[0][1].exception().reason == "quota"
    assert submitted == ["first.py", "second.py"] and stopped.is_set()
