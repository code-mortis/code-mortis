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

from code_mortis import background, ocr, verification

calls = []
verification_mode = "fixed"


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
        elif "Code Mortis project background evidence" in text and "code_comment" in names and not any(
            message.get("role") == "assistant" for message in data["messages"]
        ):
            assert names == {"code_comment", "task_done"}, names
            assert "The required output is a project overview" in text, "OCR must load the generation rule"
            content = [{"type": "tool_use", "id": "background", "name": "code_comment", "input": {"comments": [{
                "path": "evidence.py", "content": json.dumps({"background": "A Python service with a worker."}),
                "category": "other", "severity": "low",
                "existing_code": "# Code Mortis project background evidence", "suggestion_code": "",
            }]}}]
        elif "Code Mortis verification evidence" in text and "code_comment" in names and not any(
            message.get("role") == "assistant" for message in data["messages"]
        ):
            assert names == {"code_comment", "task_done"}, names
            assert "internal result transport" in text
            assert "including non-confirming outcomes" in text, "OCR must load the verification rule"
            if verification_mode == "provider_error":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"type": "error", "error": {
                    "type": "authentication_error", "message": "Smoke provider rejected fake-test-key",
                }}).encode())
                return
            value = {
                "verdict": "fixed", "original_issue_present": True,
                "explanation": "The supplied retry loop is now bounded.",
                "change_explanation": "The infinite loop was replaced with three attempts.",
                "dismissal_recommended": False,
                "evidence": [
                    {"revision": "original", "path": "main.py", "start_line": 1, "end_line": 1, "quote": "while True: retry()"},
                    {"revision": "current", "path": "main.py", "start_line": 1, "end_line": 1, "quote": "for attempt in range(3): retry()"},
                ],
            }
            content = [{"type": "tool_use", "id": "verify", "name": "code_comment", "input": {"comments": [{
                "path": "evidence.py", "content": json.dumps(value), "category": "other", "severity": "low",
                "existing_code": "# Code Mortis verification evidence", "suggestion_code": "",
            }]}}]
            if verification_mode == "no_verdict":
                content = [{"type": "tool_use", "id": "done", "name": "task_done", "input": {"state": "DONE"}}]
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
    global verification_mode
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
            evidence = {"repository": "team/project", "sources": {
                f"src/service_{index}.py": {"text": (
                    "def deliver(payload):\n    return queue.enqueue(payload)\n" * 100
                )[:3500], "truncated": True} for index in range(16)
            }}
            prompt = background.INSTRUCTIONS + "\n\n" + json.dumps(evidence)
            assert 50_000 < len(prompt.encode()) < background.CONTEXT_BYTES
            draft = background.api_generate(Path(tmp), connection, {"api_key": "fake-test-key"},
                                            prompt, 60, lambda: False, 200000)
            assert background.validate_result(draft) == "A Python service with a worker."
            assert len(calls) > 2, "Realistic repository context must reach the mock provider."
        print("Pinned OCR generated project background through a restricted result envelope (mock provider).")
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
                        "--max-tokens-budget",
                        "0",
                        "--timeout",
                        "0",
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
                git("checkout", "--detach", head)
                for index in range(40):
                    (root / f"source{index}.py").write_text("value = 1\n")
                git("add", ".")
                git("commit", "-m", "oversized review")
                large_head = git("rev-parse", "HEAD")
                code, preview, _ = ocr.command([
                    "ocr", "review", "--repo", str(root), "--from", base, "--to", large_head,
                    "--format", "json", "--preview",
                ], root, env)
                preview = json.loads(preview)
                assert code == 0
                paths = ocr.preview_paths([item for item in preview["files"] if item["will_review"]], preview["reviewable_count"])
                assert len(paths) == 41 and ocr.file_limit_result(paths, 40)[0] == "skipped"
                before = len(calls)
                result = delegated.review(root, env, base, large_head, base, native,
                                          RepoSettings().model_dump(), "large", lambda: False, events.append, {})
                assert result[0] == "skipped" and len(calls) == before
                assert result[1]["message"] == "41 reviewable files exceeds the maximum of 40."
                git("checkout", "--detach", base)
            finally:
                runner_client.review = original
            options = RepoSettings().model_dump()
            for sources in ({"main.py": "count = 1\n"}, {"nested/main.py": "count = 2\n"}):
                assert verification.filter_context(root, env, sources, options, lambda: False) == set(sources)
            options["exclusions"] = "**/private.py"
            assert not verification.filter_context(root, env, {"nested/private.py": "secret = 1"}, options, lambda: False)
            # Reproduce the real PR: an excluded test changes alongside a fix in source.
            options["exclusions"] = ""
            (root / "main.py").write_text("while True: retry()\n")
            git("add", ".")
            git("commit", "-m", "original finding")
            original_sha = git("rev-parse", "HEAD")
            (root / "main.py").write_text("for attempt in range(3): retry()\n")
            (root / "tests").mkdir()
            (root / "tests/test_retry.py").write_text("assert True # excluded fixture\n")
            git("add", ".")
            git("commit", "-m", "fix with regression test")
            actual_context = verification.build_context(root, env, env, {"path": "main.py"}, original_sha,
                                                        git("rev-parse", "HEAD"), options, lambda: False)
            assert actual_context["excluded_paths"] == ["tests/test_retry.py"]
            assert "excluded fixture" not in json.dumps(actual_context)
            context = actual_context
            scan_output = []
            original_command = ocr.command
            def capture(*args, **kwargs):
                result = original_command(*args, **kwargs)
                if "scan" in args[0]:
                    scan_output.append(result)
                return result
            ocr.command = capture
            try:
                raw, usage = verification.api_verify(root, env, connection, {"api_key": "fake-test-key"}, context, None, None, lambda: False)
            except ValueError as exc:
                raise AssertionError(str(scan_output)) from exc
            finally:
                ocr.command = original_command
            assert verification.validate_result(raw, context)["verdict"] == "fixed"
            assert usage["tokens"] > 0
            for verification_mode in ("no_verdict", "provider_error"):
                try:
                    verification.api_verify(root, env, connection, {"api_key": "fake-test-key"}, context,
                                            options["token_budget"], 60, lambda: False)
                except ValueError as exc:
                    detail = str(exc)
                    assert "fake-test-key" not in detail, detail
                    if verification_mode == "no_verdict":
                        assert "did not complete explicit finding verification" in detail, detail
                        assert exc.usage["tokens"] > 0
                    else:
                        assert '"exit_code": 1' in detail and "Smoke provider rejected" in detail, detail
                        assert "[redacted]" in detail and "token consumption is unknown" in detail, detail
                        assert not hasattr(exc, "usage")
                else:
                    raise AssertionError(f"{verification_mode} was accepted as a verification")
        assert not (Path.home() / ".opencodereview/config.json").exists()
        assert not (Path.home() / ".opencodereview/sessions").exists()
        print(
            "Real OCR connection round trip, trusted-base diff review, coverage parsing, real delegation scope/rules, checkpoint recovery, explicit fix verification, failure diagnostics, exclusion filtering, and cleanup passed."
        )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
