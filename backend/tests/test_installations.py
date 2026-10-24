from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from code_mortis import jobs
from code_mortis.core.db import now, transaction
from code_mortis.integrations import github, installations
from code_mortis.models import (
    Finding,
    Installation,
    Job,
    Membership,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.reviews import service as reviews


@pytest.fixture
def remote(seed, github_stub, monkeypatch):
    state = {
        "installation": {
            "id": 10, "app_id": 123, "account": {"id": 7, "login": "owner", "type": "User"},
            "repository_selection": "selected", "suspended_at": None,
            "permissions": {"members": "read"},
        },
        "visible": True, "memberships": [], "membership_error": None,
        "deletes": 0, "error": None, "defer": False,
        "lost_response": False,
        "repositories": [{"id": 22, "full_name": "team/project", "private": True}],
    }

    def request(method, path, token, **kwargs):
        if path.startswith("/user/memberships/orgs/"):
            assert method == "GET" and token == "github-user-token"
            assert path == f"/user/memberships/orgs/{state['installation']['account']['login']}"
            if state["membership_error"]:
                raise github.GitHubError(state["membership_error"], "GitHub denied the membership request")
            return deepcopy(state["memberships"][0])
        assert path == "/app/installations/10"
        assert token == "app-jwt"
        if state["error"]:
            raise github.GitHubError(state["error"], "GitHub is unavailable")
        if state["installation"] is None:
            raise github.GitHubError(404, "Installation removed")
        if method == "GET":
            return deepcopy(state["installation"])
        assert method == "DELETE"
        state["deletes"] += 1
        if not state["defer"]:
            state["installation"] = None
        if state["lost_response"]:
            raise github.GitHubError(503, "Response lost")
        return None

    def pages(path, token, key=None):
        if state["error"]:
            raise github.GitHubError(state["error"], "GitHub is unavailable")
        if path in {"/app/installations", "/user/installations"}:
            return [deepcopy(state["installation"])] if state["installation"] and (
                path == "/app/installations" or state["visible"]
            ) else []
        assert path == "/installation/repositories"
        return deepcopy(state["repositories"])

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "pages", pages)
    return state


def request_uninstall(client):
    response = client.delete("/api/v1/installations/10")
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def retry_job(identity):
    with transaction() as db:
        db.get(Job, identity).available_at = now()
    jobs.execute_job(identity)


def add_run(db, identity, status="queued", publication="pending", review_ids=None):
    run = ReviewRun(
        id=identity, pr_id="pr", connection_id="connection", trigger="manual", actor_id=7,
        base_sha="b" * 40, head_sha="a" * 40, status=status, publication=publication,
        config={"connection": {"id": "connection"}, "settings": {}}, dedup_key=identity,
        review_ids=review_ids or [],
    )
    db.add(run)
    return run


def test_listing_uses_remote_status_and_separates_available_installations(remote, client):
    with transaction() as db:
        db.get(Installation, 10).account_id = 7
    row = client.get("/api/v1/installations").json()[0]
    assert row == {
        "id": 10, "account": "owner", "connected": True, "active": True, "status": "active",
        "settings_url": "https://github.com/settings/installations/10", "repository_selection": "selected",
        "can_connect": True, "can_uninstall": True, "can_remove": False, "unavailable_reason": None,
        "job_id": None, "operation_status": None, "operation_error": None,
    }
    remote["installation"]["id"] = 11
    rows = client.get("/api/v1/installations").json()
    assert [(row["id"], row["connected"], row["status"]) for row in rows] == [(11, False, "active")]
    assert next(row for row in rows if row["id"] == 11)["can_connect"]


@pytest.mark.parametrize("archived", [False, True])
def test_listing_displays_current_account_without_updating_saved_identity(remote, client, archived):
    with transaction() as db:
        saved = db.get(Installation, 10)
        saved.account, saved.account_id, saved.archived = "previous-owner", 42, archived
    remote["installation"]["account"]["login"] = "renamed-owner"

    response = client.get("/api/v1/installations")
    assert response.status_code == 200, response.text
    assert [(row["id"], row["account"], row["connected"]) for row in response.json()] == [
        (10, "renamed-owner", not archived),
    ]
    with transaction() as db:
        saved = db.get(Installation, 10)
        assert (saved.account, saved.account_id, saved.archived) == ("previous-owner", 42, archived)


def test_reinstall_reuses_repositories_settings_and_review_history(remote, client, monkeypatch):
    with transaction() as db:
        db.get(Repository, 22).settings = {"model": "custom-model", "automatic": True}
        add_run(db, "published", "completed", "published", [456])
        db.flush()
        db.add(Finding(id="finding", pr_id="pr", fingerprint="fp", path="a.py", start_line=1, end_line=1,
                       content="Original finding", last_seen_run_id="published", severity="high", status="open"))
    original = deepcopy(remote["installation"])
    jobs.execute_job(request_uninstall(client))
    remote["installation"] = {**original, "id": 11, "account": {**original["account"], "login": "renamed-owner"}}
    monkeypatch.setattr(installations, "remote_installation", lambda db, identity:
        installations.parse_remote(db, remote["installation"], identity) if identity == 11 else None)
    assert client.post("/api/v1/installations/11").status_code == 202
    jobs.sync_repositories(11)
    rows = client.get("/api/v1/installations").json()
    assert [(row["id"], row["account"], row["connected"]) for row in rows] == [(11, "renamed-owner", True)]
    with transaction() as db:
        repo = db.get(Repository, 22)
        assert repo.installation_id == 11 and repo.active and not repo.enabled
        assert repo.settings == {"model": "custom-model", "automatic": True}
        assert repo.connection_id == "connection"
        assert db.scalar(select(func.count()).select_from(Repository)) == 1
        assert db.get(PullRequest, "pr") and db.get(ReviewRun, "published")
        assert db.get(Finding, "finding").status == "open"
        assert db.get(Installation, 10).account_id == db.get(Installation, 11).account_id == 7
        assert db.get(Installation, 10).uninstalled and not db.get(Installation, 11).uninstalled


def test_same_name_does_not_merge_different_or_unknown_account_identities(remote, client):
    with transaction() as db:
        db.get(Installation, 10).account_id = 7
        db.add(Installation(id=12, account="owner", active=False))
    remote["installation"]["id"] = 11
    remote["installation"]["account"]["id"] = 8
    rows = client.get("/api/v1/installations").json()
    assert {row["id"] for row in rows} == {10, 11, 12}


def test_pending_predecessor_keeps_account_removal_disabled(remote, client):
    with transaction() as db:
        db.get(Installation, 10).account_id = 7
        db.add(Installation(id=11, account="owner", account_id=7, active=False))
        db.add(Job(kind="uninstall_installation", payload={"installation_id": 10}, status="queued"))
    remote["installation"] = None
    rows = client.get("/api/v1/installations").json()
    assert len(rows) == 1 and rows[0]["operation_status"] == "queued" and not rows[0]["can_remove"]
    assert client.delete("/api/v1/installations/11/local").status_code == 409


def test_removal_archives_history_deletes_empty_predecessors_and_stays_hidden(remote, client, monkeypatch):
    with transaction() as db:
        db.get(Installation, 10).account_id = 7
        db.add(Installation(id=11, account="owner", account_id=7, active=False))
        add_run(db, "published", "completed", "published", [456])
    remote["installation"] = None
    rows = client.get("/api/v1/installations").json()
    assert len(rows) == 1 and rows[0]["id"] == 11 and rows[0]["can_remove"]
    response = client.delete("/api/v1/installations/11/local")
    assert response.status_code == 200, response.text
    assert remote["deletes"] == 0
    assert client.get("/api/v1/installations").json() == []
    assert client.get("/api/v1/settings").json()["installations"] == []
    with transaction() as db:
        saved = db.get(Installation, 10)
        assert saved.archived and not saved.active
        assert db.get(Installation, 11) is None
        repo = db.get(Repository, 22)
        assert not repo.active and not repo.enabled and repo.connection_id == "connection"
        assert db.get(PullRequest, "pr") and db.get(ReviewRun, "published").review_ids == [456]
    monkeypatch.setattr(installations, "remote_installation", lambda *args:
        pytest.fail("Archived installations must not request GitHub access"))
    jobs.sync_repositories(10)
    jobs.schedule_reconciliation()
    with transaction() as db:
        assert not db.scalar(select(Job.id).where(Job.kind == "sync_repositories"))


@pytest.mark.parametrize("state", ["active", "suspended", "pending", "unavailable", "invalid"])
def test_removal_rechecks_github_and_pending_uninstall_before_mutating(remote, client, state):
    if state == "suspended":
        remote["installation"]["suspended_at"] = "2026-10-10T00:00:00Z"
    elif state == "pending":
        with transaction() as db:
            db.add(Job(kind="uninstall_installation", payload={"installation_id": 10}, status="queued"))
        remote["installation"] = None
    elif state == "unavailable":
        remote["error"] = 503
    elif state == "invalid":
        remote["installation"]["account"]["id"] = True
    response = client.delete("/api/v1/installations/10/local")
    assert response.status_code == (502 if state in {"unavailable", "invalid"} else 409)
    with transaction() as db:
        assert not db.get(Installation, 10).archived
        assert db.get(Repository, 22).active and db.get(Repository, 22).enabled
    assert remote["deletes"] == 0


@pytest.mark.parametrize("reason", ["csrf", "member", "disabled"])
def test_removal_requires_administrator_access_and_csrf(remote, client, reason):
    remote["installation"] = None
    if reason == "csrf":
        client.headers["x-csrf-token"] = "wrong"
    else:
        with transaction() as db:
            if reason == "member":
                db.get(Membership, 7).role = "member"
            else:
                db.get(User, 7).enabled = False
    assert client.delete("/api/v1/installations/10/local").status_code == 403
    with transaction() as db:
        assert not db.get(Installation, 10).archived


def test_account_migration_preserves_existing_rows_and_backfills_verified_jobs(seed):
    with transaction() as db:
        db.add_all([Installation(id=11, account="other"), Installation(id=12, account="unknown")])
        for identity, account_id in [(10, 7), (11, True), (12, 10**30)]:
            db.add(Job(kind="uninstall_installation", status="done",
                       payload={"installation_id": identity, "account_id": account_id}))
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261009_candidate_assessment")
        command.upgrade(config, "head")
        with transaction() as db:
            assert db.get(Installation, 10).account_id == 7
            assert db.get(Installation, 11).account_id is None
            assert db.get(Installation, 12).account_id is None
            assert all(not row.archived for row in db.scalars(select(Installation)))
            assert db.get(Repository, 22).enabled and db.get(PullRequest, "pr")
    finally:
        command.upgrade(config, "head")


def test_uninstall_migration_only_backfills_confirmed_inactive_installations(seed):
    with transaction() as db:
        add_run(db, "published", "completed", "published", [456])
        db.add_all(Installation(id=identity, account=f"account-{identity}", active=identity == 15)
                   for identity in range(11, 18))
        for identity, status in [(11, "done"), (12, "queued"), (13, "failed"), (15, "done")]:
            db.add(Job(kind="uninstall_installation", status=status,
                       payload={"installation_id": identity, "delete_accepted": True}))
        db.add(Job(kind="sync_repositories", status="done", payload={"installation_id": 16}))
        db.add(Job(kind="uninstall_installation", status="done", payload={"installation_id": "17"}))
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20261021_report_grades")
        command.upgrade(config, "head")
        with transaction() as db:
            rows = db.scalars(select(Installation).order_by(Installation.id)).all()
            assert {row.id for row in rows if row.uninstalled} == {11}
            assert {row.id for row in rows if row.active} == {10, 15}
            assert len(rows) == 8 and all(not row.archived for row in rows)
            assert db.get(Repository, 22).enabled and db.get(PullRequest, "pr")
            assert db.get(ReviewRun, "published").review_ids == [456]
    finally:
        command.upgrade(config, "head")


@pytest.mark.parametrize("reason", ["csrf", "origin", "member", "disabled", "invisible", "owner", "app", "account_id"])
def test_uninstall_rejects_unauthorized_requests(remote, client, reason):
    if reason == "csrf":
        client.headers["x-csrf-token"] = "wrong"
    elif reason == "origin":
        client.headers["origin"] = "https://attacker.example"
    elif reason in {"member", "disabled"}:
        with transaction() as db:
            if reason == "member":
                db.get(Membership, 7).role = "member"
            else:
                db.get(User, 7).enabled = False
    elif reason == "invisible":
        remote["visible"] = False
    elif reason == "owner":
        remote["installation"]["account"]["id"] = 8
    elif reason == "app":
        remote["installation"]["app_id"] = 456
    else:
        remote["installation"]["account"]["id"] = True
    response = client.delete("/api/v1/installations/10")
    assert response.status_code in {403, 502}, response.text
    assert remote["deletes"] == 0
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.get(Installation, 10).active


def test_list_requires_administrator(remote, client):
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert client.get("/api/v1/installations").status_code == 403


@pytest.mark.parametrize("role,state,account_id,user_id,allowed", [
    ("admin", "active", 77, 7, True), ("member", "active", 77, 7, False),
    ("admin", "pending", 77, 7, False), ("admin", "active", 88, 7, False),
    ("admin", "active", 77, 8, False), ("admin", "active", "77", 7, False),
    ("admin", "active", 77, "7", False),
])
def test_organization_uninstall_requires_current_numeric_ownership(remote, client, role, state, account_id, user_id, allowed):
    remote["installation"]["account"] = {"id": 77, "login": "renamed-team", "type": "Organization"}
    remote["memberships"] = [{
        "role": role, "state": state, "organization": {"id": account_id, "login": "old-team"}, "user": {"id": user_id},
    }]
    row = client.get("/api/v1/installations").json()[0]
    assert row["settings_url"] == "https://github.com/organizations/renamed-team/settings/installations/10"
    assert row["can_uninstall"] is allowed
    response = client.delete("/api/v1/installations/10")
    assert response.status_code == (202 if allowed else 403)
    if allowed:
        jobs.execute_job(response.json()["job_id"])
        assert remote["deletes"] == 1


@pytest.mark.parametrize("permission", [None, "read", "write"])
def test_organization_members_permission_is_actionable_and_rechecked_before_uninstall(remote, client, permission):
    remote["installation"]["account"] = {"id": 77, "login": "team", "type": "Organization"}
    remote["installation"]["permissions"] = {"members": permission} if permission else {}
    remote["memberships"] = [{
        "role": "admin", "state": "active", "organization": {"id": 77}, "user": {"id": 7},
    }]
    row = client.get("/api/v1/installations").json()[0]
    assert row["settings_url"] and row["can_connect"]
    assert row["can_uninstall"] is bool(permission)
    if not permission:
        assert "Members: Read-only" in row["unavailable_reason"]
        assert "approve the permission update" in row["unavailable_reason"]
        response = client.delete("/api/v1/installations/10")
        assert response.status_code == 403 and "Members: Read-only" in response.json()["detail"]
    else:
        identity = request_uninstall(client)
        remote["installation"]["permissions"] = {}
        jobs.execute_job(identity)
        with transaction() as db:
            job = db.get(Job, identity)
            assert job.status == "queued" and "Members: Read-only" in job.error
    assert remote["deletes"] == 0
    with transaction() as db:
        assert db.get(Installation, 10).active and db.get(Repository, 22).enabled


def test_personal_installation_does_not_require_members_permission(remote, client):
    remote["installation"]["permissions"] = {}
    assert client.get("/api/v1/installations").json()[0]["can_uninstall"]
    jobs.execute_job(request_uninstall(client))
    assert remote["deletes"] == 1


@pytest.mark.parametrize("reason", ["role", "ownership", "visible", "disabled", "app"])
def test_worker_revalidates_permissions_before_remote_write(remote, client, reason):
    identity = request_uninstall(client)
    if reason in {"role", "disabled"}:
        with transaction() as db:
            if reason == "role":
                db.get(Membership, 7).role = "member"
            else:
                db.get(User, 7).enabled = False
    elif reason == "ownership":
        remote["installation"]["account"]["id"] = 8
    elif reason == "app":
        remote["installation"]["app_id"] = 999
    else:
        remote["visible"] = False
    jobs.execute_job(identity)
    assert remote["deletes"] == 0
    with transaction() as db:
        assert db.get(Installation, 10).active
        assert db.get(Repository, 22).enabled
        assert db.get(Job, identity).error


def test_uninstall_is_deduplicated_and_preserves_history(remote, client):
    with transaction() as db:
        for status in ["queued", "running", "blocked"]:
            add_run(db, status, status)
        add_run(db, "partial", "completed", "partially_published", [123])
        add_run(db, "published", "completed", "published", [456])
        db.flush()
        db.add(Finding(id="finding", pr_id="pr", fingerprint="fp", path="a.py", start_line=1, end_line=1,
                       content="Original finding", last_seen_run_id="published", severity="high", status="open"))
    identity = request_uninstall(client)
    assert request_uninstall(client) == identity
    assert client.get("/api/v1/installations").json()[0]["operation_status"] == "queued"
    assert client.post("/api/v1/installations/10").status_code == 409
    jobs.execute_job(identity)
    jobs.execute_job(identity)
    assert remote["deletes"] == 1
    with transaction() as db:
        assert db.get(Job, identity).status == "done"
        assert not db.get(Installation, 10).active
        repo = db.get(Repository, 22)
        assert not repo.active and not repo.enabled and repo.connection_id == "connection"
        assert db.get(PullRequest, "pr")
        assert db.get(Finding, "finding").status == "open"
        for status in ["queued", "running", "blocked"]:
            run = db.get(ReviewRun, status)
            assert run.status == "cancelled" and run.cancel_requested
        partial = db.get(ReviewRun, "partial")
        assert partial.status == "completed" and partial.publication == "partially_published"
        assert partial.review_ids == [123]
        published = db.get(ReviewRun, "published")
        assert published.status == "completed" and published.publication == "published"
        assert published.review_ids == [456] and not published.cancel_requested
    row = client.get("/api/v1/installations").json()[0]
    assert row["status"] == "uninstalled" and not row["can_uninstall"] and row["settings_url"] is None
    assert request_uninstall(client) == identity
    reviews.publish_run("partial")


def test_simultaneous_requests_share_one_durable_job(remote, client):
    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(lambda _: request_uninstall(client), range(2)))
    assert identities[0] == identities[1]
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "uninstall_installation")) == 1


def test_lost_delete_response_is_reconciled_without_another_delete(remote, client):
    identity = request_uninstall(client)
    remote["lost_response"] = True
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.get(Job, identity).status == "queued"
        assert db.get(Job, identity).payload["delete_started"]
        assert not db.get(Installation, 10).active
    retry_job(identity)
    assert remote["deletes"] == 1
    with transaction() as db:
        assert db.get(Job, identity).status == "done"


def test_accepted_uninstall_waits_for_confirmation_and_never_reactivates(remote, client):
    identity = request_uninstall(client)
    remote["defer"] = True
    jobs.execute_job(identity)
    row = client.get("/api/v1/installations").json()[0]
    assert row["status"] == "active" and row["operation_status"] == "queued" and not row["active"]
    jobs.process_webhook({"event": "installation", "body": {"action": "unsuspend", "installation": {"id": 10}}})
    jobs.sync_repositories(10)
    retry_job(identity)
    assert remote["deletes"] == 1
    with transaction() as db:
        assert not db.get(Installation, 10).active
        assert not db.get(Installation, 10).uninstalled
        assert not db.get(Repository, 22).active
    remote["installation"] = None
    retry_job(identity)
    assert client.get("/api/v1/installations").json()[0]["status"] == "uninstalled"
    with transaction() as db:
        assert db.get(Installation, 10).uninstalled


def test_failed_uninstall_can_retry_the_existing_job(remote, client):
    identity = request_uninstall(client)
    remote["defer"] = True
    for _ in range(3):
        retry_job(identity)
    row = client.get("/api/v1/installations").json()[0]
    assert row["operation_status"] == "failed" and row["operation_error"]
    assert row["can_uninstall"] and not row["can_connect"]
    assert request_uninstall(client) == identity
    remote["installation"] = None
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.get(Job, identity).status == "done"


@pytest.mark.parametrize("origin", ["local", "external"])
def test_confirmed_uninstall_stops_polling_and_keeps_history(remote, client, monkeypatch, origin):
    with transaction() as db:
        add_run(db, "published", "completed", "published", [456])
        db.flush()
        db.add(Finding(id="finding", pr_id="pr", fingerprint="fp", path="a.py", start_line=1, end_line=1,
                       content="Original finding", last_seen_run_id="published", severity="high", status="open"))
        queued = reviews.enqueue(db, "sync_repositories", {"installation_id": 10}).id
    if origin == "local":
        jobs.execute_job(request_uninstall(client))
    else:
        remote["installation"] = None
        jobs.sync_repositories(10)
    row = client.get("/api/v1/installations").json()[0]
    assert row["status"] == "uninstalled" and row["can_remove"]

    monkeypatch.setattr(installations, "remote_installation", lambda *args:
        pytest.fail("A confirmed uninstall must not be polled again"))
    jobs.sync_repositories(10)
    jobs.execute_job(queued)
    for event in ["installation", "installation_repositories"]:
        jobs.process_webhook({"event": event, "body": {
            "action": "unsuspend", "installation": {"id": 10},
        }})
    jobs.schedule_reconciliation()
    with transaction() as db:
        saved = db.get(Installation, 10)
        assert saved.uninstalled and not saved.active and not saved.archived
        assert not db.scalar(select(Job.id).where(Job.kind == "sync_repositories",
                                                 Job.status.in_(["queued", "running"])))
        repo = db.get(Repository, 22)
        assert not repo.active and not repo.enabled and repo.connection_id == "connection"
        assert db.get(PullRequest, "pr") and db.get(ReviewRun, "published").review_ids == [456]
        assert db.get(Finding, "finding").status == "open"


@pytest.mark.parametrize("state", ["active", "suspended", "inactive"])
def test_polling_keeps_active_suspended_and_unconfirmed_installations(remote, state):
    if state == "suspended":
        remote["installation"]["suspended_at"] = "2026-10-10T00:00:00Z"
        jobs.sync_repositories(10)
    elif state == "inactive":
        with transaction() as db:
            db.get(Installation, 10).active = False
    jobs.schedule_reconciliation()
    with transaction() as db:
        identity = db.scalar(select(Job.id).where(Job.kind == "sync_repositories"))
        assert identity is not None and not db.get(Installation, 10).uninstalled
    jobs.execute_job(identity)
    with transaction() as db:
        assert db.get(Job, identity).status == "done"
        assert db.get(Installation, 10).active == (state != "suspended")
        assert not db.get(Installation, 10).uninstalled
    remote["installation"]["suspended_at"] = None
    jobs.sync_repositories(10)
    with transaction() as db:
        assert db.get(Installation, 10).active and db.get(Repository, 22).active


def test_explicit_reconnect_rechecks_remote_access_and_clears_uninstalled(remote, client):
    with transaction() as db:
        saved = db.get(Installation, 10)
        saved.active, saved.uninstalled = False, True
    assert client.post("/api/v1/installations/10").status_code == 202
    with transaction() as db:
        saved = db.get(Installation, 10)
        assert saved.active and not saved.uninstalled
    jobs.sync_repositories(10)
    with transaction() as db:
        assert db.get(Repository, 22).active


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_remote_errors_do_not_mean_removed(remote, client, status):
    remote["error"] = status
    assert client.get("/api/v1/installations").status_code == (502 if status >= 500 else status)
    with pytest.raises(github.GitHubError):
        jobs.sync_repositories(10)
    with transaction() as db:
        assert db.get(Installation, 10).active and db.get(Repository, 22).enabled
        assert not db.get(Installation, 10).uninstalled


@pytest.mark.parametrize("field,value", [("id", 11), ("app_id", 999), ("account", {"id": True})])
def test_invalid_remote_installation_does_not_mark_uninstalled(remote, field, value):
    remote["installation"][field] = value
    with pytest.raises(github.GitHubError):
        jobs.sync_repositories(10)
    with transaction() as db:
        assert db.get(Installation, 10).active and db.get(Repository, 22).enabled
        assert not db.get(Installation, 10).uninstalled


@pytest.mark.parametrize("removed", [True, False])
def test_external_uninstall_or_suspension_stops_work(remote, removed):
    with transaction() as db:
        add_run(db, "run", "running")
    if removed:
        remote["installation"] = None
    else:
        remote["installation"]["suspended_at"] = "2026-10-08T00:00:00Z"
    jobs.process_webhook({"event": "installation", "body": {
        "action": "deleted" if removed else "suspend", "installation": {"id": 10},
    }})
    jobs.sync_repositories(10)
    with transaction() as db:
        assert not db.get(Installation, 10).active and not db.get(Repository, 22).active
        assert db.get(ReviewRun, "run").status == "cancelled"


def test_repository_selection_and_restoration_preserve_settings_but_pause_reviews(remote):
    with transaction() as db:
        db.get(Repository, 22).settings = {"model": "chosen-model", "automatic": True}
        add_run(db, "run")
    remote["repositories"] = [{"id": 23, "full_name": "team/new", "private": True}]
    jobs.process_webhook({"event": "installation_repositories", "body": {"installation": {"id": 10}}})
    jobs.sync_repositories(10)
    with transaction() as db:
        assert not db.get(Repository, 22).active and not db.get(Repository, 22).enabled
        assert db.get(Repository, 23).active and not db.get(Repository, 23).enabled
        assert db.get(ReviewRun, "run").status == "cancelled"
    remote["repositories"].append({"id": 22, "full_name": "team/project", "private": True})
    jobs.sync_repositories(10)
    with transaction() as db:
        repo = db.get(Repository, 22)
        assert repo.active and not repo.enabled
        assert repo.settings == {"model": "chosen-model", "automatic": True}


def test_run_creation_rechecks_repository_after_removal(remote):
    with transaction() as db:
        repo, pr = db.get(Repository, 22), db.get(PullRequest, "pr")
        with transaction() as other:
            installations.deactivate(other, other.get(Installation, 10), "Removed")
        with pytest.raises(ValueError, match="Enable this repository"):
            reviews.create_run(db, pr, repo, "manual", db.get(User, 7))


def test_delayed_suspend_event_uses_current_github_state(remote):
    jobs.process_webhook({"event": "installation", "body": {"action": "suspend", "installation": {"id": 10}}})
    jobs.sync_repositories(10)
    with transaction() as db:
        assert db.get(Installation, 10).active and db.get(Repository, 22).enabled


def test_suspended_installation_can_be_uninstalled_but_not_connected(remote, client):
    remote["installation"]["suspended_at"] = "2026-10-08T00:00:00Z"
    row = client.get("/api/v1/installations").json()[0]
    assert row["status"] == "suspended" and row["can_uninstall"] and not row["can_connect"]
    assert client.post("/api/v1/installations/10").status_code == 403
    identity = request_uninstall(client)
    jobs.execute_job(identity)
    assert remote["deletes"] == 1


def test_installed_account_inaccessible_to_user_does_not_look_uninstalled(remote, client):
    remote["visible"] = False
    row = client.get("/api/v1/installations").json()[0]
    assert row["status"] == "active" and not row["can_uninstall"] and not row["can_connect"]
    assert row["settings_url"] is None and row["unavailable_reason"]


def test_malformed_remote_identity_cannot_uninstall_another_target(remote, client):
    remote["installation"]["id"] = 11
    assert client.delete("/api/v1/installations/10").status_code == 403
    assert remote["deletes"] == 0


def test_organization_permission_failure_leaves_manage_link_and_no_uninstall(remote, client):
    remote["installation"]["account"] = {"id": 77, "login": "team", "type": "Organization"}
    remote["membership_error"] = 403
    row = client.get("/api/v1/installations").json()[0]
    assert not row["can_uninstall"] and row["settings_url"]
    assert "could not be verified" in row["unavailable_reason"]
