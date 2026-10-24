"""Native usage comes from client telemetry, never the model's structured answer."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from code_mortis import native_runner
from code_mortis.integrations import runner_client


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_codex_collects_cumulative_usage_events_without_changing_sandbox(tmp_path, monkeypatch, status):
    async def check():
        client = native_runner.Codex(tmp_path)
        reader = asyncio.StreamReader()
        client.process = SimpleNamespace(stdout=reader)
        calls = []

        async def call(method, params=None):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "thread"}}
            if method == "turn/start":
                events = [{"method": "thread/tokenUsage/updated", "params": {
                    "threadId": thread, "turnId": "turn", "tokenUsage": {"total": {"totalTokens": tokens}},
                }} for thread, tokens in [("another", 999999), ("thread", 30), ("thread", 35), ("thread", 35)]]
                events += [
                    {"method": "item/completed", "params": {"threadId": "thread", "item": {
                        "type": "agentMessage", "text": '{"comments": []}',
                    }}},
                    {"method": "turn/completed", "params": {"threadId": "thread", "turn": {
                        "status": status, "error": {"message": "rate limit"},
                    }}},
                ]
                reader.feed_data(("\n".join(json.dumps(event) for event in events) + "\n").encode())
                reader.feed_eof()
                await client.read()
                return {"turn": {"id": "turn"}}
            return {}

        async def codex(*args):
            return client

        client.call = call
        monkeypatch.setattr(native_runner, "codex", codex)
        usage = {"tokens": None}
        body = native_runner.ReviewBody(kind="chatgpt", model="model", effort="low", prompt="review",
                                       output_schema={}, timeout=30)
        if status == "completed":
            assert await native_runner.run_codex("connection", body, tmp_path, usage) == {"comments": []}
        else:
            with pytest.raises(native_runner.NativeError) as caught:
                await native_runner.run_codex("connection", body, tmp_path, usage)
            assert caught.value.reason == "quota"
        assert usage == {"tokens": 35}
        assert calls[0][1]["approvalPolicy"] == "never"
        assert calls[0][1]["permissions"] == "mortis-review"
        assert "sandboxPolicy" not in calls[1][1]
        assert [method for method, _ in calls[-2:]] == ["turn/interrupt", "thread/archive"]

    asyncio.run(check())


@pytest.mark.parametrize("counts,expected", [
    ({"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30}, 65),
    ({"input_tokens": 10, "output_tokens": 5}, 15),
    ({"input_tokens": True, "output_tokens": 5}, None),
    ({"input_tokens": 10, "output_tokens": -1}, None),
    ({}, None),
])
@pytest.mark.parametrize("failed", [False, True])
def test_claude_counts_input_output_and_cache_even_on_failure(tmp_path, monkeypatch, counts, expected, failed):
    identity = "00000000-0000-4000-8000-000000000001"
    monkeypatch.setattr(native_runner, "ROOT", tmp_path)
    async def process(*args, **kwargs):
        assert "--restricted" in args and "--safe-mode" in args and "--strict-mcp-config" in args
        assert args[args.index("--tools") + 1] == args[args.index("--allowedTools") + 1] == "Read,Glob,Grep"
        assert args[args.index("--disallowedTools") + 1] == "mcp__*"
        return SimpleNamespace(returncode=int(failed))

    async def output(*args):
        return json.dumps({"subtype": "success", "is_error": failed, "usage": counts,
                           "structured_output": {"comments": []}}).encode()

    async def stop(*args):
        pass

    monkeypatch.setattr(native_runner.asyncio, "create_subprocess_exec", process)
    monkeypatch.setattr(native_runner, "bounded_output", output)
    monkeypatch.setattr(native_runner, "stop", stop)
    usage = {"tokens": None}
    body = native_runner.ReviewBody(kind="claude_code", model="model", effort="low", prompt="review",
                                   output_schema={}, timeout=30)
    if failed:
        with pytest.raises(native_runner.NativeError):
            asyncio.run(native_runner.run_claude(identity, body, tmp_path, usage))
    else:
        assert asyncio.run(native_runner.run_claude(identity, body, tmp_path, usage)) == {"comments": []}
    assert usage == {"tokens": expected}


@pytest.mark.parametrize("kind,runner", [("chatgpt", "run_codex"), ("claude_code", "run_claude")])
@pytest.mark.parametrize("budget,tokens,reason", [
    (None, 25, None), (25, 25, None), (24, 25, "token_limit"), (None, None, "usage_unknown"),
])
def test_native_budget_uses_reported_tokens_and_supports_unlimited(monkeypatch, kind, runner, budget, tokens, reason):
    async def auth(*args):
        return {"status": "connected"}

    async def inference(connection, body, directory, usage):
        usage["tokens"] = tokens
        return {"comments": []}

    monkeypatch.setattr(native_runner, "locks", {})
    monkeypatch.setattr(native_runner, "auth_status", auth)
    monkeypatch.setattr(native_runner, runner, inference)
    body = native_runner.ReviewBody(kind=kind, model="model", effort="low", prompt="review", output_schema={},
                                   timeout=30, token_budget=budget)
    entry = {}
    asyncio.run(native_runner.execute("connection", body, entry))
    assert entry["usage"] == {"tokens": tokens}
    assert entry["status"] == ("failed" if reason else "completed")
    if reason:
        assert entry["reason"] == reason


@pytest.mark.parametrize("failed", [False, True])
def test_runner_transport_returns_usage_for_success_and_failure(monkeypatch, failed):
    methods = []

    def request(method, *args):
        methods.append(method)
        return {"status": "failed" if failed else "completed", "reason": "token_limit",
                "result": {"comments": []}, "usage": {"tokens": 25}}

    monkeypatch.setattr(runner_client, "request", request)
    if failed:
        with pytest.raises(runner_client.RunnerBlocked) as caught:
            runner_client.review("connection", "run", {}, lambda: False)
        assert caught.value.reason == "token_limit" and caught.value.usage == {"tokens": 25}
    else:
        assert runner_client.review("connection", "run", {}, lambda: False) == ({"comments": []}, {"tokens": 25})
    assert methods == ["POST", "GET", "DELETE"]


@pytest.mark.parametrize("tokens", [None, True, -1, "25"])
def test_runner_transport_never_treats_invalid_usage_as_zero(monkeypatch, tokens):
    monkeypatch.setattr(runner_client, "request", lambda *args: {
        "status": "completed", "result": {"comments": []}, "usage": {"tokens": tokens},
    })
    with pytest.raises(runner_client.RunnerBlocked) as caught:
        runner_client.review("connection", "run", {}, lambda: False)
    assert caught.value.reason == "usage_unknown" and caught.value.usage == {"tokens": None}
