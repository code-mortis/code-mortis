from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from code_mortis import api, github
from code_mortis.db import now, transaction
from code_mortis.models import LoginSession, Membership, User
from code_mortis.schemas import MemberInput
from code_mortis.security import digest, seal


@pytest.mark.parametrize("operation", ["remove", "demote"])
def test_last_administrator_cannot_be_removed_or_demoted(seed, client, monkeypatch, operation):
    monkeypatch.setattr(github, "request", lambda *args: {"id": 7, "login": "owner", "type": "User"})
    response = (
        client.delete("/api/v1/members/7") if operation == "remove"
        else client.post("/api/v1/members", json={"login": "renamed-owner", "role": "member"})
    )
    assert response.status_code == 409
    assert "At least one administrator must remain" in response.json()["detail"]
    with transaction() as db:
        assert db.get(Membership, 7).role == "admin"
        assert db.get(User, 7).enabled and db.get(User, 7).role == "admin"
        assert db.get(LoginSession, digest("session"))


def test_administrator_can_manage_others_but_cannot_remove_own_access(seed, client, monkeypatch):
    with transaction() as db:
        db.add_all([
            User(id=8, login="second", role="admin", access_token=seal("second-token")),
            Membership(github_id=8, login="second", role="admin"),
        ])
        db.flush()
        db.add(LoginSession(token_hash=digest("second-session"), user_id=8, csrf="second-csrf",
                            expires_at=now() + timedelta(days=1)))
    monkeypatch.setattr(github, "request", lambda method, path, token: {
        "id": 7 if path == "/users/owner" else 8,
        "login": "owner" if path == "/users/owner" else "second", "type": "User",
    })
    assert client.delete("/api/v1/members/7").status_code == 409
    assert client.post("/api/v1/members", json={"login": "owner", "role": "member"}).status_code == 409
    assert client.post("/api/v1/members", json={"login": "owner", "role": "admin"}).status_code == 200
    assert client.post("/api/v1/members", json={"login": "second", "role": "member"}).status_code == 200
    with transaction() as db:
        assert db.get(Membership, 8).role == db.get(User, 8).role == "member"
        assert db.get(Membership, 7).role == "admin"
    assert client.delete("/api/v1/members/8").status_code == 200
    with transaction() as db:
        assert db.get(Membership, 8) is None
        removed = db.get(User, 8)
        assert not removed.enabled and not removed.access_token and not removed.refresh_token
        assert db.get(LoginSession, digest("second-session")) is None
        assert db.get(Membership, 7).role == "admin"
        assert db.get(User, 7).enabled


@pytest.mark.parametrize("operations", [("remove", "remove"), ("demote", "demote"), ("remove", "demote")])
def test_concurrent_member_changes_recheck_administrator_access(seed, monkeypatch, operations):
    with transaction() as db:
        db.add_all([User(id=8, login="second", role="admin"), Membership(github_id=8, login="second", role="admin")])
    monkeypatch.setattr(github, "user_token", lambda *args: "test-token")
    monkeypatch.setattr(github, "request", lambda method, path, token: {
        "id": 7 if path == "/users/owner" else 8,
        "login": path.rsplit("/", 1)[1], "type": "User",
    })
    admitted = Barrier(2)

    def change(identity):
        try:
            with transaction() as db:
                user = db.get(User, identity)
                assert db.get(Membership, identity).role == user.role == "admin"
                admitted.wait(timeout=5)
                target = 8 if identity == 7 else 7
                if operations[identity - 7] == "remove":
                    api.remove_member(target, db, user)
                else:
                    api.add_member(MemberInput(login="second" if target == 8 else "owner", role="member"), db, user)
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(change, [7, 8]))
    assert sorted(responses) == [200, 403]
    with transaction() as db:
        administrators = db.scalars(select(Membership).where(Membership.role == "admin")).all()
        assert len(administrators) == 1
        assert db.get(User, administrators[0].github_id).enabled


@pytest.mark.parametrize("operation", ["remove", "demote"])
def test_member_changes_retain_csrf_and_role_checks(seed, client, operation):
    path = "/api/v1/members/7" if operation == "remove" else "/api/v1/members"
    kwargs = {} if operation == "remove" else {"json": {"login": "owner", "role": "member"}}
    method = client.delete if operation == "remove" else client.post
    assert method(path, headers={"x-csrf-token": "invalid"}, **kwargs).status_code == 403
    with transaction() as db:
        db.get(Membership, 7).role = "member"
    assert method(path, **kwargs).status_code == 403


def test_concurrent_invites_reuse_the_new_membership(seed, monkeypatch):
    monkeypatch.setattr(github, "user_token", lambda *args: "test-token")
    monkeypatch.setattr(github, "request", lambda *args: {"id": 8, "login": "second", "type": "User"})
    admitted = Barrier(2)

    def invite(_):
        with transaction() as db:
            user = db.get(User, 7)
            admitted.wait(timeout=5)
            return api.add_member(MemberInput(login="second"), db, user)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(invite, range(2))) == [{"ok": True}, {"ok": True}]
    with transaction() as db:
        assert [(row.github_id, row.role) for row in db.scalars(select(Membership).order_by(Membership.github_id))] == [
            (7, "admin"), (8, "member"),
        ]
