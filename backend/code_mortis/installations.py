"""GitHub installation ownership, removal, and retained repository history."""

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, select

from . import github, verification
from .db import transaction
from .models import Installation, Job, Membership, PullRequest, Repository, ReviewRun, User
from .schemas import InstallationOut


class Account(BaseModel):
    id: int = Field(strict=True, gt=0)
    login: str = Field(min_length=1, max_length=100)
    type: str


class RemoteInstallation(BaseModel):
    id: int = Field(strict=True, gt=0)
    app_id: int = Field(strict=True, gt=0)
    account: Account
    repository_selection: Literal["all", "selected"]
    permissions: dict[str, str] = Field(default_factory=dict)
    suspended_at: str | None = None


def parse_remote(db, data, identity=None):
    try:
        remote = RemoteInstallation.model_validate(data)
    except ValidationError as exc:
        raise github.GitHubError(502, "GitHub returned invalid installation information. Refresh and retry.") from exc
    if remote.app_id != int(github.app_config(db).github_app_id) or (identity is not None and remote.id != identity):
        raise github.GitHubError(403, "This installation does not belong to this GitHub App.")
    return remote


def remote_installation(db, identity):
    try:
        data = github.request("GET", f"/app/installations/{identity}", github.app_token(db))
    except github.GitHubError as exc:
        if exc.status == 404:
            return None
        raise
    return parse_remote(db, data, identity)


def user_installations(db, user):
    rows = github.pages("/user/installations", github.user_token(db, user), "installations")
    app_id = int(github.app_config(db).github_app_id)
    return {row["id"]: parse_remote(db, row) for row in rows if row.get("app_id") == app_id}


def owns(db, remote, user):
    if remote.account.type == "User":
        return remote.account.id == user.id
    if remote.account.type != "Organization":
        return False
    if remote.permissions.get("members") not in {"read", "write"}:
        raise github.GitHubError(
            403, "To verify organization ownership, enable Organization permissions → Members: Read-only "
            "in the GitHub App settings, approve the permission update for this installation, then refresh."
        )
    # The list endpoint can silently omit organizations when the app cannot read membership.
    row = github.request(
        "GET", f"/user/memberships/orgs/{quote(remote.account.login, safe='')}", github.user_token(db, user),
    )
    return (
        row.get("state") == "active" and row.get("role") == "admin"
        and type((row.get("user") or {}).get("id")) is int and row["user"]["id"] == user.id
        and type((row.get("organization") or {}).get("id")) is int
        and row["organization"]["id"] == remote.account.id
    )


def authorize_uninstall(db, user, remote):
    member = db.get(Membership, user.id) if user else None
    if not user or not user.enabled or not member or member.role != "admin":
        raise github.GitHubError(403, "Administrator access is required to uninstall this GitHub App.")
    if remote.id not in user_installations(db, user):
        raise github.GitHubError(403, "This installation is not available to your GitHub account.")
    if not owns(db, remote, user):
        raise github.GitHubError(403, "Only the GitHub account owner or an organization owner can uninstall this app.")


def uninstall_job(db, identity):
    return db.scalar(select(Job).where(
        Job.kind == "uninstall_installation", Job.payload["installation_id"].as_integer() == identity,
    ))


def removing(job):
    return bool(job and (job.status in {"queued", "running", "done"} or job.payload.get("delete_started")))


def list_installations(db, user):
    remote = {row.id: row for row in (
        parse_remote(db, data) for data in github.pages("/app/installations", github.app_token(db))
    )}
    visible = user_installations(db, user)
    local = {row.id: row for row in db.scalars(select(Installation))}
    results = {}
    for identity in sorted(local.keys() | visible.keys()):
        saved, current = local.get(identity), remote.get(identity)
        if saved and saved.archived:
            saved = None
        if saved is None and current is None:
            continue
        job = uninstall_job(db, identity)
        pending = bool(job and job.status in {"queued", "running"})
        accessible = current is not None and identity in visible
        owner, reason = False, None
        if accessible:
            try:
                owner = owns(db, current, user)
            except github.GitHubError as exc:
                reason = f"GitHub organization ownership could not be verified. {exc}"
        settings_url = None
        if accessible and current.account.type in {"User", "Organization"}:
            prefix = "settings" if current.account.type == "User" else f"organizations/{quote(current.account.login, safe='')}/settings"
            settings_url = f"https://github.com/{prefix}/installations/{identity}"
        if current and not owner and reason is None:
            reason = "Uninstall requires the GitHub account owner or an organization owner."
        account_id = current.account.id if current else saved.account_id
        key = ("account", account_id) if account_id is not None else ("installation", identity)
        priority = (current is not None, pending, identity)
        if key in results and results[key][0] > priority:
            continue
        results[key] = (priority, InstallationOut(
            id=identity, account=current.account.login if current else saved.account,
            connected=saved is not None, active=bool(saved and saved.active),
            status="uninstalled" if current is None else "suspended" if current.suspended_at else "active",
            settings_url=settings_url,
            repository_selection=current.repository_selection if current else None,
            can_connect=bool(accessible and not current.suspended_at and not removing(job)),
            can_uninstall=bool(saved and owner and not pending), unavailable_reason=reason,
            can_remove=bool(saved and current is None and not pending),
            job_id=job.id if job else None,
            operation_status=job.status if job else None, operation_error=job.error if job else None,
        ))
    return sorted((row for _, row in results.values()), key=lambda row: (row.account.casefold(), row.id))


def remove_installation(db, identity):
    saved = db.get(Installation, identity)
    if saved is None:
        raise github.GitHubError(404, "Connected installation not found.")
    targets = list(db.scalars(select(Installation).where(
        Installation.account_id == saved.account_id if saved.account_id is not None else Installation.id == identity,
    ).order_by(Installation.id).with_for_update()))
    remote = {row.id for row in (
        parse_remote(db, data) for data in github.pages("/app/installations", github.app_token(db))
    )}
    if any(item.id in remote for item in targets):
        raise github.GitHubError(409, "This account still has an installed GitHub App. Uninstall it before removing it from the list.")
    if any((job := uninstall_job(db, item.id)) and job.status in {"queued", "running"} for item in targets):
        raise github.GitHubError(409, "Wait for uninstall to finish before removing this account from the list.")
    for item in targets:
        if db.scalar(select(Repository.id).where(Repository.installation_id == item.id).limit(1)):
            deactivate(db, item, "The GitHub App installation was removed.")
            item.archived = True
        else:
            db.delete(item)


def deactivate_repositories(db, repositories, reason):
    from .assessment import assessment_only
    from .repository_security import cancel_access
    from .reviews import stop_review

    ids = []
    for repo in repositories:
        repo.active, repo.enabled = False, False
        ids.append(repo.id)
    if not ids:
        return
    cancel_access(db, repository_ids=ids, reason=reason)
    # Share create_run's PR-before-run lock order so concurrent requests cannot escape cancellation.
    db.execute(select(PullRequest.id).where(PullRequest.repository_id.in_(ids)).order_by(
        PullRequest.id,
    ).with_for_update()).all()
    for run in db.scalars(select(ReviewRun).join(PullRequest).where(
        PullRequest.repository_id.in_(ids),
        or_(ReviewRun.status.in_({"queued", "running", "blocked"}),
            ReviewRun.publication.in_({"pending", "publishing", "failed", "partially_published"})),
    ).with_for_update(of=ReviewRun)):
        run.cancel_requested = True
        if run.status in {"queued", "running", "blocked"}:
            stop_review(db, run, "cancelled", reason,
                        publication="published" if run.publication == "published" else "partially_published" if run.review_ids else "not_applicable")
            run.error = reason
        else:
            verification.skip_pending(db, run, reason)
        if run.publication != "published" and not assessment_only(run):
            run.publication = "partially_published" if run.review_ids else "not_applicable"
            run.publication_error = reason


def deactivate(db, installation, reason):
    installation.active = False
    deactivate_repositories(db, db.scalars(select(Repository).where(
        Repository.installation_id == installation.id,
    )), reason)


def uninstall(payload):
    identity = payload["installation_id"]
    with transaction() as db:
        installation = db.scalar(select(Installation).where(Installation.id == identity).with_for_update())
        if not installation or installation.archived:
            return
        job = db.get(Job, payload["_job_id"])
        remote = remote_installation(db, identity)
        if remote is None:
            installation.uninstalled = True
            deactivate(db, installation, "The GitHub App installation was removed.")
            return
        user = db.get(User, payload["actor_id"])
        authorize_uninstall(db, user, remote)
        if remote.account.id != payload["account_id"]:
            raise github.GitHubError(403, "The GitHub installation account changed. Refresh and retry.")
        deactivate(db, installation, "GitHub App uninstall was requested. Reviews are paused.")
        job.payload = {**job.payload, "delete_started": True}
        token = github.app_token(db)
        accepted = job.payload.get("delete_accepted", False)
    # Persist the stop before a remote write whose response might be lost.
    if not accepted:
        try:
            github.request("DELETE", f"/app/installations/{identity}", token)
        except github.GitHubError as exc:
            if exc.status != 404:
                raise
        with transaction() as db:
            job = db.get(Job, payload["_job_id"])
            job.payload = {**job.payload, "delete_accepted": True}
    with transaction() as db:
        if remote_installation(db, identity) is not None:
            raise github.GitHubError(503, "GitHub has not confirmed uninstall yet. Reviews remain paused; retry to check again.")
        db.get(Installation, identity).uninstalled = True
