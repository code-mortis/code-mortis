from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from code_mortis.schemas.common import InboxRepository, StrictModel


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Choose a valid IANA timezone.") from exc
    return value


AnalyticsTimezone = Annotated[str, Field(max_length=100), AfterValidator(valid_timezone)]


class AnalyticsFilters(StrictModel):
    ordering: Literal["count", "-count", "key", "-key"] | None = None
    repo: list[Annotated[int, Field(gt=0, le=9007199254740991)]] = Field(default_factory=list, max_length=100)
    from_at: AwareDatetime | None = None
    to_at: AwareDatetime | None = None
    all_time: bool = False
    timezone: AnalyticsTimezone = "UTC"

    @model_validator(mode="after")
    def valid_dates(self):
        if self.all_time and self.from_at is not None:
            raise ValueError("All time cannot have a start date.")
        if self.from_at and self.to_at and self.from_at >= self.to_at:
            raise ValueError("The start must be before the end.")
        return self


class AnalyticsFindingFilters(AnalyticsFilters):
    severity: list[Literal["critical", "high", "medium", "low"]] = Field(default_factory=list, max_length=4)
    category: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(default_factory=list, max_length=100)
    status: list[Literal["open", "resolved", "dismissed"]] = Field(default_factory=list, max_length=3)


class RepositoryActivityFilters(StrictModel):
    timezone: AnalyticsTimezone = "UTC"


class RepositoryActivityDay(StrictModel):
    date: date
    reviews: int
    scans: int


class RepositoryActivity(StrictModel):
    days: list[RepositoryActivityDay]


class AnalyticsScope(StrictModel):
    from_at: datetime
    to_at: datetime
    timezone: str
    bucket: Literal["day", "week", "month"]
    repositories: list[InboxRepository]


class AnalyticsCount(StrictModel):
    key: str
    count: int


class AnalyticsPoint(StrictModel):
    date: str
    runs: int = 0
    findings: int = 0
    tokens: int | None = None
    missing_reports: int = 0


class AnalyticsUsageTotals(StrictModel):
    tokens: int | None = None
    known_reports: int = 0
    missing_reports: int = 0
    complete_runs: int = 0
    total_runs: int = 0
    mean_per_run: float | None = None
    reviewed_pr_samples: int = 0
    mean_per_reviewed_pr: float | None = None


class AnalyticsRepository(StrictModel):
    id: int
    name: str
    reviewed_prs: int = 0
    findings: int = 0
    resolved: int = 0


class AnalyticsOverview(StrictModel):
    scope: AnalyticsScope
    reviewed_prs: int
    findings: int
    resolved: int
    usage: AnalyticsUsageTotals
    trend: list[AnalyticsPoint]
    severities: list[AnalyticsCount]
    categories: list[AnalyticsCount]
    repositories: list[AnalyticsRepository]


class AnalyticsFindings(StrictModel):
    scope: AnalyticsScope
    total: int
    unfiltered_total: int
    category_options: list[str]
    severities: list[AnalyticsCount]
    categories: list[AnalyticsCount]
    statuses: list[AnalyticsCount]
    trend: list[AnalyticsPoint]


class AnalyticsReviews(StrictModel):
    scope: AnalyticsScope
    runs: int
    reviewed_prs: int
    mean_duration_seconds: float | None
    median_duration_seconds: float | None
    duration_samples: int
    outcomes: list[AnalyticsCount]
    coverage: list[AnalyticsCount]
    coverage_missing_runs: int
    assessments: list[AnalyticsCount]
    assessment_missing_runs: int
    contributors: list[AnalyticsCount]


class AnalyticsSecurity(StrictModel):
    scope: AnalyticsScope
    scans: int
    findings: int
    open: int
    resolved: int
    trend: list[AnalyticsPoint]
    severities: list[AnalyticsCount]
    kinds: list[AnalyticsCount]
    states: list[AnalyticsCount]
    outcomes: list[AnalyticsCount]


class AnalyticsUsageGroup(StrictModel):
    key: str
    repository_id: int | None = None
    usage: AnalyticsUsageTotals


class AnalyticsUsage(StrictModel):
    scope: AnalyticsScope
    total: AnalyticsUsageTotals
    trend: list[AnalyticsPoint]
    stages: list[AnalyticsUsageGroup]
    repositories: list[AnalyticsUsageGroup]
    models: list[AnalyticsUsageGroup]
    efforts: list[AnalyticsUsageGroup]
