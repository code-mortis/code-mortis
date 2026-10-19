"""Real pinned OCR against local protocol fixtures, with no model account or external traffic."""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

os.environ["CM_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["CM_BOOTSTRAP_TOKEN"] = "container-smoke-test-token"
os.environ["CM_OWNER_GITHUB_LOGIN"] = "test-owner"
os.environ["CM_OCR_ISOLATED_RUNTIME"] = "true"
os.environ["CM_ALLOW_PRIVATE_ENDPOINTS"] = "true"

from code_mortis import ocr, security_engine, verification
from code_mortis.security_engine import model_schema
from code_mortis.security_schemas import SecurityAnalysis

RESULT = {"findings": [], "required_paths": []}


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
        value = {"state": "DONE", "result": self.server.result if mode == "security_citation" else RESULT}
        if mode == "rejected_round" and not continuing:
            value = {"state": "INVALID"}
        if mode == "missing_result":
            value = {"state": "DONE"}
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for protocol in ("openai", "openai-responses", "anthropic"):
            for mode in ("valid", "rejected_round", "malformed_round", "wrapped", "missing_result", "duplicate"):
                if protocol == "anthropic" and mode in {"malformed_round", "wrapped"}:
                    continue  # Anthropic tool inputs are objects, not raw JSON argument strings.
                server.mode, server.calls = mode, 0
                connection = {"protocol": protocol, "model": "test-model", "options": {},
                              "base_url": f"http://127.0.0.1:{server.server_port}"}
                with tempfile.TemporaryDirectory() as directory:
                    try:
                        result, usage = verification.api_verify(Path(directory), ocr.child_environment(), connection,
                            {"api_key": "fake-test-key"}, {"diff": "", "sources": {"current": {"app.py": "safe"}}},
                            None, 30, lambda: False, schema=model_schema(SecurityAnalysis),
                            validator=lambda raw, supplied: SecurityAnalysis.model_validate(raw, strict=True).model_dump())
                    except verification.VerificationOutputError as exc:
                        assert mode in {"missing_result", "duplicate"}, (protocol, mode, str(exc))
                        assert exc.usage == {"tokens": 15} and exc.feedback["kind"] == "delivery"
                    else:
                        assert mode not in {"missing_result", "duplicate"}, (protocol, mode)
                        assert result == RESULT
                        assert usage == {"tokens": 30 if mode in {"rejected_round", "malformed_round"} else 15}, usage
                assert server.calls == (2 if mode in {"rejected_round", "malformed_round"} else 1)
                assert not (Path.home() / ".opencodereview/config.json").exists()
            print(f"Pinned OCR {protocol}: rejected rounds, structured delivery, usage and cleanup passed (mock provider).")
            cases = json.loads((Path(__file__).parent / "fixtures/security_citation_indentation.json").read_text())["cases"]
            for case in cases:
                for changed in (False, True):
                    server.mode, server.calls = "security_citation", 0
                    citation = case["citation"].copy()
                    if changed:
                        citation["quote"] = citation["quote"].replace("1_500_000", "1_500_001").replace("150", "151")
                    path, task = citation["path"], case["task"]
                    server.result = {"findings": [{"title": "Synthetic defect", "category": "test", "severity": "low", "path": path,
                        "explanation": "Synthetic", "trigger": "Input", "impact": "Impact", "remediation": "Fix",
                        "verification": "Test", "evidence": [citation]}], "required_paths": []} if task == "analysis" else {
                            "verdict": "uncertain", "finding_id": None, "explanation": "More verification needed", "evidence": [citation], "required_paths": []}
                    source = "\n" * (citation["start_line"] - 1) + case["source_lines"]
                    context = {"assigned_path": path, "diff": "", "history": [], "sources": {"current": {path: source}}}
                    with tempfile.TemporaryDirectory() as directory:
                        item = SimpleNamespace(git=Path(directory), env=ocr.child_environment(), inventory={path: {}}, redact=lambda text: text)
                        try:
                            result, usage = security_engine.ask(item, {**connection, "kind": "api"}, {"api_key": "fake-test-key"},
                                task, context, 100000, 30, lambda: False, "citation-smoke")
                        except verification.VerificationOutputError as exc:
                            assert changed and exc.usage == {"tokens": 30}, (protocol, task, str(exc))
                            assert exc.feedback["reason"] == "quote is not verbatim text within the cited source lines"
                        else:
                            assert not changed and usage["tokens"] == 15
                            citations = result["findings"][0]["evidence"] if task == "analysis" else result["evidence"]
                            assert citations[0]["quote"] == case["source_lines"]
                            assert usage["citation_recoveries"][0]["reason"] == "Python indentation restored"
                            if task == "assessment":
                                assert result["verdict"] == "uncertain"
                    assert server.calls == (2 if changed else 1)
                    assert not (Path.home() / ".opencodereview/config.json").exists()
            print(f"Pinned OCR {protocol}: exact saved scan citation failures recovered; altered evidence rejected with bounded usage (mock provider).")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
