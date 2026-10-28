from datetime import datetime
from typing import Literal

from pydantic import Field

from code_mortis.schemas.code_graph import GitSha, StrictModel


class GraphCoverage(StrictModel):
    parsed: int = 0
    partial: int = 0
    unsupported: int = 0
    failed: int = 0
    too_large: int = 0
    visible_files: int = 0
    complete: bool = False
    reasons: dict[str, int] = Field(default_factory=dict)


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
    coverage: GraphCoverage | None = None
