from datetime import datetime
from typing import Literal

from pydantic import Field

from code_mortis.schemas.code_graph import GitSha, GraphFailure, ParseDiagnostic, RepositoryPath, StrictModel


class OversizedGraphFile(StrictModel):
    path: str
    size_bytes: int
    limit_bytes: int


class GraphFileWarning(StrictModel):
    path: RepositoryPath
    reason: Literal["syntax_error", "invalid_encoding", "parse_error"]
    diagnostic: ParseDiagnostic | None = None


class GraphCoverage(StrictModel):
    parsed: int = 0
    partial: int = 0
    unsupported: int = 0
    failed: int = 0
    too_large: int = 0
    visible_files: int = 0
    complete: bool = False
    reasons: dict[str, int] = Field(default_factory=dict)
    oversized_files: list[OversizedGraphFile] = Field(default_factory=list, max_length=20)
    warning_files: list[GraphFileWarning] = Field(default_factory=list, max_length=20)


class CodeGraphStatus(StrictModel):
    enabled: bool = False
    paused: bool = False
    state: Literal["disabled", "not_indexed", "queued", "running", "ready", "partial", "stale", "failed", "cancelled"] = "disabled"
    refreshing: bool = False
    commit_sha: GitSha | None = None
    indexed_sha: GitSha | None = None
    snapshot_id: str | None = None
    checked_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    failure: GraphFailure | None = None
    coverage: GraphCoverage | None = None
    previous_coverage: GraphCoverage | None = None
