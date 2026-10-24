import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event
from unittest.mock import Mock

import httpx
import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction
from code_mortis.core.security import digest, seal
from code_mortis.integrations import github
from code_mortis.models import Installation, LoginSession, Membership, Repository, User

PATH = "/repos/team/project"
TOKEN = "github-user-token"
ALLOWED = {"permissions": {"pull": True, "push": True}}


@pytest.fixture
def cache():
    with Redis.from_url(settings().redis_url) as cache:
        yield cache


@pytest.fixture
def remote(monkeypatch):
    request = Mock(return_value=deepcopy(ALLOWED))
    monkeypatch.setattr(github, "request", request)
    return request


def test_reads_share_a_fixed_five_second_window(cache, remote):
    key = github._permission_cache_key(7, PATH, TOKEN)
    first = github.permission_request(7, PATH, TOKEN)
    assert 4500 < cache.pttl(key) <= 5000
    # Shorten the remaining window to test hits without a five-second sleep.
    cache.pexpire(key, 300)
    first["permissions"]["push"] = False
    assert github.permission_request(7, PATH, TOKEN)["permissions"]["push"]
    assert 0 < cache.pttl(key) <= 300
    remote.assert_called_once()
    cache.pexpire(key, 1)
    time.sleep(0.02)
    remote.return_value = {"permissions": {"pull": False}}
    assert not github.permission_request(7, PATH, TOKEN)["permissions"]["pull"]
    assert remote.call_count == 2


def test_cache_isolates_deployments_users_tokens_paths_and_pages(cache, remote, monkeypatch):
    variants = [
        (7, PATH, TOKEN), (8, PATH, TOKEN), (7, PATH, "rotated-token"),
        (7, "/repos/team/other", TOKEN),
        (7, "/user/installations/10/repositories?per_page=100&page=1", TOKEN),
        (7, "/user/installations/10/repositories?per_page=100&page=2", TOKEN),
    ]
    for args in variants * 2:
        github.permission_request(*args)
    assert remote.call_count == len(variants)
    monkeypatch.setattr(settings(), "public_url", "https://other.example.com")
    github.permission_request(7, PATH, TOKEN)
    assert remote.call_count == len(variants) + 1
    assert all(TOKEN.encode() not in key and b"rotated-token" not in key for key in cache.scan_iter())


def test_writes_check_github_and_replace_cached_permissions(seed, cache, remote):
    user = seed["user"]
    assert github.authorize_repo(None, user, "team/project")["permissions"]["push"]
    for _ in range(2):
        github.authorize_repo(None, user, "team/project", write=True)
    assert remote.call_count == 3
    remote.return_value = {"permissions": {"pull": True, "push": False}}
    with pytest.raises(github.GitHubError, match="requires repository write access"):
        github.authorize_repo(None, user, "team/project", write=True)
    assert not github.authorize_repo(None, user, "team/project")["permissions"]["push"]
    assert remote.call_count == 4
    with transaction() as db:
        db.get(User, 7).access_token = seal("rotated-token")
    github.authorize_repo(None, user, "team/project")
    assert remote.call_count == 5


def test_expired_read_access_is_rechecked_and_disabled_users_cannot_use_cache(seed, cache, remote):
    user = seed["user"]
    github.authorize_repo(None, user, "team/project")
    remote.return_value = {"permissions": {"pull": False}}
    cache.pexpire(github._permission_cache_key(7, PATH, TOKEN), 1)
    time.sleep(0.02)
    with pytest.raises(github.GitHubError, match="Repository access denied"):
        github.authorize_repo(None, user, "team/project")
    assert remote.call_count == 2
    with transaction() as db:
        db.get(User, 7).enabled = False
    with pytest.raises(github.GitHubError, match="no longer allowed"):
        github.authorize_repo(None, user, "team/project", write=True)
    assert remote.call_count == 2


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_failed_refresh_replaces_allow_and_is_shared(cache, remote, status):
    github.permission_request(7, PATH, TOKEN)
    remote.side_effect = github.GitHubError(status, "Access unavailable")
    with pytest.raises(github.GitHubError) as caught:
        github.permission_request(7, PATH, TOKEN, fresh=True)
    assert caught.value.status == status
    for _ in range(3):
        with pytest.raises(github.GitHubError) as caught:
            github.permission_request(7, PATH, TOKEN)
        assert caught.value.status == status
    assert remote.call_count == 2
    key = github._permission_cache_key(7, PATH, TOKEN)
    cache.pexpire(key, 1)
    time.sleep(0.02)
    with pytest.raises(github.GitHubError):
        github.permission_request(7, PATH, TOKEN)
    assert remote.call_count == 3


@pytest.mark.parametrize("headers", [
    {"Retry-After": "60"},
    {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1060"},
    {"Retry-After": "30", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1060"},
])
def test_rate_limit_cooldown_applies_to_reads_and_writes(cache, monkeypatch, headers):
    request = Mock(return_value=httpx.Response(403, headers=headers))
    monkeypatch.setattr(github.httpx, "request", request)
    clock = Mock(return_value=1000)
    monkeypatch.setattr(github.time, "time", clock)
    with pytest.raises(github.GitHubError) as caught:
        github.permission_request(7, PATH, TOKEN)
    assert caught.value.retry_after == 60
    key = github._permission_cache_key(7, PATH, TOKEN)
    assert 59000 < cache.pttl(key) <= 60000
    clock.return_value = 1010
    for fresh in (False, True):
        with pytest.raises(github.GitHubError) as caught:
            github.permission_request(7, PATH, TOKEN, fresh=fresh)
        assert caught.value.retry_after == 50
    request.assert_called_once()


def test_concurrent_cold_and_expired_reads_share_one_refresh(cache, monkeypatch):
    gate = Barrier(10)
    request = Mock(side_effect=lambda *args: (time.sleep(0.15), ALLOWED)[1])
    monkeypatch.setattr(github, "request", request)

    def read(_):
        gate.wait(timeout=5)
        return github.permission_request(7, PATH, TOKEN)

    for expected in (1, 2):
        with ThreadPoolExecutor(max_workers=10) as pool:
            assert list(pool.map(read, range(10))) == [ALLOWED] * 10
        assert request.call_count == expected
        cache.pexpire(github._permission_cache_key(7, PATH, TOKEN), 1)
        time.sleep(0.02)


def test_concurrent_failed_refresh_is_shared(monkeypatch):
    gate = Barrier(10)

    def fail(*args):
        time.sleep(0.15)
        raise github.GitHubError(503, "GitHub unavailable")

    request = Mock(side_effect=fail)
    monkeypatch.setattr(github, "request", request)

    def read(_):
        gate.wait(timeout=5)
        with pytest.raises(github.GitHubError, match="GitHub unavailable"):
            github.permission_request(7, PATH, TOKEN)

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(read, range(10)))
    request.assert_called_once()


@pytest.mark.parametrize("primed", [False, True])
@pytest.mark.parametrize("outcome", ["allowed", "revoked", 403, 503])
def test_overlapping_fresh_checks_share_slow_refresh_without_reusing_prior_access(cache, monkeypatch, primed, outcome):
    request = Mock(return_value=deepcopy(ALLOWED))
    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "user_token", lambda *_: TOKEN)
    if primed:
        github.permission_request(7, PATH, TOKEN)
        request.reset_mock()
    gate = Barrier(6)

    def refresh(*args):
        time.sleep(2.2)
        if isinstance(outcome, int):
            raise github.GitHubError(outcome, "Access unavailable")
        return deepcopy(ALLOWED) if outcome == "allowed" else {"permissions": {"pull": True, "push": False}}

    def check(_):
        gate.wait(timeout=5)
        if outcome == "allowed":
            return github.authorize_repo(None, User(id=7), "team/project", write=True)
        with pytest.raises(github.GitHubError) as caught:
            github.authorize_repo(None, User(id=7), "team/project", write=True)
        assert caught.value.status == (outcome if isinstance(outcome, int) else 403)
        return str(caught.value)

    request.side_effect = refresh
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(check, range(6)))
    assert all(result == results[0] for result in results)
    request.assert_called_once()
    # A later write still revalidates, even when the previous refresh returned the same permissions.
    request.side_effect = None
    request.return_value = deepcopy(ALLOWED)
    assert github.authorize_repo(None, User(id=7), "team/project", write=True) == ALLOWED
    assert request.call_count == 2


def _process_read(path, fresh=False, gate=None):
    from unittest.mock import patch

    with Redis.from_url(settings().redis_url) as cache:
        def request(*args):
            cache.incr(github._permission_cache_key(7, path, TOKEN) + ":calls")
            time.sleep(0.3)
            return ALLOWED
        with patch.object(github, "request", request):
            if gate is not None:
                gate.wait(timeout=10)
            return github.permission_request(7, path, TOKEN, fresh=fresh)


@pytest.mark.parametrize("sessions", [1, 5, 20])
def test_processes_share_permission_requests(cache, sessions):
    with ProcessPoolExecutor(max_workers=sessions, mp_context=multiprocessing.get_context("spawn")) as pool:
        assert list(pool.map(_process_read, [PATH] * sessions)) == [ALLOWED] * sessions
    assert int(cache.get(github._permission_cache_key(7, PATH, TOKEN) + ":calls")) == 1


def test_processes_share_overlapping_fresh_permission_requests(cache):
    with multiprocessing.Manager() as manager:
        gate = manager.Barrier(6)
        with ProcessPoolExecutor(max_workers=6, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = [pool.submit(_process_read, PATH, True, gate) for _ in range(6)]
            assert [future.result(timeout=15) for future in futures] == [ALLOWED] * 6
    assert int(cache.get(github._permission_cache_key(7, PATH, TOKEN) + ":calls")) == 1


def test_lock_wait_is_bounded_and_does_not_call_github(cache, remote, monkeypatch):
    original_lock = Redis.lock
    waits = []

    def short_lock(self, *args, **kwargs):
        if "blocking_timeout" in kwargs:
            waits.append(kwargs["blocking_timeout"])
            kwargs["blocking_timeout"] = 0.2
        return original_lock(self, *args, **kwargs)

    monkeypatch.setattr(Redis, "lock", short_lock)
    key = github._permission_cache_key(7, PATH, TOKEN)
    with cache.lock(key + ":lock", timeout=10):
        started = time.monotonic()
        with pytest.raises(github.GitHubError, match="being refreshed") as caught:
            github.permission_request(7, PATH, TOKEN)
        assert caught.value.status == 503 and caught.value.retry_after == 2
        assert time.monotonic() - started < 3
    remote.assert_not_called()
    assert waits == [60]


def test_expired_lock_owner_cannot_overwrite_newer_denial(cache, monkeypatch):
    key = github._permission_cache_key(7, PATH, TOKEN)
    started, finish = Event(), Event()

    def request(*args):
        started.set()
        assert finish.wait(timeout=5)
        return ALLOWED

    monkeypatch.setattr(github, "request", request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(github.permission_request, 7, PATH, TOKEN)
        assert started.wait(timeout=5)
        cache.pexpire(key + ":lock", 1)
        time.sleep(0.02)
        with cache.lock(key + ":lock", timeout=10) as newer:
            cache.set(key, json.dumps({"data": {"permissions": {"pull": False}}}), ex=5)
            finish.set()
            with pytest.raises(github.GitHubError, match="refresh expired"):
                old.result(timeout=5)
            assert newer.owned()
    assert not github.permission_request(7, PATH, TOKEN)["permissions"]["pull"]


def test_redis_outage_checks_github_without_serving_cached_allow(cache, remote, monkeypatch):
    github.permission_request(7, PATH, TOKEN)
    monkeypatch.setattr(Redis, "get", Mock(side_effect=RedisConnectionError("private-host")))
    assert github.permission_request(7, PATH, TOKEN) == ALLOWED
    remote.side_effect = github.GitHubError(403, "Revoked")
    with pytest.raises(github.GitHubError, match="Revoked"):
        github.permission_request(7, PATH, TOKEN)
    assert remote.call_count == 3


@pytest.mark.parametrize("failure", ["publish", "release"])
def test_cache_failure_after_github_does_not_discard_fresh_result(remote, monkeypatch, failure):
    if failure == "publish":
        monkeypatch.setattr(Redis, "eval", Mock(side_effect=RedisConnectionError()))
    else:
        monkeypatch.setattr("redis.lock.Lock.release", Mock(side_effect=RedisConnectionError()))
    assert github.permission_request(7, PATH, TOKEN, fresh=True) == ALLOWED
    remote.assert_called_once()


@pytest.mark.parametrize("payload", ["not json", "null", "[]", '{"error": []}', '{"data": null}'])
def test_corrupt_cache_requires_a_fresh_check(cache, remote, payload):
    cache.set(github._permission_cache_key(7, PATH, TOKEN), payload, ex=5)
    assert github.permission_request(7, PATH, TOKEN) == ALLOWED
    remote.assert_called_once()


def test_visibility_pages_are_shared_but_local_deactivation_is_immediate(seed, cache, remote):
    remote.side_effect = lambda method, path, token: {
        "repositories": [{"id": 22}] * (100 if path.endswith("&page=1") else 1),
    }
    for _ in range(2):
        with transaction() as db:
            assert [r.id for r in github.visible_repositories(db, db.get(User, 7))] == [22]
    assert remote.call_count == 2
    with transaction() as db:
        db.get(Repository, 22).active = False
    with transaction() as db:
        assert github.visible_repositories(db, db.get(User, 7)) == []
    with transaction() as db:
        db.get(Repository, 22).active = True
        db.get(Installation, 10).active = False
    with transaction() as db:
        assert github.visible_repositories(db, db.get(User, 7)) == []
    assert remote.call_count == 2


@pytest.mark.parametrize("revoke,expected", [
    ("user", 403), ("membership", 403), ("session", 401), ("repository", 404), ("installation", 404),
])
def test_local_revocation_blocks_api_even_with_cached_permissions(seed, client, remote, revoke, expected):
    response = client.get("/api/v1/pulls/pr")
    assert response.status_code == 200, response.text
    with transaction() as db:
        if revoke == "user":
            db.get(User, 7).enabled = False
        elif revoke == "membership":
            db.delete(db.get(Membership, 7))
        elif revoke == "session":
            db.get(LoginSession, digest("session")).expires_at = now()
        elif revoke == "repository":
            db.get(Repository, 22).active = False
        else:
            db.get(Installation, 10).active = False
    assert client.get("/api/v1/pulls/pr").status_code == expected
    remote.assert_called_once()


def test_other_github_reads_do_not_use_permission_cache(remote):
    remote.return_value = {"items": [{"id": 1}]}
    for _ in range(2):
        assert github.pages("/other", TOKEN, "items") == [{"id": 1}]
    assert remote.call_count == 2
