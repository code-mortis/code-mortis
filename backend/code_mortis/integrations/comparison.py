"""Bounded immutable GitHub comparisons shared by browser diff and graph readers."""

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


def load(repository_id, installation_id, full_name, base, head):
    # Serialize cache admission so simultaneous screens cannot download the same comparison twice.
    with _lock:
        return cached(repository_id, installation_id, full_name, base, head)


@lru_cache(maxsize=4)
def cached(repository_id, installation_id, full_name, base, head):
    """At most four bounded immutable comparisons; never cache access or exclusions."""
    with transaction() as db:
        token = github.repository_read_token(db, installation_id, repository_id)
    data = github.request("GET", f"/repos/{full_name}/compare/{base}...{head}?per_page=1&page=1", token,
                          max_response_bytes=MAX_RESPONSE_BYTES)
    if (not isinstance(data, dict) or not isinstance(data.get("files"), list) or len(data["files"]) > 300
        or not isinstance(data.get("base_commit"), dict) or data["base_commit"].get("sha") != base
        or not isinstance(data.get("merge_base_commit"), dict)):
        raise ValueError("Invalid comparison")
    merge_base = TypeAdapter(GitSha).validate_python(data.get("merge_base_commit", {}).get("sha"))
    files = []
    paths = set()
    for item in data["files"]:
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
    return merge_base, tuple(sorted(files, key=lambda pair: pair[0].path)), len(files) == 300

