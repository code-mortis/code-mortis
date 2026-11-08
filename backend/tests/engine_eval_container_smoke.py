"""Offline adapter smoke: real pinned OCR through the pilot recording shim.

Run in the evaluation image with --network=none and writable disposable tmpfs.
This explicitly bypasses database admission at ocr._configuration; it is not
evidence of a durable workflow, a live provider, or pilot quality.
"""

import json
import os
import platform
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["CM_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["CM_BOOTSTRAP_TOKEN"] = "offline-engine-adapter-smoke"
os.environ["CM_OWNER_GITHUB_LOGIN"] = "synthetic-owner"
os.environ["CM_OCR_ISOLATED_RUNTIME"] = "true"
os.environ["CM_TRUSTED_ENDPOINT_HOSTS"] = "127.0.0.1"

from capture import capture

from code_mortis.integrations import ocr
from code_mortis.reviews import verification
from code_mortis.schemas.security import SecurityAnalysis
from code_mortis.security_scans.engine import model_schema

KEY = "synthetic-adapter-key"
MODEL = "muse-spark-1.3-contributor"
RESULT = {"findings": [], "required_paths": []}


def main():
    observed = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/fixed/v1/responses"
            assert data["model"] == MODEL
            assert data["reasoning"]["effort"] == server.effort
            assert self.headers["User-Agent"] == "code-mortis/0.1.0"
            session = self.headers["x-opencode-session"]
            assert session and "{ocr_session_key}" not in session
            assert self.headers["Authorization"] == "Bearer " + KEY
            observed.append(
                {
                    "effort": server.effort,
                    "delivery": server.delivery,
                    "session": session,
                    "requested_stream": data.get("stream", False),
                }
            )
            definitions = [tool.get("function", tool) for tool in data["tools"]]
            assert {tool["name"] for tool in definitions} == {"task_done"}
            assert definitions[0]["strict"] is True
            arguments = json.dumps({"state": "DONE", "result": RESULT})
            item = {
                "type": "function_call",
                "id": "fc_smoke",
                "call_id": "call_smoke",
                "name": "task_done",
                "arguments": arguments,
                "status": "completed",
            }
            final = {
                "id": "resp_smoke",
                "object": "response",
                "status": "completed",
                "model": MODEL,
                "output": [item],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
            if server.delivery == "json":
                body = json.dumps(final).encode()
                content_type = "application/json"
            else:
                events = [
                    {
                        "type": "response.created",
                        "response": {**final, "status": "in_progress", "output": [], "usage": None},
                    },
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {**item, "arguments": "", "status": "in_progress"},
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": item["id"],
                        "output_index": 0,
                        "delta": arguments,
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "item_id": item["id"],
                        "output_index": 0,
                        "arguments": arguments,
                    },
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": final},
                ]
                body = "".join(
                    "event: "
                    + event["type"]
                    + "\ndata: "
                    + json.dumps({**event, "sequence_number": index})
                    + "\n\n"
                    for index, event in enumerate(events)
                ).encode()
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-request-id", "offline-ocr-smoke")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    records = []
    try:
        for effort in ("medium", "high"):
            for delivery in ("json", "sse"):
                server.effort, server.delivery = effort, delivery
                selected = {
                    "kind": "api",
                    "protocol": "openai-responses",
                    "model": MODEL,
                    "base_url": f"http://127.0.0.1:{server.server_port}/fixed/v1",
                    "options": {"extra_body": {"reasoning.effort": effort}, "timeout_sec": 30},
                }
                credentials = {
                    "api_key": KEY,
                    "extra_headers": {
                        "User-Agent": "code-mortis/0.1.0",
                        "x-opencode-session": "{ocr_session_key}",
                    },
                }
                with capture(selected, credentials, allow_insecure_loopback=True) as recorder:
                    local = {**selected, "base_url": recorder.base_url}
                    with ocr._configuration(
                        local, credentials, {}, session_per_task=True, structured_result=True
                    ):
                        with tempfile.TemporaryDirectory() as directory:
                            try:
                                value, usage = verification.api_verify(
                                    Path(directory),
                                    ocr.child_environment(),
                                    local,
                                    credentials,
                                    {"diff": "", "sources": {"current": {"app.py": "PRIVATE_SMOKE_SOURCE"}}},
                                    60_000,
                                    30,
                                    lambda: False,
                                    schema=model_schema(SecurityAnalysis),
                                    configuration_managed=True,
                                    structured_result=True,
                                    validator=lambda raw, supplied: SecurityAnalysis.model_validate(
                                        raw, strict=True
                                    ).model_dump(),
                                )
                            except ValueError as exc:
                                assert delivery == "sse" and "content-type 'text/event-stream'" in str(exc), (
                                    str(exc)
                                )
                                adapter_status = "ocr_nonstream_sse_unsupported"
                            else:
                                assert delivery == "json", (
                                    "Unexpected change in pinned OCR's nonstream contract"
                                )
                                assert value == RESULT and usage == {"tokens": 15}, (value, usage)
                                adapter_status = "validated"
                    assert recorder.blocked_reason is None
                    assert len(recorder.events) == 1 and recorder.events[0]["total_tokens"] == 15
                    assert recorder.events[0]["state"] == "finished"
                    records.append(
                        {
                            "effort": effort,
                            "delivery": delivery,
                            "adapter_status": adapter_status,
                            "requested_stream": observed[-1]["requested_stream"],
                            "event": recorder.events[0],
                        }
                    )
                assert not (Path.home() / ".opencodereview/config.json").exists()
        encoded = json.dumps(records)
        assert KEY not in encoded and "PRIVATE_SMOKE_SOURCE" not in encoded
        assert "arguments" not in encoded and "findings" not in encoded
        assert len(observed) == 4 and len({value["session"] for value in observed}) == 4
        versions = {
            "python": platform.python_version(),
            "ocr": subprocess.check_output(["ocr", "--version"], text=True).strip(),
            "git": subprocess.check_output(["git", "--version"], text=True).strip(),
            "trivy": subprocess.check_output(["trivy", "--version"], text=True).strip(),
        }
        print(
            json.dumps(
                {
                    "scope": "offline_adapter_only",
                    "cases": records,
                    "versions": versions,
                    "provider_calls": len(observed),
                    "reported_tokens": 60,
                    "external_calls": 0,
                },
                indent=2,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
