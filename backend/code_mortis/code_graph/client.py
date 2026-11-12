"""Trusted worker transport and validation against the submitted snapshot manifest."""

import asyncio
import time
from bisect import bisect_right
from pathlib import PurePosixPath

import httpx

from code_mortis.code_graph import errors
from code_mortis.code_graph.syntax import LANGUAGES, import_target
from code_mortis.schemas.code_graph import (
    EXTRACTION_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    CodeGraphRequest,
    CodeGraphResult,
    source_lines,
)


class ParserUnavailable(errors.GraphError):
    def __init__(self, message="Parser unavailable", *, code="parser_unavailable"):
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
    for item in result.imports:
        target = import_target(item.file_path, item.specifier, languages[item.file_path], available)
        if item.target_path != target:
            raise ValueError("Parser returned an unvalidated import target")
    return result


async def _request(payload, body, url, token, stopped):
    deadline = time.monotonic() + EXTRACTION_SECONDS

    async def exchange():
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=EXTRACTION_SECONDS) as client:
            async with client.stream("POST", url.rstrip("/") + "/extract", content=body,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                              "Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    code = {401: "parser_authentication", 403: "parser_authentication", 409: "parser_busy",
                            413: "transport_limit", 422: "parser_invalid_request", 504: "parser_timeout",
                            502: "parser_terminated", 507: "parser_storage_limit"}.get(response.status_code, "parser_invalid_result")
                    raise ParserUnavailable(code=code)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ParserUnavailable(code="parser_invalid_result")
                length = response.headers.get("content-length")
                if length is not None and not 0 <= int(length) <= MAX_RESULT_BYTES:
                    raise ParserUnavailable(code="parser_invalid_result")
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    if len(raw) + len(chunk) > MAX_RESULT_BYTES:
                        raise ParserUnavailable(code="parser_invalid_result")
                    raw.extend(chunk)
                result = CodeGraphResult.model_validate_json(raw)
                if not {"schema_version", "extractor_version"} <= result.model_fields_set:
                    raise ParserUnavailable(code="parser_invalid_result")
                result = validate_result(payload, result)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Parser validation exceeded the deadline")
                return result

    task = asyncio.create_task(exchange())
    try:
        async with asyncio.timeout(EXTRACTION_SECONDS):
            while not task.done():
                if stopped():
                    raise ExtractionCancelled("Code graph extraction cancelled")
                await asyncio.wait({task}, timeout=0.1)
            if stopped():
                raise ExtractionCancelled("Code graph extraction cancelled")
            return await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def extract(payload: CodeGraphRequest, stopped=lambda: False) -> CodeGraphResult:
    from code_mortis.core.config import settings

    # Revalidate callers' models too: Pydantic model_copy/model_construct can bypass validators.
    payload = CodeGraphRequest.model_validate(payload.model_dump())
    body = payload.model_dump_json().encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise errors.failure("transport_limit")
    config = settings()
    if not config.code_graph_parser_url or not config.code_graph_token:
        raise ParserUnavailable("Code graph parser is not configured")
    try:
        return asyncio.run(_request(payload, body, config.code_graph_parser_url, config.code_graph_token, stopped))
    except (ExtractionCancelled, errors.GraphError):
        raise
    except (httpx.TimeoutException, TimeoutError):
        raise ParserUnavailable(code="parser_timeout") from None
    except httpx.HTTPError:
        raise ParserUnavailable() from None
    except ValueError:
        raise ParserUnavailable(code="parser_invalid_result") from None
