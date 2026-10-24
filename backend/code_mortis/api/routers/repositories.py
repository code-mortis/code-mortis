from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from code_mortis.api.dependencies import ADMIN, DB, USER, repo_access
from code_mortis.api.presenters import repo_out
from code_mortis.integrations import github
from code_mortis.models import Connection, ConnectionGrant, Job, Repository, ReviewRun
from code_mortis.reviews import background, inbox
from code_mortis.reviews.service import enqueue
from code_mortis.schemas.common import (
    BackgroundInput,
    BackgroundJobOut,
    InboxOut,
    RepositoryInput,
    RepositoryOut,
    RepositoryPullFilters,
    RunOut,
)

router = APIRouter()


@router.get("/repositories", response_model=list[RepositoryOut])
def repositories(db: DB, user: USER):
    from code_mortis.security_scans.service import summaries

    repos = github.visible_repositories(db, user)
    security = summaries(db, [repo.id for repo in repos])
    result = [repo_out(db, repo) for repo in repos]
    for repo in result:
        repo.security = security[repo.id]
    return result


@router.put("/repositories/{repository_id}", response_model=RepositoryOut)
def update_repository(repository_id: int, body: RepositoryInput, db: DB, user: ADMIN):
    db.scalar(select(Repository).where(Repository.id == repository_id).with_for_update())
    repo, _ = repo_access(db, user, repository_id, write=True)
    if body.connection_id and not db.get(Connection, body.connection_id):
        raise HTTPException(422, "Choose an existing AI connection.")
    if body.enabled and not body.connection_id:
        raise HTTPException(422, "Choose an AI connection before enabling reviews.")
    if body.enabled and body.settings.auto_resolve:
        github.installation_token(db, repo.installation_id, resolve=True)
    if body.connection_id:
        selected = db.get(Connection, body.connection_id)
        if body.enabled and not (body.settings.model.strip() or selected.model.strip()):
            raise HTTPException(422, "Choose a model before enabling reviews.")
        if selected.kind != "api":
            grant = db.get(ConnectionGrant, (selected.id, repo.id))
            if not grant or (body.settings.automatic and not grant.automatic):
                raise HTTPException(
                    403,
                    "The connection owner must authorize this repository and its automatic reviews first.",
                )
    repo.enabled, repo.connection_id, repo.settings = (
        body.enabled,
        body.connection_id,
        body.settings.model_dump(),
    )
    if repo.enabled:
        enqueue(db, "scan_repository", {"repository_id": repo.id})
    db.flush()
    return repo_out(db, repo)


@router.post("/repositories/{repository_id}/background", response_model=BackgroundJobOut, status_code=202)
def generate_background(repository_id: int, body: BackgroundInput, db: DB, user: ADMIN):
    repo, _ = repo_access(db, user, repository_id, write=True)
    connection = db.get(Connection, body.connection_id)
    background.authorize(db, repo, user, connection, check_repository=False)
    return background.job_out(background.enqueue(db, repo, user, connection, body.model, body.limits))


@router.get("/repositories/{repository_id}/background/{job_id}", response_model=BackgroundJobOut)
def background_status(repository_id: int, job_id: str, db: DB, user: ADMIN):
    repo_access(db, user, repository_id, write=True)
    job = db.get(Job, job_id)
    if (not job or job.kind != "generate_background"
        or job.payload.get("repository_id") != repository_id or job.payload.get("actor_id") != user.id):
        raise HTTPException(404, "Background generation not found.")
    return background.job_out(job)


@router.get("/repositories/{repository_id}/pulls", response_model=InboxOut)
def pull_requests(repository_id: int, db: DB, user: USER, filters: Annotated[RepositoryPullFilters, Query()]):
    repo, _ = repo_access(db, user, repository_id)
    result = inbox.list_pulls(db, [repo], filters)
    run_ids = {item.run_id for page in result.groups for item in page.items if item.run_id}
    runs = {run.id: run for run in db.scalars(select(ReviewRun).where(ReviewRun.id.in_(run_ids)))}
    for page in result.groups:
        for item in page.items:
            if run := runs.get(item.run_id):
                item.latest_run = RunOut.model_validate(run)
    return result


@router.post("/repositories/{repository_id}/sync", status_code=202)
def refresh_repository(repository_id: int, db: DB, user: USER) -> dict:
    repo_access(db, user, repository_id, write=True)
    job = enqueue(db, "scan_repository", {"repository_id": repository_id})
    return {"job_id": job.id}
