from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionConfig(StrictModel):
    kind: Literal["api", "chatgpt", "claude_code"] = "api"
    provider: str = Field(default="", max_length=100, pattern=r"^[a-z0-9-]*$")
    protocol: Literal["openai", "openai-responses", "anthropic", "anthropic-bedrock", "native"] = "native"
    base_url: str = Field(default="", max_length=1000)
    api_key: str | None = Field(default=None, max_length=20000)
    extra_headers: dict[str, str] | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def connection_type(self):
        if self.kind == "api":
            if not self.provider or self.protocol == "native":
                raise ValueError("API connections require a provider and supported API protocol")
            if self.protocol != "anthropic-bedrock" and any(key.startswith("aws_") for key in self.options):
                raise ValueError("AWS options require the Bedrock protocol")
        else:
            if (
                self.api_key
                or self.extra_headers
                or self.base_url
                or self.options
                or self.protocol != "native"
            ):
                raise ValueError("Personal connections use only the official client's authentication")
            self.provider = "openai" if self.kind == "chatgpt" else "anthropic"
        return self

    @field_validator("options")
    @classmethod
    def allowed_options(cls, value):
        allowed = {"timeout_sec", "extra_body", "retry_codes", "aws_region", "aws_profile", "auth_header"}
        if set(value) - allowed:
            raise ValueError("Unsupported provider options")
        if "timeout_sec" in value and (
            type(value["timeout_sec"]) is not int or not 5 <= value["timeout_sec"] <= 900
        ):
            raise ValueError("timeout_sec must be between 5 and 900")
        if "extra_body" in value:
            if not isinstance(value["extra_body"], dict):
                raise ValueError("extra_body must be an object")
            if set(value["extra_body"]) & {"messages", "input", "tools", "model", "stream", "api_key"}:
                raise ValueError("extra_body cannot replace review inputs, tools, or authentication")
        if "retry_codes" in value and (
            not isinstance(value["retry_codes"], list)
            or any(type(code) is not int or not 400 <= code < 500 for code in value["retry_codes"])
        ):
            raise ValueError("retry_codes must contain HTTP 4xx integers")
        for key in ("aws_region", "aws_profile", "auth_header"):
            if key in value and (not isinstance(value[key], str) or len(value[key]) > 150):
                raise ValueError(f"Invalid {key}")
        return value

    @field_validator("extra_headers")
    @classmethod
    def header_values(cls, value):
        if value and any(
            "\n" in key + val or "\r" in key + val or len(val) > 20000 for key, val in value.items()
        ):
            raise ValueError("Invalid provider headers")
        return value


class ConnectionInput(ConnectionConfig):
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(default="", max_length=150)

    @model_validator(mode="after")
    def selected_model(self):
        self.model = self.model.strip()
        if self.kind == "api" and not self.model:
            raise ValueError("Choose a model for this API connection")
        return self


class ModelDiscoveryInput(ConnectionConfig):
    kind: Literal["api"] = "api"
    connection_id: str | None = None


class ModelChoice(StrictModel):
    id: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=250)


class ModelCatalog(StrictModel):
    models: list[ModelChoice] = Field(default_factory=list)
    source: Literal["provider", "client"]
    status: Literal["ready", "unsupported", "disconnected", "unavailable"] = "ready"
    message: str | None = None


class RunLimits(StrictModel):
    timeout_seconds: int | None = Field(default=900, ge=60, le=3600)
    token_budget: int | None = Field(default=200000, ge=1000, le=5000000)


class RepoSettings(RunLimits):
    automatic: bool = True
    auto_resolve: bool = False
    model: str = Field(default="", max_length=150)
    effort: Literal["low", "medium", "high"] = "medium"
    language: str = Field(default="English", min_length=1, max_length=100)
    exclusions: str = Field(default="", max_length=4000)
    background: str = Field(default="", max_length=20000)
    max_files: int | None = Field(default=40, ge=1, strict=True)
    delegated_context_bytes: int = Field(default=800000, ge=4096, le=20000000)
    concurrency: int = Field(default=2, ge=1, le=8)
    rules: dict[str, Any] | None = None

    @field_validator("model")
    @classmethod
    def trim_model(cls, value):
        return value.strip()


class RepositoryInput(StrictModel):
    enabled: bool
    connection_id: str | None = None
    settings: RepoSettings = Field(default_factory=RepoSettings)


class BackgroundInput(StrictModel):
    connection_id: str = Field(min_length=1, max_length=36)
    model: str = Field(default="", max_length=150)
    limits: RunLimits | None = None


class BackgroundResult(StrictModel):
    background: str = Field(min_length=1, max_length=20000)

    @field_validator("background")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("The model returned an empty project background.")
        return value.strip()


class BackgroundJobOut(StrictModel):
    id: str
    status: Literal["queued", "running", "done", "failed"]
    background: str | None = None
    error: str | None = None


class FindingAction(StrictModel):
    status: Literal["open", "resolved", "dismissed"]
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def dismiss_reason(self):
        if self.status == "dismissed" and not self.reason.strip():
            raise ValueError("Add a reason when dismissing a finding")
        return self


class MemberInput(StrictModel):
    login: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}$")
    role: Literal["admin", "member"] = "member"


class ReviewComment(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(min_length=1, max_length=60000)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    category: str = Field(default="other", max_length=50)
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    existing_code: str = Field(default="", max_length=60000)
    suggestion_code: str = Field(default="", max_length=60000)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or ".git" in path.parts
            or "\\" in value
            or "\x00" in value
        ):
            raise ValueError("Finding path must stay inside the repository")
        return value

    @model_validator(mode="after")
    def ordered_lines(self):
        if self.end_line < self.start_line:
            raise ValueError("Finding end_line must follow start_line")
        return self


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORMModel):
    id: int
    login: str
    avatar_url: str
    role: str
    csrf: str = ""


class InstallationOut(StrictModel):
    id: int
    account: str
    connected: bool
    active: bool
    status: Literal["active", "suspended", "uninstalled"]
    settings_url: str | None = None
    repository_selection: Literal["all", "selected"] | None = None
    can_connect: bool = False
    can_uninstall: bool = False
    unavailable_reason: str | None = None
    job_id: str | None = None
    operation_status: str | None = None
    operation_error: str | None = None


class InstallationOperation(StrictModel):
    job_id: str


class ConnectionOut(ORMModel):
    id: str
    name: str
    provider: str
    protocol: str
    model: str
    base_url: str
    options: dict[str, Any]
    has_key: bool = False
    header_names: list[str] = Field(default_factory=list)
    last_test: str | None
    tested_at: datetime | None
    kind: str
    owner_id: int | None
    auth_status: str
    auth_message: str | None


class StartRunInput(StrictModel):
    connection_id: str | None = None


class RepositoryOut(ORMModel):
    id: int
    installation_id: int
    full_name: str
    private: bool
    active: bool
    enabled: bool
    connection_id: str | None
    settings: RepoSettings
    open_prs: int = 0


class RunOut(ORMModel):
    id: str
    pr_id: str
    trigger: str
    base_sha: str
    head_sha: str
    status: str
    publication: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    publication_error: str | None
    config: dict[str, Any]
    report: dict[str, Any] | None
    engine: str
    connection_id: str | None
    owner_id: int | None
    actor_id: int | None
    progress: dict[str, Any]
    blocked_reason: str | None
    failure_reason: str | None
    check_enabled: bool
    check_pending: bool
    check_url: str | None
    check_status: str | None
    check_conclusion: str | None
    check_error: str | None
    check_synced_at: datetime | None


class PullRequestOut(ORMModel):
    id: str
    repository_id: int
    number: int
    title: str
    author: str
    url: str
    state: str
    draft: bool
    fork: bool
    head_sha: str
    base_sha: str
    head_ref: str
    updated_at: datetime
    latest_run: RunOut | None = None
    open_findings: int = 0


class FindingOut(ORMModel):
    id: str
    pr_id: str
    status: str
    desired_status: str | None
    sync_error: str | None
    sync_intent: dict[str, Any] | None = None
    path: str
    content: str
    severity: str
    category: str
    start_line: int
    end_line: int
    existing_code: str
    suggestion: str
    thread_id: str | None
    github_url: str | None
    last_seen_run_id: str
    created_at: datetime
    updated_at: datetime


class ActivityOut(ORMModel):
    id: str
    action: str
    actor: str
    source: str
    reason: str
    created_at: datetime


class VerificationEvidence(StrictModel):
    revision: Literal["original", "current"]
    path: str = Field(min_length=1, max_length=2000)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=60000)


class VerificationResult(StrictModel):
    verdict: Literal["fixed", "still_present", "uncertain"]
    original_issue_present: bool
    explanation: str = Field(min_length=1, max_length=10000)
    change_explanation: str = Field(max_length=10000)
    dismissal_recommended: bool
    evidence: list[VerificationEvidence] = Field(max_length=30)


class VerificationOut(ORMModel):
    id: str
    run_id: str
    head_sha: str
    status: str
    verdict: str | None
    result: VerificationResult | None
    error: str | None
    resolution: str
    created_at: datetime
    finished_at: datetime | None


class FindingDetail(BaseModel):
    finding: FindingOut
    activities: list[ActivityOut]
    verifications: list[VerificationOut] = Field(default_factory=list)


class PRDetail(BaseModel):
    pull_request: PullRequestOut
    repository: RepositoryOut
    runs: list[RunOut]
    findings: list[FindingOut]
    can_write: bool


class OccurrenceOut(ORMModel):
    finding_id: str
    snapshot: dict[str, Any]
    inline_line: int | None


class RunDetail(BaseModel):
    run: RunOut
    pull_request: PullRequestOut
    repository: RepositoryOut
    occurrences: list[OccurrenceOut]
    can_write: bool


InboxGroup = Literal["active", "reviewed", "merged", "closed"]
InboxReview = Literal[
    "queued", "running", "blocked", "completed", "partial", "failed", "skipped",
    "cancelled", "superseded", "not_reviewed", "outdated",
]


class InboxFilters(StrictModel):
    q: str = Field(default="", max_length=256)
    repo: list[Annotated[int, Field(gt=0, le=9007199254740991)]] = Field(default_factory=list, max_length=100)
    state: list[Literal["open", "merged", "closed", "all"]] = Field(default=["open", "merged"], max_length=4)
    review: list[InboxReview] = Field(default_factory=list, max_length=11)
    author: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=100)
    findings: Literal["open", "none"] | None = None
    group: InboxGroup | None = None
    page: int = Field(default=1, ge=1, le=1000000)


class InboxPullOut(ORMModel):
    id: str
    repository_id: int
    repository_name: str
    number: int
    title: str
    author: str
    state: str
    draft: bool
    created_at: datetime | None
    updated_at: datetime
    review_status: str
    run_id: str | None
    publication: str | None
    publication_error: str | None
    open_findings: int


class InboxPage(BaseModel):
    group: InboxGroup
    items: list[InboxPullOut]
    total: int
    page: int
    has_more: bool


class InboxRepository(BaseModel):
    id: int
    full_name: str


class InboxOut(BaseModel):
    groups: list[InboxPage]
    total: int
    repository_count: int
    registered_total: int
    repositories: list[InboxRepository]
    authors: list[str]
    state_counts: dict[str, int]
    page_size: int = 20
