# Personal API tokens implementation plan

Status: implemented locally with automated checks passing. Real-account Settings-to-Noodle acceptance and production deployment remain pending in [acceptance.md](../acceptance.md). Collection baseline: `7155dff`.

Let an enabled Code Mortis member create a named, expiring personal API token in Settings and use it from Noodle or a script. Tokens authenticate as their owner and further restrict that owner's access by permissions and selected repositories. Existing browser GitHub login, cookie sessions, CSRF protection, and MCP OAuth remain independent.

This plan covers the backend, a small Settings section, worker authorization, Noodle collection changes, and acceptance evidence. It does not introduce a REST OAuth flow, JWTs, service accounts, new authentication dependencies, or changes to the isolated native runner.

## Proposed product decisions

These are implementation defaults, not claims that an external standard mandates these exact values.

| Decision | First implementation |
| --- | --- |
| Ownership | Members and administrators manage their own tokens through a browser session. No impersonation or token access to another user's credentials. |
| Expiration | Required; default 30 days, maximum 365 days, measured from server time in UTC. No non-expiring tokens. |
| Permissions | Read permissions selected initially; mutation and model execution permissions require explicit selection. No wildcard or implicit scope inheritance. |
| Repositories | Explicit, nonempty selection from currently accessible repositories. New repositories never become accessible automatically. |
| Token limits | At most 20 active tokens per user; enforce atomically when creating one. |
| Issuance freshness | Creating a token requires a GitHub callback completed within the previous 15 minutes. Revocation remains available from any valid browser session. |
| Replacement | Create a replacement, update clients, verify it works, then revoke the old token. No automatic renewal or dedicated rotate endpoint in the first release. |
| Session lifetime | Signing out of the browser does not revoke personal tokens. Removing membership or disabling the account permanently revokes its tokens. |
| Credential precedence | A protected request with both an Authorization header and `cm_session` is rejected. Invalid Authorization never falls back to cookies. |
| Sensitive administration | Token management, MCP settings/consent/approvals, installation/team administration, provider credential changes, and native login/consoles remain browser-only. |
| Public security issues | Issue creation, bulk creation, sync/reapproval, and other public security publication remain browser-only in this release. Preserve the reviewed-draft and recovery flow. |

Keep these decisions together during implementation; changing one must update the contracts, permission tests, UI, and collection instructions.

## Existing code to reuse

| Existing surface | Current behavior and implication |
| --- | --- |
| `backend/code_mortis/api/dependencies.py` | `current_user()` authenticates `cm_session`, checks membership and CSRF, and supplies `USER`/`ADMIN`. Repository, PR, and connection helpers enforce current GitHub, administrator, and owner permissions. Preserve these checks. |
| `backend/code_mortis/api/routers/auth.py` | `/auth/me` and logout use `request.state.login_session`. Keep both session-only; do not invent a session or CSRF token for bearer callers. |
| `backend/code_mortis/api/routers/subscriptions.py` | Console tickets and WebSockets are bound to a browser login session. They cannot simply be enabled for bearer credentials. |
| `backend/code_mortis/integrations/github.py` | `visible_repositories()` and `authorize_repo()` provide discovery and live permission checks. Apply a PAT repository restriction to both discovery and direct-resource paths. |
| `backend/code_mortis/mcp/auth.py`, `models.py`, `provenance.py` | Existing examples of opaque-token hashing, scope/repository checks, rate limiting, and durable work attribution. PAT records and credential namespaces remain separate from MCP grants. |
| `backend/code_mortis/core/security.py`, `core/telemetry.py` | Reuse `digest()` and existing event scrubbing. Extend secret redaction for the PAT prefix. |
| `backend/code_mortis/jobs.py`, `reviews/`, `security_scans/` | Authorization must reach delayed execution, paid work, and GitHub writes. Checking a bearer token only when enqueueing is insufficient. |
| `frontend/src/settings.tsx`, `mcp.tsx`, `components/app-header.tsx`, `components/app-sidebar.tsx` | Reuse member-visible Settings navigation, breadcrumbs, divided records, permission choices, dialogs, clipboard feedback, and revocation patterns. Follow `docs/design.md`. |
| `collections/code-mortis/`, `backend/tests/test_noodle_collection.py` | Replace cookie-copy authentication for supported API requests. Update the current contract test, which presently expects nearly every browser API endpoint in the collection. |

Verify callers again when implementing. In particular, MCP authorization also calls the session helper directly; changing its signature or making it return a coroutine would break that flow.

## Token storage and lifecycle

Add an Alembic migration and `PersonalApiToken` model with an opaque UUID primary key, owner ID, name, unique indexed token hash, display prefix, permission list, repository IDs, creation time, expiration time, last-used time, and revocation time. Keep repository IDs as authorization constraints even if a repository later becomes unavailable; an unavailable selection never expands access.

Generate `cm_pat_` followed by `secrets.token_urlsafe(32)`. Validate the bounded format before lookup. Store only `digest(full_token)`, using the existing SHA-256 helper. This is a randomly generated 256-bit secret, not a user-chosen password. Do not use recoverable encryption or password reset/recovery for PATs. The display prefix contains only a short identifying portion; never expose the stored hash.

Return the full token only in the successful creation response after its database transaction commits, with `Cache-Control: no-store`. List/detail responses contain metadata only. A lost response cannot recover the secret: list and revoke the unused token, then create another. Token creation is never automatically retried by the frontend. No token value goes into logs, exception messages, job payloads, URLs, query caches, localStorage, or sessionStorage. Redact the `cm_pat_` format in the shared text scrubber, API 401/403/422 error paths, frontend telemetry, lifecycle events, and worker diagnostics; test bare tokens as well as Authorization headers.

Add a nullable authentication timestamp to `LoginSession` through the additive migration. Set it only on successful GitHub callback. Existing sessions with an unknown timestamp continue to work in the frontend but must sign in again before issuing a PAT. Permit only the fixed API-token Settings return destination through the existing browser-bound login flow; do not accept arbitrary return URLs. Do not collect a GitHub password or implement a new identity provider. The freshness requirement is an issuance safeguard; it does not shorten ordinary browser sessions or block emergency revocation.

Record creation and revocation as small structured lifecycle audit events, containing token ID, actor ID, event, time, and a bounded reason code. Use a feature-local lifecycle table if no suitable durable audit surface exists; do not add a general audit framework or record request bodies. Failed authentication is rate-limited and logged with a fixed reason and request reference, never submitted credentials. Retain revoked records while durable work refers to them. Document retention before adding cleanup; cleanup is not required to enforce expiration.

Every request checks the token row and current owner/membership. Do not cache a successful authentication decision in the first implementation. Update `last_used_at` with a conditional write at most every five minutes and describe it as approximate. Validate permissions and selected repository membership again on execution and external-effect boundaries.

Reuse the existing Redis counter pattern with a separate PAT namespace. Proposed initial limits are 20 failed authentications per minute per trusted client IP, 120 accepted requests per minute per token, and 10 token creations per minute per user. Return 429 with `Retry-After`; if the limiter is unavailable, fail PAT admission and creation with a recoverable 503 instead of falling back to a browser session or issuing a secret. Browser-session revocation must still commit through PostgreSQL when Redis is unavailable; its durable cleanup can retry later. Verify client IP handling behind the documented reverse proxy before relying on IP buckets. These limits are abuse protection, not spending limits.

## HTTP contracts and authentication boundaries

| Endpoint | Authentication | Contract |
| --- | --- | --- |
| `GET /api/v1/api-tokens` | Browser session | Owner-only paginated metadata, active/expired/revoked state, allowed permission descriptions, expiration policy, and active count. No secrets or hashes. |
| `POST /api/v1/api-tokens` | Browser session plus existing CSRF/origin checks | Validated name, explicit permissions/repositories, expiration days. Return 201 with metadata and a one-time `token` field. Validate selection against current access. |
| `DELETE /api/v1/api-tokens/{token_id}` | Browser session plus existing CSRF/origin checks | Idempotent owner-only revocation. Other users' token IDs return 404. Return 202 after revocation commits and durable cancellation is scheduled; do not claim all jobs have already stopped. |
| `GET /api/v1/auth/token` | PAT only | Current user identity, token ID, permissions, selected repository IDs, and expiration. No CSRF value or browser-session creation. This replaces `/auth/me` in the bearer smoke suite. |

Use strict Pydantic inputs and generated OpenAPI types. Validate lengths, duplicate/unknown permissions, integer repository IDs, empty selections, and expiration bounds. Token-management requests with a bearer credential are rejected even if a browser cookie is also present. Tokens cannot mint, extend, broaden, list, or revoke tokens.

For protected REST requests, resolve auth once into `request.state` through a dedicated asynchronous generator dependency. Call synchronous database authentication through the existing thread-pool facility, then bind the PAT execution context in the async request task around the dependency's `yield`, resetting it on exit. Synchronous handlers and enqueue calls inherit that context when FastAPI starts their worker thread. Never set it in a synchronous dependency and expect it to propagate back. Keep `current_user()` synchronous and session-only for existing direct callers, including MCP. Test HTTP-to-handler-to-enqueue propagation, concurrent request isolation, exceptions, and cleanup.

Bearer authentication accepts only the Authorization header over HTTPS, with the existing local HTTP development origins as the documented exception. Reject an Authorization value longer than 200 characters, duplicate credentials, or a token that does not exactly match `cm_pat_` plus 43 base64url characters before hashing or lookup. No token query/body parameters, cookie-to-token conversion, or permissive CORS change. Missing/invalid/expired/revoked credentials return 401 and a Bearer challenge; insufficient scope or current permission returns 403, with existing resource-hiding 404 behavior retained where appropriate. Error bodies never reveal whether a guessed token belongs to a particular user.

Cookie requests retain their existing CSRF and origin checks. Bearer-only requests do not use browser CSRF tokens. Keep a restrictive origin policy and existing CORS settings; never combine a PAT's authority with an ambient cookie. A valid MCP token must fail PAT authentication, and a valid PAT must fail MCP authentication.

## Permissions and supported routes

Maintain a finite permission catalog and an explicit policy keyed by HTTP method and FastAPI route template. The [route policy appendix](../api-token-route-policy.md) classifies the baseline contract and proposed routes before implementation. Do not authorize by path prefix or GET-versus-POST alone. Every protected operation must be classified as PAT-supported with its required permission or session-only. A new/unmapped operation is denied to PATs, with a route-inventory test catching omissions.

| PAT permission | Supported operation families |
| --- | --- |
| `repositories:read` | Selected repository discovery and metadata. Lists, filter choices, counts, and totals respect the same selection. |
| `reviews:read` | PR inbox/details, runs, saved/current findings, candidates, coverage, and resolution history for selected repositories. |
| `reviews:write` | Resolve/reopen/dismiss findings, ignore/restore PRs, and retry finding/overview synchronization under existing state and GitHub write checks. The UI explains resulting GitHub updates and cancellation effects. |
| `reviews:run` | Start, retry, cancel, and publish review runs; retry assessment/summary model stages; repository refresh where supported. Clearly disclose model usage and that review execution can publish GitHub comments. |
| `security:read` | Selected repository scans, reports, coverage, assessments, findings, and history. |
| `security:write` | Finding triage and rotation acknowledgments, preserving reason/version checks. |
| `security:run` | Start, retry, and cancel scans using an authorized connection. Disclose provider consumption. |
| `connections:read` | Secret-free connection metadata/model selection available for at least one selected repository. Existing connection-owner and repository grant checks still apply. |
| `analytics:read` | Analytics computed from selected and currently accessible repositories, including all facets, totals, and denominators. |

Permissions do not imply one another. The UI may select several explicitly for a workflow, but the stored grant remains the exact selected set. Map each concrete route before implementing the permission dependency. Review GET routes that call providers or GitHub rather than treating every read as inert.

Keep `/auth/me`, logout, token management, setup callbacks, GitHub installation/team administration, repository configuration/background generation and status, provider credential changes/draft-model discovery/tests, personal-provider authorization/grants/console tickets, all MCP management/consent/approval routes, and the security GitHub-issue workflow from preview through publication/recovery session-only for this release. Public health/setup status and signed webhooks retain their own existing authentication contracts. Native runner service credentials remain unrelated.

Enforce effective access as the intersection of token permissions, token-selected repositories, current membership/role, current GitHub permissions, active installations/repositories, and personal-connection consent. Tokens never grant authority that their owner lacks. Set `user.api_token_repository_ids` for the PAT and use one transport-neutral effective-repository accessor in discovery, `authorize_repo()`, `repo_access()`/`pr_access()`, analytics Scope, inbox, and security summaries. Preserve existing MCP restrictions separately; do not repurpose `mcp_repository_ids` to pretend a PAT is an MCP grant. Filter analytics metadata, repository choices, grade/status counts, registered totals, and denominators before returning them. Connection lists/model lookup must enforce the selected-repository relationship even when the client omits `repository_id`, and strip unselected repository IDs/names/counts from nested connection metadata as well as filtering rows.

## Queued work and revocation

Store PAT origin using an explicit `pat` record containing the token's non-secret ID, owner, required permission, and repository context in existing job/run/scan/intent JSON. The token row remains authoritative; a scope snapshot is only evidence, never continuing authorization. Propagate origin to child jobs, summaries, checks, assessment, verification, and pending finding synchronization. No raw credential is persisted in any job or model context.

Reuse the existing durable provenance and cancellation checkpoints while keeping PAT and MCP origins distinguishable. Extract only the shared code both transports need; do not copy the entire MCP protocol into a new subsystem. Legacy `mcp` records and jobs without token origin retain their current semantics. Explicit retries adopt the initiating session or token only when the user deliberately requests the retry; background retries cannot erase or replace origin.

Revalidate token expiry/revocation, membership, role, selected repository, current GitHub permission, and connection consent before worker admission, each new paid stage, each GitHub publication batch, and recovery/retry. On owner removal/disable, revoke permanently so re-adding the account cannot resurrect old credentials. On token revocation, cancel only its unfinished work through existing review/scan lifecycle functions, stop pending synchronization/publication, and preserve completed results and remote publication IDs. Expiration is enforced at the same checkpoints; the scheduler may sweep expired origins to settle blocked work, but expiry never depends on that sweep.

Within the revoke transaction, mark the token revoked, record its lifecycle event, and enqueue an idempotent internal cancellation job containing only its token ID. Commit before cancellation/draining begins. This internal cleanup is not a PAT-authorized job and cannot start model work or publish new content. The API returns after that commit; ordinary worker checkpoints enforce revocation immediately while durable cleanup settles unfinished work. Membership-removal and disable paths use the same transaction boundary.

After revocation commits, new admissions and subsequent execution checkpoints must deny access. An external request already authorized and sent cannot be unsent. Preserve uncertain/partial publication and reconcile it without issuing a new content publication. Specify and test the narrow cleanup allowed to close already-created GitHub checks; cleanup cannot start model work or publish new findings. Keep the established job-before-run/scan lock order and never hold a revocation row lock through a network request. Cleanup failure must leave the token revoked and a retryable job, never restore access.

## Settings and Noodle workflow

Add a member-visible `API tokens` section at `/settings?tab=api-tokens`. Extend Settings navigation, breadcrumbs, and account-menu links through the existing navigation definitions. The feature manages the current user's tokens; do not make the existing administrator-only `/settings` response a prerequisite for members.

Reuse divided record rows and existing dialogs. Show name, display prefix, permission/repository summary, expiration, approximate last use, and Active/Expired/Revoked text. Default to active tokens and use server pagination for history. The create dialog has a name, finite expiration select, grouped permission checkboxes, and explicit repository selection. Execution permissions explain quota usage and GitHub effects. No new component library or visual redesign is needed.

Successful creation opens one show-once panel held only in component memory, with a selectable read-only token, Copy action, clipboard success/failure feedback, and clear instructions to store it in Noodle. Clear the value when the panel closes or the component unmounts. Never place it in a reusable query result or toast payload. Closing/refreshing loses it permanently; explain revoke-and-create recovery. Disable duplicate submission and do not silently retry an uncertain creation response. Concurrent submissions can create distinct tokens within the server's atomic active-token cap; retry cannot recover an earlier secret. Retain the affected token ID/name for recovery when available, and let the user revoke unused records from the metadata list. Include `reauth_required` in the management metadata so a stale browser session can complete GitHub sign-in before filling the create form.

Revocation identifies the token and explains that clients and unfinished token-originated work will stop. Restore focus to the triggering control. Cover loading, empty, expired, revoked, unavailable repositories, validation, denied access, rate-limit, and lost-response states with visible text. Verify keyboard use, screen-reader status announcements, mobile overflow, focus trapping, and light/dark themes.

For Noodle, retain `public/` for unauthenticated health/setup-status requests and `api/` for PAT-supported operations only. Declare `cm_api_token` as a secret in both environments, store it with `noodle secret set`, set a bearer override in `api/folder.yml`, and give every supported request `auth: { type: inherit }`. Remove the cookie/CSRF pre-script from that folder while retaining resource-input validation. Keep the collection cookie jar disabled. Public requests omit authentication. Do not add automatic cookie fallback when a PAT is missing or invalid.

Use `/auth/token` for bearer identity. Remove session-only request files from the runnable collection and list their supported browser workflow in the README; keep any HTTP examples as fenced documentation, not executable YAML under another runner folder. Do not advertise that all existing 98 files work with a PAT. Rewrite the collection contract test to compare the explicit route policy, exact PAT/public request set, per-file auth inheritance, and documented exclusions; retain browser cookie/CSRF regressions in backend API tests. Add a read-only PAT smoke tag with its required permissions and repository selection. Do not include model execution or GitHub publication in an automatic smoke suite.

## Ordered implementation tracker

| Order | Work | Completion evidence |
| --- | --- | --- |
| 1 | Inventory every route; finalize the permission/session-only policy and async propagation approach. | Exhaustive route-policy test and a small HTTP-to-enqueue provenance regression. Every operation classified; browser-only boundaries explicit. |
| 2 | Add token storage/migration, lifecycle service, session-only management contracts, hashing, limits, and redaction. | Upgrade existing data without loss; one-time disclosure/hash-only persistence; owner isolation; expiration/cap/revocation races; secrets absent from errors/logs/audits. |
| 3 | Add PAT authentication, permission checks, and repository filtering. | Positive/negative tests for every supported family; unknown policies denied; mixed credentials rejected; cookie/CSRF and MCP isolation preserved; cross-repository facets/counts blocked. |
| 4 | Extend durable origin and execution/revocation checkpoints. | Revoke/expire between enqueue and execution, between model stages, and between publication batches; affected work stops while sibling session/MCP jobs and saved history remain intact. |
| 5 | Add the Settings UI and regenerate OpenAPI/frontend contracts. | Production frontend build and focused Playwright admin/member, keyboard, desktop/mobile, light/dark, creation, copy failure, lost-response recovery, and revoke checks. No raw secret in durable browser state. |
| 6 | Convert supported Noodle requests and document exclusions. | `noodle collection audit`, actual Noodle HTTP bearer read/write and refusal tests against the isolated database; no cookie/CSRF handoff. |
| 7 | Run regression checks and controlled development acceptance; document operations and evidence. | Backend suite and Ruff, API type drift/build checks, browser/MCP regressions, migration/restore check, then actual Settings-to-Noodle create/use/revoke on the development instance. |

Do not enable token-origin execution or publication until its durable revalidation checks are complete. These are dependencies, not optional follow-up hardening.

## Acceptance and deployment

Required security cases include malformed/oversized/multiple credentials; forged, expired, and revoked tokens; invalid scopes/repository selections; token-owner isolation; removed membership and role/repository changes; unmapped routes; PAT/MCP credential separation; session-only management; concurrent creation/revocation; stale ORM/cache state; missing CSRF on cookie writes; and accidental secret disclosure. Prove that a denied token cannot inherit a valid cookie's authority.

Run the real Noodle binary against HTTP and the isolated test database for an authenticated read, a local reversible mutation, and expiry/revocation refusal. Keep GitHub/provider stubs clearly separate from real integration evidence. The final development check is browser token creation, secure Noodle setup, selected-repository reads, a permitted reversible action, browser revocation, and the next request failing. Real paid model calls or public GitHub writes require separately authorized test targets; do not run them merely to validate authentication.

Deploy the additive migration and all PAT-aware workers before opening API token creation to users. Coordinate API/worker restarts using the existing operations procedure so old workers cannot consume PAT-origin jobs without the new gates. No new credentials are sent to the native runner. Document HTTPS/reverse-proxy expectations, token replacement, incident revocation, backup behavior, last-use precision, limits, and 401/403/429/503 recovery in `docs/operations.md`. Update `docs/acceptance.md`, the collection README, and API descriptions during implementation.

A code-only rollback is unsafe while PAT-origin work is queued: older workers would not enforce its authorization. Before downgrade, stop admission, revoke tokens, cancel/drain their unfinished jobs and pending publication/sync intents, and verify no executable PAT-origin work remains in `Job.payload["pat"]`, `ReviewRun.progress["pat"]`, `SecurityScan.progress["pat"]`, `Finding.sync_intent["pat"]`, or `SecurityFinding.issue["pat"]`. Only then restore old code while retaining additive tables and history. Do not delete the new tables as a rollback shortcut.

The definition of done is a user creating an appropriately restricted token in Settings, storing it securely in Noodle, using supported requests without browser-cookie copying, and revoking it with enforced refusal of subsequent requests and future work. Existing browser login, repository permissions, personal connection consent, and MCP authorization must still pass their regressions.

## Primary guidance and design rationale

- [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) supports least privilege, lifecycle auditing, expiration, and prompt revocation. The exact lifetime, limits, and metadata choices above are Code Mortis product decisions.
- [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html#require-re-authentication-for-sensitive-features) supports reauthentication for sensitive operations. A recent GitHub callback before issuing a long-lived credential is the proposed local safeguard; it is not a claim that Code Mortis can force a new GitHub password or MFA challenge.
- [OWASP REST Security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html) supports transport protection, per-endpoint authorization, rate limiting, safe logging, and keeping credentials out of URLs. A token is an authentication credential, not a replacement for authorization checks.
- [GitHub personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) illustrate user-bound permissions and selected repository access; a token cannot give its owner additional capabilities. These Code Mortis tokens are not GitHub PATs and cannot be used directly against GitHub.
- [AnyIO context propagation](https://anyio.readthedocs.io/en/stable/threads.html#context-propagation) explains why binding request context in the async task before entering synchronous handlers is valid, while expecting a synchronous dependency's context changes to propagate back is not.

Planning review used three OpenCode plan sessions on `opencode-go/muse-spark-1.3-contributor` for backend permissions, durable revocation, and Settings/Noodle workflow, followed by two review passes on the reconciled plan. The final review reported ready as a plan. Reviewer suggestions were checked against source and primary documentation; conflicting proposals such as cached successful authentication, automatic cookie fallback, and code-only rollback were not adopted. This is planning evidence, not implementation or integration acceptance.
