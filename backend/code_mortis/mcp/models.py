"""Persistent OAuth grants and durable MCP command records."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from code_mortis.core.db import Base, now, uid


class McpClient(Base):
    __tablename__ = "mcp_clients"
    client_id: Mapped[str] = mapped_column(String(2048), primary_key=True)
    client_name: Mapped[str] = mapped_column(String(100))
    redirect_uris: Mapped[list[str]] = mapped_column(JSON)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    def get_client_id(self):
        return self.client_id

    def get_default_redirect_uri(self):
        return None

    def check_redirect_uri(self, redirect_uri):
        return redirect_uri in self.redirect_uris

    def check_client_secret(self, client_secret):
        return False

    def check_endpoint_auth_method(self, method, endpoint):
        return method == "none"

    def check_response_type(self, response_type):
        return response_type == "code"

    def check_grant_type(self, grant_type):
        return grant_type in {"authorization_code", "refresh_token"}

    def get_allowed_scope(self, scope):
        return scope


class McpGrant(Base):
    __tablename__ = "mcp_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("mcp_clients.client_id"), index=True)
    domain: Mapped[str] = mapped_column(String(20))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    repository_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    repository_ids_by_group: Mapped[dict[str, list[int]]] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpAuthorization(Base):
    __tablename__ = "mcp_authorizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    client_id: Mapped[str] = mapped_column(ForeignKey("mcp_clients.client_id"))
    domain: Mapped[str] = mapped_column(String(20))
    redirect_uri: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(Text)
    browser_hash: Mapped[str] = mapped_column(String(64))
    session_hash: Mapped[str | None] = mapped_column(String(64))
    code_challenge: Mapped[str] = mapped_column(String(128))
    code_challenge_method: Mapped[str] = mapped_column(String(10), default="S256")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    grant_id: Mapped[str | None] = mapped_column(ForeignKey("mcp_grants.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def get_redirect_uri(self):
        return self.redirect_uri

    def get_scope(self):
        return " ".join(self.scopes)


class McpToken(Base):
    __tablename__ = "mcp_tokens"
    access_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey("mcp_grants.id", ondelete="CASCADE"), index=True)
    scopes: Mapped[list[str]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    refresh_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpOperation(Base):
    __tablename__ = "mcp_operations"
    __table_args__ = (UniqueConstraint("grant_id", "tool", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    grant_id: Mapped[str] = mapped_column(ForeignKey("mcp_grants.id"), index=True)
    tool: Mapped[str] = mapped_column(String(100))
    request_key: Mapped[str] = mapped_column(String(100))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class McpApproval(Base):
    __tablename__ = "mcp_approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    grant_id: Mapped[str] = mapped_column(ForeignKey("mcp_grants.id"), index=True)
    tool: Mapped[str] = mapped_column(String(100))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    denied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpAudit(Base):
    __tablename__ = "mcp_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    grant_id: Mapped[str] = mapped_column(ForeignKey("mcp_grants.id"), index=True)
    tool: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(30))
    duration_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
