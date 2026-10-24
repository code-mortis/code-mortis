# Code Mortis MCP implementation plan

Add authenticated MCP access to Code Mortis so an external agent can discuss findings and perform the review and security workflows available in the frontend. Reviews and security must be enabled independently. Preserve the existing GitHub permission checks, personal connection consent, immutable evidence, cancellation behavior and publication safeguards.

Status: implemented in this working tree against the contracts below; the full backend and frontend suites passed, followed by final focused MCP and settings checks. Local and mocked evidence is recorded in docs/acceptance.md. No real external client integration is claimed: each client (OpenCode, Claude Code/Desktop, Codex, ChatGPT) needs its own recorded run before it is listed as supported.

## Architecture and dependencies

Use the official Python MCP SDK inside the existing FastAPI process, with two endpoints on `CM_PUBLIC_URL`:

- `/mcp/reviews` for pull requests, review runs and review findings.
- `/mcp/security` for security scans, security findings and GitHub issues.

Use explicit typed tools and existing application services. Avoid automatically publishing the entire OpenAPI schema or exposing a generic HTTP, SQL, shell or GitHub proxy tool. The MCP endpoints must be reachable only through the normal application ingress. Keep the native runner private and keep its MCP configuration empty.

The current stable specification is **2026-07-28**. Its Streamable HTTP transport uses individual POST requests and removes protocol sessions and the old GET stream. Use the SDK's compatibility handling for supported older clients, with stateless legacy HTTP where possible. Durable Code Mortis runs remain independent of the MCP connection. [MCP transport specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http), [SDK legacy client support](https://py.sdk.modelcontextprotocol.io/run/legacy-clients/).

The official Python SDK's stable release is **2.3.0**, published on 2 October 2026. Its high-level server is `MCPServer`. Pin and lock the validated release with `uv`; verify its Starlette, Pydantic and HTTP dependencies against the existing lockfile. Mount its ASGI application before the frontend catch-all, with explicit host and origin validation, no-store responses and the required lifespan handling. [SDK release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), [ASGI integration](https://py.sdk.modelcontextprotocol.io/run/asgi/).

| Option | Fit for Code Mortis | Decision |
| --- | --- | --- |
| Official Python SDK with a separate authorization module | Fits Python, FastAPI and Pydantic; leaves application permissions explicit | Recommended |
| Standalone FastMCP | Current framework with OAuth proxy, GitHub and persistent storage support; distinct from the former class in the official SDK | Viable alternative, but its default GitHub proxy would duplicate parts of the existing login and credential lifecycle |
| FastAPI-MCP route generation | Reduces adapter code but requires auditing every exposed route and verifying current protocol compatibility | Do not use as the default permission boundary |
| External authorization server such as Keycloak | Can provide OAuth, but adds deployment and account-linking work | Optional future deployment choice |

FastMCP's authentication support is real and current; the choice above is about integration cost. [FastMCP authentication](https://gofastmcp.com/servers/auth/authentication), [OAuth proxy and storage](https://gofastmcp.com/servers/auth/oauth-proxy).

## Authentication and consent

Keep the roles separate even when hosted in one application. The MCP resource server validates access tokens. A same-origin OAuth authorization module authenticates the person through the existing Code Mortis GitHub login, obtains consent and issues MCP tokens. GitHub remains the source of user identity and repository permissions. The SDK recommends the resource-server integration through `TokenVerifier` and `AuthSettings`, with resource validation enabled; its embedded authorization-provider shortcut is discouraged for new servers. [SDK authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/).

Reuse Authlib's protocol machinery and existing cryptographic helpers. Authlib documents ready-made authorization-server integrations for Flask and Django, so FastAPI requires an adapter and persistent grant models. Treat this as substantive security work, not an existing capability provided by the browser OAuth client. [Authlib authorization server](https://docs.authlib.org/en/latest/oauth2/authorization-server/index.html).

The authorization flow must provide:

1. An unauthenticated MCP request receives `401` with `WWW-Authenticate` pointing to the endpoint's protected-resource metadata. Publish metadata for both canonical resource URLs and authorization-server metadata at the same public origin.
2. Authorization Code with mandatory PKCE S256, exact registered redirects, single-use expiring codes, request-bound state, issuer identification and the `resource` parameter in authorization and token requests. Reject password and implicit grants.
3. A browser consent screen identifying the client, redirect hostname, selected repositories, domain and permissions. An existing GitHub session authenticates the user but does not automatically approve the MCP client.
4. Separate MCP bearer credentials. Do not accept `cm_session`, GitHub tokens, runner tokens or provider keys as MCP credentials, and do not forward MCP tokens to GitHub or an AI provider. Keep browser CSRF checks intact.

These follow the current [MCP authorization flow](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) and [authorization security requirements](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations).

Prefer opaque random access and refresh tokens, storing only their hashes in PostgreSQL. Record the resource, user, client, granted scopes, selected repositories, expiry and revocation through the grant. Start with 15-minute access tokens, 30-day maximum refresh lifetime and 60-second authorization codes. Rotate refresh tokens atomically and revoke their family on reuse. These lifetimes are proposed product defaults. A database-backed verifier provides immediate revocation without adding JWT signing-key management.

Support pre-registered clients and Client ID Metadata Documents (CIMD). Existing pre-registration takes priority; otherwise prefer CIMD. Implement Dynamic Client Registration only as a controlled compatibility path for clients that need it. DCR is deprecated in the current specification. Restrict accepted client metadata domains by administrator policy; bound fetch time, response size and redirects, block private and metadata addresses, and protect against DNS rebinding. Do not weaken provider endpoint restrictions to accommodate client metadata. [Client registration requirements](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration), [MCP security guidance](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices).

## Independent activation and permissions

Add persistent instance settings **Enable reviews MCP** and **Enable security MCP**, both off after upgrade. These control external MCP access independently of repository automation settings and independently of each other. Both endpoints can remain mounted so a setting change takes effect without a restart.

Each client authorization grants access to one endpoint, an explicit repository selection and a subset of permissions. A user wanting both domains authorizes both resources. A review token must fail at the security endpoint and vice versa.

| Scope | Allowed operations |
| --- | --- |
| `reviews:read` | Review repositories, PRs, runs, findings, assessments, coverage, history and review analytics |
| `reviews:write` | Resolve, reopen or dismiss findings; ignore or restore PRs; retry publication and synchronization |
| `reviews:run` | Start, resume or stop reviews; retry AI assessments and summaries; generate repository background |
| `reviews:manage` | Change review repository configuration, subject to existing administrator checks |
| `security:read` | Security repositories, scans, findings, evidence, assessments, coverage and reports |
| `security:write` | Triage, reopen and acknowledge secret rotation |
| `security:run` | Start, retry or stop security scans |
| `security:issues` | Preview, create and recover GitHub issue publication or synchronization |

Grant read access by default. Request write, execution, publication and configuration permissions explicitly. Explain that starting a review can consume the selected AI connection and publish GitHub comments as part of the existing review workflow. Read scopes do not imply execution, and write scopes do not imply permission to change OAuth grants.

Every request and every tool invocation must check active domain setting, token, grant, enabled user, membership, current role, resource, scope and selected repository. Continue using live GitHub read or push permission checks and active installation checks. Filter discovery by granted tools, but enforce the same rules on direct calls, resource reads and cached client catalogs.

Disabling a domain revokes its grants and refresh families. Re-enabling it requires new consent. Revocation blocks new MCP work immediately. Persist grant provenance on queued MCP operations and revalidate it before execution or delayed publication. Cancel pending or running work created through a revoked grant using the existing lifecycle functions; preserve saved results and unrelated frontend or automatic work. Recheck revocation at existing safe checkpoints before further model consumption or external writes.

## Review workflow coverage

The existing review surface is primarily in [api.py](../backend/code_mortis/api.py), with lifecycle commands in [reviews.py](../backend/code_mortis/reviews.py). Tools should have clear names such as `reviews_list_pull_requests`, `reviews_get_finding`, `reviews_start_review` and `reviews_set_finding_status`. Keep separate operations separate when their permissions or side effects differ.

| Frontend capability | Existing route or service to reuse | Permission |
| --- | --- | --- |
| List repositories and PR inbox; search, filter, order and paginate | `/repositories`, `/pulls`, `/repositories/{id}/pulls`; `inbox.list_pulls` | Read |
| Inspect a PR, latest review, counts and write capability | `/pulls/{id}` | Read |
| List runs and inspect status, stages, errors, publication, usage and coverage | `/pulls/{id}/runs`, `/runs/{id}`; public run schemas | Read |
| List current PR findings and inspect individual history and verification | `/pulls/{id}/findings`, `/findings/{id}` | Read |
| Inspect immutable run findings, resolved findings and assessment candidates | `/runs/{id}/findings`, `/resolved`, `/candidates` and their detail routes | Read |
| Resolve, reopen, dismiss with reason and retry GitHub status synchronization | `PATCH /findings/{id}`, `/findings/{id}/sync`; `request_finding_status` | Write |
| Start a review with the selected connection | `POST /pulls/{id}/runs`; `create_run` | Run |
| Stop one review or all cancellable reviews for a PR | `/runs/{id}/cancel`, `/pulls/{id}/cancel`; existing cancellation lifecycle | Run |
| Resume a blocked review; retry assessment or summary generation | `/runs/{id}/retry`, candidate retry and summary retry | Run |
| Retry failed or partial publication and PR summary synchronization | `/runs/{id}/publish`, `/pulls/{id}/summary` | Write |
| Ignore and restore a PR, including cancellation and suppression of pending work | `PUT /pulls/{id}/ignore` | Write |
| Refresh repository data | `/repositories/{id}/sync` | Write |
| Inspect and change review configuration; generate and inspect background | `PUT /repositories/{id}`, background routes | Read, Manage or Run as applicable |
| Inspect review analytics and safe connection/model choices | `/analytics/*`, `/providers`, `/connections`, connection model routes | Read, plus existing visibility rules |

Move only inline business actions needed by both transports into the corresponding feature modules. Reuse existing lifecycle functions directly where available. Do not invoke route functions with unresolved FastAPI dependencies, create fake browser sessions, or reproduce cancellation and publication logic inside MCP tools.

## Security workflow coverage

Reuse [security_api.py](../backend/code_mortis/security_api.py), [repository_security.py](../backend/code_mortis/repository_security.py), [security_issues.py](../backend/code_mortis/security_issues.py) and [security_schemas.py](../backend/code_mortis/security_schemas.py).

| Frontend capability | Existing route or service to reuse | Permission |
| --- | --- | --- |
| List security repositories, grades, freshness and counts with filters | `/security/repositories` and repository detail | Read |
| List scans and inspect state, stages, errors, usage and pinned commit | Repository scan list and `/security/scans/{id}` | Read |
| Inspect scan activity, coverage sections and assessments | Scan `/activity`, `/coverage/{section}`, `/assessments` and assessment detail | Read |
| List repository or scan findings with all supported filters | Repository and scan `/findings` | Read |
| Discuss finding evidence, remediation, observations and activity | Finding detail, `/observations` and `/activity` | Read |
| Inspect saved report summaries and paginated immutable finding snapshots; export reports | Scan `/report` and `/report/findings` | Read |
| Mark false positive, accept risk or reopen with a reason | Finding `PATCH`; preserve `version` conflict checks | Write |
| Acknowledge rotation of an exposed secret | Finding `/rotation`; preserve kind, state, version and evidence identity checks | Write |
| Start a scan with connection, model and current limits | Repository `POST /scans`; `create_scan` | Run |
| Stop or retry an eligible scan | Scan `/cancel`, `/retry`; existing cancellation and retry behavior | Run |
| List issue candidates and inspect exact redacted drafts | Repository `/github-issues`, finding `/github-issue` | Issues, plus existing push permission |
| Create individual or bulk issues; recover uncertain publication; retry and reapprove synchronization | Existing issue create, bulk, sync and reapprove routes/services | Issues |

Preserve security states exactly: manual triage accepts `open`, `false_positive` and `accepted_risk`. Do not add a tool that sets a security finding to fixed without the existing evidence-based lifecycle. A review finding absent from a later run remains not observed, not resolved.

For public security issue publication, require a server-verified human approval of the exact draft in Code Mortis. The agent receives an approval URL and pending operation ID, then resumes after approval. Bind approval to user, grant, repository, current visibility, finding version and draft digest; expire and consume it atomically. Reuse this for public bulk publication and recovery, where appropriate. A tool annotation or an agent-supplied `confirmed=true` cannot prove human consent. Keep existing issue markers, idempotency, visibility revalidation and recovery restrictions for private issues as well.

## Shared management and browser interactions

Frontend parity also includes the shared settings surface. Account for it explicitly instead of granting administrative powers through a domain write scope.

Add a separately authorized management tool group under a distinct **Allow management through MCP** setting, off by default. Use `instance:manage` for instance administration and `connections:manage` for owner-controlled connection actions. Access through either enabled endpoint still requires a resource-bound grant and the corresponding current role or ownership checks.

- Cover member and installation inspection, member changes, installation connect/uninstall/local removal, shared API connection create/update/test/delete and model discovery. Sensitive destructive changes use the same browser approval mechanism bound to exact arguments.
- Cover owner-only personal connection status, authentication cancellation, disconnect and repository consent grant/revoke. A review or scan execution grant must never authorize the agent to grant itself personal connection consumption.
- Use secure browser handoffs for GitHub App setup, GitHub login and installation, provider credential entry, native device authorization and Claude's session-bound login console. MCP can initiate the supported workflow, return a trusted Code Mortis URL and inspect progress. Do not expose console tickets, credential input, a general terminal or native profile files to the agent.
- Keep the browser as the authority for managing MCP settings and authorizations. An MCP client cannot expand its own grant, approve its own pending action or turn on a disabled endpoint.
- Local appearance preferences and opening external links remain client presentation behavior. They do not require new server tools.

This retains access to the frontend's outcomes while keeping credential entry and human approval on their existing trusted surfaces.

## Tool contracts and durable actions

Return typed structured results with stable IDs, repository identity, commit, current state, immutable occurrence evidence, relevant timestamps, URLs, pagination and actionable errors. The calling agent supplies the conversation experience; Code Mortis does not need another chatbot, model invocation or conversation store to discuss its findings.

Reuse strict Pydantic inputs and public output serializers. FastAPI response-model filtering does not automatically protect a direct service call. Never serialize raw ORM objects, connection secrets, native output or scanner artifacts. Keep credential and exposed-secret redaction for findings, reports, previews, errors and audit metadata.

Preserve existing multi-select filters, search, ordering, implicit Open defaults, totals and server pagination. Bound MCP result sizes and use detail tools for evidence. In particular, the existing review run detail contains all occurrences and cannot be copied wholesale into an unbounded MCP response. Report export must use an authenticated, expiring, grant-checked download or bounded chunks; do not place a bearer token in a URL.

Run and scan tools return the saved run or job ID and state promptly, with tools to inspect progress and stop the operation. A disconnected MCP client does not cancel a durable job. MCP request cancellation and product review/scan cancellation are separate operations.

Preserve scan `request_key` and issue operation/recovery idempotency. Add caller request keys for review starts and other expensive MCP commands that lack an equivalent. Scope keys to user, client grant, operation and arguments; store the result in the same transaction as job creation. Reusing a key with different arguments must fail. Do not retry a paid execution or uncertain GitHub write blindly.

Annotate tools accurately for client presentation, but enforce all authorization server-side. Apply per-client/user rate limits, bounded request bodies and execution limits. Treat PR titles, descriptions, finding text and evidence as untrusted data, never instructions to invoke another tool. Preserve request IDs and record actor, client, grant, operation, target IDs, result and duration without logging credentials or full source payloads.

## Delivery checklist

| Order | Work and affected areas | Acceptance before moving on |
| --- | --- | --- |
| 1 | Freeze the frontend action inventory and permission matrix. Validate SDK dependency resolution and a minimal FastAPI OAuth adapter using Authlib. | Discovery, login, consent, PKCE, audience binding, revocation and one read tool work with real MCP clients on a disposable instance. Resolve adapter or framework incompatibilities here. |
| 2 | Add models, additive Alembic migration, OAuth endpoints, token verifier and two MCP mounts. Add grant provenance and audit persistence. | Existing data survives upgrade; both domains default off; cross-domain tokens, removed members, wrong scopes and revoked grants fail. |
| 3 | Add review and security read tools, including history, assessments, filters, coverage and reports. | Results match frontend semantics and counts; public serialization and redaction hold; large results remain bounded. |
| 4 | Add review commands and security scan/triage commands using shared services. Extend worker authorization for MCP provenance and durable idempotency. | Start, retry, stop, ignore, resolve/reopen and triage preserve existing behavior under duplicate calls, stale versions, permission loss and worker restarts. |
| 5 | Add issue preview/publication/recovery, trusted approval handoffs and separately scoped management tools. | Exact-draft approval, visibility changes, bulk limits, uncertain writes and owner/admin boundaries pass; every frontend action has a tool or documented secure handoff. |
| 6 | Add Settings controls, consent and connected-client management; publish setup instructions and operational recovery guidance. | Independent toggles, per-domain consent, repository selection, expiry, revoke and approval flows work on desktop/mobile with keyboard access. |
| 7 | Complete interoperability, upgrade and hosted acceptance. Update PLAN, PRODUCT, operations, acceptance and generated API contracts. | End-to-end authorized client workflows and required CI finish successfully; record real-account evidence separately from mocks. |

Expected implementation locations are a small `mcp_server.py` transport/catalog module, `mcp_auth.py` authorization integration and feature tool modules only as needed; existing `api.py`, `security_api.py`, domain services, workers, models and schemas; additive migrations; `frontend/src/settings.tsx` and focused components; backend and Playwright tests; `backend/pyproject.toml`, `uv.lock`, OpenAPI export and generated frontend types. Keep code in existing layers rather than introducing a second workflow engine or a general plugin framework.

## Verification and completion

Use the isolated test database and Redis. Cover the OAuth flow over actual HTTP because in-process MCP tests alone do not establish transport authentication. Test wrong resource/issuer, missing or invalid PKCE, code replay, redirect substitution, CIMD SSRF, refresh reuse, expiry, role changes, repository permission loss and concurrent grant revocation. Verify disabled endpoints refuse both discovery and cached direct tool calls.

Add behavioral tests for shared frontend and MCP actions: duplicate review/scan starts, stop during execution/publication, ignore during pending work, blocked-only review resume, assessment retry, finding synchronization, stale triage, secret rotation, public issue approval, visibility changes, recovery and bulk atomicity. Include malicious finding text and credential sentinels in output/error/audit redaction tests. Confirm runner networking and disabled repository-provided MCP configurations remain intact.

Run backend tests and Ruff, export OpenAPI and regenerate frontend types, then run the frontend build and relevant Playwright coverage. Complete the required broader CI checks once the implementation is ready. Refresh Graphify after source and document changes.

Target real interoperability with OpenCode, Claude Code/Desktop, Codex and ChatGPT. Record exact tested client versions and whether they use CIMD, pre-registration or DCR. A client requiring a compatibility path must pass that path before it is listed as supported. Cloud clients need a reachable HTTPS instance; local clients can test a local deployment. Real reviews, scans and issue publication require a separately authorized test repository and normal product credentials.

The feature is complete only when both independent MCP domains and the separately authorized management surface cover the inventory above, with secure handoffs for interactive identity and credential workflows, and the acceptance record distinguishes local, mocked, hosted and real integration evidence. Current state: the transport, authorization, tool, provenance and frontend modules above exist and match the contracts in this plan; hosted and real-client acceptance is still pending, see docs/acceptance.md.
