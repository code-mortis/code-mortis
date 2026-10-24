"""MCP grant provenance across queued jobs and follow-up work."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from code_mortis import github, jobs, mcp_provenance, reviews, security_issues
from code_mortis.db import now, transaction, uid
from code_mortis.mcp_models import McpClient, McpGrant
from code_mortis.models import (
    AppConfig,
    Finding,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityFinding,
    SecurityScan,
)


@pytest.fixture
def mcp_setup(seed):
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"reviews_enabled": True, "security_enabled": True}
        db.add(McpClient(client_id="test-client", client_name="Test", redirect_uris=["https://example.com/cb"]))
        db.flush()
        grants = {}
        for domain, scopes in (
            ("reviews", ["reviews:read", "reviews:run", "reviews:write"]),
            ("security", ["security:read", "security:run", "security:issues"]),
        ):
            grant = McpGrant(user_id=seed["user"].id, client_id="test-client", domain=domain,
                             scopes=scopes, repository_ids=[seed["repo"].id],
                             expires_at=now() + timedelta(days=1))
            db.add(grant)
            db.flush()
            grants[domain] = {"grant_id": grant.id, "domain": domain, "scope": scopes[1]}
        return grants


def saved_run(db, seed, status="queued", provenance=None):
    progress = {"mcp": provenance} if provenance else {}
    run = ReviewRun(id=uid(), pr_id=seed["pr"].id, trigger="automatic", dedup_key=uid(),
                    base_sha="b" * 40, head_sha="a" * 40, config={"connection": {}, "settings": {}},
                    status=status, publication="pending",
                    progress=mcp_provenance.attach_progress(progress))
    db.add(run)
    db.flush()
    return run


def test_context_binding_resets_without_leakage(seed, mcp_setup):
    assert mcp_provenance.current_provenance() is None
    with mcp_provenance.scoped(mcp_setup["reviews"]):
        with transaction() as db:
            job = reviews.enqueue(db, "publish", {"run_id": "r"})
            assert job.payload["mcp"] == mcp_setup["reviews"]
    assert mcp_provenance.current_provenance() is None
    with transaction() as db:
        plain = reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": 1})
        assert "mcp" not in plain.payload


def test_revoke_cancels_origin_only_and_preserves_results(seed, mcp_setup):
    with transaction() as db:
        origin = saved_run(db, seed, status="running", provenance=mcp_setup["reviews"])
        origin.report = {"manifest": {}}
        unrelated = saved_run(db, seed, status="running")
        with mcp_provenance.scoped(mcp_setup["reviews"]):
            origin_job = reviews.enqueue(db, "publish", {"run_id": origin.id})
        unrelated_job = reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": 3})
        db.flush()
        ids = (origin.id, unrelated.id, origin_job.id, unrelated_job.id)
    with transaction() as db:
        grant = db.scalar(select(McpGrant).where(McpGrant.id == mcp_setup["reviews"]["grant_id"]))
        grant.revoked_at = now()
        mcp_provenance.revoke_work(db, grant.id)
    with transaction() as db:
        origin, unrelated = db.get(ReviewRun, ids[0]), db.get(ReviewRun, ids[1])
        assert origin.cancel_requested and origin.status == "cancelled"
        assert origin.report == {"manifest": {}}
        assert unrelated.cancel_requested is False and unrelated.status == "running"
        assert db.get(Job, ids[2]).status == "failed"
        assert db.get(Job, ids[3]).status == "queued"


def test_running_origin_stops_and_unrelated_continues(seed, mcp_setup):
    with transaction() as db:
        origin = saved_run(db, seed, status="running", provenance=mcp_setup["reviews"])
        other = saved_run(db, seed, status="running")
        ids = (origin.id, other.id)
        assert mcp_provenance.run_revoked(db, origin) is False
    with transaction() as db:
        grant = db.scalar(select(McpGrant).where(McpGrant.id == mcp_setup["reviews"]["grant_id"]))
        grant.revoked_at = now()
    assert reviews.review_stopped(ids[0]) is True
    assert reviews.review_stopped(ids[1]) is False
    with transaction() as db:
        assert db.get(ReviewRun, ids[0]).cancel_requested is True


def test_execute_job_blocks_revoked_without_side_effects(seed, mcp_setup, monkeypatch):
    monkeypatch.setattr(jobs, "execute", lambda *a, **k: pytest.fail("revoked work must not execute"))
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("no GitHub after revocation"))
    with transaction() as db:
        run = saved_run(db, seed, status="queued", provenance=mcp_setup["reviews"])
        with mcp_provenance.scoped(mcp_setup["reviews"]):
            job = reviews.enqueue(db, "review", {"run_id": run.id})
        job_id, run_id = job.id, run.id
        grant = db.scalar(select(McpGrant).where(McpGrant.id == mcp_setup["reviews"]["grant_id"]))
        grant.revoked_at = now()
    jobs.execute_job(job_id)
    assert mcp_provenance.current_provenance() is None
    with transaction() as db:
        assert db.get(Job, job_id).status == "failed"
        assert db.get(ReviewRun, run_id).status == "cancelled"


def test_publish_blocked_after_revocation_without_github(seed, mcp_setup, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("no GitHub after revocation"))
    monkeypatch.setattr(github, "pages", lambda *a, **k: pytest.fail("no GitHub after revocation"))
    with transaction() as db:
        run = saved_run(db, seed, status="completed", provenance=mcp_setup["reviews"])
        run.publication_plan = [{"commit_id": "a" * 40, "event": "COMMENT", "comments": [], "body": "saved"}]
        run_id = run.id
        grant = db.scalar(select(McpGrant).where(McpGrant.id == mcp_setup["reviews"]["grant_id"]))
        grant.revoked_at = now()
    reviews.publish_run(run_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.review_ids == []
        assert run.publication in {"partially_published", "not_applicable"}


def test_security_revoke_cancels_scan_and_intents_preserves_unrelated(seed, mcp_setup, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("no GitHub after revocation"))
    with transaction() as db:
        scan = SecurityScan(id=uid(), repository_id=seed["repo"].id, actor_id=seed["user"].id,
                            connection_id=seed["connection"].id, request_key=uid(), branch="main",
                            head_sha="a" * 40, config={}, status="running",
                            progress={"mcp": mcp_setup["security"]})
        other_repo = Repository(id=23, installation_id=10, full_name="team/other", enabled=True)
        db.add(other_repo)
        db.flush()
        other = SecurityScan(id=uid(), repository_id=other_repo.id, actor_id=seed["user"].id,
                             connection_id=seed["connection"].id, request_key=uid(), branch="main",
                             head_sha="a" * 40, config={}, status="running", progress={})
        db.add_all([scan, other])
        db.flush()
        finding = Finding(id=uid(), pr_id=seed["pr"].id, fingerprint=uid(), path="a.py", content="c",
                          start_line=1, end_line=1, last_seen_run_id=saved_run(db, seed).id,
                          desired_status="resolved",
                          sync_intent={"finding_id": "x", "version": 0, "mcp": mcp_setup["reviews"]})
        db.add(finding)
        security_finding = SecurityFinding(id=uid(), repository_id=seed["repo"].id, fingerprint=uid(),
            state="open", kind="code", severity="high", title="t", path="a.py",
            first_scan_id=scan.id, last_scan_id=scan.id, evidence={}, version=0,
            issue={"operation_id": uid(), "mcp": mcp_setup["security"]})
        db.add(security_finding)
        db.flush()
        ids = (scan.id, other.id, finding.id, security_finding.id)
        for domain in ("reviews", "security"):
            grant = db.scalar(select(McpGrant).where(McpGrant.id == mcp_setup[domain]["grant_id"]))
            grant.revoked_at = now()
        mcp_provenance.revoke_work(db, mcp_setup["reviews"]["grant_id"])
        mcp_provenance.revoke_work(db, mcp_setup["security"]["grant_id"])
    with transaction() as db:
        assert db.get(SecurityScan, ids[0]).status == "cancelled"
        assert db.get(SecurityScan, ids[1]).status == "running"
        assert db.get(Finding, ids[2]).sync_error == mcp_provenance.REVOKED_MESSAGE
        assert db.get(SecurityFinding, ids[3]).issue["sync_status"] == "failed"
    with transaction() as db:
        row = db.get(SecurityFinding, ids[3])
        assert mcp_provenance.revoked(db, (row.issue or {}).get("mcp")) is True
    security_issues.sync(ids[3])
    with transaction() as db:
        assert db.get(SecurityFinding, ids[3]).issue["sync_status"] == "failed"


def test_followup_jobs_inherit_origin_provenance(seed, mcp_setup):
    with mcp_provenance.scoped(mcp_setup["reviews"]):
        with transaction() as db:
            run = saved_run(db, seed)
            assert run.progress["mcp"] == mcp_setup["reviews"]
            followup = reviews.enqueue(db, "publish", {"run_id": run.id})
            assert followup.payload["mcp"] == mcp_setup["reviews"]
            assert db.get(PullRequest, seed["pr"].id) is not None


@pytest.mark.parametrize("via_mcp", [False, True])
def test_explicit_issue_retry_replaces_previous_authorization(seed, mcp_setup, via_mcp):
    previous = {**mcp_setup["security"], "grant_id": "previous-revoked-grant"}
    current = mcp_setup["security"] if via_mcp else None
    with transaction() as db:
        scan = SecurityScan(repository_id=22, actor_id=7, connection_id="connection", request_key=uid(),
            branch="main", head_sha="a" * 40, config={})
        db.add(scan)
        db.flush()
        finding = SecurityFinding(repository_id=22, fingerprint=uid(), kind="code", severity="high",
            title="Finding", path="a.py", first_scan_id=scan.id, last_scan_id=scan.id, evidence={},
            issue={"operation_id": uid(), "mcp": previous, "sync_status": "failed"})
        db.add(finding)
        db.flush()
        with mcp_provenance.scoped(current):
            security_issues.retry(db, finding, seed["user"])
        assert finding.issue.get("mcp") == current
        job = db.scalar(select(Job).where(Job.kind == "security_issue_sync"))
        assert job.payload.get("mcp") == current
        assert finding.issue["sync_status"] == "pending"
