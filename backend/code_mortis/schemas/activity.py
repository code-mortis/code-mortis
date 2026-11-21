from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from code_mortis.schemas.common import StrictModel

ActivityKind = Literal["review", "scan", "context", "repository_summary", "pr_summary"]


class ActivityItem(StrictModel):
    kind: ActivityKind
    id: str
    repository: str
    title: str
    href: str
    status: str
    stages: list[str]
    finished_at: datetime | None
    unread: bool


class ActivityFeed(StrictModel):
    running: list[ActivityItem]
    updates: list[ActivityItem]
    running_count: int
    queued_count: int
    unread_count: int
    as_of: datetime
    snapshot: str
    next_running_cursor: str | None
    next_updates_cursor: str | None


class ActivityReadOne(StrictModel):
    mode: Literal["one"]
    kind: ActivityKind
    id: str = Field(min_length=1, max_length=36)
    finished_at: AwareDatetime | None = None


class ActivityReadAll(StrictModel):
    mode: Literal["all"]
    as_of: AwareDatetime
    snapshot: str = Field(min_length=1, max_length=32768)


ActivityRead = Annotated[ActivityReadOne | ActivityReadAll, Field(discriminator="mode")]


class SummaryAttemptOut(StrictModel):
    id: str
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    finished_at: datetime | None
