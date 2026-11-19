import json
import re
from datetime import datetime, timedelta
from difflib import unified_diff
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from code_mortis.core.analysis import (
    EXECUTION_LIMITS,
    engine_snapshot,
    require_available_engine,
    require_execution_engine,
)
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction, uid
from code_mortis.core.errors import ReviewStopped, RunnerBlocked
from code_mortis.core.review_results import fingerprint, report_tokens
from code_mortis.core.security import digest, unseal
from code_mortis.core.shared_execution import (
    cancel_retired_review,
    is_shared,
    require_shared,
)
from code_mortis.integrations import github
from code_mortis.models import (
    Activity,
    Connection,
    Finding,
    FindingVerification,
    Installation,
    Job,
    Membership,
    Occurrence,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRun,
    User,
    WebhookDelivery,
)
from code_mortis.reviews import assessment, graph_context, mortis, pipeline, planning, verification
from code_mortis.reviews import summary as pr_summary
from code_mortis.schemas.common import RepoSettings

ACTIVE = {"queued", "running", "blocked"}
CODE_LANGUAGES = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "jsx",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".html": "html", ".htm": "html", ".css": "css", ".sql": "sql",
    ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
    ".go": "go", ".rs": "rust", ".dockerfile": "dockerfile",
}


def enqueue(db: Session, kind: str, payload: dict) -> Job:
    from code_mortis.mcp import provenance as mcp_provenance

    job = Job(id=uid(), kind=kind, payload=mcp_provenance.bind_payload(payload))
    db.add(job)
    return job


def stop_review(db: Session, run: ReviewRun, status: str, reason: str, *, publication="not_applicable"):
    run.status = status
    if assessment.assessment_only(run):
        retry = run.progress["assessment_retry"]
        run.progress = {**run.progress, "assessment_retry": {**retry, "finished_at": retry.get("finished_at") or now().isoformat()}}
    else:
        run.publication, run.finished_at = publication, now()
        verification.skip_pending(db, run, reason)
    assessment.withhold_pending(db, run, reason)
    if run.progress.get("assessment_retry") and pipeline.enabled(run):
        pipeline.update(run, "assessment", "cancelled", waiting_for=None)


def cancel_review(db: Session, run: ReviewRun):
    run.cancel_requested = True
    ambiguous = run.publication in {"publishing", "uncertain"} and not run.review_ids
    publication = "partially_published" if run.review_ids or run.publication in {"partially_published", "publishing", "uncertain"} else "not_applicable"
    if ambiguous:
        run.publication_error = "GitHub may have accepted a comment before cancellation. Reconcile the saved publication before sending any comments again."
    stop_review(db, run, "cancelled", "The run was cancelled.",
                publication=publication)


def cancel_pr_reviews(db: Session, pr: PullRequest) -> list[ReviewRun]:
    # Callers hold the PR lock, matching run creation and publication.
    runs = list(db.scalars(select(ReviewRun).where(
        ReviewRun.pr_id == pr.id, ReviewRun.status.in_(ACTIVE),
    ).order_by(ReviewRun.id).with_for_update().execution_options(populate_existing=True)))
    for run in runs:
        cancel_review(db, run)
    return runs


def connection_snapshot(connection: Connection, *, workflow=False) -> dict:
    snapshot = {
        "id": connection.id,
        "name": connection.name,
        "provider": connection.provider,
        "protocol": connection.protocol,
        "model": connection.model,
        "base_url": connection.base_url,
        "options": connection.options,
        "kind": connection.kind,
        "owner_id": connection.owner_id,
        "client_version": {"chatgpt": "0.160.1", "claude_code": "2.1.292"}.get(connection.kind),
    }
    if workflow:
        snapshot["execution_limits"] = EXECUTION_LIMITS
        options = {key: value for key, value in connection.options.items()
                   if key not in {"timeout_sec", "prompt_token_ceiling"}}
        if "extra_body" in options:
            options["extra_body"] = {key: value for key, value in options["extra_body"].items()
                                     if key not in {"max_tokens", "max_completion_tokens", "max_output_tokens"}}
        snapshot["options"] = options
    return snapshot


def create_run(
    db: Session,
    pr: PullRequest,
    repo: Repository,
    trigger: str,
    actor: User | None = None,
    connection_id: str | None = None,
    *,
    ignore_file_limit: bool = False,
    request_key: str | None = None,
) -> ReviewRun:
    # Serialize run creation per PR; automatic delivery and button retries can race.
    db.scalar(select(PullRequest).where(PullRequest.id == pr.id).with_for_update()
              .execution_options(populate_existing=True))
    db.refresh(repo)
    if pr.ignored:
        raise ValueError("Restore this pull request before starting another review.")
    if request_key:
        prior = db.scalar(select(ReviewRun).where(
            ReviewRun.pr_id == pr.id,
            ReviewRun.progress["comment_requests"][request_key].as_boolean().is_(True),
        ))
        if prior:
            return prior
    if not repo.active or not repo.enabled or not (connection_id or repo.connection_id):
        raise ValueError("Enable this repository and select an AI connection first.")
    connection = db.get(Connection, connection_id or repo.connection_id)
    if not connection:
        raise ValueError("The selected connection no longer exists.")
    authorize_connection(db, connection, repo, trigger, actor)
    options = RepoSettings.model_validate(repo.settings).model_dump()
    require_available_engine(options)
    if ignore_file_limit:
        options["max_files"] = None
    config = {"connection": connection_snapshot(connection, workflow=True), "settings": options,
              "assessment_policy": assessment.POLICY_VERSION, "pipeline_version": 2,
              "task_contract": "review-task/v2", "execution_limits": EXECUTION_LIMITS}
    config.update(engine_snapshot(options))
    config["planner_version"] = planning.VERSION
    if graph := graph_context.configuration(db, repo, connection, actor, engine=config):
        config["code_graph"] = graph
    if options["model"]:
        config["connection"]["model"] = options["model"]
    if not config["connection"]["model"].strip():
        raise ValueError("Choose a model before starting reviews.")
    key = digest(json.dumps([pr.id, pr.base_sha, pr.head_sha, config], sort_keys=True))
    if trigger == "automatic":
        # Configuration changes apply to future revisions; same-revision retries must be explicit.
        prior = db.scalar(select(ReviewRun).where(
            ReviewRun.pr_id == pr.id, ReviewRun.base_sha == pr.base_sha, ReviewRun.head_sha == pr.head_sha,
        ).order_by(ReviewRun.created_at.desc()).limit(1))
        if prior:
            return prior
    active = db.scalars(select(ReviewRun).where(
        ReviewRun.pr_id == pr.id, ReviewRun.status.in_(ACTIVE),
    ).with_for_update().execution_options(populate_existing=True)).all()
    for run in active:
        # A planner upgrade alone must not start a duplicate active review.
        config_matches = {**run.config, "planner_version": config["planner_version"]} == config
        if run.head_sha == pr.head_sha and run.base_sha == pr.base_sha and config_matches:
            if request_key:
                run.progress = {**run.progress, "comment_requests": {
                    **run.progress.get("comment_requests", {}), request_key: True,
                }}
            return run
        if run.head_sha == pr.head_sha and run.base_sha == pr.base_sha:
            continue
        run.status = "superseded"
        run.publication = "not_applicable"
        run.cancel_requested = True
        run.finished_at = now()
    run = ReviewRun(
        id=uid(),
        pr_id=pr.id,
        actor_id=actor.id if actor else None,
        trigger=trigger,
        dedup_key=request_key or (key if trigger == "automatic" else uid()),
        base_sha=pr.base_sha,
        head_sha=pr.head_sha,
        config=config,
        connection_id=connection.id,
        owner_id=connection.owner_id,
        engine=config["analysis_engine"] if connection.kind == "api" else connection.kind,
        progress={"comment_requests": {request_key: True}} if request_key else {},
        summary={"status": "pending", "tokens": 0},
        stages={stage: {"status": "pending", "tokens": 0} for stage in pipeline.STAGES},
    )
    from code_mortis.mcp import provenance as mcp_provenance

    run.progress = mcp_provenance.attach_progress(run.progress)
    db.add(run)
    enqueue(db, "review", {"run_id": run.id})
    return run


def file_limit_exceeded(run: ReviewRun) -> bool:
    return (run.report or {}).get("manifest", {}).get("schema_version") == "code-mortis.file-limit/v1"


def can_publish(run: ReviewRun, status: str | None = None) -> bool:
    status = status or run.status
    return status in {"completed", "partial"} or (status == "skipped" and file_limit_exceeded(run))


def review_anyway(db: Session, body: dict, delivery_id: str):
    if body.get("action") != "created":
        return
    comment, issue, sender, repository, installation = (
        body.get(key) for key in ("comment", "issue", "sender", "repository", "installation")
    )
    if not all(isinstance(item, dict) for item in (comment, issue, sender, repository, installation)):
        return
    author = comment.get("user")
    if (
        not isinstance(author, dict)
        or author.get("type") != "User"
        or sender.get("type") != "User"
        or not isinstance(comment.get("body"), str)
        or not isinstance(issue.get("pull_request"), dict)
        or not all(type(value) is int and value > 0 for value in (
            author.get("id"), sender.get("id"), comment.get("id"), issue.get("number"),
            repository.get("id"), installation.get("id"),
        ))
        or author["id"] != sender["id"]
    ):
        return
    slug = github.app_config(db).github_slug.casefold()
    # Keep commands in already-published notices usable.
    if comment["body"].strip().casefold() not in {
        f"@{slug}[bot] review anyway", f"@{slug} review anyway", "@codemortis review anyway",
    }:
        return
    delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update())
    if not delivery or delivery.event != "issue_comment":
        raise ValueError("The GitHub review command could not be verified. Ask the administrator to check the webhook delivery, or start a review in Code Mortis.")
    if delivery.handled_at:
        return
    repo = db.get(Repository, repository["id"])
    installed = db.get(Installation, installation["id"])
    if not repo or not installed or repo.installation_id != installed.id:
        raise ValueError("The review command does not match this repository installation.")
    if not repo.active or not repo.enabled or not installed.active:
        raise ValueError("Enable the repository and installation before requesting another review.")
    actor = db.get(User, author["id"])
    if not actor or not actor.enabled or not db.get(Membership, actor.id):
        raise ValueError("Sign in as an enabled Code Mortis member before using the review command.")
    github.authorize_repo(db, actor, repo.full_name, write=True)
    pr = db.scalar(select(PullRequest).where(
        PullRequest.repository_id == repo.id, PullRequest.number == issue["number"],
    ).with_for_update())
    if not pr:
        raise ValueError("This revision has no review skipped because of the file limit.")
    token = github.installation_token(db, repo.installation_id)
    remote = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
    if remote["state"] != "open":
        raise ValueError("Reopen the pull request before requesting another review.")
    pr = upsert_pr(db, repo, remote)
    source = next((run for run in db.scalars(select(ReviewRun).where(
        ReviewRun.pr_id == pr.id, ReviewRun.base_sha == pr.base_sha, ReviewRun.head_sha == pr.head_sha,
        ReviewRun.status == "skipped",
    ).order_by(ReviewRun.created_at.desc())) if file_limit_exceeded(run)), None)
    if not source:
        raise ValueError("This revision has no review skipped because of the file limit.")
    if not source.connection_id or not db.get(Connection, source.connection_id):
        raise ValueError("The original AI connection is no longer available. Select a connection in Code Mortis.")
    request_key = f"issue-comment:{repo.id}:{comment['id']}"
    run = create_run(db, pr, repo, "manual", actor, source.connection_id,
                     ignore_file_limit=True, request_key=request_key)
    if run.dedup_key == request_key and "file_limit_override" not in run.progress:
        run.progress = {**run.progress, "file_limit_override": {"source_run_id": source.id, "comment_id": comment["id"]}}
    delivery.handled_at = now()


def authorize_connection(db, connection, repo, trigger, actor=None, *, check_repository=True):
    require_shared(connection)


def execution_identity(run):
    retry = run.progress.get("assessment_retry")
    return (retry["actor_id"], "manual") if retry else (run.actor_id, run.trigger)


def target_matches(run, head_sha, base_sha, state):
    return ((head_sha, base_sha) == (run.head_sha, run.base_sha)
            and (state == "open" or (state == "merged" and assessment.assessment_only(run))))


def upsert_pr(db: Session, repo: Repository, data: dict) -> PullRequest:
    created_at = data.get("created_at")
    if created_at is not None:
        created_at = datetime.fromisoformat(created_at)
        if created_at.utcoffset() is None:
            raise ValueError("GitHub PR creation time must include a timezone.")
    pr = db.scalar(
        select(PullRequest).where(PullRequest.repository_id == repo.id, PullRequest.number == data["number"])
    )
    if pr is None:
        pr = PullRequest(id=uid(), repository_id=repo.id, number=data["number"])
        db.add(pr)
    pr.title, pr.author, pr.url = data["title"], data["user"]["login"], data["html_url"]
    pr.state = "merged" if data.get("merged") or data.get("merged_at") else data["state"]
    pr.draft = data.get("draft", False)
    pr.head_sha, pr.base_sha = data["head"]["sha"], data["base"]["sha"]
    pr.head_ref = data["head"]["ref"]
    head_repo = data["head"].get("repo")
    pr.fork = not head_repo or head_repo["id"] != repo.id
    # Unchanged syncs must not reshuffle lists ordered by updated_at.
    if db.is_modified(pr, include_collections=False):
        pr.updated_at = now()
    if created_at is not None:
        pr.created_at = created_at
    db.flush()
    for run in db.scalars(select(ReviewRun).where(ReviewRun.pr_id == pr.id, ReviewRun.status.in_(ACTIVE))
                          .order_by(ReviewRun.id).with_for_update().execution_options(populate_existing=True)):
        if not target_matches(run, pr.head_sha, pr.base_sha, pr.state):
            run.cancel_requested = True
            stop_review(db, run, "superseded", "New commits replaced the code being reviewed. Check the latest review.")
    return pr


def review_stopped(run_id: str, claim: str | None = None, *, stage=None) -> bool:
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        if not run or run.cancel_requested or run.status in {"cancelled", "superseded"} or not is_shared(run.config.get("connection", {})):
            return True
        from code_mortis.api_tokens import provenance as pat_provenance
        from code_mortis.mcp import provenance as mcp_provenance

        if mcp_provenance.run_revoked(db, run) or pat_provenance.run_revoked(db, run, network=False):
            run.cancel_requested = True
            return True
        if claim and (not pipeline.owns(run, stage, claim) if stage else run.progress.get("execution") != claim):
            return True
        if stage and run.stages.get(stage, {}).get("status") != "running":
            return True
        if claim and ":" in claim:
            job_id, attempt = claim.rsplit(":", 1)
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job or job.status != "running" or str(job.attempts) != attempt or job.lease_until <= now():
                return True
            if job.lease_until <= now() + timedelta(seconds=7140):
                job.lease_until = now() + timedelta(seconds=7200)
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if pr.ignored or not repo.active or not repo.enabled or not db.get(Installation, repo.installation_id).active:
            run.cancel_requested = True
            return True
        if not target_matches(run, pr.head_sha, pr.base_sha, pr.state):
            return True
        if run.owner_id:
            owner = db.get(User, run.owner_id)
            if not owner or not owner.enabled or not db.get(Membership, owner.id):
                return True
        return False


def perform_review(run_id: str, claim: str):
    if not isinstance(claim, str) or not re.fullmatch(r"[^:]+:[1-9][0-9]*", claim):
        raise ValueError("Mortis analysis requires a durable job attempt")
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not run or run.status not in ACTIVE or run.cancel_requested:
            return
        from code_mortis.api_tokens import provenance as pat_provenance
        from code_mortis.mcp import provenance as mcp_provenance

        if mcp_provenance.run_revoked(
            db, run, repository_id=db.get(PullRequest, run.pr_id).repository_id
        ) or pat_provenance.run_revoked(
            db, run, repository_id=db.get(PullRequest, run.pr_id).repository_id
        ):
            cancel_review(db, run)
            return
        if not is_shared(run.config.get("connection", {})):
            cancel_retired_review(db, run)
            return
        require_execution_engine(run.config)
        prior_claim = run.progress.get("execution", "")
        if prior_claim and prior_claim != claim and ":" in prior_claim:
            job_id, attempt = prior_claim.rsplit(":", 1)
            prior_job = db.get(Job, job_id)
            if prior_job and prior_job.status == "running" and str(prior_job.attempts) == attempt and prior_job.lease_until > now():
                if run.progress.get("assessment_retry"):
                    raise RunnerBlocked("busy")
                return
        if not pipeline.enabled(run) and run.summary.get("status") == "running":
            pr_summary.interrupt(run)
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        installation = db.get(Installation, repo.installation_id)
        if pr.ignored or not repo.active or not repo.enabled or not installation.active:
            raise ValueError("The repository or GitHub installation has been disabled.")
        actor_id, trigger = execution_identity(run)
        retry = run.progress.get("assessment_retry")
        if actor_id:
            actor = db.get(User, actor_id)
            if not actor or not actor.enabled or not db.get(Membership, actor.id):
                raise ValueError("The user who requested this run no longer has access.")
            github.authorize_repo(db, actor, repo.full_name, write=True)
        token = github.installation_token(db, repo.installation_id)
        current = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
        remote_state = "merged" if current["state"] == "closed" and current.get("merged") is True else current["state"]
        if not target_matches(run, current["head"]["sha"], current["base"]["sha"], remote_state):
            stop_review(db, run, "superseded", "The PR changed before this review could resume.")
            return
        connection = db.get(Connection, run.config["connection"]["id"])
        if not connection:
            raise ValueError("The AI connection is no longer available.")
        if any(
            run.config["connection"][key] != getattr(connection, key)
            for key in ("kind", "owner_id", "provider", "protocol", "base_url")
        ):
            raise ValueError(
                "The AI connection endpoint changed after this run was queued. Start a new review."
            )
        authorize_connection(
            db, connection, repo, trigger, db.get(User, actor_id) if actor_id else None
        )
        if connection.kind != "api":
            # Serialize ownership claims across workers without holding a transaction during inference.
            db.scalar(select(Connection).where(Connection.id == connection.id).with_for_update())
            from code_mortis.security_scans.service import connection_busy

            if connection_busy(db, connection.id, run_id=run.id):
                raise RunnerBlocked("busy")
            other = db.scalar(
                select(ReviewRun.id)
                .where(
                    ReviewRun.connection_id == connection.id,
                    ReviewRun.id != run.id,
                    ReviewRun.status == "running",
                )
                .limit(1)
            )
            if other:
                raise RunnerBlocked("busy")
            if connection.auth_status != "connected":
                raise RunnerBlocked("authentication")
        credentials = json.loads(unseal(connection.secret))
        if not retry:
            verification.prepare(db, run, repo, token)
        elif not retry.get("started_at"):
            retry = {**retry, "started_at": now().isoformat()}
            run.progress = {**run.progress, "assessment_retry": retry}
        run.progress = {**run.progress, "execution": claim}
        run.status, run.started_at, run.error, run.blocked_reason = "running", run.started_at or now(), None, None
        run.failure_reason = None
        checkpoint = run.progress
        saved_review = None
        if run.progress.get("analysis_saved"):
            saved_review = (run.progress["analysis_status"], run.report, [])
        allowance = run.config["settings"]["timeout_seconds"]
        started_at = datetime.fromisoformat(retry["started_at"]) if retry else run.started_at
        remaining = int(allowance - (now() - started_at).total_seconds()) if allowance is not None else None
        if remaining is not None and remaining <= 0:
            message = "The review reached its configured time limit. Increase Timeout or reduce the review scope, then start a new review."
            if pipeline.enabled(run):
                run.failure_reason, run.error = "timeout", message
            verification.skip_pending(db, run, "Fix verification was skipped because the review reached its configured time limit. Increase Timeout or reduce the review scope, then start a new review.")
            if not complete_saved_review(db, run, message):
                run.status, run.finished_at = "failed", now()
                run.publication, run.failure_reason, run.error = "not_applicable", "timeout", message
            return
        run.progress = {**run.progress, "analysis_started": True, "phase": "analysis"}
        parallel_stages = pipeline.enabled(run) and not retry
        if parallel_stages:
            pipeline.start(db, run, claim)
        elif pipeline.enabled(run) and retry:
            pipeline.update(run, "assessment", "running", execution=claim, started_at=now().isoformat())
        from code_mortis.reviews.context import execution_credentials

        credentials = execution_credentials(db, repo.id, credentials, token)
        args = (
            repo.full_name,
            pr.number,
            run.base_sha,
            run.head_sha,
            run.config["connection"],
            credentials,
            {**run.config["settings"], "timeout_seconds": remaining},
            token,
        )
    try:
        with transaction() as db:
            from code_mortis.api_tokens import provenance as _pat
            from code_mortis.mcp import provenance as _mcp

            _run = db.get(ReviewRun, run_id)
            if _run is not None:
                mcp_denied = _mcp.run_revoked(db, _run)
                pat_denied = _pat.run_revoked(db, _run)
                if mcp_denied and (origin := _mcp.provenance_from_run(_run)) is not None:
                    _mcp.revoke_work(db, origin["grant_id"])
                if pat_denied and (origin := _pat.provenance_from_run(_run)) is not None:
                    _pat.revoke_work(db, origin["token_id"])
                if mcp_denied or pat_denied:
                    cancel_review(db, _run)
                    return

        def progress(value):
            with transaction() as db:
                current = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
                if current and not current.cancel_requested and current.progress.get("execution") == claim:
                    current.progress = {**current.progress, **value}

        def after_review(result, repo_path, env, fetch_env):
            save_analysis(run_id, claim, *result)
            if parallel_stages:
                with transaction() as db:
                    current = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
                    if current.progress.get("execution") != claim or current.cancel_requested:
                        return
                    pipeline.update(current, pipeline.analysis_stage(current), result[0], tokens=report_tokens(result[1]))
                    if pipeline.defer_assessment(db, current):
                        return
                    pipeline.update(current, "assessment", "running", started_at=now().isoformat(), execution=claim)
                progress({"phase": "assessment"})
                assessment.perform(run_id, repo_path, env, fetch_env, args[4], credentials, claim)
                return
            if result[0] == "skipped":
                with transaction() as db:
                    current = db.get(ReviewRun, run_id)
                    if current.progress.get("execution") == claim:
                        verification.skip_pending(db, current, result[1].get("message") or "Review skipped.")
            else:
                progress({"phase": "assessment"})
                assessment.perform(run_id, repo_path, env, fetch_env, args[4], credentials, claim)
                if not retry:
                    progress({"phase": "fix verification"})
                    verification.perform(run_id, repo_path, env, fetch_env, args[4], credentials, claim)
            if not assessment.assessment_only(run):
                progress({"phase": "summary"})
                pr_summary.generate(run_id, repo_path, env, args[4], credentials, claim)

        def before_analysis(limited):
            if not parallel_stages:
                return
            with transaction() as db:
                current = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
                if current.progress.get("execution") == claim and not current.cancel_requested:
                    current.progress = {**current.progress, "file_limit_checked": True, "file_limit_exceeded": limited is not None}
                    pipeline.enqueue_stages(db, current)

        status, report, comments = mortis.review(
            *args,
            stopped=lambda: review_stopped(run_id, claim),
            run_id=run_id,
            progress=progress,
            checkpoint=checkpoint,
            saved_review=saved_review,
            after_review=after_review,
            before_analysis=before_analysis,
            graph_options=run.config.get("code_graph"),
            claim=claim,
        )
    except ReviewStopped:
        with transaction() as db:
            run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
            if run.progress.get("execution") != claim:
                return
            if run.status != "superseded":
                run.status = "cancelled" if run.cancel_requested else "superseded"
            stop_review(db, run, run.status, "Review stopped because it was cancelled or replaced by a newer run. Check the latest review.")
        return
    save_analysis(run_id, claim, status, report, comments)
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if run.progress.get("execution") != claim or run.cancel_requested or run.status in {"cancelled", "superseded"}:
            return
        if parallel_stages:
            pipeline.review_done(db, run)
            return
        run.status = status
        if not retry:
            run.finished_at = now()
        complete_saved_review(db, run)


def complete_saved_review(db, run, interruption=None, *, finalizing=False):
    """A failed verification phase must not discard completed new-finding analysis."""
    status = run.progress.get("analysis_status")
    if pipeline.enabled(run) and run.progress.get("assessment_retry"):
        pipeline.assessment_done(db, run, interruption)
    if pipeline.enabled(run) and not finalizing and not run.progress.get("assessment_retry"):
        pipeline.review_done(db, run, interruption)
        for kind, stage in pipeline.KINDS.items():
            if run.stages.get(stage, {}).get("status") == "pending" and not db.scalar(select(Job.id).where(
                Job.kind == kind, Job.payload["run_id"].as_string() == run.id,
            ).limit(1)):
                pipeline.fail_stage(db, run, stage, interruption or "The review could not start.")
        return True
    if not run.progress.get("analysis_saved") or not can_publish(run, status) or run.cancel_requested:
        return False
    if assessment.assessment_only(run):
        assessment.withhold_pending(db, run, interruption or "Assessment did not finish; candidate withheld.")
        run.status = status
        retry = run.progress["assessment_retry"]
        run.progress = {**run.progress, "assessment_retry": {**retry, "finished_at": retry.get("finished_at") or now().isoformat()}}
        return True
    if interruption:
        verification.skip_pending(db, run, interruption)
        pr_summary.interrupt(run)
    assessment.withhold_pending(db, run, interruption or "Assessment did not finish; candidate withheld.")
    run.status = status
    if not run.progress.get("assessment_retry"):
        run.finished_at = now()
    if run.progress.get("assessment_retry") and run.publication in {"published", "no_new_comments"}:
        previous = run.publication_plan or []
        extended = assessed_publication_plan(db, run, append=True)
        if len(extended) > len(previous):
            run.publication_plan, run.publication = extended, "pending"
    if run.publication == "not_applicable":
        run.publication = "pending"
    enqueue(db, "publish", {"run_id": run.id})
    if status == "skipped":
        verification.skip_pending(db, run, run.report.get("message") or "Review skipped.")
        return True
    for finding in db.scalars(select(Finding).join(FindingVerification).where(
        FindingVerification.run_id == run.id, FindingVerification.resolution == "pending"
    )):
        if finding.desired_status == "resolved" and finding.sync_intent:
            enqueue(db, "resolve", finding.sync_intent)
    return True


def save_analysis(run_id, claim, status, report, comments):
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if run.progress.get("execution") != claim or run.cancel_requested or run.progress.get("analysis_saved"):
            return
        run.report = report
        run.progress = {**run.progress, "analysis_saved": True, "analysis_status": status}
        if status in {"failed", "skipped"}:
            run.publication = "not_applicable"
            if status == "failed":
                engine = "Mortis" if run.config.get("analysis_engine") == "mortis" else "The review engine"
                run.error = f"{engine} did not complete the selected review scope. Inspect coverage and retry the review."
        if assessment.enabled(run):
            for position, comment in enumerate(comments):
                db.add(ReviewCandidate(run_id=run.id, position=position, snapshot=comment))
            db.flush()
            assessment.record_summary(db, run, advance_revision=True)
            return
        seen = set()
        for comment in comments:
            key = fingerprint(comment)
            if key in seen:
                continue
            seen.add(key)
            finding = db.scalar(select(Finding).where(Finding.pr_id == run.pr_id, Finding.fingerprint == key))
            if finding is None:
                finding = Finding(
                    id=uid(),
                    pr_id=run.pr_id,
                    fingerprint=key,
                    path=comment["path"],
                    content=comment["content"],
                    severity=comment["severity"],
                    category=comment["category"],
                    start_line=comment["start_line"],
                    end_line=comment["end_line"],
                    existing_code=comment["existing_code"],
                    suggestion=comment["suggestion_code"],
                    last_seen_run_id=run.id,
                )
                db.add(finding)
                db.flush()
                db.add(
                    Activity(finding_id=finding.id, action="detected", actor="Code Mortis", source="review")
                )
            finding.last_seen_run_id, finding.updated_at = run.id, now()
            db.add(
                Occurrence(
                    run_id=run.id,
                    finding_id=finding.id,
                    snapshot=comment,
                    inline_line=comment.get("inline_line"),
                )
            )


def marker(kind: str, identity: str) -> str:
    return f"<!-- code-mortis:{kind}:{identity} -->"


def comment_body(finding_id: str, snapshot: dict, *, with_prompt=False, sanitize=False) -> str:
    severity = snapshot["severity"]
    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}[severity]
    body = f"**{icon} {severity.title()} · {snapshot['category']}**\n\n{snapshot['content']}"
    if with_prompt or sanitize:
        body = f"**{icon} {severity.title()} · {pr_summary.text(snapshot['category'])}**\n\n{pr_summary.text(snapshot['content'])}"
    suffix = "\n\n" + marker("finding", finding_id)
    if with_prompt:
        suffix = "\n\n" + pr_summary.fix_section([snapshot], budget=12000) + suffix
    section = ""
    suggestion = snapshot.get("suggestion_code")
    if suggestion:
        # Suggestions are shown as code, never executable patches or automatic fixes.
        existing = snapshot.get("existing_code") or ""
        code = "\n".join(unified_diff(
            existing.splitlines(), suggestion.splitlines(),
            fromfile="original", tofile="suggested", n=3, lineterm="",
        )) if existing else ""
        language = "diff"
        if not code:
            code = suggestion
            path = PurePosixPath(snapshot["path"])
            language = "dockerfile" if path.name.lower() == "dockerfile" else CODE_LANGUAGES.get(path.suffix.lower(), "text")
        if with_prompt or sanitize:
            code = code.replace("<!-- code-mortis:", "&lt;!-- code-mortis:")
        fence = "`" * max(3, max((len(match) for match in re.findall(r"`+", code)), default=0) + 1)
        section = f"\n\n**Suggested change**\n\n{fence}{language}\n{code}\n{fence}"
        if len(body) + len(section) + len(suffix) > 60000:
            section = "\n\nSuggested change omitted because it exceeds the comment size limit."
    budget = 60000 - len(section) - len(suffix)
    if len(body) > budget:
        notice = "\n\nReview text truncated because it exceeds the comment size limit."
        body = body[:budget - len(notice)] + notice
    return body + section + suffix


def reconcile_threads(db: Session, pr: PullRequest, repo: Repository, token: str):
    bot = github.app_config(db).github_slug + "[bot]"
    threads = github.threads(token, repo.full_name, pr.number)
    by_id = {finding.id: finding for finding in db.scalars(
        select(Finding).where(Finding.pr_id == pr.id).with_for_update().execution_options(populate_existing=True)
    )}
    for thread in threads:
        comments = thread.get("comments", {}).get("nodes", [])
        if not comments:
            continue
        comment = comments[0]
        if (comment.get("author") or {}).get("login") != bot:
            continue
        match = re.search(r"<!-- code-mortis:finding:([0-9a-f-]{36}) -->", comment["body"])
        finding = by_id.get(match[1]) if match else None
        if not finding:
            continue
        finding.thread_id, finding.comment_id, finding.github_url = (
            thread["id"],
            comment["databaseId"],
            comment["url"],
        )
        if finding.desired_status:
            continue
        if verification.recover_reopening(db, finding, thread):
            continue
        status = (
            ("dismissed" if finding.status == "dismissed" else "resolved") if thread["isResolved"] else "open"
        )
        if status != finding.status:
            verification.invalidate(db, finding, "GitHub resolution state changed.")
            finding.status, finding.updated_at = status, now()
            actor = (thread.get("resolvedBy") or {}).get("login", "GitHub")
            db.add(Activity(finding_id=finding.id, action=status, actor=actor, source="github"))


def publication_plan(db: Session, run: ReviewRun) -> list[dict]:
    if run.status == "skipped" and file_limit_exceeded(run):
        if pr_summary.ready_to_sync(run):
            return []
        count = len(run.report["manifest"]["coverage"]["selected"])
        maximum = run.config["settings"]["max_files"]
        return [{
            "commit_id": run.head_sha, "event": "COMMENT", "comments": [],
            "body": (
                f"**Review skipped:** {count} reviewable files exceed the limit of {maximum}. No files were reviewed.\n\n"
                "To bypass the file limit for one review of this revision, post:\n"
                f"`@{github.app_config(db).github_slug}[bot] review anyway`\n\n"
                "Requires an enabled Code Mortis member with repository write access. "
                "Choose a shared API connection. Other review limits still apply.\n\n"
                f"[View run details]({settings().public_url}/runs/{run.id})\n\n"
                + marker("run", f"{run.id}:0")
            ),
        }]
    if assessment.enabled(run):
        return assessed_publication_plan(db, run)
    rows = db.execute(
        select(Occurrence, Finding)
        .join(Finding, Occurrence.finding_id == Finding.id)
        .where(Occurrence.run_id == run.id)
        .order_by(Occurrence.id)
    ).all()
    comments, unanchored = [], []
    for occurrence, finding in rows:
        if (finding.comment_id or finding.published_review_id or finding.status != "open"
            or (finding.publication_run_id and finding.publication_run_id != run.id)):
            continue
        if occurrence.inline_line:
            comments.append(
                {
                    "path": occurrence.snapshot["path"],
                    "line": occurrence.inline_line,
                    "side": "RIGHT",
                    "body": comment_body(finding.id, occurrence.snapshot),
                }
            )
        else:
            unanchored.append(f"- `{finding.path}`: {finding.content[:1200]}")
    summary = f"Code Mortis review: **{run.status}**. {len(rows)} finding(s) observed.\n\n"
    summary += f"[View findings and coverage]({settings().public_url}/runs/{run.id})"
    if run.status == "partial":
        summary += "\n\nSome review items did not complete. This is not a clean review of the entire PR."
    if unanchored:
        summary += "\n\nFindings without a valid inline location:\n" + "\n".join(unanchored)
    summary = summary[:59000]
    batches = [comments[index : index + 30] for index in range(0, len(comments), 30)] or [[]]
    return [
        {
            "commit_id": run.head_sha,
            "event": "COMMENT",
            "comments": batch,
            "body": summary + "\n\n" + marker("run", f"{run.id}:{index}"),
        }
        for index, batch in enumerate(batches)
    ]


def assessed_publication_plan(db, run, *, append=False):
    assessment.withhold_pending(db, run, "Assessment did not finish; candidate withheld.")
    rows = db.execute(select(Occurrence, Finding).join(Finding, Occurrence.finding_id == Finding.id)
                      .where(Occurrence.run_id == run.id).order_by(Occurrence.id)).all()
    batches, comments, unanchored, identities = list(run.publication_plan or []) if append else [], [], [], []
    counts = assessment.summary(db, run)

    def save_batch():
        if not identities:
            return
        body = (
            f"Code Mortis review: **{run.status}**. {counts.new_findings} new validated finding(s), "
            f"{counts.repeated_observations} repeated observation(s), "
            f"{counts.rejected_candidates} not supported candidate(s), "
            f"{counts.withheld_candidates} withheld candidate(s).\n\n"
            f"This batch contains {len(comments)} inline comment(s) and {len(unanchored)} summary finding(s).\n\n"
            f"[View evidence and coverage]({settings().public_url}/runs/{run.id})"
        )
        if run.status == "partial" or counts.incomplete:
            body += "\n\nCoverage or assessment is incomplete. This is not a clean review of the entire PR."
        if pr_summary.ready_to_sync(run):
            body = ""
        if unanchored:
            body += "\n\nFindings without a valid inline location:\n" + "\n\n".join(unanchored)
        index = len(batches)
        batches.append({"commit_id": run.head_sha, "event": "COMMENT", "comments": list(comments),
                        "body": body + "\n\n" + marker("run", f"{run.id}:{index}")})
        for identity in identities:
            finding = db.get(Finding, identity)
            finding.publication_run_id, finding.publication_batch = run.id, index
        comments.clear()
        unanchored.clear()
        identities.clear()

    for occurrence, finding in rows:
        if finding.comment_id or finding.published_review_id or finding.status != "open" or finding.desired_status:
            continue
        if finding.publication_run_id and finding.publication_run_id != run.id:
            continue
        confirmed = db.scalar(select(ReviewCandidate.id).where(
            ReviewCandidate.run_id == run.id, ReviewCandidate.finding_id == finding.id,
            ReviewCandidate.status == "completed", ReviewCandidate.result["verdict"].as_string() == "confirmed",
        ).limit(1))
        if not confirmed:
            continue
        snapshot = {**occurrence.snapshot, "source_sha": occurrence.snapshot.get("source_sha") or run.head_sha}
        if len(identities) >= 30 or sum(len(text) for text in unanchored) > 45000:
            save_batch()
        if occurrence.inline_line:
            comments.append({"path": occurrence.snapshot["path"], "line": occurrence.inline_line,
                             "side": "RIGHT", "body": comment_body(finding.id, snapshot, with_prompt=run.config["settings"].get("ai_fix_prompts_enabled", False), sanitize=bool(run.summary))})
        else:
            entry = f"- `{occurrence.snapshot['path']}`: {occurrence.snapshot['content'][:1200]}\n"
            if run.summary:
                entry = f"- {pr_summary.text(snapshot['path'])}: {pr_summary.text(snapshot['content'][:1200])}\n"
                if run.config["settings"].get("ai_fix_prompts_enabled", False):
                    entry += pr_summary.fix_section([snapshot], budget=12000) + "\n"
            unanchored.append(entry + marker("finding", finding.id))
        identities.append(finding.id)
    save_batch()
    return batches


def reconcile_publications(db, run, remote_reviews, bot):
    """Recover claims, including summary-only findings, before another run may publish."""
    for finding in db.scalars(select(Finding).where(Finding.pr_id == run.pr_id)):
        owner = db.get(ReviewRun, finding.publication_run_id) if finding.publication_run_id else None
        if owner and finding.published_review_id is None:
            expected = marker("run", f"{owner.id}:{finding.publication_batch}")
            published = next((item for item in remote_reviews if item.get("user", {}).get("login") == bot
                              and expected in (item.get("body") or "")), None)
            if published:
                finding.published_review_id = published["id"]
                assessment.record_summary(db, owner)
            elif owner.status in {"cancelled", "superseded"} or owner.publication == "not_applicable":
                finding.publication_run_id, finding.publication_batch = None, None
        if finding.comment_id or finding.publication_run_id:
            continue
        # Legacy summaries have no finding marker. Recognize their exact saved, acknowledged body.
        for occurrence, old in db.execute(select(Occurrence, ReviewRun).join(ReviewRun).where(
            Occurrence.finding_id == finding.id, ReviewRun.publication_plan.is_not(None),
        )):
            for index, payload in enumerate(old.publication_plan or []):
                inline = any(marker("finding", finding.id) in comment["body"] for comment in payload.get("comments", []))
                summary_entry = f"- `{finding.path}`: {finding.content[:1200]}"
                if not inline and (occurrence.inline_line or summary_entry not in payload.get("body", "")):
                    continue
                published = next((item for item in remote_reviews if item.get("user", {}).get("login") == bot
                                  and marker("run", f"{old.id}:{index}") in (item.get("body") or "")), None)
                if published:
                    finding.publication_run_id, finding.publication_batch = old.id, index
                    finding.published_review_id = published["id"]
                    break
                if old.publication not in {"not_applicable", "no_new_comments"} and not old.cancel_requested:
                    finding.publication_run_id, finding.publication_batch = old.id, index
                    break
            if finding.publication_run_id:
                break


def publish_run(run_id: str):
    with transaction() as db:
        candidate = db.get(ReviewRun, run_id)
        if not candidate:
            return
        db.scalar(select(PullRequest).where(PullRequest.id == candidate.pr_id).with_for_update())
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update()
                        .execution_options(populate_existing=True))
        if (not run or run.cancel_requested or assessment.assessment_only(run) or run.progress.get("publication_stopped")
            or not can_publish(run) or run.publication in {"published", "no_new_comments"}):
            return
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        if _mcp.run_revoked(db, run, repository_id=db.get(PullRequest, run.pr_id).repository_id):
            run.publication = "partially_published" if run.review_ids else "not_applicable"
            run.publication_error = _mcp.REVOKED_MESSAGE
            return
        if _pat.run_revoked(db, run, repository_id=db.get(PullRequest, run.pr_id).repository_id):
            run.publication = "partially_published" if run.review_ids else "not_applicable"
            run.publication_error = _pat.REVOKED_MESSAGE
            return
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if pr.ignored:
            return
        if not repo.active or not repo.enabled or not db.get(Installation, repo.installation_id).active:
            raise ValueError("The GitHub installation is no longer active.")
        token = github.installation_token(db, repo.installation_id)
        reconcile_threads(db, pr, repo, token)
        if assessment.enabled(run):
            bot = github.app_config(db).github_slug + "[bot]"
            reconcile_publications(db, run, list(github.pages(f"/repos/{repo.full_name}/pulls/{pr.number}/reviews", token)), bot)
        if run.publication_plan is None:
            run.publication_plan = publication_plan(db, run)
        if not run.publication_plan:
            run.publication, run.publication_error = "no_new_comments", None
            assessment.record_summary(db, run)
            return
        run.publication, run.publication_error = "publishing", None
    for index in range(len(run.publication_plan)):
        with transaction() as db:
            db.scalar(select(PullRequest).where(PullRequest.id == run.pr_id).with_for_update())
            run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
            pr = db.get(PullRequest, run.pr_id)
            repo = db.get(Repository, pr.repository_id)
            if run.progress.get("publication_stopped"):
                return
            if pr.ignored or run.cancel_requested or not repo.active or not repo.enabled or not db.get(Installation, repo.installation_id).active:
                run.publication = "partially_published" if run.review_ids else "not_applicable"
                run.publication_error = "GitHub access was removed or publication was cancelled."
                return
            from code_mortis.api_tokens import provenance as _pat
            from code_mortis.mcp import provenance as _mcp

            if _mcp.run_revoked(db, run, repository_id=repo.id):
                run.publication = "partially_published" if run.review_ids else "not_applicable"
                run.publication_error = _mcp.REVOKED_MESSAGE
                return
            if _pat.run_revoked(db, run, repository_id=repo.id):
                run.publication = "partially_published" if run.review_ids else "not_applicable"
                run.publication_error = _pat.REVOKED_MESSAGE
                return
            token = github.installation_token(db, repo.installation_id)
            current = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
            if (
                run.cancel_requested
                or current["head"]["sha"] != run.head_sha
                or current["base"]["sha"] != run.base_sha
                or current["state"] != "open"
            ):
                run.publication = "partially_published" if run.review_ids else "not_applicable"
                run.publication_error = "The PR changed or closed; remaining comments were not published."
                run.failure_reason = "superseded"
                return
            expected = marker("run", f"{run.id}:{index}")
            bot = github.app_config(db).github_slug + "[bot]"
            existing = next(
                (
                    review
                    for review in github.pages(f"/repos/{repo.full_name}/pulls/{pr.number}/reviews", token)
                    if expected in (review.get("body") or "") and review.get("user", {}).get("login") == bot
                ),
                None,
            )
            if existing is None:
                if assessment.enabled(run):
                    actor_id, trigger = execution_identity(run)
                    actor = db.get(User, actor_id) if actor_id else None
                    if actor_id:
                        if not actor or not actor.enabled or not db.get(Membership, actor.id):
                            raise ValueError("The requesting user no longer has access to publish this review.")
                        github.authorize_repo(db, actor, repo.full_name, write=True)
                    if not is_shared(run.config.get("connection", {})) and not run.owner_id:
                        raise ValueError("The original review owner is no longer available to authorize publication.")
                    if run.owner_id:
                        owner = db.get(User, run.owner_id)
                        if not owner or not owner.enabled or not db.get(Membership, owner.id):
                            raise ValueError("The original review owner no longer has access to publish this review.")
                        github.authorize_repo(db, owner, repo.full_name, write=True)
                    claimed = list(db.scalars(select(Finding).where(
                        Finding.publication_run_id == run.id, Finding.publication_batch == index,
                    ).with_for_update()))
                    expected_ids = set(re.findall(r"<!-- code-mortis:finding:([0-9a-f-]{36}) -->",
                                                  json.dumps(run.publication_plan[index])))
                    if ({finding.id for finding in claimed} != expected_ids
                        or any(finding.status != "open" or finding.desired_status for finding in claimed)):
                        run.publication = "partially_published" if run.review_ids else "not_applicable"
                        run.publication_error = "A finding changed while comments were being prepared. Those comments were not sent to GitHub. Refresh the pull request before retrying publication."
                        return
                existing = github.request(
                    "POST",
                    f"/repos/{repo.full_name}/pulls/{pr.number}/reviews",
                    token,
                    json=run.publication_plan[index],
                )
            run.review_ids = list(dict.fromkeys([*run.review_ids, existing["id"]]))
            for finding in db.scalars(select(Finding).where(
                Finding.publication_run_id == run.id, Finding.publication_batch == index,
            )):
                finding.published_review_id = existing["id"]
            assessment.record_summary(db, run)
    with transaction() as db:
        db.scalar(select(PullRequest).where(PullRequest.id == run.pr_id).with_for_update())
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if run.progress.get("publication_stopped"):
            return
        reconcile_threads(db, pr, repo, github.installation_token(db, repo.installation_id))
        run.publication, run.publication_error = "published", None


def request_finding_status(db: Session, finding: Finding, status: str, actor: User, reason: str):
    from code_mortis.api_tokens import provenance as _pat
    from code_mortis.mcp import provenance as _mcp

    verification.invalidate(db, finding, "A manual finding action superseded automatic resolution.")
    finding.sync_error = None
    finding.sync_intent = None
    if finding.thread_id:
        finding.desired_status = status
        # Explicit user action adopts the initiating session (or its PAT).
        finding.sync_intent = _pat.adopt_intent(_mcp.with_provenance({
            "finding_id": finding.id, "version": finding.sync_version,
            "actor_id": actor.id, "reason": reason, "status": status,
        }, _mcp.current_provenance()))
        enqueue(db, "resolve", finding.sync_intent)
    else:
        finding.status, finding.updated_at = status, now()
        db.add(Activity(finding_id=finding.id, action=status, actor=actor.login, source="app", reason=reason))


def synchronize_finding(payload: dict):
    if payload.get("reopen_verification_id"):
        verification.restore_reopening(payload)
        return
    if payload.get("verification_id"):
        verification.synchronize(payload)
        return
    with transaction() as db:
        finding = db.scalar(select(Finding).where(Finding.id == payload["finding_id"]).with_for_update())
        if not finding or finding.sync_version != payload["version"] or not finding.desired_status:
            return
        from code_mortis.api_tokens import provenance as _pat
        from code_mortis.mcp import provenance as _mcp

        if _mcp.revoked(db, _mcp.provenance_from_payload(payload) or (
                finding.sync_intent or {}).get(_mcp.PROVENANCE_KEY),
                repository_id=db.get(PullRequest, finding.pr_id).repository_id):
            finding.sync_error = _mcp.REVOKED_MESSAGE
            return
        if _pat.revoked(db, _pat.origin_for(payload, finding.sync_intent),
                repository_id=db.get(PullRequest, finding.pr_id).repository_id):
            finding.sync_error = _pat.REVOKED_MESSAGE
            return
        actor = db.get(User, payload["actor_id"])
        if not actor or not actor.enabled:
            raise ValueError("The requesting user no longer has access.")
        pr = db.get(PullRequest, finding.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if not repo.active or not db.get(Installation, repo.installation_id).active:
            raise ValueError("GitHub access was removed. The finding status has not changed.")
        github.authorize_repo(db, actor, repo.full_name, write=True)
        token = github.installation_token(db, repo.installation_id, resolve=True)
        desired = finding.desired_status
        github.set_thread(token, finding.thread_id, desired != "open")
        finding.status, finding.desired_status, finding.sync_error = desired, None, None
        # Retain reopening intent to recover a late automatic write after worker loss.
        if desired != "open":
            finding.sync_intent = None
        finding.updated_at = now()
        db.add(
            Activity(
                finding_id=finding.id,
                action=desired,
                actor=actor.login,
                source="app",
                reason=payload["reason"],
            )
        )
