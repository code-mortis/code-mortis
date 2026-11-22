from sqlalchemy import and_, func, select, true

from code_mortis.core.analysis import fingerprint
from code_mortis.core.db import now
from code_mortis.core.security import digest
from code_mortis.models import FindingVerification, Occurrence, ReviewCandidate, ReviewRuleUse
from code_mortis.reviews.planning import matching_custom_rules


def snapshot(saved_rules, execution_rules):
    # Rule names, activation and target ordering do not change the instruction or its scope.
    records = [
        {"id": saved["rule_id"], "version": fingerprint({
            "instructions": rule["instructions"],
            "files": sorted(set(rule.get("files", ()))),
            "globs": sorted(set(filter(None, [rule["glob"], *rule.get("globs", ())]))),
        })} if saved.get("rule_id") else None
        for saved, rule in zip(saved_rules, execution_rules, strict=True)
    ]
    return records if any(records) else None


def record_completed(db, run, status, report):
    if not run.rule_tracking or run.engine != "mortis" or status not in {"completed", "partial"}:
        return
    manifest = report.get("manifest", {})
    if manifest.get("schema_version") != "code-mortis.pr-analysis/v1":
        return
    paths = dict.fromkeys(item["path"] for item in manifest["coverage"]["completed"])
    rules = run.config["settings"]["review_rules"]
    completed_at = now()
    for path in paths:
        for index, _ in matching_custom_rules(path, rules):
            if record := run.rule_tracking[index]:
                db.add(ReviewRuleUse(run_id=run.id, rule_id=record["id"], version=record["version"],
                                     path=path, path_hash=digest(path), used_at=completed_at))


def finding_outcomes(rule_id):
    """Select dated confirmations per use and finding, with fixes credited to their verified occurrence."""
    confirmed = select(ReviewCandidate.finding_id, func.min(ReviewCandidate.finished_at).label("confirmed_at")).where(
        ReviewCandidate.run_id == ReviewRuleUse.run_id,
        ReviewCandidate.snapshot["path"].as_string() == ReviewRuleUse.path,
        ReviewCandidate.status == "completed",
        ReviewCandidate.result["verdict"].as_string() == "confirmed",
        ReviewCandidate.finished_at.is_not(None),
    ).group_by(ReviewCandidate.finding_id).correlate(ReviewRuleUse).lateral("confirmed")
    fixed_at = select(func.min(FindingVerification.finished_at)).where(
        FindingVerification.finding_id == Occurrence.finding_id,
        FindingVerification.occurrence_id == Occurrence.id,
        Occurrence.snapshot["path"].as_string() == ReviewRuleUse.path,
        FindingVerification.status == "completed",
        FindingVerification.verdict == "fixed",
    ).scalar_subquery()
    return select(ReviewRuleUse, confirmed.c.finding_id, confirmed.c.confirmed_at, fixed_at.label("fixed_at")).select_from(
        ReviewRuleUse,
    ).join(confirmed, true()).join(Occurrence, and_(
        Occurrence.run_id == ReviewRuleUse.run_id, Occurrence.finding_id == confirmed.c.finding_id,
    )).where(ReviewRuleUse.rule_id == rule_id)
