from fastapi import APIRouter, HTTPException, Request, WebSocket
from sqlalchemy import delete, select

from code_mortis.api.dependencies import DB, USER, manage_connection
from code_mortis.core.shared_execution import RETIRED_MESSAGE
from code_mortis.models import (
    ConnectionGrant,
    ConsoleTicket,
    PullRequest,
    ReviewRun,
)
from code_mortis.schemas.common import StrictModel

router = APIRouter()



def personal(db, user, identity):
    connection = manage_connection(db, user, identity)
    if connection.kind == "api":
        raise HTTPException(422, "This operation requires a personal subscription connection.")
    return connection


def cancel_connection_runs(db, identity, repository_id=None, *, security=True):
    from code_mortis.core.shared_execution import cancel_connection_jobs, cancel_retired_review

    cancel_connection_jobs(db, identity, repository_id)

    if security:
        from code_mortis.security_scans.service import cancel_access

        cancel_access(db, connection_id=identity, repository_ids=[repository_id] if repository_id is not None else None)
    query = select(ReviewRun).where(
        ReviewRun.connection_id == identity, ReviewRun.status.in_({"queued", "running", "blocked"})
    )
    if repository_id is not None:
        query = query.join(PullRequest).where(PullRequest.repository_id == repository_id)
    for run in db.scalars(query.with_for_update()):
        cancel_retired_review(db, run)


@router.post("/connections/{identity}/auth")
def start_auth(identity: str, db: DB, user: USER):
    personal(db, user, identity)
    raise HTTPException(410, RETIRED_MESSAGE)

@router.get("/connections/{identity}/auth")
def auth_status(identity: str, db: DB, user: USER):
    personal(db, user, identity)
    raise HTTPException(410, RETIRED_MESSAGE)

@router.delete("/connections/{identity}/auth")
def cancel_auth(identity: str, db: DB, user: USER):
    connection = personal(db, user, identity)
    db.execute(delete(ConsoleTicket).where(ConsoleTicket.connection_id == identity))
    connection.auth_status, connection.auth_message = "revoked", RETIRED_MESSAGE
    return {"ok": True}


@router.post("/connections/{identity}/disconnect")
def disconnect(identity: str, db: DB, user: USER):
    connection = personal(db, user, identity)
    cancel_connection_runs(db, identity)
    db.execute(delete(ConsoleTicket).where(ConsoleTicket.connection_id == identity))
    db.execute(delete(ConnectionGrant).where(ConnectionGrant.connection_id == identity))
    connection.auth_status, connection.auth_message = "revoked", RETIRED_MESSAGE
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
    raise HTTPException(410, RETIRED_MESSAGE)

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
    personal(db, user, identity)
    raise HTTPException(410, RETIRED_MESSAGE)

def console_allowed(identity, session_hash):
    return False


@router.websocket("/connections/{identity}/console")
async def console(websocket: WebSocket, identity: str):
    await websocket.close(code=1008)
