"""Run by compose.code-graph.test.yaml inside a disposable, private-network client."""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from code_mortis.code_graph import client
from code_mortis.schemas.code_graph import MAX_REQUEST_BYTES, CodeGraphRequest, blob_sha

URL = "http://code-graph-parser:8091"
TOKEN = os.environ["CM_CODE_GRAPH_TOKEN"]


def payload(files):
    return CodeGraphRequest(repository_id=1, commit_sha="a" * 40, policy_digest="b" * 64,
                            files=[dict(path=path, blob_sha=blob_sha(source), size_bytes=len(source),
                                        content_base64=base64.b64encode(source).decode())
                                   for path, source in files.items()])


def raw(body, token=TOKEN, headers=None):
    request = Request(URL + "/extract", body, headers={"Authorization": "Bearer " + token,
                                                      "Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(request, timeout=130) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def run(mode):
    if mode in {"cancel", "deadline"}:
        # The host test SIGSTOPs the real child while this bounded source is being parsed.
        request = payload({"busy.py": b"def f():\n    return 1\n" * 25000})
        if mode == "deadline":
            started = time.monotonic()
            status, _ = raw(request.model_dump_json().encode())
            assert status == 504, status
            assert 118 <= time.monotonic() - started < 130
        else:
            started = time.monotonic()
            try:
                asyncio.run(client._request(request, request.model_dump_json().encode(), URL, TOKEN,
                                            lambda: time.monotonic() - started > 3))
            except client.ExtractionCancelled:
                pass
            else:
                raise AssertionError("Expected cancellation")
        print(json.dumps({"mode": mode, "passed": True}), flush=True)
        return
    files = {path: text.encode() for path, text in json.loads(Path("/checks/fixtures.json").read_text()).items()}
    request = payload(files)
    result = asyncio.run(client._request(request, request.model_dump_json().encode(), URL, TOKEN, lambda: False))
    assert len(result.files) == len(files)
    assert len(result.symbols) == 37
    assert len(result.imports) == 20
    assert raw(b"{}", token="wrong")[0] == 401
    assert raw(b"not json")[0] == 422
    for version in ("schema_version", "extractor_version"):
        missing = request.model_dump()
        del missing[version]
        assert raw(json.dumps(missing).encode())[0] == 422
    assert raw(b"{}", headers={"Content-Encoding": "gzip"})[0] == 415
    assert raw(b"{}", headers={"Content-Length": str(MAX_REQUEST_BYTES + 1)})[0] == 413
    request = payload({f"{i}.py": b"#" + b"a" * (1024 * 1024 - 2) + b"\n" for i in range(8)})
    result = asyncio.run(client._request(request, request.model_dump_json().encode(), URL, TOKEN, lambda: False))
    assert len(result.files) == 8 and all(i.status == "parsed" for i in result.files)
    assert not result.symbols
    print(json.dumps({"mode": mode, "fixtures": len(files), "symbols": 37, "imports": 20,
                      "source_limit_bytes": 8 * 1024 * 1024, "passed": True}), flush=True)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
