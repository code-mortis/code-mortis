"""Reuse unchanged file coverage from an earlier review of the same pull request."""

from mortis_engine import __version__ as engine_version
from sqlalchemy import select

from code_mortis.core import source
from code_mortis.core.db import transaction
from code_mortis.models import AnalysisPlan, ReviewRun
from code_mortis.reviews import planning

VERSION = "pr-comparison/v1"


def signature(selection, settings, versions, connection, secret, redaction_rules):
    return planning.fingerprint([
        VERSION, engine_version, selection.base_sha, selection.merge_base, selection.policy_digest,
        settings, versions, connection, planning.fingerprint(secret),
        sorted(planning.fingerprint(rule) for rule in redaction_rules),
    ])


def select_baseline(run_id, signature, selection, repo, env, stopped):
    comparison = {"signature": signature}
    if selection.limit_reason or not selection.selected:
        return comparison
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        if run.trigger != "automatic":
            return comparison
        prior = db.scalar(select(ReviewRun).where(
            ReviewRun.pr_id == run.pr_id, ReviewRun.created_at < run.created_at,
        ).order_by(ReviewRun.created_at.desc(), ReviewRun.id.desc()).limit(1))
        if (not prior or prior.status != "completed" or prior.cancel_requested
                or prior.publication not in {"published", "no_new_comments"}
                or (prior.stages or {}).get("assessment", {}).get("status") != "completed"):
            return comparison
        report = prior.report or {}
        saved = db.get(AnalysisPlan, prior.progress.get("analysis_plan_id")) if prior.progress.get("analysis_plan_id") else None
        if not saved or saved.run_id != prior.id:
            return comparison
        snapshot = saved.snapshot
        earlier = snapshot["settings"].get("comparison", {})
        if earlier.get("signature") != signature or snapshot["revisions"]["head"] != prior.head_sha:
            return comparison
        baseline_id, baseline_head = prior.id, prior.head_sha

    manifest = report.get("manifest", {})
    coverage = manifest.get("coverage", {})
    if (manifest.get("schema_version") != "code-mortis.pr-analysis/v1"
            or manifest.get("terminal_state") != "completed" or report.get("errors")
            or coverage.get("failed") or coverage.get("waived")):
        return comparison
    selected = {item["path"] for item in coverage.get("selected", [])}
    covered = {item["path"] for kind in ("completed", "reused") for item in coverage.get(kind, [])}
    previous_files = {item["path"]: item for item in snapshot["selected"]}
    if not selected or selected != covered or selected != previous_files.keys():
        return comparison

    try:
        ancestor = source.git(repo, env, "merge-base", baseline_head, selection.head_sha, stopped=stopped).strip()
        if ancestor != baseline_head:
            return comparison
        changed = {value for path, old_path, _ in planning._changes(repo, env, baseline_head, selection.head_sha, stopped)
                   for value in (path, old_path) if value is not None}
    except ValueError:
        return comparison  # The bounded snapshot may not contain the previous head.

    paths = []
    for item in selection.selected:
        before = previous_files.get(item.path)
        if (before and item.units and not item.error and item.path not in changed and item.old_path not in changed
                and (item.old_path, item.status, item.blob_sha)
                == (before["old_path"], before["status"], before["blob_sha"])):
            paths.append(item.path)
    if paths:
        comparison.update(baseline_run_id=baseline_id, baseline_head_sha=baseline_head, reused_paths=paths)
    return comparison
