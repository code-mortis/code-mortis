# Code Mortis

Self-hosted GitHub pull request reviews and security scans powered by Mortis Engine. Findings become comments from your own GitHub App. Review runs, coverage, and resolution history stay in one workspace.

Shared API connections are the only execution mode. Historical results remain available. Mortis is enabled for this personal installation by an explicit early-adoption decision. The pending M5 evidence remains under review. See [acceptance evidence](docs/acceptance.md) for implemented behavior, local checks and integrations still awaiting real accounts.

The [documentation index](docs/README.md) links the product, developer and operations guides, implementation plans and engine milestone records.

## Run your instance

Requires Docker Compose and Python 3.12 or newer to generate configuration. Production needs a public HTTPS origin. No personal GitHub bot account is required.

```sh
python3 backend/code_mortis/manage.py init-env --owner YOUR_GITHUB_LOGIN --url https://reviews.example.com
docker compose up -d --build
```

Point an HTTPS reverse proxy at `127.0.0.1:8080`, including WebSocket forwarding. Open the configured URL, enter `CM_BOOTSTRAP_TOKEN` from `.env`, and register your GitHub App. Sign in as the configured owner. In **Settings → GitHub**, install the app on selected repositories. Then add a connection and enable reviews in the repository's **Configure** panel. Never commit `.env`; retain its encryption key with your backups.

Registration, login and installation are separate flows. Always finish each flow in the browser where you started it. `CM_PUBLIC_URL` is the browser origin and callback origin. `CM_WEBHOOK_URL` optionally overrides only the webhook destination. Production defaults to `CM_PUBLIC_URL/api/v1/webhooks/github`.

## Develop locally with Docker

Development has its own database, Redis, secrets and GitHub App. The browser URL stays `http://localhost:5173`; Vite proxies the API and WebSockets. Obtain a dedicated channel at [smee.io](https://smee.io/) to relay GitHub webhooks.

```sh
python3 backend/code_mortis/manage.py init-env --output .env.dev --owner YOUR_GITHUB_LOGIN --url http://localhost:5173 --webhook-url https://smee.io/YOUR_CHANNEL
# Set COMPOSE_PROFILES=webhooks in .env.dev before starting.
docker compose --env-file .env.dev -f compose.dev.yaml up -d --build
```

Open [localhost:5173](http://localhost:5173), bootstrap with `.env.dev`, and register a **separate development GitHub App**. Its callbacks stay on localhost; only webhooks use the relay. HMR updates the frontend and the API reloads Python changes. Restart affected Celery workers and the scheduler after changing their code. Restarting preserves data; `down -v` deletes development data. These commands do not reconfigure the production project.

Keep optional services in the persistent `COMPOSE_PROFILES` setting. To run the experimental parser alongside the relay, use `webhooks,code-graph`; see [parser setup and current limitations](docs/operations.md#optional-code-graph-parser). An explicit `--profile` flag overrides this setting.

All services, including the frontend, run in Docker. Keep port 5173 available so the browser origin and GitHub callbacks stay consistent.

## Connections and review workflow

- Administrators manage shared API connections using OpenAI Chat Completions, OpenAI Responses, Anthropic Messages or Bedrock. Models must support tools. **Test connection** completes a real Mortis gateway tool-call round trip and can incur provider charges. There is no automatic provider or connection substitution.
- Opening or updating an enabled, non-draft PR queues a review. External forks require a manual review by a repository writer. Each run pins commits, model, settings, engine, connection, owner and initiator.
- Mortis plans and validates assigned file results for PR reviews and security scans, including deterministic Trivy scans. Coverage is counted from validated results, never a model's self-reported completion. Repository scripts, agent instructions, hooks and MCP servers are not executed.
- Saved exclusions, file limits, context and token budgets remain enforced. Reaching a bound produces incomplete coverage. Provider quotas still apply. Cancellation cannot refund already consumed usage.
- Optional code graph indexing keeps its isolated parser and exact-revision retrieval. PR review can include bounded related code when an exact-commit graph is available; omitted graph context remains explicit. Security scans do not have graph enrichment.
- Findings and coverage are saved before publication. Valid diff locations become inline comments; other findings appear in the summary. Reviews use GitHub's `COMMENT` event and the App's bot identity. Publication-only retries reconcile existing review markers without repeating model analysis.
- Each new review has a **Code Mortis** GitHub check on its exact commit. It stays in progress through comment publication; success means complete coverage and published comments, with findings reported informationally. Timeouts, incomplete coverage, cancellation and failures remain explicit. The run page shows check synchronization separately. Existing Apps need **Checks: Read and write** and installation approval; see [upgrade instructions](docs/operations.md#github-review-checks).
- Resolve, reopen or dismiss findings in the app. Linked threads synchronize with GitHub, with visible pending changes until confirmed. Periodic reconciliation recovers missed events. Every run preserves its occurrences; a finding absent later is **not observed**, never automatically resolved.

API keys and extra headers are encrypted and never returned to the browser. Custom provider endpoints are administrator-controlled. Private gateways need `CM_ALLOW_PRIVATE_ENDPOINTS=true` or a narrow `CM_TRUSTED_ENDPOINT_HOSTS` allowlist. Deploy an egress policy if administrators or endpoints are not fully trusted. Bedrock credentials stay in the workers that execute the selected shared connection.

## Architecture and checks

The backend is organized into API routers, shared infrastructure, integrations, reviews, security scans, MCP, and schemas. See the [backend structure guide](docs/architecture.md) for the directory map and import rules.

FastAPI serves the production React build and API from one origin. PostgreSQL owns users, credentials, immutable runs, findings, deliveries and durable jobs. Celery/Redis dispatch review and synchronization queues. One scheduler dispatches work every ten seconds and reconciles GitHub every five minutes. The optional code graph parser stays on an internal network with no database or GitHub credentials. Deterministic security scanners retain their process and network isolation.

Upgrades preserve historical results and existing profile volumes. Do not remove volumes to migrate engines.

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

Backend tests require the isolated `mortis_test` database on port 55439 and Redis on 56389. Browser tests use the production build with mocked application APIs. Container checks verify Mortis imports, absence of retired executables, and the real deterministic scanner. These tests do not prove authenticated provider or GitHub integration. See [acceptance evidence](docs/acceptance.md) for exact commands and remaining live checks.

The [Noodle API collection](collections/code-mortis/README.md) includes session authentication, automatic CSRF, and a read-only smoke suite for local or hosted instances.

See [operations](docs/operations.md) for upgrading an existing App, backups, restoration and troubleshooting. Code Mortis is licensed under [Apache 2.0](LICENSE).
