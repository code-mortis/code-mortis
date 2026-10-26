from datetime import datetime
from typing import Literal

from code_mortis.schemas.code_graph import GitSha, StrictModel


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
