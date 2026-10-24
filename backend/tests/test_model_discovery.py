import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from code_mortis import native_runner
from code_mortis.core.db import transaction
from code_mortis.core.security import seal
from code_mortis.integrations import model_discovery, runner_client
from code_mortis.models import (
    Connection,
    ConnectionGrant,
    Membership,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from code_mortis.reviews import service as reviews
from code_mortis.schemas.common import ModelCatalog, ModelDiscoveryInput


def provider(monkeypatch, handler):
    factory = httpx.Client
    monkeypatch.setattr(model_discovery, "safe_endpoint", lambda url: url.rstrip("/"))
    monkeypatch.setattr(
        model_discovery.httpx,
        "Client",
        lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs),
    )


def draft(**kwargs):
    return {"provider": "openai", "protocol": "openai", "base_url": "https://example.com/v1", **kwargs}


def test_draft_and_saved_models_use_each_connections_credentials_without_saving(seed, client, monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200, json={"data": [{"id": "first", "name": "First model"}, {"id": "first"}, {"id": "second"}]}
        )

    provider(monkeypatch, respond)
    result = client.post(
        "/api/v1/connections/models", json=draft(api_key="draft-secret", extra_headers={"X-Test": "custom"})
    )
    assert result.status_code == 200 and result.json()["status"] == "ready"
    assert [row["id"] for row in result.json()["models"]] == ["first", "second"]
    assert (
        calls[-1].headers["authorization"] == "Bearer draft-secret"
        and calls[-1].headers["x-test"] == "custom"
    )
    assert str(calls[-1].url) == "https://example.com/v1/models"
    result = client.get("/api/v1/connections/connection/models")
    assert (
        result.json()["source"] == "provider"
        and calls[-1].headers["authorization"] == "Bearer provider-secret"
    )
    with transaction() as db:
        assert len(db.scalars(select(Connection)).all()) == 1
        row = db.get(Connection, "connection")
        assert row.model == "test-model" and row.last_test is None
        db.add(
            Connection(
                id="second",
                name="Second",
                provider="openai",
                protocol="openai",
                model="other",
                base_url="https://other.example/v1",
                secret=seal('{"api_key":"second-secret"}'),
            )
        )
    client.get("/api/v1/connections/second/models")
    assert (
        calls[-1].url.host == "other.example" and calls[-1].headers["authorization"] == "Bearer second-secret"
    )
    assert "secret" not in result.text


def test_edited_endpoint_cannot_receive_stored_key_or_headers(seed, client, monkeypatch):
    calls = []
    provider(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json={"data": []}))
    with transaction() as db:
        db.get(Connection, "connection").secret = seal(
            json.dumps({"api_key": "saved-key", "extra_headers": {"X-Private": "saved-header"}})
        )
    same = draft(connection_id="connection")
    assert client.post("/api/v1/connections/models", json=same).json()["status"] == "ready"
    assert calls[-1].headers["x-private"] == "saved-header"
    changed = {**same, "base_url": "https://different.example/v1"}
    assert client.post("/api/v1/connections/models", json=changed).status_code == 422
    assert (
        client.post("/api/v1/connections/models", json={**changed, "api_key": "replacement"}).status_code
        == 422
    )
    assert len(calls) == 1
    assert (
        client.post(
            "/api/v1/connections/models", json={**changed, "api_key": "replacement", "extra_headers": {}}
        ).json()["status"]
        == "ready"
    )
    assert "x-private" not in calls[-1].headers and calls[-1].headers["authorization"] == "Bearer replacement"
    assert client.post("/api/v1/connections/models", json={**same, "protocol": "anthropic-bedrock", "base_url": ""}).json()["status"] == "unsupported"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "protocol,base,path,cursor",
    [
        ("anthropic", "https://example.com", "/v1/models", "after_id"),
        ("anthropic", "https://example.com/v1", "/v1/models", "after_id"),
        ("openai-responses", "https://example.com/v1", "/v1/models", "after"),
    ],
)
def test_paginated_provider_models(monkeypatch, protocol, base, path, cursor):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.path == path
        if protocol == "anthropic":
            assert request.headers["x-api-key"] == "key"
            assert request.headers["anthropic-version"] == "2023-06-01"
        if len(calls) == 1:
            return httpx.Response(
                200, json={"data": [{"id": "a", "display_name": "Model A"}], "has_more": True, "last_id": "a"}
            )
        assert request.url.params[cursor] == "a"
        return httpx.Response(200, json={"data": [{"id": "b"}], "has_more": False})

    provider(monkeypatch, respond)
    result = model_discovery.api_models(
        ModelDiscoveryInput(**draft(protocol=protocol, base_url=base)), {"api_key": "key"}
    )
    assert [row.id for row in result.models] == ["a", "b"] and result.models[0].name == "Model A"


@pytest.mark.parametrize(
    "case,status",
    [
        ("timeout", "unavailable"),
        ("malformed", "unavailable"),
        ("oversized", "unavailable"),
        ("cursor", "unavailable"),
        ("bad-entry", "unavailable"),
        (302, "unavailable"),
        (401, "disconnected"),
        (403, "disconnected"),
        (404, "unsupported"),
        (405, "unsupported"),
        (429, "unavailable"),
        (500, "unavailable"),
    ],
)
def test_discovery_failures_are_bounded_and_do_not_echo_secrets(monkeypatch, case, status):
    calls = []

    def respond(request):
        calls.append(request)
        if case == "timeout":
            raise httpx.ReadTimeout("private-secret")
        if case == "oversized":
            return httpx.Response(200, content=b"x" * (model_discovery.MAX_BYTES + 1))
        if case == "malformed":
            return httpx.Response(200, content=b"private-secret")
        if case == "bad-entry":
            return httpx.Response(200, json={"data": [{"id": {"secret": "private-secret"}}]})
        if case == "cursor":
            return httpx.Response(200, json={"data": [], "has_more": True, "last_id": "same"})
        return httpx.Response(
            case, headers={"Location": "https://other.example/models"}, text="private-secret"
        )

    provider(monkeypatch, respond)
    result = model_discovery.api_models(ModelDiscoveryInput(**draft()), {"api_key": "private-secret"})
    assert result.status == status and not result.models
    assert "private-secret" not in result.model_dump_json() and len(calls) <= 2


def test_missing_key_bedrock_custom_auth_and_endpoint_validation(monkeypatch):
    calls = []
    provider(monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json={"data": []}))
    assert model_discovery.api_models(ModelDiscoveryInput(**draft()), {}).status == "disconnected"
    assert (
        model_discovery.api_models(
            ModelDiscoveryInput(**draft(protocol="anthropic-bedrock", base_url="")), {}
        ).status
        == "unsupported"
    )
    assert not calls
    body = ModelDiscoveryInput(**draft(options={"auth_header": "X-Token"}))
    assert model_discovery.api_models(body, {"api_key": "custom"}).status == "ready"
    assert calls[-1].headers["x-token"] == "custom" and "authorization" not in calls[-1].headers
    monkeypatch.setattr(
        model_discovery, "safe_endpoint", lambda url: (_ for _ in ()).throw(ValueError("Private endpoint"))
    )
    with pytest.raises(ValueError, match="Private endpoint"):
        model_discovery.api_models(body, {"api_key": "custom"})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "base_url,detail",
    [
        ("not-a-url", "Enter a complete provider URL starting with https:// or http://."),
        (
            "https://user:private-secret@example.com/v1",
            "Provider URLs cannot contain credentials, query parameters, or fragments",
        ),
    ],
)
def test_invalid_draft_endpoint_returns_validation_error(seed, client, monkeypatch, base_url, detail):
    monkeypatch.setattr(
        model_discovery.httpx, "Client", lambda **kwargs: pytest.fail("Invalid endpoints must not be called")
    )
    result = client.post("/api/v1/connections/models", json=draft(base_url=base_url, api_key="private-key"))
    assert result.status_code == 422
    assert result.json() == {"detail": detail}


def test_model_visibility_drafts_and_revoked_auth(seed, client, github_stub, monkeypatch):
    identity = client.post("/api/v1/connections", json={"name": "Personal", "kind": "chatgpt"}).json()["id"]
    calls = []
    monkeypatch.setattr(
        runner_client,
        "request",
        lambda *args, **kwargs: (
            calls.append(args)
            or ModelCatalog(source="client", models=[{"id": "native", "name": "Native"}]).model_dump()
        ),
    )
    assert client.get(f"/api/v1/connections/{identity}/models").json()["models"][0]["id"] == "native"
    with transaction() as db:
        db.add(User(id=8, login="other", role="member"))
        db.get(Connection, identity).owner_id = 8
        db.get(Membership, 7).role = "member"
    assert client.post("/api/v1/connections/models", json=draft(api_key="secret")).status_code == 403
    assert client.get(f"/api/v1/connections/{identity}/models").status_code == 403
    assert client.get(f"/api/v1/connections/{identity}/models?repository_id=22").status_code == 403
    assert len(calls) == 1
    with transaction() as db:
        db.add(ConnectionGrant(connection_id=identity, repository_id=22))
    assert client.get(f"/api/v1/connections/{identity}/models?repository_id=22").status_code == 200
    with transaction() as db:
        db.get(Connection, identity).auth_status = "revoked"
    assert (
        client.get(f"/api/v1/connections/{identity}/models?repository_id=22").json()["status"]
        == "disconnected"
    )
    assert len(calls) == 2
    assert client.get("/api/v1/connections/missing/models").status_code == 404


def test_personal_setup_requires_effective_model_and_keeps_run_snapshot(
    seed, client, github_stub, monkeypatch
):
    from test_review_flow import remote

    from code_mortis.integrations import github

    monkeypatch.setattr(github, "request", lambda *args, **kwargs: remote())
    result = client.post("/api/v1/connections", json={"name": "Personal", "kind": "chatgpt"})
    assert result.status_code == 201 and result.json()["model"] == ""
    identity = result.json()["id"]
    client.put(f"/api/v1/connections/{identity}/grants/22", json={"automatic": True})
    assert (
        client.put("/api/v1/repositories/22", json={"enabled": True, "connection_id": identity}).status_code
        == 422
    )
    assert client.post("/api/v1/pulls/pr/runs", json={"connection_id": identity}).status_code == 422
    with transaction() as db:
        repo = db.get(Repository, 22)
        repo.connection_id = identity
        with pytest.raises(ValueError, match="Choose a model"):
            reviews.create_run(db, db.get(PullRequest, "pr"), repo, "automatic")
        assert not db.scalars(select(ReviewRun)).all()
    settings = {"enabled": True, "connection_id": identity, "settings": {"model": "override-model"}}
    assert client.put("/api/v1/repositories/22", json=settings).status_code == 200
    result = client.post("/api/v1/pulls/pr/runs", json={"connection_id": identity})
    assert result.status_code == 202 and result.json()["config"]["connection"]["model"] == "override-model"
    assert (
        client.put(
            f"/api/v1/connections/{identity}",
            json={"name": "Personal", "kind": "chatgpt", "model": "chosen-model"},
        ).status_code
        == 200
    )
    with transaction() as db:
        assert db.get(ReviewRun, result.json()["id"]).config["connection"]["model"] == "override-model"
    assert (
        client.post("/api/v1/connections", json={"name": "API", **draft(), "api_key": "key"}).status_code
        == 422
    )


def test_native_catalogs_are_isolated_and_never_start_a_review(monkeypatch, tmp_path):
    identity = "00000000-0000-4000-8000-000000000010"
    monkeypatch.setattr(native_runner, "ROOT", tmp_path)
    monkeypatch.setattr(native_runner, "locks", {})
    connected, calls = False, []

    async def auth(connection_id, kind):
        assert connection_id == identity
        return {"status": "connected" if connected else "disconnected"}

    class Client:
        async def call(self, method, params):
            assert method == "model/list" and params["includeHidden"] is False
            calls.append(params)
            return {
                "data": [{"model": "b" if params["cursor"] else "a", "displayName": "Native model"}],
                "nextCursor": None if params["cursor"] else "next",
            }

    async def codex(connection_id):
        assert connection_id == identity
        return Client()

    async def claude(connection_id):
        assert connection_id == identity
        return model_discovery.choices([{"id": "claude", "name": "Claude"}])

    monkeypatch.setattr(native_runner, "auth_status", auth)
    monkeypatch.setattr(native_runner, "codex", codex)
    monkeypatch.setattr(native_runner, "claude_models", claude)

    async def check():
        nonlocal connected
        assert (await native_runner.model_catalog(identity, "chatgpt")).status == "disconnected"
        assert not calls
        connected = True
        assert [row.id for row in (await native_runner.model_catalog(identity, "chatgpt")).models] == [
            "a",
            "b",
        ]
        assert (await native_runner.model_catalog(identity, "claude_code")).models[0].id == "claude"
        async with native_runner.locks[identity]:
            assert (await native_runner.model_catalog(identity, "chatgpt")).status == "unavailable"
        assert len(calls) == 2

    asyncio.run(check())


def test_native_catalog_timeout_includes_waiting_for_lock(monkeypatch, tmp_path):
    identity = "00000000-0000-4000-8000-000000000010"
    monkeypatch.setattr(native_runner, "ROOT", tmp_path)
    monkeypatch.setattr(native_runner, "locks", {})
    auth = AsyncMock()
    monkeypatch.setattr(native_runner, "auth_status", auth)
    timeout = asyncio.timeout

    def expire_immediately(delay):
        assert delay == 60
        return timeout(0)

    monkeypatch.setattr(native_runner.asyncio, "timeout", expire_immediately)

    async def check():
        lock = native_runner.locks[identity] = asyncio.Lock()
        await lock.acquire()
        release = asyncio.Event()

        async def competing_operation():
            async with lock:
                await release.wait()

        holder = asyncio.create_task(competing_operation())
        await asyncio.sleep(0)
        lock.release()
        # A queued waiter takes priority even before it resumes and locks this again.
        assert not lock.locked()
        try:
            async with timeout(1):
                result = await native_runner.model_catalog(identity, "chatgpt")
            assert result.status == "unavailable" and not result.models
            assert lock.locked()
            auth.assert_not_awaited()
        finally:
            release.set()
            await holder
        assert not lock.locked()

    asyncio.run(check())


@pytest.mark.parametrize("newline", [b"", b"\n"])
def test_oversized_claude_model_line_is_unavailable_and_stops_process(monkeypatch, tmp_path, newline):
    identity = "00000000-0000-4000-8000-000000000010"
    monkeypatch.setattr(native_runner, "ROOT", tmp_path)
    monkeypatch.setattr(native_runner, "locks", {})
    monkeypatch.setattr(native_runner, "auth_status", AsyncMock(return_value={"status": "connected"}))
    stopped = AsyncMock()
    monkeypatch.setattr(native_runner, "stop", stopped)

    async def check():
        stdout = asyncio.StreamReader(limit=native_runner.MAX_BYTES)
        stdout.feed_data(b"x" * (native_runner.MAX_BYTES + 1) + newline)
        stdout.feed_eof()
        process = SimpleNamespace(stdout=stdout, stdin=SimpleNamespace(write=lambda data: None, drain=AsyncMock()))
        spawn = AsyncMock(return_value=process)
        monkeypatch.setattr(native_runner.asyncio, "create_subprocess_exec", spawn)
        result = await native_runner.model_catalog(identity, "claude_code")
        assert result.status == "unavailable" and not result.models
        assert spawn.call_args.kwargs["limit"] == native_runner.MAX_BYTES
        stopped.assert_awaited_once_with(process)
        assert not native_runner.locks[identity].locked()

    asyncio.run(check())
