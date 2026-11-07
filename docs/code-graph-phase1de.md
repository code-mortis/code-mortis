# Code graph Phase 1D and 1E

Local implementation, 2026-10-26. This adds internal retrieval and browser repository activation. Review enrichment, a public graph API/MCP and a graph explorer remain later phases. No real repository was activated by this implementation session.

## Repository behavior

With the existing `code-graph` Compose profile running, an administrator opens **Configure → Project context → Code graph**, enables it and saves. This setting is independent of reviews and AI connections. Older repository-update clients can omit `code_graph_enabled` without resetting it; upgraded repositories without a configuration row remain off.

The enabling administrator is recorded. Initial work and the existing reconciliation schedule use durable `code_graph_refresh` jobs to discover the default-branch SHA, then admit the existing indexing workflow. Admission, acquisition and publication revalidate the actor's access. Head checks are deduplicated per repository/actor, the reconciliation batch is limited to 16 repositories, and head-check admission defers when 16 checks are already active. Snapshot admission still strictly bounds indexing to 16 pending snapshots per instance and two per repository; extraction is serialized.

Readiness is browser-only and requires workspace administration and current GitHub write access to the fixed numeric repository identity. It reports disabled, not indexed, queued, running, ready, partial, stale, failed or cancelled, the observed revision, the previous usable revision and check time. A failed head check never changes an old snapshot into current evidence. Cancel pauses automatic updates until Retry or disabling/re-enabling. Retry records the requesting administrator and explicitly retries failed/cancelled work. Ready/partial identities remain immutable. Saving off cancels active snapshots and stops graph access. Unrelated saved settings remain intact.

The new routes are `GET /api/v1/repositories/{id}/code-graph`, `POST …/retry` and `POST …/cancel`. Existing PAT and Reviews/Security MCP grants do not authorize these routes, graph activation or code retrieval. The existing MCP repository-update tool does not acquire the new field or schedule graph work through its review-management permission.

## Internal retrieval contract

`code_graph.retrieval.query(GraphQuery(...), actor_id)` accepts a repository ID and exact commit SHA. The active extraction version and exclusion policy are part of snapshot selection. Every operation checks current membership and fresh GitHub read permission before lookup and again before returning content. Read access is independent of the administrator who originally enabled indexing. Restricted paths are filtered before traversal, result limits and truncation calculations.

Operations are exact symbol name/key lookup, `defines`, literal text matches with byte/line citations, file dependencies, reverse-dependency impact candidates, and bounded source. Source reads verify the retained ref, pinned tree entry and blob identity; unavailable source never falls back to another revision. Callers and references report unsupported explicitly. File imports remain conservative relative-path relationships, not resolved function calls or proof of defects.

Limits are 100 results, depth two, 500 visited files, ten source files and 64 KiB of source per response. Text lookup scans the same bounded source window, rather than promising repository-wide search. Responses report snapshot identity, capability gaps, source availability and truncation. Missing and excluded requested paths are both unavailable; no denied-path existence is disclosed. The service is internal to trusted workers and retains the shared source-store lock through retrieval so cleanup cannot remove its objects.

## Storage measurement

[Recorded measurements](evidence/code-graph-phase1-storage.json) use the exact Phase 0 source-manifest digests and commits. A disposable PostgreSQL schema receives validated facts through the current ORM models. Reported database sizes include graph tables, TOAST and indexes; the empty table/index sizes are also recorded. WAL is the cluster LSN increase during the insertion transaction and may include background writes. These are one-snapshot measurements, not retention-growth projections or a memory benchmark.

| Recorded corpus | Source files / bytes | Graph tables + indexes | Of which indexes | Insert WAL | Retained full shallow Git commit |
| --- | ---: | ---: | ---: | ---: | ---: |
| Code Mortis `d8a09f1` | 244 / 4,176,007 | 2,899,968 B | 1,671,168 B | 3,661,232 B | 1,230,066 B |
| Noodle `2153c77` | 653 / 6,275,421 | 3,563,520 B | 1,908,736 B | 4,806,176 B | 17,557,282 B |

The database measurements use Phase 0's extension-filtered source corpus; retained Git storage includes the complete shallow commit, including files outside that corpus. Full-tree admission is checked separately: Code Mortis's 392-entry tree is admitted, while Noodle's 1,055-entry tree is rejected by the current 1,000-entry limit. Thus the Noodle row measures storage for its recorded source subset, not a successful full-repository indexing run. No limit was raised to hide this rejection.

Reproduce after starting `compose.test.yaml` and installing backend dev dependencies, from `backend/`:

```sh
uv run python ../scripts/code_graph_eval/storage.py \
  --repo code-mortis /path/to/code-mortis d8a09f12956bf074cdc9f5db868ecf04895eb812 \
  --repo noodle /path/to/noodle 2153c7745e66b72f48dee76b992e91b3be7c653f \
  --output ../docs/evidence/code-graph-phase1-storage.json
```

The harness reads local Git objects without checkout, source execution or network access, uses only the isolated `mortis_test` database, and removes its temporary schema and Git stores. It makes no model calls. Operator upgrade, paired database/Git backups, cleanup and disabling procedures remain in [operations](operations.md#optional-code-graph-parser).

## Validation and boundaries

Commands: `cd backend && uv run python -m pytest -q`; final selection `uv run python -m pytest -q tests/test_code_graph*.py tests/test_repository_security.py tests/test_noodle_collection.py`; `uv run ruff check . ../scripts/code_graph_eval/storage.py`; frontend `bun run api:types`, `bun run build`, `bun run test`. A second OpenAPI export/type generation produced identical generated types.

- Focused API tests cover default-off activation, preserving settings, explicit retry/cancel, head/policy changes, partial/stale readiness, access loss, expired attempts and existing MCP/PAT denial. Retrieval tests cover repository/revision/version isolation, exclusion filtering, cycles, limits, missing source and exact citations.
- Frontend production build passed. The complete Playwright suite passed **1,392 tests, two skipped Sentry-envelope cases**; the 24-case repository-settings selection passed on desktop/mobile and includes keyboard controls, failed requests, draft preservation and save navigation. Light/dark captures were inspected; Impeccable's independent reviewer returned `ship`, and its documenter found no new design-system decisions.
- The full backend suite passed **2,834 tests** (637 existing deprecation warnings), before the final explicit re-enable refinement; the final selection passed **807 tests**, including all 151 graph cases, repository authorization and Noodle route contracts, and covers that refinement. Ruff and `git diff --check` pass. OpenAPI and frontend types are regenerated from the shared contracts.
- [Development evidence](evidence/code-graph-phase1de-development.json): additive migration `20261026_graph_readiness`, rebuilt application image, drained/restarted affected workers and scheduler, 13 running services, five responding workers, API readiness and frontend module delivery, zero restart loops, and a validated synthetic extraction through the real private worker-to-parser transport. Existing record counts are preserved and graph opt-ins/snapshots remain zero.

No live GitHub source acquisition, authenticated real-account graph run, hosted CI, production deployment, paid review, review-quality/token comparison or full-stack 8 GB test was performed. Existing parser-isolation evidence is recorded separately in Phase 1B; this phase's smoke check does not repeat its memory-exhaustion and deadline experiments.
