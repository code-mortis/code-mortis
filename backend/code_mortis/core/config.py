from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mortis:mortis@localhost:5432/mortis"
    redis_url: str = "redis://localhost:6379/0"
    public_url: str = "http://localhost:8080"
    webhook_url: str = ""
    code_graph_store_dir: Path = Path("/var/lib/code-mortis/code-graph")
    encryption_key: str
    bootstrap_token: str = Field(min_length=24)
    owner_github_login: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}$")
    cookie_secure: bool = True
    session_days: int = Field(default=7, ge=1, le=30)
    trivy_binary: str = "trivy"
    trivy_version: str = "0.76.0"
    trivy_cache_dir: Path = Path("/tmp/code-mortis-trivy")
    work_dir: Path = Path("/tmp/code-mortis-runs")
    frontend_dir: Path = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    allow_private_endpoints: bool = False
    trusted_endpoint_hosts: str = ""
    max_webhook_bytes: int = 10_000_000
    sentry_dsn: str = ""
    sentry_frontend_dsn: str = ""
    sentry_environment: str = "production"
    sentry_release: str = ""

    @field_validator("sentry_dsn", "sentry_frontend_dsn")
    @classmethod
    def clean_sentry_dsn(cls, value: str) -> str:
        from sentry_sdk.utils import Dsn

        if not value:
            return value
        dsn = Dsn(value)
        if dsn.secret_key:
            raise ValueError("Use a public Sentry DSN without a secret key")
        return str(dsn)

    @property
    def sentry_frontend_origin(self) -> str:
        from urllib.parse import urlsplit

        if not self.sentry_frontend_dsn:
            return ""
        parsed = urlsplit(self.sentry_frontend_dsn)
        return f"{parsed.scheme}://{parsed.netloc.rsplit('@', 1)[-1]}"

    @field_validator("public_url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("public_url must be an HTTP(S) origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("public_url must not include a path, query, or fragment")
        return value.rstrip("/")

    @field_validator("webhook_url")
    @classmethod
    def clean_webhook_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "CM_WEBHOOK_URL must be a public HTTPS URL without credentials, query, or fragment"
            )
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("CM_WEBHOOK_URL must be reachable by GitHub, not localhost")
        return value

    @property
    def github_webhook_url(self) -> str:
        return self.webhook_url or self.public_url + "/api/v1/webhooks/github"

    def diagnostics(self) -> list[str]:
        from urllib.parse import urlsplit

        errors = []
        if self.cookie_secure != (urlsplit(self.public_url).scheme == "https"):
            errors.append("CM_COOKIE_SECURE must be true for HTTPS and false for local HTTP development.")
        if not self.github_webhook_url.startswith("https://"):
            errors.append("Set CM_WEBHOOK_URL to a public HTTPS webhook relay for local development.")
        return errors


@lru_cache
def settings() -> Settings:
    return Settings()
