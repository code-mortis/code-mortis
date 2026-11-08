"""SIGKILL real prefork children in full workflows; synthetic external evidence only."""

import copy
import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select
from test_engine_evaluation import ROOT, evaluation, prepare, runtime  # noqa: F401
from test_engine_evaluation_parallel import parallel, queued_database  # noqa: F401
from test_llm_http import result as wire_result

from code_mortis.core import inference_admission
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction
from code_mortis.models import AnalysisAttempt, AnalysisBudget, AnalysisTask, InferenceAdmission, Job
from code_mortis.schemas.common import RepoSettings


def fault_worker(bootstrap_file, phase, marker_file):
    """Only external scanner output and the exact interruption boundary are adapted."""
    import asyncio
    import json
    import os
    import time
    from contextlib import ExitStack
    from pathlib import Path
    from unittest.mock import patch

    import runtime

    from code_mortis.core import analysis
    from code_mortis.integrations import llm
    from code_mortis.security_scans import engine as security_engine

    bootstrap = json.loads(Path(bootstrap_file).read_text())
    marker = Path(marker_file)

    def reached(task_id=None):
        if marker.exists():
            return False
        with marker.open("x") as output:
            json.dump({"pid": os.getpid(), "phase": phase, "task_id": task_id}, output)
        return True

    send, finish = llm.send, analysis.finish_task

    async def paused_send(connection, credentials, request, **kwargs):
        result = await send(connection, credentials, request, **kwargs)
        properties = request.output_schema.get("properties", {})
        if phase == "after_response" and ("completed_units" in properties or "findings" in properties) and reached():
            while True:
                await asyncio.sleep(.05)
        return result

    def paused_finish(lease, **kwargs):
        accepted = finish(lease, **kwargs)
        if phase == "after_ledger" and accepted and kwargs.get("result") is not None and reached(lease.task_id):
            while True:
                time.sleep(.05)
        return accepted

    def synthetic_scanner(*args, **kwargs):
        return {"metadata": {"database_fresh": True, "checks_present": True,
                             "scanners": ["vuln", "misconfig", "secret"], "synthetic_scanner": True},
                "findings": [], "problems": [], "targets": {}}

    with ExitStack() as stack:
        stack.enter_context(patch.object(llm, "send", paused_send))
        stack.enter_context(patch.object(analysis, "finish_task", paused_finish))
        stack.enter_context(patch.object(security_engine, "trivy", synthetic_scanner))
        runtime.queue_worker(bootstrap)


@pytest.mark.skipif(sys.platform != "linux", reason="Physical Celery prefork fault checks require Linux fork semantics")
@pytest.mark.parametrize("workload", ["pr", "security"])
@pytest.mark.parametrize("phase", ["after_response", "after_ledger"])
def test_prefork_workflow_loss_preserves_consumption_without_repeating_analysis(
    evaluation, queued_database, monkeypatch, tmp_path, workload, phase,  # noqa: F811
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/engine_eval"))
    manifest, case, export = evaluation
    manifest = copy.deepcopy(manifest)
    if workload == "security":
        case = prepare.prepare_case(prepare.PILOT / "sec-01", tmp_path / "security-export")
        export = tmp_path / "security-export/sec-01"
        manifest["security_limits"] = {"concurrency": 1, "token_budget": 200_000, "timeout_seconds": 90}
        import scanner_preflight

        cache = tmp_path / "scanner-cache"
        (cache / "db").mkdir(parents=True)
        (cache / "policy/content").mkdir(parents=True)
        (cache / "db/metadata.json").write_text(json.dumps({"Version": 2, "UpdatedAt": now().isoformat()}))
        (cache / "db/trivy.db").write_text("Synthetic advisory fixture; not a Trivy database")
        (cache / "policy/metadata.json").write_text(json.dumps({"MajorVersion": 2, "DownloadedAt": now().isoformat()}))
        (cache / "policy/content/check.rego").write_text("package synthetic\n")
        manifest["scanner_snapshot"] = scanner_preflight.snapshot_identity(cache)
        monkeypatch.setattr(settings(), "trivy_cache_dir", cache)
    parallel(manifest)
    options = RepoSettings(analysis_engine="mortis", concurrency=1, token_budget=200_000,
                           assessment_token_budget=200_000, verification_token_budget=200_000,
                           summary_token_budget=60_000, timeout_seconds=90).model_dump()
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            properties = body["text"]["format"]["schema"]["properties"]
            if "completed_units" in properties:
                supplied = json.loads(next(row["content"] for row in body["input"] if row.get("role") == "user"))
                task = "analysis"
                value = {"task_key": supplied["task_key"], "completed_units": [row["key"] for row in supplied["assigned_units"]], "findings": []}
            elif "findings" in properties:
                task, value = "analysis", {"findings": [], "required_paths": []}
            else:
                assert "narrative" in properties
                task, value = "summary", {"narrative": "Synthetic change.", "groups": [
                    {"title": "Billing", "description": "Change aggregation.", "paths": ["billing.py"]}]}
            requests.append(task)
            encoded = json.dumps(wire_result("openai-responses", content=json.dumps(value))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    manifest["connection"]["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    identity = "fault-" + workload + "-" + phase
    marker, launcher = tmp_path / "boundary.json", tmp_path / "fault_worker.py"
    launcher.write_text("import sys\nsys.path.insert(0, " + repr(str(ROOT / "scripts/engine_eval")) + ")\n"
                        + inspect.getsource(fault_worker) + "\nfault_worker(sys.argv[1], sys.argv[2], sys.argv[3])\n")
    popen, workers, evidence, controller_errors = subprocess.Popen, [], {}, []

    def launch(args, *positional, **kwargs):
        if len(args) > 2 and args[1] == str(Path(runtime.__file__).resolve()) and args[2] == "--queue-worker":
            args = [args[0], str(launcher), args[3], phase, str(marker)]
            process = popen(args, *positional, **kwargs)
            workers.append(process)
            return process
        return popen(args, *positional, **kwargs)

    def kill_at_boundary():
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    reached = json.loads(marker.read_text())
                    break
                except (FileNotFoundError, json.JSONDecodeError):
                    assert time.monotonic() < deadline, "Worker did not reach the requested real inference boundary"
                    time.sleep(.02)
            pid = reached["pid"]
            assert workers and pid != workers[0].pid
            parent = next(row for row in Path(f"/proc/{pid}/status").read_text().splitlines() if row.startswith("PPid:"))
            assert int(parent.split()[1]) == workers[0].pid, "Fault PID must be a real prefork child"
            with transaction() as db:
                tasks = list(db.scalars(select(AnalysisTask)))
                attempts = list(db.scalars(select(AnalysisAttempt)))
                assert len(attempts) == 1 and len(tasks) == 1
                task, attempt = tasks[0], attempts[0]
                evidence.update(boundary=reached, task_before=runtime.row_data(task), attempt_before=runtime.row_data(attempt))
                assert task.status == ("completed" if phase == "after_ledger" else "running")
                assert attempt.usage_state == ("known" if phase == "after_ledger" else "reserved")
                tickets = list(db.scalars(select(InferenceAdmission).where(InferenceAdmission.task_id == task.id)))
                assert len(tickets) == 1
                assert tickets[0].status == ("released" if phase == "after_ledger" else "active")
                admission_id = tickets[0].id
            os.kill(pid, signal.SIGKILL)
            while Path(f"/proc/{pid}").exists():
                assert time.monotonic() < deadline, "Killed child was not reaped"
                time.sleep(.02)
            evidence["confirmed_sigkill"] = True
            if phase == "after_response":
                # Physical termination is established; release capacity only,
                # retaining the uncertain token charge and inference attempt.
                assert inference_admission.release_connection(admission_id)
                evidence["capacity_release_after_confirmed_death"] = True
            with transaction() as db:
                db.get(Job, identity).lease_until = now() - timedelta(seconds=1)
        except BaseException as exc:
            controller_errors.append(exc)
            if workers and workers[0].poll() is None:
                os.killpg(workers[0].pid, signal.SIGTERM)

    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    monkeypatch.syspath_prepend(str(ROOT / "scripts/engine_eval"))
    controller = threading.Thread(target=kill_at_boundary, daemon=True)
    controller.start()
    try:
        result = runtime.execute(manifest, case, export, "mortis", {"api_key": "synthetic", "extra_headers": {}}, options, identity)
    finally:
        controller.join(timeout=35)
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
    evidence.update(result=result, provider_requests=requests, controller_errors=[str(error) for error in controller_errors])
    if destination := os.environ.get("CM_WORKFLOW_FAULT_EVIDENCE_DIR"):
        with (Path(destination) / (identity + ".json")).open("x") as output:
            json.dump(evidence, output, sort_keys=True, indent=2, default=lambda value: value.isoformat())
    assert not controller_errors, controller_errors
    assert evidence["confirmed_sigkill"]
    assert requests.count("analysis") == 1, evidence
    with transaction() as db:
        attempt = db.get(AnalysisAttempt, evidence["attempt_before"]["id"])
        task = db.get(AnalysisTask, attempt.task_id)
        budget = db.get(AnalysisBudget, (task.plan_id, task.stage))
        assert len(list(db.scalars(select(AnalysisAttempt).where(AnalysisAttempt.task_id == task.id)))) == 1
        if phase == "after_ledger":
            assert task.result == evidence["task_before"]["result"] and task.status == "completed"
            assert attempt.usage_state == "known" and attempt.usage_tokens == 12
            assert budget.charged_tokens >= 12
            if workload == "pr":
                assert result["status"] == "completed" and result["summary"]["status"] == "completed"
                assert result["coverage"]["validated_units"] == result["coverage"]["assigned_units"]
        else:
            assert task.result is None and task.status in {"running", "uncertain"}
            assert attempt.usage_tokens is None and attempt.usage_state in {"reserved", "unknown"}
            assert budget.charged_tokens >= attempt.reserved_tokens > 0
            assert result["status"] != "completed"
        if workload == "security":
            assert result["status"] == "partial" and "Security worker stopped" in result["error"]
        assert not result["findings"] and not result["occurrences"]
        assert all(row["kind"] not in {"publish", "resolve", "security_issue_publish"} for row in result.get("queue_measurement", {}).get("events", []))
        tickets = list(db.scalars(select(InferenceAdmission).where(InferenceAdmission.task_id == task.id)))
        assert len(tickets) == 1 and tickets[0].status == "released"
