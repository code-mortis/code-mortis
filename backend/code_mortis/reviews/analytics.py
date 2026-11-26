"""Read-only analytics over saved, permission-scoped review evidence."""

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import ARRAY, JSON, String, any_, case, column, func, literal, select, true, union_all

from code_mortis.core.db import now
from code_mortis.core.review_results import report_tokens
from code_mortis.integrations import github
from code_mortis.models import Finding, FindingVerification, Job, PullRequest, ReviewCandidate, ReviewRun
from code_mortis.schemas.analytics import (
    AnalyticsCount,
    AnalyticsFindings,
    AnalyticsOverview,
    AnalyticsPoint,
    AnalyticsRepository,
    AnalyticsReviews,
    AnalyticsScope,
    AnalyticsUsage,
    AnalyticsUsageGroup,
    AnalyticsUsageTotals,
)

SEVERITIES = ("critical", "high", "medium", "low")
STATUSES = ("open", "resolved", "dismissed")
STAGES = ("review", "assessment", "verification", "summary")


def ordered(rows, ordering, key=lambda row: row.key, count=lambda row: row.count):
    if not ordering:
        return rows
    rows = sorted(rows, key=lambda row: key(row).casefold())
    numeric = ordering.removeprefix("-") == "count"
    rows = sorted(rows, key=(lambda row: count(row) or 0) if numeric else lambda row: key(row).casefold(),
                  reverse=ordering.startswith("-"))
    if numeric:
        rows.sort(key=lambda row: count(row) is None)
    return rows


def counts(values, order=(), ordering=None):
    values = dict(values)
    keys = list(order) + sorted(set(values) - set(order), key=lambda key: (-values[key], key))
    return ordered([AnalyticsCount(key=key, count=values.get(key, 0)) for key in keys], ordering)


class Scope:
    def counts(self, values, order=()):
        return counts(values, order, self.filters.ordering)

    def __init__(self, db, user, filters, models=(ReviewRun, Finding)):
        self.db, self.filters = db, filters
        repositories = github.visible_repositories(db, user)
        permitted = {repo.id for repo in repositories}
        if set(filters.repo) - permitted:
            raise HTTPException(404, "Repository not available.")
        self.repositories = [repo for repo in repositories if not filters.repo or repo.id in filters.repo]
        self.ids = [repo.id for repo in self.repositories]
        self.zone = ZoneInfo(filters.timezone)
        self.end = filters.to_at or now()
        local_end = self.end.astimezone(self.zone)
        self.start = filters.from_at or (local_end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29))
        if filters.all_time:
            firsts = union_all(*[
                self.source(model, func.min(model.created_at).label("first")).where(model.created_at < self.end)
                for model in models
            ]).subquery()
            self.start = db.scalar(select(func.min(firsts.c.first))) or self.start
        if self.start >= self.end:
            raise HTTPException(422, "The start must be before the end.")
        days = (local_end.date() - self.start.astimezone(self.zone).date()).days
        self.bucket = "day" if days <= 90 else "week" if days <= 365 else "month"
        self.meta = AnalyticsScope(
            from_at=self.start, to_at=self.end, timezone=filters.timezone, bucket=self.bucket,
            repositories=[{"id": repo.id, "full_name": repo.full_name} for repo in repositories],
        )

    def source(self, model, *fields):
        query = select(*fields).select_from(model)
        if hasattr(model, "repository_id"):
            return query.where(model.repository_id.in_(self.ids))
        return query.join(PullRequest, PullRequest.id == model.pr_id).where(PullRequest.repository_id.in_(self.ids))

    def query(self, model, *fields):
        return self.source(model, *fields).where(model.created_at >= self.start, model.created_at < self.end)

    def date(self, timestamp):
        local = timestamp.astimezone(self.zone).date()
        if self.bucket == "week":
            local -= timedelta(days=local.weekday())
        elif self.bucket == "month":
            local = local.replace(day=1)
        return local.isoformat()

    def trend(self):
        points = {}
        day = datetime.fromisoformat(self.date(self.start)).date()
        last = (self.end - timedelta(microseconds=1)).astimezone(self.zone).date()
        while day <= last:
            points[day.isoformat()] = AnalyticsPoint(date=day.isoformat())
            if self.bucket == "month":
                day = day.replace(year=day.year + (day.month == 12), month=day.month % 12 + 1, day=1)
            else:
                day += timedelta(days=7 if self.bucket == "week" else 1)
        return points

    def grouped_findings(self, *conditions, by_repository=False):
        bucket = func.date_trunc(self.bucket, func.timezone(self.filters.timezone, Finding.created_at))
        fields = (bucket.label("date"), Finding.severity, Finding.category, Finding.status)
        if by_repository:
            fields = (PullRequest.repository_id, *fields)
        query = self.query(Finding, *fields, func.count().label("count")).where(*conditions).group_by(*fields)
        return list(self.db.execute(query).mappings())

    def runs(self):
        # Project only analytics fields; report bodies, configuration secrets and evidence stay server-side.
        return list(self.db.execute(self.query(
            ReviewRun, ReviewRun.id, ReviewRun.pr_id, PullRequest.repository_id, PullRequest.author,
            ReviewRun.status, ReviewRun.created_at, ReviewRun.started_at, ReviewRun.finished_at,
            ReviewRun.config["pipeline_version"].label("pipeline"),
            ReviewRun.config["assessment_policy"].as_string().label("assessment_policy"),
            ReviewRun.config["connection"]["model"].as_string().label("model"),
            ReviewRun.config["settings"]["effort"].as_string().label("effort"),
            ReviewRun.report["summary"]["total_tokens"].label("tokens"),
            ReviewRun.summary["tokens"].label("summary_tokens"),
            ReviewRun.summary["status"].as_string().label("summary_status"),
            ReviewRun.progress["summary_retry"].as_string().label("summary_retry"),
        )).mappings())


def finding_totals(rows):
    severity, category, status = defaultdict(int), defaultdict(int), defaultdict(int)
    for row in rows:
        severity[row["severity"]] += row["count"]
        category[row["category"]] += row["count"]
        status[row["status"]] += row["count"]
    return severity, category, status


def amount(value):
    return value if type(value) is int and value >= 0 else None


def recorded_usage(usage, status, has_result, legacy):
    if usage == {}:
        return 0
    if not isinstance(usage, dict):
        return None
    # Legacy interrupted checks stored a budget in `tokens`, indistinguishable from consumption.
    if legacy and (status != "completed" or not has_result):
        return None
    return amount(usage.get("tokens"))


def usage_records(db, runs):
    by_id = {row["id"]: row for row in runs}
    # Bind the IDs once, including when both branches of the candidate query use them.
    run_ids = any_(literal(list(by_id), ARRAY(String(36))))
    records = []

    def add(run, stage, tokens, model=None, effort=None):
        records.append({"run": run, "stage": stage, "tokens": tokens,
                        "model": model or run["model"] or "Not recorded",
                        "effort": effort or run["effort"] or "Not recorded"})

    for run in runs:
        unused = not run["started_at"] and run["status"] in {"queued", "blocked", "skipped", "cancelled", "superseded"}
        add(run, "review", 0 if unused and run["tokens"] is None else amount(run["tokens"]))

    candidates = select(
        ReviewCandidate.run_id, ReviewCandidate.usage, ReviewCandidate.status,
        ReviewCandidate.result["verdict"].as_string().is_not(None).label("has_result"),
    ).where(ReviewCandidate.run_id == run_ids)
    attempt = func.json_array_elements(ReviewCandidate.attempts).table_valued(column("value", JSON)).lateral()
    attempts = select(
        ReviewCandidate.run_id, attempt.c.value["usage"], attempt.c.value["status"].as_string(),
        attempt.c.value["result"]["verdict"].as_string().is_not(None),
    ).select_from(ReviewCandidate).join(attempt, true()).where(ReviewCandidate.run_id == run_ids)
    for run_id, usage, status, has_result in db.execute(union_all(candidates, attempts)):
        run = by_id[run_id]
        add(run, "assessment", recorded_usage(usage, status, has_result, run["pipeline"] != 2))
    for run_id, usage, status, verdict in db.execute(select(
        FindingVerification.run_id, FindingVerification.usage, FindingVerification.status, FindingVerification.verdict,
    ).where(FindingVerification.run_id == run_ids)):
        run = by_id[run_id]
        add(run, "verification", recorded_usage(usage, status, verdict is not None, run["pipeline"] != 2))

    retries = defaultdict(list)
    for job in db.execute(select(
        Job.id,
        Job.payload["run_id"].as_string().label("run_id"),
        Job.payload["previous_summary"]["tokens"].label("tokens"),
        Job.payload["previous_summary"]["inference_started_at"].as_string().label("started_at"),
        Job.payload["config"]["connection"]["model"].as_string().label("model"),
        Job.payload["config"]["settings"]["effort"].as_string().label("effort"),
    ).where(Job.kind == "generate_summary", Job.payload["run_id"].as_string() == run_ids)).mappings():
        retries[job["run_id"]].append(job)
    for run in runs:
        jobs = retries[run["id"]]
        previous = (run["model"], run["effort"])
        ordered = len(jobs) <= 1
        if len(jobs) > 1:
            try:
                timestamps = {job["id"]: datetime.fromisoformat(job["started_at"]) for job in jobs}
                ordered = all(value.tzinfo for value in timestamps.values()) and len(set(timestamps.values())) == len(jobs)
                if ordered:
                    jobs.sort(key=lambda job: timestamps[job["id"]])
            except (TypeError, ValueError):
                ordered = False
        if not ordered:
            # Dispatch times change on failure; ambiguous legacy retry order cannot identify a model.
            previous = tuple(next(iter(values)) if len(values) == 1 else "Not recorded" for values in (
                {run[key] or "Not recorded", *(job[key] or "Not recorded" for job in jobs)} for key in ("model", "effort")
            ))
        for job in jobs:
            add(run, "summary", amount(job["tokens"]), *previous)
            if ordered:
                previous = (job["model"] or "Not recorded", job["effort"] or "Not recorded")
        current = next((job for job in jobs if job["id"] == run["summary_retry"]), None)
        if current:
            previous = (current["model"] or "Not recorded", current["effort"] or "Not recorded")
        if run["summary_status"] not in {None, "not_applicable"}:
            add(run, "summary", amount(run["summary_tokens"]), *previous)
    return records


def run_tokens(db, run):
    records = usage_records(db, [{
        "id": run.id, "pr_id": run.pr_id, "status": run.status, "started_at": run.started_at,
        "pipeline": run.config.get("pipeline_version"),
        "model": run.config.get("connection", {}).get("model"),
        "effort": run.config.get("settings", {}).get("effort"),
        "tokens": report_tokens(run.report),
        "summary_tokens": run.summary.get("tokens"), "summary_status": run.summary.get("status"),
        "summary_retry": run.progress.get("summary_retry"),
    }])
    return None if any(row["tokens"] is None for row in records) else sum(row["tokens"] for row in records)


def usage_totals(records):
    known = [row["tokens"] for row in records if row["tokens"] is not None]
    runs, prs, reviewed = defaultdict(list), defaultdict(list), set()
    for row in records:
        run = row["run"]
        runs[run["id"]].append(row["tokens"])
        prs[run["pr_id"]].append(row["tokens"])
        if run["status"] in {"completed", "partial"}:
            reviewed.add(run["pr_id"])
    complete_runs = [sum(values) for values in runs.values() if None not in values]
    complete_prs = [sum(values) for pr, values in prs.items() if pr in reviewed and None not in values]
    return AnalyticsUsageTotals(
        tokens=sum(known) if known else (0 if not records else None), known_reports=len(known),
        missing_reports=len(records) - len(known), total_runs=len(runs), complete_runs=len(complete_runs),
        mean_per_run=sum(complete_runs) / len(complete_runs) if complete_runs else None,
        reviewed_pr_samples=len(complete_prs),
        mean_per_reviewed_pr=sum(complete_prs) / len(complete_prs) if complete_prs else None,
    )


def overview(scope):
    runs, rows = scope.runs(), scope.grouped_findings(by_repository=True)
    severity, category, status = finding_totals(rows)
    points = scope.trend()
    repositories = {repo.id: AnalyticsRepository(id=repo.id, name=repo.full_name) for repo in scope.repositories}
    reviewed = defaultdict(set)
    for run in runs:
        points[scope.date(run["created_at"])].runs += 1
        if run["status"] in {"completed", "partial"}:
            reviewed[run["repository_id"]].add(run["pr_id"])
    for row in rows:
        points[row["date"].date().isoformat()].findings += row["count"]
        repo = repositories[row["repository_id"]]
        repo.findings += row["count"]
        if row["status"] == "resolved":
            repo.resolved += row["count"]
    for repo in repositories.values():
        repo.reviewed_prs = len(reviewed[repo.id])
    return AnalyticsOverview(
        scope=scope.meta, reviewed_prs=sum(len(values) for values in reviewed.values()),
        findings=sum(status.values()), resolved=status["resolved"], usage=usage_totals(usage_records(scope.db, runs)),
        trend=list(points.values()), severities=scope.counts(severity, SEVERITIES), categories=scope.counts(category),
        repositories=ordered(sorted(repositories.values(), key=lambda repo: (-repo.reviewed_prs, repo.name))[:5],
            scope.filters.ordering, key=lambda repo: repo.name, count=lambda repo: repo.reviewed_prs),
    )


def findings(scope):
    conditions = [
        column.in_(values) for column, values in (
            (Finding.severity, scope.filters.severity),
            (Finding.category, scope.filters.category),
            (Finding.status, scope.filters.status),
        ) if values
    ]
    rows = scope.grouped_findings(*conditions)
    severity, category, status = finding_totals(rows)
    unfiltered_categories = category
    if conditions:
        category_counts = scope.query(Finding, Finding.category, func.count()).group_by(Finding.category)
        unfiltered_categories = dict(scope.db.execute(category_counts).all())
    points = scope.trend()
    for row in rows:
        points[row["date"].date().isoformat()].findings += row["count"]
    return AnalyticsFindings(
        scope=scope.meta, total=sum(status.values()), unfiltered_total=sum(unfiltered_categories.values()),
        category_options=sorted(unfiltered_categories), severities=scope.counts(severity, SEVERITIES),
        categories=scope.counts(category), statuses=scope.counts(status, STATUSES), trend=list(points.values()),
    )


def reviews(scope):
    runs = scope.runs()
    outcomes, contributors = defaultdict(int), defaultdict(set)
    reviewed, durations = set(), []
    for run in runs:
        outcomes[run["status"]] += 1
        if run["status"] in {"completed", "partial"}:
            reviewed.add(run["pr_id"])
            contributors[run["author"]].add(run["pr_id"])
        if run["started_at"] and run["finished_at"] and run["status"] not in {"queued", "running", "blocked"}:
            seconds = (run["finished_at"] - run["started_at"]).total_seconds()
            if seconds >= 0:
                durations.append(seconds)
    coverage, missing = defaultdict(int), 0
    for manifest in scope.db.scalars(scope.query(ReviewRun, ReviewRun.report["manifest"]["coverage"])):
        keys = ("selected", "completed", "reused", "failed", "waived")
        if not isinstance(manifest, dict) or any(not isinstance(manifest.get(key), list) for key in keys):
            missing += 1
            continue
        # Only manifests with disjoint, exhaustive application-assigned result IDs establish coverage.
        identities = [[item.get("item_id") if isinstance(item, dict) else None for item in manifest[key]] for key in keys]
        selected, *results = identities
        reported = [value for group in results for value in group]
        if (any(not isinstance(value, str) for group in identities for value in group)
            or len(set(selected)) != len(selected) or len(set(reported)) != len(reported) or set(selected) != set(reported)):
            missing += 1
            continue
        for key in keys:
            coverage[key] += len(manifest[key])
    run_ids = any_(literal([run["id"] for run in runs], ARRAY(String(36))))
    outcome = case(
        (ReviewCandidate.finding_id.is_not(None) & ReviewCandidate.is_new, "new"),
        (ReviewCandidate.finding_id.is_not(None), "repeated"),
        (ReviewCandidate.result["verdict"].as_string() == "rejected", "rejected"),
        (ReviewCandidate.status.in_(["queued", "running"]), "pending"), else_="withheld",
    )
    assessments = scope.db.execute(select(outcome, func.count())
        .where(ReviewCandidate.run_id == run_ids).group_by(outcome)).all()
    return AnalyticsReviews(
        scope=scope.meta, runs=len(runs), reviewed_prs=len(reviewed),
        mean_duration_seconds=mean(durations) if durations else None,
        median_duration_seconds=median(durations) if durations else None, duration_samples=len(durations),
        outcomes=scope.counts(outcomes), coverage=scope.counts(coverage, ("selected", "completed", "reused", "failed", "waived")),
        coverage_missing_runs=missing, assessments=scope.counts(assessments, ("new", "repeated", "rejected", "withheld", "pending")),
        assessment_missing_runs=sum(not run["assessment_policy"] for run in runs),
        contributors=scope.counts({author: len(prs) for author, prs in contributors.items()}),
    )


def usage(scope):
    records = usage_records(scope.db, scope.runs())
    points = scope.trend()
    groups = {key: defaultdict(list) for key in ("stage", "repository", "model", "effort")}
    names = {repo.id: repo.full_name for repo in scope.repositories}
    for record in records:
        point = points[scope.date(record["run"]["created_at"])]
        if record["tokens"] is None:
            point.missing_reports += 1
        else:
            point.tokens = (point.tokens or 0) + record["tokens"]
        for dimension in groups:
            key = record["run"]["repository_id"] if dimension == "repository" else record[dimension]
            groups[dimension][key].append(record)
    for point in points.values():
        if not point.missing_reports and point.tokens is None:
            point.tokens = 0

    def grouped(dimension):
        rows = [AnalyticsUsageGroup(
            key=names[key] if dimension == "repository" else key,
            repository_id=key if dimension == "repository" else None, usage=usage_totals(values),
        ) for key, values in groups[dimension].items()]
        return ordered(sorted(rows, key=lambda row: (-(row.usage.tokens or 0), row.key)),
                       scope.filters.ordering, count=lambda row: row.usage.tokens)

    return AnalyticsUsage(
        scope=scope.meta, total=usage_totals(records), trend=list(points.values()),
        stages=ordered([AnalyticsUsageGroup(key=stage, usage=usage_totals(groups["stage"][stage])) for stage in STAGES],
            scope.filters.ordering, count=lambda row: row.usage.tokens),
        repositories=grouped("repository"), models=grouped("model"), efforts=grouped("effort"),
    )
