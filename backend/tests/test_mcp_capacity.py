"""Exercise the public ASGI app against the isolated test database and Redis."""

import asyncio
import hashlib
import hmac
from contextlib import ExitStack, contextmanager
from threading import get_ident

import httpx2
import pytest
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import event, func, select
from starlette.requests import Request
from test_mcp_auth import credentials
from test_mcp_auth import enabled as enabled

from code_mortis.api.routers.webhooks import webhook
from code_mortis.core.db import engine, mcp_engine, transaction
from code_mortis.main import app
from code_mortis.mcp.auth import McpTokenVerifier
from code_mortis.models import WebhookDelivery


@contextmanager
def checkouts(pool):
    seen = []

    def record(*_):
        seen.append(True)

    event.listen(pool, "checkout", record)
    try:
        yield seen
    finally:
        event.remove(pool, "checkout", record)


def transport_client():
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver",
                              cookies={"cm_session": "session"})


def rpc_args(token):
    return {"headers": {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"},
            "json": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}}


def test_reconnect_burst_preserves_webhook_dashboard_and_readiness(enabled, client, monkeypatch):
    token = credentials(client)["access_token"]

    async def exercise():
        arrived = 0
        both_entered, release = asyncio.Event(), asyncio.Event()
        original = McpTokenVerifier.verify_token
        startup = rpc_args(token)
        startup["json"].update(method="initialize", params={
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "reconnect-test", "version": "1"},
        })

        async def hold(verifier, bearer):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                both_entered.set()
            await release.wait()
            return await original(verifier, bearer)

        monkeypatch.setattr(McpTokenVerifier, "verify_token", hold)
        async with transport_client() as http:
            active = [asyncio.create_task(http.post("/mcp", **startup)) for _ in range(2)]
            try:
                await asyncio.wait_for(both_entered.wait(), 5)
                with checkouts(mcp_engine()) as mcp, checkouts(engine()) as web:
                    async with asyncio.timeout(5):
                        responses = await asyncio.gather(*(http.post("/mcp", **startup) for _ in range(124)))
                        assert all(r.status_code == 429 and r.headers["retry-after"] == "2" for r in responses)
                        async def unread_body():
                            pytest.fail("Admission read a rejected OAuth request")
                            yield b""

                        assert (await http.post("/oauth/token", content=unread_body())).status_code == 429
                        assert (await http.get("/.well-known/oauth-authorization-server")).status_code == 429
                    assert not mcp and not web

                    body = b'{"action":"test"}'
                    headers = {"x-github-event": "push", "x-github-delivery": "capacity-test",
                               "x-hub-signature-256": "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()}
                    async with asyncio.timeout(5):
                        ready, dashboard, controls, first = await asyncio.gather(
                            http.get("/health/ready"), http.get("/api/v1/pulls"),
                            http.get("/api/v1/mcp/settings"),
                            http.post("/api/v1/webhooks/github", content=body, headers=headers))
                        assert ready.json() == {"status": "ready"}
                        assert dashboard.status_code == 200 and dashboard.json()["total"] == 1
                        assert controls.status_code == 200 and controls.json()["can_manage"]
                        assert first.status_code == 202 and first.json()["duplicate"] is False
                        duplicate = await http.post("/api/v1/webhooks/github", content=body, headers=headers)
                        assert duplicate.status_code == 202 and duplicate.json()["duplicate"] is True
                    assert web and not mcp
            finally:
                release.set()
                completed = await asyncio.gather(*active)
            assert all(r.status_code == 200 and "serverInfo" in r.json()["result"] for r in completed)
            assert (await http.post("/mcp", **rpc_args(token))).status_code == 200

    client.portal.call(exercise)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(WebhookDelivery)) == 1


def test_pool_exhaustion_and_sdk_nested_tool_read(enabled, client):
    token = credentials(client)["access_token"]

    async def exercise():
        async with transport_client() as http:
            with ExitStack() as held:
                for _ in range(mcp_engine().pool.size()):
                    held.enter_context(mcp_engine().connect())
                with checkouts(engine()) as web:
                    busy = await http.post("/mcp", **rpc_args(token))
                    assert busy.status_code == 503
                    assert not web
                    assert (await http.get("/health/ready")).status_code == 200
                    assert web
            http.headers["Authorization"] = "Bearer " + token
            with checkouts(engine()) as web, checkouts(mcp_engine()) as mcp:
                async with asyncio.timeout(5):
                    stream = await http.get("/mcp", headers={"Accept": "text/event-stream"})
                assert stream.status_code == 405 and not mcp
                async with streamable_http_client("http://testserver/mcp", http_client=http) as streams:
                    async with ClientSession(streams[0], streams[1]) as sdk:
                        await sdk.initialize()
                        catalog = await sdk.list_tools()
                        assert "reviews_get_pull_request" in {tool.name for tool in catalog.tools}
                        result = await sdk.call_tool("reviews_get_pull_request", {"params": {"pr_id": "pr"}})
                        assert not result.is_error and result.structured_content["pull_request"]["id"] == "pr"
                assert mcp and not web

    client.portal.call(exercise)


def test_disconnected_webhook_releases_without_database_access(seed):
    async def exercise():
        calls = iter([{"type": "http.request", "body": b"{", "more_body": True},
                      {"type": "http.disconnect"}])

        async def receive():
            assert not web
            return next(calls)

        with checkouts(engine()) as web, pytest.raises(HTTPException) as error:
            await webhook(Request({"type": "http", "headers": []}, receive))
        assert error.value.status_code == 400 and not web

    asyncio.run(exercise())


def test_chunked_webhook_finishes_upload_before_database_work(seed):
    async def exercise():
        chunks = [b'{"action":', b'"test"}']
        signature = hmac.new(b"webhook-secret", b"".join(chunks), hashlib.sha256).hexdigest()
        headers = [(b"x-github-event", b"ping"), (b"x-github-delivery", b"chunked"),
                   (b"x-hub-signature-256", ("sha256=" + signature).encode())]
        loop_thread = get_ident()
        queries = []

        def checked_out(*_):
            assert not chunks
            assert get_ident() != loop_thread
            queries.append(True)

        async def receive():
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

        event.listen(engine(), "checkout", checked_out)
        try:
            assert await webhook(Request({"type": "http", "headers": headers}, receive)) == {
                "accepted": True, "duplicate": False}
            assert queries
        finally:
            event.remove(engine(), "checkout", checked_out)

    asyncio.run(exercise())
