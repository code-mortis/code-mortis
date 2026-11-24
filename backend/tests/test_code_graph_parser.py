import base64
import errno
import json
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from code_mortis.code_graph import client, sandbox
from code_mortis.code_graph.extract import extract
from code_mortis.code_graph.syntax import LANGUAGES
from code_mortis.core import file_policy
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
                                        content_base64=base64.b64encode(raw).decode() if Path(path).suffix in LANGUAGES and not file_policy.excluded(path) else None,
                                        exclusion="excluded" if file_policy.excluded(path) else None)
                                   for path, text in files.items()
                                   for raw in [text.encode() if isinstance(text, str) else text]])


def test_multilanguage_parser_fixtures():
    source = json.loads((Path(__file__).parent / "fixtures/code_graph_parser.json").read_text())
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


@pytest.mark.parametrize("root", ["", "backend/", "packages/tool/src/"])
def test_python_absolute_imports_resolve_exact_local_modules(root):
    package = f"{root}code_mortis"
    payload = request({
        f"{package}/__init__.py": "",
        f"{package}/schemas/__init__.py": "",
        f"{package}/schemas/code_graph.py": "EXTRACTOR_VERSION = 'fixture'\n",
        f"{package}/worker.py": "from code_mortis.schemas.code_graph import EXTRACTOR_VERSION\n"
                                "from .schemas.code_graph import EXTRACTOR_VERSION\n"
                                "from unavailable_package.schema import Example\n",
    })
    result = client.validate_result(payload, extract(payload))
    assert [(item.specifier, item.target_path, item.provenance) for item in result.imports] == [
        ("code_mortis.schemas.code_graph", f"{package}/schemas/code_graph.py", "absolute_path"),
        (".schemas.code_graph", f"{package}/schemas/code_graph.py", "relative_path"),
        ("unavailable_package.schema", None, "unresolved"),
    ]
    tampered = result.model_dump()
    tampered["imports"][0]["target_path"] = f"{package}/__init__.py"
    with pytest.raises(ValueError, match="unvalidated import target"):
        client.validate_result(payload, CodeGraphResult.model_validate(tampered))


def test_python_absolute_imports_skip_ambiguous_and_unavailable_modules():
    payload = request({
        "backend/code_mortis/__init__.py": "",
        "backend/code_mortis/feature.py": "",
        "other/code_mortis/__init__.py": "",
        "other/code_mortis/feature.py": b"\xff",
        "backend/code_mortis/only_here.py": "",
        "backend/code_mortis/credential_settings.py": "",
        "backend/code_mortis/worker.py": "import code_mortis.feature, code_mortis.only_here\n"
                                         "import backend.code_mortis.only_here, backend.code_mortis.credential_settings\n",
    })
    result = client.validate_result(payload, extract(payload))
    assert [(item.specifier, item.target_path) for item in result.imports] == [
        ("code_mortis.feature", None),
        ("code_mortis.only_here", None),
        ("backend.code_mortis.only_here", "backend/code_mortis/only_here.py"),
        ("backend.code_mortis.credential_settings", None),
    ]


def test_python_absolute_imports_skip_shadowed_nested_modules():
    payload = request({
        "module.py": "",
        "module/nested.py": "",
        "src/shadowed/__init__.py": "",
        "shadowed/nested.py": "",
        "unshadowed/nested.py": "",
        "main.py": "import module, module.nested, shadowed.nested, unshadowed.nested\n",
    })
    result = client.validate_result(payload, extract(payload))
    assert [(item.specifier, item.target_path) for item in result.imports] == [
        ("module", "module.py"),
        ("module.nested", None),
        ("shadowed.nested", None),
        ("unshadowed.nested", "unshadowed/nested.py"),
    ]


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


@pytest.mark.parametrize("extension", ["ts", "tsx"])
def test_inline_import_types_preserve_definitions_ranges_and_runtime_calls(extension):
    source = '''import { helper } from "./helper";
interface Box { warnings?: import("./schema").Warning[]; }
type Alias = import("./schema").Warning[];
export function create(value: import("./schema").Warning[]) {
  const ref = useRef<import("./schema").Box | null>(null,);
  return helper(value);
}
export const callback = (value: import("./schema").Warning[]) => helper(value);
const lazy = import("./runtime").then(load);
'''.replace("\n", "\r\n")
    payload = request({f"café.{extension}": source, "helper.ts": "export function helper(value: unknown) {}"})
    result = client.validate_result(payload, extract(payload))
    file = result.files[0]
    assert file.status == "parsed" and file.diagnostic is None
    assert file.unresolved_calls == 5
    assert file.blob_sha == blob_sha(source.encode())
    assert [(item.specifier, item.target_path) for item in result.imports] == [("./helper", "helper.ts")]
    symbols = {item.name: item for item in result.symbols if item.file_path == file.path}
    assert set(symbols) == {"Box", "Alias", "create", "callback"}
    raw = source.encode()
    for name, prefix in [("Box", b"interface Box"), ("Alias", b"type Alias"),
                         ("create", b"function create"), ("callback", b"callback =")]:
        symbol = symbols[name]
        assert symbol.start_byte == raw.index(prefix)
        assert name.encode() in raw[symbol.start_byte:symbol.end_byte]
    assert b'import("./schema")' in raw[symbols["create"].start_byte:symbols["create"].end_byte]


@pytest.mark.parametrize("extension", ["ts", "tsx"])
@pytest.mark.parametrize("source", [
    'type X = import("m",).A[];',
    'interface Box { value?: import("m" extra).A[]; }',
    'interface Box { value?: import("m").; }',
    'function broken() { return useRef<import("m").T | null>(,); }',
    'function broken() { const value = import("m").T[]; }',
    'const text = \'import("m").T[]\'; function broken() { return value + ; }',
    '/* import("m").T[] */ function broken() { return value + ; }',
])
def test_import_type_recovery_does_not_accept_malformed_calls_or_runtime_syntax(extension, source):
    result = client.validate_result(payload := request({f"bad.{extension}": source}), extract(payload))
    assert result.files[0].status == "partial" and result.files[0].diagnostic is not None
    assert not any(item.name == "broken" for item in result.symbols)


def test_c_optional_expression_and_node_api_macro_preserve_all_source_facts():
    source = '''/* café */
#include "node_api.h"
static int valid_path(int value) {
  if (value
#ifdef _WIN32
      || windows_check()
#endif
  ) return accept();
  return reject();
}
NAPI_MODULE_INIT() {
#ifdef _WIN32
  windows_init();
#endif
  return exports;
}
'''.replace("\n", "\r\n")
    payload = request({"main.c": source})
    result = client.validate_result(payload, extract(payload))
    assert result.files[0].status == "parsed" and result.files[0].diagnostic is None
    assert result.files[0].unresolved_calls == 4
    assert [item.name for item in result.symbols] == ["valid_path"]
    symbol = result.symbols[0]
    assert source.encode()[symbol.start_byte:symbol.end_byte].decode() == source[source.index("static int"):source.index("\r\nNAPI_MODULE_INIT")]
    assert [item.specifier for item in result.imports] == ['#include "node_api.h"\r\n']


@pytest.mark.parametrize("source", [
    'int f() { return a\n#ifdef X\n ||\n#endif\n b; }',
    'int f() { return a\n#ifdef X\n || broken +\n#endif\n ; }',
    '#include "node_api.h"\nNAPI_MODULE_INIT() { return exports }',
    '#include "node_api.h"\nNAPI_MODULE_INIT() { const x = ; return exports; }',
    'NAPI_MODULE_INIT() { return exports; }',
    'const char *s = "#include \\"node_api.h\\"";\nNAPI_MODULE_INIT() { return exports; }',
    '/* #include "node_api.h" */\nNAPI_MODULE_INIT() { return exports; }',
    '#include "node_api.h"\nOTHER_MODULE_INIT() { return exports; }',
    'NAPI_MODULE_INIT() {}\n#include <node_api.h>',
    '#include <node_api.h>\n#undef NAPI_MODULE_INIT\nNAPI_MODULE_INIT() {}',
    '#include <node_api.h>\n# undef NAPI_MODULE_INIT // comment\nNAPI_MODULE_INIT() {}',
    '#include <node_api.h>\n#define NAPI_MODULE_INIT() x\nNAPI_MODULE_INIT() {}',
    '#include <node_api.h>\nNAPI_MODULE_INIT() {}\nNAPI_MODULE_INIT() {}',
    'int f() { return a\n#ifdef X\n || b\n#else\n || bad +\n#endif\n ; }',
])
def test_c_recovery_preserves_errors_in_optional_branches_and_macro_bodies(source):
    payload = request({"bad.c": source})
    result = client.validate_result(payload, extract(payload))
    assert result.files[0].status == "partial" and result.files[0].diagnostic is not None


@pytest.mark.parametrize("source", ["const café = ;\n", "function broken() {\n"])
def test_partial_files_record_first_error_location_without_source_text(source):
    payload = request({"problem.ts": source})
    result = client.validate_result(payload, extract(payload))
    diagnostic = result.files[0].diagnostic
    assert diagnostic is not None
    prefix = source.encode()[:diagnostic.start_byte]
    assert diagnostic.line == prefix.count(b"\n") + 1
    assert diagnostic.column == len(prefix.rsplit(b"\n", 1)[-1]) + 1
    assert "café" not in diagnostic.model_dump_json()
    assert result.files[0].status == "partial"


@pytest.mark.parametrize("patch", [{"line": 100}, {"column": 100}, {"start_byte": 999}])
def test_parser_diagnostic_location_is_validated_against_exact_source(patch):
    payload = request({"problem.ts": "const café = ;\n"})
    result = extract(payload).model_dump()
    result["files"][0]["diagnostic"].update(patch)
    with pytest.raises(ValueError, match="diagnostic does not match source"):
        client.validate_result(payload, CodeGraphResult.model_validate(result))


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
    with pytest.raises(ValueError, match="transport safety limit"):
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


@pytest.mark.parametrize("field", ["schema_version", "extractor_version"])
def test_transport_requires_explicit_response_versions(monkeypatch, field):
    payload = request()
    result = extract(payload).model_dump(exclude={field})
    monkeypatch.setattr(client, "_run", lambda *args: json.dumps(result).encode())
    with pytest.raises(client.ParserUnavailable) as failure:
        client.extract(payload)
    assert failure.value.failure.code == "parser_invalid_result"


@pytest.mark.parametrize(("error", "code"), [
    (OSError("private worker details"), "parser_unavailable"),
    (TimeoutError("private worker details"), "parser_timeout"),
    (ValueError("private worker details"), "parser_invalid_result"),
])
def test_worker_failure_messages_do_not_disclose_private_details(monkeypatch, error, code):
    def fail(*args):
        raise error

    monkeypatch.setattr(client, "_run", fail)
    with pytest.raises(client.ParserUnavailable) as failure:
        client.extract(request())
    assert failure.value.failure.code == code
    assert "private worker details" not in str(failure.value)


@pytest.mark.parametrize(("import_error", "read_error", "exit_code"), [
    pytest.param(MemoryError(), None, 5, id="grammar-import-memory-error"),
    pytest.param(None, OSError(errno.EIO, "stdin failed"), 3, id="stdin-io-error"),
])
def test_sandbox_returns_controlled_error_status(monkeypatch, import_error, read_error, exit_code):
    monkeypatch.setattr(sandbox, "ctypes", SimpleNamespace(CDLL=lambda _: SimpleNamespace(prctl=lambda *_: 0)))
    monkeypatch.setattr(sandbox, "os", SimpleNamespace(getppid=lambda: 42, read=Mock(return_value=b"", side_effect=read_error)))
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(argv=["sandbox", "42"]))
    monkeypatch.setattr(sandbox.resource, "setrlimit", Mock())
    monkeypatch.setattr(sandbox, "importlib", SimpleNamespace(import_module=Mock(side_effect=import_error)))
    monkeypatch.setattr(sandbox, "restrict_syscalls", Mock())
    assert sandbox.main() == exit_code


@pytest.mark.parametrize("cancel", [True, False])
def test_parser_is_reaped_on_cancel_or_deadline(monkeypatch, cancel):
    spawn = subprocess.Popen
    children = []

    def slow_child(args, **kwargs):
        assert kwargs["env"] == {} and kwargs["cwd"] == "/"
        process = spawn([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(client.subprocess, "Popen", slow_child)
    monkeypatch.setattr(client, "EXTRACTION_SECONDS", 0.2)
    with pytest.raises(client.ExtractionCancelled if cancel else client.ParserUnavailable) as failure:
        client.extract(request(), stopped=lambda: cancel and bool(children))
    assert children[0].returncode == -signal.SIGKILL
    if not cancel:
        assert failure.value.failure.code == "parser_timeout"
