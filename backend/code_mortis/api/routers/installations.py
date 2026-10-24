from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from code_mortis.api.dependencies import ADMIN, DB, current_user
from code_mortis.api.oauth import auth_error, consume_state, start_attempt
from code_mortis.core.config import settings
from code_mortis.core.db import now
from code_mortis.integrations import github, installations
from code_mortis.models import Installation
from code_mortis.reviews.service import enqueue
from code_mortis.schemas.common import InstallationOperation, InstallationOut

router = APIRouter()


@router.get("/installations", response_model=list[InstallationOut])
def list_installations(db: DB, user: ADMIN):
    return installations.list_installations(db, user)


@router.delete("/installations/{installation_id}", response_model=InstallationOperation, status_code=202)
def uninstall_installation(installation_id: int, db: DB, user: ADMIN):
    installation = db.scalar(select(Installation).where(Installation.id == installation_id).with_for_update())
    if not installation:
        raise HTTPException(404, "Connected installation not found.")
    remote = installations.remote_installation(db, installation_id)
    job = installations.uninstall_job(db, installation_id)
    if remote is None:
        if job:
            return InstallationOperation(job_id=job.id)
        raise HTTPException(404, "This GitHub App installation has already been removed. Refresh the list.")
    installations.authorize_uninstall(db, user, remote)
    installation.account_id = remote.account.id
    if job and job.status in {"queued", "running", "done"}:
        return InstallationOperation(job_id=job.id)
    payload = {"installation_id": installation_id, "actor_id": user.id, "account_id": remote.account.id}
    if job:
        job.payload = {**job.payload, **payload}
        job.status, job.attempts, job.error = "queued", 0, None
        job.available_at, job.dispatched_at, job.lease_until = now(), None, None
    else:
        job = enqueue(db, "uninstall_installation", payload)
    return InstallationOperation(job_id=job.id)


@router.delete("/installations/{installation_id}/local")
def remove_installation(installation_id: int, db: DB, user: ADMIN) -> dict:
    installations.remove_installation(db, installation_id)
    return {"ok": True}


@router.get("/installations/start")
def start_installation(request: Request, db: DB, user: ADMIN):
    response = RedirectResponse("/", status_code=303)
    state, _ = start_attempt(request, response, db, "installation", str(user.id))
    slug = github.app_config(db).github_slug
    response.headers["location"] = f"https://github.com/apps/{slug}/installations/new?state={state}"
    return response


@router.get("/installations/callback")
def installation_callback(request: Request, db: DB, installation_id: int | None = None, state: str = ""):
    if not installation_id or installation_id < 1:
        return auth_error(request, "installation_cancelled")
    try:
        user = current_user(request, db)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse(
                settings().public_url + f"/api/v1/auth/login?installation_id={installation_id}",
                status_code=303,
            )
        return auth_error(request, "membership")
    if user.role != "admin":
        return auth_error(request, "installation_admin")
    if state:
        try:
            attempt = consume_state(request, db, state, "installation")
        except HTTPException:
            return auth_error(request, "expired")
        if attempt.return_path != str(user.id):
            return auth_error(request, "installation_denied")
    try:
        connect_installation(installation_id, db, user)
    except (HTTPException, github.GitHubError):
        return auth_error(request, "installation_denied")
    return RedirectResponse(settings().public_url + "/settings?tab=github&installed=1", status_code=303)


@router.get("/installations/available")
def available_installations(db: DB, user: ADMIN) -> list[dict]:
    rows = github.pages("/user/installations", github.user_token(db, user), "installations")
    app_id = int(github.app_config(db).github_app_id)
    return [
        {"id": row["id"], "account": row["account"]["login"]}
        for row in rows
        if row["app_id"] == app_id and not row.get("suspended_at")
    ]


@router.post("/installations/{installation_id}", status_code=202)
def connect_installation(installation_id: int, db: DB, user: ADMIN) -> dict:
    remote = installations.user_installations(db, user).get(installation_id)
    if not remote or remote.suspended_at:
        raise HTTPException(403, "This installation is not available to your GitHub account.")
    installation = db.scalar(select(Installation).where(Installation.id == installation_id).with_for_update())
    if installations.removing(installations.uninstall_job(db, installation_id)):
        raise HTTPException(409, "Uninstall is in progress. Retry uninstall or install the app again after removal.")
    if installation is None:
        installation = Installation(id=installation_id, account=remote.account.login)
        db.add(installation)
    installation.active = True
    installation.archived = False
    installation.uninstalled = False
    installation.account, installation.account_id = remote.account.login, remote.account.id
    enqueue(db, "sync_repositories", {"installation_id": installation_id})
    return {"ok": True}
