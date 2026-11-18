"""Bounded coexistence: real indexing/Git, mocked scanner and inference; no provider calls."""

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import httpx
import pytest
from test_code_graph_review import related as related
from test_code_graph_workflow import graph as graph  # noqa: F401
from test_code_graph_workflow import run as graph_run
from test_pr_engine import Ledger, final, response

from code_mortis.code_graph import client, service
from code_mortis.core import process
from code_mortis.core.db import transaction
from code_mortis.integrations import llm
from code_mortis.models import CodeGraphSnapshot, Connection, Repository, User
from code_mortis.reviews import engine as pr_engine
from code_mortis.reviews import graph_context, planning
from code_mortis.schemas.common import RepoSettings
from code_mortis.security_scans import engine as security_engine


def test_indexing_pr_and_security_overlap_without_crossing_context_policies(related, tmp_path, monkeypatch):
    indexing, release_index = Event(), Event()
    inference_overlap = Barrier(2)
    parse = client.extract
    pr_requests, security_requests, scanner_inventories, state = [], [], [], {}
    options = RepoSettings(concurrency=1, exclusions="skip.py", token_budget=200000).model_dump()
    connection = {"id": "connection", "kind": "api", "model": "fixture", "protocol": "openai", "options": {}}
    credentials = {"api_key": "private-fixture-value"}
    with transaction() as db:
        db.get(Connection, "connection").kind = "api"
        graph_options = graph_context.configuration(db, db.get(Repository, 22), db.get(Connection, "connection"),
            db.get(User, 7), engine={"analysis_engine": "mortis"})
    indexing_id = service.enqueue_snapshot(22, commit_sha=related["first"])

    def paused_parser(payload, stopped):
        indexing.set()
        assert release_index.wait(20), "The bounded coexistence probe did not release its parser"
        return parse(payload, stopped)

    async def execute(*args, **kwargs):
        supplied = json.loads(args[3].messages[0].content)
        if not pr_requests:
            assert indexing.is_set() and not release_index.is_set()
            inference_overlap.wait(timeout=10)
        pr_requests.append(supplied)
        return await state["ledger"].execute(*args, **kwargs)

    def scanner(item, *args, **kwargs):
        scanner_inventories.append(set(item.inventory))
        return {"findings": [], "targets": {}, "problems": [], "metadata": {"database_fresh": True}}

    def pr_work():
        plan = planning.plan_review(related["source"], {}, related["first"], related["head"], related["first"], options, lambda: False)
        selected, supporting = graph_context.prepare(graph_options, "a.py", related["head"], related["source"], {}, options,
            credentials, lambda: False, remaining=graph_context.MAX_RUN_BYTES, base_sha=related["first"], trusted_rules=plan.rules)
        assert selected["reason"] == "used" and supporting["snapshot_id"] == related["snapshot"]
        batches = pr_engine.prepare_batches(plan, connection, options, credentials, graph_by_path={"a.py": supporting})
        ledger = state["ledger"] = Ledger(batches, [response(final(batch)) for batch in batches])
        result = pr_engine.review(related["source"], {}, plan, batches, job_id="coexistence-pr", job_attempt=1,
            credentials=credentials, authorize=lambda: None, register_task=ledger.register, load_task=ledger.rows.get)
        return result, plan

    def security_work():
        root = tmp_path / "security"
        root.mkdir()
        item = security_engine.Snapshot(root, related["head"], process.child_environment(), lambda: False)
        shutil.copytree(related["source"], item.git)
        item.secret_values.append(credentials["api_key"])
        item.populate()
        scanner_result = security_engine.trivy(item, 20, lambda: False)

        def infer(supplied, *, validator, **kwargs):
            assert indexing.is_set() and not release_index.is_set()
            security_requests.append(supplied)
            inference_overlap.wait(timeout=10)
            return validator({"findings": [], "required_paths": []}, supplied), {"tokens": 7}

        result = security_engine.ask(item, connection, credentials, "analysis", {
            "assigned_path": "a.py", "task_contract": security_engine.TASK_CONTRACT,
            "sources": {"current": {"a.py": item.source("a.py")}}, "diff": "",
        }, 200000, 20, lambda: False, "coexistence-security", infer=infer)
        return scanner_result, result

    monkeypatch.setattr(client, "extract", paused_parser)
    monkeypatch.setattr(llm, "execute_task", execute)
    monkeypatch.setattr(llm, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: pytest.fail("Unexpected provider HTTP request")), trust_env=False))
    monkeypatch.setattr(security_engine, "trivy", scanner)
    with ThreadPoolExecutor(max_workers=3) as workers:
        index_future = workers.submit(graph_run, indexing_id)
        try:
            assert indexing.wait(10), "Index extraction never started"
            pr_future, security_future = workers.submit(pr_work), workers.submit(security_work)
            (status, report, comments), plan = pr_future.result(timeout=15)
            scanner_result, (security_result, usage) = security_future.result(timeout=15)
            assert not index_future.done()
            with transaction() as db:
                assert db.get(CodeGraphSnapshot, indexing_id).status == "running"
                assert db.get(CodeGraphSnapshot, related["snapshot"]).status == "ready"
        finally:
            release_index.set()
        indexed, job = index_future.result(timeout=15)
    assert indexed.status == "ready" and job.status == "done"
    assert status == "completed" and not comments
    assert len(report["manifest"]["coverage"]["completed"]) == len(plan.selected)
    assigned = next(unit for payload in pr_requests for unit in payload["assigned_units"] if unit["path"] == "a.py")
    assert assigned["source"] == planning.source.read_source(related["source"], {}, related["head"], "a.py", lambda: False)
    graph_payload = next(payload["code_graph"]["a.py"] for payload in pr_requests if "a.py" in payload.get("code_graph", {}))
    assert graph_payload["commit_sha"] == related["head"] and graph_payload["sources"]
    assert all("skip.py" not in {unit["path"] for unit in payload["assigned_units"]} for payload in pr_requests)
    assert scanner_inventories and "skip.py" in scanner_inventories[0]
    assert scanner_result["metadata"]["database_fresh"] and security_result["findings"] == [] and usage == {"tokens": 7}
    assert security_requests and all("code_graph" not in payload for payload in security_requests)
    assert credentials["api_key"] not in json.dumps([pr_requests, security_requests])
