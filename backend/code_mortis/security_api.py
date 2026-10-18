"""Authenticated repository security endpoints."""

import json
from collections import Counter
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from . import github, repository_security, security_assessments, security_issues
from .api import DB, USER, repo_access, repo_out
from .db import now
from .models import SecurityActivity, SecurityFinding, SecurityOccurrence, SecurityScan
from .schemas import RepositoryOut
from .security_schemas import (
    SecurityActivityOut,
    SecurityAssessmentOut,
    SecurityAssessmentPage,
    SecurityFindingDetail,
    SecurityFindingOut,
    SecurityFindingPage,
    SecurityIssueInput,
    SecurityIssuePreview,
    SecurityRepositoryOut,
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


@router.get("/repositories", response_model=list[SecurityRepositoryOut])
def repositories(db: DB, user: USER):
    repos = github.visible_repositories(db, user)
    summaries = repository_security.summaries(db, [repo.id for repo in repos])
    return [SecurityRepositoryOut(repository=RepositoryOut.model_validate(repo), summary=summaries[repo.id]) for repo in repos]


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
    return SecurityScanDetail(repository=repo_out(db, repo), scan=repository_security.public_scan(scan),
        can_write=bool(remote.get("permissions", {}).get("push")),
        assessment=assessment,
        finding_count=db.scalar(select(func.count()).select_from(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan.id)),
        activity_count=db.scalar(select(func.count()).select_from(SecurityActivity).where(SecurityActivity.scan_id == scan.id)),
        activity=[SecurityActivityOut.model_validate(row) for row in db.scalars(select(SecurityActivity).where(
            SecurityActivity.scan_id == scan.id).order_by(SecurityActivity.created_at.desc()).limit(200))])


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


def finding_page(db, repository_id, page, state, severity, kind, search, scan_id=None):
    query = select(SecurityFinding).where(SecurityFinding.repository_id == repository_id)
    if scan_id:
        query = query.join(SecurityOccurrence).where(SecurityOccurrence.scan_id == scan_id)
    if state:
        query = query.where(SecurityFinding.state == state)
    if severity:
        query = query.where(SecurityFinding.severity == severity)
    if kind:
        query = query.where(SecurityFinding.kind == kind)
    if search:
        query = query.where(SecurityFinding.title.icontains(search, autoescape=True) | SecurityFinding.path.icontains(search, autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(SecurityFinding.updated_at.desc(), SecurityFinding.id)
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    return SecurityFindingPage(items=[SecurityFindingOut.model_validate(row) for row in rows], total=total, page=page, page_size=PAGE_SIZE)


@router.get("/repositories/{repository_id}/findings", response_model=SecurityFindingPage)
def findings(repository_id: int, db: DB, user: USER,
             page: Annotated[int, Query(ge=1, le=1000000)] = 1,
             state: Literal["open", "resolved", "false_positive", "accepted_risk"] | None = None,
             severity: Literal["critical", "high", "medium", "low", "unknown"] | None = None,
             kind: Literal["code", "dependency", "secret", "infrastructure"] | None = None,
             search: Annotated[str, Query(max_length=200)] = ""):
    repo_access(db, user, repository_id)
    return finding_page(db, repository_id, page, state, severity, kind, search)


@router.get("/scans/{scan_id}/findings", response_model=SecurityFindingPage)
def scan_findings(scan_id: str, db: DB, user: USER, page: Annotated[int, Query(ge=1, le=1000000)] = 1):
    scan, _, _ = scan_access(db, user, scan_id)
    return finding_page(db, scan.repository_id, page, None, None, None, "", scan_id)


@router.get("/findings/{finding_id}", response_model=SecurityFindingDetail)
def finding_detail(finding_id: str, db: DB, user: USER):
    finding, repo, remote = finding_access(db, user, finding_id)
    return SecurityFindingDetail(repository=repo_out(db, repo), finding=SecurityFindingOut.model_validate(finding),
        can_write=bool(remote.get("permissions", {}).get("push")),
        observations=[{"scan_id": row.scan_id, "outcome": row.outcome, "snapshot": row.snapshot, "created_at": row.created_at.isoformat()}
                      for row in db.scalars(select(SecurityOccurrence).where(SecurityOccurrence.finding_id == finding.id)
                                            .order_by(SecurityOccurrence.created_at.desc()).limit(100))],
        activity=[SecurityActivityOut.model_validate(row) for row in db.scalars(select(SecurityActivity)
            .where(SecurityActivity.finding_id == finding.id).order_by(SecurityActivity.created_at.desc()).limit(200))])


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


@router.get("/findings/{finding_id}/github-issue", response_model=SecurityIssuePreview)
def issue_preview(finding_id: str, db: DB, user: USER):
    finding, repo, remote = finding_access(db, user, finding_id, write=True)
    if finding.state not in {"open", "accepted_risk"} or finding.issue.get("number") or finding.issue.get("started"):
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
