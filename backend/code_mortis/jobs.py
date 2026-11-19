import json
import logging
from datetime import timedelta
from typing import Any

import sentry_sdk
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown, worker_shutdown
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, case, func, or_, select, update

from code_mortis.core.analysis import fingerprint
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction
from code_mortis.core.errors import ReviewStopped, RunnerBlocked
from code_mortis.core.security import unseal
from code_mortis.core.telemetry import init_sentry
from code_mortis.integrations import github, installations, llm
from code_mortis.models import (
    Connection,
    Finding,
    Installation,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityFinding,
    SecurityScan,
)
from code_mortis.reviews import background, checks, pipeline, verification
from code_mortis.reviews import summary as pr_summary
from code_mortis.reviews.service import (
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

init_sentry("worker")
worker_process_shutdown.connect(github.close_http_client)
worker_shutdown.connect(github.close_http_client)


@worker_process_init.connect
def init_worker_sentry(**kwargs):
    init_sentry("worker")


log = logging.getLogger("code_mortis.jobs")
CODE_GRAPH_KINDS = {"code_graph", "code_graph_cleanup", "code_graph_refresh"}
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
        "dispatch": {"task": "mortis.dispatch", "schedule": 10.0, "options": {"queue": "events"}},
        "reconcile": {"task": "mortis.reconcile", "schedule": 300.0, "options": {"queue": "sync"}},
    },
)


def sync_repositories(installation_id: int):
    with transaction() as db:
        installation = db.scalar(select(Installation).where(Installation.id == installation_id).with_for_update())
        if not installation or installation.archived or installation.uninstalled:
            return
        remote_installation = installations.remote_installation(db, installation_id)
        if remote_installation is None or remote_installation.suspended_at:
            installation.uninstalled = remote_installation is None
            installations.deactivate(db, installation, "GitHub installation access was removed or suspended.")
            return
        if installations.removing(installations.uninstall_job(db, installation_id)):
            return
        installation.active, installation.account = True, remote_installation.account.login
        installation.account_id = remote_installation.account.id
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


def sync_pr(repository_id: int, number: int, automatic: bool = True, sender_type: str | None = None):
    with transaction() as db:
        repo = db.get(Repository, repository_id)
        if not repo or not repo.active or not db.get(Installation, repo.installation_id).active:
            return
        tracked = db.scalar(select(PullRequest.id).where(
            PullRequest.repository_id == repo.id, PullRequest.number == number,
        ))
        if not repo.enabled and not tracked:
            return
        # A PAT-requested sync carries the owner's authority into its
        # automatic descendants: resolve the effective actor from the
        # validated origin (live push check included) instead of promoting
        # to unrestricted system execution. System/webhook syncs keep actor None.
        from code_mortis.api_tokens import provenance as _pat

        actor = None
        if automatic and _pat.current_provenance() is not None:
            try:
                actor, _ = _pat.validate(db, _pat.current_provenance(),
                                         repository_id=repository_id, write=True)
            except _pat.TransientAuthorizationError:
                raise
            except Exception as exc:
                if _pat._is_transient(exc):
                    raise _pat.TransientAuthorizationError("Token authorization is temporarily unavailable.") from exc
                raise ReviewStopped(_pat.REVOKED_MESSAGE) from exc
        token = github.installation_token(db, repo.installation_id)
        data = github.request("GET", f"/repos/{repo.full_name}/pulls/{number}", token)
        if not tracked:
            # Serialize new imports with settings changes after the remote fetch.
            repo = db.scalar(select(Repository).where(Repository.id == repository_id)
                             .with_for_update().execution_options(populate_existing=True))
            if not repo or not repo.active or not repo.enabled:
                return
        bot = data["user"].get("type") == "Bot"
        include_bots = repo.settings.get("include_bot_prs", False)
        if bot and not include_bots and not tracked:
            return
        pr = upsert_pr(db, repo, data)
        reconcile_threads(db, pr, repo, token)
        if pr.summary_comment_id or pr_summary.latest_run(db, pr.id):
            pr_summary.enqueue_sync(db, pr)
        if (
            automatic
            and repo.enabled
            and repo.connection_id
            and repo.settings.get("automatic", True)
            and pr.state == "open"
            and not pr.draft
            and not pr.fork
            and not pr.ignored
            and (data["user"].get("type") == "User" or (bot and include_bots))
            and (sender_type != "Bot" or bot)
        ):
            from code_mortis.core.analysis import new_engine_snapshot
            from code_mortis.core.shared_execution import is_shared

            if not is_shared(db.get(Connection, repo.connection_id)):
                return  # Repository setup exposes the retired selection; keep synchronized PR history.
            try:
                new_engine_snapshot(repo.settings)
            except ValueError:
                return  # Invalid or retired mutable settings never select another engine implicitly.
            create_run(db, pr, repo, "automatic", actor=actor)


def scan_repository(repository_id: int):
    with transaction() as db:
        repo = db.get(Repository, repository_id)
        if not repo or not repo.active or not repo.enabled or not db.get(Installation, repo.installation_id).active:
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
            enqueue_reconciliation(db, "sync_pr", {"repository_id": repo.id, "number": number})
        db.execute(update(Repository).where(Repository.id == repository_id).values(
            last_pr_sync_check_at=func.greatest(Repository.last_pr_sync_check_at, now()),
        ))


def _stored_origin(payload: Any) -> tuple:
    if isinstance(payload, dict):
        return tuple((key, payload[key]) for key in ("pat", "mcp") if key in payload)
    return ()


def _current_origin() -> tuple:
    from code_mortis.mcp import provenance as _mcp

    return _stored_origin(_mcp.bind_payload({}))


def enqueue_reconciliation(db, kind, payload):
    conditions = [Job.kind == kind, Job.status.in_(["queued", "running"])]
    conditions.extend(Job.payload[key].as_integer() == payload[key]
                      for key in ("installation_id", "repository_id", "number") if key in payload)
    if "finding_id" in payload:
        conditions.append(Job.payload["finding_id"].as_string() == payload["finding_id"])
    if kind == "sync_pr":
        conditions.extend([
            Job.payload["sender_type"].as_string().is_(None),
            func.coalesce(Job.payload["automatic"].as_boolean(), True) == payload.get("automatic", True),
        ])
    current = _current_origin()
    for existing in db.scalars(select(Job.payload).where(*conditions).order_by(Job.id)):
        # Dedup only against a candidate carrying the same complete origin, so
        # distinct PAT tokens, distinct MCP grants and system work never
        # swallow each other.
        if _stored_origin(existing) == current:
            return
    enqueue(db, kind, payload)


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
        if not installation or installation.archived or installation.uninstalled:
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
                and (sender.get("type") == "User" or (
                    sender.get("type") == "Bot" and repo.settings.get("include_bot_prs", False)
                ))
            )
            enqueue(db, "sync_pr", {"repository_id": repo.id, "number": pr["number"], "automatic": automatic,
                                     "sender_type": sender.get("type") if isinstance(sender, dict) else None})


def connection_test_matches(job, connection):
    if not connection:
        return False
    identity = job.payload.get("connection_test_fingerprint")
    if identity is None:
        return connection.last_test == "Testing connection…"
    return identity == fingerprint([connection_snapshot(connection), connection.secret])


def execute(kind: str, payload: dict):
    if kind in CODE_GRAPH_KINDS:
        from code_mortis.code_graph import service as graphs

        if kind == "code_graph":
            graphs.perform(payload["snapshot_id"], payload["_job_id"], payload["_attempt"])
        elif kind == "code_graph_refresh":
            graphs.refresh(payload["repository_id"], payload["_job_id"], payload["_attempt"], payload["actor_id"],
                           retry=payload["retry"], commit_sha=payload.get("commit_sha"))
        else:
            graphs.cleanup()
    elif kind == "revoke_api_token_work":
        # Internal revocation cleanup: idempotent, token_id only, never
        # token-authorized and never starts model work or publishes content.
        from code_mortis.api_tokens import provenance as _pat

        with transaction() as db:
            _pat.revoke_work(db, payload.get("token_id"))
    elif kind == "security_scan":
        from code_mortis.security_scans.service import perform

        perform(payload["scan_id"], payload["_claim"])
    elif kind == "security_refresh":
        from code_mortis.security_scans.service import refresh_repository

        refresh_repository(payload["repository_id"])
    elif kind in {"security_issue_publish", "security_issue_sync"}:
        from code_mortis.security_scans import issues as security_issues

        (security_issues.publish if kind == "security_issue_publish" else security_issues.sync)(payload["finding_id"])
    elif kind == "review":
        perform_review(payload["run_id"], payload["_claim"])
    elif kind == "generate_background":
        background.perform(payload["_job_id"], payload["_attempt"])
    elif kind == "publish":
        publish_run(payload["run_id"])
    elif kind == "sync_check":
        checks.sync_check(payload["run_id"])
    elif kind == "sync_summary":
        pr_summary.sync(payload["pr_id"])
    elif kind == "generate_summary":
        pr_summary.perform_retry(payload)
    elif kind in pipeline.KINDS:
        pipeline.perform(payload)
    elif kind == "finalize_review":
        pipeline.finalize(payload["run_id"])
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
        sync_pr(payload["repository_id"], payload["number"],
                automatic=payload.get("automatic", True), sender_type=payload.get("sender_type"))
    elif kind == "test_connection":
        with transaction() as db:
            job = None
            if payload.get("_job_id"):
                from code_mortis.core.analysis import job_live

                job = db.scalar(select(Job).where(Job.id == payload["_job_id"]).with_for_update())
                if not job_live(job, payload["_attempt"], now()):
                    return
            connection = db.get(Connection, payload["connection_id"])
            if not connection:
                return
            from code_mortis.core.shared_execution import require_shared

            require_shared(connection)
            snapshot, secret = connection_snapshot(connection), connection.secret
            credentials = json.loads(unseal(secret))
            if job:
                job.payload = {**job.payload, "connection_test_fingerprint": fingerprint([snapshot, secret])}
        def stopped():
            with transaction() as db:
                current = db.get(Connection, snapshot["id"])
                valid = bool(current and current.secret == secret and connection_snapshot(current) == snapshot)
                if payload.get("_job_id"):
                    from code_mortis.core.analysis import job_live

                    valid = valid and job_live(db.get(Job, payload["_job_id"]), payload["_attempt"], now())
            return not valid

        result = llm.test_connection(snapshot, credentials, stopped=stopped)
        with transaction() as db:
            if payload.get("_job_id"):
                from code_mortis.core.analysis import job_live

                job = db.scalar(select(Job).where(Job.id == payload["_job_id"]).with_for_update())
                if not job_live(job, payload["_attempt"], now()):
                    return
            connection = db.scalar(select(Connection).where(Connection.id == snapshot["id"]).with_for_update())
            if connection and connection.secret == secret and connection_snapshot(connection) == snapshot:
                connection.last_test, connection.tested_at = result, now()
    else:
        raise ValueError("This background operation is not supported by the running worker. Ask the administrator to check that the server and workers run the same version.")


@celery.task(name="mortis.execute")
def execute_job(job_id: str):
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.available_at.timestamp() > now().timestamp():
            return
        from code_mortis.core.shared_execution import retired_job, settle_job

        if retired_job(db, job):
            settle_job(db, job)
            return
        from code_mortis.core import engine_retirement

        if engine_retirement.retired_job(db, job):
            engine_retirement.settle_job(db, job)
            return
        delivered_queue = (execute_job.request.delivery_info or {}).get("routing_key")
        graph_delivery = job.kind in CODE_GRAPH_KINDS or delivered_queue == "code-graph"
        if graph_delivery and delivered_queue and delivered_queue != job_queue(job.kind, job.payload):
            # Old broker deliveries cannot run graph work on the review worker after an upgrade.
            job.dispatched_at = None
            return
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        if job.kind != "revoke_api_token_work":
            try:
                mcp_denied = _mcp.job_revoked(db, job, repository_id=_mcp.repository_id_for_job(db, job))
                pat_denied = _pat.job_revoked(db, job, repository_id=_pat.repository_id_for_job(db, job))
            except _pat.TransientAuthorizationError:
                # Temporary outage, not revocation: leave queued for redispatch.
                return
        else:
            mcp_denied = pat_denied = False
        if mcp_denied or pat_denied:
            # Narrow terminal-only exemption: a revoked sync_check may still
            # post its cancelled conclusion to an existing check run.
            if pat_denied and not mcp_denied and _pat.closeout_allowed(db, job):
                pass
            else:
                job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if job and job.status == "queued":
                    job.status, job.error, job.lease_until, job.dispatched_at = (
                        "failed",
                        _pat.REVOKED_MESSAGE if pat_denied else _mcp.REVOKED_MESSAGE,
                        None,
                        None,
                    )
                    job.available_at = now()
                    _mcp.cancel_origin(db, job)
                    _pat.cancel_origin(db, job)
                    if job.kind == "generate_summary":
                        pr_summary.fail_retry(db, job, job.error)
                return
        if job.kind in {"security_issue_publish", "security_issue_sync"}:
            db.scalar(select(SecurityFinding).where(SecurityFinding.id == job.payload["finding_id"]).with_for_update())
            if db.scalar(select(Job.id).where(Job.kind.in_(["security_issue_publish", "security_issue_sync"]),
                Job.status == "running", Job.payload["finding_id"].as_string() == job.payload["finding_id"]).limit(1)):
                job.available_at, job.dispatched_at = now() + timedelta(seconds=30), None
                return
        if job.kind == "sync_summary":
            # Serialize claims, then release the PR lock before GitHub calls.
            db.scalar(select(PullRequest).where(PullRequest.id == job.payload["pr_id"]).with_for_update())
            if db.scalar(select(Job.id).where(
                Job.kind == "sync_summary", Job.status == "running",
                Job.payload["pr_id"].as_string() == job.payload["pr_id"],
            ).limit(1)):
                job.available_at, job.dispatched_at = now() + timedelta(seconds=10), None
                return
        lease_seconds = ((job.payload.get("limits", {}).get("timeout_seconds", 900) or 300) + 60
                         if job.kind == "generate_background" else 7200)
        if job.kind == "security_scan":
            lease_seconds = 360
        if job.kind in CODE_GRAPH_KINDS:
            lease_seconds = 600
        job.status, job.lease_until = "running", now() + timedelta(seconds=lease_seconds)
        job.attempts += 1
        kind, payload, attempt = job.kind, job.payload, job.attempts
    from code_mortis.api_tokens import provenance as _pat
    from code_mortis.mcp import provenance as _mcp

    _mcp_token = _pat_token = None
    try:
        _mcp_token = _mcp.execution_context.set(_mcp.provenance_from_payload(payload))
        # Bind the effective stored origin (payload first, then run/scan/intent
        # fallback) so children of retried or summary work keep the origin even
        # when the job payload itself carries none.
        with transaction() as fresh:
            _pat_token = _pat.execution_context.set(_pat.job_provenance(fresh, job))
        execution = {**payload, "_claim": f"{job_id}:{attempt}", "_job_id": job_id, "_attempt": attempt}
        execute(kind, execution if kind in {"review", "security_scan", "code_graph", "code_graph_refresh", "generate_background", "generate_summary", "test_connection", "uninstall_installation", *pipeline.KINDS} else payload)
    except _pat.TransientAuthorizationError:
        # Temporary authorization outage, not revocation: requeue for retry
        # instead of failing the work as revoked.
        with transaction() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.attempts != attempt or job.status != "running":
                return
            job.status, job.lease_until, job.dispatched_at = "queued", None, None
            job.available_at = now() + timedelta(seconds=30)
        return
    except RunnerBlocked as exc:
        with transaction() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.attempts != attempt or job.status != "running":
                return
            waiting = exc.reason in {"busy", "unavailable"}
            if kind == "generate_background":
                waiting = exc.reason == "busy" and now().timestamp() < payload["expires_at"]
            if kind == "generate_summary":
                waiting = exc.reason == "busy"
            job.status, job.lease_until, job.dispatched_at = "queued" if waiting else "failed", None, None
            job.available_at, job.error = now() + timedelta(seconds=30), str(exc)
            job.attempts -= 1
            if kind == "review":
                run = db.get(ReviewRun, payload["run_id"])
                if run and not run.cancel_requested:
                    run.status, run.blocked_reason = "queued" if waiting else "blocked", exc.reason
                    run.error = str(exc)
            elif kind == "security_scan":
                scan = db.get(SecurityScan, payload["scan_id"])
                if scan and not scan.cancel_requested:
                    scan.status, scan.error = "queued" if waiting else "blocked", str(exc)
            elif kind == "generate_summary" and not waiting:
                pr_summary.fail_retry(db, job, str(exc))
            elif kind in pipeline.KINDS:
                run = db.scalar(select(ReviewRun).where(ReviewRun.id == payload["run_id"]).with_for_update())
                if waiting:
                    pipeline.update(run, pipeline.KINDS[kind], "waiting", error=str(exc))
                else:
                    pipeline.fail_stage(db, run, pipeline.KINDS[kind], str(exc))
        return
    except Exception as exc:
        tags = {"job_id": job_id, "job_kind": kind, "job_attempt": attempt}
        if isinstance(exc, github.GitHubError):
            tags["github_status"] = exc.status
        sentry_sdk.capture_exception(exc, tags=tags)
        # Provider exception bodies may contain credentials. Only explicitly safe messages are persisted.
        message = (
            str(exc)
            if isinstance(exc, (github.GitHubError, ValueError, TimeoutError, ReviewStopped, llm.InferenceError))
            else "The server could not finish this operation. Ask the administrator to check the worker logs, then retry."
        )
        retryable = isinstance(exc, github.GitHubError) and exc.status in {403, 429, 500, 502, 503, 504}
        retry = retryable and attempt < 3 and kind not in {"code_graph", "generate_summary", *pipeline.KINDS}
        with transaction() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.attempts != attempt or job.status != "running":
                return
            if kind == "generate_background" and job.payload.get("inference_started"):
                retry = False
            if kind == "generate_background" and isinstance(exc, background.GenerationError):
                job.payload = {**job.payload, "diagnostics": exc.diagnostics}
            job.status, job.error, job.lease_until = ("queued" if retry else "failed"), message[:2000], None
            delay = max(min(30 * 2**attempt, 300), getattr(exc, "retry_after", 0) or 0)
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
            elif kind == "security_scan":
                from code_mortis.security_scans.service import interrupt_work, report_data

                scan = db.get(SecurityScan, payload["scan_id"])
                if scan and scan.status in {"queued", "running", "blocked"}:
                    scan.status, scan.error, scan.finished_at = "failed", message[:2000], now()
                    interrupt_work(scan)
                    scan.report = report_data(db, scan, [scan.error])
            elif kind == "sync_check":
                run = db.get(ReviewRun, payload["run_id"])
                if run:
                    run.check_error, run.check_pending = message[:2000], True
            elif kind == "sync_summary":
                pr = db.get(PullRequest, payload["pr_id"])
                if pr:
                    pr.summary_error = message[:2000]
            elif kind == "generate_summary":
                pr_summary.fail_retry(db, job, message[:2000])
            elif kind in pipeline.KINDS:
                run = db.scalar(select(ReviewRun).where(ReviewRun.id == payload["run_id"]).with_for_update())
                pipeline.fail_stage(db, run, pipeline.KINDS[kind], message[:2000])
            elif kind == "publish":
                run = db.get(ReviewRun, payload["run_id"])
                if run and not run.cancel_requested:
                    run.publication = "partially_published" if run.review_ids else "failed"
                    run.publication_error = message[:2000]
            elif kind == "resolve":
                verification.sync_failed(db, payload, message[:2000])
            elif kind == "test_connection":
                connection = db.scalar(select(Connection).where(Connection.id == payload["connection_id"]).with_for_update())
                if connection_test_matches(job, connection):
                    connection.last_test, connection.tested_at = message[:2000], now()
        log.warning(
            "job_failed id=%s kind=%s run_id=%s error_type=%s retry=%s",
            job_id, kind, payload.get("run_id"), type(exc).__name__, retry
        )
        return
    else:
        with transaction() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.attempts != attempt or job.status != "running":
                return
            job.status, job.lease_until, job.error = "done", None, None
            # Signed webhook payloads are only needed until they have been processed.
            if kind == "webhook":
                job.payload = {"event": payload["event"]}
        log.info("job_completed id=%s kind=%s run_id=%s", job_id, kind, payload.get("run_id"))
    finally:
        if _pat_token is not None:
            _pat.execution_context.reset(_pat_token)
        if _mcp_token is not None:
            _mcp.execution_context.reset(_mcp_token)


def job_queue(kind, payload=None):
    if kind in CODE_GRAPH_KINDS:
        return "code-graph"
    if kind == "security_scan":
        return "security"
    if kind in {"security_refresh", "security_issue_publish", "security_issue_sync"}:
        return "sync"
    if kind == "verify_fixes":
        return "verification"
    if kind in {"summarize_review", "generate_summary"}:
        return "summaries"
    if kind in {"review", "assess_candidates", "test_connection", "generate_background"}:
        return "reviews"
    if kind in {"sync_repositories", "scan_repository"} or (kind == "sync_pr" and not (payload or {}).get("sender_type")):
        return "sync"
    return "events"


@celery.task(name="mortis.dispatch")
def dispatch():
    # A slow historical sync must not fill the broker with copies of unclaimed jobs.
    try:
        with Redis.from_url(settings().redis_url, socket_timeout=2, socket_connect_timeout=2) as broker:
            sync_capacity = max(0, 100 - broker.llen("sync"))
    except RedisError as error:
        sync_capacity = 0
        log.warning("sync_queue_length_failed error_type=%s", type(error).__name__)
    with transaction() as db:
        # An expired lease means the process disappeared; completed external writes are reconciled on retry.
        for job in db.scalars(
            select(Job)
            .where(Job.status == "running", Job.lease_until < now())
            .with_for_update(skip_locked=True)
        ):
            from code_mortis.core.shared_execution import retired_job, settle_job

            if retired_job(db, job):
                settle_job(db, job)
                continue
            from code_mortis.core import engine_retirement

            if engine_retirement.retired_job(db, job):
                engine_retirement.settle_job(db, job)
                continue
            if job.kind == "security_scan":
                from code_mortis.security_scans.service import interrupt_work, report_data

                job.status, job.error, job.lease_until = "failed", "Security worker stopped. Retry explicitly; inference usage may have been consumed.", None
                scan = db.get(SecurityScan, job.payload["scan_id"])
                if scan and scan.status in {"queued", "running", "blocked"}:
                    scan.status, scan.error, scan.finished_at = "partial", job.error, now()
                    interrupt_work(scan)
                    scan.report = report_data(db, scan, [job.error])
                continue
            if job.kind in pipeline.KINDS:
                job.status, job.error, job.lease_until = "failed", "The worker stopped during this stage. Retry explicitly to start new inference.", None
                run = db.scalar(select(ReviewRun).where(ReviewRun.id == job.payload["run_id"]).with_for_update())
                pipeline.fail_stage(db, run, pipeline.KINDS[job.kind], job.error)
                continue
            if job.kind == "generate_summary":
                job.status, job.error, job.lease_until = "failed", "Summary generation was interrupted. Retry generation to start a new attempt.", None
                pr_summary.fail_retry(db, job, job.error)
                continue
            if job.kind == "generate_background" and (job.payload.get("background") or job.payload.get("inference_started")):
                job.status = "done" if job.payload.get("background") else "failed"
                job.error = None if job.status == "done" else background.INTERRUPTED
                job.lease_until = None
                continue
            if job.kind == "code_graph":
                from code_mortis.code_graph.service import settle

                settle(db, job, expired=True)
                if job.status in {"done", "failed"}:
                    continue
            if job.attempts < 3:
                job.status, job.dispatched_at = "queued", None
            else:
                job.status, job.error = "failed", "The background worker stopped repeatedly before finishing. Ask the administrator to check the worker logs, then retry the operation."
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
                elif job.kind == "sync_summary":
                    pr = db.get(PullRequest, job.payload["pr_id"])
                    if pr:
                        pr.summary_error = job.error
                elif job.kind == "publish":
                    run = db.get(ReviewRun, job.payload["run_id"])
                    if run:
                        run.publication = "partially_published" if run.review_ids else "failed"
                        run.publication_error = job.error
                elif job.kind == "resolve":
                    verification.sync_failed(db, job.payload, job.error)
                elif job.kind == "test_connection":
                    connection = db.scalar(select(Connection).where(Connection.id == job.payload["connection_id"]).with_for_update())
                    if connection_test_matches(job, connection):
                        connection.last_test, connection.tested_at = job.error, now()
        candidates = list(
            db.scalars(
                select(Job)
                .where(
                    Job.status == "queued",
                    Job.available_at <= now(),
                    or_(Job.dispatched_at.is_(None), Job.dispatched_at < now() - timedelta(seconds=45)),
                )
                .order_by(case(
                    (or_(Job.kind == "webhook", and_(Job.kind == "sync_pr", Job.payload["sender_type"].as_string().is_not(None))), 0),
                    (Job.kind.in_(["sync_repositories", "scan_repository", "sync_pr"]), 2),
                    else_=1,
                ), Job.available_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for job in candidates:
            queue = job_queue(job.kind, job.payload)
            if queue == "sync":
                if not sync_capacity:
                    continue
                sync_capacity -= 1
            if job.kind == "generate_background" and now().timestamp() >= job.payload["expires_at"]:
                job.status, job.error = "failed", "Background generation expired. Generate again to retry."
                continue
            execute_job.apply_async(
                args=[job.id], queue=queue
            )
            job.dispatched_at = now()


@celery.task(name="mortis.reconcile")
def schedule_reconciliation():
    from code_mortis.code_graph.service import refresh_available, refresh_due, schedule_refresh
    from code_mortis.models import CodeGraphRepository

    with transaction() as db:
        identities = list(db.scalars(select(CodeGraphRepository.repository_id).where(
            CodeGraphRepository.enabled.is_(True), CodeGraphRepository.paused.is_(False),
            refresh_available(), refresh_due(),
        ).order_by(CodeGraphRepository.checked_at.asc().nulls_first(), CodeGraphRepository.repository_id).limit(16)))
    # Keep configuration locks out of the reconciliation transaction's job locks.
    for identity in identities:
        with transaction() as db:
            schedule_refresh(db, identity)

    with transaction() as db:
        if (db.scalar(select(CodeGraphRepository.repository_id).limit(1))
            or db.scalar(select(Job.id).where(Job.kind == "code_graph").limit(1))):
            enqueue_reconciliation(db, "code_graph_cleanup", {})
        from code_mortis.mcp import auth as _mcp_auth

        _mcp_auth.cleanup(db)
        from code_mortis.api_tokens import provenance as _pat

        # Bounded no-network sweep: cancel blocked PAT-origin work whose
        # token has since expired or been revoked. Gates enforce the rest.
        _pat.settle_blocked(db)
        for identity in db.scalars(select(SecurityScan.repository_id).join(Repository)
                                   .where(Repository.active.is_(True)).distinct()):
            enqueue_reconciliation(db, "security_refresh", {"repository_id": identity})
        for finding in db.scalars(select(SecurityFinding).where(
            SecurityFinding.issue["operation_id"].as_string().is_not(None))):
            enqueue_reconciliation(db, "security_issue_sync", {"finding_id": finding.id})
        for run in db.scalars(select(ReviewRun).where(
            ReviewRun.config["pipeline_version"].as_integer() == 2,
            ReviewRun.status.in_(["running", "queued", "cancelled", "superseded"]),
        ).with_for_update()):
            if not run.progress.get("pipeline_finalized"):
                pipeline.enqueue_finalizer(db, run)
        for pr in db.scalars(select(PullRequest).where(PullRequest.summary_error.is_not(None))):
            pr_summary.enqueue_sync(db, pr)
        for run in db.scalars(select(ReviewRun).where(
            ReviewRun.check_enabled.is_(True), ReviewRun.check_pending.is_(True)
        )):
            checks.enqueue_sync(db, run)
        for installation in db.scalars(select(Installation).where(
            Installation.archived.is_(False), Installation.uninstalled.is_(False),
        )):
            enqueue_reconciliation(db, "sync_repositories", {"installation_id": installation.id})
        for repo in db.scalars(
            select(Repository).where(Repository.active.is_(True), Repository.enabled.is_(True))
        ):
            enqueue_reconciliation(db, "scan_repository", {"repository_id": repo.id})
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
            enqueue_reconciliation(
                db, "sync_pr", {"repository_id": pr.repository_id, "number": pr.number, "automatic": False}
            )
