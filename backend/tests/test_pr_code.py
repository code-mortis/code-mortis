import copy
import json
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from code_mortis.api_tokens import provenance as pat
from code_mortis.code_graph import service
from code_mortis.core.db import now, transaction
from code_mortis.integrations import comparison as github_comparison
from code_mortis.integrations import github
from code_mortis.models import (
    Finding,
    FindingVerification,
    Job,
    Membership,
    Occurrence,
    PullRequest,
    Repository,
    ReviewRun,
)
from code_mortis.reviews import code
from code_mortis.schemas.pr_code import CodeFileInfo, CodeNavigation

URL = "/api/v1/pulls/pr/code"
HEAD, BASE, MERGE = "a" * 40, "b" * 40, "c" * 40
PATCH = "@@ -1,2 +1,2 @@\n-old\n+mañana 😀\n context\n\\ No newline at end of file"


@pytest.fixture
def comparison(seed, github_stub, monkeypatch):
    github_comparison.cached.cache_clear()
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"id": 22, "permissions": {"pull": True, "push": True}})
    monkeypatch.setattr(github, "repository_read_token", lambda *a, **kw: "read-token")
    data = {"base_commit": {"sha": BASE}, "merge_base_commit": {"sha": MERGE}, "files": [
        {"filename": "notes.md", "status": "modified", "additions": 1, "deletions": 1, "patch": PATCH},
        {"filename": "next.py", "previous_filename": "old.py", "status": "renamed", "additions": 1, "deletions": 1, "patch": PATCH},
        {"filename": "guide.txt", "status": "added", "additions": 0, "deletions": 0},
        {"filename": "image.png", "status": "added", "additions": 0, "deletions": 0},
        {"filename": ".env", "status": "modified", "patch": "+hidden"}]}
    calls, pull_files = [], []
    remote = {"number": 3, "base": {"sha": BASE, "repo": {"id": 22}}, "head": {"sha": HEAD}}
    def request(method, path, token, **kwargs):
        calls.append((path, kwargs))
        if "/files?" in path:
            page = int(path.rsplit("page=", 1)[1])
            return copy.deepcopy(pull_files[(page - 1) * 100:page * 100])
        if "/pulls/" in path:
            return copy.deepcopy(remote)
        return copy.deepcopy(data)
    monkeypatch.setattr(github, "request", request)
    yield data, calls, remote, pull_files
    github_comparison.cached.cache_clear()


def body(path="notes.md", **kwargs):
    return {"base_sha": BASE, "head_sha": HEAD, "merge_base_sha": MERGE, "path": path, **kwargs}


def seed_summary_and_findings():
    with transaction() as db:
        run = ReviewRun(id="run", pr_id="pr", trigger="manual", dedup_key="run", status="completed", base_sha=BASE,
            head_sha=HEAD, report={"manifest": {"coverage": {"selected": [{"path": "notes.md"}]}}}, config={"settings": {"exclusions": ""}}, summary={"status": "completed", "result": {
                "narrative": "Updates documentation", "groups": [{"title": "Documentation", "description": "Clarifies the notes.", "paths": ["notes.md"]}]}})
        db.add(run)
        db.flush()
        for index, sha, start, end in [(0, HEAD, 1, 1), (1, MERGE, 1, 1), (2, None, 1, 1), (3, "d" * 40, 1, 1), (4, HEAD, 8, 9)]:
            finding = Finding(id=f"f{index}", pr_id="pr", fingerprint=f"fp{index}", path="mutable-path.py", content="Mutable text", start_line=500,
                end_line=500, status="resolved" if index == 0 else "open", last_seen_run_id="run")
            db.add(finding)
            db.flush()
            db.add(Occurrence(id=f"o{index}", run_id=run.id, finding_id=finding.id, inline_line=None,
                snapshot={"path": "notes.md", "content": f"Saved evidence {index}", "start_line": start, "end_line": end,
                    "source_sha": sha, "severity": "high", "category": "bug", "existing_code": "old", "suggestion": "new"}))


def test_graph_independent_diff_summary_and_immutable_findings(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        jobs = db.scalar(select(func.count()).select_from(Job))
    overview = client.get(URL)
    assert overview.status_code == 200, overview.text
    assert overview.headers["cache-control"] == "no-store"
    data = overview.json()
    assert data["merge_base_sha"] == MERGE and data["total"] == 3 and len(data["groups"]) == 1
    grouped = client.get(URL + "?group=run:0").json()
    assert grouped["total"] == 1 and grouped["files"][0]["path"] == "notes.md"
    response = client.post(URL + "/file", json=body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["patch_state"] == "available" and result["lines"][2]["text"] == "mañana 😀"
    assert result["lines"][1]["old_line"] == 1 and result["lines"][1]["new_line"] is None
    evidence = {f["occurrence_id"]: f for f in result["findings"]}
    assert [evidence[f"o{i}"]["placement"] for i in range(5)] == ["inline", "inline", "unknown", "historical", "outside_diff"]
    assert evidence["o0"]["current_status"] == "resolved" and "Mutable text" not in response.text
    assert evidence["o1"]["side"] == "old" and MERGE in result["github_url"]
    assert len(comparison[1]) == 4 and comparison[1][0][1]["max_response_bytes"] == code.MAX_RESPONSE_BYTES
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == jobs


def test_pull_overview_uses_completed_current_summary_before_analysis(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        db.get(PullRequest, "pr").description = "Author's **description**"
        run = db.get(ReviewRun, "run")
        run.report = None
        run.status = "running"
        run.config = {**run.config, "pipeline_version": 2}
        run.stages = {"summary": {"status": "completed"}, "analysis": {"status": "pending"}}
        diagram = {"nodes": [{"id": "notes", "label": "Notes", "paths": ["notes.md"]},
                             {"id": "guide", "label": "Guide", "paths": ["guide.txt"]}],
                   "edges": [{"source": "notes", "target": "guide"}]}
        run.summary = {**run.summary, "result": {**run.summary["result"], "diagram": diagram}}
        db.add(ReviewRun(id="newer", pr_id="pr", trigger="manual", dedup_key="newer", status="queued",
                         base_sha=BASE, head_sha=HEAD, config=run.config, summary={"status": "pending"},
                         created_at=now() + timedelta(minutes=1)))
    response = client.get("/api/v1/pulls/pr/overview")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["description"] == "Author's **description**"
    assert data["summary"]["run_id"] == "run"
    assert data["summary"]["groups"][0]["id"] == "run:0"
    assert data["summary"]["diagram"] == diagram
    assert len(comparison[1]) == 1 and "/compare/" in comparison[1][0][0]
    with transaction() as db:
        db.get(ReviewRun, "run").report = {"manifest": {"coverage": {"selected": []}}}
    assert client.get("/api/v1/pulls/pr/overview").json()["summary"]["run_id"] == "run"
    assert client.get(URL + "?group=run:0").json()["group"] == "run:0"


@pytest.mark.parametrize("ignored", [False, True])
def test_pull_overview_checks_retry_policy_instead_of_original_analysis_coverage(comparison, client, ignored):
    seed_summary_and_findings()
    with transaction() as db:
        run = db.get(ReviewRun, "run")
        run.report = {"manifest": {"coverage": {"selected": [{"path": "obsolete.py"}]}}}
        run.progress = {"summary_attempts": ["retry"], **({} if ignored else {"summary_retry": "retry"})}
        db.get(PullRequest, "pr").ignored = ignored
        db.get(Repository, 22).settings = {"exclusions": "obsolete.py"}
        db.add(Job(id="retry", kind="generate_summary", status="done", payload={
            "run_id": "run", "config": {"settings": {"exclusions": "obsolete.py"}},
            "summary_attempt_result": run.summary,
        }))
    assert client.get("/api/v1/pulls/pr/overview").json()["summary"]["run_id"] == "run"
    assert client.get(URL + "?group=run:0").json()["group"] == "run:0"


def test_pull_overview_keeps_rename_paths_and_code_reuses_the_current_diff_row(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        run = db.get(ReviewRun, "run")
        group = {"title": "Rename", "description": "Moves the file", "paths": ["old.py", "next.py"]}
        diagram = {"nodes": [{"id": "rename", "label": "Rename", "paths": ["old.py"]}], "edges": []}
        run.summary = {**run.summary, "result": {**run.summary["result"], "groups": [group], "diagram": diagram}}
    summary = client.get("/api/v1/pulls/pr/overview").json()["summary"]
    assert summary["groups"][0]["paths"] == ["old.py", "next.py"]
    assert summary["diagram"] == diagram
    code_group = client.get(URL + "?group=run:0").json()
    assert code_group["group"] == "run:0" and [file["path"] for file in code_group["files"]] == ["next.py"]


def test_pull_overview_masks_credentials_in_author_markdown_without_changing_storage(comparison, client):
    description = "**Uses Bearer authentication**\n\nProvider example: provider-secret"
    with transaction() as db:
        db.get(PullRequest, "pr").description = description
    response = client.get("/api/v1/pulls/pr/overview")
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "**Uses Bearer [redacted]**\n\nProvider example: [redacted]"
    with transaction() as db:
        assert db.get(PullRequest, "pr").description == description


@pytest.mark.parametrize("cause", ["stale_revision", "exclusions", "credential", "diagram_path"])
def test_pull_overview_withholds_ineligible_summaries(comparison, client, cause):
    seed_summary_and_findings()
    with transaction() as db:
        db.get(PullRequest, "pr").description = "Author text is separate"
        run = db.get(ReviewRun, "run")
        if cause == "stale_revision":
            run.base_sha = "d" * 40
        elif cause == "exclusions":
            db.get(Repository, 22).settings = {"exclusions": "notes.md"}
        elif cause == "credential":
            run.summary = {**run.summary, "result": {**run.summary["result"], "narrative": "provider-secret"}}
        else:
            run.summary = {**run.summary, "result": {**run.summary["result"], "diagram": {
                "nodes": [{"id": "hidden", "label": "Hidden", "paths": ["image.png"]}], "edges": [],
            }}}
    response = client.get("/api/v1/pulls/pr/overview")
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "Author text is separate"
    assert response.json()["summary"] is None
    if cause == "diagram_path":
        assert client.get(URL + "?group=run:0").json()["group"] == "run:0"


def seed_current_verification(verdict="still_present"):
    seed_summary_and_findings()
    with transaction() as db:
        db.get(Finding, "f3").status = "resolved"
        db.add(FindingVerification(id="verified", finding_id="f3", run_id="run", occurrence_id="o3",
            head_sha=HEAD, finding_version=0, status="completed", verdict=verdict,
            result={"verdict": verdict, "original_issue_present": True, "explanation": "Saved verification",
                    "change_explanation": "Compared both revisions", "dismissal_recommended": False,
                    "evidence": [{"revision": "current", "path": "notes.md", "start_line": 1, "end_line": 2,
                                  "quote": "mañana 😀\ncontext\n"}]}))


@pytest.mark.parametrize("verdict", ["fixed", "still_present", "uncertain"])
def test_historical_findings_use_exact_current_verification_without_rewriting_evidence(comparison, client, verdict):
    seed_current_verification(verdict)
    with transaction() as db:
        jobs = db.scalar(select(func.count()).select_from(Job))
    response = client.post(URL + "/file", json=body())
    assert response.status_code == 200, response.text
    finding = next(item for item in response.json()["findings"] if item["finding_id"] == "f3")
    assert (finding["placement"], finding["side"], finding["line"]) == ("inline", "new", 2)
    assert finding["verification"] == {"id": "verified", "verdict": verdict, "source_sha": HEAD,
                                       "start_line": 1, "end_line": 2}
    assert finding["current_status"] == "resolved"
    assert finding["source_sha"] == "d" * 40 and finding["snapshot"]["existing_code"] == "old"
    assert finding["snapshot"]["start_line"] == finding["snapshot"]["end_line"] == 1
    assert "Saved verification" not in response.text and len(comparison[1]) == 4
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == jobs
        assert db.get(Occurrence, "o3").snapshot["source_sha"] == "d" * 40


@pytest.mark.parametrize("invalid", ["head", "run", "occurrence", "status", "verdict", "path", "quote", "range", "partial_quote", "oversized"])
def test_verification_anchors_reject_mismatched_or_unavailable_evidence(comparison, client, invalid):
    seed_current_verification()
    with transaction() as db:
        row = db.get(FindingVerification, "verified")
        value = copy.deepcopy(row.result)
        if invalid == "head":
            row.head_sha = "e" * 40
        elif invalid == "run":
            db.get(ReviewRun, "run").head_sha = "e" * 40
        elif invalid == "occurrence":
            row.occurrence_id = "o0"
        elif invalid == "status":
            row.status = "failed"
        elif invalid == "verdict":
            row.verdict = "fixed"
        elif invalid == "path":
            value["evidence"][0]["path"] = "other.py"
        elif invalid == "quote":
            value["evidence"][0]["quote"] = "different\ncontext\n"
        elif invalid == "range":
            value["evidence"][0]["end_line"] = 9999999
        elif invalid == "partial_quote":
            value["evidence"][0]["quote"] = "context"
        elif invalid == "oversized":
            value["explanation"] = "x" * (code.MAX_VERIFICATION_BYTES + 1)
        row.result = value
    finding = next(item for item in client.post(URL + "/file", json=body()).json()["findings"] if item["finding_id"] == "f3")
    assert finding["placement"] == "historical" and finding["verification"] is None


def test_current_verification_can_span_omitted_context_but_every_visible_line_must_match(comparison, client):
    seed_current_verification()
    comparison[0]["files"][0].update(patch="@@ -1 +1 @@\n-old\n+mañana 😀\n@@ -5 +5 @@\n context")
    with transaction() as db:
        row = db.get(FindingVerification, "verified")
        row.result = {**row.result, "evidence": [{"revision": "current", "path": "notes.md", "start_line": 1,
                     "end_line": 5, "quote": "mañana 😀\nomitted\nomitted\nomitted\ncontext\n"}]}
    finding = next(item for item in client.post(URL + "/file", json=body()).json()["findings"] if item["finding_id"] == "f3")
    assert finding["line"] == 5 and finding["verification"]["end_line"] == 5


def test_newer_failed_verification_does_not_fall_back_and_history_is_newest_first(comparison, client):
    seed_current_verification()
    with transaction() as db:
        db.add(ReviewRun(id="later", pr_id="pr", trigger="manual", dedup_key="later", status="completed", base_sha=BASE, head_sha=HEAD, config={}, report={}))
        db.flush()
        db.add(FindingVerification(id="later-check", finding_id="f3", run_id="later", occurrence_id="o3",
            head_sha=HEAD, finding_version=0, status="failed", created_at=now() + timedelta(seconds=1)))
    finding = next(item for item in client.post(URL + "/file", json=body()).json()["findings"] if item["finding_id"] == "f3")
    assert finding["placement"] == "historical" and finding["verification"] is None
    detail = client.get("/api/v1/findings/f3").json()
    assert [row["id"] for row in detail["verifications"]] == ["later-check", "verified"]


@pytest.mark.parametrize("changed_quote", [False, True])
def test_prior_verification_keeps_marker_only_for_unchanged_complete_quote(comparison, client, changed_quote):
    seed_current_verification()
    with transaction() as db:
        db.get(ReviewRun, "run").head_sha = "d" * 40
        db.get(FindingVerification, "verified").head_sha = "d" * 40
    if changed_quote:
        comparison[0]["files"][0]["patch"] = PATCH.replace("mañana 😀", "changed")
    result = client.post(URL + "/file", json=body()).json()
    finding = next(item for item in result["findings"] if item["finding_id"] == "f3")
    assert result["head_sha"] == HEAD
    if changed_quote:
        assert finding["placement"] == "historical" and finding["verification"] is None
    else:
        assert finding["placement"] == "inline" and finding["line"] == 2
        assert finding["verification"]["source_sha"] == "d" * 40
        assert finding["snapshot"]["existing_code"] == "old" and finding["current_status"] == "resolved"


def test_prior_verification_requires_omitted_context_to_be_visible(comparison, client):
    seed_current_verification()
    comparison[0]["files"][0].update(patch="@@ -1 +1 @@\n-old\n+mañana 😀\n@@ -5 +5 @@\n context")
    with transaction() as db:
        db.get(ReviewRun, "run").head_sha = "d" * 40
        row = db.get(FindingVerification, "verified")
        row.head_sha = "d" * 40
        row.result = {**row.result, "evidence": [{"revision": "current", "path": "notes.md",
            "start_line": 1, "end_line": 5, "quote": "mañana 😀\nomitted\nomitted\nomitted\ncontext\n"}]}
    finding = next(item for item in client.post(URL + "/file", json=body()).json()["findings"]
                   if item["finding_id"] == "f3")
    assert finding["placement"] == "historical" and finding["verification"] is None


def test_current_failed_verification_takes_precedence_over_prior_success(comparison, client):
    seed_current_verification()
    with transaction() as db:
        db.get(ReviewRun, "run").head_sha = "d" * 40
        db.get(FindingVerification, "verified").head_sha = "d" * 40
        db.add(ReviewRun(id="current", pr_id="pr", trigger="manual", dedup_key="current", status="failed",
                        base_sha=BASE, head_sha=HEAD, config={}, report={}))
        db.flush()
        db.add(FindingVerification(id="failed", finding_id="f3", run_id="current", occurrence_id="o3",
            head_sha=HEAD, finding_version=0, status="failed", created_at=now() - timedelta(days=1)))
    finding = next(item for item in client.post(URL + "/file", json=body()).json()["findings"]
                   if item["finding_id"] == "f3")
    assert finding["placement"] == "historical" and finding["verification"] is None


@pytest.mark.parametrize("invalid", [None, "changed", "incomplete", "blank", "source", "run", "path"])
def test_prior_review_marker_requires_original_provenance_and_complete_quote(comparison, client, invalid):
    seed_summary_and_findings()
    with transaction() as db:
        db.get(ReviewRun, "run").head_sha = "d" * 40
        row = db.get(Occurrence, "o3")
        snapshot = {**row.snapshot, "existing_code": "mañana 😀\ncontext", "end_line": 2}
        if invalid == "changed":
            snapshot["existing_code"] = "different\ncontext"
        elif invalid == "incomplete":
            snapshot["existing_code"] = "mañana 😀"
        elif invalid == "blank":
            snapshot.update(existing_code="\n", end_line=1)
        elif invalid == "source":
            snapshot["source_sha"] = None
        elif invalid == "run":
            db.get(ReviewRun, "run").head_sha = "e" * 40
        elif invalid == "path":
            snapshot["path"] = "old.py"
        row.snapshot = snapshot
    result = client.post(URL + "/file", json=body()).json()
    finding = next((item for item in result["findings"] if item["finding_id"] == "f3"), None)
    if invalid == "path":
        assert finding is None
    elif invalid:
        assert finding["placement"] != "inline"
    else:
        assert finding["placement"] == "inline" and finding["line"] == 2
        assert finding["source_sha"] == "d" * 40 and finding["verification"] is None


@pytest.mark.parametrize("path,state", [("guide.txt", "unavailable"), ("next.py", "available")])
def test_missing_patch_and_rename(comparison, client, path, state):
    result = client.post(URL + "/file", json=body(path)).json()
    assert result["patch_state"] == state
    if path == "next.py":
        assert result["file"]["previous_path"] == "old.py"
    else:
        assert result["file"]["additions"] is None and result["file"]["deletions"] is None


def test_pr_files_fallback_recovers_second_page_counts_patch_and_rename(comparison, client):
    data, calls, _, pull_files = comparison
    data["files"] = [{"filename": "next.py", "previous_filename": "old.py", "status": "renamed",
                      "sha": "d" * 40, "additions": 0, "deletions": 0}]
    pull_files.extend({"filename": f"file{i}.py", "status": "added"} for i in range(100))
    pull_files.append({**data["files"][0], "additions": 1, "deletions": 1, "patch": PATCH})
    response = client.post(URL + "/workspace", json={"path": "next.py"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["head_sha"] == HEAD and result["base_sha"] == BASE and result["merge_base_sha"] == MERGE
    assert result["files"][0]["additions"] == result["files"][0]["deletions"] == 1
    assert result["diff"]["patch_state"] == "available"
    assert result["diff"]["lines"][2]["text"] == "mañana 😀"
    assert result["diff"]["file"]["previous_path"] == "old.py"
    assert len(calls) == 5
    assert calls[2][0].endswith("/files?per_page=100&page=1")
    assert calls[3][0].endswith("/files?per_page=100&page=2")
    assert 0 < calls[3][1]["max_response_bytes"] < calls[2][1]["max_response_bytes"]
    assert calls[1][0] == calls[4][0] == "/repos/team/project/pulls/3"
    assert client.get(URL).status_code == 200 and len(calls) == 5


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_pr_files_fallback_rejects_revision_races_without_caching(comparison, client, monkeypatch, side, when):
    _, calls, remote, _ = comparison
    original = github.request
    def request(method, path, token, **kwargs):
        result = original(method, path, token, **kwargs)
        if "/files?" in path:
            remote[side]["sha"] = "e" * 40
        return result
    if when == "before":
        remote[side]["sha"] = "e" * 40
    else:
        monkeypatch.setattr(github, "request", request)
    response = client.post(URL + "/workspace", json={})
    assert response.status_code == 409 and "mañana" not in response.text
    assert len(calls) == (2 if when == "before" else 4)
    assert github_comparison.cached.cache_info().currsize == 0
    remote[side]["sha"] = BASE if side == "base" else HEAD
    monkeypatch.setattr(github, "request", original)
    assert client.get(URL).status_code == 200


@pytest.mark.parametrize(("key", "value"), [
    ("sha", "e" * 40), ("sha", None), ("status", "removed"), ("previous_filename", "different.py"),
    ("patch", 123), ("additions", -1),
])
def test_pr_files_fallback_validates_recovered_files(comparison, client, key, value):
    data, _, _, pull_files = comparison
    data["files"] = [{"filename": "notes.md", "status": "modified", "sha": "d" * 40}]
    pull_files.append({**data["files"][0], "patch": PATCH, key: value})
    assert client.get(URL).status_code == 422
    assert github_comparison.cached.cache_info().currsize == 0


def test_pr_files_fallback_bounds_pagination_and_preserves_unknown_counts(comparison, client):
    data, calls, _, pull_files = comparison
    data["files"] = [{"filename": "absent.py", "status": "added", "additions": 0, "deletions": 0}]
    pull_files.extend({"filename": f"file{i}.py", "status": "added"} for i in range(301))
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["additions"] is None
    assert response.json()["files"][0]["deletions"] is None
    assert len([path for path, _ in calls if "/files?" in path]) == 3


def test_pr_files_fallback_rejects_duplicate_pages(comparison, client):
    _, _, _, pull_files = comparison
    pull_files.extend({"filename": f"file{i % 100}.py", "status": "added"} for i in range(101))
    assert client.get(URL).status_code == 422


def test_pr_files_fallback_enforces_aggregate_byte_limit(comparison, client, monkeypatch):
    _, _, _, pull_files = comparison
    pull_files.append({"filename": "ignored.py", "status": "added", "patch": "x" * 1000})
    monkeypatch.setattr(github_comparison, "MAX_RESPONSE_BYTES", 1000)
    assert client.get(URL).status_code == 422


def test_pr_files_fallback_reapplies_source_checks(comparison, client):
    data, _, _, pull_files = comparison
    data["files"] = [{"filename": "notes.md", "status": "added", "sha": "d" * 40}]
    pull_files.append({**data["files"][0], "additions": 1, "deletions": 0,
                       "patch": "@@ -0,0 +1 @@\n+github-user-token"})
    response = client.post(URL + "/file", json=body())
    assert response.status_code == 200 and "github-user-token" not in response.text
    assert response.json()["patch_state"] == "withheld" and response.json()["lines"] == []


def test_complete_comparison_needs_no_pr_files_fallback(comparison, client):
    data, calls, _, _ = comparison
    data["files"] = [data["files"][0]]
    assert client.get(URL).status_code == 200
    assert len(calls) == 1


def test_cache_reapplies_policy_before_paths_counts_groups(comparison, client):
    seed_summary_and_findings()
    assert client.get(URL).json()["total"] == 3
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "old.py,notes.md"}
    result = client.get(URL).json()
    assert result["total"] == 1 and not result["groups"] and "next.py" not in json.dumps(result)
    assert client.post(URL + "/file", json=body()).status_code == 404
    assert len(comparison[1]) == 4


def test_mandatory_exclusions_hide_source_without_changing_saved_evidence(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        occurrence = db.get(Occurrence, "o2")
        saved = {**occurrence.snapshot, "path": "image.png"}
        occurrence.snapshot = saved
    overview = client.get(URL).json()
    workspace = client.post(URL + "/workspace", json={}).json()
    for result in (overview, workspace):
        assert {item["path"] for item in result["files"]} == {"guide.txt", "next.py", "notes.md"}
    assert client.post(URL + "/file", json=body("image.png")).status_code == 404
    with transaction() as db:
        assert db.get(Finding, "f2").status == "open"
        assert db.get(Occurrence, "o2").snapshot == saved


@pytest.mark.parametrize("change", ["head", "base", "policy", "membership", "github"])
def test_response_boundary_races_hide_content(comparison, client, monkeypatch, change):
    original = code.parse_patch
    def raced(*args):
        result = original(*args)
        with transaction() as db:
            if change in {"head", "base"}:
                setattr(db.get(PullRequest, "pr"), f"{change}_sha", "e" * 40)
            elif change == "policy":
                db.get(Repository, 22).settings = {"exclusions": "notes.md"}
            elif change == "membership":
                db.delete(db.get(Membership, 7))
            else:
                monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"id": 999})
        return result
    monkeypatch.setattr(code, "parse_patch", raced)
    result = client.post(URL + "/file", json=body())
    assert result.status_code in {403, 409} and "mañana" not in result.text


@pytest.mark.parametrize("key", ["base_sha", "head_sha", "merge_base_sha"])
def test_expected_identity_rejected(comparison, client, key):
    assert client.post(URL + "/file", json=body(**{key: "d" * 40})).status_code == 409


@pytest.mark.parametrize("patch,state", [("bad patch", "unavailable"), ("@@ -1 +1 @@\n-old", "truncated"),
    ("@@ -1 +1 @@\n-" + "x" * code.MAX_PATCH_BYTES, "too_large"),
    ("x" * code.MAX_PATCH_BYTES + "provider-secret", "withheld"),
    (PATCH.replace("\n", "\r\n"), "available")])
def test_patches_validated_and_secrets_checked_before_clipping(comparison, client, patch, state):
    comparison[0]["files"][0]["patch"] = patch
    result = client.post(URL + "/file", json=body())
    assert result.status_code == 200, result.text
    assert result.json()["patch_state"] == state and "provider-secret" not in result.text


def test_large_render_bound_addition_and_removal():
    info = CodeFileInfo(path="a.txt", change="added", additions=2500, deletions=0)
    state, lines = code.parse_patch("@@ -0,0 +1,2500 @@\n" + "\n".join("+line" for _ in range(2500)), info)
    assert state == "truncated" and len(lines) == code.MAX_LINES and lines[-1]["new_line"] == 1999
    info = CodeFileInfo(path="a.txt", change="removed", additions=0, deletions=1)
    assert code.parse_patch("@@ -1 +0,0 @@\n-line", info)[0] == "available"


@pytest.mark.parametrize("mutation", ["head", "policy", "missing_policy", "secret", "hidden_path", "malformed"])
def test_summary_must_match_saved_identity_and_current_policy(comparison, client, mutation):
    seed_summary_and_findings()
    with transaction() as db:
        run = db.get(ReviewRun, "run")
        if mutation == "head":
            run.head_sha = "e" * 40
        elif mutation == "policy":
            run.config = {"settings": {"exclusions": "different.py"}}
        elif mutation == "missing_policy":
            run.config = {}
        else:
            summary = copy.deepcopy(run.summary)
            if mutation == "secret":
                summary["result"]["groups"][0]["description"] = "provider-secret"
            elif mutation == "hidden_path":
                summary["result"]["groups"][0]["paths"] = [".env"]
            else:
                summary["result"] = {"invalid": True}
            run.summary = summary
    result = client.get(URL).json()
    assert result["groups"] == [] and result["total"] == 3


def test_inventory_pagination_cap_and_unknown_groups(comparison, client):
    comparison[0]["files"] = [{"filename": f"a{i:03}.txt", "status": "modified"} for i in range(300)]
    result = client.get(URL + "?page=2&q=a0&group=gone").json()
    assert result["total"] == 100 and len(result["files"]) == 10 and result["files"][0]["path"] == "a010.txt"
    assert result["comparison_truncated"] and result["group"] is None


@pytest.mark.parametrize("change", ["base", "path", "files", "rename", "patch"])
def test_malformed_provider_metadata(comparison, client, change):
    data = comparison[0]
    if change == "base":
        data["base_commit"]["sha"] = "e" * 40
    elif change == "path":
        data["files"][0]["filename"] = "../oops"
    elif change == "files":
        data["files"] = [None]
    elif change == "rename":
        del data["files"][1]["previous_filename"]
    else:
        data["files"][0]["patch"] = 123
    assert client.get(URL).status_code == 422


def test_token_provenance_denied_even_on_direct_read(comparison, seed):
    token = pat.execution_context.set({"token_id": "test", "user_id": 7, "scope": "reviews:read"})
    try:
        with transaction() as db, pytest.raises(service.AccessLost):
            code.overview(db, seed["user"], "pr")
        with transaction() as db, pytest.raises(service.AccessLost):
            code.workspace(db, seed["user"], "pr", CodeNavigation())
    finally:
        pat.execution_context.reset(token)


def test_streamed_response_limit_before_json(monkeypatch):
    class Stream:
        status_code = 200
        headers = {}
        def iter_bytes(self, **kwargs):
            yield b"a" * 10
            yield b"b" * 11
            pytest.fail("Must stop reading immediately at the bound")
    @contextmanager
    def stream(*args, **kwargs):
        yield Stream()
    monkeypatch.setattr(github.http_client(), "stream", stream)
    with pytest.raises(github.GitHubError, match="too large"):
        github.request("GET", "/bounded", "token", max_response_bytes=20)


def test_compressed_bounded_response_is_not_decoded_twice(monkeypatch):
    class Stream:
        status_code = 200
        headers = {"content-encoding": "gzip", "content-length": "40"}
        def iter_bytes(self, **kwargs):
            yield b'{"ok": true}'
    @contextmanager
    def stream(*args, **kwargs):
        yield Stream()
    monkeypatch.setattr(github.http_client(), "stream", stream)
    assert github.request("GET", "/bounded", "token", max_response_bytes=20) == {"ok": True}


def test_stream_deadline_is_bounded(monkeypatch):
    ticks = iter([0, 21])
    monkeypatch.setattr(github.time, "monotonic", lambda: next(ticks))
    class Stream:
        status_code = 200
        headers = {}
        def iter_bytes(self, **kwargs):
            yield b"{}"
    @contextmanager
    def stream(*args, **kwargs):
        yield Stream()
    monkeypatch.setattr(github.http_client(), "stream", stream)
    with pytest.raises(github.GitHubError, match="too long"):
        github.request("GET", "/bounded", "token", max_response_bytes=20)


def test_summary_all_input_paths_rechecked_not_only_groups(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        db.get(ReviewRun, "run").report = {"manifest": {"coverage": {"selected": [{"path": "notes.md"}, {"path": ".env"}]}}}
    assert client.get(URL).json()["groups"] == []


def test_occurrences_are_bounded_and_unmatched_ranges_are_not_anchored(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        snapshot = {"path": "notes.md", "source_sha": HEAD, "content": "Saved", "start_line": 1, "end_line": 3}
        for index in range(101):
            db.add(Finding(id=f"extra{index}", pr_id="pr", fingerprint=f"extra{index}", path="notes.md", content="Saved",
                           start_line=1, end_line=3, last_seen_run_id="run"))
        db.flush()
        for index in range(101):
            db.add(Occurrence(id=f"extra{index}", run_id="run", finding_id=f"extra{index}", snapshot=snapshot))
    result = client.post(URL + "/file", json=body()).json()
    assert len(result["findings"]) == 100 and result["findings_truncated"]
    assert all(item["placement"] == "outside_diff" for item in result["findings"])


@pytest.mark.parametrize("padding", ["x" * 33000, "😀" * 9000], ids=["ascii", "multibyte"])
def test_diff_bounds_evidence_before_loading_and_omits_full_run_payloads(comparison, client, monkeypatch, padding):
    from sqlalchemy import event

    from code_mortis.core.db import engine

    seed_summary_and_findings()
    with transaction() as db:
        db.get(ReviewRun, "run").report = {"padding": "x" * (2 * 1024 * 1024)}
        db.get(Occurrence, "o1").snapshot = {**db.get(Occurrence, "o1").snapshot, "unused": padding}
    statements, snapshots = [], []
    validate = code.evidence_snapshot
    def check_snapshot(raw, check):
        snapshots.append(raw)
        return validate(raw, check)
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    monkeypatch.setattr(code, "evidence_snapshot", check_snapshot)
    event.listen(engine(), "before_cursor_execute", record)
    try:
        response = client.post(URL + "/file", json=body())
    finally:
        event.remove(engine(), "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    assert {item["occurrence_id"] for item in response.json()["findings"]} == {"o0", "o2", "o3", "o4"}
    assert len(snapshots) == 5 and snapshots.count(None) == 1
    assert all(raw is None or len(json.dumps(raw).encode()) <= 32768 for raw in snapshots)
    occurrence_query = next(q for q in statements if "FROM occurrences JOIN review_runs" in q)
    assert all(f"review_runs.{column}" not in occurrence_query for column in ("report", "config", "summary"))


def test_mcp_provenance_cannot_use_browser_reads(comparison, seed, monkeypatch):
    from code_mortis.mcp import provenance
    monkeypatch.setattr(provenance, "current_provenance", lambda: {"domain": "code", "scope": "code:read"})
    with transaction() as db, pytest.raises(service.AccessLost):
        code.overview(db, seed["user"], "pr")
    with transaction() as db, pytest.raises(service.AccessLost):
        code.workspace(db, seed["user"], "pr", CodeNavigation())


@pytest.mark.parametrize("report", [None, {}, {"manifest": None}, {"manifest": {"coverage": []}}])
def test_unknown_summary_input_inventory_never_blocks_diff(comparison, client, report):
    seed_summary_and_findings()
    with transaction() as db:
        db.get(ReviewRun, "run").report = report
    response = client.get(URL)
    assert response.status_code == 200 and response.json()["groups"] == []
    assert client.post(URL + "/file", json=body()).json()["patch_state"] == "available"


def test_workspace_returns_initial_diff_and_full_deduplicated_inventory(comparison, client, monkeypatch):
    seed_summary_and_findings()
    auth = []
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw:
        auth.append(kw.get("fresh", False)) or {"id": 22, "permissions": {"pull": True}})
    result = client.post(URL + "/workspace", json={"query": "notes"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["path"] == "notes.md" and data["diff"]["patch_state"] == "available"
    assert len(data["files"]) == 3 and data["groups"][0]["file_indexes"] == [2]
    assert auth.count(True) == 2 and not data["graph_enabled"]
    assert "no-store" in result.headers["cache-control"]
    assert len(comparison[1]) == 4


@pytest.mark.parametrize("change", ["head", "policy", "membership", "github"])
def test_workspace_preserves_final_access_gate(comparison, client, monkeypatch, change):
    original = code.read_file

    def raced(*args):
        result = original(*args)
        with transaction() as db:
            if change == "head":
                db.get(PullRequest, "pr").head_sha = "f" * 40
            elif change == "policy":
                db.get(Repository, 22).settings = {"exclusions": "notes.md"}
            elif change == "membership":
                db.delete(db.get(Membership, 7))
            else:
                monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"id": 999})
        return result

    monkeypatch.setattr(code, "read_file", raced)
    result = client.post(URL + "/workspace", json={"path": "notes.md"})
    assert result.status_code in {403, 409} and "mañana" not in result.text


def test_workspace_bounds_metadata_and_maps_legacy_rename(comparison, client, monkeypatch):
    data = client.post(URL + "/workspace", json={"path": "old.py"}).json()
    assert data["path"] == "next.py" and data["diff"]["file"]["previous_path"] == "old.py"
    data = client.post(URL + "/workspace", json={"path": "next.py", "revision": "base", "view": "source"}).json()
    assert data["path"] == "old.py" and data["revision"] == "base" and data["graph"] is None
    monkeypatch.setattr(code, "MAX_METADATA_BYTES", 200)
    data = client.post(URL + "/workspace", json={}).json()
    assert data["metadata_truncated"] and len(json.dumps({"files": data["files"], "groups": data["groups"]}).encode()) <= 200


def test_workspace_empty_inventory_and_wrong_comparison(comparison, client):
    data = client.post(URL + "/workspace", json={"query": "absent"}).json()
    assert data["path"] is None and data["diff"] is None
    expected = {k: v for k, v in body().items() if k != "path"}
    expected["merge_base_sha"] = "d" * 40
    assert client.post(URL + "/workspace", json={"expected": expected, "path": "notes.md"}).status_code == 409


def test_shared_comparison_serializes_concurrent_misses(comparison):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: github_comparison.load(22, 10, "team/project", BASE, HEAD), range(8)))
    assert len(comparison[1]) == 1 and all(result == results[0] for result in results)


def test_pull_detail_supplies_counts_without_hidden_finding_reads(comparison, client):
    seed_summary_and_findings()
    response = client.get("/api/v1/pulls/pr")
    assert response.status_code == 200, response.text
    assert response.json()["finding_counts"] == {"open": 4, "resolved": 1}


def test_finding_counts_deduplicate_use_immutable_sides_and_withhold_invalid(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        original = db.get(Occurrence, "o1")
        db.add(ReviewRun(id="repeat", pr_id="pr", trigger="manual", dedup_key="repeat", status="completed", base_sha=BASE, head_sha=HEAD, config={}, report={}))
        db.flush()
        db.add(Occurrence(id="duplicate", run_id="repeat", finding_id="f1", snapshot=original.snapshot))
        db.get(Occurrence, "o4").snapshot = {**db.get(Occurrence, "o4").snapshot, "path": "old.py", "source_sha": MERGE}
    data = client.post(URL + "/workspace", json={"with_findings": True}).json()
    assert [s["open_count"] for s in data["file_states"]] == [0, 1, 3]
    assert data["file_states"][2]["resolved_count"] == 1
    assert all(s["complete"] for s in data["file_states"])
    with transaction() as db:
        db.get(Finding, "f1").status = "dismissed"
        db.get(Occurrence, "o4").snapshot = {**db.get(Occurrence, "o4").snapshot, "content": "provider-secret"}
    data = client.post(URL + "/workspace", json={"with_findings": True}).json()
    assert data["path"] == "next.py"
    assert data["file_states"][1] == {"reviewed": False, "open_count": 0, "resolved_count": 0, "dismissed_count": 0, "complete": False}
    assert data["file_states"][2]["dismissed_count"] == 1


@pytest.mark.parametrize(("findings", "path"), [
    ("any", "next.py"), ("none", "guide.txt"), ("open", "next.py"), ("resolved", "notes.md"), ("dismissed", "next.py"),
    (["open", "resolved"], "next.py"), (["resolved", "dismissed"], "next.py"),
    (["none", "resolved"], "guide.txt"), (["any", "none"], "guide.txt"), (["resolved"], "notes.md"),
])
def test_workspace_filters_saved_findings_across_revisions_and_renames(comparison, client, findings, path):
    with transaction() as db:
        for index in range(2):
            db.add(ReviewRun(id=f"historical{index}", pr_id="pr", trigger="manual", dedup_key=f"historical{index}", status="completed",
                             base_sha="e" * 40, head_sha="f" * 40, config={}, report={}))
        db.flush()
        for status, saved_path in [("resolved", "notes.md"), ("dismissed", "old.py"), ("open", "next.py")]:
            db.add(Finding(id=status, pr_id="pr", fingerprint=status, path="mutable-path.py", content="Mutable", status=status, start_line=1, end_line=1, last_seen_run_id="historical1"))
            db.flush()
            for index in range(2):
                db.add(Occurrence(id=f"{status}{index}", run_id=f"historical{index}", finding_id=status,
                                  snapshot={"path": saved_path, "content": "Saved evidence", "source_sha": "f" * 40}))
    result = client.post(URL + "/workspace", json={"findings": findings})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["path"] == path
    assert data["file_states"] == [
        {"reviewed": False, "open_count": 0, "resolved_count": 0, "dismissed_count": 0, "complete": True},
        {"reviewed": False, "open_count": 1, "resolved_count": 0, "dismissed_count": 1, "complete": True},
        {"reviewed": False, "open_count": 0, "resolved_count": 1, "dismissed_count": 0, "complete": True},
    ]
    filters = [findings] if isinstance(findings, str) else findings
    if "none" not in filters:
        assert any(item["current_status"] in filters or "any" in filters for item in data["diff"]["findings"])
    if "resolved" in filters:
        assert client.post(URL + "/workspace", json={"findings": findings, "query": "notes"}).json()["path"] == "notes.md"
    assert client.post(URL + "/workspace", json={"findings": findings, "query": "absent"}).json()["path"] is None


def test_without_findings_never_treats_incomplete_counts_as_zero(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        for occurrence in db.scalars(select(Occurrence)):
            occurrence.snapshot = {**occurrence.snapshot, "content": "provider-secret"}
    assert client.post(URL + "/workspace", json={"findings": "any", "query": "notes"}).json()["path"] == "notes.md"
    assert client.post(URL + "/workspace", json={"findings": "none", "query": "notes"}).json()["path"] is None
    assert client.post(URL + "/workspace", json={"findings": ["none", "resolved"], "query": "notes"}).json()["path"] == "notes.md"


@pytest.mark.parametrize("filters", [{"findings": ["invalid"]}, {"findings": ["open"] * 6}, {"reviewed": [True] * 3}])
def test_workspace_rejects_invalid_or_unbounded_filter_lists(comparison, client, filters):
    assert client.post(URL + "/workspace", json=filters).status_code == 422


def test_bounded_counts_and_constant_database_queries(comparison, client, monkeypatch):
    from time import perf_counter

    from sqlalchemy import event

    from code_mortis.core.db import engine
    seed_summary_and_findings()
    comparison[0]["files"] = [{"filename": "notes.md", "status": "modified"}] + [
        {"filename": f"src/file{i}.py", "status": "modified"} for i in range(299)]
    with transaction() as db:
        snapshot = {"path": "notes.md", "source_sha": HEAD, "start_line": 1, "end_line": 1, "content": "x" * 31500}
        db.add_all([Finding(id=f"bounded{i:04}", pr_id="pr", fingerprint=f"bounded{i:04}", path="notes.md", content="Saved", start_line=1, end_line=1, last_seen_run_id="run") for i in range(1001)])
        db.flush()
        db.add_all([Occurrence(id=f"bounded{i:04}", run_id="run", finding_id=f"bounded{i:04}", snapshot=snapshot) for i in range(1001)])
        db.get(Occurrence, "o4").snapshot = {**snapshot, "content": "x" * 33000}
    statements = []
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine(), "before_cursor_execute", record)
    started = perf_counter()
    try:
        result = client.post(URL + "/workspace", json={"view": "dependencies"})
    finally:
        event.remove(engine(), "before_cursor_execute", record)
    elapsed = perf_counter() - started
    assert result.status_code == 200, result.text
    data = result.json()
    assert len(data["files"]) == 300 and data["file_states"][0]["open_count"] == 1000
    assert all(not s["complete"] for s in data["file_states"])
    assert len([q for q in statements if "JOIN review_runs" in q and "occurrences" in q]) == 1
    assert len(statements) < 80
    print(f"\n300 files / 1000 candidates: {elapsed:.3f}s, {len(statements)} SQL statements, {len(result.content)} response bytes")
    monkeypatch.setattr(code, "MAX_METADATA_BYTES", 600)
    limited = client.post(URL + "/workspace", json={"view": "dependencies"}).json()
    assert limited["metadata_truncated"] and len(limited["files"]) == len(limited["file_states"]) < 300


def test_oversized_evidence_count_is_unknown(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        db.get(Occurrence, "o1").snapshot = {**db.get(Occurrence, "o1").snapshot, "unused": "x" * 33000}
    data = client.post(URL + "/workspace", json={}).json()
    assert data["file_states"][2] == {"reviewed": False, "open_count": 3, "resolved_count": 1, "dismissed_count": 0, "complete": False}



def saved_coverage(completed=("notes.md",), failed=("next.py",), reused=()):
    def entries(paths):
        return [{"item_id": path, "path": path} for path in paths]
    return {"selected": entries((*completed, *failed, *reused)), "completed": entries(completed),
            "failed": entries(failed), "reused": entries(reused), "waived": []}


def test_automatic_review_checks_use_validated_results_without_any_writes(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        run = db.get(ReviewRun, "run")
        run.status = "partial"
        run.report = {"manifest": {"coverage": saved_coverage(reused=("guide.txt",))}}
        before = [(f.id, f.status) for f in db.scalars(select(Finding))]
        jobs = db.scalar(select(func.count()).select_from(Job))
    data = client.post(URL + "/workspace", json={"unreviewed": True}).json()
    assert [state["reviewed"] for state in data["file_states"]] == [True, False, True]
    assert data["path"] == "next.py"
    assert client.post(URL + "/workspace", json={"reviewed": True}).json()["path"] == "guide.txt"
    assert client.post(URL + "/workspace", json={"reviewed": False}).json()["path"] == "next.py"
    assert client.post(URL + "/workspace", json={"reviewed": [True, False]}).json()["path"] == "guide.txt"
    assert client.post(URL + "/workspace", json={"reviewed": [False], "findings": ["open", "resolved"]}).json()["path"] is None
    assert client.post(URL + "/workspace", json={"reviewed": [True], "findings": ["open", "resolved"]}).json()["path"] == "notes.md"
    assert client.post(URL + "/workspace", json={}).json()["file_states"] == data["file_states"]
    with transaction() as db:
        assert before == [(f.id, f.status) for f in db.scalars(select(Finding))]
        assert jobs == db.scalar(select(func.count()).select_from(Job))
    assert client.put(URL + "/progress", json=body(reviewed=True)).status_code in {404, 405}


@pytest.mark.parametrize("change", ["head", "base", "missing_coverage", "missing_outcomes", "overlap", "wrong_path", "malformed", "partial_file", "oversized"])
def test_review_check_never_infers_completion_from_status(comparison, client, change):
    seed_summary_and_findings()
    with transaction() as db:
        run = db.get(ReviewRun, "run")
        coverage = saved_coverage()
        if change in {"head", "base"}:
            setattr(run, change + "_sha", "e" * 40)
        elif change == "missing_coverage":
            coverage = None
        elif change == "missing_outcomes":
            coverage["completed"] = []
        elif change == "overlap":
            coverage["failed"] += coverage["completed"]
        elif change == "wrong_path":
            coverage["completed"][0] = {"item_id": "notes.md", "path": "guide.txt"}
        elif change == "malformed":
            coverage["completed"] = [None]
        elif change == "partial_file":
            item = {"item_id": "another-chunk", "path": "notes.md"}
            coverage["selected"].append(item)
            coverage["failed"].append(item)
        else:
            coverage["untrusted_extra"] = "x" * (code.MAX_METADATA_BYTES + 1)
        run.report = {"manifest": {"coverage": coverage}}
    data = client.post(URL + "/workspace", json={}).json()
    assert not any(state["reviewed"] for state in data["file_states"])


def test_review_checks_retain_saved_fact_but_never_expose_currently_excluded_files(comparison, client):
    seed_summary_and_findings()
    with transaction() as db:
        run = db.get(ReviewRun, "run")
        run.report = {"manifest": {"coverage": saved_coverage()}}
        run.config = {}
        db.get(Repository, 22).settings = {"exclusions": "guide.txt"}
    data = client.post(URL + "/workspace", json={}).json()
    assert data["file_states"][1]["reviewed"]
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "notes.md"}
    data = client.post(URL + "/workspace", json={}).json()
    assert "notes.md" not in [file["path"] for file in data["files"]]
    assert not any(state["reviewed"] for state in data["file_states"])
