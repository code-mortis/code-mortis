---
name: mortis-verify-change
description: Verify a Code Mortis implementation change with checks selected from its diff, including isolated backend tests, generated API contracts, frontend builds and browser regressions. Use for local change validation, not live run diagnosis or engine acceptance claims.
---

# Verify a Code Mortis change

Select the smallest checks that establish the changed behavior and its affected contracts.
Read `AGENTS.md` first. All paths below resolve from the Code Mortis repository root,
not this skill's directory. Confirm the checkout and working directory before commands.

## Establish scope and ownership

- Inspect the working diff, including existing uncommitted edits, before generating files.
  Follow changed functions into their callers, tests, fixtures, schemas and exports.
- State the checks selected and what each establishes. Expand only for broad shared
  changes, required repository checks, failures or unresolved impact.
- Verification does not authorize commits, pushes, deployment, worker restarts,
  provider calls or paid evaluations. Preserve unrelated changes and existing artifacts.
- Coordinate with any other writer before changing application code or regenerating
  types. Report confirmed defects and drift without overwriting their work.

## Backend checks

Owning sources: `backend/pyproject.toml`, `backend/tests/conftest.py`,
`docker/compose.test.yaml` and `.github/workflows/backend.yml`.

- One owner runs database-backed tests at a time across agents and worktrees. The
  autouse fixtures truncate all application tables and clear shared Redis keys per case;
  separate test processes using the same services are not isolated from one another.
- Check the effective test environment before running: `CM_DATABASE_URL` can override
  its fixture default. Require the isolated `mortis_test` database on port 55439 and
  `redis://127.0.0.1:56389/0`; never substitute development or production services.
- From repository root, start the test services if needed and within task scope:
  `docker compose --project-directory . -f docker/compose.test.yaml up -d --wait`.
- From `backend/`, install only if needed with `uv sync --frozen --python 3.13`.
  Run `uv run python -m pytest -q` with selected existing test paths or node IDs appended.
  Run `uv run ruff check .` for backend changes. Do not add parallel pytest processes
  against the shared services; CI shards use separate infrastructure.
- Choose tests around the shared code. For durable inference changes, consider
  `backend/tests/test_analysis_foundation.py`, `backend/tests/test_inference_admission.py`
  and affected review and security workflow cases.

## API contracts and frontend

Owning sources: `backend/code_mortis/manage.py`, `frontend/package.json`,
`frontend/playwright.config.ts`, `.github/workflows/ci.yml` and `frontend/tests/`.

- For schema or public response changes, from `backend/` run
  `uv run python -m code_mortis.manage export-openapi` to generate the input for
  `bun run api:types`.
- From `frontend/`, run `bun install --frozen-lockfile` only if dependencies need setup.
  If `frontend/src/api.generated.ts` has unrelated edits or another writer owns it,
  export the schema with `export-openapi --output` to a temporary path and use
  `bunx openapi-typescript` with that schema and `-o` targeting a temporary TypeScript
  file. Compare without overwriting the existing file, then return drift to its owner.
  Otherwise run `bun run api:types`; never edit generated types manually. Inspect the
  result against the pre-check diff. CI exports an artifact separately but checks this
  same generated TypeScript file for drift.
- From `frontend/`, run `bun run build` for frontend changes. It includes `tsc -b`.
  For browser regressions, build first, then run `bun run test` with selected spec paths
  or Playwright filters appended. Install Chromium with `bunx playwright install chromium`
  only when absent and installation is allowed.
- Playwright serves the production build through Vite preview on port 4178, with
  desktop and mobile Chromium projects. Application API mocks establish rendered
  behavior under those fixtures; they do not prove authenticated live API integration.

## Evaluator changes and reporting

- Offline evaluator unit tests live in `scripts/engine_eval/test_*.py`. Inspect the
  relevant tests before selecting them; use Python unittest discovery with that start
  directory and a selected test filename pattern. Do not run a pilot or live launcher
  as an incidental check. Historical execution entrypoints are retired.
- Report synthetic evaluator checks as local regressions. Assess an engine acceptance
  claim against its supplied run evidence and protocol. See the [engine evidence skill](../mortis-engine-evidence/SKILL.md).
- Report changed behavior, exact commands and outcomes, generated-file changes,
  failures, skips and remaining boundaries. Focused checks establish only their scope;
  local tests do not establish hosted CI, real provider success or engine acceptance.
