"""Trusted bare Git objects. All access holds the shared-volume lock; no checkout."""

import base64
import fcntl
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from uuid import UUID

from code_mortis.core.config import settings
from code_mortis.core.security import safe_repo_name
from code_mortis.integrations import ocr
from code_mortis.integrations.runner_client import RunnerBlocked
from code_mortis.schemas import code_graph as contracts

MAX_STORE_BYTES = 1024 * 1024 * 1024
MAX_REPOSITORY_BYTES = 256 * 1024 * 1024
EXCLUDED_DIRS = {
    ".git", ".agents", ".claude", ".codex", ".cursor", ".opencodereview", ".aws", ".ssh", ".azure", ".kube",
    "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__", "coverage", "generated",
}


def policy(repo):
    exclusions = repo.settings.get("exclusions", "")
    if not isinstance(exclusions, str) or len(exclusions) > 4000:
        raise ValueError("Invalid graph file exclusions.")
    patterns = sorted(set(p.strip() for p in re.split(r"[,\n]", exclusions) if p.strip()))
    if any(p.startswith("!") for p in patterns):
        raise ValueError("Graph exclusions accept deny globs, without negation.")
    return {"version": 1, "exclusions": patterns}


def policy_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def excluded(path, rules):
    parts = PurePosixPath(path.lower()).parts
    if any(p in EXCLUDED_DIRS or p.startswith(".env") or re.search(r"secret|credential|password|private[-_]?key", p)
           for p in parts):
        return True
    if parts[-1] in {"agents.md", "claude.md", "gemini.md", ".mcp.json", "copilot-instructions.md"}:
        return True
    if parts[-1].endswith((".pem", ".key", ".p12", ".pfx", ".lock", ".min.js", ".map")):
        return True
    return any(fnmatchcase(path, p) or (p.startswith("**/") and fnmatchcase(path, p[3:]))
               or path.startswith(p.rstrip("/") + "/") for p in rules["exclusions"])


@contextmanager
def locked(*, shared=False):
    root = settings().code_graph_store_dir
    if not shared:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("r" if shared else "a") as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerBlocked("busy") from None
        try:
            yield root
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def directory(repository_id):
    if type(repository_id) is not int or repository_id <= 0:
        raise ValueError("Invalid repository identity.")
    return settings().code_graph_store_dir / str(repository_id)


def reference(snapshot_id):
    return "refs/code-mortis/" + str(UUID(snapshot_id))


def git(root, *args, token=None, stopped=lambda: False, poll=lambda: None):
    if not root.is_dir():
        raise ValueError("Graph Git objects are unavailable.")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    if token:
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraHeader",
                   GIT_CONFIG_VALUE_0="Authorization: Basic " + encoded)
    config = {"core.hooksPath": "/dev/null", "credential.helper": "", "protocol.allow": "never",
              "protocol.https.allow": "always", "http.followRedirects": "false", "fetch.recurseSubmodules": "false",
              "core.fsmonitor": "false", "gc.auto": "0", "maintenance.auto": "false", "pack.windowMemory": "32m"}
    flags = [flag for k, v in config.items() for flag in ("-c", f"{k}={v}")]
    code, output, _ = ocr.command(["git", *flags, *args], root, env, timeout=180,
                                stopped=stopped, poll=poll, binary=True)
    if code:
        raise ValueError("The exact graph Git objects are unavailable; check repository access and retry.")
    return output


def disk_limit(root):
    for directory, limit in ((root, MAX_REPOSITORY_BYTES), (settings().code_graph_store_dir, MAX_STORE_BYTES)):
        total = 0
        for path in directory.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                # Git renames temporary packfiles while acquisition is being measured.
                continue
            if total > limit:
                raise ValueError("Graph object storage exceeds its configured disk limit.")


def acquire(repository_id, full_name, sha, snapshot_id, token, stopped):
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Invalid graph commit.")
    root = directory(repository_id)
    root.mkdir(exist_ok=True, mode=0o700)
    if not (root / "HEAD").exists():
        git(root, "init", "--bare", "--template=", ".", stopped=stopped)
    checked = 0.0

    def bound_disk():
        nonlocal checked
        if time.monotonic() - checked >= 1:
            disk_limit(root)
            checked = time.monotonic()

    git(root, "fetch", "--no-tags", "--no-write-fetch-head", "--depth=1", "--no-recurse-submodules",
        f"https://github.com/{safe_repo_name(full_name)}.git", sha, token=token, stopped=stopped, poll=bound_disk)
    disk_limit(root)
    if git(root, "cat-file", "-t", sha, stopped=stopped).strip() != b"commit":
        raise ValueError("Graph revision must be a commit.")
    git(root, "update-ref", reference(snapshot_id), sha, stopped=stopped)


def tree(root, sha, stopped=lambda: False):
    entries = git(root, "ls-tree", "-r", "-l", "-z", sha, stopped=stopped).split(b"\0")
    if len(entries) - 1 > contracts.MAX_FILES:
        raise ValueError("Graph tree exceeds 1000 files.")
    result = {}
    for entry in filter(None, entries):
        metadata, raw_path = entry.split(b"\t", 1)
        path = contracts.repository_path(raw_path.decode("utf-8"))
        mode, kind, blob, size = metadata.decode("ascii").split()
        result[path] = (mode, kind, blob, int(size) if size != "-" else 0)
    return result


def read_blob(root, blob, size, stopped=lambda: False):
    if size > contracts.MAX_FILE_BYTES:
        raise ValueError("Graph source exceeds the file limit.")
    raw = git(root, "cat-file", "blob", blob, stopped=stopped)
    if len(raw) != size or contracts.blob_sha(raw) != blob:
        raise ValueError("Graph source does not match its pinned blob.")
    return raw


def request(snapshot, stopped):
    root = directory(snapshot.repository_id)
    if git(root, "rev-parse", reference(snapshot.id), stopped=stopped).strip().decode() != snapshot.commit_sha:
        raise ValueError("Graph source revision is unavailable.")
    files, total = [], 0
    for path, (mode, kind, blob, size) in tree(root, snapshot.commit_sha, stopped).items():
        reason = "excluded" if mode not in {"100644", "100755"} or kind != "blob" or excluded(path, snapshot.policy) else None
        if reason is None and size > contracts.MAX_FILE_BYTES:
            reason = "file_too_large"
        content = None
        if reason is None:
            total += size
            if total > contracts.MAX_SOURCE_BYTES:
                raise ValueError("Graph source exceeds 8 MiB.")
            content = base64.b64encode(read_blob(root, blob, size, stopped)).decode()
        files.append(contracts.CodeGraphSource(path=path, blob_sha=blob, size_bytes=size,
                                              exclusion=reason, content_base64=content))
    return contracts.CodeGraphRequest(repository_id=snapshot.repository_id, commit_sha=snapshot.commit_sha,
                                     extractor_version=snapshot.extractor_version, policy_digest=snapshot.policy_digest,
                                     files=files)


def prune(repository_id, retained):
    root = directory(repository_id)
    if not (root / "HEAD").exists():
        return
    for ref in git(root, "for-each-ref", "--format=%(refname)", "refs/code-mortis/").decode().splitlines():
        if ref not in retained:
            git(root, "update-ref", "-d", ref)
    # The lock also covers source reads, so no consumer can lose an object mid-read.
    git(root, "repack", "-ad")
    git(root, "prune", "--expire=now")
