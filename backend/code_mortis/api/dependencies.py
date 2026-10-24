import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from code_mortis.core.config import settings
from code_mortis.core.db import get_db, now
from code_mortis.core.security import digest
from code_mortis.integrations import github
from code_mortis.models import (
    Connection,
    Installation,
    LoginSession,
    Membership,
    PullRequest,
    Repository,
    User,
)

DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB) -> User:
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


USER = Annotated[User, Depends(current_user)]


def admin(user: USER) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access is required.")
    return user


ADMIN = Annotated[User, Depends(admin)]


def repo_access(db: Session, user: User, repository_id: int, write=False):
    selected = getattr(user, "mcp_repository_ids", None)
    if selected is not None and repository_id not in selected:
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
