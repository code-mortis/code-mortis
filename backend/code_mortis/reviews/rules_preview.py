"""Read trusted review configuration at the repository's current default-branch commit."""

import base64
import hashlib
import re
from functools import cache
from pathlib import PurePosixPath
from time import monotonic
from urllib.parse import quote

from code_mortis.core import source
from code_mortis.core.security import safe_repo_name
from code_mortis.integrations import github
from code_mortis.reviews import context, planning


def _oid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise ValueError("GitHub did not return a valid identifier for the rule snapshot.")
    return value


def preview(full_name, branch, token, path, options, credentials, *, prompt_token_ceiling, workspace_count=0):
    source.repository_path(path)
    safe_repo_name(full_name)
    if not isinstance(branch, str) or not branch or len(branch) > 255:
        raise ValueError("GitHub did not return a valid default branch.")
    started = monotonic()
    reads = 0

    def check():
        if monotonic() - started > 20:
            raise TimeoutError("Testing review rules took too long. Narrow the path or glob patterns and try again.")

    def read(endpoint, maximum):
        nonlocal reads
        check()
        reads += 1
        if reads > 64:
            raise ValueError("The trusted-base rules required too many GitHub reads.")
        response = github.request("GET", f"/repos/{full_name}/git/{endpoint}", token, max_response_bytes=maximum)
        check()
        if not isinstance(response, dict):
            raise ValueError("GitHub did not return a valid rule snapshot.")
        return response

    ref = read(f"ref/heads/{quote(branch, safe='')}", 32 * 1024)
    target = ref.get("object")
    if (ref.get("ref") != f"refs/heads/{branch}" or not isinstance(target, dict)
            or target.get("type") != "commit"):
        raise ValueError("GitHub did not return the default-branch commit.")
    base_sha = _oid(target.get("sha"))
    commit = read(f"commits/{base_sha}", 512 * 1024)
    root = commit.get("tree")
    if commit.get("sha") != base_sha or not isinstance(root, dict):
        raise ValueError("GitHub did not return the pinned default-branch commit.")
    root_sha = _oid(root.get("sha"))

    @cache
    def tree(sha):
        response = read(f"trees/{sha}", 2 * 1024 * 1024)
        entries = response.get("tree")
        if response.get("sha") != sha or response.get("truncated") is not False or not isinstance(entries, list):
            raise ValueError("GitHub did not return the complete tree needed to read review rules.")
        by_name = {}
        for entry in entries:
            name = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name or "/" in name or name in by_name:
                raise ValueError("GitHub returned an invalid tree entry for the rule snapshot.")
            by_name[name] = entry
        return by_name

    def entry_at(rule_path):
        sha = root_sha
        directories, name = PurePosixPath(rule_path).parts[:-1], PurePosixPath(rule_path).name
        for directory in directories:
            entry = tree(sha).get(directory)
            if entry is None or entry.get("type") != "tree":
                return None
            if entry.get("mode") != "040000":
                raise ValueError("GitHub returned an invalid directory entry for the rule snapshot.")
            sha = _oid(entry.get("sha"))
        entry = tree(sha).get(name)
        if entry is None:
            return None
        size = entry.get("size")
        if (entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}
                or type(size) is not int or size < 0):
            raise ValueError("A trusted-base review rule is not a regular text file.")
        return {"blob_sha": _oid(entry.get("sha")), "size": size}

    @cache
    def read_blob(sha, size):
        response = read(f"blobs/{sha}", 1024 * 1024)
        content = response.get("content")
        if (response.get("sha") != sha or type(response.get("size")) is not int or response["size"] != size
                or response.get("encoding") != "base64" or not isinstance(content, str)):
            raise ValueError("GitHub did not return the pinned review rule blob.")
        try:
            raw = base64.b64decode(content.replace("\n", ""), validate=True)
            text = raw.decode("utf-8")
        except ValueError:
            raise ValueError("A trusted-base review rule is not readable as text.") from None
        if (len(raw) != size or hashlib.sha1(f"blob {size}\0".encode() + raw).hexdigest() != sha
                or "\x00" in text or "\ufffd" in text):
            raise ValueError("GitHub did not return the pinned review rule as text.")
        return text

    legacy = planning.load_legacy_rules(base_sha, options, entry_at,
                                        lambda _path, entry: read_blob(entry["blob_sha"], entry["size"]), check=check)
    instructions, matches = planning.resolve_rules(path, legacy, options["review_rules"],
                                                  workspace_count=workspace_count, check=check)
    reviewable = planning.allowed_path(path, options, legacy, check=check)
    safe = context.sanitize({"rule": instructions, "path": path}, credentials)
    for match in matches:
        for key in match.keys() - {"source"}:
            match[key] = context.sanitize(match[key], credentials)
    check()
    return {"branch": branch, "base_sha": base_sha, "reviewable": reviewable,
            "matches": matches, "instructions": safe["rule"],
            "rule_size_exceeds_limit": len((instructions + path).encode()) + 512 >= planning.unit_byte_limit(prompt_token_ceiling)}
