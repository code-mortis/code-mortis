"""Read saved PR changes without indexing or inference. No mutable source fallback."""

import json
import re
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Text, case, cast, func, select
from sqlalchemy.dialects.postgresql import distinct_on

from code_mortis.code_graph import explorer, store
from code_mortis.core import file_policy
from code_mortis.integrations.comparison import MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES
from code_mortis.integrations.comparison import load as comparison
from code_mortis.models import Finding, FindingVerification, Occurrence, ReviewRun
from code_mortis.schemas.code_graph import GitSha, RepositoryPath
from code_mortis.schemas.code_graph_explorer import ExplorerQuery
from code_mortis.schemas.common import SummaryResult, VerificationResult

MAX_PATCH_BYTES = 256 * 1024
MAX_LINES = 2000
MAX_FINDINGS = 100
MAX_CANDIDATES = 1000
PAGE_SIZE = 10
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_VERIFICATION_BYTES = 128 * 1024
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")


def load(repo, pr):
    base, head = (TypeAdapter(GitSha).validate_python(sha) for sha in (pr.base_sha, pr.head_sha))
    merge_base, files, truncated = comparison(repo.id, repo.installation_id, repo.full_name, base, head, pr.number)
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
            or file_policy.policy(run.config["settings"]) != file_policy.policy(repo.settings)):
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


def evidence_snapshot(raw, check):
    if not isinstance(raw, dict):
        raise ValueError("Invalid saved evidence")
    snapshot = {key: raw[key] for key in ("path", "content", "severity", "category", "existing_code", "suggestion")
                if isinstance(raw.get(key), str)}
    snapshot.update({key: raw[key] for key in ("start_line", "end_line") if type(raw.get(key)) is int})
    TypeAdapter(RepositoryPath).validate_python(snapshot.get("path"))
    encoded = json.dumps(snapshot)
    if not isinstance(snapshot.get("content"), str) or len(encoded.encode()) > 32768:
        raise ValueError("Invalid saved evidence")
    check(encoded)
    return snapshot


def reviewed_paths(db, pr):
    """Only validated saved file outcomes can earn a review check; never the run's status alone."""
    coverage = ReviewRun.report["manifest"]["coverage"]
    bounded = case((func.octet_length(cast(coverage, Text)) <= MAX_METADATA_BYTES, coverage), else_=None)
    row = db.execute(select(bounded)
        .where(ReviewRun.pr_id == pr.id, ReviewRun.base_sha == pr.base_sha, ReviewRun.head_sha == pr.head_sha,
               ReviewRun.report["manifest"]["coverage"].is_not(None))
        .order_by(ReviewRun.created_at.desc(), ReviewRun.id.desc()).limit(1)).first()
    if not row:
        return set()
    coverage = row[0]
    if not isinstance(coverage, dict):
        return set()
    selected, outcomes, completed = {}, set(), set()
    try:
        for kind in ("selected", "completed", "reused", "failed", "waived"):
            entries = coverage.get(kind)
            if not isinstance(entries, list):
                return set()
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get("item_id"), str) or not item["item_id"]:
                    return set()
                key, path = item["item_id"], TypeAdapter(RepositoryPath).validate_python(item.get("path"))
                if kind == "selected":
                    if key in selected:
                        return set()
                    selected[key] = path
                else:
                    if key in outcomes or selected.get(key) != path:
                        return set()
                    outcomes.add(key)
                    if kind in {"completed", "reused"}:
                        completed.add(key)
    except (ValueError, TypeError):
        return set()
    if outcomes != set(selected):
        return set()
    unfinished = {path for key, path in selected.items() if key not in completed}
    return set(selected.values()) - unfinished


def file_states(db, user, repo, pr, files):
    reviewed = reviewed_paths(db, pr)
    states, by_path = [], {}
    for index, (info, _) in enumerate(files):
        states.append({"reviewed": info.path in reviewed, "open_count": 0, "resolved_count": 0, "dismissed_count": 0, "complete": True})
        for path in {info.path, info.previous_path} - {None}:
            by_path.setdefault(path, []).append(index)
    # Bound raw JSON in SQL, before Python materializes candidate evidence.
    bounded = case((func.octet_length(cast(Occurrence.snapshot, Text)) <= 32768, Occurrence.snapshot), else_=None)
    records = db.execute(select(Occurrence.finding_id, Finding.status, Occurrence.snapshot["path"].as_string(), bounded)
        .join(ReviewRun, ReviewRun.id == Occurrence.run_id).join(Finding, Finding.id == Occurrence.finding_id)
        .where(ReviewRun.pr_id == pr.id, Finding.pr_id == pr.id,
               Occurrence.snapshot["path"].as_string().in_(list(by_path)))
        .order_by(ReviewRun.created_at.desc(), Occurrence.id).limit(MAX_CANDIDATES + 1)).all()
    check, seen = explorer.source_checker(db, user, repo), set()
    if len(records) > MAX_CANDIDATES:
        for state in states:
            state["complete"] = False
    for finding_id, status, path, raw in records[:MAX_CANDIDATES]:
        indexes = by_path[path]
        try:
            evidence_snapshot(raw, check)
        except (ValueError, TypeError, HTTPException):
            for index in indexes:
                states[index]["complete"] = False
            continue
        for index in indexes:
            # Saved findings remain discoverable even when their original revision has no diff anchor.
            if (index, finding_id) not in seen and status in {"open", "resolved", "dismissed"}:
                states[index][f"{status}_count"] += 1
                seen.add((index, finding_id))
    return states


def saved_findings(db, user, repo, pr, info, identity, lines):
    paths = [p for p in (info.path, info.previous_path) if p]
    bounded = case((func.octet_length(cast(Occurrence.snapshot, Text)) <= 32768, Occurrence.snapshot), else_=None)
    records = db.execute(select(Occurrence.id, Occurrence.finding_id, ReviewRun.id, ReviewRun.base_sha,
        ReviewRun.head_sha, Finding.status, bounded).join(ReviewRun, ReviewRun.id == Occurrence.run_id)
        .join(Finding, Finding.id == Occurrence.finding_id).where(ReviewRun.pr_id == pr.id, Finding.pr_id == pr.id,
        Occurrence.snapshot["path"].as_string().in_(paths)).order_by(((ReviewRun.base_sha == pr.base_sha) & (ReviewRun.head_sha == pr.head_sha)
                   & (Finding.status == "open")).desc(), ReviewRun.created_at.desc(), Occurrence.id)
        .limit(MAX_FINDINGS + 1)).all()
    results = []
    check = explorer.source_checker(db, user, repo)
    current_lines = {line["new_line"]: line["text"] for line in lines if line.get("new_line") is not None}
    for occurrence_id, finding_id, run_id, base_sha, head_sha, status, raw in records[:MAX_FINDINGS]:
        try:
            snapshot = evidence_snapshot(raw, check)
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
        exact = (base_sha, head_sha) == (pr.base_sha, pr.head_sha)
        # The complete range must exist in the displayed hunk, not merely its last line.
        visible = {line.get(f"{side}_line") for line in lines} if side else set()
        inline = exact and side and valid_range and end - start < MAX_LINES and all(n in visible for n in range(start, end + 1))
        if (not inline and sha and sha == head_sha and snapshot["path"] == info.path
            and valid_range and end - start < MAX_LINES):
            quoted = snapshot.get("existing_code", "").splitlines()
            if (len(quoted) == end - start + 1 and any(text.strip() for text in quoted)
                and all(current_lines.get(n) == quoted[n - start] for n in range(start, end + 1))):
                inline, side = True, "new"
        results.append({"occurrence_id": occurrence_id, "finding_id": finding_id, "run_id": run_id,
            "source_sha": sha, "current_status": status, "snapshot": snapshot, "side": side if inline else None,
            "line": end if inline else None, "placement": "inline" if inline else
            "unknown" if not sha or not valid_range else "outside_diff" if exact and side else "historical"})
    place_verified_findings(db, pr, info, identity, lines, results)
    return results, len(records) > MAX_FINDINGS


def place_verified_findings(db, pr, info, identity, lines, findings):
    """Attach saved citations whose location is validated against the displayed diff."""
    ids = {item["finding_id"] for item in findings if item["placement"] != "inline"}
    visible = {line["new_line"]: line["text"] for line in lines if line.get("new_line") is not None}
    if not ids or not visible:
        return
    bounded = case((func.octet_length(cast(FindingVerification.result, Text)) <= MAX_VERIFICATION_BYTES,
                    FindingVerification.result), else_=None)
    rows = db.execute(select(FindingVerification.finding_id, FindingVerification.id, FindingVerification.status,
        FindingVerification.verdict, FindingVerification.head_sha, bounded)
        .join(ReviewRun, ReviewRun.id == FindingVerification.run_id)
        .join(Occurrence, Occurrence.id == FindingVerification.occurrence_id)
        .where(FindingVerification.finding_id.in_(ids), FindingVerification.head_sha == ReviewRun.head_sha,
               ReviewRun.pr_id == pr.id,
               Occurrence.finding_id == FindingVerification.finding_id,
               Occurrence.run_id.in_(select(ReviewRun.id).where(ReviewRun.pr_id == pr.id)))
        .ext(distinct_on(FindingVerification.finding_id))
        .order_by(FindingVerification.finding_id, (FindingVerification.head_sha == identity["head_sha"]).desc(),
                  FindingVerification.created_at.desc(), FindingVerification.id.desc())).all()
    verified = {}
    for finding_id, verification_id, status, verdict, source_sha, raw in rows:
        if status != "completed":
            continue
        try:
            result = VerificationResult.model_validate(raw, strict=True)
            TypeAdapter(GitSha).validate_python(source_sha)
        except (ValueError, TypeError):
            continue
        if result.verdict != verdict:
            continue
        citations = []
        for evidence in result.evidence:
            if evidence.revision != "current" or evidence.path != info.path or not 0 < evidence.end_line - evidence.start_line + 1 <= MAX_LINES:
                continue
            quoted = evidence.quote.splitlines()
            if len(quoted) != evidence.end_line - evidence.start_line + 1:
                continue
            matched = [n for n in range(evidence.start_line, evidence.end_line + 1) if n in visible]
            # Older citations need their entire quote visible and unchanged at the saved location.
            if source_sha != identity["head_sha"] and len(matched) != len(quoted):
                continue
            # A citation may cross omitted context. Every displayed line must match its exact quoted line.
            if not matched or any(visible[n] != quoted[n - evidence.start_line] for n in matched) or not any(visible[n].strip() for n in matched):
                continue
            citations.append({"id": verification_id, "verdict": verdict, "source_sha": source_sha,
                              "start_line": evidence.start_line, "end_line": evidence.end_line,
                              "line": matched[-1]})
        if citations:
            verified[finding_id] = citations
    for item in findings:
        citations = verified.get(item["finding_id"])
        if item["placement"] == "inline" or not citations:
            continue
        end = item["snapshot"].get("end_line", 0)
        citation = min(citations, key=lambda c: (not c["start_line"] <= end <= c["end_line"],
                                               c["end_line"] - c["start_line"], c["start_line"]))
        item.update(placement="inline", side="new", line=citation["line"],
                    verification={k: v for k, v in citation.items() if k != "line"})


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
    metadata = {"files": [info.model_dump() for info, _ in files], "groups": groups,
                "file_states": file_states(db, user, repo, pr, files)}
    metadata_truncated = False
    # Bound even adversarial long paths/group text before returning an inventory for local filtering.
    while len(json.dumps(metadata, ensure_ascii=False).encode()) > MAX_METADATA_BYTES:
        metadata_truncated = True
        if metadata["groups"]:
            metadata["groups"].pop()
        else:
            metadata["files"].pop()
            metadata["file_states"].pop()
    files = files[:len(metadata["files"])]
    group = next((g for g in metadata["groups"] if g["id"] == request.group), None)
    findings = request.findings or (["open"] if request.with_findings else [])
    reviewed = request.reviewed or ([False] if request.unreviewed else [])
    def matches(state):
        if reviewed and state["reviewed"] not in reviewed:
            return False
        total = sum(state[f"{status}_count"] for status in ("open", "resolved", "dismissed"))
        return not findings or any(
            state["complete"] and total == 0 if value == "none" else
            not state["complete"] or (total if value == "any" else state[f"{value}_count"]) > 0
            for value in findings)
    visible = [info for i, (info, _) in enumerate(files) if request.query.casefold() in info.path.casefold()
               and (not group or i in group["file_indexes"])
               and matches(metadata["file_states"][i])]
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
