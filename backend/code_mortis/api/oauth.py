import secrets
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete
from sqlalchemy.orm import Session

from code_mortis.core.config import settings
from code_mortis.core.db import now
from code_mortis.core.security import digest, seal
from code_mortis.models import OAuthState


def browser_binding(request: Request, response: Response) -> str:
    binding = request.cookies.get("cm_browser", "")
    if len(binding) != 43:
        binding = secrets.token_urlsafe(32)
    response.set_cookie(
        "cm_browser",
        binding,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="lax",
        max_age=86400,
        path="/api/v1",
    )
    return binding


def start_attempt(request: Request, response: Response, db: Session, purpose: str, return_path="/"):
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    db.execute(delete(OAuthState).where(OAuthState.expires_at <= now()))
    db.add(
        OAuthState(
            state=digest(state),
            purpose=purpose,
            expires_at=now() + timedelta(minutes=10),
            browser_hash=digest(browser_binding(request, response)),
            verifier=seal(verifier),
            return_path=return_path,
        )
    )
    return state, verifier


def consume_state(request: Request, db: Session, state: str, purpose: str):
    found = db.scalars(
        delete(OAuthState)
        .where(
            OAuthState.state == digest(state),
            OAuthState.purpose == purpose,
            OAuthState.expires_at > now(),
            OAuthState.browser_hash == digest(request.cookies.get("cm_browser", "")),
        )
        .returning(OAuthState)
    ).one_or_none()
    if not found:
        raise HTTPException(400, "This sign-in attempt has expired or was already used. Start a new sign-in attempt in this browser.")
    # Consume before exchanging a one-use remote code, even if the provider later rejects it.
    db.commit()
    return found


def auth_error(request: Request, code: str, setup=False):
    query = urlencode({"auth_error": code, "diagnostic": request.state.request_id})
    return RedirectResponse(settings().public_url + ("/setup?" if setup else "/?") + query, status_code=303)
