"""Real pinned OCR against local protocol fixtures, with no model account or external traffic."""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["CM_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["CM_BOOTSTRAP_TOKEN"] = "container-smoke-test-token"
os.environ["CM_OWNER_GITHUB_LOGIN"] = "test-owner"
os.environ["CM_OCR_ISOLATED_RUNTIME"] = "true"
os.environ["CM_ALLOW_PRIVATE_ENDPOINTS"] = "true"

from code_mortis import ocr, verification
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
        value = {"state": "DONE", "result": RESULT}
        mode = self.server.mode
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
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
