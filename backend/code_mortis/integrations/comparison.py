"""Bounded immutable GitHub comparisons shared by browser diff and graph readers."""

import json
import os
from functools import lru_cache
from threading import Lock

from pydantic import TypeAdapter

from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.schemas.code_graph import GitSha
from code_mortis.schemas.pr_code import CodeFileInfo

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_lock = Lock()


def _after_fork():
    global _lock
    _lock = Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def load(repository_id, installation_id, full_name, base, head, pull_number=None):
    # Serialize cache admission so simultaneous screens cannot download the same comparison twice.
    with _lock:
        return cached(repository_id, installation_id, full_name, base, head, pull_number)


@lru_cache(maxsize=4)
def cached(repository_id, installation_id, full_name, base, head, pull_number=None):
    """At most four bounded immutable comparisons; never cache access or exclusions."""
    with transaction() as db:
        token = github.repository_read_token(db, installation_id, repository_id, pulls=pull_number is not None)
    data = github.request("GET", f"/repos/{full_name}/compare/{base}...{head}?per_page=1&page=1", token,
                          max_response_bytes=MAX_RESPONSE_BYTES)
    if (not isinstance(data, dict) or not isinstance(data.get("files"), list) or len(data["files"]) > 300
        or not isinstance(data.get("base_commit"), dict) or data["base_commit"].get("sha") != base
        or not isinstance(data.get("merge_base_commit"), dict)):
        raise ValueError("Invalid comparison")
    merge_base = TypeAdapter(GitSha).validate_python(data.get("merge_base_commit", {}).get("sha"))
    files = parse_files(data["files"])
    if pull_number is not None and any(not patch for _, patch in files):
        files = recover_patches(data["files"], files, token, repository_id, full_name, pull_number, base, head)
    # GitHub also reports zero counts when it omits a patch from a large comparison.
    files = [(info.model_copy(update={"additions": None, "deletions": None})
              if not patch and info.additions == info.deletions == 0 else info, patch) for info, patch in files]
    if len(json.dumps([(info.model_dump(), patch) for info, patch in files]).encode()) > MAX_RESPONSE_BYTES:
        raise github.GitHubError(422, "This comparison is too large to display. Open it on GitHub.")
    return merge_base, tuple(sorted(files, key=lambda pair: pair[0].path)), len(files) == 300


def parse_files(items):
    files = []
    paths = set()
    for item in items:
        if not isinstance(item, dict) or not {"filename", "status"} <= item.keys():
            raise ValueError("Invalid file")
        info = CodeFileInfo(path=item["filename"], previous_path=item.get("previous_filename"), change=item["status"],
                            additions=item.get("additions"), deletions=item.get("deletions"))
        patch = item.get("patch")
        if (info.path in paths or (info.change == "renamed" and not info.previous_path)
            or (patch is not None and not isinstance(patch, str))):
            raise ValueError("Invalid file")
        paths.add(info.path)
        files.append((info, patch))
    return files


def recover_patches(original, files, token, repository_id, full_name, pull_number, base, head):
    missing = {info.path for info, patch in files if not patch}
    expected = {item["filename"]: item for item in original}
    url = f"/repos/{full_name}/pulls/{pull_number}"

    def check_revision():
        pr = github.request("GET", url, token, max_response_bytes=MAX_RESPONSE_BYTES)
        if (not isinstance(pr, dict) or pr.get("number") != pull_number
            or not isinstance(pr.get("base"), dict) or not isinstance(pr.get("head"), dict)
            or not isinstance(pr["base"].get("repo"), dict)):
            raise ValueError("Invalid pull request")
        if (pr["base"].get("sha") != base or pr["head"].get("sha") != head
            or pr["base"]["repo"].get("id") != repository_id):
            raise github.GitHubError(409, "The pull request revision changed. Refresh the pull request before continuing.")

    check_revision()
    recovered, seen, remaining = {}, set(), MAX_RESPONSE_BYTES
    for page in range(1, 4):
        items = github.request("GET", f"{url}/files?per_page=100&page={page}", token,
                               max_response_bytes=remaining)
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("Invalid pull request files")
        remaining -= len(json.dumps(items).encode())
        if remaining <= 0:
            raise github.GitHubError(422, "This comparison is too large to display. Open it on GitHub.")
        for item, (info, patch) in zip(items, parse_files(items), strict=True):
            if info.path in seen:
                raise ValueError("Duplicate pull request file")
            seen.add(info.path)
            if info.path in missing:
                TypeAdapter(GitSha).validate_python(item.get("sha"))
                if any(item.get(key) != expected[info.path].get(key)
                       for key in ("status", "previous_filename", "sha")):
                    raise ValueError("Pull request file does not match the comparison")
                recovered[info.path] = info, patch
        if len(items) < 100 or missing <= recovered.keys():
            break
    check_revision()
    return [recovered.get(info.path, (info, patch)) for info, patch in files]

