from fastapi import APIRouter, HTTPException, Query, Request, Response

from code_mortis.api.dependencies import DB, PAT, SESSION_USER
from code_mortis.api_tokens import policy, service
from code_mortis.core.db import now
from code_mortis.models import PersonalApiToken
from code_mortis.schemas.api_tokens import (
    ApiTokenCreated,
    ApiTokenCreateInput,
    ApiTokenIdentity,
    ApiTokenOut,
    ApiTokenPage,
    ApiTokenScope,
    ApiTokenUser,
)

router = APIRouter()

PAGE_SIZE = 10


def token_status(token: PersonalApiToken) -> str:
    if token.revoked_at is not None:
        return "revoked"
    if token.expires_at <= now():
        return "expired"
    return "active"


def token_out(token: PersonalApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=token.id, name=token.name, prefix=token.prefix, scopes=list(token.scopes or []),
        repository_ids=list(token.repository_ids or []), created_at=token.created_at,
        expires_at=token.expires_at, last_used_at=token.last_used_at, revoked_at=token.revoked_at,
        status=token_status(token),
    )


def permission_catalog() -> list[ApiTokenScope]:
    return [ApiTokenScope(id=scope, label=label, description=description)
            for scope, (label, description) in policy.SCOPES.items()]


@router.get("/api-tokens", response_model=ApiTokenPage)
def list_tokens(request: Request, db: DB, user: SESSION_USER,
                request_status: str = Query(default="active", alias="status"),
                page: int = Query(default=1, ge=1, le=1000000)) -> ApiTokenPage:
    from sqlalchemy import func, select

    if request_status not in {"active", "expired", "revoked", "all"}:
        raise HTTPException(422, "Choose active, expired, revoked or all.")
    moment = now()
    active = (PersonalApiToken.revoked_at.is_(None)) & (PersonalApiToken.expires_at > moment)
    filters = {"active": active,
               "expired": (PersonalApiToken.revoked_at.is_(None)) & (PersonalApiToken.expires_at <= moment),
               "revoked": PersonalApiToken.revoked_at.is_not(None)}
    base = select(PersonalApiToken).where(PersonalApiToken.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    active_count = db.scalar(select(func.count()).select_from(base.where(active).subquery()))
    items = base
    if request_status != "all":
        items = items.where(filters[request_status])
    rows = db.scalars(items.order_by(PersonalApiToken.created_at.desc(), PersonalApiToken.id.desc())
                      .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    filtered_total = total if request_status == "all" else db.scalar(
        select(func.count()).select_from(base.where(filters[request_status]).subquery()))
    return ApiTokenPage(
        items=[token_out(token) for token in rows],
        total=filtered_total, page=page, has_more=page * PAGE_SIZE < filtered_total,
        active_count=active_count, permissions=permission_catalog(),
        default_expiration_days=policy.DEFAULT_EXPIRATION_DAYS,
        max_expiration_days=policy.MAX_EXPIRATION_DAYS,
        reauth_required=not service.fresh_session(db, getattr(request.state, "login_session", None)),
    )


@router.post("/api-tokens", response_model=ApiTokenCreated, status_code=201)
def create_token(body: ApiTokenCreateInput, request: Request, response: Response,
                 db: DB, user: SESSION_USER) -> ApiTokenCreated:
    try:
        service.check_creation_limit(user.id)
    except service.TokenUnavailable:
        raise
    if not service.fresh_session(db, getattr(request.state, "login_session", None)):
        raise HTTPException(403, "Sign in with GitHub again before creating an API token.")
    token, raw = service.create_token(
        db, user, name=body.name, scopes=body.scopes, repository_ids=body.repository_ids,
        expires_in_days=body.expires_in_days)
    # Commit before disclosing the secret exactly once; a lost response cannot recover it.
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return ApiTokenCreated(token=raw, item=token_out(token))


@router.delete("/api-tokens/{token_id}", status_code=202)
def revoke_token(token_id: str, db: DB, user: SESSION_USER) -> dict:
    from sqlalchemy import select

    token = db.scalar(select(PersonalApiToken).where(
        PersonalApiToken.id == token_id, PersonalApiToken.user_id == user.id))
    if token is None:
        raise HTTPException(404, "API token not found.")
    service.revoke(db, token, actor_id=user.id, reason="user")
    # Browser revocation commits through PostgreSQL even when Redis is unavailable.
    db.commit()
    return {"revoked": True}


@router.get("/auth/token", response_model=ApiTokenIdentity)
def token_identity(principal: PAT) -> ApiTokenIdentity:
    return ApiTokenIdentity(user=ApiTokenUser.model_validate(principal.user, from_attributes=True),
                            token=token_out(principal.token))
