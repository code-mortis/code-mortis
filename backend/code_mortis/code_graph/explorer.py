"""Browser PR navigation over immutable comparisons and the shared bounded reader."""

import json

from fastapi import HTTPException
from pydantic import TypeAdapter

from code_mortis.api.dependencies import pr_access
from code_mortis.code_graph import retrieval, service, store
from code_mortis.core.db import transaction
from code_mortis.core.security import redact_saved_credentials, unseal
from code_mortis.integrations import comparison as github_comparison
from code_mortis.models import CodeGraphRepository, PullRequest
from code_mortis.schemas.code_graph import GitSha


def context(db, user, pr_id, expected=None, *, write=False):
    service.browser_only()
    pr, repo, _ = pr_access(db, user, pr_id, write=write)
    if expected and (pr.base_sha, pr.head_sha) != (expected.base_sha, expected.head_sha):
        raise HTTPException(409, "The pull request revision changed. Refresh Code before continuing.")
    service.authorize(repo.id, user.id, enabled=False, initiator=False)
    return pr, repo, db.get(CodeGraphRepository, repo.id)


def comparison(db, repo, pr):
    base, head = (TypeAdapter(GitSha).validate_python(sha) for sha in (pr.base_sha, pr.head_sha))
    merge_base, paths, truncated = comparison_metadata(repo.id, repo.installation_id, repo.full_name, base, head)
    rules = store.policy(repo)
    return merge_base, [{"path": path, "change": change} for path, change in paths if not store.excluded(path, rules)], truncated


def comparison_metadata(repository_id, installation_id, full_name, base, head):
    merge_base, files, truncated = github_comparison.load(repository_id, installation_id, full_name, base, head)
    paths = {}
    for info, _ in files:
        path, change = info.path, info.change
        if change == "renamed":
            paths[info.previous_path] = "removed"
        if change != "unchanged":
            paths[path] = "added" if change in {"added", "renamed", "copied"} else "removed" if change == "removed" else "modified"
    return merge_base, tuple(sorted(paths.items())), truncated


def check_source(db, user, repo, text):
    if (redact_saved_credentials(db, text, [repo.id]) != text.replace("\r\n", "\n")
        or any(value and unseal(value) in text for value in (user.access_token, user.refresh_token))):
        raise HTTPException(422, "This content contains credential material and was withheld.")


def finish(pr, repo, user, result, *, enabled=True):
    current, _ = service.authorize(repo.id, user.id, enabled=enabled, initiator=False)
    with transaction() as db:
        latest = db.get(PullRequest, pr.id)
        if not latest or (latest.repository_id, latest.base_sha, latest.head_sha) != (repo.id, pr.base_sha, pr.head_sha):
            raise HTTPException(409, "The pull request revision changed. Refresh Code before continuing.")
        if store.policy(current) != store.policy(repo):
            raise service.AccessLost("Graph policy changed during navigation.")
        config = db.get(CodeGraphRepository, repo.id)
        if "graph_enabled" in result and result["graph_enabled"] != bool(config and config.enabled):
            raise service.AccessLost("Graph availability changed during navigation.")
        for snapshot in (result, result.get("base", {}), result.get("head", {}), result.get("graph") or {}):
            if snapshot.get("policy_digest") not in (None, store.policy_digest(store.policy(current))):
                raise service.AccessLost("Graph policy changed during navigation.")
        check_source(db, user, current, json.dumps(result, ensure_ascii=False))
    return result


def overview(db, user, pr_id, page=1, query=""):
    pr, repo, config = context(db, user, pr_id)
    result = {"enabled": bool(config and config.enabled), "paused": bool(config and config.paused),
              "base_sha": pr.base_sha, "head_sha": pr.head_sha}
    if not result["enabled"]:
        return finish(pr, repo, user, result, enabled=False)
    merge_base, files, truncated = comparison(db, repo, pr)
    files = [item for item in files if query.casefold() in item["path"].casefold()]
    page = min(page, max(1, (len(files) + 9) // 10))
    result.update(merge_base_sha=merge_base, files=files[(page - 1) * 10:page * 10], total=len(files),
                  page=page, has_more=page * 10 < len(files), comparison_truncated=truncated)
    for side, sha in (("base", merge_base), ("head", pr.head_sha)):
        result[side] = retrieval.read_snapshot(retrieval.GraphQuery(repository_id=repo.id, commit_sha=sha, operation="status"), user.id)
    return finish(pr, repo, user, result)


def read(db, user, pr_id, request):
    pr, repo, config = context(db, user, pr_id, request)
    sha = comparison(db, repo, pr)[0] if request.revision == "base" else pr.head_sha
    return finish(pr, repo, user, read_file(db, user, repo, sha, request))


def read_file(db, user, repo, sha, request):
    query = retrieval.GraphQuery(repository_id=repo.id, commit_sha=sha, operation=request.operation,
                                 paths=[request.path], direction="both", depth=1, limit=100,
                                 offset=request.offset, start_byte=request.start_byte, end_byte=request.end_byte)
    result = retrieval.read_snapshot(query, user.id, source_check=lambda text: check_source(db, user, repo, text),
                                     include_coverage=True)
    result.pop("unavailable_sources", None)
    return result


def index(db, user, pr_id, request):
    pr, repo, config = context(db, user, pr_id, request, write=True)
    if not config or not config.enabled or config.paused:
        raise HTTPException(409, "Enable or resume the code graph in repository configuration first.")
    sha = comparison(db, repo, pr)[0] if request.revision == "base" else pr.head_sha
    finish(pr, repo, user, {})
    job_id = service.schedule_refresh(db, repo.id, commit_sha=sha, retry=True)
    if not job_id:
        raise HTTPException(409, "Graph indexing capacity is full. Retry after current work finishes.")
    return {"job_id": job_id, "commit_sha": sha}


def respond(callback):
    try:
        return callback()
    except service.AccessLost:
        raise HTTPException(403, "Code access is unavailable under the current permissions or repository policy.") from None
    except (ValueError, TypeError):
        raise HTTPException(422, "The comparison or code request is invalid. Refresh the pull request and retry.") from None
