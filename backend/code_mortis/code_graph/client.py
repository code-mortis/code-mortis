"""Trusted worker transport and validation against the submitted snapshot manifest."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from bisect import bisect_right
from contextlib import suppress
from pathlib import PurePosixPath

from code_mortis.code_graph import errors
from code_mortis.code_graph.syntax import LANGUAGES, import_target, python_module_targets
from code_mortis.schemas.code_graph import (
    EXTRACTION_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    CodeGraphRequest,
    CodeGraphResult,
    source_lines,
)


class ParserUnavailable(errors.GraphError):
    def __init__(self, *, code="parser_unavailable"):
        super().__init__(code, "parser", *errors.PARSER_ERRORS[code])


class ExtractionCancelled(ValueError):
    pass


def validate_result(request: CodeGraphRequest, result: CodeGraphResult) -> CodeGraphResult:
    for key in ("schema_version", "repository_id", "commit_sha", "extractor_version", "policy_digest", "limits"):
        if getattr(result, key) != getattr(request, key):
            raise ValueError("Parser returned another snapshot identity")
    manifest = {item.path: item for item in request.files}
    if {item.path for item in result.files} != set(manifest):
        raise ValueError("Parser returned another file manifest")
    sources, lines, languages = {}, {}, {}
    for item in result.files:
        expected = manifest[item.path]
        if (item.blob_sha, item.size_bytes) != (expected.blob_sha, expected.size_bytes):
            raise ValueError("Parser returned another blob")
        if expected.exclusion:
            if item.status != "excluded" or item.reason != expected.exclusion or item.language is not None or item.line_count is not None:
                raise ValueError("Parser changed file exclusion")
            continue
        config = LANGUAGES.get(PurePosixPath(item.path).suffix)
        language = config[0] if config else None
        if item.language != language:
            raise ValueError("Parser returned another language")
        if config is None:
            if item.status != "unsupported" or item.line_count is not None:
                raise ValueError("Parser claimed unsupported syntax was parsed")
            continue
        source = expected.source()
        try:
            source.decode("utf-8")
        except UnicodeDecodeError:
            if item.status != "failed" or item.reason != "invalid_encoding" or item.line_count is not None:
                raise ValueError("Parser claimed invalid source was parsed") from None
            continue
        if item.status not in {"parsed", "partial"} or item.line_count != source_lines(source) or item.unresolved_calls is None:
            raise ValueError("Parser returned invalid file coverage")
        if item.diagnostic is not None:
            diagnostic = item.diagnostic
            before = source[:diagnostic.start_byte]
            if (diagnostic.start_byte > len(source)
                or diagnostic.line != before.count(b"\n") + 1
                or diagnostic.column != len(before.rsplit(b"\n", 1)[-1]) + 1):
                raise ValueError("Parser diagnostic does not match source location")
            before.decode("utf-8")
        sources[item.path], languages[item.path] = source, language
        lines[item.path] = [index for index, value in enumerate(source) if value == 10]
    available = {item.path for item in result.files if item.status not in {"excluded", "failed"}}
    for item in [*result.symbols, *result.imports]:
        source = sources[item.file_path]
        newlines = lines[item.file_path]
        if (item.start_line, item.end_line) != (bisect_right(newlines, item.start_byte - 1) + 1,
                                                bisect_right(newlines, item.end_byte - 2) + 1):
            raise ValueError("Parser ranges do not match source lines")
        if hasattr(item, "kind"):
            definitions = LANGUAGES[PurePosixPath(item.file_path).suffix][1].split()
            if languages[item.file_path] in {"javascript", "typescript", "tsx"}:
                definitions.append("variable_declarator")
            if item.kind not in definitions:
                raise ValueError("Parser returned an unsupported declaration kind")
        fragment = source[item.start_byte:item.end_byte].decode("utf-8")
        # Range validation includes UTF-8 boundaries and literal evidence from the admitted blob.
        evidence = item.name if hasattr(item, "name") else item.specifier
        if evidence not in fragment:
            raise ValueError("Parser fact is absent from its source range")
    python_modules = python_module_targets(manifest)
    for item in result.imports:
        target = import_target(item.file_path, item.specifier, languages[item.file_path], available,
                               python_modules=python_modules)
        if item.target_path != target:
            raise ValueError("Parser returned an unvalidated import target")
    return result


def _run(body, stopped, deadline):
    with tempfile.TemporaryFile() as request, tempfile.TemporaryFile() as result:
        request.write(body)
        request.seek(0)
        with subprocess.Popen(
            [sys.executable, "-IB", "-m", "code_mortis.code_graph.sandbox", str(os.getpid())],
            env={}, cwd="/", stdin=request, stdout=result, stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                while True:
                    if stopped():
                        raise ExtractionCancelled("Code graph extraction cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Code graph extraction exceeded the deadline")
                    try:
                        status = process.wait(timeout=min(0.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
        if status != 0:
            code = {2: "parser_invalid_request", 3: "parser_invalid_result", 4: "parser_storage_limit",
                    5: "parser_unavailable", -signal.SIGXFSZ: "parser_storage_limit"}.get(status, "parser_terminated")
            raise ParserUnavailable(code=code)
        result.seek(0)
        data = result.read(MAX_RESULT_BYTES + 1)
        if len(data) > MAX_RESULT_BYTES:
            raise ParserUnavailable(code="parser_invalid_result")
        return data


def extract(payload: CodeGraphRequest, stopped=lambda: False) -> CodeGraphResult:
    # Revalidate callers' models too: Pydantic model_copy/model_construct can bypass validators.
    payload = CodeGraphRequest.model_validate(payload.model_dump())
    body = payload.model_dump_json().encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise errors.failure("transport_limit")
    if stopped():
        raise ExtractionCancelled("Code graph extraction cancelled")
    deadline = time.monotonic() + EXTRACTION_SECONDS
    try:
        raw = _run(body, stopped, deadline)
        result = CodeGraphResult.model_validate_json(raw)
        if not {"schema_version", "extractor_version"} <= result.model_fields_set:
            raise ParserUnavailable(code="parser_invalid_result")
        result = validate_result(payload, result)
        if stopped():
            raise ExtractionCancelled("Code graph extraction cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("Parser validation exceeded the deadline")
        return result
    except (ExtractionCancelled, errors.GraphError):
        raise
    except TimeoutError:
        raise ParserUnavailable(code="parser_timeout") from None
    except OSError:
        raise ParserUnavailable() from None
    except ValueError:
        raise ParserUnavailable(code="parser_invalid_result") from None
