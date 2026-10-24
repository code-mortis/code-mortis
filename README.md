# Code Mortis

Self-hosted GitHub pull request reviews powered by [Open Code Review](https://github.com/alibaba/open-code-review). Findings become comments from your own GitHub App. Review runs, coverage, and resolution history stay in one workspace.

Shared API connections and personal ChatGPT and Claude Code subscriptions use the same review history and publisher. Official clients run on the server, with persistent private authentication profiles. Personal automation requires the subscription owner's consent for each repository. See [acceptance evidence](docs/acceptance.md) for implemented behavior, local checks and integrations still awaiting real accounts.

## Run your instance

Requires Docker Compose and Python 3.12 or newer to generate configuration. Production needs a public HTTPS origin. No personal GitHub bot account is required.

```sh
python3 backend/code_mortis/manage.py init-env --owner YOUR_GITHUB_LOGIN --url https://reviews.example.com
docker compose up -d --build
```

Point an HTTPS reverse proxy at `127.0.0.1:8080`, including WebSocket forwarding. Open the configured URL, enter `CM_BOOTSTRAP_TOKEN` from `.env`, and register your GitHub App. Sign in as the configured owner. In **Settings → GitHub**, install the app on selected repositories. Then add a connection and enable reviews in the repository's **Configure** panel. Never commit `.env`; retain its encryption key with your backups.

Registration, login and installation are separate flows. Always finish each flow in the browser where you started it. `CM_PUBLIC_URL` is the browser origin and callback origin. `CM_WEBHOOK_URL` optionally overrides only the webhook destination. Production defaults to `CM_PUBLIC_URL/api/v1/webhooks/github`.

## Develop locally with Docker

Development has its own database, Redis, profiles, secrets and GitHub App. The browser URL stays `http://localhost:5173`; Vite proxies the API and WebSockets. Obtain a dedicated channel at [smee.io](https://smee.io/) to relay GitHub webhooks.

```sh
python3 backend/code_mortis/manage.py init-env --output .env.dev --owner YOUR_GITHUB_LOGIN --url http://localhost:5173 --webhook-url https://smee.io/YOUR_CHANNEL
docker compose --env-file .env.dev -f compose.dev.yaml --profile webhooks up -d --build
```

Open [localhost:5173](http://localhost:5173), bootstrap with `.env.dev`, and register a **separate development GitHub App**. Its callbacks stay on localhost; only webhooks use the relay. HMR updates the frontend and the API reloads Python changes. Restart review-worker, sync-worker, scheduler or runner after changing their code. Restarting preserves data; `down -v` deletes development data. These commands do not reconfigure the production project.

All services, including the frontend, run in Docker. Keep port 5173 available so the browser origin and GitHub callbacks stay consistent.

## Connections and review workflow

- Administrators manage shared API connections. The catalog includes OCR's 29 native presets, OpenCode and custom endpoints. Protocols are OpenAI Chat Completions, OpenAI Responses, Anthropic Messages and Bedrock. Models must support tools. **Test connection** completes a real OCR tool-call round trip and can incur provider charges.
- Members manage their own personal subscriptions. **Connect account** starts official ChatGPT device authorization or a limited console running the unmodified `claude auth login` command. Enable device authorization in ChatGPT security settings if required. Neither flow exposes a general shell or converts subscription credentials into an API key.
- The owner grants repository access and separately opts into automatic consumption. An administrator can select an authorized connection for a repository but cannot grant consent for its owner. Manual subscription reviews use the initiating member's own connection. One execution runs per personal connection; waiting, expired sessions, quotas and runner outages remain visible. There is no automatic provider or paid-API substitution.
- Opening or updating an enabled, non-draft PR queues a review. External forks require a manual review by a repository writer. Each run pins commits, model, settings, engine, connection, owner and initiator.
- API reviews use `ocr review`. Subscriptions use `ocr delegate preview` and `ocr delegate rule`, then native clients analyze application-assigned files. Coverage is counted from validated file results, never a model's self-reported completion. Native clients get only explicit context and restricted read tools; repository scripts, agent instructions, hooks and MCP servers are not executed.
- API reviews use OCR's aggregate token budget, checked between requests; in-flight calls and subsequent processing can overshoot it. It is not a hard spending cap. Native reviews enforce **context bytes and elapsed time**, without a hard token/output cap. Reaching a bound produces incomplete coverage. Provider quotas still apply. Cancellation cannot refund already consumed usage.
- Findings and coverage are saved before publication. Valid diff locations become inline comments; other findings appear in the summary. Reviews use GitHub's `COMMENT` event and the App's bot identity. Publication-only retries reconcile existing review markers without repeating model analysis.
- Each new review has a **Code Mortis** GitHub check on its exact commit. It stays in progress through comment publication; success means complete coverage and published comments, with findings reported informationally. Timeouts, incomplete coverage, cancellation and failures remain explicit. The run page shows check synchronization separately. Existing Apps need **Checks: Read and write** and installation approval; see [upgrade instructions](docs/operations.md#github-review-checks).
- Resolve, reopen or dismiss findings in the app. Linked threads synchronize with GitHub, with visible pending changes until confirmed. Periodic reconciliation recovers missed events. Every run preserves its occurrences; a finding absent later is **not observed**, never automatically resolved.

API keys and extra headers are encrypted and never returned to the browser. Custom provider endpoints are administrator-controlled. Private gateways need `CM_ALLOW_PRIVATE_ENDPOINTS=true` or a narrow `CM_TRUSTED_ENDPOINT_HOSTS` allowlist. Deploy an egress policy if administrators or endpoints are not fully trusted. Bedrock credentials belong only in the review worker, not the native runner.

## Architecture and checks

The backend is organized into API routers, shared infrastructure, integrations, reviews, security scans, MCP, and schemas. See the [backend structure guide](docs/architecture.md) for the directory map and import rules.

FastAPI serves the production React build and API from one origin. PostgreSQL owns users, credentials, immutable runs, findings, deliveries and durable jobs. Celery/Redis dispatch review and synchronization queues. One scheduler dispatches work every ten seconds and reconciles GitHub every five minutes. The isolated native runner has no database or GitHub credentials and persists only official-client profiles. Do not expose its port or mount the Docker socket.

OCR is pinned to **1.13.1**, Codex to **0.160.1**, and Claude Code to **2.1.292**. Review workers use one process each because OCR configuration is process-global. The native runner uses one ASGI process to own its sessions, terminal processes and locks.

```sh
docker compose -f compose.test.yaml up -d --wait
cd backend
uv sync --frozen --python 3.13
uv run python -m pytest -q
uv run ruff check .
uv run python -m code_mortis.manage export-openapi
cd ../frontend
bun install --frozen-lockfile
bun run api:types
bun run build
bunx playwright install chromium
bun run test
```

Backend tests require the isolated `mortis_test` database on port 55439 and Redis on 56389. Browser tests use the production build with mocked application APIs. Container checks exercise real OCR and native client binaries with controlled or empty profiles. These tests do not prove authenticated provider or GitHub integration. See [acceptance evidence](docs/acceptance.md) for exact commands and remaining live checks.

See [operations](docs/operations.md) for upgrading an existing App, backups, restoration and troubleshooting. Code Mortis is MIT licensed; dependencies retain their licenses in [third-party notices](THIRD_PARTY_NOTICES.md).
