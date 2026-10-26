"""Internal graph lifecycle. Browser activation and retrieval routes are separate phases."""

import time
from datetime import timedelta
from urllib.parse import quote

from pydantic import TypeAdapter
from sqlalchemy import func, insert, select, text

from code_mortis.api_tokens import provenance as pat
from code_mortis.code_graph import client, store
from code_mortis.core.db import now, transaction, uid
from code_mortis.integrations import github, ocr
from code_mortis.mcp import provenance as mcp
from code_mortis.models import (
    CodeGraphFile,
    CodeGraphImport,
    CodeGraphRepository,
    CodeGraphSnapshot,
    CodeGraphSymbol,
    Installation,
    Job,
    Membership,
    Repository,
    User,
)
from code_mortis.schemas.code_graph import EXTRACTOR_VERSION, GitSha

ACTIVE = {"queued", "running"}
USABLE = {"ready", "partial"}
LEASE_SECONDS = 600


class AccessLost(ValueError):
    pass


def browser_only():
    if pat.current_provenance() is not None or mcp.current_provenance() is not None:
        raise AccessLost("Existing token and MCP grants do not authorize code graph access.")


def local_access(db, repository_id, actor_id, *, enabled=True):
    browser_only()
    repo, actor = db.get(Repository, repository_id), db.get(User, actor_id) if actor_id else None
    installation = db.get(Installation, repo.installation_id) if repo else None
    config = db.get(CodeGraphRepository, repository_id)
    if (not repo or not repo.active or not installation or not installation.active or installation.uninstalled
        or installation.archived or not actor or not actor.enabled or not db.get(Membership, actor.id)):
        raise AccessLost("Graph repository access is no longer available.")
    if enabled and (not config or not config.enabled or config.actor_id != actor_id):
        raise AccessLost("Graph indexing was disabled or its initiating user changed.")
    return repo, actor, config


def authorize(repository_id, actor_id, *, enabled=True):
    with transaction() as db:
        repo, actor, _ = local_access(db, repository_id, actor_id, enabled=enabled)
        try:
            remote = github.authorize_repo(db, actor, repo.full_name, fresh=True)
        except github.GitHubError as exc:
            if exc.status in {401, 403, 404}:
                raise AccessLost("Graph repository access was revoked.") from None
            raise
        if type(remote.get("id")) is not int or remote["id"] != repo.id:
            raise AccessLost("GitHub repository identity changed.")
        return repo, remote


def configure(repository_id, actor_id, *, enabled):
    """Administration primitive for phase 1E; never called by existing settings routes."""
    browser_only()
    with transaction() as db:
        actor, member = db.get(User, actor_id), db.get(Membership, actor_id)
        if not actor or not actor.enabled or not member or member.role != "admin":
            raise AccessLost("Graph activation requires workspace administration access.")
    if enabled:
        authorize(repository_id, actor_id, enabled=False)
    with transaction() as db:
        repo = db.scalar(select(Repository).where(Repository.id == repository_id).with_for_update())
        if not repo:
            raise AccessLost("Repository unavailable.")
        actor, member = db.get(User, actor_id), db.get(Membership, actor_id)
        if not actor or not actor.enabled or not member or member.role != "admin":
            raise AccessLost("Graph activation requires workspace administration access.")
        config = db.get(CodeGraphRepository, repository_id)
        if not config:
            config = CodeGraphRepository(repository_id=repository_id)
            db.add(config)
        changed = config.actor_id != actor_id or not enabled
        config.enabled, config.actor_id = enabled, actor_id
        if changed:
            for row in db.scalars(select(CodeGraphSnapshot).where(
                CodeGraphSnapshot.repository_id == repository_id, CodeGraphSnapshot.status.in_(ACTIVE),
            ).with_for_update()):
                row.cancel_requested = True
                row.status, row.finished_at, row.error = "cancelled", now(), "Graph indexing was disabled or reconfigured."


def enqueue_snapshot(repository_id, commit_sha=None, *, retry=False):
    browser_only()
    with transaction() as db:
        config = db.get(CodeGraphRepository, repository_id)
        actor_id = config.actor_id if config else None
    repo, remote = authorize(repository_id, actor_id)
    branch = remote.get("default_branch")
    if not isinstance(branch, str) or not branch or len(branch) > 255:
        raise ValueError("GitHub did not return a valid default branch.")
    with transaction() as db:
        token = github.repository_read_token(db, repo.installation_id, repo.id)
    head = github.request("GET", f"/repos/{repo.full_name}/commits/{quote(branch, safe='')}", token)
    default_sha = TypeAdapter(GitSha).validate_python(head.get("sha"))
    sha = TypeAdapter(GitSha).validate_python(commit_sha if commit_sha is not None else default_sha)
    with transaction() as db:
        # One admission lock bounds work across repositories and deduplicates concurrent callers.
        db.execute(text("SELECT pg_advisory_xact_lock(724103091)"))
        db.scalar(select(Repository).where(Repository.id == repository_id).with_for_update())
        current, _, _ = local_access(db, repository_id, actor_id)
        if (current.full_name, current.installation_id) != (repo.full_name, repo.installation_id):
            raise AccessLost("Repository identity changed during admission.")
        rules = store.policy(current)
        identity = dict(repository_id=repository_id, commit_sha=sha, extractor_version=EXTRACTOR_VERSION,
                        policy_digest=store.policy_digest(rules))
        row = db.scalar(select(CodeGraphSnapshot).filter_by(**identity).with_for_update())
        if row and (row.status in ACTIVE | USABLE or not retry):
            if row.status in USABLE and sha == default_sha:
                db.get(CodeGraphRepository, repository_id).default_sha = sha
            return row.id
        active = select(func.count()).select_from(CodeGraphSnapshot).where(CodeGraphSnapshot.status.in_(ACTIVE))
        if db.scalar(active) >= 16 or db.scalar(active.where(CodeGraphSnapshot.repository_id == repository_id)) >= 2:
            raise ValueError("Graph indexing capacity is full; wait for existing work to finish.")
        if row is None:
            row = CodeGraphSnapshot(id=uid(), **identity)
            db.add(row)
        job = Job(id=uid(), kind="code_graph", payload={"snapshot_id": row.id, "repository_id": repository_id,
                                                        "default_branch": sha == default_sha})
        db.add(job)
        db.flush()
        row.job_id, row.actor_id, row.policy = job.id, actor_id, rules
        row.status, row.cancel_requested, row.error = "queued", False, None
        row.started_at = row.finished_at = None
        return row.id


def claimed(db, snapshot_id, job_id, attempt, *, lock=False):
    query = select(Job).where(Job.id == job_id)
    job = db.scalar(query.with_for_update() if lock else query)
    row = db.get(CodeGraphSnapshot, snapshot_id)
    if (not job or not row or row.job_id != job_id or job.status != "running" or job.attempts != attempt
        or not job.lease_until or job.lease_until <= now() or row.cancel_requested or row.status not in ACTIVE):
        raise client.ExtractionCancelled("Graph indexing was cancelled or its worker lease expired.")
    return row, job


def valid_work(db, row):
    repo, _, config = local_access(db, row.repository_id, row.actor_id)
    if row.extractor_version != EXTRACTOR_VERSION or row.policy != store.policy(repo):
        raise client.ExtractionCancelled("Graph extraction policy changed; request a new snapshot.")
    return repo, config


def cancel(snapshot_id, actor_id):
    with transaction() as db:
        row = db.get(CodeGraphSnapshot, snapshot_id)
        if row is None:
            raise ValueError("Graph snapshot unavailable.")
        repository_id = row.repository_id
    authorize(repository_id, actor_id)
    with transaction() as db:
        row = db.scalar(select(CodeGraphSnapshot).where(CodeGraphSnapshot.id == snapshot_id).with_for_update())
        if row and row.status in ACTIVE:
            row.cancel_requested = True
            row.status, row.finished_at, row.error = "cancelled", now(), "Graph indexing was cancelled."


def settle(db, job, *, expired=False):
    """Called while the durable Job is locked, including worker-loss recovery."""
    row = db.scalar(select(CodeGraphSnapshot).where(CodeGraphSnapshot.id == job.payload["snapshot_id"]).with_for_update())
    if not row or row.job_id != job.id:
        job.status, job.lease_until = "failed", None
        return
    if row.status not in ACTIVE:
        job.status, job.lease_until = ("done" if row.status in USABLE else "failed"), None
        job.error = row.error
        return
    if expired and job.attempts < 3 and not row.cancel_requested:
        row.status, row.error = "queued", "Worker lease expired; waiting for recovery."
    else:
        row.status = "cancelled" if row.cancel_requested else "failed"
        row.finished_at, row.error = now(), "Graph worker stopped; retry explicitly."


def perform(snapshot_id, job_id, attempt):
    with store.locked():
        try:
            with transaction() as db:
                row, _ = claimed(db, snapshot_id, job_id, attempt, lock=True)
                repo, _ = valid_work(db, row)
                row.status, row.started_at = "running", now()
            authorize(row.repository_id, row.actor_id)
            with transaction() as db:
                current, _ = claimed(db, snapshot_id, job_id, attempt)
                repo, _ = valid_work(db, current)
                token = github.repository_read_token(db, repo.installation_id, repo.id)

            checked = 0.0

            def stopped():
                nonlocal checked
                if time.monotonic() - checked < 1:
                    return False
                try:
                    with transaction() as db:
                        current, _ = claimed(db, snapshot_id, job_id, attempt)
                        valid_work(db, current)
                except AccessLost as exc:
                    raise client.ExtractionCancelled(str(exc)) from None
                checked = time.monotonic()
                return False

            store.acquire(repo.id, repo.full_name, row.commit_sha, row.id, token, stopped)
            payload = store.request(row, stopped)
            result = client.extract(payload, stopped=stopped)
            client.validate_result(payload, result)
            authorize(row.repository_id, row.actor_id)
            with transaction() as db:
                current, job = claimed(db, snapshot_id, job_id, attempt, lock=True)
                db.scalar(select(Repository).where(Repository.id == row.repository_id).with_for_update())
                db.scalar(select(CodeGraphSnapshot).where(CodeGraphSnapshot.id == snapshot_id)
                          .with_for_update().execution_options(populate_existing=True))
                current, _ = claimed(db, snapshot_id, job_id, attempt)
                _, config = valid_work(db, current)
                for model, values in (
                    (CodeGraphFile, [dict(snapshot_id=row.id, **f.model_dump()) for f in result.files]),
                    (CodeGraphSymbol, [dict(snapshot_id=row.id, key=s.key, **s.model_dump()) for s in result.symbols]),
                    (CodeGraphImport, [dict(snapshot_id=row.id, **i.model_dump()) for i in result.imports]),
                ):
                    if values:
                        db.execute(insert(model), values)
                current.status = "partial" if any(f.status in {"partial", "failed", "unsupported"}
                                                  or f.reason == "file_too_large" for f in result.files) else "ready"
                current.finished_at, current.error = now(), None
                if job.payload["default_branch"]:
                    config.default_sha = current.commit_sha
        except Exception as exc:
            with transaction() as db:
                job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                current = db.scalar(select(CodeGraphSnapshot).where(CodeGraphSnapshot.id == snapshot_id).with_for_update())
                if job and current and current.job_id == job_id and job.attempts == attempt and job.status == "running" and current.status in ACTIVE:
                    current.status = "cancelled" if isinstance(exc, (AccessLost, client.ExtractionCancelled, ocr.ReviewStopped)) else "failed"
                    current.finished_at = now()
                    current.error = ("Graph access or indexing policy changed; request a new snapshot."
                                     if current.status == "cancelled" else "Graph extraction failed; check parser availability, limits and Git access, then retry explicitly.")
            raise ValueError("Graph indexing did not finish; inspect its snapshot status before retrying.") from None
        finally:
            cleanup_locked()


def cleanup_locked():
    """The shared-volume lock excludes extraction and bounded source consumers."""
    retained = {}
    with transaction() as db:
        for repository_id in db.scalars(select(CodeGraphSnapshot.repository_id).distinct()):
            config = db.get(CodeGraphRepository, repository_id)
            rows = list(db.scalars(select(CodeGraphSnapshot).where(CodeGraphSnapshot.repository_id == repository_id)
                                   .order_by(CodeGraphSnapshot.finished_at.desc().nulls_last(), CodeGraphSnapshot.id)
                                   .with_for_update()))
            default = next((r for r in rows if r.status in USABLE and config and r.commit_sha == config.default_sha), None)
            other = next((r for r in rows if r.status in USABLE and r is not default
                          and r.finished_at and r.finished_at > now() - timedelta(days=7)), None)
            keep = {r.id for r in (default, other) if r}
            for row in rows:
                if row.status in ACTIVE:
                    job = db.get(Job, row.job_id) if row.job_id else None
                    if not job or job.status in {"done", "failed"}:
                        row.status, row.finished_at, row.error = "failed", now(), "Graph worker stopped; retry explicitly."
                    else:
                        keep.add(row.id)
                if row.status in USABLE and row.id not in keep:
                    db.delete(row)
                elif row.status not in ACTIVE | USABLE and (row.finished_at or row.created_at) < now() - timedelta(days=7):
                    db.delete(row)
            retained[repository_id] = {store.reference(identity) for identity in keep}
    # Also remove orphan refs after a repository cascade/delete or interrupted transaction.
    for root in store.settings().code_graph_store_dir.iterdir():
        if root.is_dir() and root.name.isdecimal():
            store.prune(int(root.name), retained.get(int(root.name), set()))


def cleanup():
    with store.locked():
        cleanup_locked()


def source(snapshot_id, actor_id, path):
    """Internal exact blob primitive; the lock pins it until the bytes are copied."""
    browser_only()
    with transaction() as db:
        row = db.get(CodeGraphSnapshot, snapshot_id)
        if not row or row.status not in USABLE:
            raise ValueError("Graph snapshot unavailable.")
    authorize(row.repository_id, actor_id)
    with store.locked(), transaction() as db:
        current = db.get(CodeGraphSnapshot, snapshot_id)
        if not current or current.status not in USABLE:
            raise ValueError("Graph snapshot unavailable.")
        repo, _, _ = local_access(db, current.repository_id, actor_id)
        file = db.get(CodeGraphFile, (snapshot_id, path))
        if (not file or file.status in {"excluded", "failed"} or current.policy != store.policy(repo)
            or store.excluded(path, current.policy)):
            raise ValueError("Graph source unavailable under the current policy.")
        root = store.directory(current.repository_id)
        if store.git(root, "rev-parse", store.reference(snapshot_id)).strip().decode() != current.commit_sha:
            raise ValueError("Graph source revision is unavailable.")
        entry = store.tree(root, current.commit_sha).get(path)
        if not entry or entry[:2] not in {("100644", "blob"), ("100755", "blob")} or entry[2:] != (file.blob_sha, file.size_bytes):
            raise ValueError("Graph source does not match its pinned tree.")
        return store.read_blob(root, file.blob_sha, file.size_bytes)
