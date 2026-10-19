"""Run in the isolated image: python tests/ocr_container_smoke.py.

Uses the real pinned OCR binary with deterministic local Anthropic and Responses endpoints.
No model account, GitHub credentials, or external traffic are used.
"""

import json
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

os.environ["CM_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["CM_BOOTSTRAP_TOKEN"] = "container-smoke-test-token"
os.environ["CM_OWNER_GITHUB_LOGIN"] = "test-owner"
os.environ["CM_OCR_ISOLATED_RUNTIME"] = "true"
os.environ["CM_ALLOW_PRIVATE_ENDPOINTS"] = "true"

from code_mortis import assessment, background, checks, ocr, security_engine, verification
from code_mortis.db import now
from code_mortis.models import ReviewRun

calls = []
verification_mode = "fixed"
assessment_barrier = None
assessment_hold = None
conversation_keys = []


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append(data)
        responses = self.path.endswith("/responses")
        messages = data.get("input", []) if responses else data["messages"]
        names = {tool["name"] for tool in data.get("tools", [])}
        text = json.dumps(data)
        if "ocr_selftest" in names and not any(
            message.get("role") == "assistant" for message in messages
        ):
            content = [
                {"type": "tool_use", "id": "selftest", "name": "ocr_selftest", "input": {"note": "test"}}
            ]
        elif verification_mode == "review_budget" and "task_done" in names:
            continuing = any(message.get("role") == "assistant" for message in messages)
            content = [{"type": "tool_use", "id": "budget", "name": "task_done" if continuing else "file_read_diff",
                        "input": {"state": "DONE"} if continuing else {"path_array": ["main.py"]}}]
        elif "Code Mortis project background evidence" in text and "code_comment" in names and not any(
            message.get("role") == "assistant" for message in messages
        ):
            assert names == {"code_comment", "task_done"}, names
            assert "The required output is a project overview" in text, "OCR must load the generation rule"
            content = [{"type": "tool_use", "id": "background", "name": "code_comment", "input": {"comments": [{
                "path": "evidence.py", "content": json.dumps({"background": "A Python service with a worker."}),
                "category": "other", "severity": "low",
                "existing_code": "# Code Mortis project background evidence", "suggestion_code": "",
            }]}}]
        elif "Code Mortis verification evidence" in text and "task_done" in names and not any(
            message.get("role") == "assistant" for message in messages
        ):
            assert names == {"task_done"}, names
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
                "required_sources": [],
                "evidence": [
                    {"revision": "original", "path": "main.py", "start_line": 1, "end_line": 1, "quote": "while True: retry()"},
                    {"revision": "current", "path": "main.py", "start_line": 1, "end_line": 1, "quote": "for attempt in range(3): retry()"},
                ],
            }
            if verification_mode == "context":
                value.update(verdict="uncertain", required_sources=[{"revision": "current", "path": "helper.py"}])
            elif verification_mode == "bad_citation":
                value["evidence"][1]["quote"] = "invented"
            elif verification_mode == "security_delivery":
                value = {"findings": [], "required_paths": []}
            elif verification_mode == "security_assessment":
                path = next(path for path in ("one.py", "two.py", "three.py", "four.py")
                            if json.dumps(f'current source: "{path}"')[1:-1] in text)
                conversation_keys.append(self.headers.get("X-Smoke-Session"))
                assessment_barrier.wait(timeout=15)
                assert "history_1" in text
                value = {"verdict": "confirmed", "finding_id": "history_1", "explanation": "Confirmed in " + path,
                         "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 1}], "required_paths": []}
            if verification_mode.startswith("assessment_"):
                assert verification.RESULT_DELIVERY in text
                value = {
                    "verdict": "uncertain" if verification_mode == "assessment_context" else verification_mode.removeprefix("assessment_"), "relationship": "new",
                    "finding_id": None, "explanation": "The supplied loop executes three times.",
                    "cause": "Unconditional retries", "trigger": "The first delivery succeeds",
                    "consequence": "Delivery is repeated after success", "history_explanation": "",
                    "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": False,
                    "evidence": [value["evidence"][1]],
                    "required_paths": ["helper.py"] if verification_mode == "assessment_context" else [],
                }
                if verification_mode == "assessment_bad_identity":
                    value.update(verdict="rejected", relationship="duplicate", finding_id="prior-candidate")
                elif verification_mode == "assessment_bad_citation":
                    value["verdict"] = "rejected"
                    value["evidence"][0]["quote"] = "invented"
                if assessment_barrier is not None:
                    path = "one.py" if "one.py" in text else "two.py"
                    conversation_keys.append(self.headers.get("X-Smoke-Session"))
                    assessment_barrier.wait(timeout=15)
                    if assessment_hold is not None:
                        assert assessment_hold.wait(timeout=15)
                    value["evidence"][0]["path"] = path
                    value["explanation"] = "The supplied loop repeats delivery in " + path
            content = [{"type": "tool_use", "id": "verify", "name": "task_done",
                        "input": {"state": "DONE", "result": value}}]
            if verification_mode == "no_verdict":
                content = [{"type": "tool_use", "id": "done", "name": "task_done", "input": {"state": "DONE"}}]
            elif verification_mode == "security_delivery":
                definition = next(tool for tool in data["tools"] if tool["name"] == "task_done")
                parameters = definition["parameters"] if responses else definition["input_schema"]
                assert parameters["required"] == ["state", "result"]
                assert parameters["properties"]["result"]["required"] == ["findings", "required_paths"]
                prompt = "\n".join(message["content"] if isinstance(message.get("content"), str) else
                    "\n".join(part.get("text", "") for part in message.get("content", []) if isinstance(part, dict))
                    for message in messages)
                if '"validation_feedback":' not in prompt:
                    content = [{"type": "tool_use", "id": "done", "name": "task_done", "input": {"state": "DONE"}}]
                else:
                    assert '"kind": "delivery"' in prompt
            elif verification_mode == "multiple_results":
                content.append({**content[0], "id": "extra"})
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
            "usage": {"input_tokens": 1000001 if verification_mode == "review_budget" and
                      content[0].get("name") == "file_read_diff" else 10, "output_tokens": 5},
        }
        if responses:
            result = {"id": "resp_smoke", "object": "response", "status": "completed", "model": "test-model",
                      "output": [{"type": "function_call", "id": "fc_smoke", "call_id": block["id"],
                                  "name": block["name"], "arguments": json.dumps(block["input"])} for block in content],
                      "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except BrokenPipeError:
            assert assessment_hold is not None, "Only cancelled assessments may close the provider connection."


def main():
    global verification_mode, assessment_barrier, assessment_hold
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
            verification_mode = "review_budget"
            with ocr.configuration(connection, {"api_key": "fake-test-key"}, {}):
                code, output, errors = ocr.command([
                    "ocr", "review", "--repo", str(root), "--from", base, "--to", head,
                    "--format", "json", "--audience", "agent", "--effort", "low", "--concurrency", "1",
                    "--max-tokens-budget", "1000000", "--timeout", "0",
                ], root, env, timeout=60)
            verification_mode = "fixed"
            assert code == 0, errors[-2000:]
            status, limited_report, comments = ocr.parse_report(output, code)
            assert status == "partial" and comments == []
            assert len(limited_report["manifest"]["coverage"]["completed"]) == 1
            assert any(w["type"] == "review_limit_reached" for w in limited_report["warnings"])
            run = ReviewRun(id="budget", head_sha=head, status=status, publication="no_new_comments",
                            report=limited_report, progress={}, created_at=now())
            assert checks.check_payload(run, 0)["conclusion"] == "failure"
            print("Pinned OCR grace-round completion retained its budget warning and could not produce a successful GitHub check.")
            from code_mortis import delegated, runner_client
            from code_mortis.schemas import RepoSettings

            original = runner_client.review
            runner_client.review = lambda *args: ({"comments": []}, {"tokens": 10})
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
                                          RepoSettings(max_files=40).model_dump(), "large", lambda: False, events.append, {})
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
                interrupted = Path.home() / ".opencodereview/sessions/interrupted"
                interrupted.mkdir(parents=True, exist_ok=True)
                (interrupted / "old.jsonl").write_text("{")
                raw, usage = verification.api_verify(root, env, connection, {"api_key": "fake-test-key"}, context, None, None, lambda: False)
            except ValueError as exc:
                raise AssertionError(str(scan_output)) from exc
            finally:
                ocr.command = original_command
            assert verification.validate_result(raw, context)["verdict"] == "fixed"
            assert usage["tokens"] > 0
            for verification_mode in ("context", "bad_citation"):
                before = len(calls)
                try:
                    raw, usage = verification.api_verify(root, env, connection, {"api_key": "fake-test-key"},
                                                         context, None, 60, lambda: False)
                except verification.VerificationOutputError as exc:
                    assert verification_mode == "bad_citation" and exc.usage["tokens"] > 0
                    assert exc.feedback["field"] == "evidence[1]" and exc.feedback["path"] == "main.py"
                else:
                    assert verification_mode == "context" and raw["verdict"] == "uncertain"
                    assert raw["required_sources"] == [{"revision": "current", "path": "helper.py"}]
                    assert usage["tokens"] > 0
                assert len(calls) == before + 1
            print("Pinned OCR transported dependency requests and strict citation feedback (mock provider).")
            verification_mode = "security_delivery"
            security_context = {"assigned_path": "main.py", "diff": "", "sources": {"current": {"main.py": "safe"}}}
            item = SimpleNamespace(git=root, env=env, inventory={"main.py": {}}, redact=lambda text: text)
            for protocol in ("anthropic", "openai-responses"):
                before = len(calls)
                raw, usage = security_engine.ask(item, {**connection, "kind": "api", "protocol": protocol}, {"api_key": "fake-test-key"},
                    "analysis", security_context, None, 60, lambda: False, "smoke-delivery")
                assert raw == {"findings": [], "required_paths": []} and usage == {"tokens": 30}
                assert len(calls) == before + 2, "A bare DONE must be rejected and receive exactly one correction."
                print(f"Pinned OCR reproduced bare DONE and recovered a validated security result with counted usage via {protocol} (mock provider).")
            verification_mode = "security_assessment"
            paths = ["one.py", "two.py", "three.py", "four.py"]
            item.inventory.update({path: {} for path in paths})
            for protocol in ("anthropic", "openai-responses"):
                assessment_barrier = threading.Barrier(4)
                conversation_keys.clear()
                before = len(calls)
                selected = {**connection, "kind": "api", "protocol": protocol}
                credentials = {"api_key": "fake-test-key", "extra_headers": {"X-Smoke-Session": "{ocr_session_key}"}}

                def security_assess(path, selected=selected, credentials=credentials):
                    finding_id = "228f3fbd-bb86-4143-a810-4b2622b1cc19"
                    prepared = {"proposal": {"path": path}, "history": [{"id": finding_id, "path": path}], "head_sha": "a" * 40, "diff": "",
                                "sources": {"current": {path: "for attempt in range(3): retry()"}}}
                    raw, usage = security_engine.ask(item, selected, credentials, "assessment", prepared, None, 60,
                                                    lambda: False, "smoke-" + path, configuration_managed=True)
                    assert raw["verdict"] == "confirmed" and raw["evidence"][0]["path"] == path
                    assert raw["finding_id"] == finding_id
                    assert raw["explanation"].endswith(path) and usage == {"tokens": 15}

                try:
                    with ocr.configuration(selected, credentials, {}, session_per_task=True), ThreadPoolExecutor(max_workers=4) as pool:
                        list(pool.map(security_assess, paths))
                finally:
                    assessment_barrier = None
                assert len(calls) == before + 4 and len(set(conversation_keys)) == 4
                assert all(conversation_keys) and "{ocr_session_key}" not in conversation_keys
                assert not (Path.home() / ".opencodereview/config.json").exists()
                print(f"Four pinned OCR security assessments overlapped with isolated sessions and validated evidence via {protocol} (mock provider).")
            conversation_keys.clear()
            assessment_context = {**context, "history": [], "finding": {}}
            for verdict in ("confirmed", "rejected", "uncertain", "context"):
                verification_mode = "assessment_" + verdict
                before = len(calls)
                raw, usage = verification.api_verify(
                    root, env, connection, {"api_key": "fake-test-key"}, assessment_context,
                    options["token_budget"], 60, lambda: False, instructions=assessment.INSTRUCTIONS,
                    schema=assessment.output_schema(assessment_context), validator=assessment.validate_response,
                )
                assert raw["verdict"] == ("uncertain" if verdict == "context" else verdict), raw
                assert raw["required_paths"] == (["helper.py"] if verdict == "context" else [])
                assert usage["tokens"] > 0
                assert len(calls) == before + 1, "A submitted assessment must finish without another model turn."
            verification_mode = "assessment_confirmed"
            assessment_barrier = threading.Barrier(2)
            before = len(calls)
            terminals = []
            original_terminal = verification.terminal_result

            def capture_terminal(workspace, session_id):
                assert (Path.home() / ".opencodereview/config.json").exists()
                terminals.append((str(workspace), session_id))
                return original_terminal(workspace, session_id)

            def assess_path(path, stopped=lambda: False):
                prepared = deepcopy(assessment_context)
                prepared["finding"] = {"path": path}
                prepared["current_path"] = path
                prepared["sources"] = {revision: {path: source["main.py"]}
                                       for revision, source in prepared["sources"].items()}
                raw, usage = verification.api_verify(
                    root, env, connection, {"api_key": "fake-test-key"}, prepared,
                    None, 60, stopped, instructions=assessment.INSTRUCTIONS,
                    schema=assessment.output_schema(prepared), validator=assessment.validate_response,
                    configuration_managed=True,
                )
                assert raw["verdict"] == "confirmed" and usage["tokens"] > 0
                assert raw["evidence"][0]["path"] == path and raw["explanation"].endswith(path)
                return raw

            verification.terminal_result = capture_terminal
            try:
                with ocr.configuration(connection, {
                    "api_key": "fake-test-key", "extra_headers": {"X-Smoke-Session": "{ocr_session_key}"},
                }, {}, session_per_task=True):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(assess_path, ["one.py", "two.py"]))
                    assert len(results) == 2
                    assert (Path.home() / ".opencodereview/sessions").exists()
            finally:
                verification.terminal_result = original_terminal
                assessment_barrier = None
            assert len(calls) == before + 2, "Both independent assessment requests must overlap."
            assert len(terminals) == 2 and len({item[0] for item in terminals}) == 2
            assert len({item[1] for item in terminals}) == 2
            assert all(conversation_keys) and len(set(conversation_keys)) == 2
            assert "{ocr_session_key}" not in conversation_keys
            assert not (Path.home() / ".opencodereview/config.json").exists()
            assert not (Path.home() / ".opencodereview/sessions").exists()
            print("Parallel pinned OCR assessments overlapped with separate conversations/results and joined cleanup (mock provider).")
            assessment_barrier = threading.Barrier(3)
            assessment_hold = threading.Event()
            cancelled = threading.Event()
            try:
                with ocr.configuration(connection, {"api_key": "fake-test-key"}, {}, session_per_task=True):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(assess_path, path, cancelled.is_set) for path in ("one.py", "two.py")]
                        try:
                            assessment_barrier.wait(timeout=15)
                            cancelled.set()
                            for future in futures:
                                try:
                                    future.result(timeout=10)
                                except ocr.ReviewStopped:
                                    pass
                                else:
                                    raise AssertionError("An active OCR process survived cancellation.")
                        finally:
                            cancelled.set()
                    assert (Path.home() / ".opencodereview/config.json").exists()
                assert not (Path.home() / ".opencodereview/config.json").exists()
                assert not (Path.home() / ".opencodereview/sessions").exists()
                print("Cancellation terminated both active pinned OCR processes before configuration cleanup.")
            finally:
                assessment_hold.set()
                assessment_barrier = None
            for failure, field in (("identity", "finding_id"), ("citation", "evidence[0]")):
                verification_mode = "assessment_bad_" + failure
                before = len(calls)
                try:
                    verification.api_verify(
                        root, env, connection, {"api_key": "fake-test-key"}, assessment_context,
                        options["token_budget"], 60, lambda: False, instructions=assessment.INSTRUCTIONS,
                        schema=assessment.output_schema(assessment_context), validator=assessment.validate_response,
                    )
                except verification.VerificationOutputError as exc:
                    assert exc.feedback["field"] == field and exc.usage["tokens"] > 0, str(exc)
                else:
                    raise AssertionError("Invalid assessment must retain validation feedback and fail closed")
                assert len(calls) == before + 1
            for repetitions in (60000, 46000):
                before = len(calls)
                try:
                    verification.api_verify(
                        root, env, connection, {"api_key": "fake-test-key"},
                        {**assessment_context, "diff": "x " * repetitions}, None, 60,
                        lambda: False, instructions=assessment.INSTRUCTIONS,
                        schema=assessment.output_schema(), validator=assessment.validate_response,
                    )
                except verification.VerificationOutputError as exc:
                    assert "47,110" in str(exc) and "58,888" in str(exc), str(exc)
                    assert ("too_large" in str(exc)) == (repetitions == 60000), str(exc)
                    assert exc.status == "skipped" and exc.usage == {"tokens": 0}
                else:
                    raise AssertionError("Oversized evidence was accepted")
                assert len(calls) == before, "Size checks must not call the provider."
            verification_mode = "assessment_uncertain"
            before = len(calls)
            raw, usage = verification.api_verify(
                root, env, {**connection, "options": {"prompt_token_ceiling": 200000}},
                {"api_key": "fake-test-key"}, {**assessment_context, "diff": "x " * 60000},
                None, 60, lambda: False, instructions=assessment.INSTRUCTIONS,
                schema=assessment.output_schema(), validator=assessment.validate_response,
            )
            assert raw["verdict"] == "uncertain" and usage["tokens"] > 0
            assert len(calls) == before + 1, "A larger ceiling must preserve and submit the full evidence."
            for verification_mode in ("no_verdict", "multiple_results", "provider_error"):
                try:
                    verification.api_verify(root, env, connection, {"api_key": "fake-test-key"}, context,
                                            options["token_budget"], 60, lambda: False)
                except ValueError as exc:
                    detail = str(exc)
                    assert "fake-test-key" not in detail, detail
                    if verification_mode in {"no_verdict", "multiple_results"}:
                        assert "The AI did not finish checking this finding." in detail, detail
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
    from ocr_transport_smoke import main as transport_smoke

    main()
    transport_smoke()
