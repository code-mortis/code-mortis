# AGENTS.md

Code Mortis is a self-hosted GitHub pull request review and repository security service. Mortis Engine uses explicitly selected shared API connections, and Trivy supplies deterministic security checks. See the [README](README.md) for local development and deployment.

## Project layout

- `backend/code_mortis/` contains the FastAPI application. HTTP routes live in `api/`, workflows in `reviews/` and `security_scans/`, and external adapters in `integrations/`. Put shared infrastructure in `core/` and API contracts in `schemas/`.
- `frontend/src/` contains the React application. Reuse the Tailwind semantic tokens and primitives in `frontend/src/components/ui/`. Default to native form controls.
- Put database changes in additive Alembic migrations in `backend/migrations/`. Existing installations must be able to upgrade without losing data.

## Local checks

Run the backend checks from the repository root with Docker Compose and `uv`.

```sh
docker compose --project-directory . -f docker/compose.test.yaml up -d --wait
cd backend
uv sync --frozen --python 3.13
uv run ruff check .
uv run python -m pytest -q
```

The tests truncate the isolated `mortis_test` database and clear shared test Redis keys. Run one backend test process at a time across worktrees sharing those services.

Run frontend checks from the repository root with Bun. Playwright tests use the production build with mocked APIs.

```sh
cd frontend
bun install --frozen-lockfile
bun run build
bunx playwright install chromium
bun run test
```

- Keep the `uv` and Bun lockfiles current. Add a focused runnable check for non-trivial behavior changes.
- For API contract changes, run `uv run python -m code_mortis.manage export-openapi` from `backend/`, then `bun run api:types` from `frontend/`. Include the generated `frontend/src/api.generated.ts` diff.
- Check queued, running, and reserved tasks and drain active work before restarting affected development workers. Shared inference changes affect the review, security, verification, and summary workers.
- Report local and mocked results separately from hosted CI and live integration results.

## Repository skills

Read the relevant skill directly when automatic skill discovery is unavailable.

- [Verify a change](.agents/skills/mortis-verify-change/SKILL.md) for selecting and running local checks.
- [Diagnose a saved run](.agents/skills/mortis-diagnose-run/SKILL.md) for read-only review and security scan troubleshooting.
- [Refresh development services](.agents/skills/mortis-refresh-dev/SKILL.md) for a requested local Docker refresh.
- [Assess engine evidence](.agents/skills/mortis-engine-evidence/SKILL.md) for reviewing, rescoring, or planning evaluations.
- [Prepare a release](.agents/skills/code-mortis-release/SKILL.md) for preparing or auditing a stable App release and its public documentation.

## Runtime and security invariants

- Preserve original findings and report current status separately. A finding absent from a later review is **not observed**. Count coverage from validated file results and label incomplete runs explicitly.
- Run new inference through Mortis with the selected shared connection. Surface provider failures without switching connections. Keep retired engine identities and results readable and their saved jobs non-executable.
- Preserve shared file exclusions and limits, immutable plans, attempt fencing, cancellation, and budget reservations. Keep unknown usage and its reservations accounted for during recovery.
- Keep credentials server-side and encrypted at rest. Keep real credentials and instance environment files out of Git. Retain the custom endpoint gates `CM_ALLOW_PRIVATE_ENDPOINTS` and `CM_TRUSTED_ENDPOINT_HOSTS`.
- Validate incoming webhooks and GitHub API payloads. Revalidate GitHub permissions before execution. An installation ID alone grants no access.
- Treat repository content as untrusted data. Give tools bounded, authorized source from the exact revision. Never execute repository scripts, agent instructions, hooks, or MCP servers during analysis.
- Run graph extraction in the sync worker's isolated subprocess with an empty environment, bounded input and output, and the Linux syscall and resource limits in place before parsing source. Keep graphs opt-in and revision-specific. Extraction changes require an `EXTRACTOR_VERSION` bump in `backend/code_mortis/schemas/code_graph.py`, a rebuilt App image, and fresh indexing.
