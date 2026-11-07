"""Real pinned OCR against local protocol fixtures, with no model account or external traffic."""

import json
import os
import tempfile
import threading
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

os.environ["CM_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["CM_BOOTSTRAP_TOKEN"] = "container-smoke-test-token"
os.environ["CM_OWNER_GITHUB_LOGIN"] = "test-owner"
os.environ["CM_OCR_ISOLATED_RUNTIME"] = "true"
os.environ["CM_ALLOW_PRIVATE_ENDPOINTS"] = "true"

from code_mortis.integrations import ocr
from code_mortis.reviews import assessment, verification
from code_mortis.reviews import context as review_context
from code_mortis.schemas.security import SecurityAnalysis
from code_mortis.security_scans import engine as security_engine
from code_mortis.security_scans.engine import model_schema

# Adapter-only smoke: no database or cross-worker admission service is available.
ocr.configuration = ocr._configuration

RESULT = {"findings": [], "required_paths": []}
original_command = ocr.command
diagnostic_mode = ""


def observed_command(args, root, env, **kwargs):
    response = original_command(args, root, env, **kwargs)
    if "--preview" in args or "scan" not in args or "CM_OCR_DIAGNOSTIC_FILE" not in env:
        return response
    capture = Path(env["CM_OCR_DIAGNOSTIC_FILE"])
    assert "OCR_RAW_LOGGING" not in env
    assert capture.stat().st_mode & 0o777 == 0o600
    captured = capture.read_text()
    assert captured and "fake-test-key" not in captured and "PRIVATE_SOURCE_MARKER" not in captured
    assert env["CM_OCR_DIAGNOSTIC_KEY"] not in captured
    assert not (Path.home() / ".opencodereview/raw").exists()
    if diagnostic_mode in {"ocr_lost", "ocr_changed", "capture_missing"}:
        session_id = json.loads(response[1])["session_id"]
        for path in (Path.home() / ".opencodereview/sessions").glob(f"*/{session_id}.jsonl"):
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            for row in rows:
                if row.get("type") == "llm_response" and row.get("taskType") == "main_task":
                    call = row["tool_calls"][0]
                    call["arguments"] = call["arguments"].replace('"DONE"', '"D0NE"') if diagnostic_mode == "ocr_changed" else "{}"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        if diagnostic_mode == "capture_missing":
            capture.unlink()
    return response


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls += 1
        continuing = self.server.calls > 1
        definitions = [tool.get("function", tool) for tool in data["tools"]]
        assert {tool["name"] for tool in definitions} == {"task_done"}
        mode = self.server.mode
        if mode in {"strict", "strict_empty", "shared_strict"}:
            assert definitions[0]["strict"] is True
            assert definitions[0]["parameters"]["required"] == ["state", "result"]
        if mode == "security_citation":
            schema = definitions[0].get("parameters", definitions[0].get("input_schema"))
            selection = schema["$defs"]["SecurityEvidenceSelection"]["properties"]
            assert set(selection) == {"source_id", "start_line", "end_line"}
            assert selection["source_id"]["enum"] == ["source_1"]
            assert "Copy source text verbatim" not in definitions[0]["description"]
        if mode == "review_selection":
            assert "SYNTHETIC_SECRET" not in json.dumps(data)
            schema = definitions[0].get("parameters", definitions[0].get("input_schema"))
            assert set(schema["$defs"]["SelectedEvidence"]["properties"]) == {"source_id", "start_line", "end_line"}
        value = {"state": "DONE", "result": self.server.result if mode in {"security_citation", "review_selection"} else RESULT}
        if mode == "rejected_round" and not continuing:
            value = {"state": "INVALID"}
        if mode == "missing_result":
            value = {"state": "DONE"}
        if mode in {"empty_arguments", "strict_empty"}:
            value = {}
        arguments = json.dumps(value)
        if mode == "malformed_round" and not continuing:
            arguments = '{"state":'
        if mode == "wrapped":
            arguments += "<|start|><|channel|>final<|message|>Finished."
        count = 2 if mode == "duplicate" else 1
        if self.path.endswith("/responses"):
            result = {"id": "resp_smoke", "object": "response", "status": "completed", "model": "test-model",
                "output": [{"type": "function_call", "id": f"fc_{i}", "call_id": f"call_{i}",
                            "name": "task_done", "arguments": arguments} for i in range(count)],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        elif self.path.endswith("/chat/completions"):
            result = {"id": "chat_smoke", "object": "chat.completion", "model": "test-model",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant",
                    "tool_calls": [{"id": f"call_{i}", "type": "function",
                                    "function": {"name": "task_done", "arguments": arguments}} for i in range(count)]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        else:
            result = {"id": "message_smoke", "type": "message", "role": "assistant", "model": "test-model",
                "content": [{"type": "tool_use", "id": f"call_{i}", "name": "task_done", "input": value} for i in range(count)],
                "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5}}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


def main():
    global diagnostic_mode
    ocr.command = observed_command
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for protocol in ("openai", "openai-responses", "anthropic"):
            modes = ["valid", "rejected_round", "malformed_round", "wrapped", "missing_result", "empty_arguments", "duplicate"]
            if protocol == "openai-responses":
                modes += ["strict", "strict_empty", "shared_strict", "ocr_lost", "ocr_changed", "capture_missing"]
            for mode in modes:
                if protocol == "anthropic" and mode in {"malformed_round", "wrapped"}:
                    continue  # Anthropic tool inputs are objects, not raw JSON argument strings.
                server.mode, server.calls = mode, 0
                diagnostic_mode = mode
                connection = {"kind": "api", "protocol": protocol, "model": "test-model", "options": {},
                              "base_url": f"http://127.0.0.1:{server.server_port}"}
                managed = mode == "shared_strict"
                configuration = ocr.configuration(connection, {"api_key": "fake-test-key"}, {},
                    session_per_task=True, structured_result=True) if managed else nullcontext()
                with configuration, tempfile.TemporaryDirectory() as directory:
                    try:
                        result, usage = verification.api_verify(Path(directory), ocr.child_environment(), connection,
                            {"api_key": "fake-test-key"}, {"diff": "", "sources": {"current": {"app.py": "PRIVATE_SOURCE_MARKER"}}},
                            None, 30, lambda: False, schema=model_schema(SecurityAnalysis),
                            structured_result=mode in {"strict", "strict_empty"}, configuration_managed=managed,
                            validator=lambda raw, supplied: SecurityAnalysis.model_validate(raw, strict=True).model_dump())
                    except verification.VerificationOutputError as exc:
                        assert mode in {"missing_result", "empty_arguments", "strict_empty", "duplicate", "ocr_lost", "ocr_changed", "capture_missing"}, (protocol, mode, str(exc))
                        assert exc.usage == {"tokens": 15} and exc.feedback["kind"] == "delivery"
                        assert "AI result delivery failed" in str(exc)
                        if protocol == "openai-responses":
                            expected = "ocr_mismatch" if mode in {"ocr_lost", "ocr_changed"} else "unavailable" if mode == "capture_missing" else (
                                "arguments_preserved" if mode == "duplicate" else "gateway_incomplete")
                            assert exc.feedback["transport"]["origin"] == expected, (mode, exc.feedback)
                    else:
                        assert mode not in {"missing_result", "empty_arguments", "strict_empty", "duplicate", "ocr_lost", "ocr_changed", "capture_missing"}, (protocol, mode)
                        assert result == RESULT
                        assert usage == {"tokens": 30 if mode in {"rejected_round", "malformed_round"} else 15}, usage
                assert server.calls == (2 if mode in {"rejected_round", "malformed_round"} else 1)
                assert not (Path.home() / ".opencodereview/config.json").exists()
            print(f"Pinned OCR {protocol}: rejected rounds, structured delivery, usage and cleanup passed (mock provider).")
            cases = json.loads((Path(__file__).parent / "fixtures/security_citation_failures.json").read_text())["cases"]
            diagnostic_mode = ""
            for case in cases:
                for changed in (False, True):
                    server.mode, server.calls = "security_citation", 0
                    citation = case["citation"].copy()
                    selected = {"source_id": "source_1", "start_line": citation["start_line"], "end_line": citation["end_line"]}
                    if changed:
                        selected.update(start_line=citation["end_line"], end_line=citation["start_line"] - 1)
                    path, task = citation["path"], case["task"]
                    server.result = {"findings": [{"title": "Synthetic defect", "category": "test", "severity": "low", "path": path,
                        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                        "verification": "Test", "evidence": [selected]}], "required_paths": []} if task == "analysis" else {
                            "verdict": case["verdict"], "finding_id": None, "explanation": "Checked source", "evidence": [selected], "required_paths": []}
                    source = "\n" * (citation["start_line"] - 1) + case["source_lines"]
                    context = {"assigned_path": path, "diff": "", "history": [], "sources": {"current": {path: source}}}
                    with tempfile.TemporaryDirectory() as directory:
                        item = SimpleNamespace(git=Path(directory), env=ocr.child_environment(), inventory={path: {}}, redact=lambda text: text)
                        try:
                            result, usage = security_engine.ask(item, {**connection, "kind": "api"}, {"api_key": "fake-test-key"},
                                task, context, 100000, 30, lambda: False, "citation-smoke")
                        except verification.VerificationOutputError as exc:
                            assert changed and exc.usage == {"tokens": 30}, (protocol, task, str(exc))
                            assert exc.feedback["kind"] == "citation_selection" and exc.feedback["reason"] == "invalid source line range"
                        else:
                            assert not changed and usage["tokens"] == 15
                            citations = result["findings"][0]["evidence"] if task == "analysis" else result["evidence"]
                            assert citations[0]["quote"] == case["source_lines"]
                            if task == "assessment":
                                assert result["verdict"] == case["verdict"]
                    assert server.calls == (2 if changed else 1)
                    assert not (Path.home() / ".opencodereview/config.json").exists()
            print(f"Pinned OCR {protocol}: source selections materialized exact saved Python/TSX evidence and preserved verdicts; invalid ranges rejected with bounded usage (mock provider).")
            for task in ("assessment", "verification"):
                server.mode, server.calls = "review_selection", 0
                context = {"task_contract": review_context.TASK_CONTRACT, "head_sha": "b" * 40, "finding": {"path": "app.py"},
                    "sources": {"current": {"app.py": "safe()\n"}, "original": {"app.py": "unsafe()\n"}},
                    "diff": "-unsafe()\n+safe()", "history": [{"id": "stored-uuid", "reason": "SYNTHETIC_SECRET"}],
                    "validation_feedback": {"reason": "SYNTHETIC_SECRET"}}
                evidence = [{"source_id": review_context.source_id("current", "app.py"), "start_line": 1, "end_line": 1}]
                if task == "assessment":
                    server.result = {"verdict": "confirmed", "relationship": "duplicate", "finding_id": "history_1",
                        "explanation": "Concrete defect", "cause": "Cause", "trigger": "Input", "consequence": "Effect",
                        "history_explanation": "Same defect", "contradicts_prior": False, "changed_conditions": "",
                        "suggestion_supported": False, "required_paths": [], "evidence": evidence}
                    schema, validator = assessment.output_schema(context), assessment.validate_response
                else:
                    server.result = {"verdict": "fixed", "original_issue_present": True, "dismissal_recommended": False,
                        "explanation": "Original cause demonstrated", "change_explanation": "Cause removed", "required_sources": [],
                        "evidence": [*evidence, {"source_id": review_context.source_id("original", "app.py"), "start_line": 1, "end_line": 1}]}
                    schema, validator = verification.output_schema(), verification.validate_response
                with tempfile.TemporaryDirectory() as directory:
                    result, usage = verification.api_verify(Path(directory), ocr.child_environment(), connection,
                        {"api_key": "fake-test-key", "redaction_values": ["SYNTHETIC_SECRET"]}, context,
                        100000, 30, lambda: False, schema=schema, validator=validator)
                assert result["evidence"][0]["quote"] == "safe()\n" and usage == {"tokens": 15}
                if task == "assessment":
                    assert result["finding_id"] == "stored-uuid"
                assert server.calls == 1
            print(f"Pinned OCR {protocol}: PR assessment and fix verification selections, history IDs and redaction passed.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
