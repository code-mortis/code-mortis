import json
import logging
from datetime import timedelta

from celery import Celery
from sqlalchemy import or_, select

from . import github, ocr, runner_client
from .config import settings
from .db import now, transaction
from .models import (
    Connection,
    ConnectionGrant,
    Finding,
    Installation,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
)
from .reviews import (
    connection_snapshot,
    create_run,
    enqueue,
    perform_review,
    publish_run,
    reconcile_threads,
    synchronize_finding,
    upsert_pr,
)
from .security import unseal

log = logging.getLogger("code_mortis.jobs")
celery = Celery("code_mortis")
celery.conf.update(
    broker_url=settings().redis_url,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 7800},
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "dispatch": {"task": "mortis.dispatch", "schedule": 10.0, "options": {"queue": "sync"}},
        "reconcile": {"task": "mortis.reconcile", "schedule": 300.0, "options": {"queue": "sync"}},
    },
)


def sync_repositories(installation_id: int):
    with transaction() as db:
        installation = db.get(Installation, installation_id)
        if not installation or not installation.active:
            return
        token = github.installation_token(db, installation.id)
        remote = github.pages("/installation/repositories", token, "repositories")
        active_ids = set()
        for data in remote:
            active_ids.add(data["id"])
            repo = db.get(Repository, data["id"])
            if repo is None:
                repo = Repository(
                    id=data["id"],
                    installation_id=installation.id,
                    full_name=data["full_name"],
                    private=data["private"],
                )
                db.add(repo)
            repo.active, repo.full_name, repo.private = True, data["full_name"], data["private"]
            repo.installation_id = installation.id
        for repo in db.scalars(select(Repository).where(Repository.installation_id == installation.id)):
            if repo.id not in active_ids:
                repo.active = False


def sync_pr(repository_id: int, number: int, automatic: bool = True):
    with transaction() as db:
        repo = db.get(Repository, repository_id)
        if not repo or not repo.active or not db.get(Installation, repo.installation_id).active:
            return
        token = github.installation_token(db, repo.installation_id)
        data = github.request("GET", f"/repos/{repo.full_name}/pulls/{number}", token)
        pr = upsert_pr(db, repo, data)
        reconcile_threads(db, pr, repo, token)
        if (
            automatic
            and repo.enabled
            and repo.connection_id
            and repo.settings.get("automatic", True)
            and pr.state == "open"
            and not pr.draft
            and not pr.fork
        ):
            connection = db.get(Connection, repo.connection_id)
            if connection and connection.kind != "api":
                grant = db.get(ConnectionGrant, (connection.id, repo.id))
                if not grant or not grant.automatic or connection.auth_status == "revoked":
                    return
            create_run(db, pr, repo, "automatic")


def scan_repository(repository_id: int):
    with transaction() as db:
        repo = db.get(Repository, repository_id)
        if not repo or not repo.active:
            return
        token = github.installation_token(db, repo.installation_id)
        remote = github.pages(f"/repos/{repo.full_name}/pulls?state=open", token)
        numbers = {item["number"] for item in remote}
        numbers.update(
            db.scalars(
                select(PullRequest.number).where(
                    PullRequest.repository_id == repo.id, PullRequest.state == "open"
                )
            )
        )
        for number in numbers:
            enqueue(db, "sync_pr", {"repository_id": repo.id, "number": number})


def process_webhook(payload: dict):
    event, body = payload["event"], payload["body"]
    installation_id = (body.get("installation") or {}).get("id")
    with transaction() as db:
        installation = db.get(Installation, installation_id) if installation_id else None
        if not installation:
            return
        if event == "installation":
            if body.get("action") in {"deleted", "suspend"}:
                installation.active = False
                for repo in db.scalars(
                    select(Repository).where(Repository.installation_id == installation.id)
                ):
                    repo.active = False
                return
            if body.get("action") == "unsuspend":
                installation.active = True
            enqueue(db, "sync_repositories", {"installation_id": installation.id})
            return
        if not installation.active:
            return
        if event == "installation_repositories":
            enqueue(db, "sync_repositories", {"installation_id": installation.id})
            return
        repository_id = (body.get("repository") or {}).get("id")
        repo = db.get(Repository, repository_id) if repository_id else None
        if not repo or repo.installation_id != installation.id:
            return
        pr = body.get("pull_request")
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
            return
        if event in {"pull_request", "pull_request_review_thread", "pull_request_review_comment"}:
            automatic = event == "pull_request" and body.get("action") in {
                "opened",
                "reopened",
                "ready_for_review",
                "synchronize",
            }
            enqueue(db, "sync_pr", {"repository_id": repo.id, "number": pr["number"], "automatic": automatic})


def execute(kind: str, payload: dict):
    if kind == "review":
        perform_review(payload["run_id"])
    elif kind == "publish":
        publish_run(payload["run_id"])
    elif kind == "resolve":
        synchronize_finding(payload)
    elif kind == "webhook":
        process_webhook(payload)
    elif kind == "sync_repositories":
        sync_repositories(payload["installation_id"])
    elif kind == "scan_repository":
        scan_repository(payload["repository_id"])
    elif kind == "sync_pr":
        sync_pr(**payload)
    elif kind == "test_connection":
        with transaction() as db:
            connection = db.get(Connection, payload["connection_id"])
            if not connection:
                return
            snapshot, credentials = connection_snapshot(connection), json.loads(unseal(connection.secret))
        result = ocr.test_connection(snapshot, credentials)
        with transaction() as db:
            connection = db.get(Connection, payload["connection_id"])
            if connection:
                connection.last_test, connection.tested_at = result, now()
    else:
        raise ValueError("Unknown job kind")


@celery.task(name="mortis.execute")
def execute_job(job_id: str):
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.available_at.timestamp() > now().timestamp():
            return
        job.status, job.lease_until = "running", now() + timedelta(seconds=7200)
        job.attempts += 1
        kind, payload, attempt = job.kind, job.payload, job.attempts
    try:
        execute(kind, payload)
    except runner_client.RunnerBlocked as exc:
        with transaction() as db:
            job = db.get(Job, job_id)
            waiting = exc.reason in {"busy", "unavailable"}
            job.status, job.lease_until, job.dispatched_at = "queued" if waiting else "failed", None, None
            job.available_at, job.error = now() + timedelta(seconds=30), str(exc)
            job.attempts -= 1
            if kind == "review":
                run = db.get(ReviewRun, payload["run_id"])
                if run and not run.cancel_requested:
                    run.status, run.blocked_reason = "queued" if waiting else "blocked", exc.reason
                    run.error = str(exc)
        return
    except Exception as exc:
        # Provider exception bodies may contain credentials. Only explicitly safe messages are persisted.
        message = (
            str(exc)
            if isinstance(exc, (github.GitHubError, ValueError, TimeoutError, ocr.ReviewStopped))
            else "The operation failed unexpectedly. Inspect the worker logs and retry."
        )
        retryable = isinstance(exc, github.GitHubError) and exc.status in {403, 429, 500, 502, 503, 504}
        retry = retryable and attempt < 3
        with transaction() as db:
            job = db.get(Job, job_id)
            job.status, job.error, job.lease_until = ("queued" if retry else "failed"), message[:2000], None
            delay = max(min(30 * 2**attempt, 300), getattr(exc, "retry_after", 0))
            job.available_at, job.dispatched_at = now() + timedelta(seconds=delay), None
            if kind == "review":
                run = db.get(ReviewRun, payload["run_id"])
                if run and run.status in {"queued", "running"}:
                    run.error = message[:2000]
                    if retry:
                        run.status = "queued"
                    else:
                        run.status, run.publication, run.finished_at = "failed", "not_applicable", now()
            elif kind == "publish":
                run = db.get(ReviewRun, payload["run_id"])
                run.publication = "partially_published" if run.review_ids else "failed"
                run.publication_error = message[:2000]
            elif kind == "resolve":
                finding = db.get(Finding, payload["finding_id"])
                if finding and finding.sync_version == payload["version"]:
                    finding.sync_error = message[:2000]
            elif kind == "test_connection":
                connection = db.get(Connection, payload["connection_id"])
                if connection:
                    connection.last_test, connection.tested_at = message[:2000], now()
        log.warning(
            "job_failed id=%s kind=%s error_type=%s retry=%s", job_id, kind, type(exc).__name__, retry
        )
        return
    with transaction() as db:
        job = db.get(Job, job_id)
        job.status, job.lease_until, job.error = "done", None, None
        # Signed webhook payloads are only needed until they have been processed.
        if kind == "webhook":
            job.payload = {"event": payload["event"]}
    log.info("job_completed id=%s kind=%s", job_id, kind)


@celery.task(name="mortis.dispatch")
def dispatch():
    with transaction() as db:
        # An expired lease means the process disappeared; completed external writes are reconciled on retry.
        for job in db.scalars(
            select(Job)
            .where(Job.status == "running", Job.lease_until < now())
            .with_for_update(skip_locked=True)
        ):
            if job.attempts < 3:
                job.status, job.dispatched_at = "queued", None
            else:
                job.status, job.error = "failed", "Worker stopped repeatedly. Retry the operation manually."
                if job.kind == "review":
                    run = db.get(ReviewRun, job.payload["run_id"])
                    if run and run.status in {"running", "queued"}:
                        run.status, run.publication, run.error, run.finished_at = (
                            "failed",
                            "not_applicable",
                            job.error,
                            now(),
                        )
                elif job.kind == "publish":
                    run = db.get(ReviewRun, job.payload["run_id"])
                    if run:
                        run.publication = "partially_published" if run.review_ids else "failed"
                        run.publication_error = job.error
                elif job.kind == "resolve":
                    finding = db.get(Finding, job.payload["finding_id"])
                    if finding and finding.sync_version == job.payload["version"]:
                        finding.sync_error = job.error
                elif job.kind == "test_connection":
                    connection = db.get(Connection, job.payload["connection_id"])
                    if connection:
                        connection.last_test, connection.tested_at = job.error, now()
        candidates = list(
            db.scalars(
                select(Job)
                .where(
                    Job.status == "queued",
                    Job.available_at <= now(),
                    or_(Job.dispatched_at.is_(None), Job.dispatched_at < now() - timedelta(seconds=45)),
                )
                .order_by(Job.available_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for job in candidates:
            execute_job.apply_async(
                args=[job.id], queue="reviews" if job.kind in {"review", "test_connection"} else "sync"
            )
            job.dispatched_at = now()


@celery.task(name="mortis.reconcile")
def schedule_reconciliation():
    with transaction() as db:
        for installation in db.scalars(select(Installation).where(Installation.active.is_(True))):
            enqueue(db, "sync_repositories", {"installation_id": installation.id})
        for repo in db.scalars(
            select(Repository).where(Repository.active.is_(True), Repository.enabled.is_(True))
        ):
            enqueue(db, "scan_repository", {"repository_id": repo.id})
        # Closed PRs also retain resolvable review threads.
        for pr in db.scalars(select(PullRequest).where(PullRequest.state != "open")):
            enqueue(
                db, "sync_pr", {"repository_id": pr.repository_id, "number": pr.number, "automatic": False}
            )
