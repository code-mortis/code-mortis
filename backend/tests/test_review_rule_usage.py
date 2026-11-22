from datetime import UTC, datetime, timedelta
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from test_review_flow import new_run
from test_review_rules import rule, update_rules

from code_mortis.core.db import transaction
from code_mortis.models import (
    Finding,
    FindingVerification,
    Occurrence,
    Repository,
    ReviewCandidate,
    ReviewRuleUse,
    ReviewRun,
)
from code_mortis.reviews import assessment, rule_usage, service
from code_mortis.schemas.common import RepoSettings

STAMP = datetime(2026, 11, 22, tzinfo=UTC)


def report(*paths):
    return {"manifest": {"schema_version": "code-mortis.pr-analysis/v1", "coverage": {
        "completed": [{"path": path} for path in paths], "failed": [{"path": "src/incomplete.py"}],
    }}}


def claim(run_id):
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        run.status = "running"
        run.progress = {**run.progress, "execution": "test-claim"}


def test_records_completed_files_once_from_frozen_rules_even_after_deletion(seed, client, github_stub, monkeypatch):
    rules = update_rules(client, [], "add", rule={**rule("Scope", "Inspect changed code."),
                                                 "files": ["src/main.py"], "globs": ["src/**/*.py"]}).json()
    rules = update_rules(client, rules, "add", rule=rule("All", "Check every file.")).json()
    rules = update_rules(client, rules, "add", rule=rule("Off", "Disabled instructions.", active=False)).json()
    first, second, _ = (item["rule_id"] for item in rules)
    original = new_run(assessed=True, parallel=True)
    claim(original)
    frozen = rule_usage.snapshot(rules, RepoSettings.for_execution({"review_rules": rules})["review_rules"])

    removed = update_rules(client, rules, "delete", index=0)
    assert removed.status_code == 200, removed.text
    draft = {**rule("Scope", "Inspect changed code."), "files": ["src/main.py"], "globs": ["src/**/*.py"]}
    readded = update_rules(client, removed.json(), "add", rule=draft)
    assert readded.status_code == 200, readded.text
    assert readded.json()[-1]["rule_id"] != first

    monkeypatch.setattr(rule_usage, "now", lambda: STAMP)
    coverage = report("src/main.py", "README.md", "src/main.py")
    service.save_analysis(original, "stale-claim", "partial", coverage, [])
    with transaction() as db:
        assert not list(db.scalars(select(ReviewRuleUse)))
    service.save_analysis(original, "test-claim", "partial", coverage, [])
    service.save_analysis(original, "test-claim", "partial", coverage, [])
    with transaction() as db:
        uses = list(db.scalars(select(ReviewRuleUse)))
        assert len(uses) == 3
        assert {(use.rule_id, use.path, use.version, use.used_at) for use in uses} == {
            (first, "src/main.py", frozen[0]["version"], STAMP),
            (second, "src/main.py", frozen[1]["version"], STAMP),
            (second, "README.md", frozen[1]["version"], STAMP),
        }


def test_versions_follow_instructions_and_scope_not_rule_name_or_activation(seed, client, github_stub):
    raw = {**rule("Original", "Keep this wording."), "files": ["b.py", "a.py"], "globs": ["src/**", "tests/**"]}
    saved = update_rules(client, [], "add", rule=raw).json()[0]
    adjusted = {**raw, "name": "Renamed", "files": ["a.py", "b.py"], "globs": ["tests/**", "src/**"], "active": False}
    edited = update_rules(client, [saved], "edit", index=0, rule=adjusted).json()[0]

    def meta(record):
        options = RepoSettings.for_execution({"review_rules": [record]})
        assert "rule_id" not in options["review_rules"][0]
        return rule_usage.snapshot([record], options["review_rules"])[0]

    assert meta(edited) == meta(saved)
    assert meta({**edited, "instructions": "Changed wording."})["version"] != meta(edited)["version"]
    assert meta({**edited, "files": ["b.py"]})["version"] != meta(edited)["version"]
    assert update_rules(client, [edited], "add", rule={**raw, "rule_id": saved["rule_id"]}).status_code == 422


def test_only_dated_confirmations_and_exact_verified_occurrences_are_joined(seed, client, github_stub):
    draft = rule("Scope", "First version.", "src/**")
    saved = update_rules(client, [], "add", rule=draft).json()[0]
    identity = saved["rule_id"]
    original = new_run(assessed=True, parallel=True)
    claim(original)
    comment = {"path": "src/main.py", "content": "Finding", "severity": "medium", "category": "other",
               "start_line": 1, "end_line": 1, "existing_code": "", "suggestion_code": ""}
    other_path = "src/duplicate.py"
    service.save_analysis(original, "test-claim", "completed", report(comment["path"], other_path),
                          [comment, comment, {**comment, "path": other_path}])
    confirmed = {"verdict": "confirmed", "suggestion_supported": False, "relationship": "new",
                 "finding_id": None, "explanation": "Confirmed with evidence."}
    with transaction() as db:
        run = db.get(ReviewRun, original)
        candidates = list(db.scalars(select(ReviewCandidate).where(ReviewCandidate.run_id == original)
                                     .order_by(ReviewCandidate.position)))
        first, second, other = candidates
        first.status, first.finished_at, first.result = "completed", STAMP, confirmed
        assessment.accept(db, run, first, confirmed)
        duplicate = {**confirmed, "relationship": "duplicate", "finding_id": first.finding_id}
        second.status, second.finished_at, second.result = "completed", STAMP + timedelta(seconds=1), duplicate
        assessment.accept(db, run, second, duplicate)
        other.status, other.finished_at, other.result = "completed", STAMP + timedelta(seconds=2), duplicate
        assessment.accept(db, run, other, duplicate)
        finding_id = first.finding_id
        db.get(Finding, finding_id).status = "resolved"
        run.status = "completed"
        first_occurrence = db.scalar(select(Occurrence).where(Occurrence.run_id == original)).id
        outcomes = {row[0].path: (row.finding_id, row.confirmed_at, row.fixed_at)
                    for row in db.execute(rule_usage.finding_outcomes(identity))}
        assert outcomes == {comment["path"]: (finding_id, STAMP, None),
                            other_path: (finding_id, STAMP + timedelta(seconds=2), None)}

    edited = update_rules(client, [saved], "edit", index=0, rule={**draft, "instructions": "Later version."})
    assert edited.status_code == 200, edited.text
    later = new_run(assessed=True, parallel=True)
    claim(later)
    service.save_analysis(later, "test-claim", "completed", report(comment["path"]), [comment])
    fixed_at = STAMP + timedelta(days=1)
    with transaction() as db:
        run = db.get(ReviewRun, later)
        candidate = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == later))
        candidate.status, candidate.finished_at, candidate.result = "completed", fixed_at, duplicate
        assessment.accept(db, run, candidate, duplicate)
        db.add(FindingVerification(finding_id=finding_id, run_id=later, occurrence_id=first_occurrence,
                                   head_sha="c" * 40, finding_version=0, status="completed", verdict="fixed",
                                   finished_at=fixed_at))
        outcomes = {(row[0].run_id, row[0].path): (row[0].version, row.confirmed_at, row.fixed_at)
                    for row in db.execute(rule_usage.finding_outcomes(identity))}
        assert outcomes[original, comment["path"]][1:] == (STAMP, fixed_at)
        assert outcomes[original, other_path][1:] == (STAMP + timedelta(seconds=2), None)
        assert outcomes[later, comment["path"]][1:] == (fixed_at, None)
        assert outcomes[original, comment["path"]][0] != outcomes[later, comment["path"]][0]


def test_migration_assigns_existing_rule_ids_without_touching_old_runs(seed, github_stub):
    draft = rule("Existing", "Keep the definition\0 and order.")
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "review_rules": [draft, draft], "background": "Legacy\0background"}
    original = new_run(assessed=True, parallel=True)
    claim(original)
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261120_activity_snapshots")
    finally:
        command.upgrade(config, "head")
    service.save_analysis(original, "test-claim", "completed", report("README.md"), [])
    with transaction() as db:
        settings = db.get(Repository, 22).settings
        assert settings["background"] == "Legacy\0background"
        rules = settings["review_rules"]
        assert len({rule["rule_id"] for rule in rules}) == 2
        assert all(UUID(record["rule_id"]).version == 4 for record in rules)
        assert [{key: value for key, value in item.items() if key != "rule_id"} for item in rules] == [draft, draft]
        assert db.get(ReviewRun, original).config["settings"]["review_rules"] == [draft, draft]
        assert db.get(ReviewRun, original).rule_tracking is None
        assert not list(db.scalars(select(ReviewRuleUse)))
