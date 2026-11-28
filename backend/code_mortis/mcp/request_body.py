"""Bound MCP and OAuth uploads before parsing or opening a database session."""

import asyncio

from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

BODY_LIMITS = {
    "/mcp": 128 * 1024,
    "/oauth/register": 256 * 1024,
    "/oauth/token": 16 * 1024,
    "/oauth/revoke": 16 * 1024,
    "/api/v1/mcp/authorize": 16 * 1024,
}
BODY_TIMEOUT_SECONDS = 15


async def buffer_mcp_request_body(scope: Scope, receive: Receive, send: Send) -> Receive | None:
    limit = BODY_LIMITS.get(scope["path"]) if scope["method"] == "POST" else None
    if limit is None:
        return receive

    body = bytearray()
    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            async for chunk in Request(scope, receive).stream():
                if len(body) + len(chunk) > limit:
                    response = JSONResponse({"detail": "MCP request body is too large."}, status_code=413,
                                            headers={"Cache-Control": "no-store"})
                    break
                body.extend(chunk)
            else:
                response = None
    except ClientDisconnect:
        return None
    except TimeoutError:
        response = JSONResponse({"detail": "MCP request body did not arrive in time."}, status_code=408,
                                headers={"Cache-Control": "no-store"})

    if response is not None:
        await response(scope, receive, send)
        return None

    sent = False

    async def replay():
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return replay
