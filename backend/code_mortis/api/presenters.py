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
from code_mortis.schemas.common import (
    ConnectionOut,
    PullRequestOut,
    RepositoryOut,
    RunOut,
)


def repo_out(db: Session, repo: Repository) -> RepositoryOut:
    result = RepositoryOut.model_validate(repo)
    result.open_prs, result.has_running_review = db.execute(
        select(
            select(func.count())
            .select_from(PullRequest)
            .where(PullRequest.repository_id == repo.id, PullRequest.state == "open", PullRequest.ignored.is_(False))
            .scalar_subquery(),
            select(ReviewRun.id)
            .join(PullRequest, ReviewRun.pr_id == PullRequest.id)
            .where(PullRequest.repository_id == repo.id, PullRequest.ignored.is_(False), ReviewRun.status == "running")
            .exists(),
        )
    ).one()
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
