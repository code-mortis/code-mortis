# Personal API token route policy

Proposed policy for baseline `7155dff`, exported directly from the local FastAPI application. Companion to [the implementation plan](api-tokens-plan.md). This is a planning inventory, not an active authorization configuration.

The baseline has 111 HTTP operations, of which 52 are proposed for PAT access. Browser session access to these operations retains its current checks. Every PAT operation also enforces current membership, selected repositories, live GitHub permissions, owner/administrator rules, and operation-specific state checks. Permissions grant access to an operation, not an exemption from its existing checks.

Implementation must encode the exact method and route template, with explicit session-only and special-protocol classifications. Unknown routes deny PAT access. A test must compare the policy against `app.openapi()` and separately account for WebSockets, static assets, schema delivery, and other routes omitted from OpenAPI. A route added later is not automatically enabled for tokens.

## Baseline operation policy

| Method | Route template | PAT policy |
| --- | --- | --- |
| GET | `/api/v1/setup/status` | Public; existing behavior |
| POST | `/api/v1/setup/manifest` | Bootstrap token and origin; no PAT |
| GET | `/api/v1/setup/callback` | Browser flow; no PAT |
| GET | `/api/v1/auth/login` | Browser flow; no PAT |
| GET | `/api/v1/auth/callback` | Browser flow; no PAT |
| GET | `/api/v1/auth/me` | Browser session only |
| POST | `/api/v1/auth/logout` | Browser session only |
| GET | `/api/v1/repositories` | PAT: `repositories:read` |
| PUT | `/api/v1/repositories/{repository_id}` | Browser session only |
| POST | `/api/v1/repositories/{repository_id}/background` | Browser session only |
| GET | `/api/v1/repositories/{repository_id}/background/{job_id}` | Browser session only |
| GET | `/api/v1/repositories/{repository_id}/pulls` | PAT: `reviews:read` |
| POST | `/api/v1/repositories/{repository_id}/sync` | PAT: `reviews:run` |
| GET | `/api/v1/analytics/overview` | PAT: `analytics:read` |
| GET | `/api/v1/analytics/findings` | PAT: `analytics:read` |
| GET | `/api/v1/analytics/reviews` | PAT: `analytics:read` |
| GET | `/api/v1/analytics/usage` | PAT: `analytics:read` |
| GET | `/api/v1/pulls` | PAT: `reviews:read` |
| GET | `/api/v1/pulls/{pr_id}` | PAT: `reviews:read` |
| PUT | `/api/v1/pulls/{pr_id}/ignore` | PAT: `reviews:write` |
| POST | `/api/v1/pulls/{pr_id}/cancel` | PAT: `reviews:run` |
| GET | `/api/v1/pulls/{pr_id}/runs` | PAT: `reviews:read` |
| POST | `/api/v1/pulls/{pr_id}/runs` | PAT: `reviews:run` |
| GET | `/api/v1/pulls/{pr_id}/findings` | PAT: `reviews:read` |
| POST | `/api/v1/pulls/{pr_id}/summary` | PAT: `reviews:write` |
| GET | `/api/v1/runs/{run_id}` | PAT: `reviews:read` |
| GET | `/api/v1/runs/{run_id}/findings` | PAT: `reviews:read` |
| GET | `/api/v1/runs/{run_id}/findings/{finding_id}` | PAT: `reviews:read` |
| GET | `/api/v1/runs/{run_id}/resolved` | PAT: `reviews:read` |
| GET | `/api/v1/runs/{run_id}/candidates` | PAT: `reviews:read` |
| GET | `/api/v1/runs/{run_id}/candidates/{candidate_id}` | PAT: `reviews:read` |
| POST | `/api/v1/runs/{run_id}/candidates/{candidate_id}/retry` | PAT: `reviews:run` |
| POST | `/api/v1/runs/{run_id}/cancel` | PAT: `reviews:run` |
| POST | `/api/v1/runs/{run_id}/retry` | PAT: `reviews:run` |
| POST | `/api/v1/runs/{run_id}/publish` | PAT: `reviews:run` |
| POST | `/api/v1/runs/{run_id}/summary/retry` | PAT: `reviews:run` |
| GET | `/api/v1/findings/{finding_id}` | PAT: `reviews:read` |
| PATCH | `/api/v1/findings/{finding_id}` | PAT: `reviews:write` |
| POST | `/api/v1/findings/{finding_id}/sync` | PAT: `reviews:write` |
| GET | `/api/v1/providers` | PAT: `connections:read` |
| GET | `/api/v1/connections` | PAT: `connections:read` |
| POST | `/api/v1/connections` | Browser session only |
| GET | `/api/v1/connections/{connection_id}/models` | PAT: `connections:read` |
| POST | `/api/v1/connections/models` | Browser session only |
| PUT | `/api/v1/connections/{connection_id}` | Browser session only |
| DELETE | `/api/v1/connections/{connection_id}` | Browser session only |
| POST | `/api/v1/connections/{connection_id}/test` | Browser session only |
| GET | `/api/v1/settings` | Browser session only |
| GET | `/api/v1/installations` | Browser session only |
| DELETE | `/api/v1/installations/{installation_id}` | Browser session only |
| POST | `/api/v1/installations/{installation_id}` | Browser session only |
| DELETE | `/api/v1/installations/{installation_id}/local` | Browser session only |
| GET | `/api/v1/installations/start` | Browser flow; no PAT |
| GET | `/api/v1/installations/callback` | Browser flow; no PAT |
| GET | `/api/v1/installations/available` | Browser session only |
| POST | `/api/v1/members` | Browser session only |
| DELETE | `/api/v1/members/{user_id}` | Browser session only |
| POST | `/api/v1/webhooks/github` | GitHub signature; no PAT |
| POST | `/api/v1/connections/{identity}/auth` | Browser session only |
| GET | `/api/v1/connections/{identity}/auth` | Browser session only |
| DELETE | `/api/v1/connections/{identity}/auth` | Browser session only |
| POST | `/api/v1/connections/{identity}/disconnect` | Browser session only |
| GET | `/api/v1/connections/{identity}/grants` | Browser session only |
| PUT | `/api/v1/connections/{identity}/grants/{repository_id}` | Browser session only |
| DELETE | `/api/v1/connections/{identity}/grants/{repository_id}` | Browser session only |
| POST | `/api/v1/connections/{identity}/console-ticket` | Browser session only |
| GET | `/api/v1/security/repositories` | PAT: `security:read` |
| GET | `/api/v1/security/repositories/{repository_id}` | PAT: `security:read` |
| GET | `/api/v1/security/repositories/{repository_id}/scans` | PAT: `security:read` |
| POST | `/api/v1/security/repositories/{repository_id}/scans` | PAT: `security:run` |
| GET | `/api/v1/security/scans/{scan_id}` | PAT: `security:read` |
| GET | `/api/v1/security/scans/{scan_id}/activity` | PAT: `security:read` |
| GET | `/api/v1/security/scans/{scan_id}/coverage/{section}` | PAT: `security:read` |
| GET | `/api/v1/security/scans/{scan_id}/assessments` | PAT: `security:read` |
| GET | `/api/v1/security/scans/{scan_id}/assessments/{assessment_id}` | PAT: `security:read` |
| POST | `/api/v1/security/scans/{scan_id}/cancel` | PAT: `security:run` |
| POST | `/api/v1/security/scans/{scan_id}/retry` | PAT: `security:run` |
| GET | `/api/v1/security/scans/{scan_id}/report` | PAT: `security:read` |
| GET | `/api/v1/security/scans/{scan_id}/report/findings` | PAT: `security:read` |
| GET | `/api/v1/security/repositories/{repository_id}/findings` | PAT: `security:read` |
| GET | `/api/v1/security/scans/{scan_id}/findings` | PAT: `security:read` |
| GET | `/api/v1/security/findings/{finding_id}` | PAT: `security:read` |
| PATCH | `/api/v1/security/findings/{finding_id}` | PAT: `security:write` |
| GET | `/api/v1/security/findings/{finding_id}/activity` | PAT: `security:read` |
| GET | `/api/v1/security/findings/{finding_id}/observations` | PAT: `security:read` |
| POST | `/api/v1/security/findings/{finding_id}/rotation` | PAT: `security:write` |
| GET | `/api/v1/security/repositories/{repository_id}/github-issues` | Browser session only |
| POST | `/api/v1/security/repositories/{repository_id}/github-issues` | Browser session only |
| GET | `/api/v1/security/findings/{finding_id}/github-issue` | Browser session only |
| POST | `/api/v1/security/findings/{finding_id}/github-issue` | Browser session only |
| POST | `/api/v1/security/findings/{finding_id}/github-issue/sync` | Browser session only |
| POST | `/api/v1/security/findings/{finding_id}/github-issue/reapprove` | Browser session only |
| GET | `/.well-known/oauth-authorization-server` | Public; existing behavior |
| GET | `/.well-known/oauth-protected-resource/mcp/{domain}` | Public; existing behavior |
| POST | `/oauth/register` | Existing OAuth protocol; no PAT |
| GET | `/oauth/authorize` | Existing OAuth protocol; no PAT |
| GET | `/api/v1/mcp/authorize` | Browser session only |
| POST | `/api/v1/mcp/authorize` | Browser session only |
| POST | `/oauth/token` | Existing OAuth protocol; no PAT |
| POST | `/oauth/revoke` | Existing OAuth protocol; no PAT |
| GET | `/api/v1/mcp/settings` | Browser session only |
| PUT | `/api/v1/mcp/settings` | Browser session only |
| GET | `/api/v1/mcp/clients` | Browser session only |
| POST | `/api/v1/mcp/clients` | Browser session only |
| DELETE | `/api/v1/mcp/clients/{client_id}` | Browser session only |
| GET | `/api/v1/mcp/grants` | Browser session only |
| DELETE | `/api/v1/mcp/grants/{grant_id}` | Browser session only |
| GET | `/api/v1/mcp/approvals/{approval_id}` | Browser session only |
| POST | `/api/v1/mcp/approvals/{approval_id}` | Browser session only |
| GET | `/health/live` | Public; existing behavior |
| GET | `/health/ready` | Public; existing behavior |

## New token feature operations

| Method | Proposed route template | PAT policy |
| --- | --- | --- |
| GET | `/api/v1/api-tokens` | Browser session only; owner metadata |
| POST | `/api/v1/api-tokens` | Browser session only; CSRF and recent GitHub callback |
| DELETE | `/api/v1/api-tokens/{token_id}` | Browser session only; CSRF and owner check |
| GET | `/api/v1/auth/token` | Valid PAT; its own identity/metadata only |

## Required interpretation checks

- The browser's `/auth/me` and logout remain session-only. The PAT smoke suite uses `/auth/token`.
- Repository refresh requires `reviews:run`, because synchronization can schedule review work under repository automation settings. Preserve the PAT origin and current connection consent through every descendant job; refresh cannot turn token work into unrestricted system work.
- `reviews:run` includes execution and publication of normal review comments. `reviews:write` includes finding/overview synchronization and the cancellation effects of ignoring a PR. The token creation UI must describe these effects.
- Repository discovery may expose its existing grade/status summary fields for the selected repositories; full security reports/findings require the respective security operations. No permission implies access to another permission's routes.
- Provider catalog, saved connection listing, and model lookup use `connections:read`. Filter connections/models to those available for selected repositories even if no `repository_id` query is supplied. Draft discovery, connection tests, personal-provider auth/grants/consoles, and credential changes are session-only.
- Repository background generation/status and repository configuration remain administrator/session workflows; no PAT route is inferred from their GET or POST method.
- Analytics metadata, repository options, inbox facets/groups, security grade/status counts, registered totals, and report/candidate counts must use the selected/currently-accessible repository intersection.
- Security issue preview/candidates, creation, recovery, and reapproval remain in the browser workflow. Never use a PAT to bypass exact-draft review or the separate MCP approval protocol.
- Public, bootstrap, signed-webhook, browser-callback, and OAuth entries retain their existing credential contracts. PATs are not accepted as a substitute for any of them.

## Noodle collection coverage

The runnable collection contains `public/` health/readiness/setup-status requests and `api/` requests for every PAT-supported baseline operation plus `/auth/token`. Session-only operations are removed from runnable YAML and documented as browser-only; token management is performed in Settings. Public OAuth discovery remains outside this collection's purpose. There is no cookie fallback or mixed credential mode.

Contract checks compare this inventory, the implementation's route policy, every collection request's method/template/auth inheritance, public-request exceptions, and the README's exclusion list. The old rule that every browser endpoint needs runnable collection YAML must be replaced rather than silently waived.
