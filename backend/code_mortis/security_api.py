"""Authenticated repository security endpoints."""

import json
from collections import Counter
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import JSONB

from . import github, repository_security, security_assessments, security_issues
from .api import DB, USER, repo_access, repo_out
from .db import now
from .models import SecurityActivity, SecurityFinding, SecurityOccurrence, SecurityScan
from .schemas import RepositoryOut
from .security_schemas import (
    SecurityActivityOut,
    SecurityActivityPage,
    SecurityAssessmentOut,
    SecurityAssessmentPage,
    SecurityBulkIssueInput,
    SecurityFindingDetail,
    SecurityFindingOut,
    SecurityFindingPage,
    SecurityIssueCandidate,
    SecurityIssueCandidatePage,
    SecurityIssueInput,
    SecurityIssuePreview,
    SecurityIssueReapproval,
    SecurityRecordPage,
    SecurityReportPage,
    SecurityRepositoryOut,
    SecurityRepositoryPage,
    SecurityRotation,
    SecurityScanDetail,
    SecurityScanInput,
    SecurityScanOut,
    SecurityScanPage,
    SecurityTriage,
)

router = APIRouter(prefix="/api/v1/security", tags=["Security"])
PAGE_SIZE = 20


def scan_access(db, user, identity, write=False):
    scan = db.get(SecurityScan, identity)
    if not scan:
        raise HTTPException(404, "Security scan not found.")
    repo, remote = repo_access(db, user, scan.repository_id, write=write)
    return scan, repo, remote


def finding_access(db, user, identity, write=False, lock=False):
    finding = db.get(SecurityFinding, identity)
    if not finding:
        raise HTTPException(404, "Security finding not found.")
    repo, remote = repo_access(db, user, finding.repository_id, write=write)
    if lock:
        finding = db.scalar(select(SecurityFinding).where(SecurityFinding.id == identity).with_for_update()
                            .execution_options(populate_existing=True))
    return finding, repo, remote


@router.get("/repositories", response_model=SecurityRepositoryPage)
def repositories(db: DB, user: USER,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    search: Annotated[str, Query(max_length=200)] = "",
    grade: Annotated[list[Literal["A", "B", "C", "D", "F", "unrated"]] | None, Query(max_length=100)] = None,
    status: Annotated[list[Literal["not_scanned", "current", "incomplete", "unavailable", "outdated"]] | None, Query(max_length=100)] = None,
    ordering: Literal["updated_at", "-updated_at", "repository", "-repository"] = "-updated_at",
):
    repos = github.visible_repositories(db, user)
    summaries = repository_security.summaries(db, [repo.id for repo in repos])
    grade_counts = Counter(summaries[repo.id].grade or "unrated" for repo in repos)
    status_counts = Counter(summaries[repo.id].state for repo in repos)
    term = search.strip().casefold()
    rows = sorted((repo for repo in repos if (not term or term in repo.full_name.casefold())
        and (not grade or (summaries[repo.id].grade or "unrated") in grade)
        and (not status or summaries[repo.id].state in status)), key=lambda repo: (repo.full_name.casefold(), repo.id))
    if ordering.removeprefix("-") == "updated_at":
        activity_repository = func.coalesce(SecurityScan.repository_id, SecurityFinding.repository_id)
        changes = select(SecurityScan.repository_id.label("repository_id"),
            func.greatest(SecurityScan.created_at, SecurityScan.started_at, SecurityScan.finished_at).label("updated_at")).union_all(
            select(SecurityFinding.repository_id, SecurityFinding.updated_at),
            select(activity_repository, SecurityActivity.created_at).select_from(SecurityActivity)
                .outerjoin(SecurityScan, SecurityActivity.scan_id == SecurityScan.id)
                .outerjoin(SecurityFinding, SecurityActivity.finding_id == SecurityFinding.id),
        ).subquery()
        updated = dict(db.execute(select(changes.c.repository_id, func.max(changes.c.updated_at))
            .where(changes.c.repository_id.in_([repo.id for repo in rows])).group_by(changes.c.repository_id)).all())
        direction = -1 if ordering.startswith("-") else 1
        rows.sort(key=lambda repo: (repo.id not in updated, direction * updated[repo.id].timestamp() if repo.id in updated else 0))
    elif ordering.startswith("-"):
        rows.reverse()
    return SecurityRepositoryPage(items=[SecurityRepositoryOut(repository=RepositoryOut.model_validate(repo),
        summary=summaries[repo.id]) for repo in rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]],
        total=len(rows), registered_total=len(repos), grade_counts=dict(grade_counts), status_counts=dict(status_counts),
        page=page, page_size=PAGE_SIZE)


@router.get("/repositories/{repository_id}", response_model=SecurityRepositoryOut)
def repository(repository_id: int, db: DB, user: USER):
    repo, remote = repo_access(db, user, repository_id)
    summary = repository_security.summaries(db, [repo.id])[repo.id]
    summary.default_branch = remote.get("default_branch") or summary.default_branch
    return SecurityRepositoryOut(repository=repo_out(db, repo), summary=summary,
        can_write=bool(remote.get("permissions", {}).get("push")),
        finding_count=db.scalar(select(func.count()).select_from(SecurityFinding).where(SecurityFinding.repository_id == repo.id)),
        scan_count=db.scalar(select(func.count()).select_from(SecurityScan).where(SecurityScan.repository_id == repo.id)))


@router.get("/repositories/{repository_id}/scans", response_model=SecurityScanPage)
def scans(repository_id: int, db: DB, user: USER, page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    repo_access(db, user, repository_id)
    query = select(SecurityScan).where(SecurityScan.repository_id == repository_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(SecurityScan.created_at.desc(), SecurityScan.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    return SecurityScanPage(items=[repository_security.public_scan(row) for row in items], total=total, page=page, page_size=PAGE_SIZE)


@router.post("/repositories/{repository_id}/scans", response_model=SecurityScanOut, status_code=202)
def start_scan(repository_id: int, body: SecurityScanInput, db: DB, user: USER):
    repo, remote = repo_access(db, user, repository_id, write=True)
    return repository_security.public_scan(repository_security.create_scan(db, repo, user, body, remote))


@router.get("/scans/{scan_id}", response_model=SecurityScanDetail)
def scan_detail(scan_id: str, db: DB, user: USER):
    scan, repo, remote = scan_access(db, user, scan_id)
    _, assessment = security_assessments.read(db, scan)
    public = repository_security.public_scan(scan)
    public.report = {key: value for key, value in public.report.items() if key != "findings"}
    public.coverage = {key: value for key, value in public.coverage.items() if key not in {"inventory", "excluded", "units", "problems"}}
    public.coverage["inventory_count"] = len(scan.coverage.get("inventory", {}))
    return SecurityScanDetail(repository=repo_out(db, repo), scan=public,
        can_write=bool(remote.get("permissions", {}).get("push")),
        assessment=assessment,
        finding_count=db.scalar(select(func.count()).select_from(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan.id)),
        report_count=len(scan.report.get("findings", [])),
        activity_count=db.scalar(select(func.count()).select_from(SecurityActivity).where(SecurityActivity.scan_id == scan.id)),
        activity=[SecurityActivityOut.model_validate(row) for row in db.scalars(select(SecurityActivity).where(
            SecurityActivity.scan_id == scan.id).order_by(SecurityActivity.created_at.desc()).limit(200))])


def activity_page(db, query, page):
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(SecurityActivity.created_at.desc(), SecurityActivity.id.desc())
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    return SecurityActivityPage(items=[SecurityActivityOut.model_validate(row) for row in rows],
                                total=total, page=page, page_size=PAGE_SIZE)


@router.get("/scans/{scan_id}/activity", response_model=SecurityActivityPage)
def scan_activity(scan_id: str, db: DB, user: USER, page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    scan_access(db, user, scan_id)
    return activity_page(db, select(SecurityActivity).where(SecurityActivity.scan_id == scan_id), page)


@router.get("/scans/{scan_id}/coverage/{section}", response_model=SecurityRecordPage)
def scan_coverage(scan_id: str, section: Literal["inventory", "excluded", "units", "problems"], db: DB, user: USER,
                  page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    scan, _, _ = scan_access(db, user, scan_id)
    data = scan.coverage.get(section, {} if section == "inventory" else [])
    rows = [{"path": path} for path in sorted(data)] if section == "inventory" else data
    return SecurityRecordPage(items=[{**row, "id": str(index)} for index, row in
        enumerate(rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], start=(page - 1) * PAGE_SIZE)],
        total=len(rows), page=page, page_size=PAGE_SIZE)


@router.get("/scans/{scan_id}/assessments", response_model=SecurityAssessmentPage)
def assessments(scan_id: str, db: DB, user: USER, q: Annotated[str, Query(max_length=200)] = "",
    outcome: Annotated[list[Literal["pending", "confirmed", "rejected", "uncertain", "failed", "interrupted", "withheld"]] | None, Query()] = None,
    severity: Annotated[list[Literal["critical", "high", "medium", "low"]] | None, Query()] = None,
    category: Annotated[list[str] | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    scan, _, _ = scan_access(db, user, scan_id)
    rows, summary = security_assessments.read(db, scan)
    severity_counts = Counter(row.proposal.get("severity") for row in rows if row.proposal.get("severity"))
    category_counts = Counter(row.proposal.get("category") for row in rows if row.proposal.get("category"))
    query = q.strip().casefold()
    rows = [row for row in rows if (not outcome or row.outcome in outcome)
        and (not severity or row.proposal.get("severity") in severity)
        and (not category or row.proposal.get("category") in category)
        and (not query or any(query in str(row.proposal.get(key, "")).casefold() for key in ("title", "path", "category")))]
    return SecurityAssessmentPage(items=rows[(page - 1) * page_size:page * page_size], total=len(rows),
        registered_total=summary.total, outcome_counts=summary.outcome_counts, severity_counts=dict(severity_counts),
        category_counts=dict(category_counts), revision=summary.revision, page=page, page_size=page_size)


@router.get("/scans/{scan_id}/assessments/{assessment_id}", response_model=SecurityAssessmentOut)
def assessment_detail(scan_id: str, assessment_id: str, db: DB, user: USER):
    scan, _, _ = scan_access(db, user, scan_id)
    rows, _ = security_assessments.read(db, scan)
    row = next((row for row in rows if row.id == assessment_id), None)
    if row is None:
        raise HTTPException(404, "Security assessment not found.")
    return row


@router.post("/scans/{scan_id}/cancel", response_model=SecurityScanOut)
def cancel_scan(scan_id: str, db: DB, user: USER):
    scan, _, _ = scan_access(db, user, scan_id, write=True)
    scan = db.scalar(select(SecurityScan).where(SecurityScan.id == scan.id).with_for_update())
    repository_security.cancel(db, scan, user)
    return repository_security.public_scan(scan)


@router.post("/scans/{scan_id}/retry", response_model=SecurityScanOut, status_code=202)
def retry_scan(scan_id: str, db: DB, user: USER, body: SecurityScanInput):
    scan, repo, remote = scan_access(db, user, scan_id, write=True)
    if scan.status not in {"partial", "failed", "cancelled", "blocked"}:
        raise HTTPException(409, "Only interrupted or incomplete scans can be retried.")
    if scan.status == "blocked":
        repository_security.cancel(db, scan, user, "Replaced by an explicit retry.")
        db.flush()
    return repository_security.public_scan(repository_security.create_scan(db, repo, user, body, remote, parent=scan))


@router.get("/scans/{scan_id}/report")
def report(scan_id: str, db: DB, user: USER, format: Literal["json", "markdown"] = "json"):
    scan, _, _ = scan_access(db, user, scan_id)
    if not scan.report:
        raise HTTPException(409, "This scan has not produced a saved report yet.")
    content = json.dumps(scan.report, indent=2) if format == "json" else repository_security.markdown_report(scan.report)
    extension = "json" if format == "json" else "md"
    return Response(content, media_type="application/json" if format == "json" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="security-{scan.id}.{extension}"'})


@router.get("/scans/{scan_id}/report/findings", response_model=SecurityReportPage)
def report_findings(scan_id: str, db: DB, user: USER,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    q: Annotated[str, Query(max_length=256)] = "",
    outcome: Annotated[list[Literal["present", "fixed", "not_observed", "uncertain", "unknown"]] | None, Query(max_length=100)] = None,
    severity: Annotated[list[Literal["critical", "high", "medium", "low", "unknown"]] | None, Query(max_length=100)] = None,
    kind: Annotated[list[Annotated[str, Field(min_length=1, max_length=50)]] | None, Query(max_length=100)] = None,
    finding_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
):
    scan, _, _ = scan_access(db, user, scan_id)
    rows = scan.report.get("findings", [])
    counts = {key: dict(Counter(row.get(key) or "unknown" for row in rows)) for key in ("outcome", "severity", "kind")}
    query = q.strip().casefold()
    items = [row for row in rows if (not finding_id or row.get("finding_id") == finding_id)
        and (not outcome or (row.get("outcome") or "unknown") in outcome)
        and (not severity or (row.get("severity") or "unknown") in severity)
        and (not kind or (row.get("kind") or "unknown") in kind)
        and (not query or any(query in str(value or "").casefold() for value in
             (row.get("title"), row.get("path"), row.get("kind"), row.get("evidence", {}).get("explanation"))))]
    return SecurityReportPage(items=items[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], total=len(items),
        registered_total=len(rows), outcome_counts=counts["outcome"], severity_counts=counts["severity"],
        kind_counts=counts["kind"], page=page, page_size=PAGE_SIZE)


def finding_page(db, repository_id, page, state, severity, kind, search, scan_id=None, results=None):
    query = select(SecurityFinding).where(SecurityFinding.repository_id == repository_id)
    result_counts = {}
    if scan_id:
        query = query.join(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan_id)
        result = case(
            (SecurityOccurrence.outcome.in_(["fixed", "not_observed", "uncertain"]), SecurityOccurrence.outcome),
            (SecurityOccurrence.outcome != "present", "recorded"),
            (SecurityFinding.first_scan_id == scan_id, "new"),
            *[(SecurityFinding.state == state, f"{state}_repeat") for state in ("open", "resolved", "false_positive", "accepted_risk")],
            else_="repeated",
        )
        result_counts = dict(db.execute(query.with_only_columns(result, func.count()).group_by(result)).all())
    registered_total = db.scalar(select(func.count()).select_from(query.subquery()))
    counts = {key: dict(db.execute(query.with_only_columns(column, func.count()).group_by(column)).all())
              for key, column in (("state", SecurityFinding.state), ("severity", SecurityFinding.severity), ("kind", SecurityFinding.kind))}
    if scan_id and results:
        query = query.where(result.in_(results))
    if state:
        query = query.where(SecurityFinding.state.in_(state))
    if severity:
        query = query.where(SecurityFinding.severity.in_(severity))
    if kind:
        query = query.where(SecurityFinding.kind.in_(kind))
    if search:
        query = query.where(SecurityFinding.title.icontains(search, autoescape=True) | SecurityFinding.path.icontains(search, autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(SecurityFinding.updated_at.desc(), SecurityFinding.id)
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    return SecurityFindingPage(items=[SecurityFindingOut.model_validate(row) for row in rows], total=total,
                               page=page, page_size=PAGE_SIZE, result_counts=result_counts, registered_total=registered_total,
                               state_counts=counts["state"], severity_counts=counts["severity"], kind_counts=counts["kind"])


@router.get("/repositories/{repository_id}/findings", response_model=SecurityFindingPage)
def findings(repository_id: int, db: DB, user: USER,
             page: Annotated[int, Query(ge=1, le=1000000)] = 1,
             state: Annotated[list[Literal["open", "resolved", "false_positive", "accepted_risk"]] | None, Query(max_length=100)] = None,
             severity: Annotated[list[Literal["critical", "high", "medium", "low", "unknown"]] | None, Query(max_length=100)] = None,
             kind: Annotated[list[Literal["code", "dependency", "secret", "infrastructure"]] | None, Query(max_length=100)] = None,
             search: Annotated[str, Query(max_length=200)] = ""):
    repo_access(db, user, repository_id)
    return finding_page(db, repository_id, page, state, severity, kind, search)


@router.get("/scans/{scan_id}/findings", response_model=SecurityFindingPage)
def scan_findings(scan_id: str, db: DB, user: USER,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    outcome: Annotated[list[Literal["new", "open_repeat", "resolved_repeat", "false_positive_repeat", "accepted_risk_repeat",
                                   "repeated", "recorded", "fixed", "not_observed", "uncertain"]] | None, Query(max_length=100)] = None,
    severity: Annotated[list[Literal["critical", "high", "medium", "low", "unknown"]] | None, Query(max_length=100)] = None,
    kind: Annotated[list[Literal["code", "dependency", "secret", "infrastructure"]] | None, Query(max_length=100)] = None,
    search: Annotated[str, Query(max_length=200)] = "",
):
    scan, _, _ = scan_access(db, user, scan_id)
    return finding_page(db, scan.repository_id, page, None, severity, kind, search, scan_id, outcome)


@router.get("/findings/{finding_id}", response_model=SecurityFindingDetail)
def finding_detail(finding_id: str, db: DB, user: USER):
    finding, repo, remote = finding_access(db, user, finding_id)
    return SecurityFindingDetail(repository=repo_out(db, repo), finding=SecurityFindingOut.model_validate(finding),
        can_write=bool(remote.get("permissions", {}).get("push")),
        observation_count=db.scalar(select(func.count()).select_from(SecurityOccurrence).where(SecurityOccurrence.finding_id == finding.id)),
        activity_count=db.scalar(select(func.count()).select_from(SecurityActivity).where(SecurityActivity.finding_id == finding.id)),
        observations=[{"scan_id": row.scan_id, "outcome": row.outcome, "snapshot": row.snapshot, "created_at": row.created_at.isoformat()}
                      for row in db.scalars(select(SecurityOccurrence).where(SecurityOccurrence.finding_id == finding.id)
                                            .order_by(SecurityOccurrence.created_at.desc()).limit(100))],
        activity=[SecurityActivityOut.model_validate(row) for row in db.scalars(select(SecurityActivity)
            .where(SecurityActivity.finding_id == finding.id).order_by(SecurityActivity.created_at.desc()).limit(200))])


@router.get("/findings/{finding_id}/activity", response_model=SecurityActivityPage)
def finding_activity(finding_id: str, db: DB, user: USER, page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    finding_access(db, user, finding_id)
    return activity_page(db, select(SecurityActivity).where(SecurityActivity.finding_id == finding_id), page)


@router.get("/findings/{finding_id}/observations", response_model=SecurityRecordPage)
def finding_observations(finding_id: str, db: DB, user: USER, page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    finding_access(db, user, finding_id)
    query = select(SecurityOccurrence).where(SecurityOccurrence.finding_id == finding_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(SecurityOccurrence.created_at.desc(), SecurityOccurrence.id.desc())
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    return SecurityRecordPage(items=[{"id": row.id, "scan_id": row.scan_id, "outcome": row.outcome,
        "created_at": row.created_at.isoformat()} for row in rows], total=total, page=page, page_size=PAGE_SIZE)


@router.patch("/findings/{finding_id}", response_model=SecurityFindingOut)
def triage(finding_id: str, body: SecurityTriage, db: DB, user: USER):
    finding, _, _ = finding_access(db, user, finding_id, write=True, lock=True)
    if finding.version != body.version:
        raise HTTPException(409, "The finding changed. Refresh it before updating its state.")
    reason = repository_security.redact_finding_text(db, finding, body.reason.strip())
    if not reason:
        raise ValueError("Enter a reason for this finding decision.")
    finding.state, finding.version, finding.updated_at = body.state, finding.version + 1, now()
    if body.state == "open":
        finding.rotation = None
    repository_security.activity(db, "finding_triaged", finding=finding, actor=user, state=body.state, reason=reason)
    return SecurityFindingOut.model_validate(finding)


@router.post("/findings/{finding_id}/rotation", response_model=SecurityFindingOut)
def rotation(finding_id: str, body: SecurityRotation, db: DB, user: USER):
    finding, _, _ = finding_access(db, user, finding_id, write=True, lock=True)
    if finding.kind != "secret" or finding.state not in {"open", "accepted_risk"}:
        raise HTTPException(409, "Rotation acknowledgment applies to an unresolved exposed-secret finding.")
    if finding.version != body.version or not body.reason.strip():
        raise HTTPException(409, "The finding changed or the rotation acknowledgment is missing a reason.")
    finding.rotation = {"identity": finding.evidence["identity"], "actor_id": user.id,
                        "reason": repository_security.redact_finding_text(db, finding, body.reason.strip()), "acknowledged_at": now().isoformat()}
    finding.version += 1
    repository_security.activity(db, "rotation_acknowledged", finding=finding, actor=user, reason=finding.rotation["reason"])
    return SecurityFindingOut.model_validate(finding)


@router.get("/repositories/{repository_id}/github-issues", response_model=SecurityIssueCandidatePage)
def issue_candidates(repository_id: int, db: DB, user: USER,
                     page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    repo, remote = repo_access(db, user, repository_id, write=True)
    if type(remote.get("private")) is not bool:
        raise HTTPException(502, "GitHub did not confirm repository visibility. Refresh the preview before publishing.")
    query = select(SecurityFinding).where(SecurityFinding.repository_id == repo.id,
        SecurityFinding.state.in_(["open", "accepted_risk"]), SecurityFinding.issue.cast(JSONB) == {})
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(SecurityFinding.updated_at.desc(), SecurityFinding.id)
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    items = [SecurityIssueCandidate(**{**security_issues.preview(repo, finding).model_dump(), "public": not remote["private"]},
             id=finding.id, path=finding.path, severity=finding.severity) for finding in rows]
    return SecurityIssueCandidatePage(items=items, repository=repo.full_name, public=not remote["private"],
                                      total=total, page=page, page_size=PAGE_SIZE)


@router.post("/repositories/{repository_id}/github-issues", response_model=list[SecurityFindingOut], status_code=202)
def create_issues(repository_id: int, body: SecurityBulkIssueInput, db: DB, user: USER):
    repo, remote = repo_access(db, user, repository_id, write=True)
    identities = [draft.finding_id for draft in body.issues]
    if len(set(identities)) != len(identities):
        raise HTTPException(422, "Select each finding only once.")
    if type(remote.get("private")) is not bool or any(draft.public != (not remote["private"]) for draft in body.issues):
        raise HTTPException(409, "Repository visibility changed. Refresh the issue previews and approve them again.")
    findings = {finding.id: finding for finding in db.scalars(select(SecurityFinding).where(
        SecurityFinding.repository_id == repo.id, SecurityFinding.id.in_(identities))
        .order_by(SecurityFinding.id).with_for_update().execution_options(populate_existing=True))}
    if len(findings) != len(identities):
        raise HTTPException(404, "A selected finding is no longer available in this repository. Refresh the issue previews.")
    for draft in body.issues:
        security_issues.create(db, repo, findings[draft.finding_id], user, draft, new_only=True)
    return [SecurityFindingOut.model_validate(findings[identity]) for identity in identities]


@router.get("/findings/{finding_id}/github-issue", response_model=SecurityIssuePreview)
def issue_preview(finding_id: str, db: DB, user: USER):
    finding, repo, remote = finding_access(db, user, finding_id, write=True)
    if (finding.state not in {"open", "accepted_risk"} or finding.issue.get("number")
        or (finding.issue.get("started") and finding.issue.get("sync_status") not in {"uncertain", "failed"})):
        raise HTTPException(409, "Only an unresolved finding without an issue can be published.")
    result = security_issues.preview(repo, finding)
    if type(remote.get("private")) is not bool:
        raise HTTPException(502, "GitHub did not confirm repository visibility. Refresh the preview before publishing.")
    result.public = not remote["private"]
    return result


@router.post("/findings/{finding_id}/github-issue", response_model=SecurityFindingOut, status_code=202)
def create_issue(finding_id: str, body: SecurityIssueInput, db: DB, user: USER):
    finding, repo, remote = finding_access(db, user, finding_id, write=True, lock=True)
    if type(remote.get("private")) is not bool or body.public != (not remote["private"]):
        raise HTTPException(409, "Repository visibility changed. Refresh the issue preview and approve it again.")
    security_issues.create(db, repo, finding, user, body)
    return SecurityFindingOut.model_validate(finding)


@router.post("/findings/{finding_id}/github-issue/sync", response_model=SecurityFindingOut, status_code=202)
def sync_issue(finding_id: str, db: DB, user: USER):
    finding, _, _ = finding_access(db, user, finding_id, write=True, lock=True)
    security_issues.retry(db, finding, user)
    return SecurityFindingOut.model_validate(finding)


@router.post("/findings/{finding_id}/github-issue/reapprove", response_model=SecurityFindingOut, status_code=202)
def reapprove_issue(finding_id: str, body: SecurityIssueReapproval, db: DB, user: USER):
    finding, _, _ = finding_access(db, user, finding_id, write=True, lock=True)
    security_issues.reapprove(db, finding, user, body)
    return SecurityFindingOut.model_validate(finding)
