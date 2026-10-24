import hmac
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from code_mortis.api.dependencies import DB, USER
from code_mortis.api.oauth import auth_error, browser_binding, consume_state, start_attempt
from code_mortis.core.config import settings
from code_mortis.core.db import now
from code_mortis.core.security import digest, seal, unseal
from code_mortis.integrations import github
from code_mortis.models import (
    AppConfig,
    LoginSession,
    Membership,
    User,
)
from code_mortis.schemas.common import UserOut

router = APIRouter()


def bootstrap(request: Request, db: Session):
    if db.get(AppConfig, 1):
        raise HTTPException(409, "This instance is already configured.")
    if not hmac.compare_digest(request.headers.get("x-setup-token", ""), settings().bootstrap_token):
        raise HTTPException(
            403,
            "The bootstrap token does not match this instance. "
            "Copy the CM_BOOTSTRAP_TOKEN value from the environment file used to start it "
            "(.env.dev for development, .env for the server).",
        )


@router.get("/setup/status")
def setup_status(request: Request, response: Response, db: DB) -> dict:
    browser_binding(request, response)
    return {
        "configured": db.get(AppConfig, 1) is not None,
        "public_url": settings().public_url,
        "configuration_errors": settings().diagnostics(),
    }


class ManifestInput(BaseModel):
    organization: str = Field(default="", pattern=r"^[a-zA-Z0-9-]*$", max_length=39)


@router.post("/setup/manifest")
def manifest(body: ManifestInput, request: Request, response: Response, db: DB) -> dict:
    bootstrap(request, db)
    origin = settings().public_url
    if request.headers.get("origin") not in {None, origin}:
        raise HTTPException(400, f"Open {origin} in this browser and start GitHub App setup there.")
    if errors := settings().diagnostics():
        raise HTTPException(422, " ".join(errors))
    state, _ = start_attempt(request, response, db, "manifest")
    prefix = f"organizations/{body.organization}/settings" if body.organization else "settings"
    return {
        "action": f"https://github.com/{prefix}/apps/new?state={state}",
        "manifest": {
            "name": f"Code Mortis {secrets.token_hex(3)}",
            "url": origin,
            "hook_attributes": {"url": settings().github_webhook_url, "active": True},
            "redirect_url": origin + "/api/v1/setup/callback",
            "callback_urls": [origin + "/api/v1/auth/callback"],
            "setup_url": origin + "/api/v1/installations/callback",
            "public": True,
            "request_oauth_on_install": False,
            "default_permissions": {"contents": "write", "pull_requests": "write", "metadata": "read", "checks": "write", "issues": "write", "members": "read"},
            "default_events": ["pull_request", "pull_request_review_thread", "pull_request_review_comment", "check_run", "issue_comment"],
        },
    }


@router.get("/setup/callback")
def manifest_callback(request: Request, db: DB, state: str = "", code: str = ""):
    if db.get(AppConfig, 1):
        return RedirectResponse(settings().public_url + "/setup?registered=1", status_code=303)
    try:
        consume_state(request, db, state, "manifest")
    except HTTPException:
        return auth_error(request, "expired", setup=True)
    if not code or len(code) > 200 or not code.isalnum():
        return auth_error(request, "cancelled", setup=True)
    try:
        data = github.request("POST", f"/app-manifests/{code}/conversions", "")
    except github.GitHubError:
        return auth_error(request, "provider", setup=True)
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
    return response


@router.get("/auth/login")
def login(request: Request, db: DB, installation_id: int | None = None, return_to: str = "/"):
    if return_to != "/" and not (
        return_to.startswith("/mcp/authorize?request_id=")
        and re.fullmatch(r"/mcp/authorize\?request_id=[a-f0-9-]{36}", return_to)
    ):
        raise HTTPException(400, "The sign-in return address is invalid.")
    # Compare hosts because TLS may terminate at a reverse proxy before reaching us.
    if request.url.netloc != urlsplit(settings().public_url).netloc:
        values = {"installation_id": installation_id} if installation_id else {"return_to": return_to} if return_to != "/" else {}
        query = "?" + urlencode(values) if values else ""
        return RedirectResponse(settings().public_url + "/api/v1/auth/login" + query, status_code=303)
    response = RedirectResponse("/", status_code=303)
    return_path = (
        f"/api/v1/installations/callback?installation_id={installation_id}" if installation_id else return_to
    )
    state, verifier = start_attempt(request, response, db, "login", return_path)
    response.headers["location"] = github.login_url(db, state, verifier)
    return response


@router.get("/auth/callback")
def login_callback(request: Request, db: DB, state: str = "", code: str = "", error: str = ""):
    try:
        attempt = consume_state(request, db, state, "login")
    except HTTPException:
        return auth_error(request, "expired")
    if error or not code:
        return auth_error(request, "cancelled")
    try:
        tokens = github.exchange_code(db, code, unseal(attempt.verifier))
        identity = github.request("GET", "/user", tokens["access_token"])
    except github.GitHubError:
        return auth_error(request, "provider")
    membership = db.get(Membership, identity["id"])
    if membership is None and identity["login"].casefold() == settings().owner_github_login.casefold():
        # Bootstrap the owner once; later username reuse must not grant administrator access.
        if not db.scalar(select(Membership).limit(1)):
            membership = Membership(github_id=identity["id"], login=identity["login"], role="admin")
            db.add(membership)
    if membership is None:
        return auth_error(request, "membership")
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
    response = RedirectResponse(settings().public_url + attempt.return_path, status_code=303)
    response.set_cookie(
        "cm_session",
        token,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="lax",
        max_age=settings().session_days * 86400,
        path="/",
    )
    return response


@router.get("/auth/me", response_model=UserOut)
def me(request: Request, user: USER):
    return UserOut.model_validate(user).model_copy(update={"csrf": request.state.login_session.csrf})


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: DB, user: USER) -> dict:
    db.delete(request.state.login_session)
    response.delete_cookie("cm_session", path="/")
    return {"ok": True}
