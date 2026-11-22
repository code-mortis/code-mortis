import base64
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_checks import rerun_body
from test_pr_planning import commit, git, plan, write
from test_pr_planning import repository as repository  # pytest fixture
from test_review_flow import new_run, remote

from code_mortis.api.routers import repositories
from code_mortis.code_graph import store
from code_mortis.core.db import transaction
from code_mortis.core.process import child_environment
from code_mortis.core.security import seal
from code_mortis.integrations import github
from code_mortis.models import (
    CodeGraphFile,
    CodeGraphRepository,
    CodeGraphSnapshot,
    Connection,
    Job,
    Repository,
    ReviewRun,
    WebhookDelivery,
)
from code_mortis.reviews import checks, engine, planning
from code_mortis.schemas.code_graph import EXTRACTOR_VERSION
from code_mortis.schemas.common import RepoSettings, ReviewRule

RULES = "/api/v1/repositories/22/review-rules"


def rule(name, instructions, glob="", active=True):
    return {"name": name, "instructions": instructions, "glob": glob, "active": active}


def update_rules(client, expected_rules, operation, **fields):
    return client.put(RULES, json={"expected_rules": expected_rules, "change": {"operation": operation, **fields}})


def test_preview_matches_engine_assignment_at_default_branch(seed, client, monkeypatch, repository):
    repo, _ = repository
    guide = "Follow the trusted guide.\n"
    wire = json.dumps({"rules": [
        {"path": "**/*.py", "rule": ".opencodereview/guide.md", "merge_system_rule": True},
        {"path": "main.py", "rule": "Second old rule"},
    ]})
    write(repo, planning.TRUSTED_RULE_PATH, wire)
    write(repo, ".opencodereview/guide.md", guide)
    base = commit(repo)
    tree = git(repo, "rev-parse", f"{base}^{{tree}}")
    rules_tree = git(repo, "rev-parse", f"{base}:.opencodereview")
    rule_blobs = {name: git(repo, "rev-parse", f"{base}:.opencodereview/{name}") for name in ("rule.json", "guide.md")}
    replies = {
        "ref/heads/release%2Fstable": {"ref": "refs/heads/release/stable", "object": {"type": "commit", "sha": base}},
        f"commits/{base}": {"sha": base, "tree": {"sha": tree}},
        f"trees/{tree}": {"sha": tree, "truncated": False, "tree": [
            {"path": ".opencodereview", "type": "tree", "mode": "040000", "sha": rules_tree},
        ]},
        f"trees/{rules_tree}": {"sha": rules_tree, "truncated": False, "tree": []},
    }
    for name, content in (("rule.json", wire), ("guide.md", guide)):
        sha = rule_blobs[name]
        replies[f"trees/{rules_tree}"]["tree"].append(
            {"path": name, "type": "blob", "mode": "100644", "size": len(content), "sha": sha})
        replies[f"blobs/{sha}"] = {"sha": sha, "size": len(content), "encoding": "base64",
                                   "content": base64.b64encode(content.encode()).decode()}
    reads = []

    def request(method, url, token, *, max_response_bytes):
        assert method == "GET" and token == "scoped-token" and max_response_bytes > 0
        path = url.removeprefix("/repos/team/project/git/")
        reads.append(path)
        return replies[path]

    def scoped_token(_db, installation, repository_id):
        assert (installation, repository_id) == (10, 22)
        return "scoped-token"

    monkeypatch.setattr(github, "authorize_repo", lambda db, user, repo, write: {
        "id": 22 if write else None, "default_branch": "release/stable",
    })
    monkeypatch.setattr(github, "repository_read_token", scoped_token)
    monkeypatch.setattr(github, "request", request)
    credentials = {"api_key": "provider-secret", "extra_headers": {"X-Hint": "repository", "X-Tag": "active"}}
    with transaction() as db:
        db.get(Connection, "connection").secret = seal(json.dumps(credentials))
    draft = [rule("Every file", "Keep provider-secret out of output."),
             rule("Disabled", "Ignored instruction", "**/*.py", False),
             rule("Python", "review-guide.md", "**/*.py"),
             rule("Only JS", "Ignored JS instruction", "**/*.js")]
    dated = [{**item, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"} for item in draft]
    response = client.post(f"{RULES}/preview", json={"path": "main.py", "rules": dated})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["branch"] == "release/stable" and result["base_sha"] == base and result["reviewable"]
    assert not result["rule_size_exceeds_limit"]
    assert reads == list(replies)
    assert [(match["source"], match["name"]) for match in result["matches"]] == [
        ("mortis", "Mortis built-in rule"), ("trusted_base", "Legacy review rule"),
        ("repository", "Every file"), ("repository", "Python"),
    ]
    assert result["instructions"].count(planning.BUILTIN_RULE) == 1
    assert guide.rstrip() in result["instructions"] and result["instructions"].endswith("\n\nreview-guide.md")
    assert "Ignored" not in result["instructions"] and "provider-secret" not in result["instructions"]
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(ReviewRun)) == 0
        assert "review_rules" not in db.get(Repository, 22).settings
    write(repo, planning.TRUSTED_RULE_PATH, '{"rules":[{"path":"**","rule":"Proposed head rules"}]}')
    write(repo, "main.py", "value = 2\n")
    head = commit(repo)
    env = child_environment()
    options = RepoSettings.for_execution({"review_rules": dated})
    assert options["review_rules"] == draft
    selected = planning.plan_review(repo, env, base, head, base, options, lambda: False)
    undated_options = RepoSettings.for_execution({"review_rules": draft})
    undated = planning.plan_review(repo, env, base, head, base, undated_options, lambda: False)
    assert selected.rules_digest == undated.rules_digest
    batches = engine.prepare_batches(selected, {"kind": "api", "model": "fixture", "protocol": "openai"},
                                    options, credentials)
    assigned = [unit["rules"] for batch in batches for unit in json.loads(batch.request.messages[0].content)["assigned_units"]]
    assert assigned == [result["instructions"]]
    with transaction() as db:
        db.get(Connection, "connection").options = {"prompt_token_ceiling": 10_240}
    large = client.post(f"{RULES}/preview", json={"path": "main.py", "rules": [rule("Long", "a" * 1000)]})
    assert large.status_code == 200, large.text
    assert large.json()["rule_size_exceeds_limit"]


def test_review_rules_preserve_legacy_only_semantics(repository):
    repo, _ = repository
    write(repo, planning.TRUSTED_RULE_PATH, '{"rules":[{"path":"**","rule":"Trusted commit","merge_system_rule":true}]}')
    base = commit(repo)
    options = {"rules": {"rules": [{"path": "main.py", "rule": "First legacy rule"},
                                   {"path": "**/*.py", "rule": "Second legacy rule"}]}}
    legacy = planning.load_rules(repo, child_environment(), base, options, lambda: False)
    new = [rule("Inactive", "Ignore me", active=False), rule("Only JS", "Ignore me", "*.js")]
    assert planning.rule_for("main.py", legacy, new) == "First legacy rule"
    assert planning.rule_for("README.md", legacy, new) == f"{planning.system_rule('README.md')}\n\nTrusted commit"
    text, matches = planning.resolve_rules("main.py", legacy, [*new, rule("Ownership", "New instructions")])
    assert text == f"{planning.system_rule('main.py')}\n\nFirst legacy rule\n\nNew instructions"
    assert [match["source"] for match in matches] == ["mortis", "legacy_settings", "repository"]


def test_multiple_files_and_globs_are_alternatives_without_duplicate_instructions(repository):
    repo, base = repository
    legacy = planning.load_rules(repo, child_environment(), base, {}, lambda: False)
    mixed = {**rule("Authentication", "Check expired tokens."),
             "files": ["src/auth/session.py", "src/auth/token.py", "test[1].py"],
             "globs": ["tests/auth/**/*.py", "src/auth/{session,refresh}.py"]}
    stored = ReviewRule.model_validate(mixed).model_dump()
    assert stored == mixed
    for file in ("src/auth/session.py", "src/auth/token.py", "test[1].py", "tests/auth/nested/test_token.py", "src/auth/refresh.py"):
        instructions, matches = planning.resolve_rules(file, legacy, [stored])
        assert instructions.count("Check expired tokens.") == 1
        assert matches[-1]["files"] == mixed["files"] and matches[-1]["globs"] == mixed["globs"]
    for nonmatching in ("src/auth/Token.py", "src/auth/other.py", "test1.py"):
        assert planning.rule_for(nonmatching, legacy, [stored]) == planning.system_rule(nonmatching)
    old = rule("Legacy", "Preserve old matching.", "{src/auth/session.py,tests/auth/**/*.py}")
    assert RepoSettings.for_execution({"review_rules": [old]})["review_rules"] == [old]
    assert "Preserve old matching." in planning.rule_for("SRC/AUTH/SESSION.PY", legacy, [old])


def test_exact_file_target_survives_git_pathspec_syntax(repository):
    repo, _ = repository
    target = ":(literal)guide.md"
    write(repo, target, "Original guide\n")
    base = commit(repo)
    write(repo, target, "Changed guide\n")
    configured = {**rule("Literal", "Review the marked guide."), "files": [target]}
    selected = plan((repo, base), review_rules=[configured])
    assert selected.selected[0].path == target and not selected.selected[0].error
    assert "Review the marked guide." in selected.units[0].rule


def test_review_rule_bounds_glob_alternatives_across_patterns():
    alternatives = "{a,b}" * 8
    ReviewRule.model_validate(rule("Legacy", "Review rules.", alternatives))
    with pytest.raises(ValueError, match="too many alternatives"):
        ReviewRule.model_validate({**rule("Mixed", "Review rules.", alternatives), "globs": ["src/**"]})


def test_legacy_reference_is_a_literal_repository_path(repository):
    repo, _ = repository
    reference = ":(literal)review-guide.md"
    write(repo, reference, "Trust only this path.\n")
    write(repo, "review-guide.md", "Interpretation would read the wrong file.\n")
    write(repo, planning.TRUSTED_RULE_PATH, json.dumps({"rules": [{"path": "**", "rule": reference}]}))
    base = commit(repo)
    legacy = planning.load_rules(repo, child_environment(), base, {}, lambda: False)
    assert planning.rule_for("main.py", legacy) == "Trust only this path."


def test_rule_matching_honors_preview_deadline_while_traversing_one_glob():
    checks = 0

    def deadline():
        nonlocal checks
        checks += 1
        if checks == 6:
            raise TimeoutError("Preview expired")

    with pytest.raises(TimeoutError, match="Preview expired"):
        planning.rule_matches("/".join(["repeated"] * 200 + ["main.py"]), "**/**/{foo,bar}.py", check=deadline)
    assert checks == 6


def test_saving_only_rules_checks_concurrency_and_keeps_existing_settings(seed, client, github_stub, monkeypatch):
    legacy = {"rules": [{"path": "**", "rule": "Keep legacy checks"}], "include": ["**/*.py"]}
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "rules": legacy, "background": "Keep context"}
    draft = rule("Auth", "Preserve permission checks.")
    saved = update_rules(client, [], "add", rule=draft)
    assert saved.status_code == 200, saved.text
    records = saved.json()
    assert UUID(records[0]["rule_id"]).version == 4
    assert records == [{**draft, "rule_id": records[0]["rule_id"],
                        "created_at": records[0]["created_at"], "updated_at": records[0]["created_at"]}]
    assert update_rules(client, [], "delete", index=0).status_code == 409
    with transaction() as db:
        repo = db.get(Repository, 22)
        assert repo.enabled and repo.settings["background"] == "Keep context"
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: [db.get(Repository, 22)])
    listed = client.get("/api/v1/repositories")
    assert listed.status_code == 200, listed.text
    settings = listed.json()[0]["settings"]
    assert "review_rules" not in settings
    changed = client.put("/api/v1/repositories/22", json={"enabled": False, "settings": {**settings, "language": "Spanish"}})
    assert changed.status_code == 200, changed.text
    assert changed.json()["settings"]["rules"] == legacy
    assert changed.json()["review_rules"] == records
    omitted = client.put("/api/v1/repositories/22", json={"enabled": False, "settings": {"language": "English"}})
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["settings"]["rules"] == legacy
    assert omitted.json()["review_rules"] == records


def test_rule_timestamps_change_only_when_targeted_rule_changes(seed, client, github_stub, monkeypatch):
    old = rule("Auth", "Check permissions.")
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "review_rules": [{"name": old["name"], "instructions": old["instructions"]}]}
    timestamps = iter(datetime(2026, 1, day, tzinfo=UTC) for day in range(1, 5))
    monkeypatch.setattr(repositories, "now", lambda: next(timestamps))
    monkeypatch.setattr(github, "visible_repositories", lambda db, user: [db.get(Repository, 22)])
    assert client.get("/api/v1/repositories").json()[0]["review_rules"] == [old]

    assert update_rules(client, [old], "add", rule={**old, "created_at": "2020-01-01T00:00:00Z"}).status_code == 422
    added = update_rules(client, [old], "add", rule=old)
    assert added.status_code == 200, added.text
    first = "2026-01-01T00:00:00Z"
    identity = added.json()[1]["rule_id"]
    assert UUID(identity).version == 4
    dated = {**old, "rule_id": identity, "created_at": first, "updated_at": first}
    assert added.json() == [old, dated]
    unchanged = update_rules(client, [old, dated], "edit", index=0, rule=old)
    assert unchanged.status_code == 200 and unchanged.json() == [old, dated], unchanged.text
    inactive = update_rules(client, [old, dated], "edit", index=1, rule={**old, "active": False})
    assert inactive.status_code == 200, inactive.text
    assert inactive.json() == [old, {**dated, "active": False, "updated_at": "2026-01-02T00:00:00Z"}]
    active = update_rules(client, inactive.json(), "edit", index=1, rule=old)
    assert active.status_code == 200, active.text
    assert active.json() == [old, {**dated, "updated_at": "2026-01-03T00:00:00Z"}]
    # The contents match again, but the dates still fence a stale positional write.
    assert update_rules(client, [old, dated], "delete", index=0).status_code == 409
    update = {**old, "instructions": "Check admin permissions."}
    edited = update_rules(client, active.json(), "edit", index=0, rule=update)
    assert edited.status_code == 200, edited.text
    updated_old = {**update, "rule_id": edited.json()[0]["rule_id"], "updated_at": "2026-01-04T00:00:00Z"}
    assert updated_old["rule_id"] != identity
    assert edited.json() == [updated_old, active.json()[1]]
    deleted = update_rules(client, edited.json(), "delete", index=1)
    assert deleted.status_code == 200 and deleted.json() == [updated_old], deleted.text
    assert client.get("/api/v1/repositories").json()[0]["review_rules"] == [updated_old]


@pytest.mark.parametrize("case", ["member", "no_write", "wrong_repo"])
def test_review_rules_require_editor_permission(seed, client, github_stub, monkeypatch, case):
    from code_mortis.models import Membership

    if case == "member":
        with transaction() as db:
            db.get(Membership, 7).role = "member"
    elif case == "no_write":
        def denied(*args, **kwargs):
            raise github.GitHubError(403, "Write permission required")
        monkeypatch.setattr(github, "authorize_repo", denied)
    else:
        monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": 23})
    monkeypatch.setattr(github, "repository_read_token", lambda *a, **k: pytest.fail("Unauthorized read"))
    assert update_rules(client, [], "add", rule=rule("Denied", "Review permissions.")).status_code == 403
    assert client.post(f"{RULES}/preview", json={"path": "main.py", "rules": []}).status_code == 403
    assert client.get(f"{RULES}/files", params={"q": "main"}).status_code == 403


def test_file_suggestions_read_only_current_policy_for_last_indexed_default(seed, client, github_stub, monkeypatch):
    with transaction() as db:
        repo = db.get(Repository, 22)
        policy = store.policy(repo)
        old, current = "a" * 40, "b" * 40
        db.add(CodeGraphRepository(repository_id=22, enabled=True, default_sha=old, observed_sha=current))
        snapshot = CodeGraphSnapshot(repository_id=22, id="default", commit_sha=old, extractor_version=EXTRACTOR_VERSION,
                                     policy_digest=store.policy_digest(policy), policy=policy, status="ready")
        other = CodeGraphSnapshot(repository_id=22, id="other", commit_sha=current, extractor_version=EXTRACTOR_VERSION,
                                  policy_digest=store.policy_digest(policy), policy=policy, status="ready")
        db.add_all([snapshot, other])
        db.flush()
        for base, path, status in [
            ("default", "src/auth_session.py", "unsupported"), ("default", "src/authZsession.py", "unsupported"),
            ("default", "src/auth_skip.py", "excluded"), ("default", "src/.env.auth_token", "unsupported"),
            ("other", "src/auth_newer.py", "unsupported"),
        ]:
            db.add(CodeGraphFile(snapshot_id=base, path=path, blob_sha="c" * 40, size_bytes=1, status=status))
        db.add_all(CodeGraphFile(snapshot_id="default", path=f"test{i:02}.py", blob_sha="c" * 40, size_bytes=1,
                                 status="unsupported") for i in range(25))
    result = client.get(f"{RULES}/files", params={"q": "AUTH_"})
    assert result.status_code == 200, result.text
    assert result.json() == ["src/auth_session.py"]
    initial = client.get(f"{RULES}/files", params={"q": ""})
    assert initial.status_code == 200, initial.text
    assert 0 < len(initial.json()) <= 20
    assert {"src/auth_session.py", "src/authZsession.py"} <= set(initial.json())
    assert not {"src/auth_skip.py", "src/.env.auth_token", "src/auth_newer.py"} & set(initial.json())
    assert len(client.get(f"{RULES}/files", params={"q": "test"}).json()) == 20
    assert client.get(f"{RULES}/files", params={"q": "\0auth"}).status_code == 422
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0

    def authorize_with_concurrent_exclusion(db, user, full_name, write):
        with transaction() as other:
            repo = other.get(Repository, 22)
            repo.settings = {**repo.settings, "exclusions": "src/**"}
        return {"id": 22}

    monkeypatch.setattr(github, "authorize_repo", authorize_with_concurrent_exclusion)
    assert client.get(f"{RULES}/files", params={"q": "auth"}).json() == []


def test_retries_use_original_rules_and_new_review_reads_current_rules(seed, client, github_stub, monkeypatch):
    old = [rule("Old", "Original instructions.")]
    new = [{**rule("New", "Updated instructions."), "files": ["src/token.py"], "globs": ["tests/**/*.py"]}]
    legacy = {"rules": [{"path": "**", "rule": "Original legacy instructions."}]}
    updated_legacy = {"rules": [{"path": "**", "rule": "Updated legacy instructions."}]}
    added = update_rules(client, [], "add", rule=old[0])
    assert added.status_code == 200, added.text
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "rules": legacy}
    run_id = new_run(parallel=True)
    edited = update_rules(client, added.json(), "edit", index=0, rule=new[0])
    assert edited.status_code == 200, edited.text
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "rules": updated_legacy}
        run = db.get(ReviewRun, run_id)
        run.check_id, run.status, run.publication = 100, "blocked", "not_applicable"
    resumed = client.post(f"/api/v1/runs/{run_id}/retry")
    assert resumed.status_code == 202, resumed.text
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.config["settings"]["review_rules"] == old
        assert run.config["settings"]["rules"] == legacy
        original_tracking = run.rule_tracking
        assert original_tracking[0]["id"] == added.json()[0]["rule_id"]
        run.status = "failed"
        db.add(WebhookDelivery(id="rerun-rules", event="check_run"))
    with transaction() as db:
        checks.rerequest(db, rerun_body(run_id), "rerun-rules")
        rerun = db.scalar(select(ReviewRun).where(ReviewRun.id != run_id))
        assert rerun.config["settings"]["review_rules"] == old
        assert rerun.config["settings"]["rules"] == legacy
        assert rerun.rule_tracking == original_tracking
    fresh = client.post("/api/v1/pulls/pr/runs")
    assert fresh.status_code == 202, fresh.text
    assert fresh.json()["config"]["settings"]["review_rules"] == new
    assert fresh.json()["config"]["settings"]["rules"] == updated_legacy
    with transaction() as db:
        tracked = db.get(ReviewRun, fresh.json()["id"]).rule_tracking[0]
        assert tracked["id"] == original_tracking[0]["id"]
        assert tracked["version"] != original_tracking[0]["version"]
