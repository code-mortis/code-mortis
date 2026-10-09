import json
import logging
from datetime import timedelta

from celery import Celery
from sqlalchemy import or_, select

from . import background, checks, github, installations, ocr, runner_client, verification
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
    complete_saved_review,
    connection_snapshot,
    create_run,
    enqueue,
    perform_review,
    publish_run,
    reconcile_threads,
    review_anyway,
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
        installation = db.scalar(select(Installation).where(Installation.id == installation_id).with_for_update())
        if not installation:
            return
        remote_installation = installations.remote_installation(db, installation_id)
        if remote_installation is None or remote_installation.suspended_at:
            installations.deactivate(db, installation, "GitHub installation access was removed or suspended.")
            return
        if installations.removing(installations.uninstall_job(db, installation_id)):
            return
        installation.active, installation.account = True, remote_installation.account.login
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
        installations.deactivate_repositories(db, db.scalars(select(Repository).where(
            Repository.installation_id == installation.id, Repository.id.not_in(active_ids),
        )), "GitHub App access to this repository was removed.")


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
            and data["user"].get("type") == "User"
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
        if not repo or not repo.active or not db.get(Installation, repo.installation_id).active:
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
    with transaction() as db:
        if event == "issue_comment":
            review_anyway(db, body, payload.get("delivery", ""))
            return
        installation_id = (body.get("installation") or {}).get("id")
        if type(installation_id) is not int or installation_id < 1:
            return
        installation = db.get(Installation, installation_id) if installation_id else None
        if not installation:
            return
        if event == "installation":
            # Read current GitHub state in the sync job instead of replaying stale event state.
            enqueue(db, "sync_repositories", {"installation_id": installation.id})
            if body.get("action") in {"new_permissions_accepted", "unsuspend"}:
                for run in db.scalars(select(ReviewRun).join(PullRequest).join(Repository).where(
                    Repository.installation_id == installation.id,
                    ReviewRun.check_enabled.is_(True), ReviewRun.check_pending.is_(True),
                )):
                    checks.enqueue_sync(db, run)
            return
        if event == "installation_repositories":
            enqueue(db, "sync_repositories", {"installation_id": installation.id})
            return
        if not installation.active:
            return
        repository_id = (body.get("repository") or {}).get("id")
        repo = db.get(Repository, repository_id) if repository_id else None
        if not repo or repo.installation_id != installation.id:
            return
        if event == "check_run":
            if body.get("action") == "rerequested":
                checks.rerequest(db, body, payload.get("delivery", ""))
            return
        pr = body.get("pull_request")
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
            return
        if event in {"pull_request", "pull_request_review_thread", "pull_request_review_comment"}:
            sender = body.get("sender")
            automatic = (
                event == "pull_request"
                and body.get("action") in {"opened", "reopened", "ready_for_review", "synchronize"}
                and isinstance(sender, dict)
                and sender.get("type") == "User"
            )
            enqueue(db, "sync_pr", {"repository_id": repo.id, "number": pr["number"], "automatic": automatic})


def execute(kind: str, payload: dict):
    if kind == "review":
        perform_review(payload["run_id"], payload.get("_claim"))
    elif kind == "generate_background":
        background.perform(payload["_job_id"], payload["_attempt"])
    elif kind == "publish":
        publish_run(payload["run_id"])
    elif kind == "sync_check":
        checks.sync_check(payload["run_id"])
    elif kind == "resolve":
        synchronize_finding(payload)
    elif kind == "webhook":
        process_webhook(payload)
    elif kind == "sync_repositories":
        sync_repositories(payload["installation_id"])
    elif kind == "uninstall_installation":
        installations.uninstall(payload)
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
        lease_seconds = ((job.payload.get("limits", {}).get("timeout_seconds", 900) or 300) + 60
                         if job.kind == "generate_background" else 7200)
        job.status, job.lease_until = "running", now() + timedelta(seconds=lease_seconds)
        job.attempts += 1
        kind, payload, attempt = job.kind, job.payload, job.attempts
    try:
        execution = {**payload, "_claim": f"{job_id}:{attempt}", "_job_id": job_id, "_attempt": attempt}
        execute(kind, execution if kind in {"review", "generate_background", "uninstall_installation"} else payload)
    except runner_client.RunnerBlocked as exc:
        with transaction() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.attempts != attempt or job.status != "running":
                return
            waiting = exc.reason in {"busy", "unavailable"}
            if kind == "generate_background":
                waiting = exc.reason == "busy" and now().timestamp() < payload["expires_at"]
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
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.attempts != attempt or job.status != "running":
                return
            if kind == "generate_background" and job.payload.get("inference_started"):
                retry = False
            if kind == "generate_background" and isinstance(exc, background.GenerationError):
                job.payload = {**job.payload, "diagnostics": exc.diagnostics}
            job.status, job.error, job.lease_until = ("queued" if retry else "failed"), message[:2000], None
            delay = max(min(30 * 2**attempt, 300), getattr(exc, "retry_after", 0))
            job.available_at, job.dispatched_at = now() + timedelta(seconds=delay), None
            if kind == "review":
                run = db.scalar(select(ReviewRun).where(ReviewRun.id == payload["run_id"]).with_for_update())
                if run and run.status in {"queued", "running"}:
                    run.error = message[:2000]
                    if retry:
                        run.status = "queued"
                    elif not complete_saved_review(db, run, message[:2000]):
                        run.status, run.publication, run.finished_at = "failed", "not_applicable", now()
                        run.failure_reason = "timeout" if isinstance(exc, TimeoutError) else "execution"
            elif kind == "sync_check":
                run = db.get(ReviewRun, payload["run_id"])
                if run:
                    run.check_error, run.check_pending = message[:2000], True
            elif kind == "publish":
                run = db.get(ReviewRun, payload["run_id"])
                if run and not run.cancel_requested:
                    run.publication = "partially_published" if run.review_ids else "failed"
                    run.publication_error = message[:2000]
            elif kind == "resolve":
                verification.sync_failed(db, payload, message[:2000])
            elif kind == "test_connection":
                connection = db.get(Connection, payload["connection_id"])
                if connection:
                    connection.last_test, connection.tested_at = message[:2000], now()
        log.warning(
            "job_failed id=%s kind=%s run_id=%s error_type=%s retry=%s",
            job_id, kind, payload.get("run_id"), type(exc).__name__, retry
        )
        return
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.attempts != attempt or job.status != "running":
            return
        job.status, job.lease_until, job.error = "done", None, None
        # Signed webhook payloads are only needed until they have been processed.
        if kind == "webhook":
            job.payload = {"event": payload["event"]}
    log.info("job_completed id=%s kind=%s run_id=%s", job_id, kind, payload.get("run_id"))


@celery.task(name="mortis.dispatch")
def dispatch():
    with transaction() as db:
        # An expired lease means the process disappeared; completed external writes are reconciled on retry.
        for job in db.scalars(
            select(Job)
            .where(Job.status == "running", Job.lease_until < now())
            .with_for_update(skip_locked=True)
        ):
            if job.kind == "generate_background" and (job.payload.get("background") or job.payload.get("inference_started")):
                job.status = "done" if job.payload.get("background") else "failed"
                job.error = None if job.status == "done" else background.INTERRUPTED
                job.lease_until = None
                continue
            if job.attempts < 3:
                job.status, job.dispatched_at = "queued", None
            else:
                job.status, job.error = "failed", "Worker stopped repeatedly. Retry the operation manually."
                if job.kind == "review":
                    run = db.scalar(select(ReviewRun).where(ReviewRun.id == job.payload["run_id"]).with_for_update())
                    if run and run.status in {"running", "queued"}:
                        if not complete_saved_review(db, run, job.error):
                            run.status, run.publication, run.error, run.finished_at = (
                                "failed", "not_applicable", job.error, now(),
                            )
                            run.failure_reason = "worker_stopped"
                elif job.kind == "sync_check":
                    run = db.get(ReviewRun, job.payload["run_id"])
                    if run:
                        run.check_error, run.check_pending = job.error, True
                elif job.kind == "publish":
                    run = db.get(ReviewRun, job.payload["run_id"])
                    if run:
                        run.publication = "partially_published" if run.review_ids else "failed"
                        run.publication_error = job.error
                elif job.kind == "resolve":
                    verification.sync_failed(db, job.payload, job.error)
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
            if job.kind == "generate_background" and now().timestamp() >= job.payload["expires_at"]:
                job.status, job.error = "failed", "Background generation expired. Generate again to retry."
                continue
            execute_job.apply_async(
                args=[job.id], queue="reviews" if job.kind in {"review", "test_connection", "generate_background"} else "sync"
            )
            job.dispatched_at = now()


@celery.task(name="mortis.reconcile")
def schedule_reconciliation():
    with transaction() as db:
        for run in db.scalars(select(ReviewRun).where(
            ReviewRun.check_enabled.is_(True), ReviewRun.check_pending.is_(True)
        )):
            checks.enqueue_sync(db, run)
        for installation in db.scalars(select(Installation)):
            enqueue(db, "sync_repositories", {"installation_id": installation.id})
        for repo in db.scalars(
            select(Repository).where(Repository.active.is_(True), Repository.enabled.is_(True))
        ):
            enqueue(db, "scan_repository", {"repository_id": repo.id})
        for finding in db.scalars(select(Finding).where(Finding.desired_status.is_not(None))):
            if finding.sync_intent and not db.scalar(select(Job.id).where(
                Job.kind == "resolve", Job.status.in_(["queued", "running"]),
                Job.payload["finding_id"].as_string() == finding.id,
            ).limit(1)) and not finding.sync_error:
                enqueue(db, "resolve", finding.sync_intent)
        # Closed PRs retain review threads; legacy PRs need their creation date filled in.
        for pr in db.scalars(select(PullRequest).where(or_(
            PullRequest.state != "open", PullRequest.created_at.is_(None),
        ))):
            enqueue(
                db, "sync_pr", {"repository_id": pr.repository_id, "number": pr.number, "automatic": False}
            )
