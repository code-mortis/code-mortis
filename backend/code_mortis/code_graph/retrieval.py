"""Bounded internal navigation over one authorized immutable revision."""

from collections import deque
from typing import Literal

from pydantic import Field
from sqlalchemy import or_, select

from code_mortis.code_graph import service, store
from code_mortis.code_graph.syntax import LANGUAGES
from code_mortis.core.db import transaction
from code_mortis.models import CodeGraphFile, CodeGraphImport, CodeGraphSnapshot, CodeGraphSymbol
from code_mortis.schemas.code_graph import (
    EXTRACTOR_VERSION,
    MAX_FILE_BYTES,
    GitSha,
    RepositoryPath,
    StrictModel,
)

MAX_RESULTS = 100
MAX_VISITED = 500
MAX_SOURCE_FILES = 10
MAX_SOURCE_BYTES = 64 * 1024
CAPABILITIES = {"defines": "supported", "imports_file": "partial", "callers": "unsupported", "references": "unsupported"}
GAPS = ["Import resolution covers conservative Python and JavaScript/TypeScript relative paths.",
        "File dependencies identify impact candidates, not resolved callers or confirmed defects."]


class GraphQuery(StrictModel):
    repository_id: int = Field(gt=0, strict=True)
    commit_sha: GitSha
    operation: Literal["status", "symbols", "defines", "text", "dependencies", "impact", "source", "callers", "references"]
    query: str = Field(default="", max_length=512)
    paths: list[RepositoryPath] = Field(default_factory=list, max_length=MAX_SOURCE_FILES)
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    depth: int = Field(default=2, ge=0, le=2, strict=True)
    limit: int = Field(default=MAX_RESULTS, ge=1, le=MAX_RESULTS, strict=True)
    offset: int = Field(default=0, ge=0, le=100_000, strict=True)
    start_byte: int = Field(default=0, ge=0, le=MAX_FILE_BYTES, strict=True)
    end_byte: int = Field(default=MAX_FILE_BYTES, ge=0, le=MAX_FILE_BYTES, strict=True)


def snapshot(db, request, actor_id):
    repo, _, _ = service.local_access(db, request.repository_id, actor_id, initiator=False)
    rules = store.policy(repo)
    row = db.scalar(select(CodeGraphSnapshot).where(
        CodeGraphSnapshot.repository_id == request.repository_id,
        CodeGraphSnapshot.commit_sha == request.commit_sha,
        CodeGraphSnapshot.extractor_version == EXTRACTOR_VERSION,
        CodeGraphSnapshot.policy_digest == store.policy_digest(rules),
    ).with_for_update(read=True))
    if row and row.policy != rules:
        return None
    return row


def visible_files(db, row):
    return {f.path: f for f in db.scalars(select(CodeGraphFile).where(
        CodeGraphFile.snapshot_id == row.id, CodeGraphFile.status.in_(["parsed", "partial", "unsupported"]),
    ).order_by(CodeGraphFile.path)) if not store.excluded(f.path, row.policy)}


def symbols(db, row, request, files, response):
    paths = set(files) & set(request.paths) if request.paths else set(files)
    query = select(CodeGraphSymbol).where(CodeGraphSymbol.snapshot_id == row.id, CodeGraphSymbol.file_path.in_(paths))
    if request.query:
        query = query.where(or_(CodeGraphSymbol.name == request.query, CodeGraphSymbol.key == request.query))
    matches = list(db.scalars(query.order_by(CodeGraphSymbol.file_path, CodeGraphSymbol.start_byte, CodeGraphSymbol.key)
                             .offset(request.offset).limit(request.limit + 1)))
    response["truncated"] = len(matches) > request.limit
    response["next_cursor"] = {"offset": request.offset + request.limit, "start_byte": 0} if response["truncated"] else None
    response["items"] = [{"kind": "symbol", "relationship": "defines", "key": s.key, "path": s.file_path,
                          "name": s.name, "symbol_kind": s.kind, "start_byte": s.start_byte, "end_byte": s.end_byte,
                          "start_line": s.start_line, "end_line": s.end_line} for s in matches[:request.limit]]


def dependencies(db, row, request, files, response):
    edges = select(CodeGraphImport).where(
        CodeGraphImport.snapshot_id == row.id, CodeGraphImport.file_path.in_(files), CodeGraphImport.target_path.in_(files),
    )
    direction = "incoming" if request.operation == "impact" else request.direction
    roots = sorted(set(request.paths) & set(files))
    visited, emitted, pending = set(roots), set(), deque((p, 0) for p in roots)
    while pending:
        path, depth = pending.popleft()
        conditions = []
        if direction in {"outgoing", "both"}:
            conditions.append(CodeGraphImport.file_path == path)
        if direction in {"incoming", "both"}:
            conditions.append(CodeGraphImport.target_path == path)
        ordering = [CodeGraphImport.file_path, CodeGraphImport.start_byte]
        if direction == "both":
            # Consumers must not crowd out this file's own dependencies at the result limit.
            ordering.insert(0, CodeGraphImport.file_path != path)
        matches = list(db.scalars(edges.where(or_(*conditions))
                                  .order_by(*ordering).limit(MAX_VISITED + 1)))
        response["truncated"] |= len(matches) > MAX_VISITED
        neighbors = [(e.target_path if e.file_path == path else e.file_path, e) for e in matches[:MAX_VISITED]]
        if depth >= request.depth:
            response["truncated"] |= any(neighbor not in visited for neighbor, _ in neighbors)
            continue
        for neighbor, edge in neighbors:
            if neighbor not in visited:
                if len(visited) >= MAX_VISITED:
                    response["truncated"] = True
                    return
                visited.add(neighbor)
                pending.append((neighbor, depth + 1))
            key = neighbor if request.operation == "impact" else (edge.file_path, edge.start_byte)
            if key in emitted or (request.operation == "impact" and neighbor in roots):
                continue
            if len(response["items"]) >= request.limit:
                response["truncated"] = True
                return
            emitted.add(key)
            response["items"].append(
                {"kind": "impact_candidate", "path": neighbor, "relationship": "imports_file"}
                if request.operation == "impact" else {
                    "kind": "dependency", "path": edge.file_path, "target_path": edge.target_path,
                    "relationship": "imports_file", "provenance": edge.provenance,
                    "start_byte": edge.start_byte, "end_byte": edge.end_byte,
                    "start_line": edge.start_line, "end_line": edge.end_line,
                })


def sources(row, request, files, response, source_check):
    response["unavailable_sources"] = sorted(set(request.paths) - set(files))
    selected = sorted(set(request.paths) & set(files)) if request.paths else sorted(files)
    selected = selected[request.offset:]
    if not selected:
        response["source_state"] = "unavailable"
        return
    root = store.directory(row.repository_id)
    try:
        if store.git(root, "rev-parse", store.reference(row.id)).strip().decode() != row.commit_sha:
            raise ValueError("Missing revision")
        tree = store.tree(root, row.commit_sha)
    except (ValueError, OSError, TimeoutError):
        response["source_state"] = "unavailable"
        return
    response["source_state"] = "partial" if response["unavailable_sources"] else "available"
    remaining, emitted = MAX_SOURCE_BYTES, 0
    for index, path in enumerate(selected):
        if index >= MAX_SOURCE_FILES or remaining <= 0 or len(response["items"]) >= request.limit:
            response["truncated"] = True
            response["next_cursor"] = {"offset": request.offset + index, "start_byte": 0}
            break
        file, entry = files[path], tree.get(path)
        try:
            if not entry or entry[:2] not in {("100644", "blob"), ("100755", "blob")} or entry[2:] != (file.blob_sha, file.size_bytes):
                raise ValueError("Missing source")
            raw = store.read_blob(root, file.blob_sha, file.size_bytes)
            decoded = raw.decode("utf-8")
        except (ValueError, OSError, TimeoutError):
            response["unavailable_sources"].append(path)
            response["source_state"] = "partial"
            continue
        if source_check:
            source_check(decoded)
        start = request.start_byte if index == 0 else 0
        end = min(len(raw), request.end_byte)
        if start > end:
            raise ValueError("Source start must not exceed its end.")
        # Byte cursors describe the original blob, including UTF-8 boundaries.
        prefix = raw[:start].decode("utf-8")
        raw[:end].decode("utf-8")
        chunk = raw[start:min(end, start + remaining)].decode("utf-8", errors="ignore")
        chunk_bytes = len(chunk.encode())
        remaining -= chunk_bytes
        response["truncated"] |= start + chunk_bytes < end
        response["searched_files"].append(path)
        first_line = prefix.count("\n") + 1
        if request.operation == "source":
            response["items"].append({"kind": "source", "path": path, "blob_sha": file.blob_sha, "text": chunk,
                                      "start_byte": start, "end_byte": start + chunk_bytes, "start_line": first_line,
                                      "end_line": first_line + max(0, chunk.count("\n") - chunk.endswith("\n"))})
        offset = start
        # Look ahead only far enough to find a literal crossing the page boundary.
        scan = raw[start:min(end, start + chunk_bytes + len(request.query.encode()))].decode("utf-8", errors="ignore")
        for number, line in enumerate(scan.splitlines(keepends=True) if request.operation == "text" else [], first_line):
            size = len(line.encode())
            if request.query in line:
                match_start = offset + len(line[:line.index(request.query)].encode())
                if match_start >= start + chunk_bytes:
                    break
                length = len(request.query.encode())
                if length > MAX_SOURCE_BYTES - emitted or len(response["items"]) >= request.limit:
                    response["truncated"] = True
                    response["next_cursor"] = {"offset": request.offset + index, "start_byte": offset}
                    return
                emitted += length
                response["items"].append({"kind": "text_match", "path": path, "blob_sha": file.blob_sha, "text": request.query,
                                          "start_byte": match_start, "end_byte": match_start + length,
                                          "start_line": number, "end_line": number})
            offset += size
        if start + chunk_bytes < end:
            response["next_cursor"] = {"offset": request.offset + index, "start_byte": start + chunk_bytes}
            return


def query(request: GraphQuery, actor_id: int, *, source_check=None):
    request = GraphQuery.model_validate(request.model_dump())
    service.authorize(request.repository_id, actor_id, initiator=False)
    response = read_snapshot(request, actor_id, source_check=source_check)
    # A disable/revocation during object reads must also prevent returning saved content.
    current, _ = service.authorize(request.repository_id, actor_id, initiator=False)
    if response["policy_digest"] and response["policy_digest"] != store.policy_digest(store.policy(current)):
        raise service.AccessLost("Graph policy changed during retrieval.")
    return response


def read_snapshot(request: GraphQuery, actor_id: int, *, source_check=None):
    """Local reader; callers must freshly authorize before reading and before returning content."""
    request = GraphQuery.model_validate(request.model_dump())
    response = {"repository_id": request.repository_id, "commit_sha": request.commit_sha,
                "extractor_version": EXTRACTOR_VERSION, "policy_digest": None, "snapshot_id": None,
                "status": "unavailable", "capabilities": dict(CAPABILITIES), "gaps": list(GAPS),
                "truncated": False, "source_state": "unavailable" if request.operation in {"source", "text"} else "not_requested", "unavailable_sources": [],
                "searched_files": [], "items": [], "next_cursor": None,
                "definition_languages": sorted({value[0] for value in LANGUAGES.values()})}
    # Only object reads need the volume; metadata remains available while indexing.
    with transaction() as db:
        row = snapshot(db, request, actor_id)
        if row:
            response.update(snapshot_id=row.id, policy_digest=row.policy_digest, status=row.status)
        if row and row.status in service.USABLE:
            files = visible_files(db, row)
            if request.operation == "status":
                response["coverage"] = {state: sum(f.status == state for f in files.values())
                                        for state in ("parsed", "partial", "unsupported")}
                response["coverage"]["visible_files"] = len(files)
                response["coverage"]["complete"] = row.status == "ready"
            elif request.operation in {"symbols", "defines"}:
                symbols(db, row, request, files, response)
            elif request.operation in {"dependencies", "impact"}:
                dependencies(db, row, request, files, response)
                paths = {item[key] for item in response["items"] for key in ("path", "target_path") if key in item}
                response["file_manifest"] = {path: {"blob_sha": files[path].blob_sha, "size_bytes": files[path].size_bytes}
                                             for path in sorted(paths)}
            elif request.operation in {"source", "text"}:
                if request.operation == "text" and (not request.query or "\n" in request.query or "\r" in request.query):
                    raise ValueError("Text lookup requires a non-empty single-line literal query.")
                try:
                    with store.locked(shared=True):
                        sources(row, request, files, response, source_check)
                except (OSError, store.RunnerBlocked):
                    response["source_state"] = "unavailable"
                    response["gaps"].append("Retained objects are unavailable or busy; retry this exact revision later.")
            else:
                response["relationship_state"] = "unsupported"
    return response
