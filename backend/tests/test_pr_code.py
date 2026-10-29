import copy
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select

from code_mortis.api_tokens import provenance as pat
from code_mortis.code_graph import service
from code_mortis.core.db import transaction
from code_mortis.integrations import comparison as github_comparison
from code_mortis.integrations import github
from code_mortis.models import (
        Finding,
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
    monkeypatch.setattr(github, "repository_read_token", lambda *a: "read-token")
    data = {"base_commit": {"sha": BASE}, "merge_base_commit": {"sha": MERGE}, "files": [
        {"filename": "notes.md", "status": "modified", "additions": 1, "deletions": 1, "patch": PATCH},
        {"filename": "next.py", "previous_filename": "old.py", "status": "renamed", "additions": 1, "deletions": 1, "patch": PATCH},
        {"filename": "image.png", "status": "added", "additions": 0, "deletions": 0},
        {"filename": ".env", "status": "modified", "patch": "+hidden"}]}
    calls = []
    def request(method, path, token, **kwargs):
        calls.append((path, kwargs))
        return copy.deepcopy(data)
    monkeypatch.setattr(github, "request", request)
    yield data, calls
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
    assert len(comparison[1]) == 1 and comparison[1][0][1]["max_response_bytes"] == code.MAX_RESPONSE_BYTES
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == jobs


@pytest.mark.parametrize("path,state", [("image.png", "unavailable"), ("next.py", "available")])
def test_missing_patch_and_rename(comparison, client, path, state):
    result = client.post(URL + "/file", json=body(path)).json()
    assert result["patch_state"] == state
    if path == "next.py":
        assert result["file"]["previous_path"] == "old.py"


def test_cache_reapplies_policy_before_paths_counts_groups(comparison, client):
    seed_summary_and_findings()
    assert client.get(URL).json()["total"] == 3
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "old.py,notes.md"}
    result = client.get(URL).json()
    assert result["total"] == 1 and not result["groups"] and "next.py" not in json.dumps(result)
    assert client.post(URL + "/file", json=body()).status_code == 404
    assert len(comparison[1]) == 1


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
    assert len(comparison[1]) == 1


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
    assert [s["open_count"] for s in data["file_states"]] == [0, 1, 1]
    assert all(s["complete"] for s in data["file_states"])
    with transaction() as db:
        db.get(Finding, "f1").status = "dismissed"
        db.get(Occurrence, "o4").snapshot = {**db.get(Occurrence, "o4").snapshot, "content": "provider-secret"}
    data = client.post(URL + "/workspace", json={"with_findings": True}).json()
    assert data["path"] == "next.py"
    assert data["file_states"][1] == {"reviewed": False, "open_count": 0, "complete": False}


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
    assert data["file_states"][2] == {"reviewed": False, "open_count": 1, "complete": False}



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
        run.report = {"manifest": {"coverage": saved_coverage(reused=("image.png",))}}
        before = [(f.id, f.status) for f in db.scalars(select(Finding))]
        jobs = db.scalar(select(func.count()).select_from(Job))
    data = client.post(URL + "/workspace", json={"unreviewed": True}).json()
    assert [state["reviewed"] for state in data["file_states"]] == [True, False, True]
    assert data["path"] == "next.py"
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
            coverage["completed"][0] = {"item_id": "notes.md", "path": "image.png"}
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
        db.get(Repository, 22).settings = {"exclusions": "image.png"}
    data = client.post(URL + "/workspace", json={}).json()
    assert data["file_states"][1]["reviewed"]
    with transaction() as db:
        db.get(Repository, 22).settings = {"exclusions": "notes.md"}
    data = client.post(URL + "/workspace", json={}).json()
    assert "notes.md" not in [file["path"] for file in data["files"]]
    assert not any(state["reviewed"] for state in data["file_states"])
