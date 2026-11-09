# M3 — PR analysis and code graph

Date: 2026-11-07. Starting source: `c5589bb` (`feat(inference): complete shared-provider transport and admission`). This record describes local implementation and verification, not deployment or measured quality/performance parity.

## 1. Implemented scope

The independently written PR engine now runs through the M1 durable ledger and M2 shared-provider gateway. No upstream implementation or prompt was copied. No dependency, database migration, public API contract or frontend change was required.

| Component | Implemented behavior |
| --- | --- |
| `reviews/planning.py` | Exact base/head/merge-base selection from inert Git objects; deterministic ordering, task identities and hashes; trusted-base rules and confined rule-file references; settings-rule precedence; original default rules; mandatory/additional exclusions and both rename sides; all-or-skip file gate before source loading or inference. |
| `reviews/engine.py` | Bounded batches and concurrent workers; original instructions honoring language, sensitivity and effort; strict task/unit/result schemas; exact source citations and finding locations; bounded head-only `read_source`; schema repair; validated unit/file coverage and retained successful candidates after partial failure. |
| `reviews/mortis.py` | PR snapshot, current authorization and immutable plan integration; credential/configuration fencing; shared connection only; real ledger admission, independent analysis cap/deadline, resume, cumulative usage and callbacks compatible with the review service. |
| `core/analysis.py` | Bounded dependent evidence tasks under the original job, plan, stage and scope; one immutable child per round; parent completion and known-consumption checks; stale-owner and lock-wait lease checks. |
| `reviews/graph_context.py` | `graph-context/v2` for shared API Mortis analysis; exact revision, extractor, graph policy, review policy and trusted-rule identities; neutral source reads; optional context bounds and honest omission reasons. Historical `graph-native/v1` remains readable. |
| `integrations/llm.py` | Evidence validation runs outside the async HTTP loop and drains on cancellation; fresh authority is checked after validation before persistence. Optional local admission checks avoid repeated GitHub requests while waiting, with fresh external checks still required before dispatch and acceptance. |

The review service contains the Mortis routing branch and engine/graph snapshots. **The public availability gate remains closed.** Ordinary user-triggered reviews and security scans continue to require OCR until M4 completes downstream parity and M5/M6 establish acceptance. This prevents a partially migrated workflow from appearing available.

## 2. Reliability and scope contracts

- Every admitted file remains in the denominator. Each source fragment must have a validated result before the file counts as completed. Failure of one fragment retains valid candidates from its successful peers while reporting the file incomplete.
- File limits retain the existing `code-mortis.file-limit/v1` skipped/waived contract. No selected file is quietly dropped to fit a limit. Deleted and binary files retain explicit exclusion reasons; documentation, configuration and supported custom-rule paths do not depend on graph parser support.
- Initial units are at most 48 KiB, with smaller bounds for configured prompt ceilings; initial batches target 96 KiB. Actual encoded-request admission subdivides multi-unit batches when needed after dropping optional graph context. Required source is sent once with a metadata-only citation index. Source/diff splitting is lossless, including large deletion hunks. The 20 MB retained source/diff safety cap produces explicit selected-file errors. Shared API analysis does not inherit the retired native `delegated_context_bytes` cap. An indivisible unit or schema/rules that still cannot fit fails explicitly rather than being clipped.
- Supporting reads are head-pinned, confined, redacted and filtered through the same trusted rules and exclusions. Rename checks use merge-base-to-head even if the trusted base advanced. Each round permits at most ten reads, 400 lines per read and 64 KiB total supporting text; raw source reads have a 20 MB guard.
- Additional investigation/repair is bounded by effort: one, two or four rounds. At most one schema repair is permitted, within that total. Malformed transport responses are not reinterpreted as schema feedback. Unknown consumption prevents new evidence rounds or additional inference under the affected stage allowance.
- Each dependent task freezes its request hash, original assignment, parent and stage. Persistence contains normalized validated results and source selection hashes rather than raw tool output, repository source or provider reasoning. No provider, model, connection or engine fallback is introduced.
- One HTTP pool and one bounded worker queue serve the run. Concurrency remains one to eight under the stricter M2 per-connection and per-run admission rules. A temporary reservation by another running batch is allowed to settle before declaring the finite budget exhausted. Capacity waits remain bounded.
- Cancellation and supersession abort and drain outstanding work. Deadlines produce explicit incomplete units and preserve previously accepted candidates/usage. A response whose permission is revoked during validation is charged accurately but not accepted.
- The original plan remains immutable on resume. Already validated results can be replayed without new inference. If frozen optional graph evidence becomes unavailable, completed assignments remain reusable; affected unfinished assignments fail explicitly instead of substituting a different request or rejecting the whole review. Changed required scope, rules, settings or versions still require a new job.
- Graph context remains optional: at most three files/12 KiB per selection and 48 KiB per run. Missing, paused, failed, unsupported or excluded graph data cannot reduce required PR coverage. Prompt-size omissions are recorded separately from graph retrieval selections. Graph rehydration cannot broaden a frozen selection.
- Browser-origin opt-in and independent Code MCP/PAT permission boundaries remain intact. M3 does not enable graph reads for otherwise ineligible origins. Assessment and verification graph callers now provide their pinned base SHA, while their migration remains M4.

## 3. Verification

Tests use the isolated local Docker PostgreSQL/Redis environment. Provider responses and GitHub network operations are mocked; local Git repositories and the planner, durable ledger, admission accounting, HTTP protocol adapter and result validators are real.

Focused evidence covers deterministic scope and rule precedence, trusted-base references, advanced-base rename exclusions, lossless splitting, huge input, exact evidence validation, prompt bounds, finite/unknown budgets, concurrent reservation settlement, immutable evidence chains, cancellation, deadlines, revocation, exact revisions, replay, graph omissions and graph loss during recovery.

| Check | Result |
| --- | --- |
| Entire backend suite, sequential isolated shards | **3,477 passed**, no failures: `PYTEST_SHARD_INDEX=0`, `1`, and `2`, each with `PYTEST_SHARD_COUNT=3 .venv/bin/python -m pytest -q`, from `backend/`. Each shard passed 1,159 cases in 139.91 s, 139.73 s and 144.98 s respectively. The disposable test PostgreSQL container was recreated between shards to bound temporary disk use. Existing dependency deprecation warnings remain. |
| Final focused integration regression | **320 passed in 76.36 s**, after all code changes: `.venv/bin/python -m pytest -q tests/test_pr_planning.py tests/test_pr_engine.py tests/test_mortis_review.py tests/test_mortis_graph_context.py tests/test_analysis_expansion.py tests/test_analysis_foundation.py tests/test_llm_execution.py tests/test_code_graph_review.py tests/test_code_graph_stages.py tests/test_engine_scope_contract.py` from `backend/`. Covers the final prompt-packing/source-deduplication refinement made after the broad suite's collection, plus its owning planner, adapter, ledger, gateway and graph callers. |
| Static/API checks | `.venv/bin/ruff check .` from `backend/` and `git diff --check` pass. `.venv/bin/python -m code_mortis.manage export-openapi` and `bun run api:types` pass with no generated API diff. |

The first broad invocation was stopped because the filesystem sandbox denied localhost database connections; the same checks passed with access to the isolated Docker test services. No product-code failure was hidden by that retry. The complete suite's three shards used the same collected 3,477 cases; the final focused run separately checks the later packing refinement.

No M3 real-provider call, comparative pilot, performance measurement, production migration, GitHub publication or security scan was performed. M2's separately recorded OpenCode Go smoke establishes its synthetic transport/tool/schema behavior only. The frozen evaluation corpus was not used to tune this implementation.

## 4. M4 handoff and remaining gates

1. Integrate candidate assessment, deduplication/history, fix verification, summaries/diagrams/file groups, fix prompts and project background with the new gateway. Their current implementations remain intact; M3 does not establish end-to-end feature parity.
2. Migrate security AI stages independently while retaining Trivy, scanner-specific exclusions, freshness, secret redaction, grading, triage and fix checks. Security workflow routing remains gated.
3. Complete publication/reconciliation, reports, analytics, PR Code and Reviews/Security MCP/PAT regression checks through the full Mortis workflow; retain independent authorization and exact issue-draft approval.
4. Remove remaining active Mortis-path OCR execution/import dependencies only when all callers have migrated. Neutral PR planning and graph source reads now exist; legacy workflow helpers and historical readers still exist intentionally.
5. Keep public Mortis availability closed until downstream parity is complete. M5 must use production paths for the frozen paired pilot, actual provider checks, worker-loss/fault injection, graph-on/off quality and resource/latency measurements. M6 remains the explicit opt-in canary gate.
6. Preserve M1 operational cutover and M2 deployment requirements: drain old workers, rebuild locked dependencies, apply existing additive migrations and retire subscription runner access. No such deployment action occurred during M3.

The owner-selected pilot remains OpenCode Go / `muse-spark-1.3-contributor`, uncapped aggregate spending and the current local Docker environment. Per-request bounds, accounting, selected resource enforcement and the frozen evaluation protocol still apply; no speed or quality improvement is claimed from mocked tests.
