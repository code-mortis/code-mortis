from datetime import datetime

from pydantic import Field, StrictInt, field_validator

from code_mortis.schemas.common import StrictModel


class ApiTokenCreateInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=20)
    repository_ids: list[StrictInt] = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=30, ge=1, le=365)

    @field_validator("name")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a token name.")
        return value.strip()

    @field_validator("scopes")
    @classmethod
    def known_scopes(cls, values: list[str]) -> list[str]:
        from code_mortis.api_tokens import policy

        unknown = set(values) - set(policy.SCOPES)
        if unknown or len(set(values)) != len(values):
            raise ValueError("Select valid API token permissions without duplicates.")
        return sorted(set(values))

    @field_validator("repository_ids")
    @classmethod
    def valid_repositories(cls, values: list[int]) -> list[int]:
        if any(type(value) is not int or value < 1 for value in values) or len(set(values)) != len(values):
            raise ValueError("Select valid repositories without duplicates.")
        return sorted(set(values))


class ApiTokenScope(StrictModel):
    id: str
    label: str
    description: str


class ApiTokenOut(StrictModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    repository_ids: list[int]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    status: str


class ApiTokenPage(StrictModel):
    items: list[ApiTokenOut]
    total: int
    page: int
    has_more: bool
    active_count: int
    permissions: list[ApiTokenScope]
    default_expiration_days: int = 30
    max_expiration_days: int = 365
    reauth_required: bool


class ApiTokenCreated(StrictModel):
    token: str
    item: ApiTokenOut


class ApiTokenUser(StrictModel):
    """Identity for PAT callers: no session values, and never a csrf field."""

    id: int
    login: str
    avatar_url: str
    role: str


class ApiTokenIdentity(StrictModel):
    user: ApiTokenUser
    token: ApiTokenOut
