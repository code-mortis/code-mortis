"""Grade finished security reports from their saved findings, preserving incomplete coverage."""

import sqlalchemy as sa
from alembic import op

revision = "20261021_report_grades"
down_revision = "20261020_security_scans"
branch_labels = None
depends_on = None


def upgrade():
    scans = sa.table("security_scans", sa.column("id", sa.String()), sa.column("status", sa.String()),
                     sa.column("grade", sa.String()), sa.column("stages", sa.JSON()), sa.column("coverage", sa.JSON()),
                     sa.column("report", sa.JSON()), sa.column("finished_at", sa.DateTime(timezone=True)))
    connection = op.get_bind()
    for row in connection.execute(sa.select(scans).where(scans.c.grade.is_(None),
        scans.c.status.in_(["completed", "partial"]), scans.c.finished_at.is_not(None))).mappings():
        if row["stages"].get("report", {}).get("status") != "completed":
            continue
        coverage, report = row["coverage"], row["report"]
        if report.get("schema_version") != "code-mortis.security/v1":
            continue
        if not coverage.get("targets") and not any(value.get("code") for value in coverage.get("inventory", {}).values()):
            continue
        severities = {finding.get("severity") for finding in report.get("findings", [])
                      if finding.get("state") in {"open", "accepted_risk"}}
        if severities - {"critical", "high", "medium", "low"}:
            continue
        grade = next((grade for severity, grade in [("critical", "F"), ("high", "D"), ("medium", "C"), ("low", "B")]
                      if severity in severities), "A")
        connection.execute(scans.update().where(scans.c.id == row["id"]).values(grade=grade, report={**report, "grade": grade}))


def downgrade():
    pass  # Keep grades derived from immutable report evidence.
