"""Offline image check for Mortis gateway contracts and retired runtime removal."""

import importlib.util
import os
import shutil

os.environ.setdefault("CM_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("CM_BOOTSTRAP_TOKEN", "isolated-mortis-smoke-only")
os.environ.setdefault("CM_OWNER_GITHUB_LOGIN", "synthetic-mortis-test")

from code_mortis.core.config import Settings
from code_mortis.integrations.llm_http import decode_response, encode_request
from code_mortis.reviews import mortis
from code_mortis.schemas.inference import Message, Request, Tool
from code_mortis.security_scans import engine as security_engine

assert mortis and security_engine
assert all(shutil.which(binary) is None for binary in ("ocr", "codex", "claude"))
assert shutil.which("git") and shutil.which("trivy")
assert all(importlib.util.find_spec(module) is None for module in (
    "code_mortis.integrations.ocr", "code_mortis.integrations.runner_client",
    "code_mortis.native_runner", "code_mortis.reviews.delegated",
))
assert not any(name.startswith(("ocr_", "runner_")) for name in Settings.model_fields)
request = Request(messages=[Message(role="user", content="Synthetic offline check")],
                  tools=[Tool(name="complete", parameters={"type": "object", "properties": {},
                                                          "additionalProperties": False})],
                  tool_choice="complete", max_output_tokens=100)
responses = {
    "openai": {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "synthetic", "type": "function", "function": {"name": "complete", "arguments": "{}"}},
    ]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    "openai-responses": {"status": "completed", "output": [
        {"type": "function_call", "call_id": "synthetic", "name": "complete", "arguments": "{}"},
    ], "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
    "anthropic": {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "synthetic", "name": "complete", "input": {}},
    ], "usage": {"input_tokens": 10, "output_tokens": 5}},
}
for protocol, response in responses.items():
    body = encode_request(protocol, {"model": "synthetic-mortis-model"}, request)
    assert body["model"] == "synthetic-mortis-model" and body["tools"]
    decoded = decode_response(protocol, response, request)
    assert decoded.tool_calls[0].name == "complete" and decoded.tool_calls[0].arguments == {}
    assert decoded.usage.total_tokens == 15
print("Mortis gateway contracts passed offline; retired OCR and subscription runtimes are absent.")
