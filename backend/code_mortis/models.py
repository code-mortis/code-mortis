from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    or_,
    select,
    text,
)
from sqlalchemy.orm import Mapped, column_property, mapped_column

from code_mortis.core.db import Base, now, uid


class AppConfig(Base):
    __tablename__ = "app_config"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    github_app_id: Mapped[str] = mapped_column(String(40))
    github_slug: Mapped[str] = mapped_column(String(100))
    client_id: Mapped[str] = mapped_column(String(100))
    client_secret: Mapped[str] = mapped_column(Text)
    private_key: Mapped[str] = mapped_column(Text)
    webhook_secret: Mapped[str] = mapped_column(Text)
    mcp_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(20), default="member")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(Base):
    __tablename__ = "memberships"
    github_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    login: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="member")


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    csrf: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Set only by a successful GitHub callback; issuance freshness for API tokens.
    authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(100), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    browser_hash: Mapped[str] = mapped_column(String(64), default="")
    verifier: Mapped[str] = mapped_column(Text, default="")
    return_path: Mapped[str] = mapped_column(Text, default="/")


class Installation(Base):
    __tablename__ = "installations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    account: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[int | None] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    uninstalled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(100))
    protocol: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(150))
    base_url: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_test: Mapped[str | None] = mapped_column(Text)
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(20), default="api")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    auth_status: Mapped[str] = mapped_column(String(30), default="disconnected")
    auth_message: Mapped[str | None] = mapped_column(Text)


class ConnectionGrant(Base):
    __tablename__ = "connection_grants"
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), primary_key=True
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConsoleTicket(Base):
    __tablename__ = "console_tickets"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id", ondelete="CASCADE"))
    session_hash: Mapped[str] = mapped_column(ForeignKey("login_sessions.token_hash", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    installation_id: Mapped[int] = mapped_column(ForeignKey("installations.id"))
    full_name: Mapped[str] = mapped_column(String(250), unique=True)
    private: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("connections.id"))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    code_graph_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default=text("'{}'::json"))


class CodeGraphRepository(Base):
    __tablename__ = "code_graph_repositories"
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    default_sha: Mapped[str | None] = mapped_column(String(40))
    default_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_sha: Mapped[str | None] = mapped_column(String(40))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class CodeGraphSnapshot(Base):
    __tablename__ = "code_graph_snapshots"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha", "extractor_version", "policy_digest",
                         name="uq_code_graph_snapshot_identity"),
        CheckConstraint("commit_sha ~ '^[a-f0-9]{40}$'", name="ck_code_graph_snapshot_commit"),
        CheckConstraint("policy_digest ~ '^[a-f0-9]{64}$'", name="ck_code_graph_snapshot_policy"),
        CheckConstraint("length(extractor_version) > 0", name="ck_code_graph_snapshot_extractor"),
        CheckConstraint("status IN ('queued', 'running', 'ready', 'partial', 'failed', 'cancelled')",
                        name="ck_code_graph_snapshot_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    commit_sha: Mapped[str] = mapped_column(String(40))
    extractor_version: Mapped[str] = mapped_column(String(128))
    policy_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default=text("'{}'::json"))


class CodeGraphFile(Base):
    __tablename__ = "code_graph_files"
    __table_args__ = (
        CheckConstraint("octet_length(path) BETWEEN 1 AND 2000", name="ck_code_graph_file_path"),
        CheckConstraint("blob_sha ~ '^[a-f0-9]{40}$'", name="ck_code_graph_file_blob"),
        CheckConstraint("size_bytes >= 0 AND (line_count IS NULL OR line_count >= 0) "
                        "AND (unresolved_calls IS NULL OR unresolved_calls >= 0)", name="ck_code_graph_file_counts"),
        CheckConstraint("status IN ('parsed', 'partial', 'unsupported', 'excluded', 'failed')",
                        name="ck_code_graph_file_status"),
        CheckConstraint("status NOT IN ('parsed', 'partial') OR (language IS NOT NULL AND line_count IS NOT NULL)",
                        name="ck_code_graph_file_parsed"),
    )
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("code_graph_snapshots.id", ondelete="CASCADE"), primary_key=True)
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    blob_sha: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    language: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20))
    line_count: Mapped[int | None] = mapped_column(Integer)
    unresolved_calls: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(50))


class CodeGraphSymbol(Base):
    __tablename__ = "code_graph_symbols"
    __table_args__ = (
        ForeignKeyConstraint(["snapshot_id", "file_path"], ["code_graph_files.snapshot_id", "code_graph_files.path"],
                             ondelete="CASCADE", name="fk_code_graph_symbol_file"),
        UniqueConstraint("snapshot_id", "file_path", "start_byte", "end_byte", "kind", name="uq_code_graph_symbol_location"),
        CheckConstraint("key ~ '^[a-f0-9]{64}$'", name="ck_code_graph_symbol_key"),
        CheckConstraint("start_byte >= 0 AND end_byte > start_byte AND start_line >= 1 AND end_line >= start_line",
                        name="ck_code_graph_symbol_range"),
        Index("ix_code_graph_symbols_name", "snapshot_id", "name"),
    )
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(64))
    start_byte: Mapped[int] = mapped_column(Integer)
    end_byte: Mapped[int] = mapped_column(Integer)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)


class CodeGraphImport(Base):
    __tablename__ = "code_graph_imports"
    __table_args__ = (
        ForeignKeyConstraint(["snapshot_id", "file_path"], ["code_graph_files.snapshot_id", "code_graph_files.path"],
                             ondelete="CASCADE", name="fk_code_graph_import_source"),
        ForeignKeyConstraint(["snapshot_id", "target_path"], ["code_graph_files.snapshot_id", "code_graph_files.path"],
                             ondelete="CASCADE", name="fk_code_graph_import_target"),
        CheckConstraint("start_byte >= 0 AND end_byte > start_byte AND start_line >= 1 AND end_line >= start_line",
                        name="ck_code_graph_import_range"),
        CheckConstraint("(target_path IS NULL AND provenance = 'unresolved') OR "
                        "(target_path IS NOT NULL AND provenance = 'relative_path')", name="ck_code_graph_import_resolution"),
        Index("ix_code_graph_imports_target", "snapshot_id", "target_path"),
    )
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_path: Mapped[str] = mapped_column(Text, primary_key=True)
    start_byte: Mapped[int] = mapped_column(Integer, primary_key=True)
    end_byte: Mapped[int] = mapped_column(Integer)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    specifier: Mapped[str] = mapped_column(Text)
    target_path: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(20))


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="open")
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fork: Mapped[bool] = mapped_column(Boolean, default=False)
    head_sha: Mapped[str] = mapped_column(String(40))
    base_sha: Mapped[str] = mapped_column(String(40))
    head_ref: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    summary_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    summary_url: Mapped[str | None] = mapped_column(Text)
    summary_error: Mapped[str | None] = mapped_column(Text)
    summary_hash: Mapped[str | None] = mapped_column(String(64))
    summary_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewRun(Base):
    __tablename__ = "review_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pr_id: Mapped[str] = mapped_column(ForeignKey("pull_requests.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    trigger: Mapped[str] = mapped_column(String(20))
    dedup_key: Mapped[str] = mapped_column(String(150), unique=True)
    base_sha: Mapped[str] = mapped_column(String(40))
    head_sha: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    publication: Mapped[str] = mapped_column(String(30), default="pending")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stages: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    publication_error: Mapped[str | None] = mapped_column(Text)
    review_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    publication_plan: Mapped[list[dict] | None] = mapped_column(JSON)
    engine: Mapped[str] = mapped_column(String(30), default="ocr")
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"), index=True
    )
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(String(30))
    check_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    check_pending: Mapped[bool] = mapped_column(Boolean, default=True)
    check_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    check_url: Mapped[str | None] = mapped_column(Text)
    check_status: Mapped[str | None] = mapped_column(String(30))
    check_conclusion: Mapped[str | None] = mapped_column(String(30))
    check_error: Mapped[str | None] = mapped_column(Text)
    check_payload_hash: Mapped[str | None] = mapped_column(String(64))
    check_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("pr_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pr_id: Mapped[str] = mapped_column(ForeignKey("pull_requests.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="open")
    desired_status: Mapped[str | None] = mapped_column(String(20))
    sync_error: Mapped[str | None] = mapped_column(Text)
    sync_version: Mapped[int] = mapped_column(Integer, default=0)
    sync_intent: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    path: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    category: Mapped[str] = mapped_column(String(30), default="other")
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    existing_code: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    thread_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    comment_id: Mapped[int | None] = mapped_column(BigInteger)
    github_url: Mapped[str | None] = mapped_column(Text)
    publication_run_id: Mapped[str | None] = mapped_column(ForeignKey("review_runs.id"))
    publication_batch: Mapped[int | None] = mapped_column(Integer)
    published_review_id: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Occurrence(Base):
    __tablename__ = "occurrences"
    __table_args__ = (UniqueConstraint("run_id", "finding_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    inline_line: Mapped[int | None] = mapped_column(Integer)


class ReviewCandidate(Base):
    __tablename__ = "review_candidates"
    __table_args__ = (UniqueConstraint("run_id", "position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("findings.id"), index=True)
    is_new: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def progress_phase(self):
        return "validating" if self.status == "running" and self.usage.get("response_received") else None

    @property
    def assessment_only(self):
        return self.context.get("assessment_only") is True


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FindingVerification(Base):
    __tablename__ = "finding_verifications"
    __table_args__ = (UniqueConstraint("finding_id", "run_id", name="uq_verification_finding_run"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"), index=True)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("occurrences.id"))
    head_sha: Mapped[str] = mapped_column(String(40))
    finding_version: Mapped[int] = mapped_column(Integer)
    thread_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    verdict: Mapped[str | None] = mapped_column(String(20))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolution: Mapped[str] = mapped_column(String(20), default="not_applicable")
    sync_attempt: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_dispatch", "status", "available_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AnalysisPlan(Base):
    __tablename__ = "analysis_plans"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"))
    scan_id: Mapped[str | None] = mapped_column(ForeignKey("security_scans.id", ondelete="CASCADE"))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AnalysisBudget(Base):
    __tablename__ = "analysis_budgets"
    __table_args__ = (CheckConstraint("charged_tokens >= 0 AND (token_limit IS NULL OR token_limit >= 0)",
                                    name="ck_analysis_budget_tokens"),)
    plan_id: Mapped[str] = mapped_column(ForeignKey("analysis_plans.id", ondelete="CASCADE"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(20), primary_key=True)
    token_limit: Mapped[int | None] = mapped_column(BigInteger)
    charged_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (UniqueConstraint("plan_id", "key", name="uq_analysis_task_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("analysis_plans.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    dependencies: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AnalysisAttempt(Base):
    __tablename__ = "analysis_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "number", name="uq_analysis_attempt_number"),
        CheckConstraint("reserved_tokens >= 0 AND (usage_tokens IS NULL OR usage_tokens >= 0)",
                        name="ck_analysis_attempt_tokens"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    job_attempt: Mapped[int] = mapped_column(Integer)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")
    reserved_tokens: Mapped[int] = mapped_column(BigInteger)
    usage_tokens: Mapped[int | None] = mapped_column(BigInteger)
    usage_state: Mapped[str] = mapped_column(String(20), default="reserved")
    provider_request_id: Mapped[str | None] = mapped_column(String(250))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class SecurityScan(Base):
    __tablename__ = "security_scans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("security_scans.id"))
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    branch: Mapped[str] = mapped_column(Text)
    head_sha: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    stages: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    grade: Mapped[str | None] = mapped_column(String(1))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("uq_security_active_repository", SecurityScan.repository_id, unique=True,
      postgresql_where=SecurityScan.status.in_(["queued", "running", "blocked"]))


class SecurityFinding(Base):
    __tablename__ = "security_findings"
    __table_args__ = (UniqueConstraint("repository_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="open", index=True)
    kind: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    first_scan_id: Mapped[str] = mapped_column(ForeignKey("security_scans.id"))
    last_scan_id: Mapped[str] = mapped_column(ForeignKey("security_scans.id"))
    evidence: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=0)
    rotation: Mapped[dict | None] = mapped_column(JSON)
    issue: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SecurityOccurrence(Base):
    __tablename__ = "security_occurrences"
    __table_args__ = (UniqueConstraint("scan_id", "finding_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scan_id: Mapped[str] = mapped_column(ForeignKey("security_scans.id"), index=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("security_findings.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SecurityActivity(Base):
    __tablename__ = "security_activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scan_id: Mapped[str | None] = mapped_column(ForeignKey("security_scans.id"), index=True)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("security_findings.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# Read pending state from durable jobs instead of locking the PR while flushing a locked run.
PullRequest.summary_pending = column_property(or_(
    PullRequest.summary_error.is_not(None),
    select(Job.id).where(Job.kind == "sync_summary", Job.status.in_(["queued", "running"]),
                         Job.payload["pr_id"].as_string() == PullRequest.id).correlate_except(Job).exists(),
))


class PersonalApiToken(Base):
    __tablename__ = "personal_api_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(20))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    repository_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Approximate: refreshed at most every five minutes on authenticated use.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiTokenEvent(Base):
    """Feature-local lifecycle audit for issuance and revocation."""

    __tablename__ = "api_token_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_id: Mapped[str] = mapped_column(ForeignKey("personal_api_tokens.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
