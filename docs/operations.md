# Operations

## Environments and configuration

Production uses `docker compose` and `.env`. Development uses `docker compose --env-file .env.dev -f compose.dev.yaml` and a separate project, database, Redis, runner profile volume and App registration. Never point both instances at the same database or GitHub webhook channel.

`CM_PUBLIC_URL` is the exact browser origin, including a development port. OAuth and installation callbacks use it. `CM_WEBHOOK_URL` overrides only the webhook destination. Use a dedicated Smee channel for local development and enable `--profile webhooks`. Keep secure cookies enabled for HTTPS and disabled only for local HTTP. Setup reports inconsistent origins, cookies and webhook configuration before registering an App. Two login tabs have independent single-use attempts tied to a stable browser cookie; switching browsers still requires starting again there.

The production API binds loopback port 8080. Terminate TLS with a reverse proxy supporting WebSockets and long-lived connections. For Caddy, a minimal site is:

```caddyfile
reviews.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

The runner has no published port, database credentials, installation keys or Docker socket. Keep its network private and retain its service bearer token in `.env`. Run exactly one runner ASGI process and one scheduler. Each review worker uses concurrency one because OCR's configuration is process-global. The runner serializes each personal connection independently. Do not set `CM_OCR_ISOLATED_RUNTIME=true` on a normal workstation account.

## Upgrade an existing installation

1. Back up the database, `.env` and native profiles. Let active reviews finish or cancel them in the app, then stop API, review-worker, sync-worker and scheduler. Keep the database available for backup and migration.
2. Check out the intended version. Run `python3 backend/code_mortis/manage.py upgrade-env --file .env`. It adds only a missing runner service token and preserves existing keys, URLs and credentials. Never regenerate the encryption key for an existing database.
3. In GitHub's settings for the **existing App**, set Callback URL to `CM_PUBLIC_URL/api/v1/auth/callback`, Setup URL to `CM_PUBLIC_URL/api/v1/installations/callback`, disable **Request user authorization (OAuth) during installation**, and set the webhook to `CM_WEBHOOK_URL` or `CM_PUBLIC_URL/api/v1/webhooks/github`. Retain the App ID, private key, client credentials and webhook secret. Contents read, Pull requests write and Metadata read remain required. The setup URL is distinct from the App manifest redirect used during initial registration.
4. Run `docker compose build`, then `docker compose run --rm migrate`, then `docker compose up -d`. Migration is additive; existing connections become shared APIs. Pending old OAuth attempts are intentionally invalidated, so restart an interrupted login.
5. Verify readiness, a fresh GitHub login, installation discovery, repository permissions and webhook delivery. Do not recreate the App or clear the database to address a callback error. Settings → GitHub displays the expected registration URLs. Finish by validating one manual and one automatic review using a repository authorized for tests.

See [GitHub registration settings](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration). Never start two active copies of a restored instance against GitHub.

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
