import base64
import json

import pytest
from sqlalchemy import func, select
from test_checks import rerun_body
from test_pr_planning import commit, git, plan, write
from test_pr_planning import repository as repository  # pytest fixture
from test_review_flow import new_run, remote

from code_mortis.core.db import transaction
from code_mortis.core.process import child_environment
from code_mortis.core.security import seal
from code_mortis.integrations import github
from code_mortis.models import Connection, Job, Repository, ReviewRun, WebhookDelivery
from code_mortis.reviews import checks, engine, planning
from code_mortis.schemas.common import RepoSettings

RULES = "/api/v1/repositories/22/review-rules"


def rule(name, instructions, glob="", active=True):
    return {"name": name, "instructions": instructions, "glob": glob, "active": active}


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
    response = client.post(f"{RULES}/preview", json={"path": "main.py", "rules": draft})
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
    options = RepoSettings.for_execution({"review_rules": draft})
    selected = plan((repo, base), **options)
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
    draft = [rule("Auth", "Preserve permission checks.")]
    saved = client.put(RULES, json={"rules": draft, "expected_rules": []})
    assert saved.status_code == 200 and saved.json() == draft
    assert client.put(RULES, json={"rules": [], "expected_rules": []}).status_code == 409
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
    assert changed.json()["review_rules"] == draft
    omitted = client.put("/api/v1/repositories/22", json={"enabled": False, "settings": {"language": "English"}})
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["settings"]["rules"] == legacy
    assert omitted.json()["review_rules"] == draft


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
    assert client.put(RULES, json={"rules": [], "expected_rules": []}).status_code == 403
    assert client.post(f"{RULES}/preview", json={"path": "main.py", "rules": []}).status_code == 403


def test_retries_use_original_rules_and_new_review_reads_current_rules(seed, client, github_stub, monkeypatch):
    old = [rule("Old", "Original instructions.")]
    new = [rule("New", "Updated instructions.")]
    legacy = {"rules": [{"path": "**", "rule": "Original legacy instructions."}]}
    updated_legacy = {"rules": [{"path": "**", "rule": "Updated legacy instructions."}]}
    assert client.put(RULES, json={"rules": old, "expected_rules": []}).status_code == 200
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.settings = {**repo.settings, "rules": legacy}
    run_id = new_run(parallel=True)
    assert client.put(RULES, json={"rules": new, "expected_rules": old}).status_code == 200
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
        run.status = "failed"
        db.add(WebhookDelivery(id="rerun-rules", event="check_run"))
    with transaction() as db:
        checks.rerequest(db, rerun_body(run_id), "rerun-rules")
        rerun = db.scalar(select(ReviewRun).where(ReviewRun.id != run_id))
        assert rerun.config["settings"]["review_rules"] == old
        assert rerun.config["settings"]["rules"] == legacy
    fresh = client.post("/api/v1/pulls/pr/runs")
    assert fresh.status_code == 202, fresh.text
    assert fresh.json()["config"]["settings"]["review_rules"] == new
    assert fresh.json()["config"]["settings"]["rules"] == updated_legacy
