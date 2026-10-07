"""Run in the isolated image: python tests/ocr_container_smoke.py.

Uses the real pinned OCR binary with a deterministic local Anthropic endpoint.
No model account, GitHub credentials, or external traffic are used.
"""

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["CM_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["CM_BOOTSTRAP_TOKEN"] = "container-smoke-test-token"
os.environ["CM_OWNER_GITHUB_LOGIN"] = "test-owner"
os.environ["CM_OCR_ISOLATED_RUNTIME"] = "true"
os.environ["CM_ALLOW_PRIVATE_ENDPOINTS"] = "true"

from code_mortis import ocr

calls = []


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append(data)
        names = {tool["name"] for tool in data.get("tools", [])}
        text = json.dumps(data)
        if "ocr_selftest" in names and not any(
            message.get("role") == "assistant" for message in data["messages"]
        ):
            content = [
                {"type": "tool_use", "id": "selftest", "name": "ocr_selftest", "input": {"note": "test"}}
            ]
        elif "task_done" in names:
            content = [{"type": "tool_use", "id": "done", "name": "task_done", "input": {"state": "DONE"}}]
        elif "file grouping assistant" in text:
            content = [{"type": "text", "text": '[{"label":"main","files":["main.py"]}]'}]
        else:
            content = [{"type": "text", "text": "The test completed."}]
        result = {
            "id": "message",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": content,
            "stop_reason": "tool_use" if content[0]["type"] == "tool_use" else "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = {
        "protocol": "anthropic",
        "model": "test-model",
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "options": {},
    }
    try:
        assert "tool-call round trip" in ocr.test_connection(connection, {"api_key": "fake-test-key"})
        assert len(calls) == 2
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = ocr.child_environment()

            def git(*args):
                return (
                    subprocess.check_output(
                        ["git", "-c", "user.name=Smoke", "-c", "user.email=smoke@example.test", *args],
                        cwd=root,
                        env=env,
                        stderr=subprocess.DEVNULL,
                    )
                    .decode()
                    .strip()
                )

            git("init")
            (root / "main.py").write_text("count = 1\n")
            git("add", ".")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            (root / "main.py").write_text("count = 2\n")
            git("add", ".")
            git("commit", "-m", "head")
            head = git("rev-parse", "HEAD")
            git("checkout", "--detach", base)
            with ocr.configuration(connection, {"api_key": "fake-test-key"}, {}):
                code, output, errors = ocr.command(
                    [
                        "ocr",
                        "review",
                        "--repo",
                        str(root),
                        "--from",
                        base,
                        "--to",
                        head,
                        "--format",
                        "json",
                        "--audience",
                        "agent",
                        "--effort",
                        "low",
                    ],
                    root,
                    env,
                    timeout=60,
                )
            assert code == 0, errors[-2000:]
            status, report, findings = ocr.parse_report(output, code)
            assert status == "completed", report
            assert len(report["manifest"]["coverage"]["selected"]) == 1
            assert len(report["manifest"]["coverage"]["completed"]) == 1
            assert findings == []
            from code_mortis import delegated, runner_client
            from code_mortis.schemas import RepoSettings

            original = runner_client.review
            runner_client.review = lambda *args: {"comments": []}
            try:
                events = []
                native = {"id": "connection", "kind": "chatgpt", "model": "test-model"}
                state, delegated_report, comments = delegated.review(
                    root,
                    env,
                    base,
                    head,
                    base,
                    native,
                    RepoSettings().model_dump(),
                    "run",
                    lambda: False,
                    events.append,
                    {},
                )
                assert state == "completed" and comments == []
                assert len(delegated_report["manifest"]["coverage"]["selected"]) == 1
                assert events[-1]["completed"] == 1
                checkpoint = events[-1]
                runner_client.review = lambda *args: (_ for _ in ()).throw(
                    AssertionError("Completed files must not be paid for twice")
                )
                assert (
                    delegated.review(
                        root,
                        env,
                        base,
                        head,
                        base,
                        native,
                        RepoSettings().model_dump(),
                        "run",
                        lambda: False,
                        events.append,
                        checkpoint,
                    )[0]
                    == "completed"
                )
            finally:
                runner_client.review = original
        assert not (Path.home() / ".opencodereview/config.json").exists()
        print(
            "Real OCR connection round trip, trusted-base diff review, coverage parsing, real delegation scope/rules, checkpoint recovery, and cleanup passed."
        )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
