"""Candidate gating, canonical identity, and publication recovery across review engines."""

import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from test_review_flow import COMMENT, new_run, remote

from code_mortis import assessment, checks, github, ocr, reviews, runner_client, verification
from code_mortis.db import transaction
from code_mortis.models import (
    Connection,
    Finding,
    Occurrence,
    PullRequest,
    Repository,
    ReviewCandidate,
    ReviewRun,
)
from code_mortis.schemas import RepoSettings

SOURCE = "\n" * 6 + "while True:\n    retry()\n    logger.error(api_key)"


def verdict(context, **changes):
    previous = context["history"]
    return {
        "verdict": "confirmed", "relationship": "duplicate" if previous else "new",
        "finding_id": previous[0]["id"] if previous else None,
        "explanation": "Retry never terminates when the destination fails.",
        "cause": "An unbounded loop", "trigger": "A failing destination", "consequence": "The worker cannot finish",
        "history_explanation": "Same retry defect despite different wording." if previous else "",
        "contradicts_prior": False, "changed_conditions": "", "suggestion_supported": False,
        "evidence": [{"revision": "current", "path": context["finding"]["path"],
                      "start_line": 7, "end_line": 7, "quote": "while True:"}],
        **changes,
    }


@pytest.fixture
def pipeline(seed, github_stub, monkeypatch):
    state = {"comments": [copy.deepcopy(COMMENT)], "calls": 0, "reviews": [], "head": "a" * 40}

    def request(method, path, token, **kwargs):
        if method == "GET":
            return remote(state["head"])
        item = {"id": len(state["reviews"]) + 1, "body": kwargs["json"]["body"],
                "user": {"login": "mortis-test[bot]"}, "comments": kwargs["json"]["comments"]}
        state["reviews"].append(item)
        if state.get("lost_response"):
            state["lost_response"] = False
            raise github.GitHubError(503, "Response lost")
        return item

    def context(repo, env, fetch_env, run, snapshot, previous, stopped):
        return {"finding": snapshot, "head_sha": run.head_sha, "original_sha": run.head_sha,
                "sources": {"current": {snapshot["path"]: SOURCE}}, "diff": "-retry()\n+while True:",
                "history": previous["findings"], "prior_candidates": previous["prior_candidates"]}

    def assess(*args, **kwargs):
        state["calls"] += 1
        context = args[4]
        if state.get("during_assessment"):
            state["during_assessment"]()
        if state.get("failure"):
            raise state["failure"]
        return verdict(context, **state.get("decision", {})), {"tokens": 25}

    def engine(*args, **kwargs):
        comments = [{**item, "source_sha": state["head"]} for item in state["comments"]]
        result = kwargs.get("saved_review") or ("completed", {"summary": {"total_tokens": state.get("tokens", 100)}}, comments)
        kwargs["after_review"](result, Path("/tmp/repo"), {}, {})
        return result

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "pages", lambda *a: copy.deepcopy(state["reviews"]))
    monkeypatch.setattr(ocr, "review", engine)
    monkeypatch.setattr(assessment, "build_context", context)
    monkeypatch.setattr(verification, "api_verify", assess)
    return state


def run_and_publish():
    identity = new_run(assessed=True)
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    return identity


def test_rephrasing_and_multiple_candidates_reuse_one_finding(pipeline, client):
    pipeline["comments"] += [{**COMMENT, "content": "Unbounded retries can hang the worker.", "category": "performance"}]
    first = run_and_publish()
    pipeline["head"] = "c" * 40
    with transaction() as db:
        db.get(PullRequest, "pr").head_sha = pipeline["head"]
    pipeline["comments"] = [{**COMMENT, "content": "Retry loop has no exit condition.", "start_line": 17, "end_line": 17}]
    second = run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
        assert db.scalar(select(func.count()).select_from(Occurrence)) == 2
        assert db.get(ReviewRun, second).publication == "no_new_comments"
        assert assessment.summary(db, db.get(ReviewRun, first)).new_findings == 1
        assert assessment.summary(db, db.get(ReviewRun, first)).repeated_observations == 1
    assert len(pipeline["reviews"]) == 1
    detail = client.get(f"/api/v1/runs/{second}").json()
    assert detail["assessment"]["repeated_observations"] == 1
    assert detail["candidates"][0]["finding_id"]


@pytest.mark.parametrize("status", ["resolved", "dismissed"])
def test_closed_finding_is_never_reopened_or_reposted(pipeline, status):
    run_and_publish()
    with transaction() as db:
        db.scalar(select(Finding)).status = status
    pipeline["comments"][0]["content"] = "Same retry loop problem, reworded."
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(Finding)).status == status
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
    assert len(pipeline["reviews"]) == 1


def test_distinct_defects_on_same_code_can_both_publish(pipeline):
    run_and_publish()
    pipeline["comments"][0]["content"] = "The loop also exposes secrets in its error handler."
    pipeline["decision"] = {"relationship": "new", "finding_id": None,
                            "cause": "Logging credentials", "consequence": "Credential disclosure",
                            "evidence": [{"revision": "current", "path": COMMENT["path"],
                                          "start_line": 9, "end_line": 9, "quote": "    logger.error(api_key)"}],
                            "history_explanation": "Credential disclosure is distinct from nontermination."}
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 2
    assert len(pipeline["reviews"]) == 2


@pytest.mark.parametrize("decision", [
    {"verdict": "uncertain"}, {"verdict": "rejected"}, {"evidence": []},
    {"trigger": ""}, {"contradicts_prior": True, "changed_conditions": ""},
    {"relationship": "uncertain"},
    {"evidence": [{"revision": "current", "path": "src/retry.py", "start_line": 7, "end_line": 7, "quote": "invented"}]},
    {"verdict": "rejected", "evidence": [{"revision": "current", "path": "evidence.py", "start_line": 1,
                                        "end_line": 1, "quote": "# Code Mortis verification evidence"}]},
])
def test_unsupported_candidates_never_become_findings(pipeline, decision):
    pipeline["decision"] = decision
    identity = run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
        run = db.get(ReviewRun, identity)
        assert assessment.summary(db, run).withheld_candidates == 1
        assert run.publication == "no_new_comments"
        assert checks.check_payload(run, 0)["conclusion"] == ("success" if decision == {"verdict": "rejected"} else "neutral")
    assert pipeline["reviews"] == []


def test_bad_suggestion_is_preserved_as_input_but_not_published(pipeline):
    pipeline["comments"][0]["suggestion_code"] = "unrelated replacement"
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(ReviewCandidate)).snapshot["suggestion_code"] == "unrelated replacement"
        assert db.scalar(select(Occurrence)).snapshot["suggestion_code"] == ""
    assert "unrelated replacement" not in pipeline["reviews"][0]["comments"][0]["body"]


def test_summary_findings_and_unknown_writes_publish_only_once(pipeline):
    pipeline["comments"][0]["inline_line"] = None
    pipeline["lost_response"] = True
    identity = new_run(assessed=True)
    reviews.perform_review(identity)
    with pytest.raises(github.GitHubError):
        reviews.publish_run(identity)
    with transaction() as db:
        saved = copy.deepcopy(db.get(ReviewRun, identity).publication_plan)
    reviews.publish_run(identity)
    pipeline["comments"][0]["content"] = "The same hang occurs on retry."
    run_and_publish()
    with transaction() as db:
        finding = db.scalar(select(Finding))
        assert finding.published_review_id == 1
        assert db.get(ReviewRun, identity).publication_plan == saved
        assert assessment.summary(db, db.get(ReviewRun, identity)).published_comments == 1
        assert reviews.marker("finding", finding.id) in pipeline["reviews"][0]["body"]
    assert len(pipeline["reviews"]) == 1


@pytest.mark.parametrize("reason", ["budget", "timeout", "provider"])
def test_incomplete_assessment_withholds_and_preserves_usage(pipeline, reason):
    if reason == "budget":
        pipeline["tokens"] = 200000
    else:
        pipeline["failure"] = TimeoutError("Timeout") if reason == "timeout" else ValueError("Unknown consumption")
    identity = run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        assert row.status == "failed" and row.finding_id is None
        assert row.usage["tokens"] == (0 if reason == "budget" else 199900)
        assert assessment.summary(db, db.get(ReviewRun, identity)).incomplete == 1
    assert not pipeline["reviews"]


def test_preflight_skip_retains_its_status_and_zero_usage(pipeline):
    pipeline["failure"] = verification.VerificationOutputError("Evidence exceeds scope", {"tokens": 0}, "skipped")
    run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        assert row.status == "skipped" and row.usage == {"tokens": 0} and row.finding_id is None
    assert not pipeline["reviews"]


@pytest.mark.parametrize("publication", ["failed", "publishing", "partially_published"])
def test_unacknowledged_owner_keeps_frozen_plan_until_retry(pipeline, publication):
    first = new_run(assessed=True)
    reviews.perform_review(first)
    with transaction() as db:
        run = db.get(ReviewRun, first)
        run.publication_plan = reviews.publication_plan(db, run)
        saved_plan = copy.deepcopy(run.publication_plan)
        run.publication = publication
    second = run_and_publish()
    assert not pipeline["reviews"]
    reviews.publish_run(first)
    with transaction() as db:
        assert db.get(ReviewRun, second).publication == "no_new_comments"
        assert db.get(ReviewRun, first).publication_plan == saved_plan
        assert db.scalar(select(Finding)).published_review_id == 1
    assert len(pipeline["reviews"]) == 1


def test_concurrent_history_change_invalidates_new_identity(pipeline):
    def change():
        with transaction() as db:
            run = db.scalar(select(ReviewRun))
            finding = Finding(id="concurrent", pr_id="pr", fingerprint="other", path=COMMENT["path"],
                              content=COMMENT["content"], severity="high", category="bug", start_line=7,
                              end_line=7, last_seen_run_id=run.id)
            db.add(finding)
            db.flush()
            db.add(Occurrence(run_id=run.id, finding_id=finding.id, snapshot=COMMENT))
    pipeline["during_assessment"] = change
    run_and_publish()
    with transaction() as db:
        row = db.scalar(select(ReviewCandidate))
        assert row.status == "skipped" and "context changed" in row.error
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
    assert not pipeline["reviews"]


@pytest.mark.parametrize("kind", ["chatgpt", "claude_code"])
def test_native_clients_use_the_same_gate(pipeline, monkeypatch, kind):
    with transaction() as db:
        connection = db.get(Connection, "connection")
        connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"

    def native(connection, identity, payload, stopped):
        import json

        assert payload["task"] == "assessment"
        return verdict(json.loads(payload["prompt"])["context"])
    monkeypatch.setattr(runner_client, "review", native)
    run_and_publish()
    assert len(pipeline["reviews"]) == 1
    assert pipeline["calls"] == 0


def test_interrupted_assessment_never_gets_a_fresh_allowance(pipeline):
    pipeline["failure"] = SystemExit("Worker terminated")
    identity = new_run(assessed=True)
    with pytest.raises(SystemExit):
        reviews.perform_review(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "running" and candidate.usage == {"tokens": 199900}
    del pipeline["failure"]
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "failed" and candidate.usage == {"tokens": 199900}
    assert pipeline["calls"] == 1 and not pipeline["reviews"]


@pytest.mark.parametrize("change", ["head", "permission", "disabled", "finding_state", "connection"])
def test_stale_or_unauthorized_decisions_are_withheld(pipeline, monkeypatch, change):
    if change == "finding_state":
        run_and_publish()
        pipeline["comments"][0]["content"] = "Reworded retry defect."

    def changed():
        with transaction() as db:
            if change == "head":
                db.get(PullRequest, "pr").head_sha = "c" * 40
            elif change == "disabled":
                db.get(Repository, 22).enabled = False
            elif change == "finding_state":
                db.scalar(select(Finding)).status = "dismissed"
            elif change == "connection":
                db.get(Connection, "connection").base_url = "https://changed.example/v1"
        if change == "permission":
            def denied(*args, **kwargs):
                raise github.GitHubError(403, "Access revoked")
            monkeypatch.setattr(github, "authorize_repo", denied)

    pipeline["during_assessment"] = changed
    identity = new_run(assessed=True)
    reviews.perform_review(identity)
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate).where(ReviewCandidate.run_id == identity))
        assert candidate.status == "skipped" and candidate.finding_id is None
        assert assessment.summary(db, db.get(ReviewRun, identity)).incomplete == 1


def test_concurrent_assessments_serialize_canonical_assignment(pipeline):
    first = new_run(assessed=True)
    with transaction() as db:
        db.get(Repository, 22).settings = {"language": "Spanish"}
    second = new_run(assessed=True)
    barrier = Barrier(2)
    pipeline["during_assessment"] = lambda: barrier.wait(timeout=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(reviews.perform_review, [first, second]))
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 1
        assert sorted(db.scalars(select(ReviewCandidate.status))) == ["completed", "skipped"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(reviews.publish_run, [first, second]))
    assert len(pipeline["reviews"]) == 1


def test_concurrent_publication_claims_allow_one_comment(pipeline):
    first = new_run(assessed=True)
    reviews.perform_review(first)
    second = new_run(assessed=True)
    reviews.perform_review(second)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(reviews.publish_run, [first, second, first]))
    assert len(pipeline["reviews"]) == 1
    with transaction() as db:
        finding = db.scalar(select(Finding))
        assert finding.published_review_id == 1
        assert db.get(ReviewRun, finding.publication_run_id).publication_plan


def test_context_tracks_renames_and_preserves_literal_case(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "--quiet")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    source = 'def Retry():\n    while True:\n        send("CaseSensitive")\n'
    (repo / "old.py").write_text(source)
    (repo / "helper.py").write_text('def send(value):\n    return value == "CaseSensitive"\n')
    git("add", ".")
    git("commit", "--quiet", "-m", "original")
    original_sha = git("rev-parse", "HEAD")
    git("mv", "old.py", "new.py")
    (repo / "unrelated.py").write_text("# unrelated change\n" * 20000)
    (repo / "caller.py").write_text("from new import Retry\nRetry()\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "rename")
    head = git("rev-parse", "HEAD")
    monkeypatch.setattr(verification, "filter_context", lambda repo, env, sources, options, stopped: set(sources))
    run = SimpleNamespace(base_sha=original_sha, head_sha=head, config={"settings": RepoSettings().model_dump()})
    previous = {"findings": [{"id": "original", "sha": original_sha, "fingerprint": "legacy",
                              "snapshot": {**COMMENT, "path": "old.py"}}], "prior_candidates": []}
    context = assessment.build_context(repo, {}, {}, run, {**COMMENT, "path": "new.py", "source_sha": head}, previous, lambda: False)
    assert context["history"][0]["id"] == "original"
    assert context["sources"] == {"current": {"new.py": source}}
    assert "CaseSensitive" in context["sources"]["current"]["new.py"]
    assert "unrelated.py" in context["changed_paths"]
    assert "unrelated change" not in verification.evidence_bundle(context)
    assert "caller.py" not in context["diff"]
    assessment.expand_context(repo, {}, run, context, ["helper.py", "caller.py"], lambda: False)
    assert context["sources"]["current"]["helper.py"] == (repo / "helper.py").read_text()
    assert context["sources"]["current"]["caller.py"] == (repo / "caller.py").read_text()
    assert "caller.py" in context["diff"]
    assert "unrelated change" not in verification.evidence_bundle(context)
    previous["findings"] = [
        {"id": "moved-block", "sha": head, "fingerprint": "legacy", "snapshot": {**COMMENT, "path": "another.py"}},
        {"id": "different-literal", "sha": head, "fingerprint": "legacy2",
         "snapshot": {**COMMENT, "path": "another.py", "existing_code": COMMENT["existing_code"].upper()}},
    ]
    context = assessment.build_context(repo, {}, {}, run, {**COMMENT, "path": "new.py", "source_sha": head}, previous, lambda: False)
    assert [item["id"] for item in context["history"]] == ["moved-block"]
    previous["prior_candidates"], previous["findings"] = previous["findings"], []
    monkeypatch.setattr(verification, "filter_context", lambda repo, env, sources, options, stopped: set(sources) - {"another.py"})
    with pytest.raises(ValueError, match="history is excluded"):
        assessment.build_context(repo, {}, {}, run, {**COMMENT, "path": "new.py", "source_sha": head}, previous, lambda: False)


@pytest.mark.parametrize("kind", ["api", "chatgpt", "claude_code"])
def test_assessment_requests_only_needed_context_and_accounts_for_every_pass(pipeline, monkeypatch, kind):
    if kind != "api":
        with transaction() as db:
            connection = db.get(Connection, "connection")
            connection.kind, connection.owner_id, connection.auth_status = kind, 7, "connected"
    calls, allowances, identities, sizes = [], [], [], []
    def expand(repo, env, run, context, paths, stopped):
        assert paths == ["helper.py"]
        context["sources"]["current"]["helper.py"] = "def retry(): return False\n"
    monkeypatch.setattr(assessment, "expand_context", expand)
    def respond(context):
        calls.append(copy.deepcopy(context))
        if len(calls) == 1:
            return verdict(context, verdict="uncertain", required_paths=["helper.py"])
        assert context["sources"]["current"]["helper.py"] == "def retry(): return False\n"
        return verdict(context, required_paths=[])
    def api(*args, **kwargs):
        allowances.append(args[5])
        return kwargs["validator"](respond(args[4]), args[4]), {"tokens": 25}
    def native(connection, identity, payload, stopped):
        identities.append(identity)
        context = json.loads(payload["prompt"])["context"]
        sizes.append(len(json.dumps(context).encode()) + len(assessment.INSTRUCTIONS.encode()) + 4096)
        assert "required_paths" in payload["output_schema"]["required"]
        return respond(context)
    monkeypatch.setattr(verification, "api_verify", api)
    monkeypatch.setattr(runner_client, "review", native)
    run_and_publish()
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "completed"
        assert "helper.py" in candidate.context["sources"]["current"]
        assert "required_paths" not in candidate.result
        assert candidate.usage == ({"tokens": 50} if kind == "api" else {"context_bytes": sum(sizes)})
    assert len(calls) == 2 and len(pipeline["reviews"]) == 1
    if kind == "api":
        assert allowances[1] == allowances[0] - 25
    else:
        assert len(set(identities)) == 2


@pytest.mark.parametrize("failure", ["limit", "unknown_usage", "excluded", "round_limit"])
def test_followup_failure_never_publishes_or_reuses_spent_allowance(pipeline, monkeypatch, failure):
    calls = []
    def expand(repo, env, run, context, paths, stopped):
        if failure == "excluded":
            raise ValueError("Necessary assessment evidence is excluded")
        context["sources"]["current"][paths[0]] = "dependency = 1\n"
    monkeypatch.setattr(assessment, "expand_context", expand)
    def api(*args, **kwargs):
        calls.append(args[5])
        if len(calls) > 1 and failure == "limit":
            raise verification.VerificationOutputError("OCR limit: 47,110 tokens", {"tokens": 0}, "skipped")
        if len(calls) > 1 and failure == "unknown_usage":
            raise ValueError("Provider failed; consumption is unknown")
        return verdict(args[4], verdict="uncertain", required_paths=[f"helper{len(calls)}.py"]), {"tokens": 25}
    monkeypatch.setattr(verification, "api_verify", api)
    run_and_publish()
    assert not pipeline["reviews"]
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.finding_id is None
        if failure == "round_limit":
            assert len(calls) == 3 and candidate.result["verdict"] == "uncertain"
            assert candidate.usage == {"tokens": 75}
        else:
            assert candidate.status in {"failed", "skipped"}
            assert candidate.usage == {"tokens": calls[0] if failure == "unknown_usage" else 25}


@pytest.mark.parametrize("path", ["", "x" * 2001, "../secret.py", "/etc/passwd", ".git/config"])
def test_evidence_requests_reject_unsafe_paths(path):
    context = {"finding": COMMENT, "history": [], "sources": {"current": {COMMENT["path"]: SOURCE}}}
    with pytest.raises(ValueError):
        assessment.validate_response(verdict(context, verdict="uncertain", required_paths=[path]), context)


def test_requested_context_keeps_scope_and_reports_byte_limit(tmp_path, monkeypatch):
    run = SimpleNamespace(base_sha="a" * 40, head_sha="b" * 40, config={"settings": RepoSettings().model_dump()})
    context = {"sources": {"current": {}}, "diff": ""}
    monkeypatch.setattr(verification, "filter_context", lambda *args: set())
    monkeypatch.setattr(verification, "read_source", lambda *args: pytest.fail("Excluded source read"))
    with pytest.raises(ValueError, match="excluded.*secret.py"):
        assessment.expand_context(tmp_path, {}, run, context, ["secret.py"], lambda: False)
    with pytest.raises(ValueError, match="limit is 4,096 bytes.*not truncated"):
        assessment.check_context_size({"sources": {"current": {"main.py": "x" * 5000}}}, {"delegated_context_bytes": 4096})


def test_exact_repeat_requires_previously_requested_evidence():
    context = {"finding": COMMENT, "head_sha": "a" * 40, "history": [],
               "sources": {"current": {COMMENT["path"]: SOURCE}}}
    previous = verdict(context, evidence=[{"revision": "current", "path": "helper.py", "start_line": 1,
                                         "end_line": 1, "quote": "while True: retry()"}])
    context["history"] = [{"id": "existing", "sha": context["head_sha"], "snapshot": COMMENT, "assessment": previous}]
    assert assessment.exact_repeat(context) is None
    context["sources"]["current"]["helper.py"] = "while True: retry()\n"
    assert assessment.exact_repeat(context)["relationship"] == "duplicate"


def test_revocation_between_evidence_passes_stops_before_another_call(pipeline, monkeypatch):
    pipeline["decision"] = {"verdict": "uncertain", "required_paths": ["helper.py"]}
    def expand(*args):
        with transaction() as db:
            db.get(Repository, 22).enabled = False
    monkeypatch.setattr(assessment, "expand_context", expand)
    run_and_publish()
    assert pipeline["calls"] == 1 and not pipeline["reviews"]
    with transaction() as db:
        candidate = db.scalar(select(ReviewCandidate))
        assert candidate.status == "skipped" and candidate.usage == {"tokens": 25}


@pytest.mark.parametrize("comment", json.loads((Path(__file__).parent / "fixtures/pr15_candidates.json").read_text()),
                         ids=lambda comment: str(comment["id"]))
def test_pr15_claim_patterns_need_current_evidence(pipeline, comment):
    content = comment["body"].split("\n\n")[1]
    pipeline["comments"][0].update(content=content, path=comment["path"],
                                   start_line=comment["original_line"], end_line=comment["original_line"])
    pipeline["decision"] = {"verdict": "uncertain", "evidence": [],
                            "explanation": "The supplied source does not establish this claim."}
    run_and_publish()
    with transaction() as db:
        assert db.scalar(select(ReviewCandidate)).snapshot["content"] == content
        assert db.scalar(select(func.count()).select_from(Finding)) == 0
    assert not pipeline["reviews"]


def test_supported_distinct_regression_can_reverse_prior_advice(pipeline):
    run_and_publish()
    pipeline["comments"][0]["content"] = "A new error handler exposes credentials."
    pipeline["decision"] = {"relationship": "new", "finding_id": None, "contradicts_prior": True,
                            "changed_conditions": "The new error path sends credentials to logs.",
                            "history_explanation": "Retry termination does not address credential disclosure.",
                            "cause": "Logging credentials", "consequence": "Credential disclosure",
                            "evidence": [{"revision": "current", "path": COMMENT["path"],
                                          "start_line": 9, "end_line": 9, "quote": "    logger.error(api_key)"}]}
    run_and_publish()
    assert len(pipeline["reviews"]) == 2


@pytest.mark.parametrize("publication", ["pending", "failed", "publishing", "partially_published", "published"])
def test_legacy_summary_publication_is_adopted_without_changing_its_plan(pipeline, publication):
    pipeline["comments"][0]["inline_line"] = None
    old = new_run()
    reviews.perform_review(old)
    with transaction() as db:
        run = db.get(ReviewRun, old)
        run.publication_plan = reviews.publication_plan(db, run)
        saved = copy.deepcopy(run.publication_plan)
        fingerprint = db.scalar(select(Finding)).fingerprint
        if publication != "published":
            run.publication = publication
    if publication == "published":
        reviews.publish_run(old)
    current = run_and_publish()
    if publication != "published":
        reviews.publish_run(old)
    with transaction() as db:
        assert db.get(ReviewRun, old).publication_plan == saved
        assert db.get(ReviewRun, current).publication == "no_new_comments"
        assert db.scalar(select(Finding)).fingerprint == fingerprint
        assert db.scalar(select(Finding)).publication_run_id == old
        assert db.scalar(select(Finding)).published_review_id == 1
    assert len(pipeline["reviews"]) == 1


def test_publication_rechecks_permissions_after_assessment(pipeline, monkeypatch):
    identity = new_run(assessed=True)
    reviews.perform_review(identity)

    def revoked(*args, **kwargs):
        raise github.GitHubError(403, "Permission revoked")
    monkeypatch.setattr(github, "authorize_repo", revoked)
    with pytest.raises(github.GitHubError):
        reviews.publish_run(identity)
    assert not pipeline["reviews"]


def test_additive_migration_keeps_legacy_identity_occurrences_and_frozen_plan(pipeline):
    identity = new_run()
    reviews.perform_review(identity)
    reviews.publish_run(identity)
    with transaction() as db:
        finding = db.scalar(select(Finding))
        before = (finding.id, finding.fingerprint, finding.content, finding.status,
                  db.scalar(select(Occurrence)).snapshot, db.get(ReviewRun, identity).publication_plan)
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261008_pull_creation_time")
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        finding = db.scalar(select(Finding))
        after = (finding.id, finding.fingerprint, finding.content, finding.status,
                 db.scalar(select(Occurrence)).snapshot, db.get(ReviewRun, identity).publication_plan)
        assert before == after
        assert assessment.summary(db, db.get(ReviewRun, identity)) is None
        assert db.scalar(select(func.count()).select_from(ReviewCandidate)) == 0
        assert finding.publication_run_id is None and finding.published_review_id is None


def test_summary_batch_counts_one_published_comment(pipeline):
    pipeline["comments"] = [
        {**COMMENT, "inline_line": None},
        {**COMMENT, "inline_line": None, "content": "Credentials enter the error log."},
    ]
    pipeline["decision"] = {"relationship": "new", "finding_id": None,
                            "history_explanation": "Credential disclosure is separate from nontermination."}
    identity = run_and_publish()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Finding)) == 2
        assert assessment.summary(db, db.get(ReviewRun, identity)).published_comments == 1
    assert len(pipeline["reviews"]) == 1
