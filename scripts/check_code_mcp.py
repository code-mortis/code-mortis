"""Real external-client check. Run with backend's uv environment; browser consent is manual.

Creates a DCR client, holds tokens only in memory and revokes the temporary grant on exit.
Never imports application code, starts indexing, invokes models or prints source/token bodies.
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation


async def navigate(origin, token, repository_id, path):
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=60) as http:
        async with streamable_http_client(origin + "/mcp/code", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1], client_info=Implementation(name="code-graph-check", version="1")) as session:
                await session.initialize()
                catalog = await session.list_tools()
                names = [tool.name for tool in catalog.tools]
                assert len(names) == 7 and all(name.startswith("code_") for name in names)

                async def call(name, request=None):
                    result = await session.call_tool(name, {"request": request} if request is not None else {})
                    assert not result.is_error, f"{name} failed; inspect instance diagnostics"
                    assert result.structured_content, f"{name} omitted its structured result"
                    return result.structured_content

                repos = await call("code_list_repositories")
                assert repository_id in [repo["repository_id"] for repo in repos["items"]]
                status = await call("code_index_status", {"repository_id": repository_id})
                assert status["status"] in {"ready", "partial"}, "The selected repository needs a usable snapshot"
                revision = {"repository_id": repository_id, "commit_sha": status["commit_sha"]}
                search = await call("code_search", {**revision, "paths": [path], "limit": 10})
                assert search["items"], "Choose a path with indexed definitions"
                symbol = await call("code_get_symbol", {**revision, "symbol_key": search["items"][0]["key"]})
                assert symbol["items"]
                relations = await call("code_get_relations", {**revision, "paths": [path], "limit": 100})
                assert relations["items"], "Choose a path with resolved relative file imports"
                edge = next((edge for edge in relations["items"] if edge["target_path"].endswith("/button.tsx")), relations["items"][0])
                source = await call("code_get_source", {**revision, "path": edge["target_path"]})
                assert source["source_state"] == "available" and source["items"]
                assert all(result["commit_sha"] == status["commit_sha"] and result["snapshot_id"] == status["snapshot_id"]
                           for result in (search, symbol, relations, source))
                excerpt = source["items"][0]
                assert excerpt["end_byte"] - excerpt["start_byte"] == len(excerpt["text"].encode())
                return {"client": "MCP Python SDK", "client_version": version("mcp"), "transport": "Streamable HTTP",
                        "oauth": "DCR + S256 PKCE + manual browser consent", "tools": names, **revision,
                        "snapshot_id": status["snapshot_id"], "status": status["status"], "searched_path": path,
                        "symbol_key": search["items"][0]["key"], "relation": edge, "source_path": excerpt["path"],
                        "source_blob_sha": excerpt["blob_sha"], "source_bytes": len(excerpt["text"].encode()),
                        "source_excerpt_sha256": hashlib.sha256(excerpt["text"].encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://localhost:5173")
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--path", default="frontend/src/mcp.tsx")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    parsed = urlsplit(origin)
    assert not parsed.username and not parsed.password and parsed.path == "" and not parsed.query and not parsed.fragment
    assert parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
    state, verifier, returned = secrets.token_urlsafe(32), secrets.token_urlsafe(48), {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            values = parse_qs(urlsplit(self.path).query)
            if (urlsplit(self.path).path != "/callback" or not hmac.compare_digest(values.get("state", [""])[0], state)
                or values.get("iss") != [origin]):
                self.send_error(400)
                return
            returned.update(values)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authorization received. You can close this tab. The read-only check will revoke its grant when finished.")

        def log_message(self, *args):
            pass  # Authorization codes must never enter callback logs.

    token = None
    with HTTPServer(("127.0.0.1", 0), Callback) as server, httpx2.Client(timeout=30) as http:
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        registration = http.post(origin + "/oauth/register", json={"client_name": "Code graph verification (MCP Python SDK)",
                                 "redirect_uris": [redirect], "scope": "code:read"})
        assert registration.status_code == 201, "Enable Code MCP in Settings before starting this check"
        client_id = registration.json()["client_id"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = origin + "/oauth/authorize?" + urlencode({"client_id": client_id, "redirect_uri": redirect,
            "response_type": "code", "resource": origin + "/mcp/code", "scope": "code:read", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        print("Open this URL and approve only the intended repository in your browser:\n" + url, flush=True)
        server.timeout = 1
        deadline = time.monotonic() + 600
        while not returned and time.monotonic() < deadline:
            server.handle_request()
        assert returned.get("code"), "Browser consent was denied or timed out"
        response = http.post(origin + "/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id,
            "code": returned["code"][0], "redirect_uri": redirect, "resource": origin + "/mcp/code", "code_verifier": verifier})
        assert response.status_code == 200, "OAuth code exchange failed"
        token = response.json()["access_token"]
        try:
            report = asyncio.run(navigate(origin, token, args.repository_id, args.path))
        finally:
            revoked = http.post(origin + "/oauth/revoke", data={"client_id": client_id, "token": token})
            assert revoked.status_code == 200, "Revoke the verification grant in Settings"
        report.update(checked_at=datetime.now(UTC).isoformat(), origin=origin, grant_revoked=True,
                      model_calls=0, source_bodies_recorded=False)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print("Code navigation passed; temporary grant revoked. Evidence: " + str(args.output), flush=True)


if __name__ == "__main__":
    main()
