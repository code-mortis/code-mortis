"""Parse supplied bytes only. Never materialize or execute repository files."""

import importlib
import resource
import sys
from pathlib import Path, PurePosixPath

from tree_sitter import Language, Parser

from code_mortis.code_graph.syntax import LANGUAGES, descendants, import_target
from code_mortis.schemas.code_graph import (
    MAX_FACTS,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    CodeGraphFile,
    CodeGraphImport,
    CodeGraphRequest,
    CodeGraphResult,
    CodeGraphSymbol,
    source_lines,
)


def location(path, node):
    return dict(file_path=path, start_byte=node.start_byte, end_byte=node.end_byte,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + (node.end_point.column > 0))


def extract(request: CodeGraphRequest) -> CodeGraphResult:
    parsers, files, symbols, imports = {}, [], [], []
    for item in request.files:
        metadata = dict(path=item.path, blob_sha=item.blob_sha, size_bytes=item.size_bytes)
        if item.exclusion:
            files.append(CodeGraphFile(**metadata, status="excluded", reason=item.exclusion))
            continue
        config = LANGUAGES.get(PurePosixPath(item.path).suffix)
        if config is None:
            files.append(CodeGraphFile(**metadata, status="unsupported", reason="unsupported_language"))
            continue
        language, definitions, calls, import_kinds = config
        source = item.source()
        try:
            source.decode("utf-8")
        except UnicodeDecodeError:
            files.append(CodeGraphFile(**metadata, language=language, status="failed", reason="invalid_encoding"))
            continue
        if language not in parsers:
            package = "typescript" if language == "tsx" else language
            module = importlib.import_module("tree_sitter_" + package)
            factory = "language_" + language if language in {"typescript", "tsx", "php"} else "language"
            parsers[language] = Parser(Language(getattr(module, factory)()))
        root = parsers[language].parse(source).root_node
        count = 0
        for node in descendants(root):
            if node.has_error:
                continue
            value = node.child_by_field_name("value") if node.type == "variable_declarator" else None
            if node.type in definitions.split() or (value is not None and value.type in {"arrow_function", "function_expression"}):
                identifier = node.child_by_field_name("name")
                if identifier is None and node.type == "function_definition":
                    declarator = node.child_by_field_name("declarator")
                    if declarator is not None:
                        identifier = next((n for n in descendants(declarator) if n.type in {"identifier", "field_identifier"}), None)
                if identifier is not None:
                    if len(symbols) >= MAX_FACTS:
                        raise ValueError("Symbol count exceeds the result limit")
                    symbols.append(CodeGraphSymbol(**location(item.path, node), name=identifier.text.decode(), kind=node.type))
            count += node.type in calls.split()
            if node.type not in import_kinds.split():
                continue
            if language == "python":
                if node.type == "import_statement":
                    # Each imported name has its own source range, including multi-import statements.
                    sites = [(n, n.child_by_field_name("name") if n.type == "aliased_import" else n)
                             for n in node.children_by_field_name("name")]
                else:
                    sites = [(node, node.child_by_field_name("module_name"))]
            else:
                specifier = node.child_by_field_name("source")
                if node.type == "export_statement" and specifier is None:
                    continue
                sites = [(node, specifier)]
            for site, specifier in sites:
                text = (specifier or site).text.decode()
                if language in {"javascript", "typescript", "tsx"} and specifier is not None:
                    text = text[1:-1]
                if len(imports) >= MAX_FACTS:
                    raise ValueError("Import count exceeds the result limit")
                imports.append(CodeGraphImport(**location(item.path, site), specifier=text, provenance="unresolved"))
        files.append(CodeGraphFile(**metadata, language=language,
                                   status="partial" if root.has_error else "parsed",
                                   reason="syntax_error" if root.has_error else None,
                                   line_count=source_lines(source), unresolved_calls=count))
    available = {item.path for item in files if item.status not in {"excluded", "failed"}}
    for item in imports:
        language = LANGUAGES[PurePosixPath(item.file_path).suffix][0]
        target = import_target(item.file_path, item.specifier, language, available)
        if target is not None:
            item.target_path, item.provenance = target, "relative_path"
    return CodeGraphResult(**request.model_dump(exclude={"files"}), files=files, symbols=symbols, imports=imports)


def main():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES, MAX_RESULT_BYTES))
    request_path, result_path = map(Path, sys.argv[1:])
    try:
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("Oversize request")
        request = CodeGraphRequest.model_validate_json(request_path.read_bytes())
        if not {"schema_version", "extractor_version"} <= request.model_fields_set:
            raise ValueError("Explicit protocol and extractor versions are required")
    except ValueError:
        return 2
    try:
        result_path.write_text(extract(request).model_dump_json(), encoding="utf-8")
    except (ValueError, MemoryError, OSError):
        return 3
    return 0

if __name__ == "__main__":
    sys.exit(main())
