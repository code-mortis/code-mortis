from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from test_review_flow import new_run
from test_review_rules import rule, update_rules

from code_mortis.core.db import transaction
from code_mortis.integrations import github
from code_mortis.models import (
    CustomReviewRule,
    Finding,
    FindingVerification,
    Occurrence,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRuleUse,
    ReviewRun,
    User,
)
from code_mortis.reviews import assessment, rule_usage, rules, service
from code_mortis.schemas.common import StoredReviewRule

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
    with transaction() as db:
        frozen = db.get(ReviewRun, original).rule_tracking

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
        stored = StoredReviewRule.model_validate(record)
        options = stored.for_execution()
        assert "rule_id" not in options
        return rule_usage.snapshot([stored], [options])[0]

    assert meta(edited) == meta(saved)
    assert meta({**edited, "instructions": "Changed wording."})["version"] != meta(edited)["version"]
    assert meta({**edited, "files": ["b.py"]})["version"] != meta(edited)["version"]
    assert update_rules(client, [edited], "add", rule={**raw, "rule_id": saved["rule_id"]}).status_code == 422


def test_workspace_rules_apply_to_new_repositories_without_leaking_repository_rules(seed, client, github_stub, monkeypatch):
    shared = rule("Workspace", "Shared instructions.", "**/*.py")
    local = rule("Repository", "Repository-specific instructions.")
    saved = update_rules(client, [], "add", rule=local).json()
    with transaction() as db:
        workspace_rule = CustomReviewRule(scope="workspace", position=0, definition=shared)
        db.add(workspace_rule)
        db.flush()
        shared_record = rules.record(workspace_rule).model_dump(mode="json")
        local_repo = db.get(Repository, 22)
        other = Repository(id=23, installation_id=10, full_name="team/other", enabled=True,
                           connection_id=local_repo.connection_id, settings=local_repo.settings)
        db.add(other)
        db.flush()
        other_pr = PullRequest(id="other-pr", repository_id=other.id, number=3, title="Other repository", author="owner",
                               url="https://github.com/team/other/pull/3", head_sha="a" * 40, base_sha="b" * 40,
                               head_ref="fix/other")
        db.add(other_pr)
        db.flush()
        other_run = service.create_run(db, other_pr, other, "manual", db.get(User, 7))
        assert other_run.config["settings"]["review_rules"] == [shared]
        other_run_id = other_run.id
    local_run_id = new_run(assessed=True, parallel=True)
    with transaction() as db:
        assert db.get(ReviewRun, local_run_id).config["settings"]["review_rules"] == [shared, local]
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: [db.get(Repository, i) for i in (22, 23)])
    listed = client.get("/api/v1/repositories")
    assert listed.status_code == 200, listed.text
    assert {item["id"]: item["review_rules"] for item in listed.json()} == {22: saved, 23: []}
    other_endpoint = "/api/v1/repositories/23/review-rules"
    for inaccessible in (saved, [shared_record]):
        rejected = client.put(other_endpoint, json={
            "expected_rules": inaccessible, "change": {"operation": "delete", "index": 0},
        })
        assert rejected.status_code == 409, rejected.text
    for run_id in (local_run_id, other_run_id):
        claim(run_id)
        service.save_analysis(run_id, "test-claim", "completed", report("main.py", "README.md"), [])
    with transaction() as db:
        uses = list(db.scalars(select(ReviewRuleUse)))
        assert {(use.run_id, use.rule_id, use.path) for use in uses} == {
            (local_run_id, shared_record["rule_id"], "main.py"), (other_run_id, shared_record["rule_id"], "main.py"),
            (local_run_id, saved[0]["rule_id"], "main.py"), (local_run_id, saved[0]["rule_id"], "README.md"),
        }
        assert len({use.version for use in uses if use.rule_id == shared_record["rule_id"]}) == 1


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
    original = new_run(assessed=True, parallel=True)
    claim(original)
    with transaction() as db:
        run = db.get(ReviewRun, original)
        run.config = {**run.config, "settings": {**run.config["settings"], "review_rules": [draft, draft]}}
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261120_activity_snapshots")
        with transaction() as db:
            repo = db.get(Repository, 22)
            repo.settings = {**repo.settings, "review_rules": [draft, draft], "background": "Legacy\0background"}
    finally:
        command.upgrade(config, "head")
    service.save_analysis(original, "test-claim", "completed", report("README.md"), [])
    with transaction() as db:
        settings = db.get(Repository, 22).settings
        assert settings["background"] == "Legacy\0background"
        assert "review_rules" not in settings
        records = rules.by_repository(db, [22])[22]
        assert len({row.rule_id for row in records}) == 2
        assert all(row.rule_id.version == 4 for row in records)
        assert [row.for_execution() for row in records] == [draft, draft]
        assert db.get(ReviewRun, original).config["settings"]["review_rules"] == [draft, draft]
        assert db.get(ReviewRun, original).rule_tracking is None
        assert not list(db.scalars(select(ReviewRuleUse)))


def test_saved_rule_timestamps_use_utc_at_full_precision_in_non_utc_database_session(seed):
    with transaction() as db:
        db.add(CustomReviewRule(scope="repository", repository_id=22, position=0,
                                definition=rule("Dated", "Preserve precise dates."),
                                created_at=STAMP.replace(microsecond=123456),
                                updated_at=STAMP.replace(microsecond=123457)))
    with transaction() as db:
        db.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
        row = db.scalar(select(CustomReviewRule))
        assert row.updated_at.utcoffset() == timedelta(hours=5, minutes=30)
        wire = rules.by_repository(db, [22])[22][0].model_dump(mode="json")
        assert wire["created_at"] == "2026-11-22T00:00:00.123456Z"
        assert wire["updated_at"] == "2026-11-22T00:00:00.123457Z"


def test_scoped_rule_migration_preserves_ids_order_and_history_on_round_trip(seed, github_stub):
    original = new_run(assessed=True, parallel=True)
    dated_id, undated_id = "784e9c1f-5c75-4268-b205-d334507fe35d", "c0d6d3c9-7ed5-49d4-88f3-e8d5ebad8314"
    first = rule("Duplicate", "Definition with an escaped \0")
    second = rule("Duplicate", "Another definition.", "**/*.py", active=False)
    stored = [
        {**first, "rule_id": dated_id, "created_at": "2026-11-22T00:00:00Z", "updated_at": "2026-11-22T00:00:00Z"},
        {**second, "rule_id": undated_id},
    ]
    config = Config("alembic.ini")
    legacy = {"rules": [{"path": "**", "rule": "Keep legacy behavior."}]}
    tracking = [{"id": dated_id, "version": "a" * 64}, {"id": undated_id, "version": "b" * 64}]
    try:
        command.downgrade(config, "20261122_review_rule_usage")
        with transaction() as db:
            repo = db.get(Repository, 22)
            repo.settings = {**repo.settings, "rules": legacy, "review_rules": stored, "background": "Keep\0me"}
            run = db.get(ReviewRun, original)
            run.config = {**run.config, "settings": {**run.config["settings"], "review_rules": [first, second]}}
            run.rule_tracking = tracking
            db.add(ReviewRuleUse(run_id=original, rule_id=dated_id, path="main.py", path_hash="c" * 64,
                                 version=tracking[0]["version"], used_at=STAMP))
        command.upgrade(config, "head")
        with transaction() as db:
            settings = db.get(Repository, 22).settings
            assert "review_rules" not in settings and settings["rules"] == legacy and settings["background"] == "Keep\0me"
            records = rules.by_repository(db, [22])[22]
            assert [row.model_dump(mode="json") for row in records] == stored
            run = db.get(ReviewRun, original)
            assert run.config["settings"]["review_rules"] == [first, second]
            assert run.rule_tracking == tracking
            changed = {**second, "instructions": "Edited after upgrade."}
            db.get(CustomReviewRule, undated_id).definition = changed
        command.downgrade(config, "20261122_review_rule_usage")
        with transaction() as db:
            settings = db.get(Repository, 22).settings
            assert settings["review_rules"] == [stored[0], {**changed, "rule_id": undated_id}]
            assert settings["rules"] == legacy and settings["background"] == "Keep\0me"
            use = db.get(ReviewRuleUse, (original, dated_id, "c" * 64))
            assert use.version == tracking[0]["version"] and use.used_at == STAMP
    finally:
        command.upgrade(config, "head")


def test_scoped_rule_downgrade_refuses_to_drop_workspace_rules(seed):
    with transaction() as db:
        db.add(CustomReviewRule(scope="workspace", position=0, definition=rule("Shared", "Keep my instructions.")))
    config = Config("alembic.ini")
    try:
        with pytest.raises(ValueError, match="Workspace-wide review rules"):
            command.downgrade(config, "20261122_review_rule_usage")
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        assert db.scalar(select(CustomReviewRule)).definition["name"] == "Shared"
