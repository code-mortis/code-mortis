"""Bounded browser views over an authorized repository's indexed revisions."""

from collections import Counter, defaultdict

from fastapi import HTTPException
from sqlalchemy import or_, select

from code_mortis.code_graph import retrieval, service, store
from code_mortis.code_graph.explorer import source_checker
from code_mortis.core.db import transaction
from code_mortis.models import CodeGraphImport
from code_mortis.schemas.code_graph_explorer import GraphRead
from code_mortis.schemas.repository_graph import (
    RepositoryGraphEdge,
    RepositoryGraphFiles,
    RepositoryGraphFilters,
    RepositoryGraphNode,
    RepositoryGraphView,
)

MAX_NODES = 300
MAX_LINKS = 1_000
MAX_SEARCH = 50


def respond(callback):
    headers = {"Cache-Control": "no-store"}
    try:
        return callback()
    except service.AccessLost:
        raise HTTPException(403, "Code access is unavailable under the current repository policy or permissions.",
                            headers=headers) from None
    except (ValueError, TypeError):
        raise HTTPException(422, "The code graph request is invalid.", headers=headers) from None
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **headers}
        raise


def authorized_read(repository_id, user_id, load, *, allow_disabled=False):
    service.browser_only()
    before, remote = service.authorize(repository_id, user_id, enabled=not allow_disabled, initiator=False)
    with transaction() as db:
        repo, actor, config = service.local_access(db, repository_id, user_id, enabled=not allow_disabled,
                                                   initiator=False)
        rules = store.policy(repo)
        if rules != store.policy(before):
            raise service.AccessLost("Graph policy changed during retrieval.")
        result, text = load(db, repo, actor, config, remote)
        enabled = bool(config and config.enabled)

    service.authorize(repository_id, user_id, enabled=enabled, initiator=False)
    with transaction() as db:
        repo, actor, _ = service.local_access(db, repository_id, user_id, enabled=enabled, initiator=False)
        if rules != store.policy(repo):
            raise service.AccessLost("Graph policy changed during retrieval.")
        source_checker(db, actor, repo)(text)
    return result


def import_pairs(db, snapshot_id, files, incident=None):
    source, target = CodeGraphImport.file_path, CodeGraphImport.target_path
    query = (select(source, target).where(CodeGraphImport.snapshot_id == snapshot_id, target.is_not(None))
             .distinct().order_by(source, target))
    if incident is not None:
        query = query.where(or_(source.in_(incident), target.in_(incident)))
    return [(a, b) for a, b in db.execute(query) if a in files and b in files]


def file_node(path, language, incoming, outgoing):
    return RepositoryGraphNode(id=f"file:{path}", kind="file", path=path, language=language,
                               incoming=incoming[path], outgoing=outgoing[path], file_count=1)


def degree(pairs):
    return Counter(b for _, b in pairs), Counter(a for a, _ in pairs)


def focus_paths(path, eligible, pairs, direction, depth):
    neighbors = defaultdict(list)
    for a, b in pairs:
        if direction != "incoming":
            neighbors[a].append((b, (a, b)))
        if direction != "outgoing":
            neighbors[b].append((a, (a, b)))
    visited, links = {path: None}, set()
    frontier = [path]
    for _ in range(depth):
        next_frontier = []
        for current in frontier:
            for neighbor, edge in neighbors[current]:
                if neighbor != path and neighbor not in eligible:
                    continue
                links.add(edge)
                if neighbor not in visited:
                    visited[neighbor] = None
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return list(visited), sorted(links, key=lambda edge: (path not in edge, edge))


def graph_view(files, pairs, filters: RepositoryGraphFilters, result: RepositoryGraphView) -> RepositoryGraphView:
    # Stored pairs are distinct and ordered by endpoints. All degree counts refer to the snapshot.
    incoming, outgoing = degree(pairs)
    prefix = f"{filters.folder}/" if filters.folder else ""
    paths = [path for path, file in files.items()
             if path.startswith(prefix) and (not filters.language or file.language == filters.language)]
    eligible = set(paths)
    if filters.focus:
        if filters.focus not in files:
            return result
        paths, links = focus_paths(filters.focus, eligible, pairs, filters.direction, filters.depth)
    else:
        links = [(a, b) for a, b in pairs if a in eligible and b in eligible]
    if filters.hide_unlinked:
        linked = {path for edge in links for path in edge}
        paths = [path for path in paths if path in linked or path == filters.focus]

    result.total_files, result.total_links = len(paths), len(links)
    oversized = len(paths) > MAX_NODES or len(links) > MAX_LINKS
    if oversized and not filters.focus:
        groups = {}
        counts = Counter()
        for path in paths:
            folder, separator, _ = path[len(prefix):].partition("/")
            node = ("folder", prefix + folder) if separator else ("file", path)
            groups[path] = node
            counts[node] += 1

        grouped_links = sorted({(groups[a], groups[b]) for a, b in links if groups[a] != groups[b]})
        grouped_incoming, grouped_outgoing = degree(grouped_links)
        group_nodes = sorted(counts, key=lambda node: (node[0] != "folder", node[1]))
        nodes = []
        for kind, path in group_nodes[:MAX_NODES]:
            if kind == "file":
                nodes.append(file_node(path, files[path].language, incoming, outgoing))
            else:
                pair = (kind, path)
                nodes.append(RepositoryGraphNode(id=f"folder:{path}", kind="folder", path=path, language=None,
                                                 incoming=grouped_incoming[pair], outgoing=grouped_outgoing[pair],
                                                 file_count=counts[pair]))
        if any(kind == "folder" for kind, _ in group_nodes):
            result.mode = "folders"
        result.nodes = nodes
        ids = {node.id for node in nodes}
        edges = [(f"{a_kind}:{a}", f"{b_kind}:{b}") for (a_kind, a), (b_kind, b) in grouped_links]
        result.truncated = len(group_nodes) > MAX_NODES
    else:
        result.nodes = [file_node(path, files[path].language, incoming, outgoing) for path in paths[:MAX_NODES]]
        ids = {node.id for node in result.nodes}
        edges = [(f"file:{a}", f"file:{b}") for a, b in links]
        result.truncated = len(paths) > MAX_NODES

    edges = [(a, b) for a, b in edges if a in ids and b in ids]
    result.truncated |= len(edges) > MAX_LINKS
    result.edges = [RepositoryGraphEdge(source=a, target=b) for a, b in edges[:MAX_LINKS]]
    return result


def view(repository_id, user_id, filters):
    def load(db, repo, actor, config, remote):
        enabled = bool(config and config.enabled)
        sha = filters.commit_sha or (config.default_sha if enabled else None)
        branch = remote.get("default_branch")
        if not isinstance(branch, str) or not 1 <= len(branch) <= 255 or (enabled and filters.commit_sha and sha != config.default_sha):
            branch = None
        result = RepositoryGraphView(status="unavailable" if enabled else "disabled", commit_sha=sha,
                                     observed_sha=config.observed_sha if enabled else None,
                                     snapshot_id=None, branch=branch, mode="files", nodes=[], edges=[], truncated=False,
                                     total_files=0, total_links=0)
        if enabled and sha:
            request = retrieval.GraphQuery(repository_id=repo.id, commit_sha=sha, operation="status")
            row = retrieval.snapshot(db, request, actor.id)
            if row and row.status in service.USABLE:
                result.status, result.snapshot_id = row.status, row.id
                files = retrieval.visible_files(db, row)
                graph_view(files, import_pairs(db, row.id, files), filters, result)
        return result, "\n".join([branch or "", *(node.path for node in result.nodes)])

    return authorized_read(repository_id, user_id, load, allow_disabled=True)


def files(repository_id, user_id, commit_sha, query):
    def load(db, repo, actor, config, remote):
        request = retrieval.GraphQuery(repository_id=repo.id, commit_sha=commit_sha, operation="status")
        row = retrieval.snapshot(db, request, actor.id)
        if not row or row.status not in service.USABLE:
            raise HTTPException(409, "This indexed revision is unavailable. Refresh the graph and try again.")
        files = retrieval.visible_files(db, row)
        term = query.casefold()
        matches = [path for path in files if term in path.casefold()]
        matches.sort(key=lambda path: (path != query, not path.rpartition("/")[2].casefold().startswith(term), path))
        paths = matches[:MAX_SEARCH]
        incoming, outgoing = degree(import_pairs(db, row.id, files, incident=paths))
        nodes = [file_node(path, files[path].language, incoming, outgoing) for path in paths]
        return RepositoryGraphFiles(files=nodes, truncated=len(matches) > MAX_SEARCH), "\n".join(paths)

    return authorized_read(repository_id, user_id, load)


def source(repository_id, user_id, commit_sha, path, start_byte):
    def load(db, repo, actor, config, remote):
        request = retrieval.GraphQuery(repository_id=repo.id, commit_sha=commit_sha, operation="source",
                                       paths=[path], start_byte=start_byte, limit=1)
        loaded = retrieval.read_snapshot(request, actor.id, source_check=source_checker(db, actor, repo))
        return GraphRead.model_validate(loaded), "\n".join(
            field for item in loaded["items"] for field in (item["path"], item["text"])
        )

    return authorized_read(repository_id, user_id, load)
