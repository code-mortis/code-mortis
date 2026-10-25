import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from code_mortis.core.access import client_ip, effective_repository_ids
from code_mortis.core.config import settings
from code_mortis.core.db import get_db, now
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.models import (
    Connection,
    Installation,
    LoginSession,
    Membership,
    PersonalApiToken,
    PullRequest,
    Repository,
    User,
)

DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB) -> User:
    # Session-only helper for direct callers (including MCP). Never accept a
    # bearer credential here; PAT traffic authenticates through request_principal.
    if request.headers.getlist("authorization"):
        raise HTTPException(401, "Sign in with GitHub to continue.")
    token = request.cookies.get("cm_session", "")
    session = db.get(LoginSession, digest(token)) if token else None
    if not session or session.expires_at.timestamp() <= now().timestamp():
        raise HTTPException(401, "Sign in with GitHub to continue.")
    user = db.get(User, session.user_id)
    membership = db.get(Membership, user.id) if user else None
    if not user or not user.enabled or not membership:
        raise HTTPException(403, "Your GitHub account does not have access to Code Mortis. Ask the administrator to add you as a member.")
    user.role = membership.role
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "Your session changed. Refresh the page and retry.")
        if request.headers.get("origin") not in {None, settings().public_url}:
            raise HTTPException(403, "This request came from a different website address. Open Code Mortis at its configured address and try again.")
    request.state.login_session = session
    return user


SESSION_USER = Annotated[User, Depends(current_user)]


def session_admin(user: SESSION_USER) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access is required.")
    return user


SESSION_ADMIN = Annotated[User, Depends(session_admin)]


@dataclass
class PatPrincipal:
    user: User
    token: PersonalApiToken


def _bearer_challenge(status: int, detail: str) -> HTTPException:
    return HTTPException(status, detail, headers={"WWW-Authenticate": "Bearer"})


def _authenticate_pat(request: Request, db: Session, raw: str) -> PatPrincipal:
    from code_mortis.api_tokens import service as pat_service

    token = pat_service.find_token(db, raw)
    if token is None:
        pat_service.log_failed_auth(request, "unknown_token")
        _count_failed_attempt(request)
        raise _bearer_challenge(401, "This API token is invalid. Check the value and try again.")
    # Count the failure before denying so guessing cannot bypass the bucket.
    if token.revoked_at is not None or token.expires_at <= now():
        pat_service.log_failed_auth(request, "inactive_token", token.id)
        _count_failed_attempt(request)
        raise _bearer_challenge(401, "This API token is invalid or expired. Check the value and try again.")
    try:
        pat_service.check_accept_limit(token.id)
    except pat_service.TokenUnavailable:
        raise
    try:
        user, token = pat_service.authorize(db, token.id)
    except HTTPException as exc:
        pat_service.log_failed_auth(request, "owner_inactive", token.id)
        _count_failed_attempt(request)
        if exc.status_code == 401:
            raise _bearer_challenge(401, exc.detail) from exc
        raise
    # Finish the metadata write before handlers perform network work, so
    # approximate last-use tracking cannot hold up a concurrent revocation.
    pat_service.record_use(db, token)
    db.commit()
    return PatPrincipal(user=user, token=token)


def _count_failed_attempt(request: Request) -> None:
    from code_mortis.api_tokens import service as pat_service

    try:
        pat_service.check_failed_auth(client_ip(request))
    except pat_service.TokenUnavailable:
        raise
    except HTTPException as exc:
        if exc.status_code == 429:
            raise


def _refuse_credential(request: Request, reason: str) -> None:
    from code_mortis.api_tokens import service as pat_service

    pat_service.log_failed_auth(request, reason)
    _count_failed_attempt(request)


def _pat_transport_allowed(request: Request) -> bool:
    from urllib.parse import urlsplit

    if request.url.scheme == "https":
        return True
    if settings().cookie_secure:
        return False
    # Local HTTP development and test origins only; the ASGI server owns TLS
    # termination, so spoofed forwarding headers grant nothing here.
    local_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    configured = urlsplit(settings().public_url)
    return (configured.scheme == "http" and configured.hostname in local_hosts
            and (request.url.hostname or "").lower() in local_hosts)


async def request_principal(request: Request, db: DB) -> AsyncIterator[User]:
    from code_mortis.api_tokens import policy as pat_policy

    authorizations = request.headers.getlist("authorization")
    if len(authorizations) > 1:
        _refuse_credential(request, "duplicate_credential")
        raise _bearer_challenge(401, "Send exactly one Authorization credential.")
    cookie = request.cookies.get("cm_session", "")
    if authorizations:
        from code_mortis.api_tokens import service as pat_service

        if not _pat_transport_allowed(request):
            _refuse_credential(request, "cleartext_transport")
            raise _bearer_challenge(401, "Send API tokens over HTTPS, not cleartext HTTP.")
        if cookie:
            _refuse_credential(request, "mixed_credential")
            raise _bearer_challenge(401, "Send either an API token or a browser session, not both.")
        scheme, _, credential = authorizations[0].partition(" ")
        if scheme.lower() != "bearer" or not credential.strip() or len(authorizations[0]) > pat_service.MAX_CREDENTIAL_BYTES:
            _refuse_credential(request, "malformed_credential")
            raise _bearer_challenge(401, "This API token is invalid. Check the value and try again.")
        try:
            raw = pat_service.parse_raw_token(credential.strip())
        except HTTPException:
            # A valid MCP credential must fail PAT authentication without cookie fallback.
            _refuse_credential(request, "malformed_token")
            raise _bearer_challenge(401, "This API token is invalid. Check the value and try again.") from None
        principal = await run_in_threadpool(_authenticate_pat, request, db, raw)
        route = request.scope.get("route")
        template = getattr(route, "path_format", "")
        # Included routers retain their original relative path in recent FastAPI versions.
        route_path = str(route.url_path_for(route.name, **{
            name: request.path_params[name] for name in route.param_convertors
        }))
        request_path = request.url.path.removeprefix(request.scope.get("root_path", ""))
        template = request_path.removesuffix(route_path) + template if request_path.endswith(route_path) else ""
        if (request.method, template) == pat_policy.AUTH_TOKEN_ROUTE:
            required = None
        else:
            required = pat_policy.pat_scope(request.method, template or "")
            if required is None:
                raise HTTPException(403, "This operation is not available for API tokens. Use a browser session.")
            if required not in (principal.token.scopes or []):
                raise HTTPException(403, "This API token was not granted the required permission.")
        request.state.pat_principal = principal
        request.state.login_session = None
        from code_mortis.api_tokens import provenance as pat_provenance

        try:
            repository_id = int(request.path_params.get("repository_id", 0))
        except (TypeError, ValueError):
            repository_id = 0
        record = {"token_id": principal.token.id, "user_id": principal.user.id, "scope": required} if required else None
        if record is not None and repository_id > 0:
            record["repository_id"] = repository_id
        with pat_provenance.scoped(record):
            yield principal.user
        return
    try:
        user = await run_in_threadpool(current_user, request, db)
    except HTTPException as exc:
        if exc.status_code == 401:
            exc.headers = {**(exc.headers or {}), "WWW-Authenticate": "Bearer"}
        raise
    request.state.pat_principal = None
    yield user


USER = Annotated[User, Depends(request_principal)]


def admin(user: USER) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access is required.")
    return user


ADMIN = Annotated[User, Depends(admin)]


async def pat_only(request: Request, db: DB) -> AsyncIterator[PatPrincipal]:
    # Drive the shared boundary without closing it: the PAT execution context
    # must stay bound while the endpoint runs, and reset on exit.
    generator = request_principal(request, db)
    await generator.__anext__()
    principal = getattr(request.state, "pat_principal", None)
    if principal is None:
        await generator.aclose()
        raise _bearer_challenge(401, "Send an API token with this request.")
    try:
        yield principal
    finally:
        await generator.aclose()


PAT = Annotated[PatPrincipal, Depends(pat_only)]


def repo_access(db: Session, user: User, repository_id: int, write=False):
    selected = effective_repository_ids(user)
    if selected is not None and repository_id not in selected:
        if getattr(user, "api_token_repository_ids", None) is not None:
            raise HTTPException(403, "This repository was not selected for this API token.")
        raise HTTPException(403, "This repository was not selected for this MCP client.")
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


def review_pr_access(db: Session, user: User, pr_id: str):
    from sqlalchemy import select

    _, repo, remote = pr_access(db, user, pr_id, write=True)
    pr = db.scalar(select(PullRequest).where(PullRequest.id == pr_id).with_for_update()
                   .execution_options(populate_existing=True))
    if pr.ignored:
        raise HTTPException(409, "Restore this pull request before reviewing it.")
    return pr, repo, remote


def manage_connection(db: Session, user: User, identity: str) -> Connection:
    connection = db.get(Connection, identity)
    if not connection:
        raise HTTPException(404, "Connection not found.")
    if (connection.kind == "api" and user.role != "admin") or (
        connection.kind != "api" and connection.owner_id != user.id
    ):
        raise HTTPException(
            403,
            "Only the connection owner can manage this personal account; API connections require an administrator.",
        )
    return connection
