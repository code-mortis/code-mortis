import hmac
import json
import secrets
from datetime import timedelta
from importlib.resources import files
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import github
from .config import settings
from .db import get_db, now
from .models import (
    Activity,
    AppConfig,
    Connection,
    Finding,
    Installation,
    Job,
    LoginSession,
    Membership,
    OAuthState,
    Occurrence,
    PullRequest,
    Repository,
    ReviewRun,
    User,
    WebhookDelivery,
)
from .reviews import create_run, enqueue, request_finding_status, upsert_pr
from .schemas import (
    ActivityOut,
    ConnectionInput,
    ConnectionOut,
    FindingAction,
    FindingDetail,
    FindingOut,
    MemberInput,
    OccurrenceOut,
    PRDetail,
    PullRequestOut,
    RepositoryInput,
    RepositoryOut,
    RunDetail,
    RunOut,
    UserOut,
)
from .security import digest, safe_endpoint, seal, unseal, valid_signature

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB) -> User:
    token = request.cookies.get("cm_session", "")
    session = db.get(LoginSession, digest(token)) if token else None
    if not session or session.expires_at.timestamp() <= now().timestamp():
        raise HTTPException(401, "Sign in with GitHub to continue.")
    user = db.get(User, session.user_id)
    if not user or not user.enabled or not db.get(Membership, user.id):
        raise HTTPException(403, "Your account is not allowed on this instance.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "Your session changed. Refresh the page and retry.")
        if request.headers.get("origin") not in {None, settings().public_url}:
            raise HTTPException(403, "Request origin does not match this instance.")
    request.state.login_session = session
    return user


USER = Annotated[User, Depends(current_user)]


def admin(user: USER) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access is required.")
    return user


ADMIN = Annotated[User, Depends(admin)]


def bootstrap(request: Request, db: Session):
    if db.get(AppConfig, 1):
        raise HTTPException(409, "This instance is already configured.")
    if not hmac.compare_digest(request.headers.get("x-setup-token", ""), settings().bootstrap_token):
        raise HTTPException(403, "Enter the bootstrap token from your instance configuration.")


def state_cookie(response: Response, state: str):
    response.set_cookie(
        "cm_oauth",
        state,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="lax",
        max_age=600,
        path="/api/v1",
    )


def consume_state(request: Request, db: Session, state: str, purpose: str):
    if not state or not hmac.compare_digest(request.cookies.get("cm_oauth", ""), state):
        raise HTTPException(400, "Sign-in state did not match. Start again.")
    found = db.execute(
        delete(OAuthState)
        .where(
            OAuthState.state == digest(state), OAuthState.purpose == purpose, OAuthState.expires_at > now()
        )
        .returning(OAuthState.state)
    ).scalar_one_or_none()
    if not found:
        raise HTTPException(400, "This sign-in attempt has expired or was already used.")


@router.get("/setup/status")
def setup_status(db: DB) -> dict:
    return {"configured": db.get(AppConfig, 1) is not None, "public_url": settings().public_url}


class ManifestInput(BaseModel):
    organization: str = Field(default="", pattern=r"^[a-zA-Z0-9-]*$", max_length=39)


@router.post("/setup/manifest")
def manifest(body: ManifestInput, request: Request, response: Response, db: DB) -> dict:
    bootstrap(request, db)
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=digest(state), purpose="manifest", expires_at=now() + timedelta(minutes=10)))
    state_cookie(response, state)
    prefix = f"organizations/{body.organization}/settings" if body.organization else "settings"
    origin = settings().public_url
    return {
        "action": f"https://github.com/{prefix}/apps/new?state={state}",
        "manifest": {
            "name": f"Code Mortis {secrets.token_hex(3)}",
            "url": origin,
            "hook_attributes": {"url": origin + "/api/v1/webhooks/github", "active": True},
            "redirect_url": origin + "/api/v1/setup/callback",
            "callback_urls": [origin + "/api/v1/auth/callback"],
            "setup_url": origin + "/settings?tab=github",
            "public": True,
            "request_oauth_on_install": True,
            "default_permissions": {"contents": "read", "pull_requests": "write", "metadata": "read"},
            "default_events": ["pull_request", "pull_request_review_thread", "pull_request_review_comment"],
        },
    }


@router.get("/setup/callback")
def manifest_callback(request: Request, db: DB, state: str = "", code: str = ""):
    if db.get(AppConfig, 1):
        raise HTTPException(409, "This instance is already configured.")
    consume_state(request, db, state, "manifest")
    if not code or len(code) > 200 or not code.isalnum():
        raise HTTPException(400, "Invalid GitHub App registration code.")
    data = github.request("POST", f"/app-manifests/{code}/conversions", "")
    db.add(
        AppConfig(
            id=1,
            github_app_id=str(data["id"]),
            github_slug=data["slug"],
            client_id=data["client_id"],
            client_secret=seal(data["client_secret"]),
            private_key=seal(data["pem"]),
            webhook_secret=seal(data["webhook_secret"]),
        )
    )
    response = RedirectResponse(settings().public_url + "/setup?registered=1", status_code=303)
    response.delete_cookie("cm_oauth", path="/api/v1")
    return response


@router.get("/auth/login")
def login(db: DB):
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=digest(state), purpose="login", expires_at=now() + timedelta(minutes=10)))
    response = RedirectResponse(github.login_url(db, state))
    state_cookie(response, state)
    return response


@router.get("/auth/callback")
def login_callback(request: Request, db: DB, state: str = "", code: str = ""):
    consume_state(request, db, state, "login")
    tokens = github.exchange_code(db, code)
    identity = github.request("GET", "/user", tokens["access_token"])
    membership = db.get(Membership, identity["id"])
    if membership is None and identity["login"].casefold() == settings().owner_github_login.casefold():
        # Bootstrap the owner once; later username reuse must not grant administrator access.
        if not db.scalar(select(Membership).limit(1)):
            membership = Membership(github_id=identity["id"], login=identity["login"], role="admin")
            db.add(membership)
    if membership is None:
        raise HTTPException(403, "Ask the instance administrator to allow your GitHub account.")
    user = db.get(User, identity["id"])
    if user is None:
        user = User(id=identity["id"], login=identity["login"], role=membership.role)
        db.add(user)
    user.login, user.avatar_url, user.role, user.enabled = (
        identity["login"],
        identity["avatar_url"],
        membership.role,
        True,
    )
    github.save_tokens(user, tokens)
    db.flush()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=digest(token),
            user_id=user.id,
            csrf=csrf,
            expires_at=now() + timedelta(days=settings().session_days),
        )
    )
    response = RedirectResponse(settings().public_url + "/", status_code=303)
    response.set_cookie(
        "cm_session",
        token,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="lax",
        max_age=settings().session_days * 86400,
        path="/",
    )
    response.delete_cookie("cm_oauth", path="/api/v1")
    return response


@router.get("/auth/me", response_model=UserOut)
def me(request: Request, user: USER):
    return UserOut.model_validate(user).model_copy(update={"csrf": request.state.login_session.csrf})


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: DB, user: USER) -> dict:
    db.delete(request.state.login_session)
    response.delete_cookie("cm_session", path="/")
    return {"ok": True}


def repo_access(db: Session, user: User, repository_id: int, write=False):
    repo = db.get(Repository, repository_id)
    if not repo or not repo.active or not db.get(Installation, repo.installation_id).active:
        raise HTTPException(404, "Repository not found or its installation is inactive.")
    remote = github.authorize_repo(db, user, repo.full_name, write)
    return repo, remote


def pr_access(db: Session, user: User, pr_id: str, write=False):
    pr = db.get(PullRequest, pr_id)
    if not pr:
        raise HTTPException(404, "Pull request not found.")
    repo, remote = repo_access(db, user, pr.repository_id, write)
    return pr, repo, remote


def repo_out(db: Session, repo: Repository) -> RepositoryOut:
    result = RepositoryOut.model_validate(repo)
    result.open_prs = db.scalar(
        select(func.count())
        .select_from(PullRequest)
        .where(PullRequest.repository_id == repo.id, PullRequest.state == "open")
    )
    return result


def pr_out(db: Session, pr: PullRequest) -> PullRequestOut:
    result = PullRequestOut.model_validate(pr)
    run = db.scalar(
        select(ReviewRun).where(ReviewRun.pr_id == pr.id).order_by(ReviewRun.created_at.desc()).limit(1)
    )
    result.latest_run = RunOut.model_validate(run) if run else None
    result.open_findings = db.scalar(
        select(func.count()).select_from(Finding).where(Finding.pr_id == pr.id, Finding.status == "open")
    )
    return result


@router.get("/repositories", response_model=list[RepositoryOut])
def repositories(db: DB, user: USER):
    token = github.user_token(db, user)
    visible = set()
    for installation in db.scalars(select(Installation).where(Installation.active.is_(True))):
        try:
            visible.update(
                repo["id"]
                for repo in github.pages(
                    f"/user/installations/{installation.id}/repositories", token, "repositories"
                )
            )
        except github.GitHubError as exc:
            if exc.status not in {403, 404}:
                raise
    return [
        repo_out(db, repo)
        for repo in db.scalars(
            select(Repository).where(Repository.active.is_(True)).order_by(Repository.full_name)
        )
        if repo.id in visible
    ]


@router.put("/repositories/{repository_id}", response_model=RepositoryOut)
def update_repository(repository_id: int, body: RepositoryInput, db: DB, user: ADMIN):
    repo, _ = repo_access(db, user, repository_id, write=True)
    if body.connection_id and not db.get(Connection, body.connection_id):
        raise HTTPException(422, "Choose an existing AI connection.")
    if body.enabled and not body.connection_id:
        raise HTTPException(422, "Choose an AI connection before enabling reviews.")
    repo.enabled, repo.connection_id, repo.settings = (
        body.enabled,
        body.connection_id,
        body.settings.model_dump(),
    )
    if repo.enabled:
        enqueue(db, "scan_repository", {"repository_id": repo.id})
    db.flush()
    return repo_out(db, repo)


@router.get("/repositories/{repository_id}/pulls", response_model=list[PullRequestOut])
def pull_requests(repository_id: int, db: DB, user: USER):
    repo_access(db, user, repository_id)
    return [
        pr_out(db, pr)
        for pr in db.scalars(
            select(PullRequest)
            .where(PullRequest.repository_id == repository_id)
            .order_by(PullRequest.updated_at.desc())
        )
    ]


@router.post("/repositories/{repository_id}/sync", status_code=202)
def refresh_repository(repository_id: int, db: DB, user: USER) -> dict:
    repo_access(db, user, repository_id, write=True)
    job = enqueue(db, "scan_repository", {"repository_id": repository_id})
    return {"job_id": job.id}


@router.get("/pulls/{pr_id}", response_model=PRDetail)
def pull_detail(pr_id: str, db: DB, user: USER):
    pr, repo, remote = pr_access(db, user, pr_id)
    return PRDetail(
        pull_request=pr_out(db, pr),
        repository=repo_out(db, repo),
        runs=[
            RunOut.model_validate(run)
            for run in db.scalars(
                select(ReviewRun).where(ReviewRun.pr_id == pr.id).order_by(ReviewRun.created_at.desc())
            )
        ],
        findings=[
            FindingOut.model_validate(finding)
            for finding in db.scalars(
                select(Finding).where(Finding.pr_id == pr.id).order_by(Finding.created_at.desc())
            )
        ],
        can_write=remote.get("permissions", {}).get("push", False),
    )


@router.post("/pulls/{pr_id}/runs", response_model=RunOut, status_code=202)
def start_run(pr_id: str, db: DB, user: USER):
    pr, repo, _ = pr_access(db, user, pr_id, write=True)
    token = github.installation_token(db, repo.installation_id)
    data = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
    pr = upsert_pr(db, repo, data)
    if pr.state != "open":
        raise HTTPException(409, "Reopen the pull request before starting another review.")
    run = create_run(db, pr, repo, "manual", user)
    db.flush()
    return RunOut.model_validate(run)


@router.get("/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: str, db: DB, user: USER):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr, repo, remote = pr_access(db, user, run.pr_id)
    return RunDetail(
        run=RunOut.model_validate(run),
        pull_request=pr_out(db, pr),
        repository=repo_out(db, repo),
        occurrences=[
            OccurrenceOut.model_validate(item)
            for item in db.scalars(select(Occurrence).where(Occurrence.run_id == run.id))
        ],
        can_write=remote.get("permissions", {}).get("push", False),
    )


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: str, db: DB, user: USER):
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id, write=True)
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "This review is no longer running.")
    run.cancel_requested, run.status, run.publication, run.finished_at = (
        True,
        "cancelled",
        "not_applicable",
        now(),
    )
    return RunOut.model_validate(run)


@router.post("/runs/{run_id}/publish", status_code=202)
def retry_publication(run_id: str, db: DB, user: USER) -> dict:
    run = db.get(ReviewRun, run_id)
    if not run:
        raise HTTPException(404, "Review run not found.")
    pr_access(db, user, run.pr_id, write=True)
    run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
    if run.status not in {"completed", "partial"} or run.publication not in {"failed", "partially_published"}:
        raise HTTPException(409, "This run has no failed publication to retry.")
    run.publication, run.publication_error = "pending", None
    job = enqueue(db, "publish", {"run_id": run.id})
    return {"job_id": job.id}


@router.get("/findings/{finding_id}", response_model=FindingDetail)
def finding_detail(finding_id: str, db: DB, user: USER):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found.")
    pr_access(db, user, finding.pr_id)
    return FindingDetail(
        finding=FindingOut.model_validate(finding),
        activities=[
            ActivityOut.model_validate(activity)
            for activity in db.scalars(
                select(Activity).where(Activity.finding_id == finding.id).order_by(Activity.created_at)
            )
        ],
    )


@router.patch("/findings/{finding_id}", response_model=FindingOut, status_code=202)
def update_finding(finding_id: str, body: FindingAction, db: DB, user: USER):
    finding = db.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found.")
    pr_access(db, user, finding.pr_id, write=True)
    finding = db.scalar(select(Finding).where(Finding.id == finding_id).with_for_update())
    request_finding_status(db, finding, body.status, user, body.reason)
    return FindingOut.model_validate(finding)


def connection_out(connection: Connection) -> ConnectionOut:
    result = ConnectionOut.model_validate(connection)
    credentials = json.loads(unseal(connection.secret))
    result.has_key, result.header_names = (
        bool(credentials.get("api_key")),
        list(credentials.get("extra_headers", {})),
    )
    return result


@router.get("/providers")
def providers(user: ADMIN) -> list[dict]:
    return json.loads(files("code_mortis").joinpath("providers.json").read_text())


@router.get("/connections", response_model=list[ConnectionOut])
def connections(db: DB, user: ADMIN):
    return [connection_out(row) for row in db.scalars(select(Connection).order_by(Connection.name))]


def save_connection(db: Session, body: ConnectionInput, connection: Connection | None = None):
    old = json.loads(unseal(connection.secret)) if connection else {}
    credentials = {
        "api_key": body.api_key if body.api_key is not None else old.get("api_key", ""),
        "extra_headers": body.extra_headers
        if body.extra_headers is not None
        else old.get("extra_headers", {}),
    }
    if body.protocol != "anthropic-bedrock" and not credentials["api_key"]:
        raise HTTPException(
            422, "Enter an API key; use a placeholder for local models that do not require one."
        )
    url = safe_endpoint(body.base_url) if body.protocol != "anthropic-bedrock" else ""
    if connection is None:
        connection = Connection()
        db.add(connection)
    for key in ("name", "provider", "protocol", "model", "options"):
        setattr(connection, key, getattr(body, key))
    connection.base_url, connection.secret = url, seal(json.dumps(credentials))
    connection.last_test, connection.tested_at = None, None
    db.flush()
    return connection_out(connection)


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def add_connection(body: ConnectionInput, db: DB, user: ADMIN):
    return save_connection(db, body)


@router.put("/connections/{connection_id}", response_model=ConnectionOut)
def edit_connection(connection_id: str, body: ConnectionInput, db: DB, user: ADMIN):
    connection = db.get(Connection, connection_id)
    if not connection:
        raise HTTPException(404, "Connection not found.")
    return save_connection(db, body, connection)


@router.post("/connections/{connection_id}/test", status_code=202)
def test_connection(connection_id: str, db: DB, user: ADMIN) -> dict:
    connection = db.get(Connection, connection_id)
    if not connection:
        raise HTTPException(404, "Connection not found.")
    connection.last_test = "Testing connection…"
    job = enqueue(db, "test_connection", {"connection_id": connection.id})
    return {"job_id": job.id}


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str, db: DB, user: ADMIN) -> dict:
    connection = db.get(Connection, connection_id)
    if not connection:
        raise HTTPException(404, "Connection not found.")
    if db.scalar(select(Repository).where(Repository.connection_id == connection_id).limit(1)):
        raise HTTPException(409, "Change the repositories using this connection before deleting it.")
    if any(
        run.config["connection"]["id"] == connection_id
        for run in db.scalars(select(ReviewRun).where(ReviewRun.status.in_({"queued", "running"})))
    ):
        raise HTTPException(409, "Wait for active reviews using this connection to finish.")
    db.delete(connection)
    return {"ok": True}


@router.get("/settings")
def instance_settings(db: DB, user: ADMIN) -> dict:
    config = github.app_config(db)
    return {
        "github_app": {
            "id": config.github_app_id,
            "slug": config.github_slug,
            "install_url": f"https://github.com/apps/{config.github_slug}/installations/new",
        },
        "installations": [
            {"id": row.id, "account": row.account, "active": row.active}
            for row in db.scalars(select(Installation))
        ],
        "members": [
            {"id": row.github_id, "login": row.login, "role": row.role}
            for row in db.scalars(select(Membership))
        ],
        "failed_jobs": [
            {"id": row.id, "kind": row.kind, "error": row.error}
            for row in db.scalars(
                select(Job).where(Job.status == "failed").order_by(Job.available_at.desc()).limit(20)
            )
        ],
        "public_url": settings().public_url,
    }


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
    allowed = available_installations(db, user)
    remote = next((row for row in allowed if row["id"] == installation_id), None)
    if not remote:
        raise HTTPException(403, "This installation is not available to your GitHub account.")
    installation = db.get(Installation, installation_id)
    if installation is None:
        installation = Installation(id=installation_id, account=remote["account"])
        db.add(installation)
    installation.active = True
    enqueue(db, "sync_repositories", {"installation_id": installation_id})
    return {"ok": True}


@router.post("/members")
def add_member(body: MemberInput, db: DB, user: ADMIN) -> dict:
    identity = github.request("GET", f"/users/{body.login}", github.user_token(db, user))
    if identity.get("type") != "User":
        raise HTTPException(422, "Select an individual GitHub user.")
    if identity["id"] == user.id and body.role != "admin":
        raise HTTPException(409, "You cannot remove your own administrator access.")
    membership = db.get(Membership, identity["id"])
    if membership is None:
        membership = Membership(github_id=identity["id"], login=identity["login"], role=body.role)
        db.add(membership)
    membership.role = body.role
    existing = db.get(User, identity["id"])
    if existing:
        existing.role, existing.enabled = body.role, True
    return {"ok": True}


@router.delete("/members/{user_id}")
def remove_member(user_id: int, db: DB, user: ADMIN) -> dict:
    if user.id == user_id:
        raise HTTPException(409, "You cannot remove your own account.")
    membership = db.get(Membership, user_id)
    if membership:
        db.delete(membership)
    existing = db.get(User, user_id)
    if existing:
        existing.enabled, existing.access_token, existing.refresh_token = False, "", ""
    db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
    return {"ok": True}


@router.post("/webhooks/github", status_code=202)
async def webhook(request: Request, db: DB) -> dict:
    config = db.get(AppConfig, 1)
    if config is None:
        raise HTTPException(503, "Instance setup is incomplete.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings().max_webhook_bytes:
            raise HTTPException(413, "Webhook payload is too large.")
    if not valid_signature(
        bytes(body), request.headers.get("x-hub-signature-256", ""), unseal(config.webhook_secret)
    ):
        raise HTTPException(401, "Invalid webhook signature.")
    delivery = request.headers.get("x-github-delivery", "")
    event = request.headers.get("x-github-event", "")
    if not delivery or len(delivery) > 100 or not event or len(event) > 100:
        raise HTTPException(400, "Missing webhook delivery headers.")
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(400, "Invalid webhook payload.") from exc
    try:
        with db.begin_nested():
            db.add(WebhookDelivery(id=delivery, event=event))
            db.flush()
            if event != "ping":
                enqueue(db, "webhook", {"event": event, "body": data})
    except IntegrityError:
        return {"accepted": True, "duplicate": True}
    return {"accepted": True, "duplicate": False}
