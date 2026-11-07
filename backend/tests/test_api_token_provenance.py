"""Personal API token origin across queued work and revocation checkpoints."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_review_flow import remote

from code_mortis import jobs
from code_mortis.api_tokens import provenance as pat_provenance
from code_mortis.api_tokens import service as pat_service
from code_mortis.core.db import now, transaction, uid
from code_mortis.core.security import digest
from code_mortis.integrations import github, ocr
from code_mortis.mcp import provenance as mcp_provenance
from code_mortis.models import (
    Finding,
    Job,
    PersonalApiToken,
    PullRequest,
    Repository,
    ReviewRun,
    SecurityFinding,
    SecurityScan,
)
from code_mortis.reviews import checks as review_checks
from code_mortis.reviews import service as reviews
from code_mortis.security_scans import issues as security_issues


def make_token(db, seed, scopes, repository_ids, **overrides):
    token = PersonalApiToken(
        user_id=seed["user"].id,
        name="noodle",
        token_hash=digest(f"raw-{uid()}"),
        prefix="cm_pat_test",
        scopes=list(scopes),
        repository_ids=list(repository_ids),
        expires_at=now() + timedelta(days=30),
        **overrides,
    )
    db.add(token)
    db.flush()
    return token


@pytest.fixture
def pat_setup(seed):
    with transaction() as db:
        token = make_token(db, seed, ["reviews:read", "reviews:run", "reviews:write"],
                           [seed["repo"].id])
        record = {"token_id": token.id, "user_id": seed["user"].id,
                  "scope": "reviews:run", "repository_id": seed["repo"].id}
        return {"token": token, "record": record}


def saved_run(db, seed, status="queued", provenance=None):
    progress = {"pat": provenance} if provenance else {}
    run = ReviewRun(id=uid(), pr_id=seed["pr"].id, trigger="automatic", dedup_key=uid(),
                    base_sha="b" * 40, head_sha="a" * 40, config={"connection": {"id": "connection", "kind": "api"}, "settings": {}},
                    status=status, publication="pending",
                    progress=pat_provenance.attach_progress(progress))
    db.add(run)
    db.flush()
    return run


def github_allows(monkeypatch):
    monkeypatch.setattr(github, "authorize_repo", lambda *args, **kwargs: {"id": 22})
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: pytest.fail("no GitHub after revocation"))


def test_review_poll_checks_token_without_github_requests(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: pytest.fail("polling must not refresh GitHub"))
    with transaction() as db:
        run = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        run_id = run.id
    assert reviews.review_stopped(run_id) is False
    with transaction() as db:
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    assert reviews.review_stopped(run_id) is True
    monkeypatch.setattr(github, "pages", lambda *args, **kwargs: pytest.fail("no GitHub after revocation"))


def test_context_binding_resets_without_leakage(seed, pat_setup):
    assert pat_provenance.current_provenance() is None
    assert mcp_provenance.current_provenance() is None
    with pat_provenance.scoped(pat_setup["record"]):
        with transaction() as db:
            job = reviews.enqueue(db, "publish", {"run_id": "r"})
            assert job.payload["pat"] == pat_setup["record"]
            assert "mcp" not in job.payload
    assert pat_provenance.current_provenance() is None
    with transaction() as db:
        plain = reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": 1})
        assert "pat" not in plain.payload
    # MCP context never implies PAT origin and vice versa.
    with mcp_provenance.scoped({"grant_id": "g", "domain": "reviews", "scope": "reviews:run"}):
        with transaction() as db:
            job = reviews.enqueue(db, "publish", {"run_id": "r"})
            assert "pat" not in job.payload


def test_revoke_cancels_origin_only_and_preserves_results(seed, pat_setup):
    with transaction() as db:
        origin = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        origin.report = {"manifest": {}}
        unrelated = saved_run(db, seed, status="running")
        with pat_provenance.scoped(pat_setup["record"]):
            origin_job = reviews.enqueue(db, "publish", {"run_id": origin.id})
        unrelated_job = reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": 3})
        db.flush()
        ids = (origin.id, unrelated.id, origin_job.id, unrelated_job.id)
    with transaction() as db:
        token = db.get(PersonalApiToken, pat_setup["token"].id)
        token.revoked_at = now()
        pat_provenance.revoke_work(db, token.id)
    with transaction() as db:
        origin, unrelated = db.get(ReviewRun, ids[0]), db.get(ReviewRun, ids[1])
        assert origin.cancel_requested and origin.status == "cancelled"
        assert origin.report == {"manifest": {}}
        assert unrelated.cancel_requested is False and unrelated.status == "running"
        assert db.get(Job, ids[2]).status == "failed"
        assert db.get(Job, ids[3]).status == "queued"


def test_sibling_mcp_work_survives_pat_revoke(seed, pat_setup, mcp_grant):
    with transaction() as db:
        pat_run = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        mcp_run = saved_run(db, seed, status="running")
        mcp_run.progress = {**mcp_run.progress, "mcp": mcp_grant}
        ids = (pat_run.id, mcp_run.id)
        token = db.get(PersonalApiToken, pat_setup["token"].id)
        token.revoked_at = now()
        pat_provenance.revoke_work(db, token.id)
    with transaction() as db:
        assert db.get(ReviewRun, ids[0]).status == "cancelled"
        assert db.get(ReviewRun, ids[1]).status == "running"


def test_running_origin_stops_and_unrelated_continues(seed, pat_setup, monkeypatch):
    github_allows(monkeypatch)
    with transaction() as db:
        origin = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        other = saved_run(db, seed, status="running")
        ids = (origin.id, other.id)
        assert pat_provenance.run_revoked(db, origin) is False
    with transaction() as db:
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    assert reviews.review_stopped(ids[0]) is True
    assert reviews.review_stopped(ids[1]) is False
    with transaction() as db:
        assert db.get(ReviewRun, ids[0]).cancel_requested is True


def test_expired_token_denies_without_network(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: pytest.fail("expired token must not reach GitHub"))
    with transaction() as db:
        origin = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        run_id = origin.id
        db.get(PersonalApiToken, pat_setup["token"].id).expires_at = now() - timedelta(seconds=1)
    assert reviews.review_stopped(run_id) is True


def test_scope_snapshot_is_not_authority(seed, pat_setup, monkeypatch):
    github_allows(monkeypatch)
    with transaction() as db:
        origin = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        run_id = origin.id
        assert pat_provenance.run_revoked(db, origin) is False
        db.get(PersonalApiToken, pat_setup["token"].id).scopes = ["reviews:read"]
    with transaction() as db:
        assert pat_provenance.run_revoked(db, db.get(ReviewRun, run_id)) is True


def test_malformed_origin_denies_not_system(seed, pat_setup, monkeypatch):
    github_allows(monkeypatch)
    with transaction() as db:
        run = saved_run(db, seed, status="running")
        run.progress = {**run.progress, "pat": {"token_id": 123}}
        payload = {"run_id": run.id, "pat": {"user_id": seed["user"].id}}
        assert pat_provenance.run_revoked(db, run) is True
        assert pat_provenance.revoked(db, {"user_id": 1}, repository_id=seed["repo"].id) is True
        # A tampered payload marker cannot fall back to a valid stored origin.
        run.progress = {**run.progress, "pat": pat_setup["record"]}
        assert pat_provenance.revoked(db, pat_provenance.origin_for(payload, run)) is True


def test_execute_job_blocks_revoked_without_side_effects(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(jobs, "execute", lambda *a, **k: pytest.fail("revoked work must not execute"))
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("no GitHub after revocation"))
    with transaction() as db:
        run = saved_run(db, seed, status="queued", provenance=pat_setup["record"])
        with pat_provenance.scoped(pat_setup["record"]):
            job = reviews.enqueue(db, "review", {"run_id": run.id})
        job_id, run_id = job.id, run.id
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    jobs.execute_job(job_id)
    assert pat_provenance.current_provenance() is None
    with transaction() as db:
        assert db.get(Job, job_id).status == "failed"
        assert db.get(ReviewRun, run_id).status == "cancelled"


def test_publish_blocked_after_revocation_without_github(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("no GitHub after revocation"))
    monkeypatch.setattr(github, "pages", lambda *a, **k: pytest.fail("no GitHub after revocation"))
    with transaction() as db:
        run = saved_run(db, seed, status="completed", provenance=pat_setup["record"])
        run.publication_plan = [{"commit_id": "a" * 40, "event": "COMMENT", "comments": [], "body": "saved"}]
        run_id = run.id
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    reviews.publish_run(run_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.review_ids == []
        assert run.publication in {"partially_published", "not_applicable"}


def test_cleanup_job_runs_despite_dead_origin_and_is_idempotent(seed, pat_setup, monkeypatch):
    github_allows(monkeypatch)
    with transaction() as db:
        run = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        run.report = {"manifest": {}}
        stale = dict(pat_setup["record"])
        job = reviews.enqueue(db, "revoke_api_token_work", {"token_id": pat_setup["token"].id})
        job.payload = {**job.payload, "pat": stale}
        job_id, run_id = job.id, run.id
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    # Internal cleanup is exempt from admission gates even though its own
    # payload carries the now-dead origin.
    jobs.execute_job(job_id)
    with transaction() as db:
        assert db.get(Job, job_id).status == "done"
        assert db.get(ReviewRun, run_id).status == "cancelled"
        assert db.get(ReviewRun, run_id).report == {"manifest": {}}
        counts = pat_provenance.revoke_work(db, pat_setup["token"].id)
        assert counts == {"runs": 0, "scans": 0, "jobs": 0, "findings": 0, "issues": 0}


def test_explicit_retry_replaces_origin_background_preserves(seed, pat_setup):
    replacement = {**pat_setup["record"], "scope": "reviews:write"}
    with transaction() as db:
        finding = Finding(id=uid(), pr_id=seed["pr"].id, fingerprint=uid(), path="a.py", content="c",
                          start_line=1, end_line=1, last_seen_run_id=saved_run(db, seed).id,
                          desired_status="resolved",
                          sync_intent={"finding_id": "x", "version": 0, "pat": pat_setup["record"]})
        db.add(finding)
        db.flush()
        with pat_provenance.scoped(replacement):
            finding.sync_intent = mcp_provenance.bind_intent(
                {**finding.sync_intent, "sync_status": "pending"})
        assert finding.sync_intent["pat"] == replacement
        # Background retry with no bound context keeps the stored origin.
        with pat_provenance.scoped(None):
            preserved = mcp_provenance.bind_intent({**finding.sync_intent, "sync_status": "pending"})
        assert preserved["pat"] == replacement


def test_automatic_descendants_retain_pat_origin(seed, pat_setup):
    with pat_provenance.scoped(pat_setup["record"]):
        with transaction() as db:
            pr = db.get(PullRequest, seed["pr"].id)
            repo = db.get(Repository, seed["repo"].id)
            run = reviews.create_run(db, pr, repo, "automatic")
            assert run.progress["pat"] == pat_setup["record"]
            child = reviews.enqueue(db, "publish", {"run_id": run.id})
            assert child.payload["pat"] == pat_setup["record"]
            review_checks.enqueue_sync(db, run)
            check_job = db.scalar(select(Job).where(
                Job.kind == "sync_check", Job.status == "queued",
                Job.payload["run_id"].as_string() == run.id))
            assert check_job.payload["pat"] == pat_setup["record"]


def test_security_revoke_cancels_scan_and_intents_preserves_unrelated(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(github, "request", lambda *args, **kwargs: pytest.fail("no GitHub after revocation"))
    with transaction() as db:
        scan = SecurityScan(id=uid(), repository_id=seed["repo"].id, actor_id=seed["user"].id,
                            connection_id=seed["connection"].id, request_key=uid(), branch="main",
                            head_sha="a" * 40, config={}, status="running",
                            progress={"pat": pat_setup["record"]})
        other_repo = Repository(id=23, installation_id=10, full_name="team/other", enabled=True)
        db.add(other_repo)
        db.flush()
        other = SecurityScan(id=uid(), repository_id=other_repo.id, actor_id=seed["user"].id,
                             connection_id=seed["connection"].id, request_key=uid(), branch="main",
                             head_sha="a" * 40, config={}, status="running", progress={})
        db.add_all([scan, other])
        db.flush()
        security_finding = SecurityFinding(id=uid(), repository_id=seed["repo"].id, fingerprint=uid(),
            state="open", kind="code", severity="high", title="t", path="a.py",
            first_scan_id=scan.id, last_scan_id=scan.id, evidence={}, version=0,
            issue={"operation_id": uid(), "pat": pat_setup["record"]})
        db.add(security_finding)
        db.flush()
        ids = (scan.id, other.id, security_finding.id)
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
        pat_provenance.revoke_work(db, pat_setup["token"].id)
    with transaction() as db:
        assert db.get(SecurityScan, ids[0]).status == "cancelled"
        assert db.get(SecurityScan, ids[1]).status == "running"
        assert db.get(SecurityFinding, ids[2]).issue["sync_status"] == "failed"
    with transaction() as db:
        row = db.get(SecurityFinding, ids[2])
        assert pat_provenance.revoked(db, (row.issue or {}).get("pat")) is True
    security_issues.sync(ids[2])
    with transaction() as db:
        assert db.get(SecurityFinding, ids[2]).issue["sync_status"] == "failed"


def test_settle_blocked_cancels_dead_origin_only(seed, pat_setup, monkeypatch):
    github_allows(monkeypatch)
    with transaction() as db:
        dead = saved_run(db, seed, status="blocked", provenance=pat_setup["record"])
        live = saved_run(db, seed, status="blocked")
        live_token = make_token(db, seed, ["reviews:run"], [seed["repo"].id])
        live.progress = {**live.progress, "pat": {"token_id": live_token.id,
                                                  "user_id": seed["user"].id, "scope": "reviews:run"}}
        ids = (dead.id, live.id)
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
        counts = pat_provenance.settle_blocked(db)
        assert counts == {"runs": 1, "scans": 0}
    with transaction() as db:
        assert db.get(ReviewRun, ids[0]).status == "cancelled"
        assert db.get(ReviewRun, ids[1]).status == "blocked"


@pytest.fixture
def mcp_grant(seed):
    from code_mortis.mcp.models import McpClient, McpGrant

    with transaction() as db:
        db.add(McpClient(client_id="test-client", client_name="Test",
                         redirect_uris=["https://example.com/cb"]))
        db.flush()
        grant = McpGrant(user_id=seed["user"].id, client_id="test-client", domain="reviews",
                         scopes=["reviews:read", "reviews:run"], repository_ids=[seed["repo"].id],
                         expires_at=now() + timedelta(days=1))
        db.add(grant)
        db.flush()
        return {"grant_id": grant.id, "domain": "reviews", "scope": "reviews:run"}


def test_present_null_denies_and_absent_allows(seed, pat_setup):
    with transaction() as db:
        run = saved_run(db, seed, status="running")
        assert pat_provenance.run_revoked(db, run) is False
        run.progress = {**run.progress, "pat": None}
        assert pat_provenance.run_revoked(db, run) is True
        job = reviews.enqueue(db, "review", {"run_id": run.id})
        job.payload = {**job.payload, "pat": None}
        assert pat_provenance.job_revoked(db, job) is True
        assert pat_provenance.revoked(db, None) is True
        assert pat_provenance.revoked(db, pat_provenance._ABSENT) is False
        assert pat_provenance.origin_for({}, {}) is pat_provenance._ABSENT
        assert pat_provenance.mapping_revoked(db, {}) is False
        assert pat_provenance.mapping_revoked(db, {"pat": None}) is True


def test_scoped_malformed_raises_and_scope_owner_strict(seed, pat_setup, monkeypatch):
    with pytest.raises(ValueError):
        with pat_provenance.scoped({"token_id": "", "user_id": 1}):
            pass
    with pytest.raises(ValueError):
        with pat_provenance.scoped({"token_id": "t", "user_id": True, "scope": "reviews:run"}):
            pass
    github_allows(monkeypatch)
    with transaction() as db:
        run = saved_run(db, seed, status="running")
        # Scope-less record (auth-token style) binds but cannot authorize paid work.
        run.progress = {**run.progress, "pat": {"token_id": pat_setup["token"].id,
                                                "user_id": seed["user"].id}}
        assert pat_provenance.run_revoked(db, run, repository_id=seed["repo"].id) is True
        # Unknown scope and owner mismatch deny.
        assert pat_provenance.revoked(db, {"token_id": pat_setup["token"].id, "user_id": seed["user"].id,
                                           "scope": "admin:all"}) is True
        assert pat_provenance.revoked(db, {"token_id": pat_setup["token"].id, "user_id": 999,
                                           "scope": "reviews:run"}) is True


def test_write_inference_for_run_scopes(seed, pat_setup, monkeypatch):
    captured = {}

    def fake_authorize(db, token_id, *, scope=None, repository_id=None, write=False, lock=False):
        captured.update(scope=scope, repository_id=repository_id, write=write)
        from types import SimpleNamespace

        return SimpleNamespace(id=1), SimpleNamespace(id=token_id, user_id=seed["user"].id)

    monkeypatch.setattr(pat_service, "authorize", fake_authorize)
    record = {"token_id": "t", "user_id": seed["user"].id, "scope": "reviews:run"}
    with transaction() as db:
        assert pat_provenance.revoked(db, record, repository_id=seed["repo"].id) is False
        assert captured["write"] is True and captured["repository_id"] == seed["repo"].id
        read_record = {"token_id": "t", "user_id": seed["user"].id, "scope": "repositories:read"}
        assert pat_provenance.revoked(db, read_record, repository_id=seed["repo"].id) is False
        assert captured["write"] is False
        assert pat_provenance.revoked(db, record, repository_id=seed["repo"].id, write=False) is False
        assert captured["write"] is False


def test_job_kind_rejects_read_scope_for_review_work(seed, pat_setup, monkeypatch):
    github_allows(monkeypatch)
    with transaction() as db:
        read_token = make_token(db, seed, ["repositories:read"], [seed["repo"].id])
        read_record = {"token_id": read_token.id, "user_id": seed["user"].id,
                       "scope": "repositories:read", "repository_id": seed["repo"].id}
        run = saved_run(db, seed, status="running")
        job = reviews.enqueue(db, "review", {"run_id": run.id})
        job.payload = {**job.payload, "pat": read_record}
        assert pat_provenance.job_revoked(db, job) is True
        run_token = make_token(db, seed, ["reviews:run"], [seed["repo"].id])
        run_record = {"token_id": run_token.id, "user_id": seed["user"].id,
                      "scope": "reviews:run", "repository_id": seed["repo"].id}
        job.payload = {**job.payload, "pat": run_record}
        assert pat_provenance.job_revoked(db, job) is False


def test_explicit_adoption_replaces_and_session_clears(seed, pat_setup):
    dead = {"token_id": "dead", "user_id": seed["user"].id, "scope": "reviews:run"}
    live = {"token_id": pat_setup["token"].id, "user_id": seed["user"].id, "scope": "reviews:write"}
    with pat_provenance.scoped(live):
        assert pat_provenance.adopt_intent({"pat": dead, "version": 1}) == {"version": 1, "pat": live}
        assert pat_provenance.adopt_progress({"pat": dead}) == {"pat": live}
    with pat_provenance.scoped(None):
        assert pat_provenance.adopt_intent({"pat": dead, "version": 1}) == {"version": 1}
        assert pat_provenance.adopt_progress({"pat": dead}) == {}
    # An explicit session retry of dead PAT work clears the origin so gates pass as system work.
    with transaction() as db:
        run = saved_run(db, seed, status="blocked", provenance=dead)
        assert pat_provenance.run_revoked(db, run) is True
        with pat_provenance.scoped(None):
            run.progress = pat_provenance.adopt_progress(run.progress)
        assert "pat" not in run.progress
        assert pat_provenance.run_revoked(db, run) is False


@pytest.mark.parametrize("worker", [False, True])
def test_sync_pr_descendant_sets_owner_actor(seed, pat_setup, monkeypatch, worker):
    monkeypatch.setattr(github, "installation_token", lambda *a, **k: "token")
    monkeypatch.setattr(github, "authorize_repo", lambda *a, **k: {"id": seed["repo"].id})
    pr_data = {"title": "t", "user": {"login": "owner", "type": "User"}, "html_url": "u",
               "state": "open", "draft": False, "number": seed["pr"].number,
               "head": {"sha": "a" * 40, "ref": "fix/retries", "repo": {"id": seed["repo"].id}},
               "base": {"sha": "b" * 40}}
    monkeypatch.setattr(github, "request", lambda *a, **k: pr_data)
    monkeypatch.setattr(github, "threads", lambda *a, **k: [])
    create = jobs.create_run
    def create_with_owner(db, pr, repo, trigger, actor):
        assert actor.api_token_id == pat_setup["token"].id
        assert actor.api_token_repository_ids == frozenset({seed["repo"].id})
        return create(db, pr, repo, trigger, actor=actor)
    monkeypatch.setattr(jobs, "create_run", create_with_owner)
    with pat_provenance.scoped(pat_setup["record"]):
        if worker:
            with transaction() as db:
                job_id = reviews.enqueue(db, "sync_pr", {"repository_id": seed["repo"].id,
                                                         "number": seed["pr"].number}).id
        else:
            jobs.sync_pr(seed["repo"].id, seed["pr"].number)
    if worker:
        jobs.execute_job(job_id)
    with transaction() as db:
        if worker:
            assert db.get(Job, job_id).status == "done", db.get(Job, job_id).error
        run = db.scalars(select(ReviewRun).where(
            ReviewRun.pr_id == seed["pr"].id, ReviewRun.actor_id == seed["user"].id
        ).order_by(ReviewRun.created_at.desc()).limit(1)).one_or_none()
        assert run is not None
        assert run.progress["pat"] == pat_setup["record"]
    with transaction() as db:
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    with pat_provenance.scoped(pat_setup["record"]):
        with pytest.raises(ocr.ReviewStopped, match="revoked or expired"):
            jobs.sync_pr(seed["repo"].id, seed["pr"].number)


@pytest.mark.parametrize("failure", ["revoked", "expired", "http403", "http404", "github403", "github404",
                                     "http503", "github503", "rate_limit", "network", "transient"])
def test_sync_pr_revalidates_after_admission_without_losing_origin(seed, pat_setup, monkeypatch, failure):
    executing, requests = [False], []
    transient = failure in {"http503", "github503", "rate_limit", "network", "transient"}
    errors = {"http403": HTTPException(403, "Permission removed"), "http404": HTTPException(404, "Unavailable"),
              "github403": github.GitHubError(403, "Permission removed"), "github404": github.GitHubError(404, "Unavailable"),
              "http503": HTTPException(503, "Unavailable"), "github503": github.GitHubError(503, "Unavailable"),
              "rate_limit": github.GitHubError(403, "Rate limit", retry_after=60), "network": ConnectionError("Connection reset"),
              "transient": pat_provenance.TransientAuthorizationError("Unavailable")}
    def authorize(db, user, name, write=False):
        assert write
        if executing[0] and failure in errors:
            raise errors[failure]
        return {"id": seed["repo"].id}
    monkeypatch.setattr(github, "authorize_repo", authorize)
    monkeypatch.setattr(github, "installation_token", lambda *a: requests.append("token") or "token")
    monkeypatch.setattr(github, "request", lambda *a, **kw: requests.append("pull") or remote())
    monkeypatch.setattr(github, "threads", lambda *a: requests.append("threads") or [])
    origin = pat_provenance.job_provenance
    def after_admission(db, job):
        executing[0] = True
        token = db.get(PersonalApiToken, pat_setup["token"].id)
        if failure == "revoked":
            token.revoked_at = now()
        elif failure == "expired":
            token.expires_at = now() - timedelta(seconds=1)
        return origin(db, job)
    monkeypatch.setattr(pat_provenance, "job_provenance", after_admission)
    with pat_provenance.scoped(pat_setup["record"]), transaction() as db:
        job_id = reviews.enqueue(db, "sync_pr", {"repository_id": seed["repo"].id, "number": seed["pr"].number}).id
    jobs.execute_job(job_id)
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == ("queued" if transient else "failed")
        assert job.error == (None if transient else pat_provenance.REVOKED_MESSAGE)
        assert job.attempts == 1 and job.lease_until is None and job.dispatched_at is None
        assert job.payload["pat"] == pat_setup["record"]
        assert db.scalar(select(ReviewRun)) is None
        if transient:
            assert job.available_at > now()
    assert requests == [] and pat_provenance.current_provenance() is None
    monkeypatch.setattr(pat_provenance, "job_provenance", origin)
    executing[0] = False
    with transaction() as db:
        plain_id = reviews.enqueue(db, "sync_pr", {"repository_id": seed["repo"].id,
                                                   "number": seed["pr"].number, "automatic": False}).id
    jobs.execute_job(plain_id)
    with transaction() as db:
        assert db.get(Job, plain_id).status == "done"
        assert "pat" not in db.get(Job, plain_id).payload
        if transient:
            db.get(Job, job_id).available_at = now()
    if transient:
        jobs.execute_job(job_id)
        with transaction() as db:
            assert db.get(Job, job_id).status == "done" and db.get(Job, job_id).attempts == 2
            run = db.scalars(select(ReviewRun)).one()
            assert run.actor_id == seed["user"].id and run.progress["pat"] == pat_setup["record"]


@pytest.mark.parametrize("automatic,sender_type", [(True, None), (False, None), (True, "Bot")])
def test_sync_pr_mcp_dispatch_preserves_origin_and_options(seed, mcp_grant, monkeypatch, automatic, sender_type):
    from code_mortis.models import AppConfig

    monkeypatch.setattr(github, "authorize_repo", lambda *a, **kw: {"id": seed["repo"].id})
    monkeypatch.setattr(github, "installation_token", lambda *a: "token")
    monkeypatch.setattr(github, "request", lambda *a, **kw: remote())
    monkeypatch.setattr(github, "threads", lambda *a: [])
    with mcp_provenance.scoped(mcp_grant), transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"reviews_enabled": True}
        job_id = reviews.enqueue(db, "sync_pr", {"repository_id": seed["repo"].id, "number": seed["pr"].number,
                                                 "automatic": automatic, "sender_type": sender_type}).id
    jobs.execute_job(job_id)
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == "done", job.error
        assert job.payload["mcp"] == mcp_grant
        run = db.scalar(select(ReviewRun))
        if automatic and sender_type is None:
            assert run is not None and run.progress["mcp"] == mcp_grant
        else:
            assert run is None
    assert mcp_provenance.current_provenance() is None


def test_reconciliation_dedup_matches_complete_origins(seed, pat_setup, mcp_grant):
    from code_mortis.mcp.models import McpGrant

    payload = {"repository_id": seed["repo"].id, "number": 9, "automatic": True}
    with transaction() as db:
        grant_b = McpGrant(user_id=seed["user"].id, client_id="test-client", domain="reviews",
                           scopes=["reviews:read", "reviews:run"], repository_ids=[seed["repo"].id],
                           expires_at=now() + timedelta(days=1))
        db.add(grant_b)
        db.flush()
        grants = [mcp_grant, {"grant_id": grant_b.id, "domain": "reviews", "scope": "reviews:run"}]
        token_b = make_token(db, seed, ["reviews:run"], [seed["repo"].id])
        record_b = {"token_id": token_b.id, "user_id": seed["user"].id,
                    "scope": "reviews:run", "repository_id": seed["repo"].id}

        def queued():
            return list(db.scalars(select(Job.payload).where(
                Job.kind == "sync_pr", Job.status == "queued",
                Job.payload["number"].as_integer() == 9)))

        jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 1
        jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 1
        with pat_provenance.scoped(pat_setup["record"]):
            jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 2
        # Same PAT token dedups; a different PAT token does not.
        with pat_provenance.scoped(pat_setup["record"]):
            jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 2
        with pat_provenance.scoped(record_b):
            jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 3
        # Distinct MCP grants do not swallow each other or system work.
        with mcp_provenance.scoped(grants[0]):
            jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 4
        with mcp_provenance.scoped(grants[0]):
            jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 4
        with mcp_provenance.scoped(grants[1]):
            jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 5
        jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 5
        families = sorted(_family_key(p) for p in queued())
        assert families == ["mcp", "mcp", "pat", "pat", "system"]
        # Combined origins compare both credentials and cannot swallow single-origin work.
        with pat_provenance.scoped(pat_setup["record"]):
            for grant in grants:
                with mcp_provenance.scoped(grant):
                    jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
                    jobs.enqueue_reconciliation(db, "sync_pr", dict(payload))
        assert len(queued()) == 7
        # Present malformed markers must remain distinct from absent origins.
        assert jobs._stored_origin({"pat": None}) != jobs._stored_origin({})
        assert jobs._stored_origin({"pat": pat_setup["record"], "mcp": None}) != jobs._stored_origin({"pat": pat_setup["record"]})


def _family_key(payload):
    if "pat" in payload:
        return "pat"
    if "mcp" in payload:
        return "mcp"
    return "system"


def test_closeout_requires_terminal_status_and_matching_origin(seed, pat_setup):
    with transaction() as db:
        requested_only = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        requested_only.cancel_requested, requested_only.check_id = True, 7
        job = reviews.enqueue(db, "sync_check", {"run_id": requested_only.id})
        job.payload = {**job.payload, "pat": pat_setup["record"]}
        assert pat_provenance.closeout_allowed(db, job) is False
        mismatch = saved_run(db, seed, status="cancelled", provenance=pat_setup["record"])
        mismatch.cancel_requested, mismatch.check_id = True, 8
        other = dict(pat_setup["record"], scope="reviews:write")
        stray = reviews.enqueue(db, "sync_check", {"run_id": mismatch.id})
        stray.payload = {**stray.payload, "pat": other}
        assert pat_provenance.closeout_allowed(db, stray) is False
        mismatch.progress = {**mismatch.progress, "pat": other}
        assert pat_provenance.closeout_allowed(db, stray) is True


def test_closeout_exemption_allows_existing_check_only(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(jobs, "execute", lambda *a, **k: None)
    with transaction() as db:
        run = saved_run(db, seed, status="cancelled", provenance=pat_setup["record"])
        run.cancel_requested, run.check_id = True, 5
        allowed_job = reviews.enqueue(db, "sync_check", {"run_id": run.id})
        allowed_job.payload = {**allowed_job.payload, "pat": pat_setup["record"]}
        running = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        denied_job = reviews.enqueue(db, "sync_check", {"run_id": running.id})
        denied_job.payload = {**denied_job.payload, "pat": pat_setup["record"]}
        allowed_id, denied_id = allowed_job.id, denied_job.id
        assert pat_provenance.closeout_allowed(db, allowed_job) is True
        assert pat_provenance.closeout_allowed(db, denied_job) is False
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    jobs.execute_job(allowed_id)
    jobs.execute_job(denied_id)
    with transaction() as db:
        assert db.get(Job, allowed_id).status == "done"
        assert db.get(Job, denied_id).status == "failed"


def test_revoke_work_spares_closeout_and_preserves_history(seed, pat_setup):
    with transaction() as db:
        done = saved_run(db, seed, status="completed", provenance=pat_setup["record"])
        done.report = {"manifest": {}}
        cancelled = saved_run(db, seed, status="cancelled", provenance=pat_setup["record"])
        cancelled.cancel_requested, cancelled.check_id = True, 5
        check_job = reviews.enqueue(db, "sync_check", {"run_id": cancelled.id})
        check_job.payload = {**check_job.payload, "pat": pat_setup["record"]}
        finding = Finding(id=uid(), pr_id=seed["pr"].id, fingerprint=uid(), path="a.py", content="c",
                          start_line=1, end_line=1, last_seen_run_id=done.id,
                          desired_status="resolved",
                          sync_intent={"finding_id": "x", "version": 0, "pat": pat_setup["record"]})
        db.add(finding)
        db.flush()
        ids = (done.id, cancelled.id, check_job.id, finding.id)
        token = db.get(PersonalApiToken, pat_setup["token"].id)
        token.revoked_at = now()
        counts = pat_provenance.revoke_work(db, token.id)
        assert counts["jobs"] == 0
        assert counts["findings"] == 1
    with transaction() as db:
        assert db.get(ReviewRun, ids[0]).status == "completed"
        assert db.get(ReviewRun, ids[0]).report == {"manifest": {}}
        assert db.get(ReviewRun, ids[1]).status == "cancelled"
        assert db.get(Job, ids[2]).status == "queued"
        assert db.get(Finding, ids[3]).sync_error == pat_provenance.REVOKED_MESSAGE


def test_job_provenance_falls_back_to_stored_origin(seed, pat_setup):
    with transaction() as db:
        run = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        job = reviews.enqueue(db, "sync_check", {"run_id": run.id})
        assert "pat" not in job.payload
        assert pat_provenance.job_provenance(db, job) == pat_setup["record"]
        plain = reviews.enqueue(db, "sync_pr", {"repository_id": 22, "number": 1})
        assert pat_provenance.job_provenance(db, plain) is None


def test_sweep_makes_no_github_requests(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(github, "authorize_repo",
                        lambda *a, **k: pytest.fail("sweep must not call GitHub"))
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("sweep must not call GitHub"))
    with transaction() as db:
        dead = saved_run(db, seed, status="blocked", provenance=pat_setup["record"])
        run_id = dead.id
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
        counts = pat_provenance.settle_blocked(db)
        assert counts == {"runs": 1, "scans": 0}
    with transaction() as db:
        assert db.get(ReviewRun, run_id).status == "cancelled"


def test_transient_outage_propagates_permanent_denies(seed, pat_setup, monkeypatch):
    live_record = dict(pat_setup["record"])

    def permanent_403(db, token_id, **kwargs):
        raise HTTPException(403, "This account no longer has access to Code Mortis.")

    def transient_503(db, token_id, **kwargs):
        raise HTTPException(503, "Token authorization is temporarily unavailable. Try again shortly.")

    def transient_github(db, token_id, **kwargs):
        raise github.GitHubError(503, "upstream unavailable")

    def transient_network(db, token_id, **kwargs):
        raise ConnectionError("connection reset")

    def rate_limited_github(db, token_id, **kwargs):
        raise github.GitHubError(403, "rate limit reached", retry_after=60)

    with transaction() as db:
        monkeypatch.setattr(pat_service, "authorize", permanent_403)
        assert pat_provenance.revoked(db, live_record, repository_id=seed["repo"].id) is True
        monkeypatch.setattr(pat_service, "authorize", transient_503)
        with pytest.raises(pat_provenance.TransientAuthorizationError):
            pat_provenance.revoked(db, live_record, repository_id=seed["repo"].id)
        monkeypatch.setattr(pat_service, "authorize", transient_github)
        with pytest.raises(pat_provenance.TransientAuthorizationError):
            pat_provenance.revoked(db, live_record, repository_id=seed["repo"].id)
        monkeypatch.setattr(pat_service, "authorize", transient_network)
        with pytest.raises(pat_provenance.TransientAuthorizationError):
            pat_provenance.revoked(db, live_record, repository_id=seed["repo"].id)
        monkeypatch.setattr(pat_service, "authorize", rate_limited_github)
        with pytest.raises(pat_provenance.TransientAuthorizationError):
            pat_provenance.revoked(db, live_record, repository_id=seed["repo"].id)


def test_execute_job_holds_queued_on_transient_outage(seed, pat_setup, monkeypatch):
    def transient_503(db, token_id, **kwargs):
        raise HTTPException(503, "Token authorization is temporarily unavailable. Try again shortly.")

    monkeypatch.setattr(pat_service, "authorize", transient_503)
    monkeypatch.setattr(jobs, "execute", lambda *a, **k: pytest.fail("transient must not execute"))
    with transaction() as db:
        run = saved_run(db, seed, status="queued", provenance=pat_setup["record"])
        job = reviews.enqueue(db, "review", {"run_id": run.id})
        job.payload = {**job.payload, "pat": pat_setup["record"]}
        job_id, run_id = job.id, run.id
    jobs.execute_job(job_id)
    def rate_limited_github(db, token_id, **kwargs):
        raise github.GitHubError(403, "rate limit reached", retry_after=60)

    monkeypatch.setattr(pat_service, "authorize", rate_limited_github)
    jobs.execute_job(job_id)
    with transaction() as db:
        assert db.get(Job, job_id).status == "queued"
        assert db.get(ReviewRun, run_id).status == "queued"
        assert db.get(ReviewRun, run_id).cancel_requested is False


@pytest.mark.parametrize("origin", [None, {"token_id": 123}, {"token_id": "x", "user_id": 7}])
def test_review_pre_dispatch_rejects_malformed_origin_without_crashing(seed, github_stub, monkeypatch, origin):
    from code_mortis.reviews import context

    monkeypatch.setattr(github, "request", lambda *a, **k: {
        "state": "open", "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40},
    })
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("invalid origin must not execute"))
    with transaction() as db:
        run_id = reviews.create_run(db, db.get(PullRequest, seed["pr"].id),
                                    db.get(Repository, seed["repo"].id), "manual", seed["user"]).id

    def credentials(db, repository_id, credentials, token):
        run = db.get(ReviewRun, run_id)
        run.progress = {**run.progress, "pat": origin}
        return credentials

    monkeypatch.setattr(context, "execution_credentials", credentials)
    reviews.perform_review(run_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.status == "cancelled" and run.cancel_requested


def test_review_pre_dispatch_cleans_both_revoked_origins(seed, github_stub, pat_setup, mcp_grant, monkeypatch):
    from code_mortis.mcp.models import McpGrant
    from code_mortis.models import AppConfig
    from code_mortis.reviews import context

    monkeypatch.setattr(github, "request", lambda *a, **k: {
        "state": "open", "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40},
    })
    monkeypatch.setattr(ocr, "review", lambda *a, **k: pytest.fail("revoked origins must not execute"))
    with transaction() as db:
        db.get(AppConfig, 1).mcp_settings = {"reviews_enabled": True}
        run = reviews.create_run(db, db.get(PullRequest, seed["pr"].id),
                                 db.get(Repository, seed["repo"].id), "manual", seed["user"])
        run.progress = {**run.progress, "pat": pat_setup["record"], "mcp": mcp_grant}
        pat_sibling = saved_run(db, seed, provenance=pat_setup["record"])
        mcp_sibling = saved_run(db, seed)
        mcp_sibling.progress = {**mcp_sibling.progress, "mcp": mcp_grant}
        ids = run.id, pat_sibling.id, mcp_sibling.id

    def credentials(db, repository_id, credentials, token):
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
        db.get(McpGrant, mcp_grant["grant_id"]).revoked_at = now()
        return credentials

    monkeypatch.setattr(context, "execution_credentials", credentials)
    reviews.perform_review(ids[0])
    with transaction() as db:
        assert all(db.get(ReviewRun, run_id).status == "cancelled" for run_id in ids)


def test_job_kind_scopes_match_pipeline_kinds(seed, pat_setup, monkeypatch):
    from code_mortis.reviews import pipeline as review_pipeline

    assert set(review_pipeline.KINDS) <= set(pat_provenance._JOB_SCOPES)
    for kind in review_pipeline.KINDS:
        assert pat_provenance._JOB_SCOPES[kind] == {"reviews:run"}
    github_allows(monkeypatch)
    with transaction() as db:
        write_token = make_token(db, seed, ["reviews:write"], [seed["repo"].id])
        write_record = {"token_id": write_token.id, "user_id": seed["user"].id,
                        "scope": "reviews:write", "repository_id": seed["repo"].id}
        run = saved_run(db, seed, status="running")
        review_job = reviews.enqueue(db, "review", {"run_id": run.id})
        review_job.payload = {**review_job.payload, "pat": write_record}
        # reviews:write cannot start paid inference, but can resolve and sync.
        assert pat_provenance.job_revoked(db, review_job) is True
        resolve_job = reviews.enqueue(db, "resolve", {"finding_id": "f"})
        resolve_job.payload = {**resolve_job.payload, "pat": write_record}
        assert pat_provenance.job_revoked(db, resolve_job) is False
        # Session-only and unknown kinds reject PAT origins outright.
        for kind in ("generate_background", "test_connection", "webhook", "uninstall_installation", "frobnicate"):
            other = reviews.enqueue(db, kind, {"run_id": run.id})
            other.payload = {**other.payload, "pat": pat_setup["record"]}
            assert pat_provenance.job_revoked(db, other) is True, kind
        system = reviews.enqueue(db, "generate_background", {"run_id": run.id})
        assert pat_provenance.job_revoked(db, system) is False


def test_sync_check_closeout_patches_minimally(seed, pat_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(github, "installation_token", lambda *a, **k: "token")
    monkeypatch.setattr(github, "request",
                        lambda method, url, token, **kwargs: calls.append((method, url, kwargs.get("json"))))
    with transaction() as db:
        run = saved_run(db, seed, status="cancelled", provenance=pat_setup["record"])
        run.cancel_requested, run.check_id, run.check_url = True, 11, "https://example.com/checks/11"
        run_id = run.id
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    review_checks.sync_check(run_id)
    assert len(calls) == 1
    method, url, body = calls[0]
    assert method == "PATCH" and url.endswith("/check-runs/11")
    assert body["conclusion"] == "cancelled" and body["status"] == "completed"
    assert set(body) <= {"name", "external_id", "details_url", "status", "conclusion", "output",
                         "started_at", "completed_at"}
    assert set(body["output"]) == {"title", "summary"}
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.check_pending is False and run.check_error is None


def test_sync_check_aborts_write_when_revoked_after_claim(seed, pat_setup, monkeypatch):
    monkeypatch.setattr(github, "installation_token", lambda *a, **k: "token")
    monkeypatch.setattr(github, "request", lambda *a, **k: pytest.fail("revoked origin must not write"))
    monkeypatch.setattr(github, "pages", lambda *a, **k: pytest.fail("revoked origin must not write"))
    with transaction() as db:
        run = saved_run(db, seed, status="running", provenance=pat_setup["record"])
        run.check_id, run.check_url = 13, "https://example.com/checks/13"
        run_id = run.id
        db.get(PersonalApiToken, pat_setup["token"].id).revoked_at = now()
    review_checks.sync_check(run_id)
    with transaction() as db:
        run = db.get(ReviewRun, run_id)
        assert run.check_pending is False
        assert run.check_error == pat_provenance.REVOKED_MESSAGE
