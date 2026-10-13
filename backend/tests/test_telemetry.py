import json

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from code_mortis import github, jobs, main, telemetry
from code_mortis.config import Settings, settings
from code_mortis.db import transaction
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


def test_handled_job_failure_is_reported_and_persisted(events, monkeypatch):
    with transaction() as db:
        job = enqueue(db, "publish", {"run_id": "missing"})
        db.flush()
        job_id = job.id

    def fail(*args):
        raise RuntimeError("private-provider-key")

    monkeypatch.setattr(jobs, "execute", fail)
    jobs.execute_job(job_id)
    assert len(events) == 1
    assert events[0]["tags"]["job_id"] == job_id
    assert events[0]["tags"]["job_kind"] == "publish"
    assert "private-provider-key" not in json.dumps(events)
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed" and "private-provider-key" not in job.error


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
