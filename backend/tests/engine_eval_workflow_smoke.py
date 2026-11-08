"""Pinned-image offline workflow smoke with a real OCR binary and local provider.

Use a fresh, migrated mortis_eval database reachable only on an internal Docker
network. No pilot inputs, real credentials, GitHub writes or external traffic.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from runtime import PARALLEL_PROFILE, QUEUE_PROFILE, captured_execute, fingerprint, validate_environment

from code_mortis.reviews.context import source_id
from code_mortis.schemas.common import RepoSettings

MODEL = "synthetic-workflow-model"
KEY = "synthetic-workflow-key"


def git(root, *args):
    environment = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Synthetic",
        "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic",
        "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
    }
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
        env=environment,
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()


def main():
    security = "--security" in sys.argv
    parallel = "--parallel" in sys.argv
    calibration = "--calibrate-baseline" in sys.argv
    paired = "--controlled-pairs" in sys.argv
    assert not (calibration and paired), "Calibration and paired diagnostics use separate frozen runs"
    assert not (calibration or paired) or parallel, "Controlled measurements require the production queue profile"
    validate_environment()
    command.upgrade(Config("alembic.ini"), "head")
    observed = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/v1/responses" and data["model"] == MODEL
            assert data["reasoning"] == {"effort": "high" if security else "medium"}
            assert self.headers["Authorization"] == "Bearer " + KEY
            if server.rate_limited:
                observed.append({"phase": "known_zero_429", "stream": data.get("stream", False)})
                body = b'{"error":{"type":"rate_limit_error","code":"rate_limit","message":"Synthetic known-zero rejection"}}'
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(body)
                return
            definitions = {
                tool.get("function", tool)["name"]: tool.get("function", tool)
                for tool in data.get("tools", [])
            }
            completed = definitions.get("task_done", {}).get("parameters", {}).get("properties", {})
            schema = completed.get("result", {}).get("properties", {})
            direct = data.get("text", {}).get("format", {}).get("schema", {}).get("properties", {})
            phase = "review"
            if security and ("findings" in schema or "findings" in direct):
                phase = "security_analysis"
                value = {"findings": [], "required_paths": []}
            elif "narrative" in schema or "narrative" in direct:
                phase = "summary"
                value = {
                    "narrative": "The application limit changes.",
                    "groups": [
                        {"title": "Application", "description": "Change the limit.", "paths": ["app.py"]}
                    ],
                }
            elif "relationship" in schema or "relationship" in direct:
                phase = "assessment"
                value = {
                    "verdict": "confirmed",
                    "relationship": "new",
                    "finding_id": None,
                    "explanation": "Returning zero prevents allocation.",
                    "cause": "The limit is zero.",
                    "trigger": "A caller allocates an item.",
                    "consequence": "No item can be allocated.",
                    "history_explanation": "",
                    "contradicts_prior": False,
                    "changed_conditions": "",
                    "suggestion_supported": False,
                    "required_paths": [],
                    "evidence": [
                        {"source_id": source_id("current", "app.py"), "start_line": 2, "end_line": 2}
                    ],
                }
            elif "completed_units" in direct:
                phase = "analysis"
                supplied = next(
                    json.loads(message["content"])
                    for message in data["input"]
                    if message.get("role") == "user"
                )
                unit = supplied["assigned_units"][0]
                value = {
                    "task_key": supplied["task_key"],
                    "completed_units": [row["key"] for row in supplied["assigned_units"]],
                    "findings": [
                        {
                            "unit_key": unit["key"],
                            "path": "app.py",
                            "start_line": 2,
                            "end_line": 2,
                            "severity": "medium",
                            "category": "correctness",
                            "content": "The limit returns zero.",
                            "existing_code": "    return 0",
                            "suggestion_code": "    return 1",
                            "trigger": "A caller requests one permitted item.",
                            "consequence": "No item can be allocated.",
                            "evidence": [
                                {
                                    "source_id": next(
                                        row["source_id"]
                                        for row in supplied["citation_sources"]
                                        if row["path"] == "app.py"
                                    ),
                                    "start_line": 2,
                                    "end_line": 2,
                                }
                            ],
                        }
                    ],
                }
            else:
                value = None
            output = []
            if value is not None and direct:
                output = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(value)}],
                    }
                ]
            elif value is not None:
                output = [
                    {
                        "type": "function_call",
                        "id": "fc_result",
                        "call_id": "call_result",
                        "name": "task_done",
                        "arguments": json.dumps({"state": "DONE", "result": value}),
                        "status": "completed",
                    }
                ]
            elif "code_comment" in definitions:
                output = [
                    {
                        "type": "function_call",
                        "id": "fc_finding",
                        "call_id": "call_finding",
                        "name": "code_comment",
                        "arguments": json.dumps(
                            {
                                "comments": [
                                    {
                                        "path": "app.py",
                                        "severity": "medium",
                                        "category": "correctness",
                                        "content": "The limit returns zero.",
                                        "existing_code": "    return 0",
                                        "suggestion_code": "    return 1",
                                    }
                                ]
                            }
                        ),
                        "status": "completed",
                    },
                    {
                        "type": "function_call",
                        "id": "fc_done",
                        "call_id": "call_done",
                        "name": "task_done",
                        "arguments": json.dumps({"state": "DONE"}),
                        "status": "completed",
                    },
                ]
            elif "task_done" in definitions:
                output = [
                    {
                        "type": "function_call",
                        "id": "fc_done",
                        "call_id": "call_done",
                        "name": "task_done",
                        "arguments": json.dumps({"state": "DONE"}),
                        "status": "completed",
                    }
                ]
            else:
                output = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '[{"label":"Application","files":["app.py"]}]'}
                        ],
                    }
                ]
            observed.append(
                {"phase": phase, "tools": sorted(definitions), "stream": data.get("stream", False)}
            )
            body = json.dumps(
                {
                    "id": "resp_workflow",
                    "object": "response",
                    "status": "completed",
                    "model": MODEL,
                    "output": output,
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.rate_limited = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    records = []
    try:
        with tempfile.TemporaryDirectory(prefix="workflow-source-") as temporary:
            folder = Path(temporary) / ("sec-00" if security else "pr-00")
            root = folder / "repo"
            root.mkdir(parents=True)
            git(root, "init", "--quiet", "--template=")
            (root / "app.py").write_text("def limit():\n    return 1\n")
            git(root, "add", ".")
            git(root, "commit", "--quiet", "-m", "base")
            base = git(root, "rev-parse", "HEAD")
            (root / "app.py").write_text("def limit():\n    return 0\n")
            git(root, "add", ".")
            git(root, "commit", "--quiet", "-m", "proposed change")
            head = git(root, "rev-parse", "HEAD")
            case = {
                "case_id": folder.name,
                "workload": "security" if security else "pr",
                "base": {"commit": base},
                "head": {"commit": head},
                "merge_base_sha": base,
                "selected_paths": ["app.py"],
                "expected_assigned_units": 1,
                "identity": {
                    "model_options_sha256": fingerprint(
                        {
                            "prompt_token_ceiling": 500000,
                            "timeout_sec": 60,
                            "extra_body": {"reasoning.effort": "high" if security else "medium"},
                        }
                    )
                },
            }
            manifest = {
                "run_id": "offline-workflow-smoke",
                "connection": {
                    "id": "offline-workflow",
                    "name": "Synthetic",
                    "provider": "custom",
                    "protocol": "openai-responses",
                    "model": MODEL,
                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                    "options": {"prompt_token_ceiling": 500000, "timeout_sec": 60},
                    "kind": "api",
                    "owner_id": None,
                },
                "provider_reasoning_by_workload": {"pr": "medium", "security": "high"},
                "case_deadline_seconds": 1800,
            }
            if parallel:
                manifest.update(execution_profile=PARALLEL_PROFILE, queue=dict(QUEUE_PROFILE))
            if security:
                manifest["scanner_snapshot"] = json.loads(Path("/snapshot.json").read_text())
                manifest["security_limits"] = {
                    "timeout_seconds": 1800,
                    "token_budget": None,
                    "concurrency": 4,
                }
            schedule = [(1, "ocr"), (1, "mortis")]
            if calibration:
                schedule = [(repetition, "ocr") for repetition in range(1, 6)]
            elif paired:
                schedule = [(repetition, arm) for repetition in range(1, 6)
                            for arm in (("ocr", "mortis") if repetition % 2 else ("mortis", "ocr"))]
            for repetition, arm in schedule:
                start = len(observed)
                options = RepoSettings(
                    analysis_engine=arm,
                    token_budget=None,
                    timeout_seconds=1800,
                    summary_timeout_seconds=1800,
                    summary_token_budget=None,
                ).model_dump()
                result = captured_execute(
                    manifest,
                    case,
                    folder,
                    arm,
                    {"api_key": KEY, "extra_headers": {}},
                    options,
                    str(uuid4()),
                    fake_provider=True,
                )
                assert result["status"] == "completed", result
                if security:
                    assert result["scanner_guard"] == {"validated": True, "error": None}, result
                    assert result["stages"]["trivy"]["status"] == "completed", result
                    assert (
                        result["report"]["coverage"]["trivy"]["evaluation_snapshot"]["before"]
                        == manifest["scanner_snapshot"]["sha256"]
                    ), result
                else:
                    assert result["summary"]["status"] == "completed", result
                assert result["coverage"] == {"assigned_units": 1, "validated_units": 1}, result
                assert len(result["findings"]) == (0 if security else 1), result
                phases = [row["phase"] for row in observed[start:]]
                assert (
                    "security_analysis" in phases
                    if security
                    else "assessment" in phases and "summary" in phases
                ), phases
                assert len(result["provider_events"]) == len(observed) - start
                assert result["usage"]["tokens"] == 15 * len(result["provider_events"]), result
                assert all(row["state"] == "finished" for row in result["job"]["payload"]["provider_events"])
                assert (
                    result["stop_pilot"] is False and result["github_adapter"]["publication"] == "forbidden"
                )
                records.append(
                    {
                        "arm": arm,
                        "repetition": repetition,
                        "status": result["status"],
                        "coverage": result["coverage"],
                        "findings": len(result["findings"]),
                        "requests": result["usage"]["requests"],
                        "tokens": result["usage"]["tokens"],
                        "phases": phases,
                        "timing_ms": result["timing_ms"],
                        "queue_measurement": result.get("queue_measurement"),
                        "provider_mode": result["transport"].get("provider_mode", "controlled_local_synthetic"),
                    }
                )
            server.rate_limited = not security
            retry_records = []
            for arm in () if security or calibration or paired else ("ocr", "mortis"):
                before = len(observed)
                options = RepoSettings(
                    analysis_engine=arm,
                    token_budget=None,
                    timeout_seconds=1800,
                    summary_timeout_seconds=1800,
                    summary_token_budget=None,
                ).model_dump()
                failed = captured_execute(
                    manifest,
                    case,
                    folder,
                    arm,
                    {"api_key": KEY, "extra_headers": {}},
                    options,
                    str(uuid4()),
                    fake_provider=True,
                )
                events = failed["provider_events"]
                assert events and all(
                    row["status"] == 429 and row["total_tokens"] == 0 and not row["consumption_unknown"]
                    for row in events
                ), events
                assert len(events) == len(observed) - before and failed["usage"]["tokens"] == 0
                assert failed["status"] != "completed"
                retry_records.append(
                    {
                        "arm": arm,
                        "http_attempts": len(events),
                        "known_zero_retries": len(events) - 1,
                        "tokens": failed["usage"]["tokens"],
                        "status": failed["status"],
                    }
                )
        print(
            json.dumps(
                {
                    "scope": "offline_full_security_workflow" if security else
                    "offline_full_parallel_workflow" if parallel else "offline_full_sequential_workflow",
                    "execution_profile": PARALLEL_PROFILE if parallel else "retained_sequential_v1",
                    "performance_acceptance": False,
                    "baseline_calibration": calibration,
                    "controlled_pairs": paired,
                    "configured_provider_delay_ms": 0,
                    "limitations": "One visible synthetic workflow; host quiescence must be established separately. No heldout quality, graph, historical verification or acceptance claim.",
                    "external_calls": 0,
                    "arms": records,
                    "known_zero_429_probe": retry_records,
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
