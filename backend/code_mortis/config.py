from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mortis:mortis@localhost:5432/mortis"
    redis_url: str = "redis://localhost:6379/0"
    public_url: str = "http://localhost:8080"
    encryption_key: str
    bootstrap_token: str = Field(min_length=24)
    owner_github_login: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}$")
    cookie_secure: bool = True
    session_days: int = Field(default=7, ge=1, le=30)
    ocr_binary: str = "ocr"
    ocr_version: str = "1.12.11"
    ocr_isolated_runtime: bool = False
    work_dir: Path = Path("/tmp/code-mortis-runs")
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    allow_private_endpoints: bool = False
    trusted_endpoint_hosts: str = ""
    max_webhook_bytes: int = 10_000_000

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


@lru_cache
def settings() -> Settings:
    return Settings()
