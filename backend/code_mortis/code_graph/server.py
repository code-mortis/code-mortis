"""Private ASGI transport. One disposable parser process, bounded before JSON decoding."""

import asyncio
import contextlib
import os
import secrets
import signal
import sys
import tempfile
from pathlib import Path

from code_mortis.schemas.code_graph import EXTRACTION_SECONDS, MAX_REQUEST_BYTES, MAX_RESULT_BYTES


class Rejected(Exception):
    def __init__(self, status):
        self.status = status


async def reply(send, status, body=b""):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class ParserApp:
    def __init__(self):
        self.active = False

    async def __call__(self, scope, receive, send):
        token = os.environ.get("CM_CODE_GRAPH_TOKEN", "")
        headers = dict(scope["headers"])
        if not token or not secrets.compare_digest(headers.get(b"authorization", b""), ("Bearer " + token).encode()):
            return await reply(send, 401)
        if scope["path"] == "/health" and scope["method"] == "GET":
            return await reply(send, 200, b'{"status":"ok"}')
        if scope["path"] != "/extract" or scope["method"] != "POST":
            return await reply(send, 404)
        if self.active:
            return await reply(send, 409)
        self.active = True
        process = None
        disconnected = None
        waited = None
        response_started = False
        try:
            async with asyncio.timeout(EXTRACTION_SECONDS):
                if headers.get(b"content-encoding", b"identity") != b"identity":
                    raise Rejected(415)
                if headers.get(b"content-type", b"").split(b";")[0] != b"application/json":
                    raise Rejected(415)
                if b"content-length" in headers:
                    try:
                        size = int(headers[b"content-length"])
                    except ValueError:
                        raise Rejected(400) from None
                    if not 0 <= size <= MAX_REQUEST_BYTES:
                        raise Rejected(413)
                with tempfile.TemporaryDirectory(prefix="code-graph-") as directory:
                    request_path, result_path = Path(directory) / "request.json", Path(directory) / "result.json"
                    size = 0
                    with request_path.open("wb") as stream:
                        while True:
                            event = await receive()
                            if event["type"] == "http.disconnect":
                                return
                            chunk = event.get("body", b"")
                            size += len(chunk)
                            if size > MAX_REQUEST_BYTES:
                                raise Rejected(413)
                            stream.write(chunk)
                            if not event.get("more_body", False):
                                break
                    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                        sys.executable, "-m", "code_mortis.code_graph.extract", str(request_path), str(result_path),
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
                    ))
                    try:
                        process = await asyncio.shield(spawning)
                    except asyncio.CancelledError:
                        process = await spawning
                        raise
                    disconnected = asyncio.create_task(receive())
                    waited = asyncio.create_task(process.wait())
                    done, _ = await asyncio.wait({disconnected, waited}, return_when=asyncio.FIRST_COMPLETED)
                    if disconnected in done:
                        return
                    if process.returncode != 0:
                        raise Rejected(422 if process.returncode == 2 else 502)
                    if not result_path.is_file() or result_path.stat().st_size > MAX_RESULT_BYTES:
                        raise Rejected(502)
                    # Keep the slot until delivery finishes, so slow readers cannot accumulate responses.
                    response_started = True
                    await send({"type": "http.response.start", "status": 200,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(result_path.stat().st_size).encode())]})
                    with result_path.open("rb") as stream:
                        while chunk := stream.read(65536):
                            await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    await send({"type": "http.response.body", "body": b""})
        except TimeoutError:
            if response_started:
                raise
            await reply(send, 504)
        except Rejected as exc:
            await reply(send, exc.status)
        except OSError:
            if response_started:
                raise
            await reply(send, 502)
        finally:
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            for task in (disconnected, waited):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            self.active = False


app = ParserApp()
