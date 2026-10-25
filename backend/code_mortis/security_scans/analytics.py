"""Read-only analytics over saved security scans and unique findings."""

from collections import defaultdict

from sqlalchemy import func

from code_mortis.models import SecurityFinding, SecurityScan
from code_mortis.reviews.analytics import SEVERITIES, Scope
from code_mortis.schemas.analytics import AnalyticsSecurity


def security(db, user, filters):
    scope = Scope(db, user, filters, models=(SecurityScan, SecurityFinding))
    trend = scope.trend()
    outcomes, severities, kinds, states = (defaultdict(int) for _ in range(4))
    scan_date = func.date_trunc(scope.bucket, func.timezone(filters.timezone, SecurityScan.created_at))
    scans = scope.query(SecurityScan, scan_date, SecurityScan.status, func.count()).group_by(
        scan_date, SecurityScan.status,
    )
    for date, status, count in db.execute(scans):
        trend[date.date().isoformat()].runs += count
        outcomes[status] += count
    finding_date = func.date_trunc(scope.bucket, func.timezone(filters.timezone, SecurityFinding.created_at))
    fields = (finding_date, SecurityFinding.severity, SecurityFinding.kind, SecurityFinding.state)
    findings = scope.query(SecurityFinding, *fields, func.count()).group_by(*fields)
    for date, severity, kind, state, count in db.execute(findings):
        trend[date.date().isoformat()].findings += count
        severities[severity] += count
        kinds[kind] += count
        states[state] += count
    return AnalyticsSecurity(
        scope=scope.meta, scans=sum(outcomes.values()), findings=sum(severities.values()),
        open=states["open"], resolved=states["resolved"], trend=list(trend.values()),
        severities=scope.counts(severities, SEVERITIES), kinds=scope.counts(kinds),
        states=scope.counts(states, ("open", "resolved", "false_positive", "accepted_risk")),
        outcomes=scope.counts(outcomes),
    )
