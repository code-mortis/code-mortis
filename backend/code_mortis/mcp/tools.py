"""Explicit MCP tools for the reviews, security, and management domains.

Every tool delegates to the existing route handlers and services in
``api/routers/`` and the workflow packages so GitHub
permission checks, public serializers, pagination, version conflicts,
and credential redaction behave exactly as in the frontend. No generic
proxy or OpenAPI exposure: each operation is a named, typed tool.

Execution always goes through ``mcp.auth.invoke_tool``, which owns
authentication, request keys, audit, and
provenance. Management tools additionally require the current
administrator or owner checks; ``ADMIN`` route annotations are FastAPI
dependencies and do not run on direct calls, so those checks are
repeated explicitly here.

Excluded on purpose (see docs/mcp-plan.md): raw console tickets and the
console socket, browser login/setup flows, webhooks, unsaved-connection
model discovery and credential-bearing connection fields (credentials
are entered in the browser), and bearer-in-URL report downloads (use
the bounded ``security_get_report`` instead).
"""
import functools
import json
from contextvars import ContextVar
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from code_mortis.api import dependencies as dependencies_api
from code_mortis.api.routers import connections as connections_routes
from code_mortis.api.routers import findings as findings_routes
from code_mortis.api.routers import installations as installations_routes
from code_mortis.api.routers import members as members_routes
from code_mortis.api.routers import pulls as pulls_routes
from code_mortis.api.routers import repositories as repositories_routes
from code_mortis.api.routers import runs as runs_routes
from code_mortis.api.routers import security as security_routes
from code_mortis.api.routers import settings as settings_routes
from code_mortis.api.routers import subscriptions as connection_routes
from code_mortis.core.config import settings
from code_mortis.core.ordering import FindingOrdering, RunFindingOrdering, SnapshotOrdering
from code_mortis.core.security import digest
from code_mortis.mcp.auth import invoke_tool, tool_access
from code_mortis.models import ReviewRun
from code_mortis.reviews.analytics import Scope as AnalyticsScope
from code_mortis.reviews.analytics import findings as analytics_findings
from code_mortis.reviews.analytics import overview as analytics_overview
from code_mortis.reviews.analytics import reviews as analytics_reviews
from code_mortis.reviews.analytics import usage as analytics_usage
from code_mortis.schemas.analytics import AnalyticsFindingFilters
from code_mortis.schemas.common import (
    BackgroundInput,
    CandidateFilters,
    ConnectionInput,
    FindingAction,
    FindingFilters,
    IgnorePullInput,
    InboxFilters,
    MemberInput,
    RepoSettings,
    RepositoryInput,
    RepositoryPullFilters,
    ResolvedFilters,
    RunFindingFilters,
    RunLimits,
    RunOut,
    StartRunInput,
    StrictModel,
)
from code_mortis.schemas.security import (
    SecurityBulkIssueInput,
    SecurityIssueDraft,
    SecurityIssueInput,
    SecurityIssueReapproval,
    SecurityRotation,
    SecurityScanInput,
    SecurityTriage,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True)
REPORT_MAX_CHARS = 32000

# The endpoint a management tool is mounted on (reviews or security).
# invoke_tool rejects a domain that does not match the grant, so management
# handlers resolve through this instead of a literal domain name.
_MOUNT: ContextVar[str] = ContextVar("mcp_mount_domain", default="reviews")


def _require_admin(user) -> None:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access is required.")


def _dump(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _run(domain, scope, name, arguments, callback, *, request_key=None, approval=False):
    if domain == "management":
        domain = _MOUNT.get()
    return invoke_tool(
        domain, scope, name, arguments, lambda db, user: _dump(callback(db, user)),
        request_key=request_key, approval=approval,
    )


def _register(server, fn, name, scope, description, *, read_only=False):
    server.add_tool(
        fn, name=name, description=description, meta={"scope": scope},
        annotations=READ_ONLY if read_only else None, structured_output=True,
    )


# Shared typed inputs. Filter models subclass the route filters so
# multi-select, search, ordering, and pagination semantics are preserved.


class RepositoryScopedInput(StrictModel):
    repository_id: int = Field(gt=0, le=9007199254740991)


class PullScopedInput(StrictModel):
    pr_id: str = Field(min_length=1, max_length=36)


class RunScopedInput(StrictModel):
    run_id: str = Field(min_length=1, max_length=36)


class FindingScopedInput(StrictModel):
    finding_id: str = Field(min_length=1, max_length=36)


class ScanScopedInput(StrictModel):
    scan_id: str = Field(min_length=1, max_length=36)


class RepositoryPullsInput(RepositoryPullFilters):
    repository_id: int = Field(gt=0, le=9007199254740991)


class PullFindingsInput(FindingFilters):
    pr_id: str = Field(min_length=1, max_length=36)


class RunFindingsInput(RunFindingFilters):
    run_id: str = Field(min_length=1, max_length=36)


class ResolvedInput(ResolvedFilters):
    run_id: str = Field(min_length=1, max_length=36)


class CandidatesInput(CandidateFilters):
    run_id: str = Field(min_length=1, max_length=36)


class AnalyticsInput(AnalyticsFindingFilters):
    section: Literal["overview", "findings", "reviews", "usage"] = "overview"


class MutationInput(StrictModel):
    request_key: str = Field(min_length=16, max_length=100)


class ApprovalInput(MutationInput):
    approval_id: str | None = Field(default=None, min_length=1, max_length=36)


class ManagementConnectionInput(ApprovalInput):
    """Connection fields without credentials; secrets are entered in the browser."""

    kind: Literal["api", "chatgpt", "claude_code"] = "api"
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="", max_length=100, pattern=r"^[a-z0-9-]*$")
    protocol: Literal["openai", "openai-responses", "anthropic", "anthropic-bedrock", "native"] = "native"
    base_url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=150)
    options: dict[str, Any] = Field(default_factory=dict)
    request_key: str = Field(min_length=16, max_length=100)

    def to_connection_input(self) -> ConnectionInput:
        data = self.model_dump(exclude={"request_key", "approval_id", "connection_id"})
        return ConnectionInput.model_validate({**data, "api_key": None, "extra_headers": None})


# Reviews domain.


def reviews_list_repositories() -> dict[str, Any]:
    """List review repositories selected for this client."""
    def callback(db, user):
        return [repo.model_copy(update={"security": None}) for repo in repositories_routes.repositories(db, user)]

    return _run("reviews", "reviews:read", "reviews_list_repositories", {},
                callback)


def reviews_list_pull_requests(params: InboxFilters) -> dict[str, Any]:
    """Search the review inbox across visible repositories."""
    return _run("reviews", "reviews:read", "reviews_list_pull_requests", params.model_dump(),
                lambda db, user: pulls_routes.pull_inbox(db, user, params))


def reviews_list_repository_pulls(params: RepositoryPullsInput) -> dict[str, Any]:
    """List pull requests for one repository with inbox filters."""
    return _run("reviews", "reviews:read", "reviews_list_repository_pulls", params.model_dump(),
                lambda db, user: repositories_routes.pull_requests(params.repository_id, db, user, params))


def reviews_get_pull_request(params: PullScopedInput) -> dict[str, Any]:
    """Inspect a pull request, its latest review, counts, and write capability."""
    return _run("reviews", "reviews:read", "reviews_get_pull_request", params.model_dump(),
                lambda db, user: pulls_routes.pull_detail(params.pr_id, db, user))


def reviews_list_runs(params: PullScopedInput, page: int = 1) -> dict[str, Any]:
    """List review runs for a pull request, newest first."""
    return _run("reviews", "reviews:read", "reviews_list_runs", {"pr_id": params.pr_id, "page": page},
                lambda db, user: pulls_routes.pull_runs(params.pr_id, db, user, max(1, page)))


def reviews_get_run(params: RunScopedInput, ordering: RunFindingOrdering = "-severity") -> dict[str, Any]:
    """Inspect review status and stages; fetch findings and larger saved sections separately."""
    args = {"run_id": params.run_id, "ordering": ordering}

    def callback(db, user):
        detail = runs_routes.run_detail(params.run_id, db, user, ordering)
        result = detail.model_dump(mode="json", exclude={"occurrences"})
        result["occurrence_count"] = len(detail.occurrences)
        result["run"].pop("report", None)
        result["run"].pop("progress", None)
        result["saved_sections_tool"] = "reviews_get_run_section"
        return result

    return _run("reviews", "reviews:read", "reviews_get_run", args,
                callback)


class RunSectionInput(RunScopedInput):
    section: Literal["report", "progress", "config", "summary", "stages"]
    offset: int = Field(default=0, ge=0)


def reviews_get_run_section(params: RunSectionInput) -> dict[str, Any]:
    """Read a complete saved public run section in bounded JSON chunks."""
    def callback(db, user):
        run = db.get(ReviewRun, params.run_id)
        if not run:
            raise HTTPException(404, "Review run not found.")
        dependencies_api.pr_access(db, user, run.pr_id)
        content = json.dumps(RunOut.model_validate(run).model_dump(mode="json")[params.section], ensure_ascii=False)
        end = min(len(content), params.offset + REPORT_MAX_CHARS)
        return {"run_id": run.id, "section": params.section, "offset": params.offset,
            "content": content[params.offset:end], "total_chars": len(content),
            "next_offset": end if end < len(content) else None}

    return _run("reviews", "reviews:read", "reviews_get_run_section", params.model_dump(), callback)


def reviews_list_findings(params: PullFindingsInput) -> dict[str, Any]:
    """List current findings for a pull request with status and severity filters."""
    return _run("reviews", "reviews:read", "reviews_list_findings", params.model_dump(),
                lambda db, user: pulls_routes.pull_findings(params.pr_id, db, user, params))


def reviews_get_finding(params: FindingScopedInput) -> dict[str, Any]:
    """Inspect a finding with its activity and verification history."""
    return _run("reviews", "reviews:read", "reviews_get_finding", params.model_dump(),
                lambda db, user: findings_routes.finding_detail(params.finding_id, db, user))


def reviews_list_run_findings(params: RunFindingsInput) -> dict[str, Any]:
    """List immutable findings saved on a review run."""
    return _run("reviews", "reviews:read", "reviews_list_run_findings", params.model_dump(),
                lambda db, user: runs_routes.run_saved_findings(params.run_id, db, user, params))


class GetRunFindingInput(StrictModel):
    run_id: str = Field(min_length=1, max_length=36)
    finding_id: str = Field(min_length=1, max_length=36)
    status: Literal["open", "resolved"] = "open"


def reviews_get_run_finding(params: GetRunFindingInput) -> dict[str, Any]:
    """Inspect one immutable finding occurrence saved on a review run."""
    args = params.model_dump()
    return _run("reviews", "reviews:read", "reviews_get_run_finding", args,
                lambda db, user: runs_routes.run_saved_finding(
                    params.run_id, params.finding_id, db, user, params.status))


def reviews_list_resolved(params: ResolvedInput) -> dict[str, Any]:
    """List findings resolved on a review run."""
    return _run("reviews", "reviews:read", "reviews_list_resolved", params.model_dump(),
                lambda db, user: runs_routes.run_resolved(params.run_id, db, user, params))


def reviews_list_candidates(params: CandidatesInput) -> dict[str, Any]:
    """List assessment candidates for a review run."""
    return _run("reviews", "reviews:read", "reviews_list_candidates", params.model_dump(),
                lambda db, user: runs_routes.run_candidates(params.run_id, db, user, params))


class GetCandidateInput(StrictModel):
    run_id: str = Field(min_length=1, max_length=36)
    candidate_id: str = Field(min_length=1, max_length=36)


def reviews_get_candidate(params: GetCandidateInput) -> dict[str, Any]:
    """Inspect one assessment candidate."""
    args = params.model_dump()
    return _run("reviews", "reviews:read", "reviews_get_candidate", args,
                lambda db, user: runs_routes.candidate_detail(params.run_id, params.candidate_id, db, user))


def reviews_get_analytics(params: AnalyticsInput) -> dict[str, Any]:
    """Review analytics for visible repositories: overview, findings, reviews, or usage."""
    readers = {"overview": analytics_overview, "findings": analytics_findings,
               "reviews": analytics_reviews, "usage": analytics_usage}
    return _run("reviews", "reviews:read", "reviews_get_analytics", params.model_dump(),
                lambda db, user: readers[params.section](AnalyticsScope(db, user, params)))


def reviews_list_connections(repository_id: int | None = None) -> dict[str, Any]:
    """List safe connection choices; secrets are never returned."""
    return _run("reviews", "reviews:read", "reviews_list_connections", {"repository_id": repository_id},
                lambda db, user: connections_routes.connections(db, user, repository_id))


class ConnectionModelsInput(StrictModel):
    connection_id: str = Field(min_length=1, max_length=36)
    repository_id: int | None = Field(default=None, gt=0, le=9007199254740991)


def reviews_get_connection_models(params: ConnectionModelsInput) -> dict[str, Any]:
    """List models for a saved connection."""
    return _run("reviews", "reviews:read", "reviews_get_connection_models", params.model_dump(),
                lambda db, user: connections_routes.connection_models(
                    params.connection_id, db, user, params.repository_id))


def reviews_list_providers() -> dict[str, Any]:
    """List supported providers for connection setup."""
    return _run("reviews", "reviews:read", "reviews_list_providers", {},
                lambda db, user: connections_routes.providers(user))


class FindingStatusInput(FindingScopedInput, FindingAction, MutationInput):
    pass


def reviews_set_finding_status(params: FindingStatusInput) -> dict[str, Any]:
    """Resolve, reopen, or dismiss a finding; dismissing requires a reason."""
    body = FindingAction(status=params.status, reason=params.reason)
    args = params.model_dump()
    return _run("reviews", "reviews:write", "reviews_set_finding_status", args,
                lambda db, user: findings_routes.update_finding(params.finding_id, body, db, user),
                request_key=params.request_key)


class SyncFindingStatusInput(FindingScopedInput, MutationInput):
    pass


def reviews_sync_finding_status(params: SyncFindingStatusInput) -> dict[str, Any]:
    """Retry GitHub status synchronization for a finding with a pending update."""
    args = params.model_dump()
    return _run("reviews", "reviews:write", "reviews_sync_finding_status", args,
                lambda db, user: findings_routes.retry_finding_sync(params.finding_id, db, user),
                request_key=params.request_key)


class RetryPublicationInput(RunScopedInput, MutationInput):
    pass


def reviews_retry_publication(params: RetryPublicationInput) -> dict[str, Any]:
    """Retry failed or partial GitHub comment publication for a run."""
    return _run("reviews", "reviews:write", "reviews_retry_publication", params.model_dump(),
                lambda db, user: runs_routes.retry_publication(params.run_id, db, user),
                request_key=params.request_key)


class SyncSummaryInput(PullScopedInput, MutationInput):
    pass


def reviews_sync_summary(params: SyncSummaryInput) -> dict[str, Any]:
    """Retry failed PR overview synchronization with GitHub."""
    return _run("reviews", "reviews:write", "reviews_sync_summary", params.model_dump(),
                lambda db, user: pulls_routes.retry_summary_sync(params.pr_id, db, user),
                request_key=params.request_key)


class SetPullIgnoredInput(PullScopedInput, MutationInput):
    ignored: bool = False


def reviews_set_pull_ignored(params: SetPullIgnoredInput) -> dict[str, Any]:
    """Ignore or restore a pull request; ignoring stops pending work."""
    body = IgnorePullInput(ignored=params.ignored)
    return _run("reviews", "reviews:write", "reviews_set_pull_ignored", params.model_dump(),
                lambda db, user: pulls_routes.ignore_pull(params.pr_id, body, db, user),
                request_key=params.request_key)


class RefreshRepositoryInput(RepositoryScopedInput, MutationInput):
    pass


def reviews_refresh_repository(params: RefreshRepositoryInput) -> dict[str, Any]:
    """Refresh repository data from GitHub."""
    return _run("reviews", "reviews:write", "reviews_refresh_repository", params.model_dump(),
                lambda db, user: repositories_routes.refresh_repository(params.repository_id, db, user),
                request_key=params.request_key)


class StartReviewInput(PullScopedInput, MutationInput):
    connection_id: str | None = Field(default=None, min_length=1, max_length=36)


def reviews_start_review(params: StartReviewInput) -> dict[str, Any]:
    """Start a manual review; returns the saved run promptly. May publish GitHub comments."""
    body = StartRunInput(connection_id=params.connection_id)
    return _run("reviews", "reviews:run", "reviews_start_review", params.model_dump(),
                lambda db, user: pulls_routes.start_run(params.pr_id, db, user, body),
                request_key=params.request_key)


class CancelRunInput(RunScopedInput, MutationInput):
    pass


def reviews_cancel_run(params: CancelRunInput) -> dict[str, Any]:
    """Stop one queued, running, or blocked review."""
    return _run("reviews", "reviews:run", "reviews_cancel_run", params.model_dump(),
                lambda db, user: runs_routes.cancel_run(params.run_id, db, user),
                request_key=params.request_key)


class CancelPullReviewsInput(PullScopedInput, MutationInput):
    pass


def reviews_cancel_pull_reviews(params: CancelPullReviewsInput) -> dict[str, Any]:
    """Stop all cancellable reviews for a pull request."""
    return _run("reviews", "reviews:run", "reviews_cancel_pull_reviews", params.model_dump(),
                lambda db, user: pulls_routes.cancel_pull_reviews(params.pr_id, db, user),
                request_key=params.request_key)


class RetryReviewInput(RunScopedInput, MutationInput):
    pass


def reviews_retry_review(params: RetryReviewInput) -> dict[str, Any]:
    """Resume a blocked review; only blocked runs can resume."""
    return _run("reviews", "reviews:run", "reviews_retry_review", params.model_dump(),
                lambda db, user: runs_routes.retry_review(params.run_id, db, user),
                request_key=params.request_key)


class RetryAssessmentInput(RunScopedInput, MutationInput):
    candidate_id: str = Field(min_length=1, max_length=36)


def reviews_retry_assessment(params: RetryAssessmentInput) -> dict[str, Any]:
    """Retry a failed assessment or history conflict on a finished, published run."""
    return _run("reviews", "reviews:run", "reviews_retry_assessment", params.model_dump(),
                lambda db, user: runs_routes.retry_assessment(params.run_id, params.candidate_id, db, user),
                request_key=params.request_key)


def reviews_retry_summary_generation(params: RetryReviewInput) -> dict[str, Any]:
    """Retry overview generation for a run."""
    return _run("reviews", "reviews:run", "reviews_retry_summary_generation", params.model_dump(),
                lambda db, user: runs_routes.retry_summary_generation(params.run_id, db, user),
                request_key=params.request_key)


class GenerateBackgroundInput(RepositoryScopedInput, MutationInput):
    connection_id: str = Field(min_length=1, max_length=36)
    model: str = Field(default="", max_length=150)
    limits: RunLimits | None = None


def reviews_generate_background(params: GenerateBackgroundInput) -> dict[str, Any]:
    """Generate repository background with the selected connection. Administrator only."""
    body = BackgroundInput(connection_id=params.connection_id, model=params.model, limits=params.limits)

    def callback(db, user):
        _require_admin(user)
        return repositories_routes.generate_background(params.repository_id, body, db, user)

    return _run("reviews", "reviews:run", "reviews_generate_background", params.model_dump(),
                callback, request_key=params.request_key)


class BackgroundStatusInput(RepositoryScopedInput):
    job_id: str = Field(min_length=1, max_length=36)


def reviews_get_background_status(params: BackgroundStatusInput) -> dict[str, Any]:
    """Inspect a background generation job. Administrator only."""
    def callback(db, user):
        _require_admin(user)
        return repositories_routes.background_status(params.repository_id, params.job_id, db, user)

    return _run("reviews", "reviews:read", "reviews_get_background_status", params.model_dump(), callback)


class UpdateRepositoryInput(RepositoryScopedInput, MutationInput):
    enabled: bool = False
    connection_id: str | None = Field(default=None, min_length=1, max_length=36)
    settings: RepoSettings = Field(default_factory=RepoSettings)


def reviews_update_repository_config(params: UpdateRepositoryInput) -> dict[str, Any]:
    """Change review configuration for a repository. Administrator only."""
    body = RepositoryInput(enabled=params.enabled, connection_id=params.connection_id, settings=params.settings)

    def callback(db, user):
        _require_admin(user)
        if params.enabled:
            tool_access(db, "reviews", "reviews:run", params.repository_id)
        return repositories_routes.update_repository(params.repository_id, body, db, user)

    return _run("reviews", "reviews:manage", "reviews_update_repository_config", params.model_dump(), callback,
                request_key=params.request_key)


# Security domain.


class SecurityRepositoriesInput(StrictModel):
    page: int = Field(default=1, ge=1, le=1000000)
    search: str = Field(default="", max_length=200)
    grade: list[Literal["A", "B", "C", "D", "F", "unrated"]] | None = Field(default=None, max_length=100)
    status: list[Literal["not_scanned", "current", "incomplete", "unavailable", "outdated"]] | None = Field(
        default=None, max_length=100)
    ordering: Literal["updated_at", "-updated_at", "repository", "-repository"] = "-updated_at"


def security_list_repositories(params: SecurityRepositoriesInput) -> dict[str, Any]:
    """List security repositories with grades, freshness, and counts."""
    args = params.model_dump()
    return _run("security", "security:read", "security_list_repositories", args,
                lambda db, user: security_routes.repositories(
                    db, user, params.page, params.search, params.grade, params.status, params.ordering))


def security_get_repository(params: RepositoryScopedInput) -> dict[str, Any]:
    """Inspect one security repository with finding and scan counts."""
    return _run("security", "security:read", "security_get_repository", params.model_dump(),
                lambda db, user: security_routes.repository(params.repository_id, db, user))


class RepositoryScansInput(RepositoryScopedInput):
    page: int = Field(default=1, ge=1, le=1000000)


def security_list_scans(params: RepositoryScansInput) -> dict[str, Any]:
    """List security scans for a repository, newest first."""
    return _run("security", "security:read", "security_list_scans", params.model_dump(),
                lambda db, user: security_routes.scans(params.repository_id, db, user, params.page))


def security_get_scan(params: ScanScopedInput) -> dict[str, Any]:
    """Inspect a scan: state, stages, errors, usage, pinned commit, and bounded activity."""
    return _run("security", "security:read", "security_get_scan", params.model_dump(),
                lambda db, user: security_routes.scan_detail(params.scan_id, db, user))


class ScanPageInput(ScanScopedInput):
    page: int = Field(default=1, ge=1, le=1000000)


def security_get_scan_activity(params: ScanPageInput) -> dict[str, Any]:
    """List scan lifecycle activity for a scan."""
    return _run("security", "security:read", "security_get_scan_activity", params.model_dump(),
                lambda db, user: security_routes.scan_activity(params.scan_id, db, user, params.page))


class ScanCoverageInput(ScanPageInput):
    section: Literal["inventory", "excluded", "units", "problems"] = "inventory"


def security_get_scan_coverage(params: ScanCoverageInput) -> dict[str, Any]:
    """Inspect one coverage section for a scan; use detail pages for large sections."""
    return _run("security", "security:read", "security_get_scan_coverage", params.model_dump(),
                lambda db, user: security_routes.scan_coverage(params.scan_id, params.section, db, user, params.page))


class AssessmentsInput(ScanScopedInput):
    q: str = Field(default="", max_length=200)
    outcome: list[Literal["pending", "confirmed", "rejected", "uncertain", "failed", "interrupted", "withheld"]] | None = None
    severity: list[Literal["critical", "high", "medium", "low"]] | None = None
    category: list[str] | None = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=10, ge=1, le=100)
    ordering: SnapshotOrdering = "-severity"


def security_list_assessments(params: AssessmentsInput) -> dict[str, Any]:
    """List assessment proposals for a scan with outcome filters."""
    args = params.model_dump()
    return _run("security", "security:read", "security_list_assessments", args,
                lambda db, user: security_routes.assessments(
                    params.scan_id, db, user, params.q, params.outcome, params.severity,
                    params.category, params.page, params.page_size, params.ordering))


class AssessmentInput(ScanScopedInput):
    assessment_id: str = Field(min_length=1, max_length=36)


def security_get_assessment(params: AssessmentInput) -> dict[str, Any]:
    """Inspect one assessment proposal."""
    return _run("security", "security:read", "security_get_assessment", params.model_dump(),
                lambda db, user: security_routes.assessment_detail(params.scan_id, params.assessment_id, db, user))


class SecurityFindingsInput(StrictModel):
    repository_id: int | None = Field(default=None, gt=0, le=9007199254740991)
    scan_id: str | None = Field(default=None, min_length=1, max_length=36)
    page: int = Field(default=1, ge=1, le=1000000)
    state: list[Literal["open", "resolved", "false_positive", "accepted_risk"]] | None = Field(
        default=None, max_length=4)
    severity: list[Literal["critical", "high", "medium", "low", "unknown"]] | None = Field(
        default=None, max_length=5)
    kind: list[Literal["code", "dependency", "secret", "infrastructure"]] | None = Field(
        default=None, max_length=4)
    search: str = Field(default="", max_length=200)
    outcome: list[Literal["new", "open_repeat", "resolved_repeat", "false_positive_repeat",
                          "accepted_risk_repeat", "repeated", "recorded", "fixed", "not_observed",
                          "uncertain"]] | None = Field(default=None, max_length=10)
    resolved: bool | None = None
    ordering: SnapshotOrdering | FindingOrdering = "-severity"


def security_list_findings(params: SecurityFindingsInput) -> dict[str, Any]:
    """List findings for a repository or a scan snapshot with supported filters."""
    if bool(params.repository_id) == bool(params.scan_id):
        raise HTTPException(422, "Choose exactly one of repository_id or scan_id.")
    args = params.model_dump()

    def callback(db, user):
        if params.repository_id is not None:
            return security_routes.findings(
                params.repository_id, db, user, params.page, params.state,
                params.severity, params.kind, params.search, params.ordering)
        return security_routes.scan_findings(
            params.scan_id, db, user, params.page, params.state, params.outcome,
            params.severity, params.kind, params.search, params.ordering, params.resolved)

    return _run("security", "security:read", "security_list_findings", args, callback)


def security_get_finding(params: FindingScopedInput) -> dict[str, Any]:
    """Discuss a finding: evidence, remediation, bounded observations and activity."""
    return _run("security", "security:read", "security_get_finding", params.model_dump(),
                lambda db, user: security_routes.finding_detail(params.finding_id, db, user))


class FindingPageInput(FindingScopedInput):
    page: int = Field(default=1, ge=1, le=1000000)


def security_get_finding_activity(params: FindingPageInput) -> dict[str, Any]:
    """List activity for a security finding."""
    return _run("security", "security:read", "security_get_finding_activity", params.model_dump(),
                lambda db, user: security_routes.finding_activity(params.finding_id, db, user, params.page))


def security_get_finding_observations(params: FindingPageInput) -> dict[str, Any]:
    """List scan observations that recorded a security finding."""
    return _run("security", "security:read", "security_get_finding_observations", params.model_dump(),
                lambda db, user: security_routes.finding_observations(params.finding_id, db, user, params.page))


class ReportInput(ScanScopedInput):
    format: Literal["json", "markdown"] = "json"
    offset: int = Field(default=0, ge=0)


def security_get_report(params: ReportInput) -> dict[str, Any]:
    """Read a complete saved scan report in bounded chunks, following next_offset."""
    args = params.model_dump()

    def callback(db, user):
        response = security_routes.report(params.scan_id, db, user, params.format)
        content = bytes(response.body).decode()
        end = min(len(content), params.offset + REPORT_MAX_CHARS)
        return {"scan_id": params.scan_id, "format": params.format, "offset": params.offset,
            "truncated": end < len(content), "content": content[params.offset:end],
            "total_chars": len(content), "next_offset": end if end < len(content) else None}

    return _run("security", "security:read", "security_get_report", args, callback)


class ReportFindingsInput(ScanScopedInput):
    page: int = Field(default=1, ge=1, le=1000000)
    q: str = Field(default="", max_length=256)
    state: list[Literal["open", "resolved", "false_positive", "accepted_risk", "unknown"]] | None = None
    outcome: list[Literal["present", "fixed", "not_observed", "uncertain", "unknown"]] | None = None
    severity: list[Literal["critical", "high", "medium", "low", "unknown"]] | None = None
    kind: list[Annotated[str, Field(min_length=1, max_length=50)]] | None = Field(default=None, max_length=100)
    finding_id: str | None = Field(default=None, min_length=1, max_length=36)
    ordering: SnapshotOrdering = "-severity"


def security_list_report_findings(params: ReportFindingsInput) -> dict[str, Any]:
    """List paginated immutable finding snapshots from a saved scan report."""
    args = params.model_dump()
    return _run("security", "security:read", "security_list_report_findings", args,
                lambda db, user: security_routes.report_findings(
                    params.scan_id, db, user, params.page, params.q, params.state, params.outcome,
                    params.severity, params.kind, params.finding_id, params.ordering))


class TriageInput(FindingScopedInput, MutationInput):
    state: Literal["open", "false_positive", "accepted_risk"] = "open"
    reason: str = Field(min_length=1, max_length=4000)
    version: int = Field(ge=0)


def security_triage_finding(params: TriageInput) -> dict[str, Any]:
    """Mark a finding false positive, accept the risk, or reopen it. Version conflicts fail."""
    body = SecurityTriage(state=params.state, reason=params.reason, version=params.version)
    return _run("security", "security:write", "security_triage_finding", params.model_dump(),
                lambda db, user: security_routes.triage(params.finding_id, body, db, user),
                request_key=params.request_key)


class RotationInput(FindingScopedInput, MutationInput):
    reason: str = Field(min_length=1, max_length=4000)
    version: int = Field(ge=0)


def security_acknowledge_rotation(params: RotationInput) -> dict[str, Any]:
    """Acknowledge rotation of an exposed secret; kind, state, version, and evidence must match."""
    body = SecurityRotation(reason=params.reason, version=params.version)
    return _run("security", "security:write", "security_acknowledge_rotation", params.model_dump(),
                lambda db, user: security_routes.rotation(params.finding_id, body, db, user),
                request_key=params.request_key)


class StartScanInput(RepositoryScopedInput, MutationInput):
    connection_id: str = Field(min_length=1, max_length=36)
    model: str = Field(default="", max_length=150)
    concurrency: int = Field(default=4, ge=1, le=8, strict=True)
    limits: RunLimits | None = None


def _scan_body(params: StartScanInput, user, operation: str) -> SecurityScanInput:
    key = digest(f"mcp:{user.mcp_grant_id}:{operation}:{params.request_key}")
    return SecurityScanInput(connection_id=params.connection_id, model=params.model,
                             concurrency=params.concurrency, request_key=key,
                             timeout_seconds=params.limits.timeout_seconds if params.limits else None,
                             token_budget=params.limits.token_budget if params.limits else None)


def security_start_scan(params: StartScanInput) -> dict[str, Any]:
    """Start a security scan; returns the saved scan promptly."""
    return _run("security", "security:run", "security_start_scan", params.model_dump(),
                lambda db, user: security_routes.start_scan(params.repository_id,
                    _scan_body(params, user, "security_start_scan"), db, user),
                request_key=params.request_key)


class CancelScanInput(ScanScopedInput, MutationInput):
    pass


def security_cancel_scan(params: CancelScanInput) -> dict[str, Any]:
    """Stop an eligible security scan."""
    return _run("security", "security:run", "security_cancel_scan", params.model_dump(),
                lambda db, user: security_routes.cancel_scan(params.scan_id, db, user),
                request_key=params.request_key)


class RetryScanInput(StartScanInput):
    scan_id: str = Field(min_length=1, max_length=36)


def security_retry_scan(params: RetryScanInput) -> dict[str, Any]:
    """Retry an interrupted, incomplete, or blocked scan."""
    return _run("security", "security:run", "security_retry_scan", params.model_dump(),
                lambda db, user: security_routes.retry_scan(params.scan_id, db, user,
                    _scan_body(params, user, "security_retry_scan")),
                request_key=params.request_key)


class IssueCandidatesInput(RepositoryScopedInput):
    page: int = Field(default=1, ge=1, le=1000000)


def security_list_issue_candidates(params: IssueCandidatesInput) -> dict[str, Any]:
    """List findings eligible for GitHub issues with exact redacted drafts."""
    return _run("security", "security:issues", "security_list_issue_candidates", params.model_dump(),
                lambda db, user: security_routes.issue_candidates(params.repository_id, db, user, params.page))


def security_preview_issue(params: FindingScopedInput) -> dict[str, Any]:
    """Preview the exact redacted draft for one finding before publication."""
    return _run("security", "security:issues", "security_preview_issue", params.model_dump(),
                lambda db, user: security_routes.issue_preview(params.finding_id, db, user))


class CreateIssueInput(FindingScopedInput, ApprovalInput):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=50000)
    version: int = Field(ge=0)
    public: bool = False
    recovery_token: str | None = Field(default=None, min_length=1, max_length=36)


def security_create_issue(params: CreateIssueInput) -> dict[str, Any]:
    """Create a GitHub issue; public publication requires browser approval of the exact draft."""
    body = SecurityIssueInput(title=params.title, body=params.body, version=params.version,
                              public=params.public, recovery_token=params.recovery_token)
    return _run("security", "security:issues", "security_create_issue", params.model_dump(),
                lambda db, user: security_routes.create_issue(params.finding_id, body, db, user),
                request_key=params.request_key, approval=True)


class BulkIssueInput(RepositoryScopedInput, ApprovalInput):
    issues: list[SecurityIssueDraft] = Field(min_length=1, max_length=100)


def security_create_issues_bulk(params: BulkIssueInput) -> dict[str, Any]:
    """Create issues in bulk; public publication requires browser approval of the exact drafts."""
    body = SecurityBulkIssueInput(issues=params.issues)
    return _run("security", "security:issues", "security_create_issues_bulk", params.model_dump(),
                lambda db, user: security_routes.create_issues(params.repository_id, body, db, user),
                request_key=params.request_key, approval=True)


class SyncIssueInput(FindingScopedInput, ApprovalInput):
    pass


def security_sync_issue(params: SyncIssueInput) -> dict[str, Any]:
    """Recover or retry synchronization of an uncertain or failed issue publication."""
    args = params.model_dump()
    return _run("security", "security:issues", "security_sync_issue", args,
                lambda db, user: security_routes.sync_issue(params.finding_id, db, user),
                request_key=params.request_key, approval=True)


class ReapproveIssueInput(FindingScopedInput, ApprovalInput):
    version: int = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=36)
    approval_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=4000)


def security_reapprove_issue(params: ReapproveIssueInput) -> dict[str, Any]:
    """Reapprove a recovered issue publication after review."""
    body = SecurityIssueReapproval(version=params.version, operation_id=params.operation_id,
                                   approval_revision=params.approval_revision, reason=params.reason)
    return _run("security", "security:issues", "security_reapprove_issue", params.model_dump(),
                lambda db, user: security_routes.reapprove_issue(params.finding_id, body, db, user),
                request_key=params.request_key, approval=True)


# Management domain (instance:manage and connections:manage), reachable
# through either endpoint with role and ownership checks preserved.


def management_get_instance_settings() -> dict[str, Any]:
    """Read instance settings, members, installations, and diagnostics. Administrator only."""
    def callback(db, user):
        _require_admin(user)
        return settings_routes.instance_settings(db, user)

    return _run("management", "instance:manage", "management_get_instance_settings", {}, callback)


class AddMemberInput(ApprovalInput):
    login: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "member"] = "member"


def management_add_member(params: AddMemberInput) -> dict[str, Any]:
    """Add a GitHub user as a member. Administrator only."""
    body = MemberInput(login=params.login, role=params.role)

    def callback(db, user):
        _require_admin(user)
        return members_routes.add_member(body, db, user)

    return _run("management", "instance:manage", "management_add_member", params.model_dump(), callback,
                request_key=params.request_key, approval=True)


class RemoveMemberInput(ApprovalInput):
    user_id: int = Field(gt=0)


def management_remove_member(params: RemoveMemberInput) -> dict[str, Any]:
    """Remove a member and stop their pending work. Administrator only; needs approval."""
    def callback(db, user):
        _require_admin(user)
        return members_routes.remove_member(params.user_id, db, user)

    return _run("management", "instance:manage", "management_remove_member", params.model_dump(),
                callback, request_key=params.request_key, approval=True)


def management_list_installations() -> dict[str, Any]:
    """List connected GitHub App installations. Administrator only."""
    def callback(db, user):
        _require_admin(user)
        return installations_routes.list_installations(db, user)

    return _run("management", "instance:manage", "management_list_installations", {}, callback)


def management_list_available_installations() -> dict[str, Any]:
    """List GitHub App installations available to the administrator's account."""
    def callback(db, user):
        _require_admin(user)
        return installations_routes.available_installations(db, user)

    return _run("management", "instance:manage", "management_list_available_installations", {}, callback)


class InstallationInput(ApprovalInput):
    installation_id: int = Field(gt=0)


def management_connect_installation(params: InstallationInput) -> dict[str, Any]:
    """Connect an available GitHub App installation. Administrator only."""
    def callback(db, user):
        _require_admin(user)
        return installations_routes.connect_installation(params.installation_id, db, user)

    return _run("management", "instance:manage", "management_connect_installation",
                params.model_dump(), callback, request_key=params.request_key)


def management_uninstall_installation(params: InstallationInput) -> dict[str, Any]:
    """Uninstall a GitHub App installation. Administrator only; needs approval."""
    def callback(db, user):
        _require_admin(user)
        return installations_routes.uninstall_installation(params.installation_id, db, user)

    return _run("management", "instance:manage", "management_uninstall_installation",
                params.model_dump(), callback, request_key=params.request_key, approval=True)


def management_remove_installation_local(params: InstallationInput) -> dict[str, Any]:
    """Remove a local installation record without touching GitHub. Administrator only; needs approval."""
    def callback(db, user):
        _require_admin(user)
        return installations_routes.remove_installation(params.installation_id, db, user)

    return _run("management", "instance:manage", "management_remove_installation_local",
                params.model_dump(), callback, request_key=params.request_key, approval=True)


def management_get_installation_setup_url() -> dict[str, Any]:
    """Return the trusted browser URL for GitHub App setup; install there, then connect."""
    def callback(db, user):
        _require_admin(user)
        config = settings_routes.instance_settings(db, user)
        return {"url": settings().public_url + config["github_app"]["install_url"]}

    return _run("management", "instance:manage", "management_get_installation_setup_url", {}, callback)


def management_list_providers() -> dict[str, Any]:
    """List supported providers for connection setup."""
    return _run("management", "connections:manage", "management_list_providers", {},
                lambda db, user: connections_routes.providers(user))


def management_list_connections(repository_id: int | None = None) -> dict[str, Any]:
    """List connections visible to the user; secrets are never returned."""
    return _run("management", "connections:manage", "management_list_connections",
                {"repository_id": repository_id},
                lambda db, user: connections_routes.connections(db, user, repository_id))


def management_get_connection_models(params: ConnectionModelsInput) -> dict[str, Any]:
    """List models for a saved connection the user may consume."""
    return _run("management", "connections:manage", "management_get_connection_models", params.model_dump(),
                lambda db, user: connections_routes.connection_models(
                    params.connection_id, db, user, params.repository_id))


class DiscoverModelsInput(StrictModel):
    connection_id: str = Field(min_length=1, max_length=36)


def management_discover_models(params: DiscoverModelsInput) -> dict[str, Any]:
    """Discover models through a saved connection. Administrator only; no credential entry here."""
    def callback(db, user):
        _require_admin(user)
        return connections_routes.connection_models(params.connection_id, db, user, None)

    return _run("management", "connections:manage", "management_discover_models",
                params.model_dump(), callback)


def management_create_connection(params: ManagementConnectionInput) -> dict[str, Any]:
    """Create a connection record; API connections need an administrator, credentials in browser."""
    body = params.to_connection_input()
    return _run("management", "connections:manage", "management_create_connection", params.model_dump(),
                lambda db, user: connections_routes.add_connection(body, db, user),
                request_key=params.request_key, approval=True)


class UpdateConnectionInput(ManagementConnectionInput):
    connection_id: str = Field(min_length=1, max_length=36)


def management_update_connection(params: UpdateConnectionInput) -> dict[str, Any]:
    """Update a connection record without touching credentials; owner or administrator only."""
    body = params.to_connection_input()

    def callback(db, user):
        # manage_connection preserves owner and API-administrator checks.
        dependencies_api.manage_connection(db, user, params.connection_id)
        return connections_routes.edit_connection(params.connection_id, body, db, user)

    args = params.model_dump()
    return _run("management", "connections:manage", "management_update_connection", args, callback,
                request_key=params.request_key, approval=True)


class ConnectionRef(StrictModel):
    connection_id: str = Field(min_length=1, max_length=36)


class ConnectionRefInput(ConnectionRef, ApprovalInput):
    pass


def management_test_connection(params: ConnectionRefInput) -> dict[str, Any]:
    """Test a shared API connection; owner or administrator only."""
    return _run("management", "connections:manage", "management_test_connection", params.model_dump(),
                lambda db, user: connections_routes.test_connection(params.connection_id, db, user),
                request_key=params.request_key)


def management_delete_connection(params: ConnectionRefInput) -> dict[str, Any]:
    """Delete a connection after active work moves away; owner or administrator only; needs approval."""
    return _run("management", "connections:manage", "management_delete_connection", params.model_dump(),
                lambda db, user: connections_routes.delete_connection(params.connection_id, db, user),
                request_key=params.request_key, approval=True)


def management_start_connection_auth(params: ConnectionRefInput) -> dict[str, Any]:
    """Hand off owner-only native sign-in to the trusted Code Mortis browser UI."""
    def callback(db, user):
        connection = connection_routes.personal(db, user, params.connection_id)
        return {"url": settings().public_url + "/settings?tab=connections", "connection_id": connection.id,
            "message": "Open " + connection.name + " in AI connections and complete sign-in there."}

    return _run("management", "connections:manage", "management_start_connection_auth", params.model_dump(),
                callback,
                request_key=params.request_key)


def management_get_connection_auth_status(params: ConnectionRef) -> dict[str, Any]:
    """Inspect personal-connection authentication progress. Owner only."""
    def callback(db, user):
        result = connection_routes.auth_status(params.connection_id, db, user)
        return {key: result[key] for key in ("status", "message") if key in result}

    return _run("management", "connections:manage", "management_get_connection_auth_status", params.model_dump(), callback)


def management_cancel_connection_auth(params: ConnectionRefInput) -> dict[str, Any]:
    """Cancel a personal-connection authentication attempt. Owner only."""
    return _run("management", "connections:manage", "management_cancel_connection_auth", params.model_dump(),
                lambda db, user: connection_routes.cancel_auth(params.connection_id, db, user),
                request_key=params.request_key)


def management_disconnect_connection(params: ConnectionRefInput) -> dict[str, Any]:
    """Disconnect a personal connection and revoke its repository access; needs approval. Owner only."""
    return _run("management", "connections:manage", "management_disconnect_connection", params.model_dump(),
                lambda db, user: connection_routes.disconnect(params.connection_id, db, user),
                request_key=params.request_key, approval=True)


def management_list_connection_grants(params: ConnectionRef) -> dict[str, Any]:
    """List repository consents granted to a personal connection. Owner only."""
    return _run("management", "connections:manage", "management_list_connection_grants", params.model_dump(),
                lambda db, user: connection_routes.grants(params.connection_id, db, user))


class GrantAccessInput(ConnectionRefInput):
    repository_id: int = Field(gt=0, le=9007199254740991)
    automatic: bool = False


def management_grant_repository_access(params: GrantAccessInput) -> dict[str, Any]:
    """Grant a personal connection repository consumption; needs approval. Owner only."""
    body = connection_routes.GrantInput(automatic=params.automatic)
    return _run("management", "connections:manage", "management_grant_repository_access", params.model_dump(),
                lambda db, user: connection_routes.grant(params.connection_id, params.repository_id, body, db, user),
                request_key=params.request_key, approval=True)


class RevokeAccessInput(ConnectionRefInput):
    repository_id: int = Field(gt=0, le=9007199254740991)


def management_revoke_repository_access(params: RevokeAccessInput) -> dict[str, Any]:
    """Revoke a personal connection's repository consent and stop its work; needs approval. Owner only."""
    return _run("management", "connections:manage", "management_revoke_repository_access", params.model_dump(),
                lambda db, user: connection_routes.revoke(params.connection_id, params.repository_id, db, user),
                request_key=params.request_key, approval=True)


REVIEW_TOOLS = [
    (reviews_list_repositories, "reviews_list_repositories", "reviews:read",
     "List repositories visible to the MCP user with review and security summaries.", True),
    (reviews_list_pull_requests, "reviews_list_pull_requests", "reviews:read",
     "Search the review inbox across visible repositories.", True),
    (reviews_list_repository_pulls, "reviews_list_repository_pulls", "reviews:read",
     "List pull requests for one repository with inbox filters.", True),
    (reviews_get_pull_request, "reviews_get_pull_request", "reviews:read",
     "Inspect a pull request, its latest review, counts, and write capability.", True),
    (reviews_list_runs, "reviews_list_runs", "reviews:read", "List review runs for a pull request.", True),
    (reviews_get_run, "reviews_get_run", "reviews:read",
     "Inspect a review run: stages, errors, publication, usage, coverage, occurrences.", True),
    (reviews_get_run_section, "reviews_get_run_section", "reviews:read",
     "Read saved report, progress, config, summary or stages in complete bounded chunks.", True),
    (reviews_list_findings, "reviews_list_findings", "reviews:read",
     "List current findings for a pull request with status and severity filters.", True),
    (reviews_get_finding, "reviews_get_finding", "reviews:read",
     "Inspect a finding with its activity and verification history.", True),
    (reviews_list_run_findings, "reviews_list_run_findings", "reviews:read",
     "List immutable findings saved on a review run.", True),
    (reviews_get_run_finding, "reviews_get_run_finding", "reviews:read",
     "Inspect one immutable finding occurrence saved on a review run.", True),
    (reviews_list_resolved, "reviews_list_resolved", "reviews:read",
     "List findings resolved on a review run.", True),
    (reviews_list_candidates, "reviews_list_candidates", "reviews:read",
     "List assessment candidates for a review run.", True),
    (reviews_get_candidate, "reviews_get_candidate", "reviews:read",
     "Inspect one assessment candidate.", True),
    (reviews_get_analytics, "reviews_get_analytics", "reviews:read",
     "Review analytics for visible repositories.", True),
    (reviews_list_connections, "reviews_list_connections", "reviews:read",
     "List safe connection choices; secrets are never returned.", True),
    (reviews_get_connection_models, "reviews_get_connection_models", "reviews:read",
     "List models for a saved connection.", True),
    (reviews_list_providers, "reviews_list_providers", "reviews:read",
     "List supported providers for connection setup.", True),
    (reviews_set_finding_status, "reviews_set_finding_status", "reviews:write",
     "Resolve, reopen, or dismiss a finding; dismissing requires a reason.", False),
    (reviews_sync_finding_status, "reviews_sync_finding_status", "reviews:write",
     "Retry GitHub status synchronization for a finding with a pending update.", False),
    (reviews_retry_publication, "reviews_retry_publication", "reviews:write",
     "Retry failed or partial GitHub comment publication for a run.", False),
    (reviews_sync_summary, "reviews_sync_summary", "reviews:write",
     "Retry failed PR overview synchronization with GitHub.", False),
    (reviews_set_pull_ignored, "reviews_set_pull_ignored", "reviews:write",
     "Ignore or restore a pull request; ignoring stops pending work.", False),
    (reviews_refresh_repository, "reviews_refresh_repository", "reviews:write",
     "Refresh repository data from GitHub.", False),
    (reviews_start_review, "reviews_start_review", "reviews:run",
     "Start a manual review; returns the saved run. Requires request_key.", False),
    (reviews_cancel_run, "reviews_cancel_run", "reviews:run", "Stop one queued, running, or blocked review.", False),
    (reviews_cancel_pull_reviews, "reviews_cancel_pull_reviews", "reviews:run",
     "Stop all cancellable reviews for a pull request.", False),
    (reviews_retry_review, "reviews_retry_review", "reviews:run",
     "Resume a blocked review. Requires request_key.", False),
    (reviews_retry_assessment, "reviews_retry_assessment", "reviews:run",
     "Retry a failed assessment on a finished, published run. Requires request_key.", False),
    (reviews_retry_summary_generation, "reviews_retry_summary_generation", "reviews:run",
     "Retry overview generation for a run. Requires request_key.", False),
    (reviews_generate_background, "reviews_generate_background", "reviews:run",
     "Generate repository background. Administrator only. Requires request_key.", False),
    (reviews_get_background_status, "reviews_get_background_status", "reviews:read",
     "Inspect a background generation job. Administrator only.", True),
    (reviews_update_repository_config, "reviews_update_repository_config", "reviews:manage",
     "Change review configuration for a repository. Administrator only.", False),
]

SECURITY_TOOLS = [
    (security_list_repositories, "security_list_repositories", "security:read",
     "List security repositories with grades, freshness, and counts.", True),
    (security_get_repository, "security_get_repository", "security:read",
     "Inspect one security repository with finding and scan counts.", True),
    (security_list_scans, "security_list_scans", "security:read", "List security scans for a repository.", True),
    (security_get_scan, "security_get_scan", "security:read",
     "Inspect a scan: state, stages, errors, usage, pinned commit, bounded activity.", True),
    (security_get_scan_activity, "security_get_scan_activity", "security:read",
     "List scan lifecycle activity for a scan.", True),
    (security_get_scan_coverage, "security_get_scan_coverage", "security:read",
     "Inspect one coverage section for a scan.", True),
    (security_list_assessments, "security_list_assessments", "security:read",
     "List assessment proposals for a scan with outcome filters.", True),
    (security_get_assessment, "security_get_assessment", "security:read", "Inspect one assessment proposal.", True),
    (security_list_findings, "security_list_findings", "security:read",
     "List findings for a repository or a scan snapshot with supported filters.", True),
    (security_get_finding, "security_get_finding", "security:read",
     "Discuss a finding: evidence, remediation, bounded observations and activity.", True),
    (security_get_finding_activity, "security_get_finding_activity", "security:read",
     "List activity for a security finding.", True),
    (security_get_finding_observations, "security_get_finding_observations", "security:read",
     "List scan observations that recorded a security finding.", True),
    (security_get_report, "security_get_report", "security:read",
     "Read the complete saved scan report in bounded chunks using next_offset.", True),
    (security_list_report_findings, "security_list_report_findings", "security:read",
     "List paginated immutable finding snapshots from a saved scan report.", True),
    (security_triage_finding, "security_triage_finding", "security:write",
     "Mark a finding false positive, accept the risk, or reopen it.", False),
    (security_acknowledge_rotation, "security_acknowledge_rotation", "security:write",
     "Acknowledge rotation of an exposed secret.", False),
    (security_start_scan, "security_start_scan", "security:run",
     "Start a security scan. Requires request_key.", False),
    (security_cancel_scan, "security_cancel_scan", "security:run", "Stop an eligible security scan.", False),
    (security_retry_scan, "security_retry_scan", "security:run",
     "Retry an interrupted, incomplete, or blocked scan. Requires request_key.", False),
    (security_list_issue_candidates, "security_list_issue_candidates", "security:issues",
     "List findings eligible for GitHub issues with exact redacted drafts.", True),
    (security_preview_issue, "security_preview_issue", "security:issues",
     "Preview the exact redacted draft for one finding before publication.", True),
    (security_create_issue, "security_create_issue", "security:issues",
     "Create a GitHub issue; public publication requires browser approval.", False),
    (security_create_issues_bulk, "security_create_issues_bulk", "security:issues",
     "Create issues in bulk. Requires request_key and browser approval.", False),
    (security_sync_issue, "security_sync_issue", "security:issues",
     "Recover or retry synchronization of an uncertain or failed issue publication.", False),
    (security_reapprove_issue, "security_reapprove_issue", "security:issues",
     "Reapprove a recovered issue publication after review.", False),
]

MANAGEMENT_TOOLS = [
    (management_get_instance_settings, "management_get_instance_settings", "instance:manage",
     "Read instance settings, members, installations, and diagnostics. Administrator only.", True),
    (management_add_member, "management_add_member", "instance:manage",
     "Add a GitHub user as a member. Administrator only.", False),
    (management_remove_member, "management_remove_member", "instance:manage",
     "Remove a member and stop their pending work. Needs approval.", False),
    (management_list_installations, "management_list_installations", "instance:manage",
     "List connected GitHub App installations. Administrator only.", True),
    (management_list_available_installations, "management_list_available_installations", "instance:manage",
     "List GitHub App installations available to the administrator's account.", True),
    (management_connect_installation, "management_connect_installation", "instance:manage",
     "Connect an available GitHub App installation. Administrator only.", False),
    (management_uninstall_installation, "management_uninstall_installation", "instance:manage",
     "Uninstall a GitHub App installation. Needs approval.", False),
    (management_remove_installation_local, "management_remove_installation_local", "instance:manage",
     "Remove a local installation record without touching GitHub. Needs approval.", False),
    (management_get_installation_setup_url, "management_get_installation_setup_url", "instance:manage",
     "Return the trusted browser URL for GitHub App setup.", True),
    (management_list_providers, "management_list_providers", "connections:manage",
     "List supported providers for connection setup.", True),
    (management_list_connections, "management_list_connections", "connections:manage",
     "List connections visible to the user; secrets are never returned.", True),
    (management_get_connection_models, "management_get_connection_models", "connections:manage",
     "List models for a saved connection the user may consume.", True),
    (management_discover_models, "management_discover_models", "connections:manage",
     "Discover models through a saved connection. Administrator only.", True),
    (management_create_connection, "management_create_connection", "connections:manage",
     "Create a connection record; credentials are entered in the browser.", False),
    (management_update_connection, "management_update_connection", "connections:manage",
     "Update a connection record without touching credentials.", False),
    (management_test_connection, "management_test_connection", "connections:manage",
     "Test a shared API connection.", False),
    (management_delete_connection, "management_delete_connection", "connections:manage",
     "Delete a connection after active work moves away. Needs approval.", False),
    (management_start_connection_auth, "management_start_connection_auth", "connections:manage",
     "Start personal-connection authentication in the official client.", False),
    (management_get_connection_auth_status, "management_get_connection_auth_status", "connections:manage",
     "Inspect personal-connection authentication progress.", True),
    (management_cancel_connection_auth, "management_cancel_connection_auth", "connections:manage",
     "Cancel a personal-connection authentication attempt.", False),
    (management_disconnect_connection, "management_disconnect_connection", "connections:manage",
     "Disconnect a personal connection and revoke its access. Needs approval.", False),
    (management_list_connection_grants, "management_list_connection_grants", "connections:manage",
     "List repository consents granted to a personal connection.", True),
    (management_grant_repository_access, "management_grant_repository_access", "connections:manage",
     "Grant a personal connection repository consumption. Needs approval.", False),
    (management_revoke_repository_access, "management_revoke_repository_access", "connections:manage",
     "Revoke a personal connection's repository consent. Needs approval.", False),
]


_TOOL_DOMAINS: dict[str, str] = {}


def tool_domain(name: str) -> str | None:
    """Return the domain a registered tool name belongs to, if known."""
    return _TOOL_DOMAINS.get(name)


def _scoped(fn, domain: str):
    """Bind a management handler to the endpoint it is registered on."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _MOUNT.set(domain)
        try:
            return fn(*args, **kwargs)
        finally:
            _MOUNT.reset(token)

    return wrapper


def register_tools(server: MCPServer, domain: str) -> None:
    """Register the explicit tool catalog for ``domain`` plus management tools.

    ``domain`` is ``"reviews"`` or ``"security"``; management tools are
    reachable through either endpoint under their own scopes.
    """
    if domain == "code":
        from code_mortis.mcp.code_tools import TOOLS

        for fn, description in TOOLS:
            _register(server, fn, fn.__name__, "code:read", description, read_only=True)
            _TOOL_DOMAINS[fn.__name__] = domain
        return
    if domain == "reviews":
        catalog = REVIEW_TOOLS
    elif domain == "security":
        catalog = SECURITY_TOOLS
    else:
        raise ValueError("Register the reviews or security tool catalog.")
    for fn, name, scope, description, read_only in catalog:
        _register(server, fn, name, scope, description, read_only=read_only)
        _TOOL_DOMAINS[name] = domain
    for fn, name, scope, description, read_only in MANAGEMENT_TOOLS:
        _register(server, _scoped(fn, domain), name, scope, description, read_only=read_only)
        _TOOL_DOMAINS.setdefault(name, "management")
