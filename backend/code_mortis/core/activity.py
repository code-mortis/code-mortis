import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import quote, urlencode

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    String,
    and_,
    case,
    cast,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    tuple_,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, insert
from sqlalchemy.types import UserDefinedType

from code_mortis.api.dependencies import repo_access
from code_mortis.core.security import seal, unseal
from code_mortis.integrations import github
from code_mortis.models import ActivityReadReceipt, Job, PullRequest, Repository, ReviewRun, SecurityScan
from code_mortis.schemas.activity import ActivityFeed, ActivityItem, ActivityReadOne

ACTIVE = ("running", "queued", "blocked")
REPOSITORY_GENERATION = ("context", "repository_summary")
TERMINAL = ("completed", "done", "partial", "failed", "cancelled", "superseded", "skipped")
PAGE_SIZE = 50
RECENT_READ = 5
STAGES = {
    "snapshot": "Snapshot",
    "trivy": "Trivy",
    "analysis": "Analysis",
    "ocr": "Analysis",
    "assessment": "Assessment",
    "verification": "Fix verification",
    "summary": "PR summary",
    "report": "Report",
}
JOB_STAGES = {
    "context": "Generating project context",
    "repository_summary": "Generating repository summary",
    "pr_summary": "PR summary",
}


class PgSnapshot(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kwargs):
        return "pg_snapshot"


class Xid8(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kwargs):
        return "xid8"


def access_denied(exc):
    if isinstance(exc, github.GitHubError):
        if exc.status == 403 and exc.retry_after > 0:
            raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
        return exc.status in {403, 404}
    return exc.status_code in {403, 404}


def target_scope(db, user, target):
    background = target.kind in REPOSITORY_GENERATION
    if background:
        if user.role != "admin":
            return [], []
        job = db.execute(select(Job.payload["repository_id"], Job.payload["actor_id"]).where(
            Job.id == target.id, Job.kind == "generate_background",
        )).one_or_none()
        if job is None or type(job[0]) is not int or type(job[1]) is not int or job[1] != user.id:
            return [], []
        repository_id = job[0]
    elif target.kind == "scan":
        repository_id = db.scalar(select(SecurityScan.repository_id).where(SecurityScan.id == target.id))
    else:
        statement = select(PullRequest.repository_id).join(ReviewRun, ReviewRun.pr_id == PullRequest.id)
        if target.kind == "pr_summary":
            statement = statement.join(Job, Job.payload["run_id"].as_string() == ReviewRun.id).where(
                Job.kind == "generate_summary", Job.id == target.id,
            )
        else:
            statement = statement.where(ReviewRun.id == target.id)
        repository_id = db.scalar(statement)
    if repository_id is None:
        return [], []
    try:
        _, remote = repo_access(db, user, repository_id, write=background)
    except (HTTPException, github.GitHubError) as exc:
        if access_denied(exc):
            return [], []
        raise
    if type(remote.get("id")) is not int or remote["id"] != repository_id:
        return [], []
    return [repository_id], [str(repository_id)] if background else []


def workspace_scope(db, user, *, mark_all):
    try:
        repos = github.visible_repositories(db, user, fail_on_throttle=True)
    except github.GitHubError as exc:
        access_denied(exc)  # Surface rate limits so a viewer keeps their last successful count.
        raise
    repo_ids = [repo.id for repo in repos]
    jobs_in_repos = []
    if user.role == "admin":
        background_repo_ids = set(db.scalars(select(Job.payload["repository_id"].as_string()).distinct().where(
            Job.kind == "generate_background",
            Job.payload["actor_id"].as_string() == str(user.id),
            Job.payload["repository_id"].as_string().in_([str(identity) for identity in repo_ids]),
            or_(Job.status.in_(ACTIVE), Job.finished_at.is_not(None)),
        )))
        for repo in repos:
            if str(repo.id) not in background_repo_ids:
                continue
            try:
                remote = github.authorize_repo(db, user, repo.full_name, write=mark_all)
            except github.GitHubError as exc:
                if access_denied(exc):
                    continue
                raise
            if (type(remote.get("id")) is int and remote["id"] == repo.id
                    and remote.get("permissions", {}).get("push") is True):
                jobs_in_repos.append(str(repo.id))
    return repo_ids, jobs_in_repos


def candidates(db, user, as_of, target=None, snapshot=None):
    repo_ids, jobs_in_repos = (target_scope(db, user, target) if target is not None
                              else workspace_scope(db, user, mark_all=snapshot is not None))

    def columns(kind, identity, status, started_at, finished_at, activity_xid, title,
                number=None, stages=None, phase=None, parent=None, mode=None):
        return (
            kind.label("kind"), identity.label("id"), Repository.full_name.label("repository"),
            Repository.id.label("repository_id"), status.label("status"), started_at.label("started_at"),
            finished_at.label("finished_at"), activity_xid.label("activity_xid"), title.label("title"),
            (literal(None) if number is None else number).label("number"),
            (cast(literal(None), JSON) if stages is None else stages).label("stages"),
            (literal(None) if phase is None else phase).label("phase"),
            (literal(None) if parent is None else parent).label("parent"),
            (literal(None) if mode is None else mode).label("mode"),
        )

    # All four branches project status metadata only; reports, prompts and credentials stay in their source tables.
    review = select(*columns(
        literal("review"), ReviewRun.id, ReviewRun.status, ReviewRun.created_at,
        func.coalesce(ReviewRun.activity_finished_at, ReviewRun.finished_at), ReviewRun.activity_xid,
        PullRequest.title, PullRequest.number,
        case((ReviewRun.status == "running", ReviewRun.stages), else_=cast(literal(None), JSON)),
        case((ReviewRun.status == "running", ReviewRun.progress["phase"].as_string())),
    )).select_from(ReviewRun).join(PullRequest).join(Repository).where(Repository.id.in_(repo_ids))
    scan = select(*columns(
        literal("scan"), SecurityScan.id, SecurityScan.status, SecurityScan.created_at,
        SecurityScan.finished_at, SecurityScan.activity_xid, SecurityScan.branch,
        stages=case((SecurityScan.status == "running", SecurityScan.stages), else_=cast(literal(None), JSON)),
        phase=case((SecurityScan.status == "running", SecurityScan.progress["phase"].as_string())),
    )).select_from(SecurityScan).join(Repository).where(Repository.id.in_(repo_ids))
    mode = Job.payload["mode"].as_string()
    repository_summary = mode.in_(("summary", "summary_draft", "summary_refresh"))
    background = select(*columns(
        case((repository_summary, "repository_summary"), else_="context"),
        Job.id, Job.status, Job.available_at, Job.finished_at, Job.activity_xid,
        case((repository_summary, "Repository summary"), else_="Project context"), mode=mode,
    )).select_from(Job).join(Repository, cast(Repository.id, Job.id.type) == Job.payload["repository_id"].as_string()).where(
        Job.kind == "generate_background", Job.payload["actor_id"].as_string() == str(user.id),
        func.json_typeof(Job.payload["actor_id"]) == "number",
        func.json_typeof(Job.payload["repository_id"]) == "number",
        Job.payload["repository_id"].as_string().in_(jobs_in_repos),
    )
    pr_summary = select(*columns(
        literal("pr_summary"), Job.id, Job.status, Job.available_at, Job.finished_at, Job.activity_xid,
        PullRequest.title, PullRequest.number, parent=ReviewRun.id,
    )).select_from(Job).join(ReviewRun, ReviewRun.id == Job.payload["run_id"].as_string()).join(
        PullRequest,
    ).join(Repository).where(Job.kind == "generate_summary", Repository.id.in_(repo_ids))
    raw = union_all(review, scan, background, pr_summary).subquery()
    c = raw.c
    unread = and_(
        c.finished_at >= user.activity_started_at,
        ~exists().where(
            ActivityReadReceipt.user_id == user.id,
            ActivityReadReceipt.kind == c.kind,
            ActivityReadReceipt.item_id == c.id,
            ActivityReadReceipt.finished_at == c.finished_at,
        ),
    )
    statement = select(
        raw, unread.label("unread"), func.coalesce(c.finished_at, c.started_at).label("sort_at"),
    ).where(or_(c.status.in_(ACTIVE), and_(c.status.in_(TERMINAL), c.finished_at <= as_of)))
    if target is not None:
        statement = statement.where(c.kind == target.kind, c.id == target.id)
    if snapshot is not None:
        statement = statement.where(or_(
            c.activity_xid.is_(None),  # Historical activity from before the migration.
            func.pg_visible_in_snapshot(cast(c.activity_xid, Xid8()), cast(literal(snapshot), PgSnapshot())),
        ))
    return statement.cte("activity_candidates")


def stage_labels(row):
    if row.status != "running":
        return []
    if row.kind in JOB_STAGES:
        return [JOB_STAGES[row.kind]]
    stages = row.stages or {}
    current = [label for stage, label in STAGES.items()
               if isinstance(stages.get(stage), dict) and stages[stage].get("status") == "running"]
    if current:
        return list(dict.fromkeys(current))
    fallback = {"fix verification": "Fix verification", "summary": "PR summary",
                "assessment": "Assessment", "analysis": "Analysis"}
    return [fallback.get(row.phase) or ("Preparing review" if row.kind == "review" else "Preparing scan")]


def item(row):
    identity = quote(row.id, safe="")
    title = row.title
    if row.kind in {"review", "pr_summary"}:
        title = f"#{row.number} {title}"
        href = f"/runs/{identity}" if row.kind == "review" else (
            f"/runs/{quote(row.parent, safe='')}?{urlencode({'summaryJob': row.id})}"
        )
    elif row.kind == "scan":
        href = f"/security/scans/{identity}"
    else:
        repository = f"/repositories/{row.repository_id}"
        if row.mode in {"summary", "summary_refresh"}:
            href = f"{repository}/overview?{urlencode({'generation': row.id})}"
        elif row.mode == "context_refresh":
            href = f"{repository}/settings?tab=context"
        else:
            href = f"{repository}/settings?{urlencode({'tab': 'context', 'generation': row.id})}"
    return ActivityItem(
        kind=row.kind, id=row.id, repository=row.repository, title=title, href=href,
        status="completed" if row.status == "done" else row.status, stages=stage_labels(row),
        finished_at=row.finished_at if row.status in TERMINAL else None, unread=bool(row.unread),
    )


def page_query(rows, cursor):
    columns = (rows.c.unread, rows.c.sort_at, rows.c.kind, rows.c.id)
    statement = select(rows)
    if cursor is not None:
        try:
            unread, iso, kind, identity = urlsafe_b64decode(cursor).decode().split("|")
            sort_at = datetime.fromisoformat(iso)
            if unread not in {"0", "1"} or sort_at.tzinfo is None or not kind or not identity:
                raise ValueError
        except (ValueError, Base64Error) as exc:
            raise HTTPException(422, "Invalid activity cursor.") from exc
        statement = statement.where(tuple_(*columns) < (unread == "1", sort_at, kind, identity))
    ordered = statement.order_by(*(column.desc() for column in columns)).limit(PAGE_SIZE + 1).subquery()
    return select(
        cast(func.jsonb_agg(aggregate_order_by(func.to_jsonb(ordered.table_valued()),
             *(ordered.c[column.key].desc() for column in columns))), JSONB)
    ).scalar_subquery()


def page_result(result):
    result = result or []
    next_cursor = None
    if len(result) > PAGE_SIZE:
        last = SimpleNamespace(**result[PAGE_SIZE - 1])
        key = f"{int(bool(last.unread))}|{last.sort_at}|{last.kind}|{last.id}"
        next_cursor = urlsafe_b64encode(key.encode()).decode()
    return [item(SimpleNamespace(**row)) for row in result[:PAGE_SIZE]], next_cursor


def feed(db, user, running_cursor, updates_cursor):
    # Counts, both pages, and the bulk-read token all describe the same MVCC snapshot.
    bounds = select(func.clock_timestamp().label("as_of"),
                    cast(func.pg_current_snapshot(), String).label("snapshot")).cte()
    rows = candidates(db, user, select(bounds.c.as_of).scalar_subquery())
    c = rows.c
    active = select(
        *[column for column in c if column.key != "unread"], false().label("unread"),
    ).where(c.status.in_(ACTIVE)).subquery()
    terminal = select(rows).where(c.status.in_(TERMINAL))
    recent = terminal.where(~c.unread).order_by(c.sort_at.desc(), c.kind.desc(), c.id.desc()).limit(RECENT_READ)
    updates_query = union_all(terminal.where(c.unread), recent).subquery()
    row = db.execute(select(
        bounds.c.as_of,
        bounds.c.snapshot,
        select(func.count()).where(c.status == "running").scalar_subquery().label("running_count"),
        select(func.count()).where(c.status == "queued").scalar_subquery().label("queued_count"),
        select(func.count()).where(c.status.in_(TERMINAL), c.unread).scalar_subquery().label("unread_count"),
        page_query(active, running_cursor).label("running"),
        page_query(updates_query, updates_cursor).label("updates"),
    )).one()
    running, next_running = page_result(row.running)
    updates, next_updates = page_result(row.updates)
    snapshot = seal(json.dumps(["activity-inbox-v1", user.id, row.as_of.isoformat(), row.snapshot]))
    return ActivityFeed(running=running, updates=updates, running_count=row.running_count, queued_count=row.queued_count,
                        unread_count=row.unread_count, as_of=row.as_of, snapshot=snapshot,
                        next_running_cursor=next_running, next_updates_cursor=next_updates)


def mark_read(db, user, body, now):
    target = body if isinstance(body, ActivityReadOne) else None
    snapshot = None
    as_of = now
    if target is None:
        try:
            purpose, owner, timestamp, snapshot = json.loads(unseal(body.snapshot))
            as_of = datetime.fromisoformat(timestamp)
            if purpose != "activity-inbox-v1" or owner != user.id or as_of != body.as_of:
                raise ValueError
        except (InvalidToken, TypeError, ValueError) as exc:
            raise HTTPException(422, "Invalid activity snapshot. Refresh the inbox and try again.") from exc
    rows = candidates(db, user, as_of, target, snapshot)
    c = rows.c
    pending = select(literal(user.id), c.kind, c.id, c.finished_at).where(c.status.in_(TERMINAL), c.unread)
    if target is not None and target.finished_at is not None:
        pending = pending.where(c.finished_at == target.finished_at)
    db.execute(insert(ActivityReadReceipt).from_select(
        ["user_id", "kind", "item_id", "finished_at"], pending,
    ).on_conflict_do_nothing())
