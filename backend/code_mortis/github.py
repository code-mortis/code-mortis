import time
from datetime import timedelta

import httpx
import jwt
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oauth2 import OAuth2Error
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import now, transaction
from .models import AppConfig, Installation, Repository, User
from .security import clean_text, seal, unseal

API = "https://api.github.com"


def visible_repositories(db: Session, user: User) -> list[Repository]:
    token = user_token(db, user)
    visible = set()
    for installation in db.scalars(select(Installation).where(Installation.active.is_(True))):
        try:
            visible.update(
                repo["id"] for repo in pages(
                    f"/user/installations/{installation.id}/repositories", token, "repositories"
                )
            )
        except GitHubError as exc:
            if exc.status not in {403, 404}:
                raise
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


def request(method: str, path: str, token: str, **kwargs):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(kwargs.pop("headers", {}))
    for attempt in range(2):
        retry_get = method.upper() == "GET" and attempt == 0
        try:
            response = httpx.request(
                method, API + path, headers=headers, timeout=25, follow_redirects=False, **kwargs
            )
        except httpx.HTTPError as exc:
            if not retry_get or not isinstance(exc, (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)):
                raise GitHubError(503, "GitHub could not be reached. Try again shortly.") from exc
        else:
            if (
                not retry_get
                or response.status_code not in {500, 502, 503, 504}
                or "retry-after" in response.headers
            ):
                break
        time.sleep(1)
    if response.status_code >= 300:
        retry = response.headers.get("retry-after", "0")
        message = {
            401: "GitHub authorization expired. Sign in again.",
            403: "GitHub denied this request. Check repository and GitHub App permissions; if you recently sent many requests, wait and try again.",
            404: "The repository or resource is no longer accessible.",
            422: "GitHub could not accept this request. Refresh the PR and retry.",
            429: "GitHub rate limit reached. Wait for the limit to reset, then retry if the operation has not completed.",
        }.get(response.status_code, f"GitHub could not complete the request (HTTP {response.status_code}). Try again shortly; if it keeps failing, ask the administrator to check GitHub access.")
        raise GitHubError(response.status_code, message, int(retry) if retry.isdigit() else 0)
    return response.json() if response.content else None


def pages(path: str, token: str, key: str | None = None):
    separator = "&" if "?" in path else "?"
    result = []
    for page in range(1, 101):
        data = request("GET", f"{path}{separator}per_page=100&page={page}", token)
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


def installation_token(db: Session, installation_id: int, *, checks: bool = False, resolve: bool = False) -> str:
    return installation_token_from_jwt(app_token(db), installation_id, checks=checks, resolve=resolve)


def installation_token_from_jwt(token: str, installation_id: int, *, checks: bool = False, resolve: bool = False) -> str:
    """Exchange a snapshotted App JWT without keeping a database transaction open."""
    data = request("POST", f"/app/installations/{installation_id}/access_tokens", token)
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


def authorize_repo(db: Session, user: User, full_name: str, write: bool = False) -> dict:
    result = request("GET", f"/repos/{full_name}", user_token(db, user))
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


def graphql(token: str, query: str, variables: dict):
    data = request("POST", "/graphql", token, json={"query": query, "variables": variables})
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
        data = graphql(token, query, {"owner": owner, "repo": repo, "number": number, "after": cursor})
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
