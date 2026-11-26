import json
from datetime import datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from code_mortis.api.dependencies import ADMIN, DB, SESSION_ADMIN, SESSION_USER, USER, repo_access
from code_mortis.api.presenters import latest_repository_reviews, repo_out, repos_out
from code_mortis.api_tokens import provenance as pat
from code_mortis.code_graph import repository as repository_graph
from code_mortis.code_graph import retrieval, store
from code_mortis.code_graph import service as graphs
from code_mortis.core.db import now, uid
from code_mortis.core.security import unseal
from code_mortis.integrations import github
from code_mortis.mcp import provenance as mcp
from code_mortis.models import (
    CodeGraphRepository,
    Connection,
    CustomReviewRule,
    Finding,
    FindingVerification,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityActivity,
    SecurityFinding,
    SecurityOccurrence,
    SecurityScan,
)
from code_mortis.reviews import background, inbox, rules, rules_preview
from code_mortis.reviews.context import execution_credentials
from code_mortis.reviews.service import enqueue
from code_mortis.schemas.analytics import (
    RepositoryActivity,
    RepositoryActivityDay,
    RepositoryActivityFilters,
    RepositoryResult,
)
from code_mortis.schemas.code_graph import MAX_INDEXED_FILE_BYTES, GitSha, RepositoryPath
from code_mortis.schemas.code_graph_explorer import GraphRead
from code_mortis.schemas.code_graph_status import CodeGraphStatus
from code_mortis.schemas.common import (
    REVIEW_RULE_LIMIT,
    BackgroundCancelInput,
    BackgroundInput,
    BackgroundJobOut,
    InboxOut,
    RepositoryInput,
    RepositoryOut,
    RepositoryPullFilters,
    ReviewRulesPreview,
    ReviewRulesPreviewInput,
    ReviewRulesUpdate,
    RunOut,
    StoredReviewRule,
)
from code_mortis.schemas.repository_graph import (
    RepositoryGraphFiles,
    RepositoryGraphFilters,
    RepositoryGraphView,
)

router = APIRouter()


@router.get("/repositories", response_model=list[RepositoryOut])
def repositories(db: DB, user: USER):
    from code_mortis.security_scans.service import summaries

    repos = github.visible_repositories(db, user)
    repository_ids = [repo.id for repo in repos]
    security = summaries(db, repository_ids)
    reviews = latest_repository_reviews(db, repository_ids)
    result = repos_out(db, repos)
    for repo in result:
        repo.security = security[repo.id]
        repo.latest_completed_review = reviews.get(repo.id)
    return result


def _recent_results(db: Session, repository_id: int, end: datetime) -> list[RepositoryResult]:
    reviews = db.execute(select(ReviewRun.id, ReviewRun.finished_at, PullRequest.number, PullRequest.title)
        .join(PullRequest, PullRequest.id == ReviewRun.pr_id).where(
            PullRequest.repository_id == repository_id, ReviewRun.status == "completed", ReviewRun.finished_at <= end,
        ).order_by(ReviewRun.finished_at.desc(), ReviewRun.id.desc()).limit(3))
    results = [RepositoryResult(kind="review", at=at, run_id=run_id, title=title, pr_number=number)
               for run_id, at, number, title in reviews]
    scans = db.execute(select(SecurityScan.id, SecurityScan.finished_at, SecurityScan.branch, SecurityScan.status)
        .where(SecurityScan.repository_id == repository_id, SecurityScan.status.in_(["completed", "partial"]),
               SecurityScan.finished_at <= end)
        .order_by(SecurityScan.finished_at.desc(), SecurityScan.id.desc()).limit(3))
    results.extend(RepositoryResult(kind="scan", at=at, run_id=run_id, title=branch, scan_status=status)
                   for run_id, at, branch, status in scans)
    review_fixes = db.execute(select(FindingVerification.run_id, FindingVerification.finished_at, Finding.id,
                                    Finding.path, PullRequest.number)
        .join(ReviewRun, ReviewRun.id == FindingVerification.run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pr_id)
        .join(Finding, (Finding.id == FindingVerification.finding_id) & (Finding.pr_id == PullRequest.id))
        .where(PullRequest.repository_id == repository_id, FindingVerification.status == "completed",
               FindingVerification.verdict == "fixed", FindingVerification.resolution == "resolved",
               FindingVerification.finished_at <= end)
        .order_by(FindingVerification.finished_at.desc(), FindingVerification.run_id.desc(), Finding.id.desc()).limit(3))
    results.extend(RepositoryResult(kind="review_fix", at=at, run_id=run_id, title=path, pr_number=number, finding_id=finding_id)
                   for run_id, at, finding_id, path, number in review_fixes)
    scan_fixes = db.execute(select(SecurityActivity.scan_id, SecurityActivity.created_at, SecurityFinding.id,
                                  func.coalesce(SecurityOccurrence.snapshot["title"].as_string(), SecurityFinding.title))
        .join(SecurityScan, SecurityScan.id == SecurityActivity.scan_id)
        .join(SecurityFinding, SecurityFinding.id == SecurityActivity.finding_id)
        .join(SecurityOccurrence, (SecurityOccurrence.scan_id == SecurityScan.id)
              & (SecurityOccurrence.finding_id == SecurityFinding.id))
        .where(SecurityScan.repository_id == repository_id, SecurityFinding.repository_id == repository_id,
               SecurityActivity.action == "finding_resolved", SecurityActivity.created_at <= end,
               SecurityOccurrence.outcome == "fixed")
        .order_by(SecurityActivity.created_at.desc(), SecurityActivity.scan_id.desc(), SecurityFinding.id.desc()).limit(3))
    results.extend(RepositoryResult(kind="scan_fix", at=at, run_id=run_id, title=title, finding_id=finding_id)
                   for run_id, at, finding_id, title in scan_fixes)
    return sorted(results, key=lambda result: (result.at, result.kind, result.run_id, result.finding_id or ""), reverse=True)[:3]


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
    return RepositoryActivity(
        days=[RepositoryActivityDay(date=day, reviews=reviews.get(day, 0), scans=scans.get(day, 0)) for day in dates],
        recent_results=_recent_results(db, repo.id, end),
    )


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
    if "rules" not in body.settings.model_fields_set:
        effective_settings["rules"] = repo.settings.get("rules")
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
        repo.scan_settings = {**repo.scan_settings, **body.scan_settings.model_dump(exclude_unset=True)}
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


@router.put("/repositories/{repository_id}/review-rules", response_model=list[StoredReviewRule])
def update_review_rules(repository_id: int, body: ReviewRulesUpdate, db: DB, user: SESSION_ADMIN):
    db.scalar(select(Repository).where(Repository.id == repository_id).with_for_update()
              .execution_options(populate_existing=True))
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    rows = rules.repository_rows(db, [repo.id])
    records = [rules.record(row) for row in rows]
    if records != body.expected_rules:
        raise HTTPException(409, "Review rules changed while you were editing. Reload settings and try again.")
    change = body.change
    if change.operation == "add":
        if len(records) >= REVIEW_RULE_LIMIT:
            raise HTTPException(422, f"A repository can have at most {REVIEW_RULE_LIMIT} custom rules.")
        timestamp = now()
        row = CustomReviewRule(rule_id=uid(), scope="repository", repository_id=repo.id,
                               position=rows[-1].position + 1 if rows else 0, definition=change.rule.model_dump(),
                               created_at=timestamp, updated_at=timestamp)
        db.add(row)
        records.append(rules.record(row))
    elif change.index >= len(records):
        raise HTTPException(422, "Review rule index is out of range.")
    elif change.operation == "delete":
        db.delete(rows[change.index])
        del records[change.index]
    else:
        row = rows[change.index]
        updated = change.rule.model_dump()
        if updated != records[change.index].for_execution():
            row.definition, row.updated_at = updated, now()
            records[change.index] = rules.record(row)
    return records


@router.get("/repositories/{repository_id}/review-rules/files", response_model=list[RepositoryPath])
def review_rule_files(repository_id: int, db: DB, user: SESSION_ADMIN,
                      q: Annotated[str, Query(max_length=2000, pattern=r"^[^\x00-\x1f\x7f]*$")]):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    try:
        return retrieval.default_file_paths(db, repo.id, user.id, q)
    except graphs.AccessLost as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/repositories/{repository_id}/review-rules/preview", response_model=ReviewRulesPreview)
def preview_review_rules(repository_id: int, body: ReviewRulesPreviewInput, db: DB, user: SESSION_ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    token = github.repository_read_token(db, repo.installation_id, repo.id)
    connection = db.get(Connection, repo.connection_id) if repo.connection_id else None
    shared = connection if connection and connection.kind == "api" else None
    secret = json.loads(unseal(shared.secret)) if shared else {}
    credentials = execution_credentials(db, repo.id, secret, token)
    records, workspace_count = rules.effective(db, repo.id, repository_draft=body.rules)
    options = {**repo.settings, "review_rules": [rule.for_execution() for rule in records]}
    ceiling = shared.options.get("prompt_token_ceiling") if shared else None
    try:
        return rules_preview.preview(repo.full_name, remote.get("default_branch"), token, body.path, options, credentials,
                                     prompt_token_ceiling=ceiling, workspace_count=workspace_count)
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc


@router.get("/repositories/{repository_id}/code-graph", response_model=CodeGraphStatus)
def code_graph_status(repository_id: int, db: DB, user: SESSION_ADMIN):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("id")) is not int or remote["id"] != repo.id:
        raise HTTPException(403, "GitHub repository identity changed.")
    return graphs.status(db, repo)


@router.get("/repositories/{repository_id}/code-graph/view", response_model=RepositoryGraphView)
def repository_code_graph(repository_id: int, user: SESSION_USER, response: Response,
                          filters: Annotated[RepositoryGraphFilters, Query()]):
    response.headers["Cache-Control"] = "no-store"
    return repository_graph.respond(lambda: repository_graph.view(repository_id, user.id, filters))


@router.get("/repositories/{repository_id}/code-graph/files", response_model=RepositoryGraphFiles)
def repository_code_graph_files(repository_id: int, user: SESSION_USER, response: Response,
                                commit_sha: GitSha, query: Annotated[str, Query(min_length=1, max_length=512)]):
    response.headers["Cache-Control"] = "no-store"
    return repository_graph.respond(lambda: repository_graph.files(repository_id, user.id, commit_sha, query))


@router.get("/repositories/{repository_id}/code-graph/source", response_model=GraphRead)
def repository_code_graph_source(repository_id: int, user: SESSION_USER, response: Response,
                                 commit_sha: GitSha, path: RepositoryPath,
                                 start_byte: Annotated[int, Query(ge=0, le=MAX_INDEXED_FILE_BYTES)] = 0):
    response.headers["Cache-Control"] = "no-store"
    return repository_graph.respond(
        lambda: repository_graph.source(repository_id, user.id, commit_sha, path, start_byte)
    )


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
    result = inbox.list_pulls(db, [repo], filters, strict_state=filters.strict_state)
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
