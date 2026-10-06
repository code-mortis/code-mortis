# Code Mortis

Self-hosted GitHub pull request reviews powered by [Open Code Review](https://github.com/alibaba/open-code-review). Findings become comments from your own GitHub App. Review runs, coverage, and resolution history stay in one workspace.

## Run your instance

Requires Docker with Compose and Python 3.12 or newer to generate configuration. Production also needs a public HTTPS origin reachable by GitHub. No personal bot account is required.

```sh
python3 backend/code_mortis/manage.py init-env --owner YOUR_GITHUB_LOGIN --url https://reviews.example.com
docker compose up -d --build
```

Put your reverse proxy in front of `127.0.0.1:8080`, forwarding to the same public origin configured above. Open that URL, enter `CM_BOOTSTRAP_TOKEN` from `.env`, and follow the GitHub App registration flow. Sign in as the configured owner. In **Settings → GitHub**, install the app on selected repositories and connect the installation. Then add and test an AI connection, and enable reviews in the repository's **Configure** panel.

For a local UI preview, omit `--url` to use `http://localhost:8080`. GitHub needs a public webhook URL for real automatic reviews; use an HTTPS reverse proxy or tunnel and set the matching public origin before registering the app. Never commit `.env`. Keep its encryption key with your backups.

## Review workflow

- Opening or updating an enabled, non-draft PR queues a review. External forks require a manual review by someone with repository write access.
- Workers fetch the requested commits and run pinned OCR 1.12.11 with the configured model. Proposed code is not executed. The checkout uses the trusted base commit; OCR reads the PR diff from Git objects. OCR MCP servers are disabled.
- Completed and partial runs preserve findings and the coverage manifest. Incomplete coverage is labeled **partial**, including when OCR exits successfully. Failed or cancelled runs never become a clean review.
- Findings with valid diff locations become inline comments. Others appear in the review summary. Reviews use GitHub's `COMMENT` event; they do not approve or request changes.
- GitHub publication has its own status. Retrying publication uses saved results and reconciles already-posted review markers before writing again.
- Resolve, reopen, or dismiss findings in the app. Linked threads update on GitHub. Thread changes on GitHub return through webhooks and periodic reconciliation. Pending or failed sync remains visible until confirmed.
- Every run preserves its finding snapshots. Findings absent from a later review are **not observed**, not automatically resolved.

## AI connections

The provider picker includes OCR's native presets, OpenCode, and custom endpoints. Supported protocols: OpenAI Chat Completions, OpenAI Responses, Anthropic Messages, and Anthropic Bedrock. Models must support tool calls. Provider availability and model support depend on the selected service; the connection test performs a real OCR tool-call round trip and may incur provider charges.

API keys and extra headers are encrypted with Fernet and never returned to the frontend. Repository settings can override model, effort, language, exclusions, background, rules, concurrency, timeout, and token budget. Bedrock supports worker-provided AWS credentials; inject them into the review worker using your deployment's secret mechanism.

Custom endpoint URLs are administrator-controlled. HTTPS public endpoints are the default. Trusted local gateways need `CM_ALLOW_PRIVATE_ENDPOINTS=true` or a narrowly configured `CM_TRUSTED_ENDPOINT_HOSTS` entry. Network policy should restrict worker egress when administrators or custom endpoints are not fully trusted; URL checks are not a DNS-rebinding or redirect firewall.

Personal ChatGPT/OpenAI and Claude Code subscription sign-in are a follow-up milestone. They are not exposed as working authentication options in this release.

## Architecture

FastAPI serves the React production build and API from one origin. PostgreSQL stores users, encrypted connections, immutable run snapshots, findings, webhook delivery IDs, and durable jobs. Celery and Redis dispatch work, with separate review and synchronization workers. One scheduler dispatches pending database jobs every 10 seconds and reconciles GitHub every five minutes.

The review worker runs as an unprivileged container user with a read-only root filesystem, temporary checkout storage, and a private temporary OCR configuration directory. There is no Docker socket mount. Do not set `CM_OCR_ISOLATED_RUNTIME=true` on an ordinary workstation account: OCR writes its configuration in the service user's actual home.

The first release intentionally uses one review process and one synchronization process. Repeated worker crashes surface as failed operations after three attempts. An expired worker lease is recovered after two hours, allowing the maximum review plus fetch time. Review history is retained indefinitely; size your database and backups accordingly.

## Development and verification

```sh
docker compose -f compose.test.yaml up -d --wait
cd backend
uv sync --python 3.13
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

The backend tests require the isolated `mortis_test` database on port 55439 and Redis on 56389. They refuse another database name. Browser tests serve the production build and mock GitHub-facing application APIs. This exercises UI behavior without publishing comments or calling paid models. Live integration needs your own GitHub App installation and provider credentials.

The image's engine integration check runs the real OCR binary against a local deterministic provider with external networking disabled:

```sh
docker build -t code-mortis:test .
docker run --rm --network none --read-only --tmpfs /tmp:mode=1777,size=256m --tmpfs /home/mortis/.opencodereview:uid=10001,gid=10001,mode=0700,size=32m code-mortis:test python tests/ocr_container_smoke.py
```

For frontend development use `bun run dev`; Vite proxies `/api` and `/health` to `localhost:8080`. The generated API contract is `docs/openapi.json`, with TypeScript types in `frontend/src/api.generated.ts`.

See [operations](docs/operations.md) for backup, restore, upgrades, and troubleshooting. Code Mortis is MIT licensed; bundled OCR and other dependencies retain their licenses in [third-party notices](THIRD_PARTY_NOTICES.md).
