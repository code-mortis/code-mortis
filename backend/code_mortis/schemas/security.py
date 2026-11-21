"""Public security records and strict model output contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from code_mortis.schemas.common import (
    ORMModel,
    RepositoryOut,
    ReviewComment,
    ScanLimits,
    SecuritySummary,
    StrictModel,
)

ISSUE_BODY_LIMIT = 50000


class SecurityScanInput(ScanLimits):
    """Use saved limits by default. Explicit limits are supported for existing API clients."""

    connection_id: str = Field(min_length=1, max_length=36)
    model: str = Field(default="", max_length=150)
    concurrency: int = Field(default_factory=lambda: 4, ge=1, le=8, strict=True,
                             description="Legacy override. Omit to use saved limits.")
    request_key: str = Field(min_length=16, max_length=100)


class SecurityCitation(StrictModel):
    revision: Literal["original", "current"] = "current"
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=20000)

    _path = field_validator("path")(ReviewComment.relative_path)


class SecurityProposal(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    severity: Literal["critical", "high", "medium", "low"]
    path: str
    explanation: str = Field(min_length=1, max_length=12000)
    trigger: str = Field(min_length=1, max_length=4000)
    impact: str = Field(min_length=1, max_length=4000)
    remediation: str = Field(min_length=1, max_length=8000)
    verification: str = Field(min_length=1, max_length=4000)
    evidence: list[SecurityCitation] = Field(min_length=1, max_length=20)

    _path = field_validator("path")(ReviewComment.relative_path)


class SecurityAnalysis(StrictModel):
    findings: list[SecurityProposal] = Field(max_length=100)
    required_paths: list[str] = Field(max_length=10)


class SecurityAssessment(StrictModel):
    verdict: Literal["confirmed", "rejected", "uncertain"]
    finding_id: str | None
    explanation: str = Field(min_length=1, max_length=12000)
    evidence: list[SecurityCitation] = Field(max_length=20)
    required_paths: list[str] = Field(max_length=10)


class SecurityFix(StrictModel):
    verdict: Literal["fixed", "still_present", "uncertain"]
    explanation: str = Field(min_length=1, max_length=12000)
    change_explanation: str = Field(max_length=12000)
    original_issue_present: bool
    evidence: list[SecurityCitation] = Field(max_length=20)
    required_paths: list[str] = Field(max_length=10)


class SecurityEvidenceSelection(StrictModel):
    source_id: str = Field(min_length=1, max_length=40)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class SecurityProposalSelection(SecurityProposal):
    evidence: list[SecurityEvidenceSelection] = Field(min_length=1, max_length=20)


class SecurityAnalysisSelection(SecurityAnalysis):
    findings: list[SecurityProposalSelection] = Field(max_length=100)


class SecurityAssessmentSelection(SecurityAssessment):
    evidence: list[SecurityEvidenceSelection] = Field(max_length=20)


class SecurityFixSelection(SecurityFix):
    evidence: list[SecurityEvidenceSelection] = Field(max_length=20)


class SecurityScanSummary(ORMModel):
    id: str
    branch: str
    head_sha: str
    status: str
    grade: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SecurityScanOut(SecurityScanSummary):
    repository_id: int
    actor_id: int
    connection_id: str | None
    parent_id: str | None
    config: dict[str, Any]
    stages: dict[str, Any]
    progress: dict[str, Any]
    coverage: dict[str, Any]
    report: dict[str, Any]
    error: str | None


class SecurityFindingOut(ORMModel):
    id: str
    repository_id: int
    state: str
    kind: str
    severity: str
    title: str
    path: str
    first_scan_id: str
    last_scan_id: str
    evidence: dict[str, Any]
    version: int
    rotation: dict[str, Any] | None
    issue: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SecurityActivityOut(ORMModel):
    id: str
    scan_id: str | None
    finding_id: str | None
    finding_title: str | None = None
    finding_path: str | None = None
    actor_id: int | None
    action: str
    detail: dict[str, Any]
    created_at: datetime


class SecurityRepositoryOut(StrictModel):
    repository: RepositoryOut
    summary: SecuritySummary
    can_write: bool | None = None
    finding_count: int | None = Field(default=None, ge=0)
    finding_state_counts: dict[str, int] = Field(default_factory=dict)
    scan_count: int | None = Field(default=None, ge=0)


class SecurityAssessmentOut(StrictModel):
    id: str
    revision: str = ""
    proposal: dict[str, Any]
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    progress_phase: Literal["queued", "checking", "validating", "finished"]
    outcome: Literal["pending", "confirmed", "rejected", "uncertain", "failed", "interrupted", "withheld"]
    result: dict[str, Any] | None = None
    error: str | None = None
    tokens: int | None = None
    reused: bool = False
    finding_id: str | None = None


class SecurityAssessmentSummary(StrictModel):
    total: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    revision: str = ""


class SecurityAssessmentPage(StrictModel):
    items: list[SecurityAssessmentOut]
    total: int
    registered_total: int
    outcome_counts: dict[str, int]
    severity_counts: dict[str, int]
    category_counts: dict[str, int]
    revision: str
    page: int
    page_size: int


class SecurityScanDetail(StrictModel):
    repository: RepositoryOut
    scan: SecurityScanOut
    can_write: bool
    assessment: SecurityAssessmentSummary = Field(default_factory=SecurityAssessmentSummary)
    finding_count: int = Field(ge=0)
    finding_state_counts: dict[str, int] = Field(default_factory=dict)
    report_count: int = Field(default=0, ge=0)
    resolved_count: int = Field(default=0, ge=0)
    activity_count: int = Field(ge=0)
    activity: list[SecurityActivityOut]


class SecurityFindingDetail(StrictModel):
    repository: RepositoryOut
    finding: SecurityFindingOut
    can_write: bool
    observation_count: int = 0
    activity_count: int = 0
    observations: list[dict[str, Any]]
    activity: list[SecurityActivityOut]


class SecurityScanPage(StrictModel):
    items: list[SecurityScanOut]
    total: int
    page: int
    page_size: int


class SecurityScanSummaryPage(StrictModel):
    items: list[SecurityScanSummary]
    total: int
    page: int
    page_size: int


class SecurityRepositoryPage(StrictModel):
    items: list[SecurityRepositoryOut]
    total: int
    registered_total: int
    grade_counts: dict[str, int]
    status_counts: dict[str, int]
    page: int
    page_size: int


class SecurityActivityPage(StrictModel):
    items: list[SecurityActivityOut]
    total: int
    page: int
    page_size: int


class SecurityRecordPage(StrictModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class SecurityFindingPage(StrictModel):
    items: list[SecurityFindingOut]
    total: int
    page: int
    page_size: int
    result_counts: dict[str, int] = Field(default_factory=dict)
    registered_total: int = 0
    state_counts: dict[str, int] = Field(default_factory=dict)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    kind_counts: dict[str, int] = Field(default_factory=dict)


class SecurityReportPage(StrictModel):
    items: list[dict[str, Any]]
    total: int
    registered_total: int
    state_counts: dict[str, int]
    outcome_counts: dict[str, int]
    severity_counts: dict[str, int]
    kind_counts: dict[str, int]
    page: int
    page_size: int


class SecurityTriage(StrictModel):
    state: Literal["open", "false_positive", "accepted_risk"]
    reason: str = Field(min_length=1, max_length=4000)
    version: int = Field(ge=0)


class SecurityRotation(StrictModel):
    reason: str = Field(min_length=1, max_length=4000)
    version: int = Field(ge=0)


class SecurityIssueInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=ISSUE_BODY_LIMIT)
    version: int = Field(ge=0)
    public: bool
    recovery_token: str | None = Field(default=None, min_length=1, max_length=36)


class SecurityIssuePreview(StrictModel):
    title: str
    body: str = Field(max_length=ISSUE_BODY_LIMIT)
    repository: str
    public: bool
    version: int
    recovery_token: str | None = None


class SecurityIssueReapproval(StrictModel):
    version: int = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=36)
    approval_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=4000)


class SecurityIssueCandidate(SecurityIssuePreview):
    id: str
    path: str
    severity: str


class SecurityIssueCandidatePage(StrictModel):
    items: list[SecurityIssueCandidate]
    repository: str
    public: bool
    total: int
    page: int
    page_size: int


class SecurityIssueDraft(SecurityIssueInput):
    finding_id: str = Field(min_length=1, max_length=36)


class SecurityBulkIssueInput(StrictModel):
    issues: list[SecurityIssueDraft] = Field(min_length=1, max_length=100)
