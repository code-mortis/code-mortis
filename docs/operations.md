# Operations

## Environments and configuration

Production uses `docker compose` and `.env`. Development uses `docker compose --env-file .env.dev -f compose.dev.yaml` and a separate project, database, Redis, runner profile volume and App registration. Never point both instances at the same database or GitHub webhook channel.

`CM_PUBLIC_URL` is the exact browser origin, including a development port. OAuth and installation callbacks use it. `CM_WEBHOOK_URL` overrides only the webhook destination. Use a dedicated Smee channel for local development and include `webhooks` in the persistent `COMPOSE_PROFILES` setting. Keep secure cookies enabled for HTTPS and disabled only for local HTTP. Setup reports inconsistent origins, cookies and webhook configuration before registering an App. Two login tabs have independent single-use attempts tied to a stable browser cookie; switching browsers still requires starting again there.

The production API binds loopback port 8080. Terminate TLS with a reverse proxy supporting WebSockets and long-lived connections. For Caddy, a minimal site is:

```caddyfile
reviews.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

The runner has no published port, database credentials, installation keys or Docker socket. Keep its network private and retain its service bearer token in `.env`. Run exactly one runner ASGI process and one scheduler. Each review worker uses concurrency one because OCR's configuration is process-global. The runner serializes each personal connection independently. Do not set `CM_OCR_ISOLATED_RUNTIME=true` on a normal workstation account.

## Optional code graph parser

The isolated parser, authorized indexing, internal retrieval and browser repository activation are implemented locally. External code MCP and review enrichment remain later phases. Existing installations do not start a parser and do not install its grammar dependency group. Adding this service alone does not enable repository graphs.

The parser service lives directly in `compose.yaml` and `compose.dev.yaml`, behind the `code-graph` profile. Fresh `init-env` configurations include a separate `CM_CODE_GRAPH_TOKEN`; existing installations run `python3 backend/code_mortis/manage.py upgrade-env --file .env` (or `--file .env.dev`). This adds missing service tokens without rotating existing credentials or changing active profiles.

Set `COMPOSE_PROFILES=code-graph` in the instance environment file once. Preserve other enabled profiles; for development with the webhook relay use `COMPOSE_PROFILES=webhooks,code-graph`. Then use the normal commands: `docker compose up -d --build` in production, or `docker compose --env-file .env.dev -f compose.dev.yaml up -d --build` in development. No graph-specific Compose file or repeated `--profile` flag is required. Omit explicit `--profile` flags when using the persistent list, because those flags override `COMPOSE_PROFILES`. Coolify keeps its normal overlay; configure the profile and token in that deployment's environment. Starting the parser with an empty token fails immediately. Leaving the profile disabled requires no graph token and starts no parser.

Only the trusted review worker and parser join the dedicated internal network. That network is declared even while the optional parser is disabled. The parser gets only its service token, with no app env file, GitHub/database/Redis/model credentials, source volume or Docker socket. Keep one parser replica and one ASGI worker. Limits are one CPU, 256 MiB total memory with no swap, 128 MiB temporary storage, 32 process IDs and a 120-second request deadline. A busy parser rejects additional extraction; an oversized, malformed, timed-out or killed extraction fails explicitly and cannot publish a usable snapshot. A worker disconnect kills the active parser child. No embedding/model API is involved.

In a repository, open **Configure → Project context → Code graph**, switch it on and Save. This is independent of Enable reviews and requires a browser administrator with current GitHub write access. Existing repositories remain off. The enabling user is recorded; queued work rechecks that user’s membership and repository access. The existing reconciliation schedule checks enabled repositories for default-branch changes. Status shows queued/running/ready/partial/stale/failed/cancelled, the observed revision and previous usable revision. Retry explicitly resumes failed/cancelled attempts and records the requesting administrator. Cancel pauses automatic updates until Retry or disabling/re-enabling. Saving off cancels active work and stops code access; retained data follows the existing cleanup policy. Existing PAT and MCP grants cannot read or manage code graphs.

To disable the service, first turn off the repository settings, then stop `code-graph-parser` with the normal Compose command, then remove `code-graph` from `COMPOSE_PROFILES` while preserving other profiles. Removing a profile alone does not stop an already running container. The parser has no persistent data volume to delete.

Indexing uses an additional named `code-graph-data` volume on the trusted review worker, owned by UID/GID 10001. Rebuild the application image before starting that worker so a new volume gets the correct ownership. All review-worker replicas must share this same POSIX-lock-capable volume. The parser and native runner never mount it. The volume contains private repository Git objects, including excluded files retained as part of a commit, even though excluded bytes never go to the parser. Protect its backups like the source repositories.

Back up `code-graph-data` together with PostgreSQL while graph work is stopped. Restoring graph rows without this volume leaves source unavailable; lookup must never substitute another revision. Retention keeps the latest successful default-branch snapshot and one other usable snapshot for seven days, removes terminal attempt records after seven days, and prunes orphan graph refs/objects under the shared lock. A periodic cleanup job runs only after graph configuration/work exists. Failed/cancelled attempts require explicit retry; successful identities remain immutable. Do not delete Git refs manually while workers run. Graph cleanup never deletes saved review evidence. Missing objects require restoring the matching volume or a deliberate graph-cache rebuild through an operator-controlled cache rebuild while graph work is stopped; Retry does not rewrite immutable ready snapshots.

Initial indexing admits at most two pending snapshots per repository and sixteen per instance, with one extraction at a time. Git acquisition monitors 256 MiB per repository and 1 GiB total on disk, alongside the parser's source limits. These polling limits can overshoot during a fetch and are not hard disk quotas. Disabling the parser profile does not erase retained graph data or change repository opt-ins; disable repository opt-ins separately if code access must stop. A missing parser produces a failed indexing attempt with an explicit Retry action after infrastructure is restored.

The current tree admission limit counts all Git tree entries, including excluded and unsupported files: 1,000 total, alongside 1 MiB per admitted file and 8 MiB total source. Oversized trees fail explicitly. See [measured storage and corpus limits](code-graph-phase1de.md); these measurements do not establish full-stack 8 GB capacity.

To verify locally, run `python3 scripts/check_code_graph.py` from the repository root. It builds disposable images, verifies the real private-network transport, and tests cancellation, the full deadline, tmpfs exhaustion and memory enforcement. It uses a separate Compose project and removes that project's containers/network afterward. Run ordinary backend graph tests against `compose.test.yaml`. The optional test stack is `compose.code-graph.test.yaml`; the existing database/test services remain separate.

The protocol, publication and retention requirements are documented in [checkpoint 1B and the handoff](code-graph-plan.md#phase-1-execution-checklist). These parser limits do not establish full-stack operation on an 8 GB server; that remains a Phase 2 measurement.

## Upgrade an existing installation

1. Back up the database, `.env` and native profiles. Let active reviews finish or cancel them in the app, then stop API, review-worker, sync-worker and scheduler. Keep the database available for backup and migration.
2. Check out the intended version. Run `python3 backend/code_mortis/manage.py upgrade-env --file .env`. It adds missing runner/parser service tokens and preserves existing keys, URLs, credentials and profiles. Never regenerate the encryption key for an existing database.
3. In GitHub's settings for the **existing App**, set Callback URL to `CM_PUBLIC_URL/api/v1/auth/callback`, Setup URL to `CM_PUBLIC_URL/api/v1/installations/callback`, disable **Request user authorization (OAuth) during installation**, and set the webhook to `CM_WEBHOOK_URL` or `CM_PUBLIC_URL/api/v1/webhooks/github`. Retain the App ID, private key, client credentials and webhook secret. Contents read, Pull requests write and Metadata read remain required. The setup URL is distinct from the App manifest redirect used during initial registration.
4. Run `docker compose build`, then `docker compose run --rm migrate`, then `docker compose up -d`. Migration is additive; existing connections become shared APIs. Pending old OAuth attempts are intentionally invalidated, so restart an interrupted login.
5. Verify readiness, a fresh GitHub login, installation discovery, repository permissions and webhook delivery. Do not recreate the App or clear the database to address a callback error. Settings → GitHub displays the expected registration URLs. Finish by validating one manual and one automatic review using a repository authorized for tests.

See [GitHub registration settings](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration). Never start two active copies of a restored instance against GitHub.

## GitHub review checks

After deploying the check migration, open the existing GitHub App's **Permissions & events** settings. Add **Repository permissions → Checks → Read and write** and save. Confirm the **Check run** webhook subscription, then approve the new permission in each account's installed-App settings. Do this separately for development and server Apps; retain their existing credentials, callbacks and webhook URLs. New registrations request the permission automatically.

Reviews and comments keep working while approval is pending. The run page shows the permission error under **GitHub check**. Permission acceptance triggers recovery, with periodic reconciliation every five minutes as a fallback. No model rerun is needed to repair a check update. If an installation is removed, remote checks cannot be updated until access is restored; the app retains the synchronization error.

The check is queued while waiting, in progress during analysis and publication, and successful only after full coverage and comment publication. Findings do not fail a completed check or approve the PR. Partial coverage, execution errors and publication failures fail it; timeouts, cancellation, skipped work and blocked authentication/quota have distinct conclusions. Repository branch-protection settings are unchanged.

GitHub's per-check **Re-run** requires an enabled Code Mortis member with current repository write access. It rejects closed PRs and obsolete commits. For complete analysis with failed publication it retries saved comments; for blocked work it resumes the existing run. Other retries create a new manual run with current repository settings and the original connection. Personal connections remain owner-only, without provider fallback. Use Code Mortis to cancel a running review; its check follows the cancellation. Check-suite-wide reruns are not handled.

Checks are added to new runs and unfinished work during migration, not completed historical runs. Details links use `CM_PUBLIC_URL`; development localhost links are reachable only on the development machine. Check metadata and errors are included in normal database backups. Logs identify `sync_check` jobs and their run IDs; repeated GitHub failures recover independently of paid review execution. Never mark a check successful manually to hide missing coverage or failed publication.

## Backup and restoration

PostgreSQL owns durable jobs and history. Redis can be rebuilt. `.env` and the native profile volume require separate encrypted backups; database ciphertext cannot be recovered without `CM_ENCRYPTION_KEY`. Native client profiles contain authentication credentials. Store the archive privately and never upload it as a diagnostic attachment.

With work quiesced, make private backups:

```sh
umask 077
docker compose exec -T postgres pg_dump -U mortis -d mortis -Fc > mortis.dump
# Stop authentication and review activity before archiving profiles.
docker compose stop api review-worker sync-worker scheduler runner
docker run --rm --network none -v code-mortis_runner-profiles:/profiles:ro postgres:17-alpine tar -C /profiles -czf - . > runner-profiles.tar.gz
```

The volume name above is for the default production project; use `docker volume ls` to check a custom project name. Copy `.env` into your encrypted backup store. Restart services only when the backup is complete.

Restore into a **new isolated installation**, retaining the original encryption key and matching App settings. Do not start workers while restoring:

```sh
docker compose up -d postgres redis
docker compose exec -T postgres pg_restore -U mortis -d mortis --no-owner --exit-on-error < mortis.dump
docker compose create runner
docker run --rm -i --network none -v code-mortis_runner-profiles:/profiles postgres:17-alpine tar -C /profiles -xzf - < runner-profiles.tar.gz
docker compose run --rm migrate
docker compose up -d
```

Restore profile ownership as UID/GID 10001 and private directory permissions. Use the same pinned client versions first, then upgrade deliberately. Providers can revoke or expire restored sessions; verify native account status and reconnect through the app if needed. A missing archive does not destroy review history: reconnect affected subscriptions. Inspect pending jobs before enabling dispatch or accepting webhooks. Test restoration privately before returning service to users.

The repository includes `backend/tests/restore_smoke.py` for a disposable database restored from an offline copy of the original volume. It fingerprints original columns and verifies that additive migrations preserve every old record. Its source/target names are deliberately restricted; see [acceptance evidence](acceptance.md) for the completed test.

## MCP access

Authenticated MCP is implemented in this codebase and off by default. Upgrade adds tables through additive migration `20261024_mcp` and the pinned `mcp==2.3.0` dependency, so rebuild the dependency image (`docker compose build`) and run `docker compose run --rm migrate` before starting workers. Restart `review-worker`, `security-worker`, `verification-worker`, `summary-worker`, `sync-worker` and `scheduler` after the code change so provenance validation and cleanup run everywhere. No GitHub App permission change is needed for MCP itself.

Settings → MCP access holds three independent instance switches, all off after upgrade: reviews MCP, security MCP, and management through MCP. Each switch takes effect without a restart. Disabling a domain immediately revokes its grants and refresh families; clients must consent again after re-enabling. Administrators also manage the allowed client-metadata hostnames and pre-registered clients there. Members see only the instance status and their own connected grants with revoke controls.

The two resource endpoints live on the public origin: `CM_PUBLIC_URL/mcp/reviews` and `CM_PUBLIC_URL/mcp/security`. Discovery metadata is served at `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource/mcp/{reviews,security}`; the OAuth endpoints are `/oauth/authorize`, `/oauth/token`, `/oauth/revoke` and `/oauth/register`. A review token never works on the security endpoint and vice versa. The native runner is unchanged and isolated: no MCP configuration, no published port, no database or GitHub credentials.

Each connection starts in the browser. The agent directs the user to `/mcp/authorize?request_id=...`, which requires the normal GitHub login and shows the client, exact callback, domain, repositories and permissions. Read permissions start selected; write, run and management permissions need explicit selection, and at least one repository is required. Deny returns an access-denied redirect to the client. Grants last 30 days, access tokens 15 minutes, authorization codes 60 seconds single-use; refresh tokens rotate atomically and reuse revokes the family. Each user holds at most 100 active grants; the instance holds at most 1000 registered clients. Revoking a grant in Settings → MCP access stops new MCP work at once and cancels that grant's queued, running or blocked reviews, scans and jobs while preserving saved results and unrelated work.

Client registration accepts pre-registered public clients with exact redirect URIs (PKCE S256 required, at most 10 URIs, no secret). Otherwise the server fetches Client ID Metadata Documents only from administrator-allowed hostnames over HTTPS with a 5 second timeout, 64 KB cap, no redirects, no private or metadata addresses and a pinned resolved IP. Dynamic registration at `/oauth/register` is a compatibility path only: it requires an enabled domain, allows loopback or allowed hostnames, and is limited to 20 requests per minute per address.

Public security-issue publication and destructive management actions pause for human approval. The agent receives an approval URL of the form `CM_PUBLIC_URL/mcp/approvals/<id>`; the owner opens it in the browser, checks the exact arguments and summary, then approves or denies. Approvals expire after 5 minutes, are single-use, and are bound to the granting user, tool and argument digest. Native credential entry never goes through MCP: sign-in tools return a trusted Settings URL and the browser completes device authorization or the login console. Raw tokens, codes and refresh values are never logged; only their hashes are stored, and the audit keeps tool, outcome and duration without arguments.

Limits and retention are enforced per minute: OAuth and tool calls 120 per address or grant, registration 20 per address, with `Retry-After: 60` on 429. The periodic reconciliation task deletes expired authorizations, tokens, operations and approvals, and audit rows older than 30 days; review and security history is never touched by this cleanup.

Troubleshooting: 401 means the token expired, the grant was revoked, or the wrong resource was requested, so connect the client again. 403 means the domain is disabled, the scope or repository was not granted, or the role changed, so check Settings → MCP access and re-consent. 409 means a code, refresh token, request key or approval was replayed, the arguments changed, or the approval expired or was already decided, so ask the agent for a fresh preview instead of retrying blindly. 422 on consent means the submitted scopes or repositories fall outside what was offered. After any disable or role change, revoke and re-consent; reusing an old code or refresh token revokes the family by design.

## Personal API tokens

Members create named personal API tokens in Settings → API tokens and use them from Noodle or scripts. A token acts as its owner, further restricted by its selected permissions and repositories. It never gains authority its owner lacks.

Deploy every PAT-aware worker before opening token creation: API, review-worker, sync-worker, scheduler, and the security/verification/summary workers. Older workers do not enforce PAT origin gates, so rolling restarts must finish while admission stays closed. Run `docker compose run --rm migrate` for the additive token tables first, then restart all workers using the existing upgrade procedure. Creation stays closed until `GET /health/ready` passes and every worker runs the new code.

Token setup: issuing requires a GitHub callback completed within the previous 15 minutes; otherwise Settings reports `reauth_required` and the member signs in again. Expiration is required, default 30 days, maximum 365 days, measured in server UTC. At most 20 active tokens per user. The full value is shown once with `Cache-Control: no-store`; only its hash is stored. A lost value cannot be recovered: revoke the unused record and create another. Replacement is create, update clients, verify, then revoke the old token. Signing out of the browser does not revoke tokens; removing membership or disabling the account revokes them permanently, and re-adding the account never resurrects old credentials.

Expiry and revocation are enforced at admission, each paid stage, each GitHub publication batch, and recovery/retry. Revocation commits first, then an internal cleanup job cancels only that token's unfinished work through the existing review/scan lifecycle; completed results and remote publication IDs stay. An external request already sent cannot be unsent. To revoke in an incident, delete the token in Settings or remove the member, then confirm its queued work drained before considering the incident closed.

Limits are abuse protection, not spending limits: 20 failed authentications per minute per client IP, 120 accepted requests per minute per token, 10 creations per minute per user, with 429 and `Retry-After`. If the limiter is unavailable, PAT admission and creation fail with a recoverable 503 rather than falling back to a browser session or issuing a secret. Browser-session revocation still commits through PostgreSQL while Redis is down; durable cleanup retries later.

PATs authenticate only through the Authorization header over HTTPS (local HTTP development origins excepted). Missing, malformed, expired, or revoked credentials return 401 with a Bearer challenge; insufficient scope or current permission returns 403, with resource-hiding 404 retained where the route already hides. A request carrying both an Authorization header and `cm_session` is rejected. Token values never appear in logs, errors, job payloads, URLs, or telemetry; the `cm_pat_` format is redacted by the shared scrubber.

When a reverse proxy terminates HTTPS, configure Uvicorn's `FORWARDED_ALLOW_IPS` in the deployment environment with the proxy's exact source IPs or networks, and have the proxy set `X-Forwarded-Proto`. The API uses the ASGI scheme and client address; it never trusts forwarding headers itself. Keep the API port private and avoid trusting arbitrary peers with `*`. Without trusted proxy configuration, a forwarded HTTP connection is refused even when the public URL uses HTTPS. Cleartext development requires `CM_COOKIE_SECURE=false`, a configured local HTTP public URL, and a local request hostname.

A code-only rollback is unsafe while PAT-origin work is queued. Before downgrading: stop admission, revoke tokens, cancel and drain their unfinished jobs and pending publication/sync intents, then verify no executable PAT-origin work remains in `Job.payload["pat"]`, `ReviewRun.progress["pat"]`, `SecurityScan.progress["pat"]`, `Finding.sync_intent["pat"]`, or `SecurityFinding.issue["pat"]`. Only then restore old code while retaining the additive tables and history. Never delete the token tables as a rollback shortcut.

Troubleshooting: 401 means the value is missing, malformed, expired, or revoked, so create a replacement. 403 means the token lacks the permission or the owner lost the repository access, so check the token selection in Settings and current GitHub permissions. 429 means the per-token or creation limit was hit, so wait for `Retry-After`. 503 on PAT admission means the limiter is down, so retry without changing credentials.

## Diagnosis

For OpenCode Go, select the OpenCode API provider and set the base URL to `https://opencode.ai/zen/go/v1`. Muse Spark 1.3 Contributor uses model ID `muse-spark-1.3-contributor` and the `openai-responses` protocol. Code Mortis supplies its own User-Agent and a fresh `x-opencode-session` for each OCR invocation, preserving it across tool calls. These headers are generated for the Go endpoint, not stored as static connection headers. The connection test reports bounded provider errors with stored credentials redacted. See [OpenCode Go client requirements and endpoints](https://opencode.ai/docs/go/). Contributor prompts and completions may be used for Meta model training; consider this before sending repository content.

Every HTTP response has an `X-Request-ID`; callback errors display that diagnostic ID. Job logs contain job ID, kind and error class. Runs retain their IDs, pinned commits, connection, owner, initiator, engine, verified progress and publication state. Webhook delivery IDs are persisted for deduplication. Raw auth console input, OAuth codes and provider requests are not logged. Do not put secrets into model IDs, rules, background text, options or URLs.

| Stage | Evidence and recovery |
| --- | --- |
| Startup | `docker compose ps` and `GET /health/ready` verify API, PostgreSQL and Redis. Inspect migration logs before retrying a failed startup. |
| GitHub login | Check browser origin, callback URL and cookie scheme. An expired, cancelled, replayed or different-browser attempt shows a recovery action. Start a fresh login; do not disable state checks. |
| Installation | Match the dedicated setup URL. Sign in if installation began at GitHub, then validate available installations with the user API. An installation ID is not authorization. |
| Webhook | Check GitHub deliveries and relay connectivity in development. Verify destination and signature secret. Duplicate delivery IDs are acknowledged without duplicate work. |
| Permissions | Check current repository access, installed repositories and instance membership. Reading requires access; manual reviews and finding mutations require write access. Removal/revocation prevents consumption. |
| API connection | Test connection performs an OCR tool call. Check protocol, tool support, model, endpoint and provider quota. No native subscription tokens belong here. |
| Personal auth | Settings → Connections → Manage account. ChatGPT requires device authorization enabled in account security settings. Claude uses its native login console, never a general shell. Status and reconnect controls are owner-only. |
| Native runner | Check `docker compose ps runner` and its health. The API and review worker must share its bearer token. Runner outage waits without changing provider. Restart retains profile volume; recheck account status. |
| Quota or expired session | The run is blocked, with a reason. Restore usage or reconnect the same account, then **Resume review**. Saved completed file checkpoints are reused. No automatic fallback to another account, model or paid API. |
| Git fetch | Temporary worker filesystems are intentionally `noexec`. GitHub installation credentials are supplied through URL-scoped Git configuration in the fetch process environment. They are not written to a helper script or passed to OCR. Keep `noexec` enabled; a helper permission error indicates an older worker that needs the fetch correction and a restart. |
| OCR token budget | This is an aggregate input/output threshold, including cached input, not a strict spending ceiling. OCR 1.12.11 checks estimates before dispatch and accumulated usage between main-loop requests; concurrent calls and subsequent processing can overshoot. Inspect `budget_exceeded` and coverage reasons. A larger threshold requires an explicit repository configuration change; cancellation cannot recover consumed tokens. |
| Review | Queued work needs scheduler and review worker. Failed or partial coverage is not a clean review. Native limits are context bytes and timeout, not an output-token guarantee. Cancellation is cooperative in the worker and enforced by native process deadlines. |
| Publication | Retry publication from the run. Saved findings are reconciled against GitHub markers first. New commits supersede old work; publication retry does not rerun OCR. |
| Resolution | Pending state remains visible until GitHub confirms. Retry a failed operation. Periodic reconciliation recovers missed events; absent later findings are not resolved automatically. |

`GET /health/live` proves only the API process is alive. Readiness does not prove workers, native authentication, GitHub or a paid provider work. Inspect connection status, background errors and real runs separately. Useful logs:

```sh
docker compose logs --tail=100 migrate review-worker sync-worker scheduler runner
```

## Access and recovery boundaries

Only explicitly allowed members can use an instance. The initial owner binds to a numeric GitHub ID. Personal accounts belong to that member; administrators cannot authenticate as them or authorize their subscription consumption. Grants permit administrator selection for the named repository, and automatic permission is explicit. Disconnect removes grants and cancels affected work even when the runner is offline; retry disconnect after recovery to remove any remaining native session. Reconnection never recreates grants automatically.

A native console ticket lasts 60 seconds, is single-use and is bound to the owner's authenticated session and origin. The console runs only `claude auth login`, expires after 15 minutes and closes on membership/session revocation. Authentication input is not retained. Reverse proxies must not record request bodies, WebSocket payloads or sensitive callback query strings.

The database persists queued work before dispatch. Worker leases recover interrupted jobs; repeated process failures become visible after three attempts. Publication reconciles uncertain responses before a retry. History is retained indefinitely. Review content and model output remain untrusted and need human judgment; no scripts or generated changes are executed.
