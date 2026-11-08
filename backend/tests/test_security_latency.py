"""Independent cross-file fixtures; no pilot source, truth, or provider access."""

import json
import time
from contextlib import nullcontext
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Lock, Thread

import pytest
from sqlalchemy import select

from code_mortis.core.analysis import engine_snapshot
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction, uid
from code_mortis.core.errors import ReviewStopped
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.models import (
    AnalysisAttempt,
    AnalysisBudget,
    Job,
    SecurityFinding,
    SecurityOccurrence,
    SecurityScan,
)
from code_mortis.schemas.security import SecurityScanInput
from code_mortis.security_scans import engine, service

SHA = "a" * 40
CONNECTION = {"id": "connection", "kind": "api", "model": "test-model", "protocol": "openai", "options": {}}


def snapshot(tmp_path, sources):
    item = engine.Snapshot(tmp_path, SHA, {}, lambda: False)
    item.inventory = {path: {"code": True, "hash": digest(text), "lines": len(text.splitlines())}
                      for path, text in sources.items()}
    item.read = lambda path, sha=None: sources[path]
    return item


@pytest.mark.parametrize("task", ["analysis", "assessment"])
def test_small_snapshot_packs_complete_redacted_support_before_first_call(tmp_path, task):
    item = snapshot(tmp_path, {"route.py": "return records[request.key]", "guard.py": "assert token == synthetic-secret"})
    item.secret_values = ["synthetic-secret"]
    item.inventory["AGENTS.md"] = {"code": True}
    calls = []
    context = {"assigned_path": "route.py", "history": [], "diff": "", "head_sha": SHA, "task_contract": engine.TASK_CONTRACT,
               "sources": {"current": {"route.py": item.source("route.py")}}}

    def infer(supplied, **kwargs):
        calls.append(supplied)
        assert set(supplied["sources"]["current"]) == {"route.py", "guard.py"}
        assert supplied["sources"]["current"]["guard.py"] == "assert token == [redacted]"
        assert "AGENTS.md" not in supplied["available_paths"]
        result = {"findings": [], "required_paths": []} if task == "analysis" else {
            "verdict": "rejected", "finding_id": None, "explanation": "The supplied guard protects access.",
            "evidence": [], "required_paths": []}
        return kwargs["validator"](result, supplied), {"tokens": 9}

    result, usage = engine.ask(item, CONNECTION, {}, task, context, 100_000, 60, lambda: False, "task", infer=infer)
    assert usage == {"tokens": 9} and len(calls) == 1
    assert context["sources"]["current"] == {"route.py": "return records[request.key]"}
    assert result["required_paths"] == []


@pytest.mark.parametrize("limit", ["bytes", "files", "prompt", "budget", "unreadable"])
def test_optional_packing_preserves_focused_task_when_support_cannot_fit(tmp_path, limit):
    sources = {"route.py": "return lookup(key)", "guard.py": "x" * (32769 if limit == "bytes" else 2000)}
    if limit == "files":
        sources.update({f"extra{index}.py": "pass" for index in range(15)})
    item = snapshot(tmp_path, sources)
    if limit == "unreadable":
        item.source = lambda path: (_ for _ in ()).throw(ValueError("Unavailable optional source"))
    calls = []

    def infer(supplied, **kwargs):
        calls.append(supplied)
        assert supplied["sources"]["current"] == {"route.py": sources["route.py"]}
        return kwargs["validator"]({"findings": [], "required_paths": []}, supplied), {"tokens": 4}

    connection = {**CONNECTION, "options": {"prompt_token_ceiling": 7000} if limit == "prompt" else {}}
    engine.ask(item, connection, {}, "analysis", {"assigned_path": "route.py", "diff": "", "task_contract": engine.TASK_CONTRACT, "sources": {
        "current": {"route.py": sources["route.py"]}}}, 16_000 if limit == "budget" else 100_000, 60,
        lambda: False, "task", infer=infer)
    assert len(calls) == 1


def test_optional_packing_obeys_cancellation_before_reading_support(tmp_path):
    item = snapshot(tmp_path, {"route.py": "pass", "guard.py": "pass"})
    item.source = lambda path: pytest.fail("Read source after cancellation")
    with pytest.raises(ReviewStopped):
        engine.ask(item, CONNECTION, {}, "analysis", {"task_contract": engine.TASK_CONTRACT, "sources": {"current": {"route.py": "pass"}}},
                   100_000, 60, lambda: True, "task", infer=lambda *args, **kwargs: pytest.fail("Dispatched"))


def test_optional_source_reads_consume_task_deadline(tmp_path, monkeypatch):
    item = snapshot(tmp_path, {"route.py": "pass", "guard.py": "pass"})
    elapsed = [0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: elapsed[0])

    def source(path):
        elapsed[0] += 6
        return "pass"

    item.source = source
    with pytest.raises(engine.verification.VerificationOutputError, match="time limit") as caught:
        engine.ask(item, CONNECTION, {}, "analysis", {"task_contract": engine.TASK_CONTRACT, "sources": {"current": {"route.py": "pass"}}, "diff": ""},
                   100_000, 5, lambda: False, "task", infer=lambda *args, **kwargs: pytest.fail("Dispatched"))
    assert caught.value.usage == {"tokens": 0}


@pytest.mark.parametrize("first", ["packed", "budget", "read_failure"])
def test_persisted_source_choice_survives_changed_allowance_and_transient_reads(tmp_path, first):
    item = snapshot(tmp_path, {"route.py": "return lookup(key)", "guard.py": "x" * 2000})
    source, saved, requests = item.source, {}, []
    if first == "read_failure":
        item.source = lambda path: (_ for _ in ()).throw(ValueError("Transient source failure"))

    def select_packing(selected):
        return saved.setdefault("source_packing", selected)

    def infer(supplied, **kwargs):
        assert "source_packing" in saved, "Persist selection before any durable dispatch."
        requests.append((json.dumps(supplied, sort_keys=True), json.dumps(kwargs["schema"], sort_keys=True)))
        return kwargs["validator"]({"findings": [], "required_paths": []}, supplied), {"tokens": 4}

    context = {"assigned_path": "route.py", "diff": "", "task_contract": engine.TASK_CONTRACT,
               "sources": {"current": {"route.py": "return lookup(key)"}}}
    engine.ask(item, CONNECTION, {}, "analysis", context, 100_000 if first == "packed" else 16_000,
               60, lambda: False, "task", infer=infer, select_packing=select_packing)
    item.source = source
    engine.ask(item, CONNECTION, {}, "analysis", context, 0 if first == "packed" else 100_000,
               60, lambda: False, "task", infer=infer, source_packing=saved["source_packing"],
               select_packing=select_packing)
    assert saved["source_packing"] == (first == "packed")
    assert requests[0] == requests[1]


@pytest.fixture
def delayed_security_provider(seed, github_stub, tmp_path, monkeypatch):
    sources = {"alpha.py": "return alpha_store[request.alpha]", "beta.py": "return beta_store[request.beta]",
               "guard.py": "def authenticated(request): return request.user is not None"}
    item = snapshot(tmp_path, sources)
    # Supporting files remain available without creating another required analysis assignment.
    item.inventory["guard.py"]["code"] = False
    state = {"active": 0, "peak": 0, "calls": [], "finished": {}, "fault": None, "usage": {},
             "after_response": None, "persisted": []}
    lock, barrier = Lock(), Barrier(2)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            content = body["messages"][-1]["content"]
            metadata = json.JSONDecoder().raw_decode(content.split("\n\n", 1)[1])[0]
            task = "assessment" if "proposal" in metadata else "analysis"
            path = metadata["proposal"]["path"] if task == "assessment" else metadata["assigned_path"]
            reference = next(key for key, value in metadata["citation_sources"].items() if value["path"] == path)
            evidence = [{"source_id": reference, "start_line": 1, "end_line": 1}]
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                state["calls"].append((task, path, time.monotonic()))
            barrier.wait(timeout=10)
            # Reverse completion order while preserving source-order persistence.
            time.sleep(0.08 if path == "alpha.py" else 0.01)
            result = {"findings": [{"title": "Unowned " + path, "category": "authorization", "severity": "high",
                "path": path, "explanation": "The route selects private records without checking their owner.",
                "trigger": "An authenticated user supplies a different owner's key.", "impact": "Private record disclosure.",
                "remediation": "Check the record owner.", "verification": "Try another owner's key.", "evidence": evidence}],
                "required_paths": []} if task == "analysis" else {"verdict": "confirmed", "finding_id": None,
                "explanation": "Authentication does not check record ownership.", "evidence": evidence, "required_paths": []}
            if task == "analysis" and state["after_response"]:
                state["after_response"]()
            tokens = state["usage"].get((task, path), 12)
            response = json.dumps({"id": uid(), "choices": [{"message": {"role": "assistant", "content": json.dumps(result)},
                "finish_reason": "stop"}], "usage": {} if state["fault"] == "unknown" else {
                    "prompt_tokens": tokens - 5, "completion_tokens": 5, "total_tokens": tokens}}).encode()
            with lock:
                state["active"] -= 1
                state["finished"][task, path] = time.monotonic()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings(), "allow_private_endpoints", True)
    remote = {"id": 22, "private": True, "default_branch": "main", "permissions": {"pull": True, "push": True}}
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: remote)
    monkeypatch.setattr(github, "request", lambda method, path, token, **kw: {"sha": SHA} if "/commits/" in path else remote)
    monkeypatch.setattr(engine, "snapshot", lambda *args: nullcontext(item))
    monkeypatch.setattr(engine, "trivy", lambda *args: {"findings": [], "targets": {}, "problems": [], "metadata": {}})
    observe = service.observe

    def observed(db, scan, evidence, existing_id=None):
        state["persisted"].append(evidence["path"])
        return observe(db, scan, evidence, existing_id)

    monkeypatch.setattr(service, "observe", observed)
    with transaction() as db:
        connection = db.get(type(seed["connection"]), "connection")
        connection.base_url = f"http://127.0.0.1:{server.server_port}/v1"
        scan = service.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=2, token_budget=200_000))
        scan.config = {**scan.config, **engine_snapshot({"analysis_engine": "mortis"})}
        job = db.scalar(select(Job).where(Job.kind == "security_scan"))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=10)
        state.update(scan_id=scan.id, claim=f"{job.id}:1")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_independent_security_assessments_overlap_and_persist_deterministically(delayed_security_provider):
    state = delayed_security_provider
    service.perform(state["scan_id"], state["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        assert scan.status == "completed", scan.error
        assert scan.progress["tokens"] == 48
        assert scan.stages["analysis"]["completed"] == scan.stages["assessment"]["completed"] == 2
        assert sorted(row["path"] for row in scan.report["findings"]) == ["alpha.py", "beta.py"]
        assert db.scalar(select(AnalysisBudget)).charged_tokens == 48
        assert all(row.usage_state == "known" for row in db.scalars(select(AnalysisAttempt)))
        assert len(list(db.scalars(select(SecurityFinding)))) == len(list(db.scalars(select(SecurityOccurrence)))) == 2
    assert state["peak"] == 2
    assert state["persisted"] == ["alpha.py", "beta.py"]
    assessments = [started for task, _, started in state["calls"] if task == "assessment"]
    assert len(assessments) == 2
    assert max(assessments) < min(state["finished"]["assessment", path] for path in ("alpha.py", "beta.py"))
    assert len(state["calls"]) == 4


def test_partial_peer_checkpoint_replays_packed_result_after_remaining_budget_shrinks(
    delayed_security_provider, monkeypatch,
):
    from code_mortis.core import workflow_inference

    class WorkerLost(BaseException):
        pass

    state = delayed_security_provider
    state["usage"]["analysis", "alpha.py"] = 28_000
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        scan.config = {**scan.config, "limits": {**scan.config["limits"], "token_budget": 40_000}}
    execute, requests, lost = workflow_inference.execute, [], []

    def interrupted(*args, **kwargs):
        if kwargs["task_key"] == "analysis:beta.py:round:0":
            requests.append(json.dumps(args[2], sort_keys=True))
            result = execute(*args, **kwargs)
            if not lost:
                lost.append(True)
                raise WorkerLost()
            return result
        return execute(*args, **kwargs)

    monkeypatch.setattr(workflow_inference, "execute", interrupted)
    with pytest.raises(WorkerLost):
        service.perform(state["scan_id"], state["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        assert scan.progress["tokens"] == 28_000
        assert scan.progress["tasks"]["analysis:alpha.py"]["status"] == "completed"
        assert scan.progress["tasks"]["analysis:beta.py"]["source_packing"] is True
        assert scan.progress["tasks"]["analysis:beta.py"]["status"] == "running"
        job = db.get(Job, state["claim"].rsplit(":", 1)[0])
        job.attempts, job.lease_until = 2, now() + timedelta(minutes=10)
        claim = f"{job.id}:2"
    service.perform(state["scan_id"], claim)
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        task = scan.progress["tasks"]["analysis:beta.py"]
        assert task["status"] == "completed", scan.error
        assert task["source_packing"] is True and task["tokens"] == 12
        assert scan.progress["tokens"] == 28_012
        # Remaining capacity cannot admit fresh assessment work; replay never grants free inference.
        assert scan.status != "completed"
    assert len(requests) == 2 and requests[0] == requests[1]
    assert sorted((task, path) for task, path, _ in state["calls"]) == [
        ("analysis", "alpha.py"), ("analysis", "beta.py")]


def test_legacy_mortis_retry_keeps_v2_checkpoint_identity_and_focused_mode(
    delayed_security_provider, seed, monkeypatch,
):
    state = delayed_security_provider
    monkeypatch.setattr(service, "require_available_engine", lambda options: None)
    with transaction() as db:
        parent = db.get(SecurityScan, state["scan_id"])
        parent.config = {key: value for key, value in parent.config.items() if key != "task_contract"}
        config = {**parent.config, "limits": {key: value for key, value in parent.config["limits"].items()
                                            if key != "concurrency"}}
        tasks = {}
        for path, text in [("alpha.py", "return alpha_store[request.alpha]"),
                           ("beta.py", "return beta_store[request.beta]")]:
            context = {"assigned_path": path, "head_sha": SHA, "diff": "", "sources": {"current": {path: text}},
                       "language": "English", "task_contract": "security-task/v2"}
            tasks["analysis:" + path] = {"status": "completed", "tokens": 12,
                "input_hash": digest(json.dumps([config, context], sort_keys=True)),
                "result": {"findings": [], "required_paths": []}}
        parent.status, parent.progress = "partial", {"tokens": 24, "tasks": tasks}
        child = service.create_scan(db, seed["repo"], seed["user"], SecurityScanInput(
            connection_id="connection", request_key=uid(), concurrency=2, token_budget=200_000), parent=parent)
        assert "task_contract" not in child.config
        job = db.scalar(select(Job).where(Job.payload["scan_id"].as_string() == child.id))
        job.status, job.attempts, job.lease_until = "running", 1, now() + timedelta(minutes=10)
        scan_id, claim = child.id, f"{job.id}:1"
    monkeypatch.setattr(engine, "ask", lambda *args, **kwargs: pytest.fail("Legacy checkpoint was not reused"))
    service.perform(scan_id, claim)
    with transaction() as db:
        scan = db.get(SecurityScan, scan_id)
        assert scan.status == "completed", scan.error
        assert scan.progress["tokens"] == 0
        assert all(task["reused"] for task in scan.progress["tasks"].values())
        assert all("source_packing" not in task for task in scan.progress["tasks"].values())
    assert not state["calls"]


@pytest.mark.parametrize("fault", ["unknown", "cancel", "revoke"])
def test_parallel_security_halts_after_unknown_usage_cancel_or_revocation(delayed_security_provider, fault):
    from code_mortis.models import User

    state = delayed_security_provider
    state["fault"] = fault

    def revoke():
        with transaction() as db:
            if fault == "cancel":
                db.get(SecurityScan, state["scan_id"]).cancel_requested = True
            else:
                db.get(User, 7).enabled = False

    state["after_response"] = revoke if fault != "unknown" else None
    service.perform(state["scan_id"], state["claim"])
    with transaction() as db:
        scan = db.get(SecurityScan, state["scan_id"])
        assert scan.status != "completed"
        assert not list(db.scalars(select(SecurityFinding)))
        if fault == "unknown":
            assert scan.progress["tokens"] is None
            assert all(row.usage_state == "unknown" for row in db.scalars(select(AnalysisAttempt)))
    assert len(state["calls"]) == 2
