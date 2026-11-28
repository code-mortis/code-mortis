"""Bound external MCP work before reading a body or authenticating a token."""

import logging
from collections import Counter
from threading import Lock

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from code_mortis.core.config import settings
from code_mortis.core.db import mcp_request_path
from code_mortis.mcp.request_body import buffer_mcp_request_body

MCP_PATHS = frozenset({"/mcp", "/oauth/register",
                       "/oauth/authorize", "/oauth/token", "/oauth/revoke", "/api/v1/mcp/authorize"})
log = logging.getLogger(__name__)


class McpAdmission:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.lock = Lock()
        self.active = 0
        self.active_by_path: Counter[str] = Counter()
        self.peak_by_path: Counter[str] = Counter()
        self.admitted: Counter[str] = Counter()
        self.rejected: Counter[str] = Counter()
        self.overloaded_paths: set[str] = set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get("path", "")
        if scope["type"] == "http" and path.rstrip("/") in {"/mcp/reviews", "/mcp/security", "/mcp/code"}:
            response = JSONResponse({"detail": "This MCP endpoint has been retired. Connect to /mcp."}, status_code=404,
                                    headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        if scope["type"] != "http" or path not in MCP_PATHS:
            return await self.app(scope, receive, send)

        # This stateless service has no server-initiated events. An optional
        # standalone SSE stream would otherwise occupy a request slot until disconnect.
        if path == "/mcp" and scope["method"] in {"GET", "HEAD"}:
            response = Response(status_code=405, headers={"Allow": "POST, DELETE"})
            return await response(scope, receive, send)

        with self.lock:
            busy = self.active >= settings().mcp_max_concurrent_requests
            if busy:
                self.rejected[path] += 1
                self.overloaded_paths.add(path)
            else:
                self.active += 1
                self.active_by_path[path] += 1
                self.peak_by_path[path] = max(self.peak_by_path[path], self.active_by_path[path])
                self.admitted[path] += 1
            admissions, rejections, peak = self.admitted[path], self.rejected[path], self.peak_by_path[path]

        # Keep per-endpoint capacity visible without a log entry for every retry.
        count = rejections if busy else admissions
        if count & (count - 1) == 0:
            log.log(logging.WARNING if busy else logging.INFO,
                    "MCP admission endpoint=%s admitted=%d rejected=%d peak=%d", path, admissions, rejections, peak)
        if busy:
            response = JSONResponse({"detail": "MCP is busy. Retry shortly."}, status_code=429,
                                    headers={"Retry-After": "2", "Cache-Control": "no-store"})
            return await response(scope, receive, send)

        marker = mcp_request_path.set(path)
        try:
            buffered = await buffer_mcp_request_body(scope, receive, send)
            if buffered is not None:
                await self.app(scope, buffered, send)
        finally:
            mcp_request_path.reset(marker)
            with self.lock:
                self.active -= 1
                self.active_by_path[path] -= 1
                totals = []
                if self.active == 0 and self.overloaded_paths:
                    totals = [(endpoint, self.admitted[endpoint], self.rejected[endpoint], self.peak_by_path[endpoint])
                              for endpoint in self.overloaded_paths]
                    self.overloaded_paths.clear()
            for total in totals:
                log.info("MCP capacity recovered endpoint=%s admitted=%d rejected=%d peak=%d", *total)
