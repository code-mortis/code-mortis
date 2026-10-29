"""Read saved PR changes without indexing or inference. No mutable source fallback."""

import json
import re
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from code_mortis.code_graph import explorer, store
from code_mortis.integrations.comparison import MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES
from code_mortis.integrations.comparison import load as comparison
from code_mortis.models import Finding, Occurrence, ReviewRun
from code_mortis.schemas.code_graph import GitSha, RepositoryPath
from code_mortis.schemas.code_graph_explorer import ExplorerQuery
from code_mortis.schemas.common import SummaryResult

MAX_PATCH_BYTES = 256 * 1024
MAX_LINES = 2000
MAX_FINDINGS = 100
PAGE_SIZE = 10
MAX_METADATA_BYTES = 2 * 1024 * 1024
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")


def load(repo, pr):
    base, head = (TypeAdapter(GitSha).validate_python(sha) for sha in (pr.base_sha, pr.head_sha))
    merge_base, files, truncated = comparison(repo.id, repo.installation_id, repo.full_name, base, head)
    rules = store.policy(repo)
    files = [(info, patch) for info, patch in files if not any(store.excluded(p, rules)
             for p in (info.path, info.previous_path) if p)]
    return {"base_sha": base, "head_sha": head, "merge_base_sha": merge_base}, files, truncated


def saved_groups(db, user, repo, pr, files):
    run = db.scalar(select(ReviewRun).where(ReviewRun.pr_id == pr.id, ReviewRun.base_sha == pr.base_sha,
        ReviewRun.head_sha == pr.head_sha, ReviewRun.summary["status"].as_string() == "completed")
        .order_by(ReviewRun.created_at.desc(), ReviewRun.id.desc()).limit(1))
    if not run or not isinstance(run.config, dict) or not isinstance(run.config.get("settings"), dict):
        return [], None
    try:
        if ("exclusions" not in run.config["settings"]
            or store.policy(SimpleNamespace(settings=run.config["settings"])) != store.policy(repo)):
            return [], None
        coverage = run.report
        for key in ("manifest", "coverage"):
            coverage = coverage.get(key) if isinstance(coverage, dict) else None
        selected = coverage.get("selected") if isinstance(coverage, dict) else None
        if not isinstance(selected, list) or not selected or any(
            not isinstance(item, dict) or store.excluded(TypeAdapter(RepositoryPath).validate_python(item.get("path")), store.policy(repo))
            for item in selected
        ):
            return [], None
        result = SummaryResult.model_validate(run.summary.get("result"))
        paths = {info.path for info, _ in files}
        # A description can mention any member. Withhold the entire summary if any path is now hidden.
        if any(p not in paths for group in result.groups for p in group.paths):
            return [], None
        explorer.check_source(db, user, repo, result.model_dump_json())
    except (ValueError, TypeError, HTTPException):
        return [], None
    return [{"id": f"{run.id}:{i}", **group.model_dump()} for i, group in enumerate(result.groups)], run.id


def overview(db, user, pr_id, page=1, query="", group=None):
    pr, repo, _ = explorer.context(db, user, pr_id)
    identity, files, truncated = load(repo, pr)
    groups, run_id = saved_groups(db, user, repo, pr, files)
    selected = next((item for item in groups if item["id"] == group), None)
    files = [info.model_dump() for info, _ in files if query.casefold() in info.path.casefold()
             and (not selected or info.path in selected["paths"])]
    page = min(page, max(1, (len(files) + PAGE_SIZE - 1) // PAGE_SIZE))
    result = {**identity, "policy_digest": store.policy_digest(store.policy(repo)),
              "files": files[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], "total": len(files), "page": page,
              "comparison_truncated": truncated, "groups": groups, "summary_run_id": run_id,
              "group": selected["id"] if selected else None}
    return explorer.finish(pr, repo, user, result, enabled=False)


def parse_patch(patch, info):
    """Validate complete hunks before returning bounded, numbered rows. Never guess missing lines."""
    if not patch:
        return "unavailable", []
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        return "too_large", []
    rows, old, new, old_left, new_left, additions, deletions = [], 0, 0, 0, 0, 0, 0
    for text in patch.replace("\r\n", "\n").split("\n"):
        if not text and not (old_left or new_left):
            continue
        match = HUNK.fullmatch(text)
        if match:
            if old_left or new_left:
                return "unavailable", []
            a, ac, b, bc = match.groups()
            if int(a) < old or int(b) < new:
                return "unavailable", []
            old, new, old_left, new_left = int(a), int(b), int(ac or 1), int(bc or 1)
            row = {"kind": "hunk", "text": text}
        elif text == "\\ No newline at end of file" and rows:
            row = {"kind": "notice", "text": text}
        elif text[:1] in {" ", "+", "-"} and (old_left or new_left):
            prefix = text[0]
            take_old, take_new = prefix != "+", prefix != "-"
            if (take_old and (old_left <= 0 or old <= 0)) or (take_new and (new_left <= 0 or new <= 0)):
                return "unavailable", []
            row = {"kind": {" ": "context", "+": "addition", "-": "deletion"}[prefix], "text": text[1:],
                   "old_line": old if take_old else None, "new_line": new if take_new else None}
            old, new = old + take_old, new + take_new
            old_left, new_left = old_left - take_old, new_left - take_new
            additions, deletions = additions + (prefix == "+"), deletions + (prefix == "-")
        else:
            return "unavailable", []
        rows.append(row)
    truncated = bool(old_left or new_left or (info.additions is not None and additions != info.additions)
                     or (info.deletions is not None and deletions != info.deletions) or len(rows) > MAX_LINES)
    return ("truncated" if truncated else "available"), rows[:MAX_LINES]


def saved_findings(db, user, repo, pr, info, identity, lines):
    paths = [p for p in (info.path, info.previous_path) if p]
    records = db.execute(select(Occurrence, ReviewRun, Finding.status).join(ReviewRun, ReviewRun.id == Occurrence.run_id)
        .join(Finding, Finding.id == Occurrence.finding_id).where(ReviewRun.pr_id == pr.id, Finding.pr_id == pr.id,
        Occurrence.snapshot["path"].as_string().in_(paths)).order_by(ReviewRun.created_at.desc(), Occurrence.id)
        .limit(MAX_FINDINGS + 1)).all()
    results = []
    for occurrence, run, status in records[:MAX_FINDINGS]:
        raw = occurrence.snapshot
        snapshot = {key: raw[key] for key in ("path", "content", "severity", "category", "existing_code", "suggestion")
                    if isinstance(raw.get(key), str)}
        snapshot.update({key: raw[key] for key in ("start_line", "end_line") if type(raw.get(key)) is int})
        try:
            TypeAdapter(RepositoryPath).validate_python(snapshot.get("path"))
            if not isinstance(snapshot.get("content"), str) or len(json.dumps(snapshot).encode()) > 32768:
                continue
            explorer.check_source(db, user, repo, json.dumps(snapshot, ensure_ascii=False))
        except (ValueError, TypeError, HTTPException):
            continue
        try:
            sha = TypeAdapter(GitSha).validate_python(raw.get("source_sha"))
        except ValidationError:
            sha = None
        start, end = snapshot.get("start_line"), snapshot.get("end_line")
        valid_range = type(start) is int and type(end) is int and 0 < start <= end
        side = ("new" if sha == identity["head_sha"] and snapshot["path"] == info.path else
                "old" if sha == identity["merge_base_sha"] and snapshot["path"] == (info.previous_path or info.path) else None)
        exact = (run.base_sha, run.head_sha) == (pr.base_sha, pr.head_sha)
        # The complete range must exist in the displayed hunk, not merely its last line.
        visible = {line.get(f"{side}_line") for line in lines} if side else set()
        inline = exact and side and valid_range and end - start < MAX_LINES and all(n in visible for n in range(start, end + 1))
        results.append({"occurrence_id": occurrence.id, "finding_id": occurrence.finding_id, "run_id": run.id,
            "source_sha": sha, "current_status": status, "snapshot": snapshot, "side": side if inline else None,
            "line": end if inline else None, "placement": "inline" if inline else
            "unknown" if not sha or not valid_range else "outside_diff" if exact and side else "historical"})
    return results, len(records) > MAX_FINDINGS


def read(db, user, pr_id, request):
    pr, repo, _ = explorer.context(db, user, pr_id, request)
    identity, files, _ = load(repo, pr)
    if identity["merge_base_sha"] != request.merge_base_sha:
        raise HTTPException(409, "The comparison changed. Refresh Code before continuing.")
    selected = next(((info, patch) for info, patch in files if info.path == request.path), None)
    if not selected:
        raise HTTPException(404, "This file is not available in the current comparison.")
    return explorer.finish(pr, repo, user, read_file(db, user, repo, pr, identity, selected), enabled=False)


def read_file(db, user, repo, pr, identity, selected):
    info, patch = selected
    try:
        explorer.check_source(db, user, repo, patch or "")
        state, lines = parse_patch(patch, info)
    except HTTPException:
        state, lines = "withheld", []
    findings, truncated = saved_findings(db, user, repo, pr, info, identity, lines)
    result = {**identity, "file": info.model_dump(), "patch_state": state, "lines": lines,
              "findings": findings, "findings_truncated": truncated,
              "github_url": f"https://github.com/{quote(repo.full_name, safe='/')}/compare/{identity['merge_base_sha']}...{pr.head_sha}"}
    return result


def workspace(db, user, pr_id, request):
    pr, repo, config = explorer.context(db, user, pr_id, request.expected)
    identity, files, truncated = load(repo, pr)
    if request.expected and identity["merge_base_sha"] != request.expected.merge_base_sha:
        raise HTTPException(409, "The comparison changed. Refresh Code before continuing.")
    groups, run_id = saved_groups(db, user, repo, pr, files)
    indexes = {info.path: i for i, (info, _) in enumerate(files)}
    groups = [{"id": g["id"], "title": g["title"], "description": g["description"],
               "file_indexes": sorted({indexes[p] for p in g["paths"]})} for g in groups]
    metadata = {"files": [info.model_dump() for info, _ in files], "groups": groups}
    metadata_truncated = False
    # Bound even adversarial long paths/group text before returning an inventory for local filtering.
    while len(json.dumps(metadata, ensure_ascii=False).encode()) > MAX_METADATA_BYTES:
        metadata_truncated = True
        if metadata["groups"]:
            metadata["groups"].pop()
        else:
            metadata["files"].pop()
    files = files[:len(metadata["files"])]
    group = next((g for g in metadata["groups"] if g["id"] == request.group), None)
    visible = [info for i, (info, _) in enumerate(files) if request.query.casefold() in info.path.casefold()
               and (not group or i in group["file_indexes"])]
    page = min(request.page, max(1, (len(visible) + PAGE_SIZE - 1) // PAGE_SIZE))
    path = request.path or (visible[(page - 1) * PAGE_SIZE].path if visible else None)
    selected = next(((info, patch) for info, patch in files if path is not None and path in (info.path, info.previous_path)), None)
    revision = request.revision
    if selected:
        info, _ = selected
        if request.view == "diff":
            path = info.path
        elif revision == "base" or info.change == "removed":
            path, revision = info.previous_path or info.path, "base"
    result = {**identity, **metadata, "policy_digest": store.policy_digest(store.policy(repo)),
              "summary_run_id": run_id, "comparison_truncated": truncated, "metadata_truncated": metadata_truncated,
              "path": path, "view": request.view, "revision": revision,
              "graph_enabled": bool(config and config.enabled), "graph_paused": bool(config and config.paused),
              "diff": None, "graph": None}
    if path and request.view == "diff":
        if not selected:
            raise HTTPException(404, "This file is not available in the current comparison.")
        result["diff"] = read_file(db, user, repo, pr, identity, selected)
    elif path and result["graph_enabled"]:
        query = ExplorerQuery(base_sha=pr.base_sha, head_sha=pr.head_sha, revision=revision, path=path,
            operation="symbols" if request.view == "definitions" else request.view,
            offset=request.offset, start_byte=request.start_byte, end_byte=request.end_byte)
        sha = identity["merge_base_sha"] if revision == "base" else pr.head_sha
        result["graph"] = explorer.read_file(db, user, repo, sha, query)
    return explorer.finish(pr, repo, user, result, enabled=result["graph"] is not None)
