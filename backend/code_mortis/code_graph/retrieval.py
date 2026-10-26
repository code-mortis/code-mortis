"""Bounded internal navigation over one authorized immutable revision."""

from collections import deque
from typing import Literal

from pydantic import Field
from sqlalchemy import or_, select

from code_mortis.code_graph import service, store
from code_mortis.core.db import transaction
from code_mortis.models import CodeGraphFile, CodeGraphImport, CodeGraphSnapshot, CodeGraphSymbol
from code_mortis.schemas.code_graph import EXTRACTOR_VERSION, GitSha, RepositoryPath, StrictModel

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
    operation: Literal["symbols", "defines", "text", "dependencies", "impact", "source", "callers", "references"]
    query: str = Field(default="", max_length=200)
    paths: list[RepositoryPath] = Field(default_factory=list, max_length=MAX_SOURCE_FILES)
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    depth: int = Field(default=2, ge=0, le=2, strict=True)
    limit: int = Field(default=MAX_RESULTS, ge=1, le=MAX_RESULTS, strict=True)


def snapshot(db, request, actor_id):
    repo, _, _ = service.local_access(db, request.repository_id, actor_id, initiator=False)
    rules = store.policy(repo)
    row = db.scalar(select(CodeGraphSnapshot).where(
        CodeGraphSnapshot.repository_id == request.repository_id,
        CodeGraphSnapshot.commit_sha == request.commit_sha,
        CodeGraphSnapshot.extractor_version == EXTRACTOR_VERSION,
        CodeGraphSnapshot.policy_digest == store.policy_digest(rules),
    ))
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
    matches = list(db.scalars(query.order_by(CodeGraphSymbol.file_path, CodeGraphSymbol.start_byte).limit(request.limit + 1)))
    response["truncated"] = len(matches) > request.limit
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
        matches = list(db.scalars(edges.where(or_(*conditions))
                                  .order_by(CodeGraphImport.file_path, CodeGraphImport.start_byte).limit(MAX_VISITED + 1)))
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


def sources(row, request, files, response):
    response["unavailable_sources"] = sorted(set(request.paths) - set(files))
    selected = sorted(set(request.paths) & set(files)) if request.paths else sorted(files)
    response["truncated"] = len(selected) > MAX_SOURCE_FILES
    selected = selected[:MAX_SOURCE_FILES]
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
        if remaining <= 0 or len(response["items"]) >= request.limit:
            response["truncated"] = True
            break
        file, entry = files[path], tree.get(path)
        try:
            if not entry or entry[:2] not in {("100644", "blob"), ("100755", "blob")} or entry[2:] != (file.blob_sha, file.size_bytes):
                raise ValueError("Missing source")
            raw = store.read_blob(root, file.blob_sha, file.size_bytes)
            raw.decode("utf-8")
        except (ValueError, OSError, TimeoutError):
            response["unavailable_sources"].append(path)
            response["source_state"] = "partial"
            continue
        chunk = raw[:remaining].decode("utf-8", errors="ignore")
        chunk_bytes = len(chunk.encode())
        remaining -= chunk_bytes
        response["truncated"] |= chunk_bytes < len(raw) or (remaining == 0 and index < len(selected) - 1)
        response["searched_files"].append(path)
        if request.operation == "source":
            response["items"].append({"kind": "source", "path": path, "blob_sha": file.blob_sha, "text": chunk,
                                      "start_byte": 0, "end_byte": chunk_bytes, "start_line": 1,
                                      "end_line": max(1, chunk.count("\n") + (not chunk.endswith("\n")))})
            continue
        offset = 0
        for number, line in enumerate(chunk.splitlines(keepends=True), 1):
            size = len(line.encode())
            if request.query in line:
                length = len(request.query.encode())
                if length > MAX_SOURCE_BYTES - emitted or len(response["items"]) >= request.limit:
                    response["truncated"] = True
                    return
                emitted += length
                start = offset + len(line[:line.index(request.query)].encode())
                response["items"].append({"kind": "text_match", "path": path, "blob_sha": file.blob_sha, "text": request.query,
                                          "start_byte": start, "end_byte": start + length,
                                          "start_line": number, "end_line": number})
            offset += size


def query(request: GraphQuery, actor_id: int):
    request = GraphQuery.model_validate(request.model_dump())
    service.authorize(request.repository_id, actor_id, initiator=False)
    response = {"repository_id": request.repository_id, "commit_sha": request.commit_sha,
                "extractor_version": EXTRACTOR_VERSION, "policy_digest": None, "snapshot_id": None,
                "status": "unavailable", "capabilities": dict(CAPABILITIES), "gaps": list(GAPS),
                "truncated": False, "source_state": "unavailable" if request.operation in {"source", "text"} else "not_requested", "unavailable_sources": [],
                "searched_files": [], "items": []}
    with store.locked(), transaction() as db:
        row = snapshot(db, request, actor_id)
        if row:
            response.update(snapshot_id=row.id, policy_digest=row.policy_digest, status=row.status)
        if row and row.status in service.USABLE:
            files = visible_files(db, row)
            if request.operation in {"symbols", "defines"}:
                symbols(db, row, request, files, response)
            elif request.operation in {"dependencies", "impact"}:
                dependencies(db, row, request, files, response)
            elif request.operation in {"source", "text"}:
                if request.operation == "text" and (not request.query or "\n" in request.query or "\r" in request.query):
                    raise ValueError("Text lookup requires a non-empty single-line literal query.")
                sources(row, request, files, response)
            else:
                response["relationship_state"] = "unsupported"
    # A disable/revocation during object reads must also prevent returning saved content.
    service.authorize(request.repository_id, actor_id, initiator=False)
    return response
