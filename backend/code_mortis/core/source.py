"""Authorized Git objects and inert source snapshots; no inference adapter dependency."""

import base64
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from code_mortis.core.config import settings
from code_mortis.core.errors import ReviewStopped
from code_mortis.core.process import child_environment, command
from code_mortis.core.security import safe_repo_name

PR_FETCH_TIMEOUT_SECONDS = 360


def git(directory: Path, env: dict, *args: str, stopped=lambda: False, timeout: int = 180) -> str:
    code, output, _ = command(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "credential.helper=",
            *args,
        ],
        directory,
        env,
        timeout=timeout,
        stopped=stopped,
    )
    if code:
        raise ValueError(
            "Git could not fetch or inspect the requested PR commits. Check installation access and retry."
        )
    return output


def repository_path(value):
    path = PurePosixPath(value)
    if (not value or not path.parts or path.as_posix() != value or path.is_absolute() or ".." in path.parts
        or ".git" in path.parts or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Source path must stay inside the repository.")
    return value


def read_source(repo, env, sha, path, stopped, *, git=git):
    repository_path(path)
    entry = git(repo, env, "ls-tree", "-z", sha, "--", path, stopped=stopped)
    if (not re.match(r"^100(?:644|755) blob [0-9a-f]{40}\t", entry)
        or entry.rstrip("\0\n").partition("\t")[2] != path):
        raise ValueError(f"The source file {path!r} is missing or is not a regular file in commit {sha}. Check the original and current code manually.")
    source = git(repo, env, "show", f"{sha}:{path}", stopped=stopped)
    if "\x00" in source or "\ufffd" in source:
        raise ValueError("A file needed to verify this finding is binary or is not readable as text. Check it manually.")
    return source


@contextmanager
def review_snapshot(full_name, number, base_sha, head_sha, token, stopped, *, timeout_seconds=None, git=git, environment=child_environment):
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    cancelled = stopped

    def stopped():
        if cancelled():
            return True
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("The source snapshot reached its time limit.")
        return False

    safe_repo_name(full_name)
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base_sha, head_sha)):
        raise ValueError("The review has an invalid Git commit reference. Refresh the pull request and start a new review.")
    root = settings().work_dir
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="review-", dir=root) as temporary:
        directory = Path(temporary)
        repo = directory / "repo"
        repo.mkdir()
        env = environment()
        # Fetch credentials stay in this process environment, scoped to GitHub.
        # Temporary workspaces are noexec; no credential helper is written there.
        authorization = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        fetch_env = {
            **env,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {authorization}",
        }
        git(repo, env, "init", "--quiet", stopped=stopped)
        git(repo, env, "remote", "add", "origin", f"https://github.com/{full_name}.git", stopped=stopped)
        fetch_refs = [f"{base_sha}:refs/mortis/base", f"refs/pull/{number}/head:refs/mortis/head"]
        git(repo, fetch_env, "fetch", "--no-tags", "--depth=500", "origin", *fetch_refs, stopped=stopped,
            timeout=PR_FETCH_TIMEOUT_SECONDS)
        fetched_head = git(repo, env, "rev-parse", "refs/mortis/head", stopped=stopped).strip()
        if fetched_head != head_sha:
            raise ReviewStopped("The pull request changed before its commits could be fetched.")
        # Checkout only the trusted base revision; proposed changes stay in Git objects.
        git(repo, env, "checkout", "--detach", "--quiet", base_sha, stopped=stopped)
        try:
            merge_base = git(repo, env, "merge-base", base_sha, head_sha, stopped=stopped).strip()
        except ValueError:
            git(repo, fetch_env, "fetch", "--unshallow", "--no-tags", "origin", *fetch_refs, stopped=stopped,
                timeout=PR_FETCH_TIMEOUT_SECONDS)
            merge_base = git(repo, env, "merge-base", base_sha, head_sha, stopped=stopped).strip()
        # Check that trusted rules cannot lead the resolver outside the checkout through symlinks.
        for path in (repo / ".opencodereview", repo / ".opencodereview" / "rule.json"):
            if path.exists() and not path.resolve().is_relative_to(repo.resolve()):
                raise ValueError("Repository review rules point outside the checkout.")
        yield repo, env, fetch_env, merge_base


def review_paths(repo, env, base, head, paths, options, stopped, *, git=git):
    """A rename/copy cannot restore access through its other path."""
    from code_mortis.core import file_policy

    rules = file_policy.policy(options)
    allowed = {path for path in paths if not file_policy.excluded(path, rules)}
    if not allowed:
        return allowed
    names = git(repo, env, "diff", "--no-ext-diff", "--no-textconv", "--find-renames", "--name-status", "-z",
                base, head, stopped=stopped).rstrip("\0").split("\0")
    index = 0
    while index < len(names) and names[index]:
        status, old = names[index:index + 2]
        repository_path(old)
        index += 2
        if status.startswith(("R", "C")):
            target = names[index]
            repository_path(target)
            index += 1
            if file_policy.excluded(old, rules) or file_policy.excluded(target, rules):
                allowed.difference_update((old, target))
    return allowed
