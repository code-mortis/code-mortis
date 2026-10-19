import asyncio
import contextlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, WebSocket
from sqlalchemy import delete, select
from websockets.asyncio.client import connect

from . import runner_client, verification
from .api import DB, USER, manage_connection, repo_access
from .config import settings
from .db import now, transaction
from .models import (
    Connection,
    ConnectionGrant,
    ConsoleTicket,
    LoginSession,
    Membership,
    PullRequest,
    ReviewRun,
    User,
)
from .schemas import StrictModel
from .security import digest

router = APIRouter(prefix="/api/v1")


def personal(db, user, identity):
    connection = manage_connection(db, user, identity)
    if connection.kind == "api":
        raise HTTPException(422, "This operation requires a personal subscription connection.")
    return connection


def cancel_connection_runs(db, identity, repository_id=None, *, security=True):
    if security:
        from .repository_security import cancel_access

        cancel_access(db, connection_id=identity, repository_ids=[repository_id] if repository_id is not None else None)
    query = select(ReviewRun).where(
        ReviewRun.connection_id == identity, ReviewRun.status.in_({"queued", "running", "blocked"})
    )
    if repository_id is not None:
        query = query.join(PullRequest).where(PullRequest.repository_id == repository_id)
    for run in db.scalars(query.with_for_update()):
        run.cancel_requested = True
        run.status, run.publication, run.finished_at = "cancelled", "not_applicable", now()
        verification.skip_pending(db, run, "Subscription access was revoked.")


@router.post("/connections/{identity}/auth")
def start_auth(identity: str, db: DB, user: USER):
    connection = personal(db, user, identity)
    result = runner_client.request("POST", f"/connections/{identity}/auth", {"kind": connection.kind})
    connection.auth_status, connection.auth_message = "connecting", None
    return result


@router.get("/connections/{identity}/auth")
def auth_status(identity: str, db: DB, user: USER):
    connection = personal(db, user, identity)
    if connection.auth_status == "revoked":
        return {"status": "disconnected", "message": connection.auth_message, "attempt": None}
    try:
        result = runner_client.request("GET", f"/connections/{identity}/auth?kind={connection.kind}")
    except runner_client.RunnerBlocked as exc:
        connection.auth_status, connection.auth_message = "unavailable", str(exc)
        return {"status": "unavailable", "message": str(exc), "attempt": None}
    connection.auth_status, connection.auth_message = result["status"], None
    return result


@router.delete("/connections/{identity}/auth")
def cancel_auth(identity: str, db: DB, user: USER):
    connection = personal(db, user, identity)
    runner_client.request("DELETE", f"/connections/{identity}/auth")
    db.execute(delete(ConsoleTicket).where(ConsoleTicket.connection_id == identity))
    connection.auth_status = "disconnected"
    return {"ok": True}


@router.post("/connections/{identity}/disconnect")
def disconnect(identity: str, db: DB, user: USER):
    connection = personal(db, user, identity)
    cancel_connection_runs(db, identity)
    db.execute(delete(ConsoleTicket).where(ConsoleTicket.connection_id == identity))
    db.execute(delete(ConnectionGrant).where(ConnectionGrant.connection_id == identity))
    connection.auth_status, connection.auth_message = "revoked", None
    # Revoke application access durably even if the native runner is currently offline.
    db.commit()
    try:
        runner_client.request("POST", f"/connections/{identity}/disconnect", {"kind": connection.kind})
    except runner_client.RunnerBlocked:
        connection.auth_message = (
            "Code Mortis access was revoked, but the account sign-in service is unavailable. Retry disconnect when the service is back to remove the saved account session."
        )
        return {"status": "disconnected", "message": connection.auth_message}
    return {"status": "disconnected"}


class GrantInput(StrictModel):
    automatic: bool = False


@router.get("/connections/{identity}/grants")
def grants(identity: str, db: DB, user: USER):
    personal(db, user, identity)
    return [
        {"repository_id": row.repository_id, "automatic": row.automatic}
        for row in db.scalars(select(ConnectionGrant).where(ConnectionGrant.connection_id == identity))
    ]


@router.put("/connections/{identity}/grants/{repository_id}")
def grant(identity: str, repository_id: int, body: GrantInput, db: DB, user: USER):
    personal(db, user, identity)
    repo_access(db, user, repository_id, write=True)
    row = db.get(ConnectionGrant, (identity, repository_id))
    if row is None:
        row = ConnectionGrant(connection_id=identity, repository_id=repository_id)
        db.add(row)
    if row.automatic and not body.automatic:
        cancel_connection_runs(db, identity, repository_id, security=False)
    row.automatic = body.automatic
    return {"repository_id": repository_id, "automatic": body.automatic}


@router.delete("/connections/{identity}/grants/{repository_id}")
def revoke(identity: str, repository_id: int, db: DB, user: USER):
    personal(db, user, identity)
    row = db.get(ConnectionGrant, (identity, repository_id))
    if row:
        db.delete(row)
    cancel_connection_runs(db, identity, repository_id)
    return {"ok": True}


@router.post("/connections/{identity}/console-ticket")
def console_ticket(identity: str, request: Request, db: DB, user: USER):
    connection = personal(db, user, identity)
    if connection.kind != "claude_code":
        raise HTTPException(422, "Only Claude Code uses the native login console.")
    token = secrets.token_urlsafe(32)
    db.execute(delete(ConsoleTicket).where(ConsoleTicket.expires_at <= now()))
    db.add(
        ConsoleTicket(
            token_hash=digest(token),
            connection_id=identity,
            session_hash=request.state.login_session.token_hash,
            expires_at=now() + timedelta(seconds=60),
        )
    )
    return {"ticket": token}


def console_allowed(identity, session_hash):
    with transaction() as db:
        session = db.get(LoginSession, session_hash)
        connection = db.get(Connection, identity)
        user = db.get(User, session.user_id) if session else None
        return bool(
            session
            and session.expires_at > now()
            and user
            and user.enabled
            and db.get(Membership, user.id)
            and connection
            and connection.owner_id == user.id
            and connection.kind == "claude_code"
            and connection.auth_status != "revoked"
        )


@router.websocket("/connections/{identity}/console")
async def console(websocket: WebSocket, identity: str):
    if websocket.headers.get("origin") != settings().public_url:
        await websocket.close(code=1008)
        return
    session_hash = digest(websocket.cookies.get("cm_session", ""))
    if not console_allowed(identity, session_hash):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        value = await asyncio.wait_for(websocket.receive_json(), 5)
        ticket = value.get("ticket", "") if isinstance(value, dict) else ""
        if not isinstance(ticket, str) or len(ticket) > 100:
            return
        with transaction() as db:
            consumed = db.execute(
                delete(ConsoleTicket)
                .where(
                    ConsoleTicket.token_hash == digest(ticket),
                    ConsoleTicket.connection_id == identity,
                    ConsoleTicket.session_hash == session_hash,
                    ConsoleTicket.expires_at > now(),
                )
                .returning(ConsoleTicket.token_hash)
            ).scalar_one_or_none()
        if not consumed:
            return
        url = settings().runner_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        async with connect(
            url + f"/connections/{identity}/console",
            max_size=16384,
            additional_headers={"Authorization": "Bearer " + settings().runner_token},
        ) as native:

            async def to_native():
                while True:
                    value = await websocket.receive_text()
                    if len(value) > 8192:
                        return
                    await native.send(value)

            async def to_browser():
                async for value in native:
                    await websocket.send_text(value)

            async def access_guard():
                while console_allowed(identity, session_hash):
                    await asyncio.sleep(2)

            pumps = [asyncio.create_task(fn()) for fn in (to_native, to_browser, access_guard)]
            try:
                async with asyncio.timeout(900):
                    await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
    except Exception:
        # Never log native authentication input, output, codes, cookies or tokens.
        pass
    finally:
        with contextlib.suppress(RuntimeError):
            await websocket.close()
