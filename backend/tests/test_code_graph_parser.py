import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
import pytest

from code_mortis.code_graph import client, server
from code_mortis.code_graph.extract import extract
from code_mortis.schemas.code_graph import (
    MAX_FILE_BYTES,
    MAX_FILES,
    CodeGraphRequest,
    CodeGraphResult,
    blob_sha,
)


def request(files=None):
    files = files if files is not None else {"main.py": "def café():\r\n    return 1\r\n"}
    return CodeGraphRequest(repository_id=1, commit_sha="a" * 40, policy_digest="b" * 64,
                            files=[dict(path=path, blob_sha=blob_sha(raw), size_bytes=len(raw),
                                        content_base64=base64.b64encode(raw).decode())
                                   for path, text in files.items()
                                   for raw in [text.encode() if isinstance(text, str) else text]])


def test_phase0_fixtures():
    source = json.loads((Path(__file__).parents[2] / "scripts/code_graph_eval/fixtures.json").read_text())
    payload = request(source)
    result = client.validate_result(payload, extract(payload))
    files = {item.path: item for item in result.files}
    assert files["safety/broken.py"].status == "partial"
    assert not any(item.name == "broken" for item in result.symbols)
    assert files["safety/unsupported.unknown"].status == "unsupported"
    assert files["python/use.py"].unresolved_calls == 5
    assert len({item.key for item in result.symbols}) == len(result.symbols)
    pairs = {(item.file_path, item.target_path) for item in result.imports}
    for language, suffix in (("python", "py"), ("typescript", "ts"), ("javascript", "js")):
        assert (f"{language}/use.{suffix}", f"{language}/barrel.{suffix}") in pairs
        assert (f"{language}/barrel.{suffix}", f"{language}/target.{suffix}") in pairs
    for language in ("go", "rust", "java", "c", "cpp", "ruby", "csharp", "php", "bash"):
        assert {"helper", "entry"} <= {i.name for i in result.symbols if i.file_path.startswith(language + "/")}
    for path in ("typescript/component.tsx", "javascript/component.jsx"):
        assert any(i.name == "Component" and i.file_path == path for i in result.symbols)
    assert not Path("/tmp/CODE_GRAPH_REPO_EXECUTED").exists()


@pytest.mark.parametrize("text", ["def café():\n    pass", "\ufeffdef café():\r\n    pass\r\n", "\n\ndef café():\n    pass\n"])
def test_exact_utf8_and_line_ranges(text):
    payload = request({"main.py": text})
    result = client.validate_result(payload, extract(payload))
    symbol = result.symbols[0]
    assert symbol.name == "café"
    assert text.encode()[symbol.start_byte:symbol.end_byte].decode().startswith("def café")
    assert symbol.end_line == payload.files[0].source().count(b"\n") + (not text.endswith("\n"))


@pytest.mark.parametrize("extension", ["jsx", "tsx"])
@pytest.mark.parametrize("markup", ["<h2>Limits & rules</h2>", "<>café & té</>",
                                   "<div>A & <b>B & C</b></div>", "<h2>&amp; &#38; &unknown; &</h2>"])
def test_jsx_literal_ampersands_preserve_original_definition_ranges(extension, markup):
    source = f"export function Section() {{\r\n  return {markup};\r\n}}\r\n"
    payload = request({f"section.{extension}": source})
    result = client.validate_result(payload, extract(payload))
    assert result.files[0].status == "parsed" and result.files[0].reason is None
    symbol = next(item for item in result.symbols if item.name == "Section")
    assert source.encode()[symbol.start_byte:symbol.end_byte].decode() == source.removeprefix("export ").rstrip("\r\n")
    assert result.files[0].blob_sha == blob_sha(source.encode())


@pytest.mark.parametrize("source", [
    "function Broken() { return <h2>{a & }</h2>; }",
    "function Broken() { return <h2>Limits & rules{</h2>; }",
    "function Section() { return <h2>Limits & rules</h2>; }\nconst broken = ;",
    "function Broken() { return value & ; }",
])
def test_jsx_ampersand_recovery_keeps_real_syntax_errors_partial(source):
    payload = request({"section.tsx": source})
    result = client.validate_result(payload, extract(payload))
    assert result.files[0].status == "partial" and result.files[0].reason == "syntax_error"
    assert not any(item.name == "Broken" for item in result.symbols)


def test_ambiguous_jsx_recovery_keeps_parser_warning():
    payload = request({"section.tsx": "function Section() { return <>café & té\r\n& more</>; }"})
    result = client.validate_result(payload, extract(payload))
    assert result.files[0].status == "partial" and result.files[0].reason == "syntax_error"


def test_exclusions_invalid_encoding_multi_imports_aliases_and_ambiguous_targets():
    payload = request({
        "a.py": "import one as first, two\nfrom .target import x as y\n",
        "target.py": "x = 1\n", "bad.py": b"\xff", "binary.bin": b"\xff", "empty.py": "",
        "use.ts": "import x from './bad.py';\nimport y from './same';\nimport z from '@app/target';\nexport const x = 1;\n",
        "same.ts": "", "same.js": "",
    }).model_dump()
    payload["files"].append(dict(path="hidden.py", blob_sha="c" * 40, size_bytes=99999999, exclusion="excluded"))
    payload = CodeGraphRequest.model_validate(payload)
    result = client.validate_result(payload, extract(payload))
    assert [i.specifier for i in result.imports if i.file_path == "a.py"] == ["one", "two", ".target"]
    assert [i.target_path for i in result.imports if i.file_path == "use.ts"] == [None, None, None]
    files = {i.path: i for i in result.files}
    assert files["bad.py"].reason == "invalid_encoding"
    assert files["binary.bin"].status == "unsupported"
    assert files["hidden.py"].status == "excluded"
    assert files["empty.py"].line_count == 0


def test_grouped_go_and_empty_js_imports_preserve_other_facts():
    modules = [f"example.com/company/project/packages/module{i}" for i in range(50)]
    source = 'package example\nimport "fmt"\nimport (\n' + "".join(
        f' p{i} "{module}"\n' for i, module in enumerate(modules)
    ) + ')\nimport `os`\nfunc main() {}\n'
    assert len(source) > 2000
    payload = request({"main.go": source, "empty.js": 'import "";\nexport * from \'\';\n',
                       "good.py": "def good():\n    pass\n"})
    result = client.validate_result(payload, extract(payload))
    assert all(file.status == "parsed" for file in result.files)
    assert {symbol.name for symbol in result.symbols} == {"main", "good"}
    assert [item.specifier for item in result.imports if item.file_path == "main.go"] == ["fmt", *modules, "os"]
    assert [item.specifier for item in result.imports if item.file_path == "empty.js"] == ['""', "''"]
    assert all(item.provenance == "unresolved" and item.target_path is None for item in result.imports)


@pytest.mark.parametrize("change", [
    {"blob_sha": "0" * 40}, {"size_bytes": 4}, {"content_base64": "$$"},
    {"content_base64": None}, {"exclusion": "excluded"}, {"exclusion": "file_too_large", "content_base64": None},
    {"path": "../escape.py"}, {"path": ".git/config"},
])
def test_source_manifest_rejected(change):
    data = request().model_dump()
    data["files"][0].update(change)
    with pytest.raises(ValueError):
        CodeGraphRequest.model_validate(data)


def test_limits_before_transfer(monkeypatch):
    data = request().model_dump()
    for change in [{"files": data["files"] * (MAX_FILES + 1)}, {"schema_version": "next"}, {"extractor_version": "next"}]:
        with pytest.raises(ValueError):
            CodeGraphRequest.model_validate(data | change)
    with pytest.raises(ValueError):
        request({"big.py": b"a" * (MAX_FILE_BYTES + 1)})
    with pytest.raises(ValueError, match="source byte limit"):
        request({f"{i}.py": b"a" * MAX_FILE_BYTES for i in range(9)})
    monkeypatch.setattr(client, "MAX_REQUEST_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        client.extract(request())


@pytest.mark.parametrize("field,value", [("repository_id", 2), ("commit_sha", "c" * 40),
                                         ("extractor_version", "wrong"), ("policy_digest", "d" * 64)])
def test_untrusted_identity(field, value):
    payload = request()
    result = extract(payload).model_dump()
    result[field] = value
    with pytest.raises(ValueError, match="identity"):
        client.validate_result(payload, CodeGraphResult.model_validate(result))


@pytest.mark.parametrize("mutation", ["path", "blob", "size", "lines", "range", "name", "language", "status", "missing", "kind"])
def test_untrusted_file_and_fact_claims(mutation):
    payload = request()
    result = extract(payload).model_dump()
    if mutation == "path":
        result["files"][0]["path"] = "other.py"
        result["symbols"][0]["file_path"] = "other.py"
    elif mutation in {"blob", "size", "lines", "language", "status"}:
        key, value = {"blob": ("blob_sha", "c" * 40), "size": ("size_bytes", 99),
                      "lines": ("line_count", 99), "language": ("language", "rust"),
                      "status": ("status", "unsupported")}[mutation]
        result["files"][0][key] = value
    elif mutation == "range":
        result["symbols"][0]["start_line"] = 2
    elif mutation == "kind":
        result["symbols"][0]["kind"] = "invented_kind"
    elif mutation == "name":
        result["symbols"][0]["name"] = "invented"
    else:
        result["files"] = []
        result["symbols"] = []
    with pytest.raises(ValueError):
        client.validate_result(payload, CodeGraphResult.model_validate(result))


def test_untrusted_import_resolution():
    payload = request({"a.py": "from .b import value\n", "b.py": "", "c.py": ""})
    result = extract(payload).model_dump()
    result["imports"][0]["target_path"] = "c.py"
    with pytest.raises(ValueError, match="target"):
        client.validate_result(payload, CodeGraphResult.model_validate(result))


def test_transport_bounds_errors_and_cancellation(monkeypatch):
    payload = request()
    original_client = httpx.AsyncClient
    responses = [httpx.Response(200, stream=httpx.ByteStream(extract(payload).model_dump_json().encode())),
                 httpx.Response(200, headers={"content-length": str(client.MAX_RESULT_BYTES + 1)}, stream=httpx.ByteStream(b"{}")),
                 httpx.Response(200, stream=httpx.ByteStream(b"x" * 50)), httpx.Response(200, stream=httpx.ByteStream(b"invalid json")),
                 httpx.Response(200, headers={"content-encoding": "gzip"}), httpx.Response(409),
                 httpx.Response(302, headers={"location": "http://elsewhere"})]
    async def handle(req):
        assert req.headers["accept-encoding"] == "identity"
        assert req.headers["authorization"] == "Bearer test"
        return responses.pop(0)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw))
    def run(stopped=lambda: False):
        return asyncio.run(client._request(payload, payload.model_dump_json().encode(), "http://parser", "test", stopped))
    assert run().repository_id == 1
    with pytest.raises(ValueError):
        run()
    monkeypatch.setattr(client, "MAX_RESULT_BYTES", 40)
    for _ in range(5):
        with pytest.raises(ValueError):
            run()
    with pytest.raises(client.ExtractionCancelled):
        run(lambda: True)
    async def hanging(req):
        await asyncio.sleep(10)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(hanging), **kw))
    monkeypatch.setattr(client, "EXTRACTION_SECONDS", 0.1)
    with pytest.raises(TimeoutError):
        run()


def test_server_body_and_single_slot(monkeypatch):
    monkeypatch.setenv("CM_CODE_GRAPH_TOKEN", "test")
    async def exercise():
        app = server.ParserApp()
        events = []
        pending = asyncio.Queue()
        scope = {"path": "/extract", "method": "POST", "headers": [
            (b"authorization", b"Bearer test"), (b"content-type", b"application/json")]}
        async def send(event):
            events.append(event)
        task = asyncio.create_task(app(scope, pending.get, send))
        await asyncio.sleep(0)
        await app(scope, pending.get, send)
        assert events[0]["status"] == 409
        await pending.put({"type": "http.request", "body": b"x" * 16, "more_body": True})
        monkeypatch.setattr(server, "MAX_REQUEST_BYTES", 10)
        await task
        assert events[-2]["status"] == 413
        assert not app.active
        events.clear()
        await app(scope | {"headers": []}, pending.get, send)
        assert events[0]["status"] == 401
    asyncio.run(exercise())


def test_real_process_deadline_and_disconnect(monkeypatch):
    monkeypatch.setenv("CM_CODE_GRAPH_TOKEN", "test")
    original_spawn = asyncio.create_subprocess_exec
    children = []
    async def spawn(*args, **kwargs):
        # A trusted sleeping process simulates a stuck native parser without a production test hook.
        process = await original_spawn(args[0], "-c", "import time; time.sleep(30)", **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(server, "EXTRACTION_SECONDS", 0.3)
    async def exercise(disconnect):
        app, queue, events = server.ParserApp(), asyncio.Queue(), []
        await queue.put({"type": "http.request", "body": request().model_dump_json().encode()})
        async def send(event):
            events.append(event)
        scope = {"path": "/extract", "method": "POST", "headers": [
            (b"authorization", b"Bearer test"), (b"content-type", b"application/json")]}
        task = asyncio.create_task(app(scope, queue.get, send))
        while len(children) < (2 if disconnect else 1):
            await asyncio.sleep(0.01)
        if disconnect:
            await queue.put({"type": "http.disconnect"})
        await task
        assert children[-1].returncode == -9
        assert not app.active
        if not disconnect:
            assert events[0]["status"] == 504
    started = time.monotonic()
    asyncio.run(exercise(False))
    asyncio.run(exercise(True))
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("version", ["schema_version", "extractor_version"])
def test_transport_requires_explicit_response_versions(monkeypatch, version):
    payload = request()
    result = extract(payload).model_dump()
    result.pop(version)
    original_client = httpx.AsyncClient
    async def handle(req):
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(result).encode()))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError, match="omitted|Field required"):
        asyncio.run(client._request(payload, payload.model_dump_json().encode(), "http://parser", "test", lambda: False))


def test_timeout_during_spawn_still_reaps_child(monkeypatch):
    monkeypatch.setenv("CM_CODE_GRAPH_TOKEN", "test")
    original_spawn, children = asyncio.create_subprocess_exec, []
    async def spawn(*args, **kwargs):
        process = await original_spawn(args[0], "-c", "import time; time.sleep(30)", **kwargs)
        children.append(process)
        await asyncio.sleep(0.1)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(server, "EXTRACTION_SECONDS", 0.05)
    async def exercise():
        app, events = server.ParserApp(), []
        async def receive():
            return {"type": "http.request", "body": b"{}"}
        async def send(event):
            events.append(event)
        await app({"path": "/extract", "method": "POST", "headers": [
            (b"authorization", b"Bearer test"), (b"content-type", b"application/json")]}, receive, send)
        assert events[0]["status"] == 504
        assert children[0].returncode == -9
        assert not app.active
    asyncio.run(exercise())
