import json
from datetime import timedelta
from unittest.mock import Mock

import httpx
import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from code_mortis import github, jobs, main, telemetry
from code_mortis.config import Settings, settings
from code_mortis.db import now, transaction
from code_mortis.models import Job
from code_mortis.reviews import enqueue

DSN = "https://public@sentry.example.com:8443/1"


@pytest.fixture
def events(monkeypatch):
    config = settings().model_copy(update={"sentry_dsn": DSN, "sentry_environment": "test"})
    monkeypatch.setattr(telemetry, "settings", lambda: config)
    captured = []

    class TestTransport(Transport):
        def capture_envelope(self, envelope):
            event = envelope.get_event()
            if event is not None:
                captured.append(event)

    original_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: original_init(**kwargs, transport=TestTransport))
    global_scope = sentry_sdk.get_global_scope()
    previous_client = global_scope.client
    with sentry_sdk.isolation_scope(), sentry_sdk.new_scope():
        telemetry.init_sentry("api")
        try:
            yield captured
        finally:
            sentry_sdk.get_client().close()
            global_scope.set_client(previous_client)


def test_sentry_is_not_initialized_without_a_dsn(monkeypatch):
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    telemetry.init_sentry("worker")
    assert calls == []


def test_captured_errors_keep_stacks_without_secrets(events):
    secret = "private-provider-key"
    sentry_sdk.set_user({"email": secret})
    sentry_sdk.set_extra("payload", {"api_key": secret})
    sentry_sdk.add_breadcrumb(message=secret)
    try:
        raise RuntimeError(secret)
    except RuntimeError as error:
        sentry_sdk.capture_exception(error)
    assert len(events) == 1
    event = events[0]
    assert secret not in json.dumps(event)
    exception = event["exception"]["values"][0]
    assert exception["type"] == "RuntimeError" and exception["stacktrace"]["frames"]
    assert all("vars" not in frame for frame in exception["stacktrace"]["frames"])
    assert event["environment"] == "test" and event["tags"]["service"] == "api"


@pytest.mark.parametrize("error,status", [(RuntimeError("private-provider-key"), 500), (github.GitHubError(503, "private-provider-key"), 502)])
def test_api_failure_is_reported_once_with_request_reference(events, seed, client, monkeypatch, error, status):
    def fail(*args):
        raise error

    monkeypatch.setattr(github, "visible_repositories", fail)
    with TestClient(main.app, raise_server_exceptions=False) as browser:
        browser.cookies.update(client.cookies)
        response = browser.get("/api/v1/repositories?token=private-provider-key")
    assert response.status_code == status and len(events) == 1
    assert events[0]["tags"]["request_id"] == response.headers["x-request-id"]
    assert "private-provider-key" not in json.dumps(events)


@pytest.mark.parametrize("attempt", [1, 2, 3])
@pytest.mark.parametrize("error,status", [
    (RuntimeError("private-provider-key"), "failed"),
    (github.GitHubError(503, "GitHub could not be reached. Try again shortly."), "queued"),
    (github.GitHubError(404, "The repository or resource is no longer accessible."), "failed"),
])
def test_handled_job_failure_is_reported_and_persisted(events, monkeypatch, attempt, error, status):
    with transaction() as db:
        job = enqueue(db, "publish", {"run_id": "missing"})
        job.attempts = attempt - 1
        db.flush()
        job_id = job.id

    def fail(*args):
        raise error

    monkeypatch.setattr(jobs, "execute", fail)
    jobs.execute_job(job_id)
    assert len(events) == 1
    assert events[0]["tags"]["job_id"] == job_id
    assert events[0]["tags"]["job_kind"] == "publish"
    assert events[0]["tags"]["job_attempt"] == attempt
    if isinstance(error, github.GitHubError):
        assert events[0]["tags"]["github_status"] == error.status
    else:
        assert "github_status" not in events[0]["tags"]
    assert "private-provider-key" not in json.dumps(events)
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == ("failed" if attempt == 3 else status) and job.attempts == attempt
        assert "private-provider-key" not in job.error


@pytest.mark.parametrize("failure,delay,calls", [
    (httpx.Response(503, json={"message": "private-provider-key"}), 60, 2),
    (httpx.Response(503, headers={"Retry-After": "180"}, json={"message": "private-provider-key"}), 180, 1),
    (httpx.RemoteProtocolError("private-provider-key"), 60, 2),
])
def test_github_failure_preserves_durable_job_recovery(events, monkeypatch, failure, delay, calls):
    request = Mock(side_effect=[failure] * calls + [httpx.Response(200, json={"number": 3})])
    sleep = Mock()
    monkeypatch.setattr(github.httpx, "request", request)
    monkeypatch.setattr(github.time, "sleep", sleep)
    monkeypatch.setattr(jobs, "execute", lambda *args: github.request(
        "GET", "/repos/team/project/pulls/3", "installation-token",
    ))
    with transaction() as db:
        job_id = enqueue(db, "sync_pr", {"repository_id": 22, "number": 3}).id

    started = now()
    jobs.execute_job(job_id)
    assert request.call_count == calls
    assert sleep.call_count == calls - 1
    assert len(events) == 1
    assert events[0]["tags"]["job_attempt"] == 1
    assert events[0]["tags"]["github_status"] == 503
    assert "private-provider-key" not in json.dumps(events)
    if isinstance(failure, httpx.RemoteProtocolError):
        exceptions = events[0]["exception"]["values"]
        assert [error["type"] for error in exceptions] == ["RemoteProtocolError", "GitHubError"]
        assert all(error["value"] == "[redacted]" for error in exceptions)
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == "queued" and job.attempts == 1
        assert job.lease_until is None and job.dispatched_at is None
        assert job.available_at >= started + timedelta(seconds=delay)
        assert job.available_at <= now() + timedelta(seconds=delay)
        assert "private-provider-key" not in job.error
        job.available_at = now()

    jobs.execute_job(job_id)
    assert request.call_count == calls + 1 and len(events) == 1
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == "done" and job.attempts == 2 and job.error is None


def test_browser_csp_allows_only_the_configured_sentry_origin(client, monkeypatch):
    config = settings().model_copy(update={"sentry_frontend_dsn": DSN})
    monkeypatch.setattr(main, "settings", lambda: config)
    policy = client.get("/health/live").headers["content-security-policy"]
    assert "connect-src 'self' https://sentry.example.com:8443;" in policy
    assert "public@" not in policy
    monkeypatch.setattr(main, "settings", settings)
    assert "connect-src 'self';" in client.get("/health/live").headers["content-security-policy"]


@pytest.mark.parametrize("value", ["invalid", "https://sentry.example.com/1", "https://public:secret@sentry.example.com/1"])
def test_sentry_rejects_invalid_or_private_dsns(value):
    with pytest.raises(ValueError):
        Settings(**{**settings().model_dump(), "sentry_frontend_dsn": value}, _env_file=None)


def test_worker_child_initializes_sentry(monkeypatch):
    services = []
    monkeypatch.setattr(jobs, "init_sentry", services.append)
    jobs.init_worker_sentry()
    assert services == ["worker"]
