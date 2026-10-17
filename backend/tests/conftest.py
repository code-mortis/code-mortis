import os
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import text

os.environ.setdefault(
    "CM_DATABASE_URL", "postgresql+psycopg://mortis:mortis-test-only@127.0.0.1:55439/mortis_test"
)
os.environ.setdefault("CM_REDIS_URL", "redis://127.0.0.1:56389/0")
os.environ["CM_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["CM_BOOTSTRAP_TOKEN"] = "test-bootstrap-token-not-for-deployment"
os.environ["CM_OWNER_GITHUB_LOGIN"] = "owner"
os.environ["CM_PUBLIC_URL"] = "http://testserver"
os.environ["CM_COOKIE_SECURE"] = "false"
os.environ["CM_SENTRY_DSN"] = ""
os.environ["CM_SENTRY_FRONTEND_DSN"] = ""
os.environ["CM_WEBHOOK_URL"] = "https://relay.example.com/github"

from code_mortis.config import settings
from code_mortis.db import Base, engine, now, transaction
from code_mortis.main import app
from code_mortis.models import (
    AppConfig,
    Connection,
    Installation,
    LoginSession,
    Membership,
    PullRequest,
    Repository,
    User,
)
from code_mortis.security import digest, seal


@pytest.fixture(scope="session", autouse=True)
def database():
    assert engine().url.database == "mortis_test", "Tests only run against the isolated mortis_test database"
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine().dispose()


@pytest.fixture(autouse=True)
def clean_database(database):
    with engine().begin() as connection:
        connection.execute(
            text("TRUNCATE " + ", ".join(f'"{table}"' for table in Base.metadata.tables) + " CASCADE")
        )


@pytest.fixture(autouse=True)
def clean_permission_cache():
    assert settings().redis_url == "redis://127.0.0.1:56389/0", "Use the isolated test Redis"
    with Redis.from_url(settings().redis_url) as cache:
        for key in cache.scan_iter("code-mortis:github-permissions:v1:*"):
            cache.delete(key)
        yield
        for key in cache.scan_iter("code-mortis:github-permissions:v1:*"):
            cache.delete(key)


@pytest.fixture
def seed():
    with transaction() as db:
        db.add(
            AppConfig(
                id=1,
                github_app_id="123",
                github_slug="mortis-test",
                client_id="client",
                client_secret=seal("secret"),
                private_key=seal("test"),
                webhook_secret=seal("webhook-secret"),
            )
        )
        user = User(id=7, login="owner", role="admin", access_token=seal("github-user-token"))
        db.add_all(
            [user, Membership(github_id=7, login="owner", role="admin"), Installation(id=10, account="team")]
        )
        connection = Connection(
            id="connection",
            name="Test model",
            provider="openai",
            protocol="openai",
            model="test-model",
            base_url="https://example.com/v1",
            secret=seal('{"api_key":"provider-secret","extra_headers":{}}'),
        )
        db.add(connection)
        db.flush()
        db.add(
            LoginSession(
                token_hash=digest("session"),
                user_id=user.id,
                csrf="csrf",
                expires_at=now() + timedelta(days=1),
            )
        )
        repo = Repository(
            id=22, installation_id=10, full_name="team/project", connection_id=connection.id, enabled=True
        )
        db.add(repo)
        db.flush()
        pr = PullRequest(
            id="pr",
            repository_id=repo.id,
            number=3,
            title="Fix retry logic",
            author="owner",
            url="https://github.com/team/project/pull/3",
            head_sha="a" * 40,
            base_sha="b" * 40,
            head_ref="fix/retries",
        )
        db.add(pr)
        db.flush()
        return {"user": user, "repo": repo, "pr": pr, "connection": connection}


@pytest.fixture
def client():
    with TestClient(app) as client:
        client.cookies.set("cm_session", "session")
        client.headers["x-csrf-token"] = "csrf"
        yield client


@pytest.fixture
def github_stub(monkeypatch):
    from code_mortis import github

    monkeypatch.setattr(
        github, "authorize_repo", lambda *args, **kwargs: {"permissions": {"pull": True, "push": True}}
    )
    monkeypatch.setattr(github, "installation_token", lambda *args, **kwargs: "installation-token")
    monkeypatch.setattr(github, "app_token", lambda *args: "app-jwt")
    monkeypatch.setattr(github, "installation_token_from_jwt", lambda *args, **kwargs: "installation-token")
    monkeypatch.setattr(github, "threads", lambda *args: [])
