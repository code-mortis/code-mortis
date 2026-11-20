from datetime import datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Date, cast, func, select

from code_mortis.api.dependencies import ADMIN, DB, SESSION_ADMIN, SESSION_USER, USER, repo_access
from code_mortis.api.presenters import latest_repository_reviews, repo_out
from code_mortis.api_tokens import provenance as pat
from code_mortis.code_graph import service as graphs
from code_mortis.code_graph import store
from code_mortis.core.db import now
from code_mortis.integrations import github
from code_mortis.mcp import provenance as mcp
from code_mortis.models import (
    CodeGraphRepository,
    Connection,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityScan,
)
from code_mortis.reviews import background, inbox
from code_mortis.reviews.service import enqueue
from code_mortis.schemas.analytics import RepositoryActivity, RepositoryActivityDay, RepositoryActivityFilters
from code_mortis.schemas.code_graph_status import CodeGraphStatus
from code_mortis.schemas.common import (
    BackgroundCancelInput,
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
    reviews = latest_repository_reviews(db, [repo.id for repo in repos])
    result = [repo_out(db, repo) for repo in repos]
    for repo in result:
        repo.security = security[repo.id]
        repo.latest_completed_review = reviews.get(repo.id)
    return result


@router.get("/repositories/{repository_id}/activity", response_model=RepositoryActivity)
def repository_activity(
    repository_id: int, db: DB, user: SESSION_USER, filters: Annotated[RepositoryActivityFilters, Query()],
):
    repo, remote = repo_access(db, user, repository_id)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")

    end = now()
    zone = ZoneInfo(filters.timezone)
    first = end.astimezone(zone).date() - timedelta(days=364)
    start = datetime.combine(first, time.min, zone)
    review_day = cast(func.timezone(filters.timezone, ReviewRun.created_at), Date)
    scan_day = cast(func.timezone(filters.timezone, SecurityScan.created_at), Date)
    reviews = dict(db.execute(
        select(review_day, func.count()).select_from(ReviewRun).join(PullRequest).where(
            PullRequest.repository_id == repo.id, ReviewRun.created_at >= start, ReviewRun.created_at <= end,
        ).group_by(review_day)
    ).all())
    scans = dict(db.execute(
        select(scan_day, func.count()).where(
            SecurityScan.repository_id == repo.id, SecurityScan.created_at >= start, SecurityScan.created_at <= end,
        ).group_by(scan_day)
    ).all())
    dates = (first + timedelta(days=offset) for offset in range(365))
    return RepositoryActivity(days=[
        RepositoryActivityDay(date=day, reviews=reviews.get(day, 0), scans=scans.get(day, 0)) for day in dates
    ])


@router.put("/repositories/{repository_id}", response_model=RepositoryOut)
def update_repository(repository_id: int, body: RepositoryInput, db: DB, user: ADMIN):
    db.scalar(select(Repository).where(Repository.id == repository_id).with_for_update()
              .execution_options(populate_existing=True))
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    effective_settings = body.settings.model_dump()
    saved_background = repo.settings.get("background", "")
    if body.expected_background is not None and saved_background != body.expected_background:
        raise HTTPException(409, "Project context changed while you were editing. Reload settings and try again.")
    if ("project_summary" in body.settings.model_fields_set and "expected_project_summary" in body.model_fields_set
            and repo.settings.get("project_summary") != body.expected_project_summary):
        raise HTTPException(409, "The repository summary changed while you were editing. Reload settings and try again.")
    if "background" not in body.settings.model_fields_set:
        effective_settings["background"] = saved_background
    if "project_summary" not in body.settings.model_fields_set:
        effective_settings["project_summary"] = repo.settings.get("project_summary")
    if effective_settings["project_summary"] is None:
        effective_settings.pop("project_summary")
    if body.code_graph_enabled is not None or body.code_graph_limits is not None:
        graphs.browser_only()
    if body.connection_id and not db.get(Connection, body.connection_id):
        raise HTTPException(422, "Choose an existing AI connection.")
    if body.enabled and not body.connection_id:
        raise HTTPException(422, "Choose an AI connection before enabling reviews.")
    if body.enabled and body.settings.auto_resolve:
        github.installation_token(db, repo.installation_id, resolve=True)
    if body.connection_id:
        selected = db.get(Connection, body.connection_id)
        if selected.kind != "api":
            if selected.id != repo.connection_id or (body.enabled and not repo.enabled):
                raise HTTPException(422, "Subscription connections are retired. Select a shared API connection.")
        elif body.enabled and not (body.settings.model.strip() or selected.model.strip()):
            raise HTTPException(422, "Choose a model before enabling reviews.")
    old_policy = store.policy(repo)
    if body.code_graph_limits is not None:
        repo.code_graph_limits = body.code_graph_limits.model_dump()
    if body.scan_settings is not None:
        repo.scan_settings = body.scan_settings.model_dump()
    repo.enabled, repo.connection_id, repo.settings = (
        body.enabled,
        body.connection_id,
        effective_settings,
    )
    config = db.get(CodeGraphRepository, repo.id)
    graph_changed = body.code_graph_enabled is not None and body.code_graph_enabled != bool(config and config.enabled)
    if graph_changed:
        config = graphs.configure_record(db, repo, user.id, enabled=body.code_graph_enabled)
    if config and config.enabled:
        store.policy(repo)
        browser = pat.current_provenance() is None and mcp.current_provenance() is None
        if browser and (body.code_graph_enabled is not None or old_policy != store.policy(repo)):
            graphs.schedule_refresh(db, repo.id, retry=graph_changed or old_policy != store.policy(repo))
    if repo.enabled and selected.kind == "api":
        enqueue(db, "scan_repository", {"repository_id": repo.id})
    db.flush()
    return repo_out(db, repo)


@router.get("/repositories/{repository_id}/code-graph", response_model=CodeGraphStatus)
def code_graph_status(repository_id: int, db: DB, user: SESSION_ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    return graphs.status(db, repo)


@router.post("/repositories/{repository_id}/code-graph/retry", response_model=CodeGraphStatus, status_code=202)
def retry_code_graph(repository_id: int, db: DB, user: SESSION_ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    db.scalar(select(Repository).where(Repository.id == repo.id).with_for_update())
    config = db.get(CodeGraphRepository, repo.id)
    if not config or not config.enabled:
        raise HTTPException(409, "Enable the code graph before retrying indexing.")
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    graphs.configure_record(db, repo, user.id, enabled=True)
    graphs.schedule_refresh(db, repo.id, retry=True)
    db.flush()
    return graphs.status(db, repo)


@router.post("/repositories/{repository_id}/code-graph/cancel", response_model=CodeGraphStatus)
def cancel_code_graph(repository_id: int, db: DB, user: SESSION_ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    db.scalar(select(Repository).where(Repository.id == repo.id).with_for_update())
    graphs.cancel_repository(db, repo)
    db.flush()
    return graphs.status(db, repo)


@router.post("/repositories/{repository_id}/background", response_model=BackgroundJobOut, status_code=202)
def generate_background(repository_id: int, body: BackgroundInput, db: DB, user: ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    db.refresh(repo, with_for_update={"key_share": True})
    if body.generation_id is not None:
        prior = background.requested_job(db, repo, user, body.generation_id)
        if prior:
            return background.job_out(prior)
    connection_id, model, limits = body.connection_id, body.model, body.limits
    if body.mode not in {"draft", "context_refresh"}:
        saved = repo.settings.get("background", "").strip()
        has_summary = bool(repo.settings.get("project_summary"))
        if body.mode in {"summary_draft", "summary_refresh"}:
            if not has_summary:
                raise HTTPException(409, "The saved repository summary changed. Refresh the repository and try again.")
        elif has_summary or bool(saved) != (body.mode == "summary"):
            raise HTTPException(409, "The saved project context changed. Refresh the repository and try again.")
        connection_id, model, limits = repo.connection_id, repo.settings.get("model", ""), None
    connection = db.get(Connection, connection_id) if connection_id else None
    background.authorize(db, repo, user, connection, check_repository=False)
    return background.job_out(background.enqueue(db, repo, user, connection, model, limits,
                                                 mode=body.mode, generation_id=body.generation_id))


@router.post("/repositories/{repository_id}/background/cancel", response_model=BackgroundJobOut)
def cancel_background(repository_id: int, body: BackgroundCancelInput, db: DB, user: SESSION_ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    # Serialize with starts while allowing the worker's analysis plan foreign-key check.
    db.refresh(repo, with_for_update={"key_share": True})
    return background.job_out(background.cancel_request(db, repo, user, body.generation_id))


@router.get("/repositories/{repository_id}/background/{job_id}", response_model=BackgroundJobOut)
def background_status(repository_id: int, job_id: str, db: DB, user: ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
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
