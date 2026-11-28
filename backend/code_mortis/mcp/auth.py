"""OAuth and per-command authorization for the Code Mortis MCP resource."""

import hmac
import http.client
import ipaddress
import json
import logging
import re
import secrets
import socket
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from authlib.oauth2.rfc6749 import AuthorizationServer, OAuth2Error, OAuth2Request, grants
from authlib.oauth2.rfc6749.errors import InvalidRequestError
from authlib.oauth2.rfc6749.requests import BasicOAuth2Payload
from authlib.oauth2.rfc7636 import CodeChallenge
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import Field, field_validator
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select, update
from starlette.concurrency import run_in_threadpool

from code_mortis.api.dependencies import ADMIN, DB, USER, current_user
from code_mortis.core.config import settings
from code_mortis.core.db import get_db, now, transaction, uid
from code_mortis.core.security import digest, redact_saved_credentials
from code_mortis.integrations import github
from code_mortis.mcp.models import (
    McpApproval,
    McpAudit,
    McpAuthorization,
    McpClient,
    McpGrant,
    McpOperation,
    McpToken,
)
from code_mortis.models import (
    AppConfig,
    CodeGraphRepository,
    Finding,
    Membership,
    PullRequest,
    ReviewRun,
    SecurityFinding,
    SecurityScan,
    User,
)
from code_mortis.schemas.common import StrictModel

router = APIRouter(tags=["MCP"])
logger = logging.getLogger(__name__)
UNIFIED = "unified"
McpGroup = Literal["reviews", "security", "code", "management"]
DOMAIN_SCOPES = {
    "code": {
        "code:read": ("Read code", "Read indexed definitions, file dependencies and source at exact commits in selected repositories. No indexing, model runs or writes."),
    },
    "reviews": {
        "reviews:read": ("Read reviews", "Read pull requests, runs, findings, coverage and history."),
        "reviews:write": ("Update reviews", "Resolve or reopen findings, ignore pull requests and synchronize GitHub updates."),
        "reviews:run": ("Run reviews", "Consume the selected AI connection, run or stop reviews and publish review comments."),
        "reviews:manage": ("Configure reviews", "Change repository review settings with administrator access."),
    },
    "security": {
        "security:read": ("Read security", "Read scans, findings, evidence, coverage and reports."),
        "security:write": ("Triage findings", "Accept risk, mark false positives, reopen findings and acknowledge secret rotation."),
        "security:run": ("Run security scans", "Consume the selected AI connection and run, retry or stop scans."),
        "security:issues": ("Publish security issues", "Preview, publish and recover GitHub issues with the required approvals."),
    },
}
MANAGEMENT_SCOPES = {
    "instance:manage": ("Manage this instance", "Manage members and GitHub installations with administrator access."),
    "connections:manage": ("Manage AI connections", "Manage permitted connections and your personal connection consent."),
}
GROUP_SCOPES = {**DOMAIN_SCOPES, "management": MANAGEMENT_SCOPES}
SCOPE_GROUP = {scope: group for group, scopes in GROUP_SCOPES.items() for scope in scopes}
ADMIN_SCOPES = {"reviews:manage", "instance:manage"}
DEFAULT_SETTINGS = {"enabled": False, "reviews_enabled": False, "security_enabled": False, "code_enabled": False, "management_enabled": False,
                    "allowed_client_domains": []}


class McpSettingsInput(StrictModel):
    enabled: bool = False
    reviews_enabled: bool = False
    security_enabled: bool = False
    code_enabled: bool = False
    management_enabled: bool = False
    allowed_client_domains: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allowed_client_domains")
    @classmethod
    def domains(cls, values):
        result = []
        for value in values:
            value = value.strip().lower()
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value) or ".." in value:
                raise ValueError("Enter exact client hostnames without schemes, paths or wildcards.")
            result.append(value)
        return sorted(set(result))


def redirect_uri(value):
    parsed = urlsplit(value)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value)
        or not parsed.hostname or parsed.username or parsed.password or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or any(key in {"code", "state", "iss", "error", "error_description"} for key, _ in parse_qsl(parsed.query))):
        raise ValueError("Use an exact HTTPS callback or a local loopback HTTP callback without credentials or fragments.")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("The callback port is invalid.") from exc
    return value


class McpClientInput(StrictModel):
    client_id: str = Field(min_length=1, max_length=2048, pattern=r"^[^\s\x00-\x1f]+$")
    client_name: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f]+$")
    redirect_uris: list[str] = Field(min_length=1, max_length=10)

    @field_validator("redirect_uris")
    @classmethod
    def redirects(cls, values):
        return list(dict.fromkeys(redirect_uri(value) for value in values))


class McpRegistrationInput(StrictModel):
    client_name: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f]+$")
    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    token_endpoint_auth_method: Literal["none"] = "none"
    grant_types: list[Literal["authorization_code", "refresh_token"]] = Field(default_factory=lambda: ["authorization_code", "refresh_token"], max_length=2)
    response_types: list[Literal["code"]] = Field(default_factory=lambda: ["code"], max_length=1)
    application_type: Literal["native", "web"] | None = None
    scope: str = Field(default="", max_length=2000)

    @field_validator("redirect_uris")
    @classmethod
    def redirects(cls, values):
        return list(dict.fromkeys(redirect_uri(value) for value in values))


class McpConsentInput(StrictModel):
    request_id: str = Field(min_length=1, max_length=36)
    repository_ids_by_group: dict[McpGroup, list[int]] = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list, max_length=len(SCOPE_GROUP))
    approve: bool

    @field_validator("repository_ids_by_group")
    @classmethod
    def limit_repositories(cls, values):
        if any(len(ids) > 100 for ids in values.values()):
            raise ValueError("Select at most 100 repositories per tool group.")
        return values


class McpScopeOption(StrictModel):
    name: str
    label: str
    description: str
    group: McpGroup


class McpRepositoryOption(StrictModel):
    id: int
    full_name: str


class McpConsentPreview(StrictModel):
    request_id: str
    client_id: str
    client_name: str
    redirect_uri: str
    domain: Literal["unified"]
    expires_at: datetime
    scopes: list[McpScopeOption]
    repositories_by_group: dict[McpGroup, list[McpRepositoryOption]]


class McpGrantSummary(StrictModel):
    id: str
    client_id: str
    client_name: str
    domain: Literal["reviews", "security", "code", "unified"]
    scopes: list[str]
    repository_ids: list[int]
    repository_ids_by_group: dict[McpGroup, list[int]]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class McpSettingsView(McpSettingsInput):
    public_url: str
    can_manage: bool


class McpApprovalInput(StrictModel):
    approve: bool


def resource_url():
    return settings().public_url + "/mcp"


def mcp_settings(db):
    config = db.get(AppConfig, 1)
    return {**DEFAULT_SETTINGS, **(config.mcp_settings if config else {})}


def enabled_groups(config):
    return {group for group in GROUP_SCOPES if config["enabled"] and config[group + "_enabled"]}


def scoped_permissions(groups, role=None):
    return {
        scope: labels
        for group in GROUP_SCOPES if group in groups
        for scope, labels in GROUP_SCOPES[group].items()
        if role in (None, "admin") or scope not in ADMIN_SCOPES
    }


def grant_repository_ids(grant, group):
    return (grant.repository_ids_by_group or {}).get(group, [])


def rate_limit(category, identity, limit=120):
    key = f"code-mortis:mcp:{category}:{digest(identity)}:{int(time.time()) // 60}"
    try:
        with Redis.from_url(settings().redis_url, socket_timeout=2) as cache:
            with cache.pipeline() as pipe:
                count, _ = pipe.incr(key).expire(key, 120).execute()
    except RedisError as exc:
        raise HTTPException(503, "MCP authorization is temporarily unavailable. Try again shortly.") from exc
    if count > limit:
        raise HTTPException(429, "Too many MCP requests. Wait a minute and try again.", headers={"Retry-After": "60"})


def revoke_grant(db, grant):
    if not grant.revoked_at:
        grant.revoked_at = now()
        from code_mortis.mcp.provenance import revoke_work

        revoke_work(db, grant.id)


def load_grant(db, grant_id, *, lock=False):
    query = select(McpGrant).where(McpGrant.id == grant_id).execution_options(populate_existing=True)
    grant = db.scalar(query.with_for_update() if lock else query)
    if not grant or grant.domain != UNIFIED or grant.revoked_at or grant.expires_at <= now():
        raise HTTPException(401, "This MCP authorization expired or was revoked. Connect again.")
    user = db.get(User, grant.user_id)
    membership = db.get(Membership, grant.user_id)
    if not user or not user.enabled or not membership:
        raise HTTPException(403, "This account no longer has access to Code Mortis.")
    user.role = membership.role
    groups = enabled_groups(mcp_settings(db))
    scopes = set(grant.scopes) & set(scoped_permissions(groups, user.role))
    if not scopes:
        raise HTTPException(403, "This MCP authorization has no enabled permissions.")
    return user, grant, scopes


def ensure_grant(db, grant_id, domain=None, scope=None, repository_id=None, lock=False):
    user, grant, scopes = load_grant(db, grant_id, lock=lock)
    if scope and (scope not in scopes or (domain is not None and SCOPE_GROUP[scope] != domain)):
        raise HTTPException(403, "This MCP client was not granted the required permission.")
    group = domain or (SCOPE_GROUP[scope] if scope else None)
    if group and not any(SCOPE_GROUP[permitted] == group for permitted in scopes):
        raise HTTPException(403, "MCP access is disabled for this tool group.")
    selected = grant_repository_ids(grant, group)
    if repository_id is not None and repository_id not in selected:
        raise HTTPException(403, "This repository was not selected for this MCP client.")
    user.mcp_repository_ids = frozenset(selected)
    user.mcp_grant_id = grant.id
    return user, grant


class McpTokenVerifier(TokenVerifier):
    async def verify_token(self, token):
        if len(token) > 200:
            return None
        return await run_in_threadpool(self._verify_token, token)

    def _verify_token(self, token):
        with transaction() as db:
            record = db.get(McpToken, digest(token))
            if not record or record.expires_at <= now():
                return None
            try:
                user, grant, allowed = load_grant(db, record.grant_id)
            except HTTPException:
                return None
            scopes = sorted(set(record.scopes) & allowed)
            if not scopes:
                return None
            timestamp = now()
            cutoff = timestamp - timedelta(minutes=1)
            if not grant.last_used_at or grant.last_used_at <= cutoff:
                # Discovery can skip the timestamp when a command already holds the grant lock.
                unlocked = db.scalar(select(McpGrant).where(McpGrant.id == grant.id)
                    .execution_options(populate_existing=True).with_for_update(skip_locked=True))
                if unlocked and (not unlocked.last_used_at or unlocked.last_used_at <= cutoff):
                    unlocked.last_used_at = timestamp
            logger.info("MCP authenticated client_fingerprint=%s grant_id=%s", digest(grant.client_id), grant.id)
            return AccessToken(token=token, client_id=grant.client_id, subject=str(user.id),
                scopes=scopes, expires_at=int(record.expires_at.timestamp()),
                resource=resource_url(), claims={"iss": settings().public_url, "grant_id": grant.id})


def tool_access(db, domain, scope, repository_id=None):
    token = get_access_token()
    if not token or scope not in token.scopes:
        raise HTTPException(403, "This MCP client was not granted the required permission.")
    record = db.get(McpToken, digest(token.token))
    if not record or record.expires_at <= now() or scope not in record.scopes:
        raise HTTPException(401, "The MCP access token expired. Refresh the authorization.")
    return ensure_grant(db, record.grant_id, domain, scope, repository_id)


def public_result(value):
    from pydantic import BaseModel

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): public_result(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_result(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("MCP results must use the public response schemas.")


def check_cached_repository_access(db, user, group, arguments):
    from code_mortis.api.dependencies import repo_access

    repository_id = arguments.get("repository_id")
    if repository_id is None:
        target = None
        if arguments.get("pr_id"):
            target = db.get(PullRequest, arguments["pr_id"])
        elif arguments.get("run_id"):
            run = db.get(ReviewRun, arguments["run_id"])
            target = db.get(PullRequest, run.pr_id) if run else None
        elif arguments.get("scan_id"):
            target = db.get(SecurityScan, arguments["scan_id"])
        elif arguments.get("finding_id"):
            finding = db.get(SecurityFinding if group == "security" else Finding, arguments["finding_id"])
            if finding:
                target = finding if group == "security" else db.get(PullRequest, finding.pr_id)
        if target is not None:
            repository_id = target.repository_id
        elif any(arguments.get(key) for key in ("pr_id", "run_id", "scan_id", "finding_id")):
            raise HTTPException(404, "The original command target is no longer available.")
    if repository_id is not None:
        repo_access(db, user, repository_id)


def redact_tool_text(db, text, grant=None, group=None):
    return redact_saved_credentials(db, text, grant_repository_ids(grant, group) if grant else None)


def validate_public_arguments(db, grant, group, arguments):
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    if redact_tool_text(db, text, grant, group) != text:
        raise HTTPException(422, "Remove credential material from the tool arguments before continuing.")


def invoke_tool(domain, scope, name, arguments, callback, *, request_key=None, approval=False):
    from code_mortis.mcp.provenance import execution_context

    started, grant_id = time.monotonic(), None
    try:
        # Match browser transactions: disconnect commits revocation before native logout.
        with contextmanager(get_db)() as db:
            user, grant = tool_access(db, domain, scope)
            grant_id = grant.id
            rate_limit("tools", grant.id)
            # Serialize writes with revocation and duplicate command delivery.
            if request_key or approval:
                user, grant = ensure_grant(db, grant.id, domain, scope, lock=True)
            payload = {key: value for key, value in arguments.items() if key not in {"approval_id", "request_key"}}
            fingerprint = digest(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
            operation = None
            if request_key:
                if not 16 <= len(request_key) <= 100:
                    raise HTTPException(422, "Use a request key between 16 and 100 characters.")
                operation = db.scalar(select(McpOperation).where(McpOperation.grant_id == grant.id,
                    McpOperation.tool == name, McpOperation.request_key == request_key))
                if operation:
                    if operation.arguments_hash != fingerprint:
                        raise HTTPException(409, "This request key was already used with different arguments.")
                    check_cached_repository_access(db, user, domain, payload)
                    db.add(McpAudit(grant_id=grant.id, tool=name, outcome="replayed",
                        duration_ms=int((time.monotonic() - started) * 1000)))
                    return operation.result
            if approval:
                validate_public_arguments(db, grant, domain, payload)
                approval_id = arguments.get("approval_id")
                decision = db.scalar(select(McpApproval).where(McpApproval.id == approval_id,
                    McpApproval.grant_id == grant.id).with_for_update()) if approval_id else None
                if approval_id and (not decision or decision.tool != name or decision.arguments_hash != fingerprint
                    or decision.expires_at <= now() or decision.denied_at or decision.consumed_at):
                    raise HTTPException(409, "The approval expired or the action changed. Review a new preview.")
                if not decision:
                    decision = McpApproval(id=uid(), grant_id=grant.id, tool=name, arguments=payload,
                        arguments_hash=fingerprint, expires_at=now() + timedelta(minutes=5))
                    db.add(decision)
                if not decision.approved_at:
                    db.add(McpAudit(grant_id=grant.id, tool=name, outcome="approval_required",
                        duration_ms=int((time.monotonic() - started) * 1000)))
                    return {"status": "approval_required", "approval_id": decision.id,
                        "approval_url": settings().public_url + "/mcp/approvals/" + decision.id,
                        "message": "Review and approve this exact action in Code Mortis, then retry with this approval_id."}
                decision.consumed_at = now()
            marker = execution_context.set({"grant_id": grant.id, "domain": domain, "scope": scope})
            try:
                result = public_result(callback(db, user))
            finally:
                execution_context.reset(marker)
            if not isinstance(result, dict):
                result = {"items": result} if isinstance(result, list) else {"result": result}
            serialized = json.dumps(result, ensure_ascii=False)
            if redact_tool_text(db, serialized, grant, domain) != serialized:
                raise HTTPException(422, "This result contains credential material. Inspect its redacted details in Code Mortis.")
            if len(serialized.encode("utf-8")) > 262144:
                raise HTTPException(413, "The result is too large. Use pagination, narrower filters or a detail tool.")
            if request_key:
                db.add(McpOperation(grant_id=grant.id, tool=name, request_key=request_key,
                    arguments_hash=fingerprint, result=result))
            db.add(McpAudit(grant_id=grant.id, tool=name, outcome="succeeded",
                duration_ms=int((time.monotonic() - started) * 1000)))
            return result
    except Exception:
        if grant_id:
            with transaction() as audit_db:
                audit_db.add(McpAudit(grant_id=grant_id, tool=name, outcome="failed",
                    duration_ms=int((time.monotonic() - started) * 1000)))
        raise


def fetch_client_metadata(client_id, allowed_domains):
    try:
        parsed = urlsplit(client_id)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(400, "The client metadata URL is invalid.") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in allowed_domains
        or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path in {"", "/"}
        or port not in {None, 443}):
        raise HTTPException(400, "This client metadata URL is not permitted. Ask an administrator to register the client or allow its exact hostname.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("Protected metadata address")
        # Pin the checked IP while retaining TLS hostname verification; never follow redirects.
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=5)
        connection.sock = ssl.create_default_context().wrap_socket(
            socket.create_connection((addresses[0][4][0], 443), timeout=5), server_hostname=parsed.hostname)
        try:
            connection.request("GET", parsed.path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise ValueError("Invalid metadata response")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Invalid metadata document")
        finally:
            connection.close()
        if data.get("client_id") != client_id or data.get("token_endpoint_auth_method", "none") != "none":
            raise ValueError("Client identity does not match")
        client = McpClientInput(client_id=client_id, client_name=data["client_name"], redirect_uris=data["redirect_uris"])
        allowed = {"localhost", "127.0.0.1", "::1", *allowed_domains}
        if any(urlsplit(uri).hostname not in allowed for uri in client.redirect_uris):
            raise ValueError("Client callback hostname is not allowed")
        return client
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as exc:
        raise HTTPException(400, "The client metadata could not be safely verified.") from exc


def resolve_client(db, client_id):
    client = db.get(McpClient, client_id)
    if client and client.revoked_at:
        raise HTTPException(400, "This OAuth client was removed. Ask an administrator to register a new client.")
    if client and not client.metadata_managed:
        return client
    if client_id.startswith("https://"):
        data = fetch_client_metadata(client_id, mcp_settings(db)["allowed_client_domains"])
        if client:
            client.client_name, client.redirect_uris = data.client_name, data.redirect_uris
        else:
            client = McpClient(**data.model_dump(), metadata_managed=True)
            db.add(client)
        db.flush()
    if not client:
        raise HTTPException(400, "This OAuth client is not registered. Register it in Settings → MCP first.")
    return client


class ProtocolRequest(OAuth2Request):
    def __init__(self, method, data):
        origin = settings().public_url
        if not origin.startswith("https://"):
            if urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
                raise HTTPException(503, "MCP OAuth requires an HTTPS public URL.")
            # Authlib's protocol parser requires HTTPS; local transport is permitted only above.
            origin = "https://localhost"
        super().__init__(method, origin + "/oauth/" + ("authorize" if method == "GET" else "token"))
        self.payload = BasicOAuth2Payload(data)
        self._values = data

    @property
    def form(self):
        return self._values

    @property
    def args(self):
        return self._values


class S256Challenge(CodeChallenge):
    SUPPORTED_CODE_CHALLENGE_METHOD = ["S256"]

    def validate_code_challenge(self, grant, redirect_uri):
        if (not re.fullmatch(r"[A-Za-z0-9_-]{43}", grant.request.payload.data.get("code_challenge", ""))
            or grant.request.payload.data.get("code_challenge_method") != "S256"):
            raise InvalidRequestError("PKCE S256 is required.")
        super().validate_code_challenge(grant, redirect_uri)


class CodeGrant(grants.AuthorizationCodeGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]

    def query_authorization_code(self, code, client):
        row = self.server.db.scalar(select(McpAuthorization).where(McpAuthorization.code_hash == digest(code),
            McpAuthorization.client_id == client.client_id).with_for_update())
        if not row:
            return None
        if row.consumed_at:
            if row.grant_id:
                revoke_grant(self.server.db, self.server.db.get(McpGrant, row.grant_id))
            return None
        if row.domain != UNIFIED or row.expires_at <= now() or self.request.form.get("resource") != resource_url():
            return None
        try:
            _, self.server.active_grant, available = load_grant(self.server.db, row.grant_id, lock=True)
            if not set(row.scopes) <= available:
                return None
        except HTTPException:
            return None
        return row

    def authenticate_user(self, authorization_code):
        return self.server.db.get(User, self.server.active_grant.user_id)

    def delete_authorization_code(self, authorization_code):
        authorization_code.consumed_at = now()


class RefreshCredential:
    def __init__(self, token, grant, scopes):
        self.token, self.grant = token, grant
        self.scopes = scopes

    def check_client(self, client):
        return self.grant.client_id == client.client_id

    def get_scope(self):
        return " ".join(self.scopes)


class RefreshGrant(grants.RefreshTokenGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]
    INCLUDE_NEW_REFRESH_TOKEN = True

    def authenticate_refresh_token(self, refresh_token):
        row = self.server.db.scalar(select(McpToken).where(McpToken.refresh_hash == digest(refresh_token)).with_for_update())
        if not row:
            return None
        if self.request.form.get("resource") != resource_url():
            return None
        try:
            _, grant, available = load_grant(self.server.db, row.grant_id, lock=True)
        except HTTPException:
            return None
        if grant.client_id != self.request.client.client_id:
            return None
        if row.refresh_used_at:
            revoke_grant(self.server.db, grant)
            return None
        scopes = sorted(set(row.scopes) & available)
        if not scopes:
            return None
        self.server.active_grant = grant
        return RefreshCredential(row, grant, scopes)

    def authenticate_user(self, refresh_token):
        return self.server.db.get(User, refresh_token.grant.user_id)

    def revoke_old_credential(self, refresh_token):
        refresh_token.token.refresh_used_at = now()


class McpAuthorizationServer(AuthorizationServer):
    def __init__(self, db):
        super().__init__(scopes_supported=list(SCOPE_GROUP))
        self.db = db
        self.active_grant = None
        self.register_grant(CodeGrant, [S256Challenge(required=True)])
        self.register_grant(RefreshGrant)
        self.register_token_generator("default", lambda **values: {"access_token": secrets.token_urlsafe(32),
            "refresh_token": secrets.token_urlsafe(48), "token_type": "Bearer", "expires_in": 900,
            "scope": values.get("scope") or ""})

    def query_client(self, client_id):
        return self.db.get(McpClient, client_id)

    def create_oauth2_request(self, request):
        return request

    def handle_response(self, status, body, headers):
        return JSONResponse(body, status_code=status, headers=dict(headers))

    def send_signal(self, name, *args, **kwargs):
        pass

    def save_token(self, token, request):
        self.db.add(McpToken(access_hash=digest(token["access_token"]), refresh_hash=digest(token["refresh_token"]),
            scopes=token["scope"].split(), grant_id=self.active_grant.id,
            expires_at=min(now() + timedelta(seconds=900), self.active_grant.expires_at)))


def oauth_parameters(request, pairs):
    data = {}
    for key, value in pairs:
        # RFC 8707 permits repeated resource indicators; keep a single audience.
        if len(value) > 4096 or (key in data and (key != "resource" or data[key] != value)):
            raise HTTPException(400, "OAuth parameters must be unique and bounded.")
        data[key] = value
    rate_limit("oauth", request.client.host if request.client else "unknown", 120)
    return data


def client_metadata_supported():
    with transaction() as db:
        return bool(mcp_settings(db)["allowed_client_domains"])


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    # Discovery must survive MCP overload. Limit its web-pool usage to one
    # settings read per process at a time, including during reconnect bursts.
    async with request.app.state.oauth_discovery_lock:
        supports_metadata = await run_in_threadpool(client_metadata_supported)
    origin = settings().public_url
    return {"issuer": origin, "authorization_endpoint": origin + "/oauth/authorize",
        "token_endpoint": origin + "/oauth/token", "revocation_endpoint": origin + "/oauth/revoke",
        "registration_endpoint": origin + "/oauth/register", "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"], "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": supports_metadata,
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": list(SCOPE_GROUP)}


@router.get("/.well-known/oauth-protected-resource/mcp")
def resource_metadata():
    return {"resource": resource_url(), "authorization_servers": [settings().public_url],
        "scopes_supported": list(SCOPE_GROUP), "bearer_methods_supported": ["header"]}


@router.post("/oauth/register", status_code=201)
def register_client(body: McpRegistrationInput, request: Request, db: DB):
    rate_limit("register", request.client.host if request.client else "unknown", 20)
    db.scalar(select(AppConfig).where(AppConfig.id == 1).execution_options(populate_existing=True).with_for_update())
    config = mcp_settings(db)
    if not enabled_groups(config):
        raise HTTPException(403, "MCP access is disabled.")
    if db.scalar(select(func.count()).select_from(McpClient)) >= 1000:
        raise HTTPException(429, "The instance has reached its OAuth client registration limit. Ask an administrator to register the client.")
    for uri in body.redirect_uris:
        if urlsplit(uri).hostname not in {"localhost", "127.0.0.1", "::1", *config["allowed_client_domains"]}:
            raise HTTPException(403, "This callback hostname is not allowed for client registration. Ask an administrator to register the client.")
    client = McpClient(client_id=uid(), client_name=body.client_name, redirect_uris=body.redirect_uris)
    db.add(client)
    return {"client_id": client.client_id, **body.model_dump(exclude_none=True)}


@router.get("/oauth/authorize")
def authorize(request: Request, db: DB):
    data = oauth_parameters(request, request.query_params.multi_items())
    groups = enabled_groups(mcp_settings(db))
    if data.get("resource") != resource_url() or not groups:
        raise HTTPException(400, "Choose an enabled Code Mortis MCP resource.")
    requested = data["scope"].split() if "scope" in data else list(SCOPE_GROUP)
    if not set(requested) <= SCOPE_GROUP.keys():
        raise HTTPException(400, "The requested scopes do not belong to this MCP resource.")
    requested = list(dict.fromkeys(scope for scope in requested if SCOPE_GROUP[scope] in groups))
    if not requested:
        raise HTTPException(400, "Request at least one enabled Code Mortis MCP permission.")
    data["scope"] = " ".join(requested)
    client = resolve_client(db, data.get("client_id", ""))
    try:
        McpAuthorizationServer(db).get_consent_grant(ProtocolRequest("GET", data))
    except OAuth2Error as exc:
        raise HTTPException(400, exc.description or "The OAuth authorization request is invalid.") from exc
    binding = request.cookies.get("cm_mcp_browser", "")
    if len(binding) != 43:
        binding = secrets.token_urlsafe(32)
    row = McpAuthorization(id=uid(), client_id=client.client_id, domain=UNIFIED, redirect_uri=data["redirect_uri"],
        scopes=requested, state=data.get("state", ""), code_challenge=data["code_challenge"],
        browser_hash=digest(binding), expires_at=now() + timedelta(minutes=10))
    db.add(row)
    destination = "/mcp/authorize?" + urlencode({"request_id": row.id})
    try:
        current_user(request, db)
        row.session_hash = request.state.login_session.token_hash
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        destination = "/api/v1/auth/login?" + urlencode({"return_to": destination})
    response = RedirectResponse(settings().public_url + destination, status_code=303)
    response.set_cookie("cm_mcp_browser", binding, secure=settings().cookie_secure,
        httponly=True, samesite="lax", path="/", max_age=600)
    return response


def authorization_request(db, request, identity, user, *, lock=False, bind_session=True):
    query = select(McpAuthorization).where(McpAuthorization.id == identity).execution_options(populate_existing=True)
    row = db.scalar(query.with_for_update() if lock else query)
    if (not row or row.domain != UNIFIED or row.expires_at <= now() or row.decided_at
        or not hmac.compare_digest(row.browser_hash, digest(request.cookies.get("cm_mcp_browser", "")))):
        raise HTTPException(409, "This authorization request expired or was already used. Connect the MCP client again.")
    session_hash = request.state.login_session.token_hash
    if row.session_hash and row.session_hash != session_hash:
        raise HTTPException(403, "This authorization belongs to a different browser session.")
    if bind_session:
        row.session_hash = session_hash
    if not enabled_groups(mcp_settings(db)):
        raise HTTPException(403, "MCP access is disabled for this domain.")
    return row


def consent_repositories(db, user, groups):
    repositories = github.visible_repositories(db, user)
    result = {group: repositories for group in GROUP_SCOPES if group in groups}
    if "code" in result:
        indexed = set(db.scalars(select(CodeGraphRepository.repository_id).where(CodeGraphRepository.enabled.is_(True))))
        result["code"] = [repo for repo in repositories if repo.id in indexed]
    return result


@router.get("/api/v1/mcp/authorize", response_model=McpConsentPreview)
def consent_preview(request: Request, request_id: str, db: DB, user: USER):
    row = authorization_request(db, request, request_id, user)
    available = scoped_permissions(enabled_groups(mcp_settings(db)), user.role)
    scopes = [scope for scope in row.scopes if scope in available]
    repositories = consent_repositories(db, user, {SCOPE_GROUP[scope] for scope in scopes})
    client = db.get(McpClient, row.client_id)
    return {"request_id": row.id, "client_id": row.client_id, "client_name": client.client_name,
        "redirect_uri": row.redirect_uri, "domain": row.domain, "expires_at": row.expires_at,
        "scopes": [{"name": scope, "label": available[scope][0], "description": available[scope][1],
            "group": SCOPE_GROUP[scope]} for scope in scopes],
        "repositories_by_group": {group: [{"id": repo.id, "full_name": repo.full_name} for repo in repos]
            for group, repos in repositories.items()}}


def oauth_redirect(row, **values):
    parsed = urlsplit(row.redirect_uri)
    return urlunsplit(parsed._replace(query=urlencode([*parse_qsl(parsed.query),
        *values.items(), ("state", row.state), ("iss", settings().public_url)])))


@router.post("/api/v1/mcp/authorize")
def consent(body: McpConsentInput, request: Request, db: DB, user: USER):
    if not body.approve:
        row = authorization_request(db, request, body.request_id, user, lock=True)
        row.decided_at = now()
        return {"redirect_url": oauth_redirect(row, error="access_denied")}

    row = authorization_request(db, request, body.request_id, user, bind_session=False)
    scopes = set(body.scopes)
    allowed = scoped_permissions(enabled_groups(mcp_settings(db)), user.role)
    if not scopes or not scopes <= set(row.scopes) or not scopes <= allowed.keys():
        raise HTTPException(422, "Select only the permissions offered for this client.")
    groups = {SCOPE_GROUP[scope] for scope in scopes}
    needs_repositories = {SCOPE_GROUP[scope] for scope in scopes if scope != "instance:manage"}
    selections = {group: set(ids) for group, ids in body.repository_ids_by_group.items()}
    if not selections.keys() <= groups:
        raise HTTPException(422, "Select repositories only for approved tool groups.")
    available = consent_repositories(db, user, groups)
    for group in groups:
        selected = selections.setdefault(group, set())
        if (group in needs_repositories and not selected) or not selected <= {repo.id for repo in available[group]}:
            raise HTTPException(403, "Select repositories you can currently access for each requested tool group.")
    client = resolve_client(db, row.client_id)
    if not client.check_redirect_uri(row.redirect_uri):
        raise HTTPException(409, "The client callback changed. Connect the client again.")
    client = db.scalar(select(McpClient).where(McpClient.client_id == row.client_id)
        .execution_options(populate_existing=True).with_for_update())
    if not client or client.revoked_at or not client.check_redirect_uri(row.redirect_uri):
        raise HTTPException(409, "The client registration changed. Connect the client again.")

    # Serialize grant creation with settings changes after outbound checks complete.
    db.scalar(select(AppConfig).where(AppConfig.id == 1).execution_options(populate_existing=True).with_for_update())
    row = authorization_request(db, request, body.request_id, user, lock=True)
    allowed = scoped_permissions(enabled_groups(mcp_settings(db)), user.role)
    if not scopes <= allowed.keys():
        raise HTTPException(422, "Select only the permissions offered for this client.")
    if db.scalar(select(func.count()).select_from(McpGrant).where(McpGrant.user_id == user.id,
        McpGrant.revoked_at.is_(None), McpGrant.expires_at > now())) >= 100:
        raise HTTPException(409, "Revoke an unused MCP authorization in Settings before connecting another client.")
    grant = McpGrant(id=uid(), user_id=user.id, client_id=row.client_id, domain=UNIFIED,
        scopes=sorted(scopes), repository_ids_by_group={group: sorted(ids) for group, ids in selections.items()},
        expires_at=now() + timedelta(days=30))
    db.add(grant)
    db.flush()
    code = secrets.token_urlsafe(32)
    row.grant_id, row.code_hash, row.scopes, row.decided_at = grant.id, digest(code), sorted(scopes), now()
    row.expires_at = now() + timedelta(seconds=60)
    return {"redirect_url": oauth_redirect(row, code=code)}


@router.post("/oauth/token")
async def token_endpoint(request: Request, db: DB):
    raw = await request.body()
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    def respond():
        data = oauth_parameters(request, parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True))
        return McpAuthorizationServer(db).create_token_response(ProtocolRequest("POST", data))

    return await run_in_threadpool(respond)


@router.post("/oauth/revoke")
async def revoke_token(request: Request, db: DB):
    raw = await request.body()
    def revoke():
        data = oauth_parameters(request, parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True))
        fingerprint = digest(data.get("token", ""))
        row = db.scalar(select(McpToken).where((McpToken.access_hash == fingerprint) | (McpToken.refresh_hash == fingerprint)))
        if row:
            grant = db.scalar(select(McpGrant).where(McpGrant.id == row.grant_id).with_for_update())
            if grant.client_id == data.get("client_id"):
                revoke_grant(db, grant)

    await run_in_threadpool(revoke)
    return JSONResponse({}, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/mcp/settings", response_model=McpSettingsView)
def read_settings(db: DB, user: USER):
    config = mcp_settings(db)
    if user.role != "admin":
        config["allowed_client_domains"] = []
    return {**config, "public_url": settings().public_url, "can_manage": user.role == "admin"}


@router.put("/api/v1/mcp/settings", response_model=McpSettingsView)
def update_settings(body: McpSettingsInput, db: DB, user: ADMIN):
    from code_mortis.mcp.provenance import revoke_work

    config = db.scalar(select(AppConfig).where(AppConfig.id == 1).execution_options(populate_existing=True).with_for_update())
    new = body.model_dump()
    disabled_groups = enabled_groups(mcp_settings(db)) - enabled_groups(new)
    config.mcp_settings = new
    if not body.enabled:
        db.execute(update(McpAuthorization).where(McpAuthorization.decided_at.is_(None),
            McpAuthorization.expires_at > now()).values(expires_at=now()))
    if body.enabled and not disabled_groups:
        return read_settings(db, user)
    for grant in db.scalars(select(McpGrant).where(McpGrant.revoked_at.is_(None)).order_by(McpGrant.id).with_for_update()):
        if not body.enabled or grant.domain != UNIFIED:
            revoke_grant(db, grant)
            continue
        retained = [scope for scope in grant.scopes if SCOPE_GROUP.get(scope) not in disabled_groups]
        if not retained:
            revoke_grant(db, grant)
        elif retained != grant.scopes:
            grant.scopes = retained
            grant.repository_ids_by_group = {group: ids for group, ids in grant.repository_ids_by_group.items()
                if group not in disabled_groups}
            revoke_work(db, grant.id, groups=disabled_groups)
    return read_settings(db, user)


@router.get("/api/v1/mcp/clients")
def list_clients(db: DB, user: ADMIN):
    return [{"client_id": client.client_id, "client_name": client.client_name, "redirect_uris": client.redirect_uris}
        for client in db.scalars(select(McpClient).where(McpClient.revoked_at.is_(None)).order_by(McpClient.client_name))]


@router.post("/api/v1/mcp/clients", status_code=201)
def add_client(body: McpClientInput, db: DB, user: ADMIN):
    if db.get(McpClient, body.client_id):
        raise HTTPException(409, "A client with this ID already exists.")
    db.add(McpClient(**body.model_dump()))
    return body


@router.delete("/api/v1/mcp/clients/{client_id:path}")
def remove_client(client_id: str, db: DB, user: ADMIN):
    client = db.scalar(select(McpClient).where(McpClient.client_id == client_id).with_for_update())
    if not client:
        raise HTTPException(404, "MCP client not found.")
    for grant in db.scalars(select(McpGrant).where(McpGrant.client_id == client_id).order_by(McpGrant.id).with_for_update()):
        revoke_grant(db, grant)
    # Retain referenced client identity for history while refusing further authorization.
    client.redirect_uris = []
    client.revoked_at = now()
    return {"ok": True}


@router.get("/api/v1/mcp/grants", response_model=list[McpGrantSummary])
def list_grants(db: DB, user: USER):
    return [{"id": grant.id, "client_id": grant.client_id, "client_name": client.client_name,
        "domain": grant.domain, "scopes": grant.scopes, "repository_ids": grant.repository_ids,
        "repository_ids_by_group": grant.repository_ids_by_group,
        "created_at": grant.created_at, "expires_at": grant.expires_at, "last_used_at": grant.last_used_at,
        "revoked_at": grant.revoked_at} for grant, client in db.execute(select(McpGrant, McpClient)
        .join(McpClient).where(McpGrant.user_id == user.id).order_by(McpGrant.created_at.desc()))]


@router.delete("/api/v1/mcp/grants/{grant_id}")
def remove_grant(grant_id: str, db: DB, user: USER):
    grant = db.scalar(select(McpGrant).where(McpGrant.id == grant_id).with_for_update())
    if not grant or (grant.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "MCP authorization not found.")
    revoke_grant(db, grant)
    return {"ok": True}


def owned_approval(db, identity, user, lock=False):
    from code_mortis.mcp.tools import tool_group_and_scope

    query = select(McpApproval).join(McpGrant).where(McpApproval.id == identity, McpGrant.user_id == user.id)
    row = db.scalar(query)
    if not row:
        raise HTTPException(404, "Action approval not found.")
    permission = tool_group_and_scope(row.tool)
    if not permission:
        raise HTTPException(404, "Action approval not found.")
    group, scope = permission
    owner, grant = ensure_grant(db, row.grant_id, group, scope, lock=lock)
    if lock:
        row = db.scalar(query.execution_options(populate_existing=True).with_for_update(of=McpApproval))
        if not row:
            raise HTTPException(404, "Action approval not found.")
    check_cached_repository_access(db, owner, group, row.arguments)
    if row.expires_at <= now():
        raise HTTPException(409, "This action approval expired. Ask your agent for a new preview.")
    return row, grant, group


@router.get("/api/v1/mcp/approvals/{approval_id}")
def approval_preview(approval_id: str, db: DB, user: USER):
    row, grant, group = owned_approval(db, approval_id, user)
    action = row.tool.removeprefix(group + "_").replace("_", " ").capitalize()
    return {"id": row.id, "tool": row.tool, "domain": group,
        "summary": action + " for " + db.get(McpClient, grant.client_id).client_name + ".",
        "arguments": row.arguments, "expires_at": row.expires_at, "approved_at": row.approved_at,
        "consumed_at": row.consumed_at, "denied_at": row.denied_at}


@router.post("/api/v1/mcp/approvals/{approval_id}")
def decide_approval(approval_id: str, body: McpApprovalInput, db: DB, user: USER):
    row, _, _ = owned_approval(db, approval_id, user, lock=True)
    if row.approved_at or row.denied_at or row.consumed_at:
        raise HTTPException(409, "This approval was already decided.")
    if body.approve:
        row.approved_at = now()
    else:
        row.denied_at = now()
    return {"approved": body.approve}


def cleanup(db):
    """Expire transport credentials without touching review or security history."""
    expired = select(McpGrant.id).where(McpGrant.expires_at <= now())
    db.execute(delete(McpAuthorization).where(McpAuthorization.expires_at <= now(),
        (McpAuthorization.grant_id.is_(None)) | McpAuthorization.grant_id.in_(expired)))
    db.execute(delete(McpToken).where(McpToken.grant_id.in_(expired)))
    db.execute(delete(McpOperation).where(McpOperation.grant_id.in_(expired)))
    db.execute(delete(McpApproval).where(McpApproval.expires_at <= now()))
    db.execute(delete(McpAudit).where(McpAudit.created_at < now() - timedelta(days=30)))
