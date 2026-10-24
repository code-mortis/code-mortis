"""Compare gateway metadata with OCR without retaining argument contents."""

import hashlib
import hmac
import json
import re
from pathlib import Path

MAX_BYTES = 20_000_000
FIELDS = {"call", "task_done", "argument_type", "bytes", "fingerprint", "json_valid", "done", "result_object"}


def fingerprint(key, domain, value):
    return hmac.new(key, (domain + "\0" + value).encode(), hashlib.sha256).hexdigest()


def call_shape(call, key):
    arguments = call.get("arguments")
    shape = {"call": fingerprint(key, "call", call.get("id", "")), "task_done": call.get("name") == "task_done",
             "argument_type": "non_string", "bytes": 0, "json_valid": False, "done": False, "result_object": False}
    if not isinstance(arguments, str):
        return shape
    shape.update(argument_type="string", bytes=len(arguments.encode()), fingerprint=fingerprint(key, "arguments", arguments))
    value = None
    try:
        value = json.loads(arguments)
        shape["json_valid"] = True
    except ValueError:
        start = re.search(r"[\[{]", arguments)
        if start:
            try:
                value, _ = json.JSONDecoder().raw_decode(arguments[start.start():])
            except ValueError:
                pass
    if isinstance(value, dict):
        shape.update(done=value.get("state") == "DONE", result_object=isinstance(value.get("result"), dict))
    return shape


def valid_shape(value):
    return (isinstance(value, dict) and not set(value) - FIELDS
            and isinstance(value.get("call"), str) and re.fullmatch(r"[0-9a-f]{64}", value["call"])
            and value.get("argument_type") in {"string", "non_string"}
            and type(value.get("bytes")) is int and 0 <= value["bytes"] <= MAX_BYTES
            and all(type(value.get(field)) is bool for field in ("task_done", "json_valid", "done", "result_object"))
            and (isinstance(value.get("fingerprint"), str) and re.fullmatch(r"[0-9a-f]{64}", value["fingerprint"])
                 if value["argument_type"] == "string" else "fingerprint" not in value))


def compare(root, session_id, capture, key):
    """Missing, corrupt or partial observations never establish which side lost data."""
    unavailable = {"origin": "unavailable"}
    try:
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", session_id):
            return unavailable
        if not 0 < capture.stat().st_size <= 65536:
            return unavailable
        records = [json.loads(line) for line in capture.read_text().splitlines()]
        gateway = []
        for record in records:
            if (not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1
                or record.get("attempt") != fingerprint(key, "attempt", "code-mortis.delivery/v1")
                or not isinstance(record.get("calls"), list) or len(record["calls"]) > 16
                or not all(valid_shape(call) for call in record["calls"])):
                return unavailable
            gateway.extend(record["calls"])
        normalized, responses, matched = [], 0, 0
        for path in (Path.home() / ".opencodereview/sessions").glob(f"*/{session_id}.jsonl"):
            if path.stat().st_size > MAX_BYTES:
                return unavailable
            with path.open() as stream:
                header = json.loads(next(stream, ""))
                if (not isinstance(header, dict) or header.get("type") != "session_start"
                    or header.get("cwd") != str(root) or header.get("sessionId") != session_id):
                    continue
                matched += 1
                for line in stream:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        return unavailable
                    if row.get("type") != "llm_response" or row.get("taskType") != "main_task":
                        continue
                    calls = row.get("tool_calls") or []
                    if (row.get("sessionId") != session_id or not isinstance(calls, list)
                        or not all(isinstance(call, dict) and isinstance(call.get("id", ""), str) for call in calls)):
                        return unavailable
                    responses += 1
                    normalized.extend(call_shape(call, key) for call in calls)
        if matched != 1 or responses != len(records) or max(len(gateway), len(normalized)) > 32:
            return unavailable
        def identities(calls):
            return [{field: call.get(field) for field in
                     ("call", "task_done", "argument_type", "bytes", "fingerprint")} for call in calls]
        changed = identities(gateway) != identities(normalized)
        complete = any(call["task_done"] and call["done"] and call["result_object"] for call in gateway)
        origin = "gateway_incomplete" if not complete else "ocr_mismatch" if changed else "arguments_preserved"
        result = {"origin": origin, "gateway_calls": len(gateway), "ocr_calls": len(normalized), "responses": responses}
        position = next((index for index in range(max(len(gateway), len(normalized)))
                         if identities(gateway[index:index + 1]) != identities(normalized[index:index + 1])), len(gateway) - 1)
        if position >= 0:
            result["comparison"] = {"gateway": gateway[position] if position < len(gateway) else None,
                                    "ocr": normalized[position] if position < len(normalized) else None}
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return unavailable


def explanation(diagnostics):
    origin = diagnostics.get("origin") if isinstance(diagnostics, dict) else None
    if not isinstance(origin, str):
        return ""
    return {
        "gateway_incomplete": "The gateway returned incomplete or malformed completion arguments.",
        "ocr_mismatch": "OCR's parsed completion differs from the gateway response.",
        "arguments_preserved": "The gateway and OCR recorded identical completion arguments.",
        "unavailable": "Gateway/OCR comparison was unavailable; the origin could not be determined.",
    }.get(origin, "")
