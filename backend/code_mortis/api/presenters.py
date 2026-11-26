import json

from sqlalchemy import BigInteger, String, any_, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from code_mortis.core.security import unseal
from code_mortis.models import (
    CodeGraphRepository,
    Connection,
    Finding,
    PullRequest,
    Repository,
    ReviewRun,
)
from code_mortis.reviews import rules
from code_mortis.schemas.common import (
    ConnectionOut,
    PullRequestOut,
    RepositoryOut,
    RepositoryReviewSummary,
    RunOut,
)


def latest_repository_reviews(db: Session, repository_ids: list[int]) -> dict[int, RepositoryReviewSummary]:
    if not repository_ids:
        return {}
    latest = select(
        PullRequest.repository_id, ReviewRun.id.label("run_id"), PullRequest.id.label("pull_request_id"),
        PullRequest.number, ReviewRun.head_sha, ReviewRun.finished_at,
        func.row_number().over(partition_by=PullRequest.repository_id,
                               order_by=(ReviewRun.finished_at.desc(), ReviewRun.id.desc())).label("rank"),
    ).join(PullRequest, ReviewRun.pr_id == PullRequest.id).where(
        PullRequest.repository_id.in_(repository_ids), ReviewRun.status == "completed",
        ReviewRun.finished_at.is_not(None),
    ).subquery()
    return {
        row.repository_id: RepositoryReviewSummary.model_validate({
            key: value for key, value in row._mapping.items() if key not in {"repository_id", "rank"}
        })
        for row in db.execute(select(latest).where(latest.c.rank == 1))
    }


def repos_out(db: Session, repos: list[Repository]) -> list[RepositoryOut]:
    if not repos:
        return []
    ids = [repo.id for repo in repos]
    repo_ids = literal(ids, type_=ARRAY(BigInteger))
    connection_ids = literal([repo.connection_id for repo in repos], type_=ARRAY(String))
    # Match the connection ID returned to the caller even if repository settings change mid-request.
    listed = func.unnest(repo_ids, connection_ids).table_valued("id", "connection_id").render_derived(name="listed")
    selected = any_(repo_ids)
    open_pulls = select(PullRequest.repository_id, func.count().label("total")).where(
        PullRequest.repository_id == selected, PullRequest.ignored.is_(False), PullRequest.state == "open",
    ).group_by(PullRequest.repository_id).subquery()
    running = select(PullRequest.repository_id).join(ReviewRun, ReviewRun.pr_id == PullRequest.id).where(
        PullRequest.repository_id == selected, PullRequest.ignored.is_(False), ReviewRun.status == "running",
    ).distinct().subquery()
    stats = (
        select(listed.c.id, CodeGraphRepository.enabled, Connection.kind,
               func.coalesce(open_pulls.c.total, 0).label("open_prs"),
               running.c.repository_id.is_not(None).label("has_running_review"))
        .select_from(listed)
        .outerjoin(CodeGraphRepository, CodeGraphRepository.repository_id == listed.c.id)
        .outerjoin(Connection, Connection.id == listed.c.connection_id)
        .outerjoin(open_pulls, open_pulls.c.repository_id == listed.c.id)
        .outerjoin(running, running.c.repository_id == listed.c.id)
    )

    by_id = {row.id: row for row in db.execute(stats)}
    configured_rules = rules.by_repository(db, ids)
    result = []
    for repo in repos:
        row = by_id[repo.id]
        item = RepositoryOut.model_validate(repo)
        item.review_rules = configured_rules[repo.id]
        item.code_graph_enabled = bool(row.enabled)
        item.open_prs = row.open_prs
        item.has_running_review = row.has_running_review
        item.connection_requires_setup = bool(repo.connection_id and row.kind != "api")
        result.append(item)
    return result


def repo_out(db: Session, repo: Repository) -> RepositoryOut:
    return repos_out(db, [repo])[0]


def pr_out(db: Session, pr: PullRequest) -> PullRequestOut:
    result = PullRequestOut.model_validate(pr)
    run = db.scalar(
        select(ReviewRun).where(ReviewRun.pr_id == pr.id)
        .order_by(ReviewRun.created_at.desc(), ReviewRun.id.desc()).limit(1)
    )
    result.latest_run = RunOut.model_validate(run) if run else None
    result.open_findings = db.scalar(
        select(func.count()).select_from(Finding).where(Finding.pr_id == pr.id, Finding.status == "open")
    )
    return result


def connection_out(connection: Connection) -> ConnectionOut:
    result = ConnectionOut.model_validate(connection)
    credentials = json.loads(unseal(connection.secret))
    result.has_key, result.header_names = (
        bool(credentials.get("api_key")),
        list(credentials.get("extra_headers", {})),
    )
    return result
