"""Personal API token issuance, validation and revocation.

The token row is authoritative; scope snapshots in durable payloads are only
evidence. No raw token value leaves this module except once at creation.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from code_mortis.api_tokens import policy
from code_mortis.core.db import now
from code_mortis.core.security import digest
from code_mortis.models import ApiTokenEvent, Job, Membership, PersonalApiToken, User

log = logging.getLogger(__name__)

TOKEN_PREFIX = "cm_pat_"
RAW_PATTERN = re.compile(r"cm_pat_[A-Za-z0-9_-]{43}\Z")
MAX_CREDENTIAL_BYTES = 200
FAILED_AUTH_LIMIT = 20
ACCEPTED_REQUEST_LIMIT = 120
CREATION_LIMIT = 10
REVOKE_CLEANUP_KIND = "revoke_api_token_work"
LAST_USED_INTERVAL = timedelta(minutes=5)
ISSUANCE_FRESHNESS = timedelta(minutes=15)


class TokenUnavailable(HTTPException):
    def __init__(self):
        super().__init__(503, "Token authorization is temporarily unavailable. Try again shortly.")


def parse_raw_token(value: str) -> str:
    if len(value) > MAX_CREDENTIAL_BYTES or not RAW_PATTERN.fullmatch(value):
        raise HTTPException(401, "This API token is invalid. Check the value and try again.",
                            headers={"WWW-Authenticate": "Bearer"})
    return value


def find_token(db, raw: str) -> PersonalApiToken | None:
    return db.scalar(select(PersonalApiToken).where(PersonalApiToken.token_hash == digest(raw))
                     .execution_options(populate_existing=True))


def _active(token: PersonalApiToken | None) -> PersonalApiToken | None:
    if token is None or token.revoked_at is not None or token.expires_at <= now():
        return None
    return token


def fresh_session(db, session) -> bool:
    if not session or not session.authenticated_at:
        return False
    age = now() - session.authenticated_at
    # A future timestamp is clock skew or tampering, never fresh.
    return timedelta(0) <= age <= ISSUANCE_FRESHNESS


def authorize(db, token_id: str, *, scope: str | None = None, repository_id: int | None = None,
              write: bool = False, lock: bool = False) -> tuple[User, PersonalApiToken]:
    """Validate the stored token row and return its owner with token restrictions.

    Takes the non-secret row id; never a raw credential. Reads are refreshed
    from the database so a stale Session identity map cannot grant authority.
    No row lock is held through GitHub network calls: the lock only rechecks
    the active state after external checks complete.
    """
    from code_mortis.integrations import github

    token = db.scalar(select(PersonalApiToken).where(PersonalApiToken.id == token_id)
                      .execution_options(populate_existing=True))
    if _active(token) is None:
        raise HTTPException(401, "This API token is invalid or expired. Check the value and try again.",
                            headers={"WWW-Authenticate": "Bearer"})
    if scope is not None and scope not in (token.scopes or []):
        raise HTTPException(403, "This API token was not granted the required permission.")
    if repository_id is not None and repository_id not in (token.repository_ids or []):
        raise HTTPException(403, "This repository was not selected for this API token.")
    user = db.scalar(select(User).where(User.id == token.user_id)
                     .execution_options(populate_existing=True))
    membership = db.scalar(select(Membership).where(Membership.github_id == token.user_id)
                           .execution_options(populate_existing=True))
    if user is None or not user.enabled or membership is None:
        raise HTTPException(403, "This account no longer has access to Code Mortis.")
    user.role = membership.role
    if repository_id is not None:
        github.authorize_repo(db, user, _repo_name(db, repository_id), write)
    if lock:
        token = db.scalar(select(PersonalApiToken).where(PersonalApiToken.id == token_id).with_for_update()
                          .execution_options(populate_existing=True))
        if _active(token) is None:
            raise HTTPException(401, "This API token is invalid or expired. Check the value and try again.",
                                headers={"WWW-Authenticate": "Bearer"})
    user.api_token_id = token.id
    user.api_token_scopes = list(token.scopes or [])
    user.api_token_repository_ids = frozenset(token.repository_ids or [])
    return user, token


def _repo_name(db, repository_id: int) -> str:
    from code_mortis.models import Installation, Repository

    repo = db.scalar(select(Repository).where(Repository.id == repository_id)
                     .execution_options(populate_existing=True))
    installation = (db.scalar(select(Installation).where(Installation.id == repo.installation_id)
                              .execution_options(populate_existing=True)) if repo else None)
    if not repo or not repo.active or not installation or not installation.active:
        raise HTTPException(404, "Repository not found or its installation is inactive.")
    return repo.full_name


def create_token(db, user: User, *, name: str, scopes: list[str], repository_ids: list[int],
                 expires_in_days: int, actor_id: int | None = None) -> tuple[PersonalApiToken, str]:
    from code_mortis.integrations import github

    cleaned = sorted(set(scopes))
    if not cleaned or set(cleaned) - set(policy.SCOPES):
        raise HTTPException(422, "Select at least one valid API token permission.")
    selected = sorted(set(repository_ids))
    if not selected:
        raise HTTPException(422, "Select at least one repository for this API token.")
    if not 1 <= expires_in_days <= policy.MAX_EXPIRATION_DAYS:
        raise HTTPException(422, f"Choose an expiration from 1 to {policy.MAX_EXPIRATION_DAYS} days.")
    # GitHub credential refresh locks this user in its own transaction.
    # Complete discovery before taking the issuance lock to avoid self-blocking.
    available = {repo.id for repo in github.visible_repositories(db, user)}
    if not set(selected) <= available:
        raise HTTPException(403, "Select repositories you can currently access.")
    # Serialize per-user creation so the active-token cap is atomic, then
    # revalidate the owner under that lock so concurrent removal cannot issue.
    locked = db.scalar(select(User).where(User.id == user.id).with_for_update()
                       .execution_options(populate_existing=True))
    membership = db.scalar(select(Membership).where(Membership.github_id == user.id)
                           .execution_options(populate_existing=True))
    if locked is None or not locked.enabled or membership is None:
        raise HTTPException(403, "This account no longer has access to Code Mortis.")
    active = db.scalar(select(func.count()).select_from(PersonalApiToken).where(
        PersonalApiToken.user_id == user.id, PersonalApiToken.revoked_at.is_(None),
        PersonalApiToken.expires_at > now()))
    if active >= policy.MAX_ACTIVE_TOKENS:
        raise HTTPException(409, "This account already has 20 active API tokens. Revoke an unused token first.")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = PersonalApiToken(
        user_id=user.id, name=name.strip(), token_hash=digest(raw), prefix=raw[:12],
        scopes=cleaned, repository_ids=selected, expires_at=now() + timedelta(days=expires_in_days),
    )
    db.add(token)
    db.flush()
    db.add(ApiTokenEvent(token_id=token.id, actor_id=actor_id if actor_id is not None else user.id,
                         event="created", reason="user"))
    return token, raw


def revoke(db, token: PersonalApiToken, *, actor_id: int | None = None, reason: str = "user") -> PersonalApiToken:
    """Mark revoked, audit and enqueue idempotent cleanup. Caller commits."""
    from code_mortis.reviews.service import enqueue

    token = db.scalar(select(PersonalApiToken).where(PersonalApiToken.id == token.id).with_for_update()
                      .execution_options(populate_existing=True))
    if token.revoked_at is None:
        token.revoked_at = now()
        db.add(ApiTokenEvent(token_id=token.id,
                             actor_id=actor_id if actor_id is not None else token.user_id,
                             event="revoked", reason=reason[:100]))
        enqueue(db, REVOKE_CLEANUP_KIND, {"token_id": token.id})
    return token


def revoke_for_user(db, user_id: int, reason: str = "membership_removed",
                    actor_id: int | None = None) -> int:
    # Hold the user row so revocation serializes with concurrent creation.
    db.scalar(select(User).where(User.id == user_id).with_for_update())
    count = 0
    for token in db.scalars(select(PersonalApiToken).where(
            PersonalApiToken.user_id == user_id, PersonalApiToken.revoked_at.is_(None))
            .order_by(PersonalApiToken.id).with_for_update()):
        revoke(db, token, actor_id=actor_id if actor_id is not None else token.user_id, reason=reason)
        count += 1
    return count


def record_use(db, token: PersonalApiToken) -> None:
    if token.last_used_at is None or now() - token.last_used_at >= LAST_USED_INTERVAL:
        token.last_used_at = now()


def check_rate_limit(kind: str, identity: str, limit: int) -> None:
    from redis import Redis
    from redis.exceptions import RedisError

    from code_mortis.core.config import settings

    # Fixed per-minute buckets like the existing MCP limiter: the pipeline is
    # atomic and short-lived keys keep memory bounded without extra machinery.
    key = f"code-mortis:pat:{kind}:{digest(identity)}:{int(time.time()) // 60}"
    try:
        with Redis.from_url(settings().redis_url, socket_timeout=2, socket_connect_timeout=2) as cache:
            with cache.pipeline(transaction=True) as pipe:
                count, _ = pipe.incr(key).expire(key, 120).execute()
    except RedisError as exc:
        raise TokenUnavailable() from exc
    if count > limit:
        raise HTTPException(429, "Too many API token requests. Wait a minute and try again.",
                            headers={"Retry-After": "60"})


def check_creation_limit(user_id: int) -> None:
    check_rate_limit("create", str(user_id), CREATION_LIMIT)


def check_accept_limit(token_id: str) -> None:
    check_rate_limit("requests", token_id, ACCEPTED_REQUEST_LIMIT)


def check_failed_auth(client_ip: str) -> None:
    check_rate_limit("failed", client_ip, FAILED_AUTH_LIMIT)


def log_failed_auth(request, reason: str, token_id: str = "") -> None:
    # Never log credential material: unknown guesses carry no identifier, and
    # known tokens are referenced by their non-secret row id only.
    reference = getattr(request.state, "request_id", "unavailable")
    log.warning("pat_auth_failed reason=%s token_id=%s reference=%s", reason, token_id, reference)


def cleanup_payload_remaining(db) -> int:
    """Count unfinished jobs still referencing token cleanup (worker drains these)."""
    return db.scalar(select(func.count()).select_from(Job).where(
        Job.kind == REVOKE_CLEANUP_KIND, Job.status.in_(["queued", "running"])))
