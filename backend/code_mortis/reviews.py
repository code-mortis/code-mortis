import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import github, ocr
from .config import settings
from .db import now, transaction, uid
from .models import (
    Activity,
    Connection,
    Finding,
    Installation,
    Job,
    Occurrence,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from .schemas import RepoSettings
from .security import digest, unseal

ACTIVE = {"queued", "running"}


def enqueue(db: Session, kind: str, payload: dict) -> Job:
    job = Job(id=uid(), kind=kind, payload=payload)
    db.add(job)
    return job


def connection_snapshot(connection: Connection) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "provider": connection.provider,
        "protocol": connection.protocol,
        "model": connection.model,
        "base_url": connection.base_url,
        "options": connection.options,
    }


def create_run(
    db: Session, pr: PullRequest, repo: Repository, trigger: str, actor: User | None = None
) -> ReviewRun:
    # Serialize run creation per PR; automatic delivery and button retries can race.
    db.scalar(select(PullRequest).where(PullRequest.id == pr.id).with_for_update())
    if not repo.active or not repo.enabled or not repo.connection_id:
        raise ValueError("Enable this repository and select an AI connection first.")
    connection = db.get(Connection, repo.connection_id)
    options = RepoSettings.model_validate(repo.settings).model_dump()
    config = {"connection": connection_snapshot(connection), "settings": options}
    if options["model"]:
        config["connection"]["model"] = options["model"]
    key = digest(json.dumps([pr.id, pr.base_sha, pr.head_sha, config], sort_keys=True))
    if trigger == "automatic":
        prior = db.scalar(select(ReviewRun).where(ReviewRun.dedup_key == key))
        if prior:
            return prior
    active = db.scalars(select(ReviewRun).where(ReviewRun.pr_id == pr.id, ReviewRun.status.in_(ACTIVE))).all()
    for run in active:
        if run.head_sha == pr.head_sha and run.base_sha == pr.base_sha:
            return run
        run.status = "superseded"
        run.publication = "not_applicable"
        run.cancel_requested = True
        run.finished_at = now()
    run = ReviewRun(
        id=uid(),
        pr_id=pr.id,
        actor_id=actor.id if actor else None,
        trigger=trigger,
        dedup_key=key if trigger == "automatic" else uid(),
        base_sha=pr.base_sha,
        head_sha=pr.head_sha,
        config=config,
    )
    db.add(run)
    enqueue(db, "review", {"run_id": run.id})
    return run


def upsert_pr(db: Session, repo: Repository, data: dict) -> PullRequest:
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
    pr.updated_at = now()
    db.flush()
    for run in db.scalars(select(ReviewRun).where(ReviewRun.pr_id == pr.id, ReviewRun.status.in_(ACTIVE))):
        if run.head_sha != pr.head_sha or run.base_sha != pr.base_sha or pr.state != "open":
            run.status, run.publication, run.cancel_requested = "superseded", "not_applicable", True
            run.finished_at = now()
    return pr


def review_stopped(run_id: str) -> bool:
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        return not run or run.cancel_requested or run.status in {"cancelled", "superseded"}


def perform_review(run_id: str):
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not run or run.status not in ACTIVE or run.cancel_requested:
            return
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        installation = db.get(Installation, repo.installation_id)
        if not repo.active or not repo.enabled or not installation.active:
            raise ValueError("The repository or GitHub installation has been disabled.")
        if run.actor_id:
            actor = db.get(User, run.actor_id)
            if not actor or not actor.enabled:
                raise ValueError("The user who requested this run no longer has access.")
            github.authorize_repo(db, actor, repo.full_name, write=True)
        token = github.installation_token(db, repo.installation_id)
        current = github.request("GET", f"/repos/{repo.full_name}/pulls/{pr.number}", token)
        if (
            current["head"]["sha"] != run.head_sha
            or current["base"]["sha"] != run.base_sha
            or current["state"] != "open"
        ):
            run.status, run.publication, run.finished_at = "superseded", "not_applicable", now()
            return
        connection = db.get(Connection, run.config["connection"]["id"])
        if not connection:
            raise ValueError("The AI connection is no longer available.")
        if any(
            run.config["connection"][key] != getattr(connection, key)
            for key in ("provider", "protocol", "base_url")
        ):
            raise ValueError(
                "The AI connection endpoint changed after this run was queued. Start a new review."
            )
        credentials = json.loads(unseal(connection.secret))
        run.status, run.started_at, run.error = "running", now(), None
        args = (
            repo.full_name,
            pr.number,
            run.base_sha,
            run.head_sha,
            run.config["connection"],
            credentials,
            run.config["settings"],
            token,
        )
    try:
        status, report, comments = ocr.review(*args, stopped=lambda: review_stopped(run_id))
    except ocr.ReviewStopped:
        with transaction() as db:
            run = db.get(ReviewRun, run_id)
            if run.status != "superseded":
                run.status = "cancelled" if run.cancel_requested else "superseded"
            run.publication, run.finished_at = "not_applicable", now()
        return
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if run.cancel_requested or run.status in {"cancelled", "superseded"}:
            return
        run.status, run.report, run.finished_at = status, report, now()
        if status in {"failed", "skipped"}:
            run.publication = "not_applicable"
            if status == "failed":
                run.error = (
                    "OCR did not complete the selected review scope. Inspect coverage and retry the review."
                )
        seen = set()
        for comment in comments:
            key = ocr.fingerprint(comment)
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
        if status in {"completed", "partial"}:
            enqueue(db, "publish", {"run_id": run.id})


def marker(kind: str, identity: str) -> str:
    return f"<!-- code-mortis:{kind}:{identity} -->"


def comment_body(finding_id: str, snapshot: dict) -> str:
    body = f"**{snapshot['severity'].title()} · {snapshot['category']}**\n\n{snapshot['content']}"
    suggestion = snapshot.get("suggestion_code")
    if suggestion:
        # Suggestions are shown as code, never executable patches or automatic fixes.
        fence = "`" * max(3, max((len(match) for match in re.findall(r"`+", suggestion)), default=0) + 1)
        body += f"\n\nSuggested change:\n{fence}\n{suggestion}\n{fence}"
    return body[:60000] + "\n\n" + marker("finding", finding_id)


def reconcile_threads(db: Session, pr: PullRequest, repo: Repository, token: str):
    bot = github.app_config(db).github_slug + "[bot]"
    by_id = {finding.id: finding for finding in db.scalars(select(Finding).where(Finding.pr_id == pr.id))}
    for thread in github.threads(token, repo.full_name, pr.number):
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
        status = (
            ("dismissed" if finding.status == "dismissed" else "resolved") if thread["isResolved"] else "open"
        )
        if status != finding.status:
            finding.status, finding.updated_at = status, now()
            actor = (thread.get("resolvedBy") or {}).get("login", "GitHub")
            db.add(Activity(finding_id=finding.id, action=status, actor=actor, source="github"))


def publication_plan(db: Session, run: ReviewRun) -> list[dict]:
    rows = db.execute(
        select(Occurrence, Finding)
        .join(Finding, Occurrence.finding_id == Finding.id)
        .where(Occurrence.run_id == run.id)
        .order_by(Occurrence.id)
    ).all()
    comments, unanchored = [], []
    for occurrence, finding in rows:
        if finding.comment_id or finding.status != "open":
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


def publish_run(run_id: str):
    with transaction() as db:
        run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
        if not run or run.status not in {"completed", "partial"} or run.publication == "published":
            return
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        if not repo.active or not db.get(Installation, repo.installation_id).active:
            raise ValueError("The GitHub installation is no longer active.")
        token = github.installation_token(db, repo.installation_id)
        reconcile_threads(db, pr, repo, token)
        if run.publication_plan is None:
            run.publication_plan = publication_plan(db, run)
        run.publication, run.publication_error = "publishing", None
    for index in range(len(run.publication_plan)):
        with transaction() as db:
            run = db.scalar(select(ReviewRun).where(ReviewRun.id == run_id).with_for_update())
            pr = db.get(PullRequest, run.pr_id)
            repo = db.get(Repository, pr.repository_id)
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
                existing = github.request(
                    "POST",
                    f"/repos/{repo.full_name}/pulls/{pr.number}/reviews",
                    token,
                    json=run.publication_plan[index],
                )
            run.review_ids = list(dict.fromkeys([*run.review_ids, existing["id"]]))
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        pr = db.get(PullRequest, run.pr_id)
        repo = db.get(Repository, pr.repository_id)
        reconcile_threads(db, pr, repo, github.installation_token(db, repo.installation_id))
        run.publication, run.publication_error = "published", None


def request_finding_status(db: Session, finding: Finding, status: str, actor: User, reason: str):
    finding.sync_version += 1
    finding.sync_error = None
    if finding.thread_id:
        finding.desired_status = status
        enqueue(
            db,
            "resolve",
            {
                "finding_id": finding.id,
                "version": finding.sync_version,
                "actor_id": actor.id,
                "reason": reason,
            },
        )
    else:
        finding.status, finding.updated_at = status, now()
        db.add(Activity(finding_id=finding.id, action=status, actor=actor.login, source="app", reason=reason))


def synchronize_finding(payload: dict):
    with transaction() as db:
        finding = db.scalar(select(Finding).where(Finding.id == payload["finding_id"]).with_for_update())
        if not finding or finding.sync_version != payload["version"] or not finding.desired_status:
            return
        actor = db.get(User, payload["actor_id"])
        if not actor or not actor.enabled:
            raise ValueError("The requesting user no longer has access.")
        pr = db.get(PullRequest, finding.pr_id)
        repo = db.get(Repository, pr.repository_id)
        github.authorize_repo(db, actor, repo.full_name, write=True)
        token = github.installation_token(db, repo.installation_id)
        desired = finding.desired_status
        github.set_thread(token, finding.thread_id, desired != "open")
        finding.status, finding.desired_status, finding.sync_error = desired, None, None
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
