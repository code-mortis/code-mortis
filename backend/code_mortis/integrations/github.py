import atexit
import json
import logging
import os
import time
from collections import OrderedDict
from datetime import timedelta
from hashlib import sha256
from http.cookiejar import CookieJar, DefaultCookiePolicy
from math import ceil
from threading import Lock

import httpx
import jwt
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oauth2 import OAuth2Error
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from code_mortis.core.access import effective_repository_ids
from code_mortis.core.config import settings
from code_mortis.core.db import now, transaction
from code_mortis.core.security import clean_text, seal, unseal
from code_mortis.models import AppConfig, Installation, Repository, User

API = "https://api.github.com"
TRANSIENT_SERVER_ERRORS = {500, 502, 503, 504}
logger = logging.getLogger(__name__)
_get_cache: OrderedDict[tuple[bytes, str], tuple[str, bytes]] = OrderedDict()
_get_cache_lock = Lock()
_client: httpx.Client | None = None
_client_lock = Lock()


def http_client():
    global _client
    with _client_lock:
        if _client is None:
            # GitHub identity is per request; a pooled connection must never carry another user's cookies.
            _client = httpx.Client(cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
                limits=httpx.Limits(max_connections=40, max_keepalive_connections=20, keepalive_expiry=30))
        return _client


def close_http_client(**kwargs):
    global _client
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        client.close()


def _after_fork():
    global _client, _client_lock, _get_cache_lock
    # Do not enter inherited transport locks; children establish their own connections.
    _client, _client_lock, _get_cache_lock = None, Lock(), Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
atexit.register(close_http_client)


def visible_repositories(db: Session, user: User, *, fail_on_throttle: bool = False) -> list[Repository]:
    token = user_token(db, user)
    visible = set()
    for installation in db.scalars(select(Installation).where(Installation.active.is_(True))):
        try:
            visible.update(
                repo["id"] for repo in pages(
                    f"/user/installations/{installation.id}/repositories", token, "repositories",
                    permission_user_id=user.id,
                )
            )
        except GitHubError as exc:
            if exc.status not in {403, 404} or (fail_on_throttle and exc.retry_after > 0):
                raise
    selected = effective_repository_ids(user)
    if selected is not None:
        visible.intersection_update(selected)
    return list(db.scalars(
        select(Repository).join(Installation).where(
            Repository.id.in_(visible), Repository.active.is_(True), Installation.active.is_(True)
        ).order_by(Repository.full_name)
    ))


class GitHubError(Exception):
    def __init__(self, status: int, message: str, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def rate_limited(self):
        return self.status == 429 or (self.status == 403 and self.retry_after > 0)


def request(method: str, path: str, token: str, *, retry_server_errors: bool = False, **kwargs):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    custom_headers = kwargs.pop("headers", {})
    max_response_bytes = kwargs.pop("max_response_bytes", None)
    headers.update(custom_headers)
    is_get = method.upper() == "GET"
    cache_key = (sha256(token.encode()).digest(), path) if is_get and token and not kwargs and not custom_headers and not max_response_bytes else None
    cached = None
    if cache_key:
        with _get_cache_lock:
            cached = _get_cache.get(cache_key)
            if cached:
                _get_cache.move_to_end(cache_key)
        if cached:
            headers["If-None-Match"] = cached[0]
    for attempt in range(2):
        try:
            if max_response_bytes:
                # Bound decoded bytes before JSON parsing, including compressed responses.
                started = time.monotonic()
                with http_client().stream(method, API + path, headers=headers, timeout=5, follow_redirects=False, **kwargs) as stream:
                    body = bytearray()
                    for chunk in stream.iter_bytes(chunk_size=65536):
                        if len(body) + len(chunk) > max_response_bytes:
                            raise GitHubError(422, "This comparison is too large to display. Open it on GitHub.")
                        if time.monotonic() - started > 20:
                            raise GitHubError(503, "GitHub took too long to return this comparison. Try again shortly.")
                        body.extend(chunk)
                    decoded_headers = {key: value for key, value in stream.headers.items()
                                       if key.lower() not in {"content-encoding", "content-length"}}
                    response = httpx.Response(stream.status_code, headers=decoded_headers, content=bytes(body))
            else:
                response = http_client().request(
                    method, API + path, headers=headers, timeout=25, follow_redirects=False, **kwargs
                )
        except httpx.HTTPError as exc:
            if attempt or not is_get or not isinstance(exc, (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)):
                raise GitHubError(503, "GitHub could not be reached. Try again shortly.") from exc
        else:
            # Token exchanges and read-only GraphQL calls also tolerate an explicit
            # server failure. Only GETs are replayed after an ambiguous disconnect.
            if (
                attempt or not (is_get or retry_server_errors)
                or response.status_code not in TRANSIENT_SERVER_ERRORS
                or "retry-after" in response.headers
            ):
                break
        time.sleep(1)
    # Conditional responses still require GitHub validation; errors never reuse the body.
    if response.status_code == 304 and cached:
        return json.loads(cached[1])
    if cache_key:
        with _get_cache_lock:
            _get_cache.pop(cache_key, None)
    if response.status_code >= 300:
        retry = response.headers.get("retry-after", "0")
        retry_after = int(retry) if retry.isdigit() else 0
        reset = response.headers.get("x-ratelimit-reset", "")
        if response.headers.get("x-ratelimit-remaining") == "0" and reset.isdigit():
            retry_after = max(retry_after, ceil(int(reset) - time.time()))
        message = {
            401: "GitHub authorization expired. Sign in again.",
            403: "GitHub denied this request. Check repository and GitHub App permissions; if you recently sent many requests, wait and try again.",
            404: "The repository or resource is no longer accessible.",
            422: "GitHub could not accept this request. Refresh the PR and retry.",
            429: "GitHub rate limit reached. Wait for the limit to reset, then retry if the operation has not completed.",
        }.get(response.status_code, f"GitHub could not complete the request (HTTP {response.status_code}). Try again shortly; if it keeps failing, ask the administrator to check GitHub access.")
        raise GitHubError(response.status_code, message, retry_after)
    result = response.json() if response.content else None
    if cache_key and response.status_code == 200 and response.headers.get("etag") and len(response.content) <= 1024 * 1024:
        with _get_cache_lock:
            _get_cache[cache_key] = (response.headers["etag"], response.content)
            if len(_get_cache) > 128:
                _get_cache.popitem(last=False)
    return result


def _permission_cache_key(user_id: int, path: str, token: str) -> str:
    config = settings()
    deployment = sha256(f"{config.public_url}\0{config.encryption_key}".encode()).hexdigest()
    identity = sha256(f"{user_id}\0{token}\0{path}".encode()).hexdigest()
    return f"code-mortis:github-permissions:v1:{deployment}:{identity}"


def _cached_permission(cache: Redis, key: str, fresh: bool):
    try:
        entry = json.loads(cache.get(key) or "null")
        if not isinstance(entry, dict):
            return None
        if "error" in entry:
            status, message, retry_at = entry["error"]
            retry_after = max(0, ceil(retry_at - time.time()))
            if not fresh or retry_after:
                raise GitHubError(status, message, retry_after)
        elif not fresh and isinstance(entry.get("data"), dict):
            return entry["data"]
    except (ValueError, TypeError, KeyError):
        pass
    return None


def permission_request(user_id: int, path: str, token: str, *, fresh: bool = False) -> dict:
    key = _permission_cache_key(user_id, path, token)
    try:
        with Redis.from_url(settings().redis_url, socket_timeout=2, socket_connect_timeout=2) as cache:
            previous = cache.get(key) if fresh else None
            cached = _cached_permission(cache, key, fresh)
            if cached is not None:
                return cached
            lock = cache.lock(key + ":lock", timeout=120, blocking_timeout=60)
            if not lock.acquire():
                raise GitHubError(503, "GitHub permissions are being refreshed. Try again shortly.", 2)
            try:
                # Share only a refresh published since this caller started waiting.
                cached = _cached_permission(cache, key, fresh and cache.get(key) == previous)
                if cached is not None:
                    return cached
                error = None
                try:
                    result = request("GET", path, token)
                    entry, ttl = {"data": result}, 5
                except GitHubError as exc:
                    error = exc
                    entry = {"error": [exc.status, str(exc), time.time() + exc.retry_after]}
                    ttl = max(5, exc.retry_after)
                entry["refresh_id"] = lock.local.token.decode()
                try:
                    # Publish only while owning the lease, so a late response cannot restore revoked access.
                    stored = cache.eval("""
                        if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
                        redis.call('set', KEYS[2], ARGV[2], 'EX', ARGV[3])
                        return 1
                    """, 2, lock.name, key, lock.local.token, json.dumps(entry), ttl)
                    if not stored:
                        raise GitHubError(503, "GitHub permission refresh expired. Try again shortly.", 2)
                except RedisError:
                    logger.warning("Could not cache the fresh GitHub permission result")
                if error:
                    raise error
                return result
            finally:
                try:
                    lock.release()
                except RedisError:
                    logger.warning("Could not release the GitHub permission refresh lock")
    except RedisError:
        logger.warning("GitHub permission cache unavailable; checking GitHub directly")
        return request("GET", path, token)


def pages(path: str, token: str, key: str | None = None, *, permission_user_id: int | None = None):
    separator = "&" if "?" in path else "?"
    result = []
    for page in range(1, 101):
        page_path = f"{path}{separator}per_page=100&page={page}"
        data = (request("GET", page_path, token) if permission_user_id is None
                else permission_request(permission_user_id, page_path, token))
        rows = data[key] if key else data
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise GitHubError(422, "GitHub returned more than 10,000 items, exceeding the synchronization limit. Ask the administrator to reduce the repositories accessible to this installation.")


def app_config(db: Session) -> AppConfig:
    config = db.get(AppConfig, 1)
    if config is None:
        raise GitHubError(409, "Complete GitHub App setup first.")
    return config


def app_token(db: Session) -> str:
    config = app_config(db)
    return jwt.encode(
        {"iat": int(time.time()) - 30, "exp": int(time.time()) + 540, "iss": config.github_app_id},
        unseal(config.private_key),
        algorithm="RS256",
    )


def repository_read_token(db: Session, installation_id: int, repository_id: int, *, pulls: bool = False) -> str:
    permissions = {"contents": "read", "metadata": "read"}
    if pulls:
        permissions["pull_requests"] = "read"
    data = request("POST", f"/app/installations/{installation_id}/access_tokens", app_token(db), retry_server_errors=True, json={
        "repository_ids": [repository_id], "permissions": permissions,
    })
    return data["token"]


def installation_token(db: Session, installation_id: int, *, checks: bool = False, resolve: bool = False, issues: bool = False) -> str:
    options = {"checks": checks, "resolve": resolve}
    if issues:
        options["issues"] = True
    return installation_token_from_jwt(app_token(db), installation_id, **options)


def installation_token_from_jwt(token: str, installation_id: int, *, checks: bool = False, resolve: bool = False, issues: bool = False) -> str:
    """Exchange a snapshotted App JWT without keeping a database transaction open."""
    data = request("POST", f"/app/installations/{installation_id}/access_tokens", token, retry_server_errors=True)
    if issues and data.get("permissions", {}).get("issues") != "write":
        raise GitHubError(403, "Enable Issues: Read and write on the existing GitHub App and approve the installation permission update. Security scans and reports remain available.")
    if resolve and data.get("permissions", {}).get("contents") != "write":
        raise GitHubError(
            403, "GitHub requires Contents: Read and write to resolve or reopen review threads. "
            "Update the existing GitHub App permissions and approve the installation update, "
            "then retry synchronization. The saved verification does not need to run again."
        )
    if checks and data.get("permissions", {}).get("checks") != "write":
        raise GitHubError(
            403, "Enable Checks: Read and write on the GitHub App and approve the installation permission update."
        )
    return data["token"]


def oauth_client(db: Session) -> OAuth2Client:
    config = app_config(db)
    return OAuth2Client(
        config.client_id,
        unseal(config.client_secret),
        redirect_uri=settings().public_url + "/api/v1/auth/callback",
        token_endpoint_auth_method="client_secret_post",
        code_challenge_method="S256",
        headers={"Accept": "application/json"},
        timeout=25,
    )


def exchange_code(db: Session, code: str, verifier: str) -> dict:
    try:
        with oauth_client(db) as client:
            return dict(
                client.fetch_token(
                    "https://github.com/login/oauth/access_token",
                    code=code,
                    code_verifier=verifier,
                    grant_type="authorization_code",
                )
            )
    except (httpx.HTTPError, OAuth2Error, ValueError) as exc:
        raise GitHubError(401, "GitHub authorization was declined or expired. Sign in again.") from exc


def oauth_request(data: dict) -> dict:
    try:
        response = httpx.post(
            "https://github.com/login/oauth/access_token",
            data=data,
            headers={"Accept": "application/json"},
            timeout=25,
        )
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GitHubError(503, "GitHub sign-in could not be completed.") from exc
    if response.status_code != 200 or "access_token" not in result:
        raise GitHubError(401, "GitHub authorization was declined or expired. Sign in again.")
    return result


def save_tokens(user: User, tokens: dict):
    user.access_token = seal(tokens["access_token"])
    user.refresh_token = seal(tokens.get("refresh_token", ""))
    user.token_expires = now() + timedelta(seconds=tokens["expires_in"]) if tokens.get("expires_in") else None


def user_token(db: Session, user: User) -> str:
    # GitHub rotates refresh tokens immediately. Persist them even if the caller's
    # later GitHub request fails and its application transaction rolls back.
    with transaction() as token_db:
        locked = token_db.scalar(select(User).where(User.id == user.id).with_for_update())
        if not locked or not locked.enabled:
            raise GitHubError(403, "Your account is no longer allowed on this instance.")
        expires = locked.token_expires
        if expires and expires.timestamp() < time.time() + 90:
            if not locked.refresh_token:
                raise GitHubError(401, "GitHub authorization expired. Sign in again.")
            config = app_config(token_db)
            tokens = oauth_request(
                {
                    "client_id": config.client_id,
                    "client_secret": unseal(config.client_secret),
                    "grant_type": "refresh_token",
                    "refresh_token": unseal(locked.refresh_token),
                }
            )
            save_tokens(locked, tokens)
        return unseal(locked.access_token)


def authorize_repo(db: Session, user: User, full_name: str, write: bool = False, *, fresh: bool = False) -> dict:
    from code_mortis.core.access import effective_repository_ids

    selected = effective_repository_ids(user)
    if selected is not None and not db.scalar(select(Repository.id).where(
        Repository.id.in_(selected), Repository.full_name == full_name,
    )):
        if getattr(user, "api_token_repository_ids", None) is not None:
            raise GitHubError(403, "This repository was not selected for this API token.")
        raise GitHubError(403, "This repository was not selected for this MCP client.")
    result = permission_request(user.id, f"/repos/{full_name}", user_token(db, user), fresh=write or fresh)
    permissions = result.get("permissions", {})
    if not permissions.get("pull") or (write and not permissions.get("push")):
        raise GitHubError(
            403, "This action requires repository write access." if write else "Repository access denied."
        )
    return result


def login_url(db: Session, state: str, verifier: str) -> str:
    with oauth_client(db) as client:
        return client.create_authorization_url(
            "https://github.com/login/oauth/authorize", state=state, code_verifier=verifier
        )[0]


def graphql(token: str, query: str, variables: dict, *, retry_server_errors: bool = False):
    data = request("POST", "/graphql", token, json={"query": query, "variables": variables},
                   retry_server_errors=retry_server_errors)
    if data.get("errors"):
        errors = data["errors"]
        forbidden = any(error.get("type") == "FORBIDDEN" for error in errors)
        detail = "; ".join(str(error.get("message", error.get("type", "Unknown error"))) for error in errors)
        raise GitHubError(403 if forbidden else 502, "GitHub could not complete the request. Check repository and app permissions or retry shortly. GitHub details: " + clean_text(detail, (token,))[:1500])
    return data["data"]


def threads(token: str, full_name: str, number: int) -> list[dict]:
    owner, repo = full_name.split("/")
    query = """query($owner:String!,$repo:String!,$number:Int!,$after:String) {
      repository(owner:$owner,name:$repo) { pullRequest(number:$number) {
        reviewThreads(first:100,after:$after) { pageInfo { hasNextPage endCursor }
          nodes { id isResolved isOutdated resolvedBy { login databaseId }
            comments(first:100) { pageInfo { hasNextPage }
              nodes { databaseId body url updatedAt author { __typename login ... on Bot { databaseId } } } }
          }
        }
      } }
    }"""
    result, cursor = [], None
    for _ in range(100):
        data = graphql(token, query, {"owner": owner, "repo": repo, "number": number, "after": cursor},
                       retry_server_errors=True)
        connection = data["repository"]["pullRequest"]["reviewThreads"]
        for thread in connection["nodes"]:
            for comment in thread.get("comments", {}).get("nodes", []):
                author = comment.get("author") or {}
                if author.get("__typename") == "Bot":
                    # GraphQL omits the suffix REST/webhooks use for the same bot account.
                    author["login"] = author["login"].removesuffix("[bot]") + "[bot]"
                    resolver = thread.get("resolvedBy") or {}
                    if author.get("databaseId") and resolver.get("databaseId") == author["databaseId"]:
                        resolver["login"] = author["login"]
        result.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"]["endCursor"]
    raise GitHubError(422, "This pull request has too many review discussions to synchronize in one operation. Check finding statuses directly on GitHub.")


def set_thread(token: str, thread_id: str, resolved: bool):
    operation = "resolveReviewThread" if resolved else "unresolveReviewThread"
    return graphql(
        token,
        f"mutation($id:ID!) {{ {operation}(input:{{threadId:$id}}) {{ thread {{ id isResolved }} }} }}",
        {"id": thread_id},
    )
