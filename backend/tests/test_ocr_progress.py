import json
import os
import sys
from pathlib import Path

import pytest
from test_review_flow import new_run, remote

from code_mortis.integrations import github, ocr
from code_mortis.reviews import service as reviews


@pytest.fixture
def live_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = tmp_path / "repo"
    folder = tmp_path / ".opencodereview/sessions" / str(repo).lstrip("/").replace("/", "-")
    folder.mkdir(parents=True)
    session = "00000000-0000-4000-8000-000000000001"
    header = {"type": "session_start", "sessionId": session, "cwd": str(repo),
              "reviewMode": "range", "diffFrom": "a" * 40, "diffTo": "b" * 40}
    events = []
    poll = ocr.review_progress(repo, "a" * 40, "b" * 40, ["a.py", "b.py", "c.py"], events.append)
    path = folder / f"{session}.jsonl"
    path.write_text(json.dumps(header) + "\n")

    def write(kind="review_item_done", file="a.py", **values):
        row = {"type": kind, "sessionId": session, "filePath": file, "fingerprint": "c" * 64, **values}
        with path.open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    return poll, events, path, header, write


def test_progress_counts_only_complete_checkpoints_and_deduplicates_files(live_progress):
    poll, events, path, header, write = live_progress
    assert events == [{"selected": 3, "completed": 0, "reused": 0, "failed": 0}]
    write("llm_response", tool_calls=[{"name": "task_done", "arguments": '{"state":"DONE"}'}])
    poll(force=True)
    assert len(events) == 1
    row = json.dumps({"type": "review_item_done", "sessionId": header["sessionId"],
                      "filePath": "a.py", "fingerprint": "c" * 64})
    with path.open("a") as stream:
        stream.write(row[:30])
    poll(force=True)
    assert len(events) == 1
    with path.open("a") as stream:
        stream.write(row[30:] + "\n")
    poll(force=True)
    assert events[-1]["completed"] == 1
    write()
    poll(force=True)
    assert len(events) == 2
    write("review_item_failed", "b.py", error="provider-secret")
    write("review_item_reused", "c.py")
    poll(force=True)
    assert events[-1] == {"selected": 3, "completed": 1, "reused": 1, "failed": 1}
    assert "provider-secret" not in json.dumps(events)
    write("review_item_failed")
    write()
    poll(force=True)
    assert events[-1] == {"selected": 3, "completed": 0, "reused": 1, "failed": 2}


@pytest.mark.parametrize("field,value", [("cwd", "/another/repo"), ("diffTo", "c" * 40),
                                         ("diffFrom", "c" * 40), ("reviewMode", "full_scan"),
                                         ("sessionId", "another-session")])
def test_progress_rejects_unrelated_sessions(live_progress, field, value):
    poll, events, path, header, write = live_progress
    path.write_text(json.dumps({**header, field: value}) + "\n")
    write()
    poll(force=True)
    assert len(events) == 1


def test_progress_ignores_out_of_scope_and_malformed_records(live_progress, monkeypatch):
    poll, events, path, _, write = live_progress
    write(file="../secret")
    write(file="outside.py")
    write(file=["a.py"])
    write(sessionId="another-session")
    write(fingerprint="invalid")
    write(kind=["review_item_done"])
    poll(force=True)
    assert len(events) == 1
    with path.open("a") as stream:
        stream.write('{"type":\n')
    poll(force=True)
    write()
    poll(force=True)
    assert len(events) == 1
    monkeypatch.setattr(ocr, "MAX_OUTPUT", 10)
    path.with_name("large.jsonl").write_text("x" * 11)
    poll(force=True)
    assert len(events) == 1


def test_progress_is_published_before_subprocess_finishes(live_progress, tmp_path):
    poll, events, path, header, _ = live_progress
    checkpoint = json.dumps({"type": "review_item_done", "sessionId": header["sessionId"],
                             "filePath": "a.py", "fingerprint": "c" * 64}) + "\n"
    signal = tmp_path / "observed"

    def observe():
        poll(force=True)
        if events[-1]["completed"] == 1:
            signal.touch()

    script = """
import pathlib, sys, time
with pathlib.Path(sys.argv[1]).open('a') as stream:
    stream.write(sys.argv[2])
while not pathlib.Path(sys.argv[3]).exists():
    time.sleep(0.02)
print('final report')
"""
    code, output, _ = ocr.command([sys.executable, "-c", script, str(path), checkpoint, str(signal)],
                                  tmp_path, os.environ.copy(), timeout=5, poll=observe)
    assert code == 0 and output == "final report\n"
    assert events[-1]["completed"] == 1


def test_progress_can_recover_checkpoints_from_the_same_execution(live_progress):
    _, _, _, header, write = live_progress
    write()
    events = []
    poll = ocr.review_progress(Path(header["cwd"]), header["diffFrom"], header["diffTo"],
                              ["a.py", "b.py", "c.py"], events.append)
    poll(force=True)
    assert events[-1] == {"selected": 3, "completed": 1, "reused": 0, "failed": 0}


@pytest.mark.parametrize("parallel", [False, True])
def test_live_counts_reach_run_api_before_report_is_saved(seed, github_stub, client, monkeypatch, parallel):
    monkeypatch.setattr(github, "request", lambda *a, **k: remote())
    identity = new_run(parallel=parallel)

    def review(*args, progress, **kwargs):
        for completed in (0, 1, 2):
            value = {"selected": 3, "completed": completed, "reused": 0, "failed": 0}
            progress(value)
            response = client.get(f"/api/v1/runs/{identity}")
            assert response.status_code == 200
            run = response.json()["run"]
            assert run["status"] == "running" and run["report"] is None
            assert {key: run["progress"][key] for key in value} == value
        return "completed", {"message": "final report"}, []

    monkeypatch.setattr(ocr, "review", review)
    reviews.perform_review(identity)
    assert client.get(f"/api/v1/runs/{identity}").json()["run"]["report"] == {"message": "final report"}


@pytest.mark.parametrize("failure", ["callback", "filesystem"])
def test_progress_failures_do_not_interrupt_ocr_and_can_recover(live_progress, tmp_path, monkeypatch, failure):
    _, _, _, header, write = live_progress
    delivered, attempts = [], []
    failing = True
    original_glob = Path.glob

    def callback(value):
        attempts.append(value)
        if failing and failure == "callback":
            raise RuntimeError("Progress transaction unavailable")
        delivered.append(value)

    def glob(path, *args, **kwargs):
        if failing and failure == "filesystem":
            attempts.append(None)
            raise OSError("Checkpoint directory unavailable")
        return original_glob(path, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", glob)
    poll = ocr.review_progress(Path(header["cwd"]), header["diffFrom"], header["diffTo"], ["a.py"], callback)
    write()
    code, output, _ = ocr.command([sys.executable, "-c", "import time; time.sleep(0.3); print('final report')"],
                                 tmp_path, os.environ.copy(), timeout=5, poll=lambda: poll(force=True))
    poll(force=True)
    assert attempts and code == 0 and output == "final report\n"
    failing = False
    poll(force=True)
    assert delivered[-1] == {"selected": 1, "completed": 1, "reused": 0, "failed": 0}
