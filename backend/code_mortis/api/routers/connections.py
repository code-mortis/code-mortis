import json
from importlib.resources import files

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from code_mortis.api.dependencies import DB, SESSION_ADMIN, SESSION_USER, USER, manage_connection, repo_access
from code_mortis.api.presenters import connection_out
from code_mortis.core.security import safe_endpoint, seal, unseal
from code_mortis.core.shared_execution import RETIRED_MESSAGE, require_shared
from code_mortis.integrations import model_discovery
from code_mortis.models import (
    Connection,
    Repository,
    ReviewRun,
)
from code_mortis.reviews.service import enqueue
from code_mortis.schemas.common import (
    ConnectionInput,
    ConnectionOut,
    ModelCatalog,
    ModelDiscoveryInput,
)

router = APIRouter()


@router.get("/providers")
def providers(user: USER) -> list[dict]:
    return json.loads(files("code_mortis.integrations").joinpath("providers.json").read_text())


@router.get("/connections", response_model=list[ConnectionOut])
def connections(db: DB, user: USER, repository_id: int | None = None):
    if repository_id is not None:
        repo_access(db, user, repository_id)
    rows = list(db.scalars(select(Connection).where(Connection.kind == "api").order_by(Connection.name)))
    if getattr(user, "api_token_repository_ids", None) is not None:
        effective = _token_effective_ids(db, user)
        rows = [row for row in rows if _available_for_token(db, row, effective)]
    return [connection_out(row) for row in rows]


def _token_effective_ids(db: Session, user) -> set[int]:
    from code_mortis.integrations import github

    # Recompute what the owner can currently access: token selection alone is
    # stale the moment GitHub permission, membership or installation changes.
    return {repo.id for repo in github.visible_repositories(db, user)}


def _available_for_token(db: Session, connection: Connection, effective: set[int]) -> bool:
    if not effective:
        return False
    if connection.kind == "api":
        return bool(db.scalar(select(Repository.id).where(
            Repository.id.in_(effective), Repository.connection_id == connection.id).limit(1)))
    return False


@router.get("/connections/{connection_id}/models", response_model=ModelCatalog)
def connection_models(connection_id: str, db: DB, user: USER, repository_id: int | None = None):
    connection = db.get(Connection, connection_id)
    if not connection:
        raise HTTPException(404, "Connection not found.")
    if repository_id is not None:
        repo_access(db, user, repository_id)
    if connection.kind != "api":
        raise HTTPException(410, RETIRED_MESSAGE)
    if getattr(user, "api_token_repository_ids", None) is not None:
        if not _available_for_token(db, connection, _token_effective_ids(db, user)):
            raise HTTPException(403, "This connection is not available for the selected repositories.")
    return model_discovery.api_models(connection, json.loads(unseal(connection.secret)))


@router.post("/connections/models", response_model=ModelCatalog)
def draft_models(body: ModelDiscoveryInput, db: DB, user: SESSION_ADMIN):
    connection = manage_connection(db, user, body.connection_id) if body.connection_id else None
    if connection and connection.kind != "api":
        raise HTTPException(410, RETIRED_MESSAGE)
    if body.protocol == "anthropic-bedrock":
        return model_discovery.api_models(body, {})
    credentials = model_discovery.credentials_for(body, connection)
    return model_discovery.api_models(body, credentials)


def save_connection(db: Session, body: ConnectionInput, connection: Connection | None = None, owner_id=None):
    require_shared(body)
    if connection:
        require_shared(connection)
    if connection and connection.kind != body.kind:
        raise HTTPException(422, "Create a new connection to change its type.")
    old = json.loads(unseal(connection.secret)) if connection else {}
    credentials = {
        "api_key": body.api_key if body.api_key is not None else old.get("api_key", ""),
        "extra_headers": body.extra_headers
        if body.extra_headers is not None
        else old.get("extra_headers", {}),
    }
    if body.kind == "api" and body.protocol != "anthropic-bedrock" and not credentials["api_key"]:
        raise HTTPException(
            422, "Enter an API key; use a placeholder for local models that do not require one."
        )
    url = safe_endpoint(body.base_url) if body.kind == "api" and body.protocol != "anthropic-bedrock" else ""
    if connection is None:
        connection = Connection(kind=body.kind, owner_id=owner_id if body.kind != "api" else None)
        db.add(connection)
    for key in ("name", "provider", "protocol", "model", "options"):
        setattr(connection, key, getattr(body, key))
    connection.base_url, connection.secret = url, seal(json.dumps(credentials))
    connection.last_test, connection.tested_at = None, None
    db.flush()
    return connection_out(connection)


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def add_connection(body: ConnectionInput, db: DB, user: SESSION_USER):
    if body.kind == "api" and user.role != "admin":
        raise HTTPException(403, "Administrator access is required to create shared API connections.")
    return save_connection(db, body, owner_id=user.id)


@router.put("/connections/{connection_id}", response_model=ConnectionOut)
def edit_connection(connection_id: str, body: ConnectionInput, db: DB, user: SESSION_USER):
    connection = manage_connection(db, user, connection_id)
    return save_connection(db, body, connection)


@router.post("/connections/{connection_id}/test", status_code=202)
def test_connection(connection_id: str, db: DB, user: SESSION_USER) -> dict:
    connection = manage_connection(db, user, connection_id)
    if connection.kind != "api":
        raise HTTPException(410, RETIRED_MESSAGE)
    connection.last_test = "Testing connection…"
    job = enqueue(db, "test_connection", {"connection_id": connection.id})
    return {"job_id": job.id}


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str, db: DB, user: SESSION_USER) -> dict:
    from code_mortis.models import SecurityScan

    connection = manage_connection(db, user, connection_id)
    if db.scalar(select(SecurityScan.id).where(SecurityScan.connection_id == connection_id,
                                               SecurityScan.status.in_({"queued", "running", "blocked"})).limit(1)):
        raise HTTPException(409, "Wait for active security scans using this connection to finish or cancel them.")
    if db.scalar(select(Repository).where(Repository.connection_id == connection_id).limit(1)):
        raise HTTPException(409, "Change the repositories using this connection before deleting it.")
    if any(
        run.config["connection"]["id"] == connection_id
        for run in db.scalars(select(ReviewRun).where(ReviewRun.status.in_({"queued", "running", "blocked"})))
    ):
        raise HTTPException(409, "Wait for active reviews using this connection to finish.")
    if connection.kind != "api":
        from code_mortis.integrations import runner_client

        runner_client.request("POST", f"/connections/{connection.id}/disconnect", {"kind": connection.kind})
    db.delete(connection)
    return {"ok": True}
