"""Explicit, durable publication of individual security findings as GitHub issues."""

import re
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select

from . import github, repository_security
from .db import now, transaction, uid
from .models import AppConfig, Installation, Membership, Repository, SecurityFinding, SecurityScan, User
from .reviews import enqueue
from .security import clean_text
from .security_engine import secure_digest
from .security_schemas import SecurityIssuePreview


def marker(finding, operation):
    return f"<!-- code-mortis-security:{finding.id}:{operation}:{secure_digest(finding.id + operation)} -->"


def preview(repo, finding):
    evidence = finding.evidence
    sha = evidence["source_sha"]
    anchor = f"#L{evidence['start_line']}-L{evidence.get('end_line', evidence['start_line'])}" if evidence.get("start_line") else ""
    lines = [f"## {finding.title}", "", f"Severity: **{finding.severity.title()}**", "",
             evidence.get("explanation", ""), "", "### Evidence", "",
             f"Observed in commit `{sha}`: https://github.com/{repo.full_name}/blob/{sha}/{quote(finding.path, safe='/')}{anchor}", ""]
    if finding.kind == "dependency":
        lines.extend([f"Package: `{evidence.get('package', '')}` | Installed: `{evidence.get('installed_version', '')}`",
                      f"Advisory: `{evidence.get('rule_id', '')}` | Fixed versions: `{evidence.get('fixed_version') or 'Not reported'}`", ""])
    for citation in evidence.get("evidence", []):
        lines.extend([f"https://github.com/{repo.full_name}/blob/{sha}/{quote(citation['path'], safe='/')}#L{citation['start_line']}-L{citation['end_line']}", "",
                      "    " + citation["quote"].replace("\n", "\n    "), ""])
    lines.extend(["### Impact", "", evidence.get("impact", "See the affected condition and references."), "",
                  "### Remediation", "", evidence.get("remediation", ""), "",
                  "### Verification", "", evidence.get("verification", ""), ""])
    for reference in evidence.get("references", []):
        if isinstance(reference, str) and reference.startswith("https://"):
            lines.append(reference)
    return SecurityIssuePreview(title=clean_text(f"[Security] {finding.title}")[:200],
        body=clean_text("\n".join(lines)), repository=repo.full_name, public=not repo.private, version=finding.version,
        recovery_token=finding.issue.get("attempt_id", finding.issue.get("operation_id")) if finding.issue.get("started") else None)


def create(db, repo, finding, actor, body, *, new_only=False):
    if finding.state not in {"open", "accepted_risk"} or finding.version != body.version:
        raise HTTPException(409, "The finding changed. Refresh its issue preview.")
    operation, recovering = uid(), bool(body.recovery_token)
    if recovering and new_only:
        raise HTTPException(409, "Uncertain publications require individual review and approval.")
    title, content = validate_public_text(body.title.strip()), validate_public_text(body.body.strip())
    if any(repository_security.redact_finding_text(db, finding, value) != value for value in (title, content)):
        raise ValueError("Issue text contains credential material. Remove it and review the payload before publishing.")
    if not title or not content:
        raise ValueError("Enter an issue title and body without application identity markers.")
    if finding.issue:
        saved = finding.issue
        if (saved.get("title") == title and saved.get("body") == content + "\n\n" + marker(finding, saved["operation_id"])
            and saved.get("version") == body.version and saved.get("public") == body.public
            and (not recovering or (saved.get("recovery_approved") == body.recovery_token
                                   and not saved.get("number") and saved.get("sync_status") == "pending"))):
            return
        if recovering:
            if (not saved.get("started") or saved.get("number") or saved.get("sync_status") not in {"uncertain", "failed"}
                or body.recovery_token != saved.get("attempt_id", saved["operation_id"])):
                raise HTTPException(409, "The publication attempt changed. Refresh its recovery preview.")
            operation = saved["operation_id"]
        elif new_only or saved.get("number") or saved.get("started"):
            raise HTTPException(409, "This finding already has an issue or an uncertain publication. Retry synchronization instead.")
    elif recovering:
        raise HTTPException(409, "There is no uncertain publication to recover.")
    finding.issue = {"operation_id": operation, "actor_id": actor.id, "version": finding.version,
                     "public": body.public,
                     "title": title, "body": content + "\n\n" + marker(finding, operation),
                     "publication_status": "pending", "sync_status": "pending", "started": recovering,
                     "approved_at": saved["approved_at"] if recovering else now().isoformat(),
                     **({"attempt_id": body.recovery_token, "recovery_approved": body.recovery_token} if recovering else {})}
    enqueue(db, "security_issue_publish", {"finding_id": finding.id})
    repository_security.activity(db, "issue_approved", finding=finding, actor=actor,
                                 operation_id=operation, title=title, body=content, public=body.public, version=body.version,
                                 recovery_token=body.recovery_token)


def identity(db, finding, *, publishing=False):
    repo = db.get(Repository, finding.repository_id)
    install = db.get(Installation, repo.installation_id) if repo else None
    config = db.get(AppConfig, 1)
    if not repo or not repo.active or not install or not install.active or not config:
        raise ValueError("Repository or GitHub App access is no longer available.")
    actor = db.get(User, finding.issue.get("actor_id"))
    if not actor or not actor.enabled or not db.get(Membership, actor.id):
        raise ValueError("The user who approved this issue no longer has access.")
    remote = github.authorize_repo(db, actor, repo.full_name, write=True)
    if remote.get("id") != repo.id:
        raise ValueError("GitHub returned a different repository identity.")
    if publishing and (type(remote.get("private")) is not bool or finding.issue.get("public") != (not remote["private"])):
        raise ValueError("Repository visibility changed. Review and approve the issue again before publishing.")
    token = github.installation_token(db, repo.installation_id, issues=True)
    return repo.full_name, token, config.github_slug + "[bot]"


def owned(remote, issue_marker, full_name, login):
    author = remote.get("user") or {}
    number = remote.get("number")
    if (type(number) is not int or number <= 0 or type(remote.get("id")) is not int
        or not isinstance(remote.get("node_id"), str) or not remote["node_id"]
        or author.get("login") != login or author.get("type") != "Bot" or remote.get("pull_request")
        or issue_marker not in (remote.get("body") or "")
        or remote.get("html_url") != f"https://github.com/{full_name}/issues/{number}"
        or remote.get("repository_url") != f"https://api.github.com/repos/{full_name}"):
        raise ValueError("The linked GitHub issue could not be verified as this Code Mortis publication.")
    return remote


def failure(finding_id, message, *, uncertain=False):
    with transaction() as db:
        row = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
        if row:
            row.issue = {**row.issue, "sync_status": "uncertain" if uncertain else "failed", "error": clean_text(message)[:2000]}
            repository_security.activity(db, "issue_sync_failed", finding=row, reason=row.issue["error"])


def publish(finding_id):
    claimed, attempted = False, False
    try:
        with transaction() as db:
            finding = db.get(SecurityFinding, finding_id)
            if not finding or not finding.issue or finding.issue.get("number"):
                return
            approved = dict(finding.issue)
            attempt = approved.get("attempt_id", approved["operation_id"])
            full_name, token, login = identity(db, finding, publishing=not approved["started"])
            issue_marker = marker(finding, approved["operation_id"])
        remote = None
        if approved["started"]:
            matches = [row for row in github.pages(f"/repos/{full_name}/issues?state=all&since={approved['approved_at']}", token)
                       if issue_marker in (row.get("body") or "") and not row.get("pull_request")]
            if len(matches) == 1:
                remote = owned(matches[0], issue_marker, full_name, login)
            elif matches or approved.get("recovery_approved") != attempt:
                failure(finding_id, "Issue creation has an uncertain outcome. Retry synchronization to look for the original issue, or review and explicitly approve another creation attempt after checking GitHub. Another attempt may create a duplicate.", uncertain=True)
                return
        if remote is None:
            with transaction() as db:
                finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
                if finding.issue.get("operation_id") != approved["operation_id"]:
                    return
                if finding.issue.get("started") != approved["started"] or finding.issue.get("number"):
                    return
                if approved["started"] and (finding.issue.get("recovery_approved") != attempt
                    or finding.issue.get("attempt_id", finding.issue["operation_id"]) != attempt):
                    return
                if finding.version != approved["version"] or finding.state not in {"open", "accepted_risk"}:
                    finding.issue = {**finding.issue, "publication_status": "needs_review", "sync_status": "failed",
                                     "error": "The finding changed after approval. Review and approve its issue again."}
                    return
                finding.issue = {**finding.issue, "started": True, "publication_status": "creating",
                                 "attempt_id": uid(), "recovery_approved": None}
            claimed = True
            with transaction() as db:
                finding = db.get(SecurityFinding, finding_id)
                full_name, token, login = identity(db, finding, publishing=True)
                if finding.version != approved["version"] or finding.state not in {"open", "accepted_risk"}:
                    finding.issue = {**finding.issue, "started": approved["started"], "publication_status": "needs_review", "sync_status": "failed",
                                     "error": "The finding changed before publication. Review and approve its issue again."}
                    return
            attempted = True
            remote = owned(github.request("POST", f"/repos/{full_name}/issues", token,
                json={"title": approved["title"], "body": approved["body"]}), issue_marker, full_name, login)
        with transaction() as db:
            finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
            if finding.issue.get("operation_id") != approved["operation_id"]:
                return
            finding.issue = {**finding.issue, "number": remote["number"], "id": remote["id"],
                "node_id": remote["node_id"], "url": remote["html_url"], "publication_status": "published",
                "sync_status": "synced", "remote_state": remote["state"], "error": None,
                "closed_by_app": False, "synced_at": now().isoformat()}
            repository_security.activity(db, "issue_published", finding=finding, url=remote["html_url"])
    except Exception as exc:
        if claimed and not attempted:
            with transaction() as db:
                finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
                if finding and finding.issue.get("operation_id") == approved["operation_id"]:
                    finding.issue = {**finding.issue, "started": approved["started"], "publication_status": "needs_review"}
        failure(finding_id, str(exc), uncertain=True)


def sync(finding_id):
    try:
        with transaction() as db:
            finding = db.get(SecurityFinding, finding_id)
            if not finding or not finding.issue:
                return
            if not finding.issue.get("number"):
                publish_pending = True
            else:
                publish_pending = False
                full_name, token, login = identity(db, finding)
                saved, state, version = dict(finding.issue), finding.state, finding.version
                scan = db.get(SecurityScan, saved.get("verification_scan_id")) if saved.get("verification_scan_id") else None
                scan_sha, scan_branch = (scan.head_sha, scan.branch) if scan else (None, None)
                scan_status = scan.status if scan else None
                issue_marker = marker(finding, saved["operation_id"])
        if publish_pending:
            publish(finding_id)
            return
        remote = owned(github.request("GET", f"/repos/{full_name}/issues/{saved['number']}", token), issue_marker, full_name, login)
        if remote["id"] != saved["id"] or remote["node_id"] != saved["node_id"]:
            raise ValueError("GitHub issue identity changed.")
        desired = saved.get("desired_state")
        closed_by_app = saved.get("closed_by_app", False)
        if desired:
            if saved.get("intent_version") != version or (desired == "closed" and state != "resolved") or (desired == "open" and state != "open"):
                raise ValueError("The finding changed after issue synchronization was queued.")
            fresh = repository_security.branch_head(full_name, token)
            if scan_status != "completed" or fresh["head_sha"] != scan_sha or fresh["default_branch"] != scan_branch:
                raise ValueError("Issue synchronization requires a complete scan of the current default branch.")
            operation_marker = f"<!-- code-mortis-security-state:{saved['operation_id']}:{version}:{desired} -->"
            if remote["state"] == desired and saved.get("state_started") == operation_marker:
                if desired == "open" or (remote.get("closed_by") or {}).get("login") == login:
                    closed_by_app = desired == "closed"
            if desired == "open" and remote["state"] != "open" and (not closed_by_app or (remote.get("closed_by") or {}).get("login") != login):
                raise ValueError("A human changed this issue's closure state. Reopen it manually if appropriate.")
            if desired == "closed" and remote["state"] == "closed" and not closed_by_app:
                desired = None
            elif remote["state"] != desired:
                with transaction() as db:
                    finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
                    if finding.version != version or finding.issue.get("intent_version") != version:
                        raise ValueError("Finding state changed before GitHub synchronization.")
                comment_marker = operation_marker
                comments = github.pages(f"/repos/{full_name}/issues/{saved['number']}/comments", token)
                if not any(comment_marker in (value.get("body") or "") and (value.get("user") or {}).get("login") == login for value in comments):
                    # A comment with an unknown delivery must be reconciled before another POST.
                    with transaction() as db:
                        finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
                        if finding.issue.get("comment_started") == comment_marker:
                            raise ValueError("Verification comment delivery is uncertain. Retry synchronization to reconcile it.")
                        finding.issue = {**finding.issue, "comment_started": comment_marker}
                    text = f"{'Fix verified' if desired == 'closed' else 'Finding recurrence confirmed'} in commit `{scan_sha}`. The saved security scan verified the current default-branch evidence.\n\n{comment_marker}"
                    github.request("POST", f"/repos/{full_name}/issues/{saved['number']}/comments", token, json={"body": text})
                with transaction() as db:
                    finding = db.get(SecurityFinding, finding_id)
                    full_name, token, login = identity(db, finding)
                    if finding.version != version or finding.issue.get("intent_version") != version or finding.state != state:
                        raise ValueError("The finding changed immediately before issue synchronization.")
                fresh = repository_security.branch_head(full_name, token)
                if fresh["head_sha"] != scan_sha or fresh["default_branch"] != scan_branch:
                    raise ValueError("The default branch changed before issue synchronization.")
                latest = owned(github.request("GET", f"/repos/{full_name}/issues/{saved['number']}", token), issue_marker, full_name, login)
                if latest["state"] != remote["state"] or (latest.get("closed_by") or {}).get("login") != (remote.get("closed_by") or {}).get("login"):
                    raise ValueError("The GitHub issue changed during synchronization. Retry to reconcile its state.")
                with transaction() as db:
                    finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
                    if finding.version != version or finding.issue.get("intent_version") != version:
                        raise ValueError("The finding changed before the issue state request.")
                    finding.issue = {**finding.issue, "state_started": operation_marker}
                remote = owned(github.request("PATCH", f"/repos/{full_name}/issues/{saved['number']}", token,
                    json={"state": desired, "state_reason": "completed" if desired == "closed" else "reopened"}), issue_marker, full_name, login)
                if remote["state"] != desired:
                    raise ValueError("GitHub did not confirm the requested issue state.")
                closed_by_app = desired == "closed"
        with transaction() as db:
            finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == finding_id).with_for_update())
            if finding.version != version:
                raise ValueError("The finding changed before synchronization could be recorded.")
            finding.issue = {**finding.issue, "sync_status": "synced", "desired_state": None,
                             "remote_state": remote["state"], "closed_by_app": closed_by_app,
                             "synced_at": now().isoformat(), "error": None}
            if saved.get("desired_state"):
                repository_security.activity(db, "issue_state_synced", finding=finding, state=remote["state"], commit=scan_sha)
    except Exception as exc:
        failure(finding_id, str(exc))


def retry(db, finding, actor):
    if not finding.issue:
        raise ValueError("This finding has no approved GitHub issue publication.")
    finding.issue = {**finding.issue, "sync_status": "pending", "error": None}
    enqueue(db, "security_issue_sync", {"finding_id": finding.id})
    repository_security.activity(db, "issue_sync_retried", finding=finding, actor=actor)


def validate_public_text(value):
    if re.search(r"<!--\s*code-mortis-security", value):
        raise ValueError("Application identity markers cannot be supplied in issue text.")
    if clean_text(value) != value:
        raise ValueError("Issue text contains credential material. Remove it and review the sanitized payload before publishing.")
    return value
