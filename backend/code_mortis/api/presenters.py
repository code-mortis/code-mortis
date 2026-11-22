import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from code_mortis.core.security import unseal
from code_mortis.models import (
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
    StoredReviewRule,
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


def repo_out(db: Session, repo: Repository, *, review_rules: list[StoredReviewRule] | None = None) -> RepositoryOut:
    from code_mortis.models import CodeGraphRepository

    result = RepositoryOut.model_validate(repo)
    result.review_rules = rules.by_repository(db, [repo.id])[repo.id] if review_rules is None else review_rules
    graph = db.get(CodeGraphRepository, repo.id)
    result.code_graph_enabled = bool(graph and graph.enabled)
    result.open_prs, result.has_running_review, connection_kind = db.execute(
        select(
            select(func.count())
            .select_from(PullRequest)
            .where(PullRequest.repository_id == repo.id, PullRequest.state == "open", PullRequest.ignored.is_(False))
            .scalar_subquery(),
            select(ReviewRun.id)
            .join(PullRequest, ReviewRun.pr_id == PullRequest.id)
            .where(PullRequest.repository_id == repo.id, PullRequest.ignored.is_(False), ReviewRun.status == "running")
            .exists(),
            select(Connection.kind).where(Connection.id == repo.connection_id).scalar_subquery(),
        )
    ).one()
    result.connection_requires_setup = bool(repo.connection_id and connection_kind != "api")
    return result


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
