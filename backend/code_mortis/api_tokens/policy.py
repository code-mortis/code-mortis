"""Personal API token permission catalog and explicit route policy.

Every protected operation is classified as PAT-supported with its required
scope, or session-only. Unknown routes deny PATs; add new routes here
explicitly before exposing them to tokens.
"""

SCOPES: dict[str, tuple[str, str]] = {
    "repositories:read": ("Read repositories", "Read selected repository metadata and discovery lists."),
    "reviews:read": ("Read reviews", "Read pull requests, runs, findings, coverage and history."),
    "reviews:write": ("Update reviews", "Resolve or reopen findings, ignore pull requests and synchronize GitHub updates."),
    "reviews:run": ("Run reviews", "Start, retry, cancel and publish review runs; refresh repositories."),
    "security:read": ("Read security", "Read scans, findings, evidence, coverage and reports."),
    "security:write": ("Triage findings", "Accept risk, mark false positives, reopen findings and acknowledge secret rotation."),
    "security:run": ("Run security scans", "Start, retry and cancel scans using an authorized connection."),
    "connections:read": ("Read connections", "Read secret-free connection metadata and model selection."),
    "analytics:read": ("Read analytics", "Read analytics computed from selected repositories."),
}

DEFAULT_EXPIRATION_DAYS = 30
MAX_EXPIRATION_DAYS = 365
MAX_ACTIVE_TOKENS = 20

# (method, route template) -> required PAT scope. Token identity is classified separately.
PAT_ROUTE_SCOPES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/repositories"): "repositories:read",
    ("GET", "/api/v1/repositories/{repository_id}/pulls"): "reviews:read",
    ("POST", "/api/v1/repositories/{repository_id}/sync"): "reviews:run",
    ("GET", "/api/v1/analytics/overview"): "analytics:read",
    ("GET", "/api/v1/analytics/findings"): "analytics:read",
    ("GET", "/api/v1/analytics/reviews"): "analytics:read",
    ("GET", "/api/v1/analytics/usage"): "analytics:read",
    ("GET", "/api/v1/analytics/security"): "analytics:read",
    ("GET", "/api/v1/pulls"): "reviews:read",
    ("GET", "/api/v1/pulls/{pr_id}"): "reviews:read",
    ("PUT", "/api/v1/pulls/{pr_id}/ignore"): "reviews:write",
    ("POST", "/api/v1/pulls/{pr_id}/cancel"): "reviews:run",
    ("GET", "/api/v1/pulls/{pr_id}/runs"): "reviews:read",
    ("POST", "/api/v1/pulls/{pr_id}/runs"): "reviews:run",
    ("GET", "/api/v1/pulls/{pr_id}/findings"): "reviews:read",
    ("POST", "/api/v1/pulls/{pr_id}/summary"): "reviews:write",
    ("GET", "/api/v1/runs/{run_id}"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/findings"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/findings/{finding_id}"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/resolved"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/candidates"): "reviews:read",
    ("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}"): "reviews:read",
    ("POST", "/api/v1/runs/{run_id}/candidates/{candidate_id}/retry"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/cancel"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/retry"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/publish"): "reviews:run",
    ("POST", "/api/v1/runs/{run_id}/summary/retry"): "reviews:run",
    ("GET", "/api/v1/findings/{finding_id}"): "reviews:read",
    ("PATCH", "/api/v1/findings/{finding_id}"): "reviews:write",
    ("POST", "/api/v1/findings/{finding_id}/sync"): "reviews:write",
    ("GET", "/api/v1/providers"): "connections:read",
    ("GET", "/api/v1/connections"): "connections:read",
    ("GET", "/api/v1/connections/{connection_id}/models"): "connections:read",
    ("GET", "/api/v1/security/repositories"): "security:read",
    ("GET", "/api/v1/security/repositories/{repository_id}"): "security:read",
    ("GET", "/api/v1/security/repositories/{repository_id}/scans"): "security:read",
    ("POST", "/api/v1/security/repositories/{repository_id}/scans"): "security:run",
    ("GET", "/api/v1/security/scans/{scan_id}"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/activity"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/coverage/{section}"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/assessments"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/assessments/{assessment_id}"): "security:read",
    ("POST", "/api/v1/security/scans/{scan_id}/cancel"): "security:run",
    ("POST", "/api/v1/security/scans/{scan_id}/retry"): "security:run",
    ("GET", "/api/v1/security/scans/{scan_id}/report"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/report/findings"): "security:read",
    ("GET", "/api/v1/security/repositories/{repository_id}/findings"): "security:read",
    ("GET", "/api/v1/security/scans/{scan_id}/findings"): "security:read",
    ("GET", "/api/v1/security/findings/{finding_id}"): "security:read",
    ("PATCH", "/api/v1/security/findings/{finding_id}"): "security:write",
    ("GET", "/api/v1/security/findings/{finding_id}/activity"): "security:read",
    ("GET", "/api/v1/security/findings/{finding_id}/observations"): "security:read",
    ("POST", "/api/v1/security/findings/{finding_id}/rotation"): "security:write",
}

# Valid PAT; its own identity/metadata only. Any granted scope suffices.
AUTH_TOKEN_ROUTE = ("GET", "/api/v1/auth/token")

SESSION_ONLY_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/v1/pulls/{pr_id}/code"),
    ("POST", "/api/v1/pulls/{pr_id}/code/file"),
    ("POST", "/api/v1/pulls/{pr_id}/code/workspace"),
    ("GET", "/api/v1/pulls/{pr_id}/code-graph"),
    ("POST", "/api/v1/pulls/{pr_id}/code-graph/query"),
    ("POST", "/api/v1/pulls/{pr_id}/code-graph/index"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/api-tokens"),
    ("POST", "/api/v1/api-tokens"),
    ("DELETE", "/api/v1/api-tokens/{token_id}"),
    ("PUT", "/api/v1/repositories/{repository_id}"),
    ("GET", "/api/v1/repositories/{repository_id}/activity"),
    ("GET", "/api/v1/repositories/{repository_id}/code-graph"),
    ("POST", "/api/v1/repositories/{repository_id}/code-graph/retry"),
    ("POST", "/api/v1/repositories/{repository_id}/code-graph/cancel"),
    ("POST", "/api/v1/repositories/{repository_id}/background"),
    ("GET", "/api/v1/repositories/{repository_id}/background/{job_id}"),
    ("GET", "/api/v1/settings"),
    ("GET", "/api/v1/installations"),
    ("DELETE", "/api/v1/installations/{installation_id}"),
    ("POST", "/api/v1/installations/{installation_id}"),
    ("DELETE", "/api/v1/installations/{installation_id}/local"),
    ("GET", "/api/v1/installations/available"),
    ("POST", "/api/v1/members"),
    ("DELETE", "/api/v1/members/{user_id}"),
    ("POST", "/api/v1/connections"),
    ("POST", "/api/v1/connections/models"),
    ("PUT", "/api/v1/connections/{connection_id}"),
    ("DELETE", "/api/v1/connections/{connection_id}"),
    ("POST", "/api/v1/connections/{connection_id}/test"),
    ("POST", "/api/v1/connections/{identity}/auth"),
    ("GET", "/api/v1/connections/{identity}/auth"),
    ("DELETE", "/api/v1/connections/{identity}/auth"),
    ("POST", "/api/v1/connections/{identity}/disconnect"),
    ("GET", "/api/v1/connections/{identity}/grants"),
    ("PUT", "/api/v1/connections/{identity}/grants/{repository_id}"),
    ("DELETE", "/api/v1/connections/{identity}/grants/{repository_id}"),
    ("POST", "/api/v1/connections/{identity}/console-ticket"),
    ("GET", "/api/v1/security/repositories/{repository_id}/github-issues"),
    ("POST", "/api/v1/security/repositories/{repository_id}/github-issues"),
    ("GET", "/api/v1/security/findings/{finding_id}/github-issue"),
    ("POST", "/api/v1/security/findings/{finding_id}/github-issue"),
    ("POST", "/api/v1/security/findings/{finding_id}/github-issue/sync"),
    ("POST", "/api/v1/security/findings/{finding_id}/github-issue/reapprove"),
    ("GET", "/api/v1/mcp/authorize"),
    ("POST", "/api/v1/mcp/authorize"),
    ("GET", "/api/v1/mcp/settings"),
    ("PUT", "/api/v1/mcp/settings"),
    ("GET", "/api/v1/mcp/clients"),
    ("POST", "/api/v1/mcp/clients"),
    ("DELETE", "/api/v1/mcp/clients/{client_id}"),
    ("GET", "/api/v1/mcp/grants"),
    ("DELETE", "/api/v1/mcp/grants/{grant_id}"),
    ("GET", "/api/v1/mcp/approvals/{approval_id}"),
    ("POST", "/api/v1/mcp/approvals/{approval_id}"),
}

PUBLIC_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/v1/setup/status"),
    ("GET", "/.well-known/oauth-authorization-server"),
    ("GET", "/.well-known/oauth-protected-resource/mcp/{domain}"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
}

# Retained credential contracts; PATs are never accepted as a substitute.
SPECIAL_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/setup/manifest"): "Bootstrap token and origin; no PAT",
    ("GET", "/api/v1/setup/callback"): "Browser flow; no PAT",
    ("GET", "/api/v1/auth/login"): "Browser flow; no PAT",
    ("GET", "/api/v1/auth/callback"): "Browser flow; no PAT",
    ("GET", "/api/v1/installations/start"): "Browser flow; no PAT",
    ("GET", "/api/v1/installations/callback"): "Browser flow; no PAT",
    ("POST", "/api/v1/webhooks/github"): "GitHub signature; no PAT",
    ("POST", "/oauth/register"): "Existing OAuth protocol; no PAT",
    ("GET", "/oauth/authorize"): "Existing OAuth protocol; no PAT",
    ("POST", "/oauth/token"): "Existing OAuth protocol; no PAT",
    ("POST", "/oauth/revoke"): "Existing OAuth protocol; no PAT",
}


def pat_scope(method: str, path: str) -> str | None:
    return PAT_ROUTE_SCOPES.get((method, path))
