# Engine M1 implementation and evidence

Date: 2026-11-07. Starting revision: `bfc6632` (`docs(engine-eval): freeze synthetic pilot and owner profile`). Changes are local and uncommitted. This record supplements the dated M0 audit; it does not rewrite its baseline evidence.

M1 is complete for local implementation and verification: all eight checklist items are implemented. It delivers the shared execution foundation. OCR remains the available shared review engine. Selecting Mortis fails explicitly until its execution path is implemented; it never falls back to OCR. No provider calls, production migration, deployment, or pilot comparison were performed for M1.

Three GPT-6.1 Sol agents at high reasoning implemented and reviewed shared admission, durable task accounting, and neutral source access. The parent integrated repository settings, generated contracts, the UI, regression checks, and this handoff.

## 1. Delivered behavior

| Area | Implementation | Evidence boundary |
| --- | --- | --- |
| Shared-only admission | Exact `kind == api` checks reject native and unknown connection kinds at creation, selection, execution, delayed jobs, standalone summaries/background generation, scan retries, and native runner entry points. Subscription authentication, consoles, grants for new work, and native MCP management are retired. | Existing shared protocol/provider support is retained. This does not establish real provider compatibility. |
| Repository remediation | Retired connection references remain intact. Unrelated settings edits and disabling a repository remain possible; enabling new work requires explicit shared selection. API output identifies repositories needing setup; review and scan controls show the required action. | No automatic connection substitution or secret migration. A manual shared review override is explicit. |
| Historical reads | Existing runs, findings, candidates, coverage, scan history, original engines/configuration, and PAT/MCP authorization remain separate from native execution. Saved publication still revalidates GitHub authority. | Permission checks are preserved; history is not made public. |
| Independent summary attempts | Explicit regeneration can select a current shared connection for eligible completed native history. Its durable Job records configuration, requester, authorization, prior summary, result, and usage separately. Overview synchronization uses that attempt's authority. | Original run configuration/engine/provenance are not rewritten. Cancelled parent runs remain ineligible; this is not a resume operation. |
| Neutral helpers | `core/process.py`, `core/source.py`, and `core/errors.py` own reusable bounded commands, cancellation, restricted environment, Git/path validation, source reads, and snapshots. Graph and scanner helper paths no longer import OCR. | OCR still owns its own adapter, rules preview, parsing and reports. Other live shared workflows still use OCR until their later milestones. |
| Evidence policy | PR/graph exclusions are reapplied at required/optional source reads, saved and fallback summary paths, assessment expansion, verification diffs and graph context. Renames check original and destination paths. | Scanner admission and limits remain independent. Existing graph revision/policy/size semantics are preserved. |
| Engine snapshots | Repository setting `analysis_engine` defaults to `ocr`; new work records engine/planner contract versions. Legacy clients omitting the field preserve the stored selection, as they do omitted graph configuration. | Existing jobs are not backfilled or rewritten. UI displays Mortis as unavailable; explicit unsupported execution fails. |
| Additive persistence | Four new tables store immutable plans, stable tasks, append-only attempt identities and independent stage budget accounting alongside existing durable Jobs. | New task APIs have no provider calls. Wiring transport execution, progress and workflow orchestration remains M2–M4. |

The subscription setup screens are no longer reachable from settings. Shared connection forms, token management, MCP access, security scan controls and historical native engine labels remain. Cleanup/revocation endpoints and old data are retained for controlled retirement; removing all native packages, profiles and deployment services is later work.

## 2. Durable task contract

`schemas/analysis.py` validates exact revisions, required contract versions, a selected shared connection without credentials, stage budgets, timezone-aware deadlines and a dependency DAG. Plans contain repository/requester/provenance, settings, file policy, selection, graph metadata and input hashes. These are internal contracts, not a new public execution endpoint.

`core/analysis.py` persists one deterministic plan per existing Job. Stable task IDs derive from the plan and task key. PostgreSQL triggers reject changes to saved plans, task inputs and budget limits/deadlines; operational outcomes remain mutable. Persisting a different plan for the same Job fails.

Claims serialize only the short database admission transaction. They validate the current Job attempt, owner state, pinned revisions, current shared connection, dependencies, deadline and whole-request reservation before returning a lease. No provider or GitHub calls occur while these locks are held. Time is sampled after lock acquisition, including budget locks, so waiting cannot renew an already expired right to execute.

Each task attempt retains its identity, lease, reservation, observed usage, provider request identity, sanitized error and result. Completion and renewal reject stale attempts. A lost/expired attempt retains its reservation; recovery requires an explicit uncertainty acknowledgement. A validated response with missing usage remains usable but blocks further consumption in that stage until its retained charge is acknowledged. Unlimited budgets are represented explicitly; they do not erase usage, deadlines or cancellation.

Cancellation preserves completed results and marks unresolved consumption as uncertain. This establishes the storage/recovery primitive, not exactly-once provider execution. M2 must revalidate external GitHub/PAT/MCP authority immediately before a request and accepting evidence; the task ledger does not replace those checks. Connection-wide fair admission and provider-aware token estimates remain M2.

## 3. Subscription cutover procedure

The new binary rejects retired work even when old queue deliveries arrive. `python -m code_mortis.manage retire-subscriptions` additionally settles active legacy reviews, scans and inference Jobs durably, retaining history and unknown usage. It preserves uncertain/partial publication and saved publication plans. The command is idempotent and database-only.

A new binary cannot fence an old process that still possesses usable credentials. For deployment, perform these steps in order:

1. Back up the database and inventory active native reviews, scans, standalone generations, authentication sessions and consumers. Stop old admission, scheduler and consumers; drain or terminate their processes according to operational requirements.
2. Invalidate the old runner bearer token and prevent old workers from reaching an inference-capable runner. Revoke active login/console access. Confirm old worker processes are stopped; changing database rows alone is insufficient.
3. Apply the additive migration with the new application image, then run `python -m code_mortis.manage retire-subscriptions` against the deployment database. Retain the reported counts and inspect unresolved/uncertain publication and consumption.
4. Start only the new API/workers/scheduler and the new runner if still needed for cleanup. Its native inference/authentication entry points are closed. Verify delayed legacy deliveries settle without network inference and saved history remains readable.
5. Have administrators explicitly select shared connections for affected repositories. Start new work explicitly; do not convert or resume old native work automatically.

These deployment steps were **not executed against production or the development stack**. Local fixture tests establish new-binary behavior; they do not prove old-process termination or credential revocation. Rollback must preserve shared-only admission rather than re-enable native execution. Removing tables/profiles is not part of this cutover.

## 4. Verification

Checks used isolated local Docker PostgreSQL/Redis (`mortis_test`, ports 55439/56389). Browser tests used the production frontend build and mocked application APIs. Provider and GitHub behavior in pytest is mocked; real-service acceptance remains separate.

| Check | Result | What it establishes |
| --- | --- | --- |
| Backend full suite: `cd backend && .venv/bin/python -m pytest -q --tb=short` | **3,078 passed**, 638 warnings, 381.77 s | Full retained suite after shared-only fixture updates. Last summary revocation/lock-order fixes landed during this process and are covered by the fresh follow-up below. |
| Fresh final follow-up: `.venv/bin/python -m pytest -q --tb=short tests/test_shared_retirement.py tests/test_pr_summary.py tests/test_api_token_provenance.py tests/test_mcp_provenance.py` | **181 passed**, 10.52 s | Final code: queued/running PAT/MCP revocation, delayed delivery, independent summary publication, expired-lease refusal, and retry eligibility. These cases overlap the full run; they are not 181 additional unique cases. |
| Foundation/limits/protection/transport/repository selection | **256 passed**, 6.69 s | Additive upgrades, stable plans, immutable inputs, concurrent reservations, lock-wait expiry, unknown usage, completed-parent summary leases, and legacy-client settings. Also included in broad verification. |
| Graph/source/provenance/history selection | **174 passed**, 28.18 s | Exact source validation, graph queue isolation using real local Redis/Celery, graph migrations, token boundaries and old repository clients. Also included in broad verification. |
| Frontend: `cd frontend && bunx playwright test --workers=4` | **1,438 passed**, 4 fixture failures, 2 skipped | Desktop/mobile regression run. Four Inbox detail failures were incomplete repository fixtures, corrected to include engine settings. The two existing Sentry envelope cases require separate Sentry setup. |
| Fresh frontend follow-up: `bunx playwright test tests/inbox.spec.ts tests/workspace.spec.ts tests/security.spec.ts --grep 'unified loading|ordering survives|file-limit skip notice|retired|native review history' --workers=4` | **22 passed**, 5.9 s | All four corrected Inbox cases plus final shared-only copy, review/scan gating, native historical reads and repository remediation. Across these runs all 1,442 executed browser cases have passing evidence. |
| `bun run build`; OpenAPI export and `bun run api:types` | Passed | Strict TypeScript and production build; generated API contracts refreshed. Existing bundle-size warning remains. |
| `.venv/bin/ruff check .`; `git diff --check`; Impeccable detector on changed UI | Passed | Static checks and whitespace; detector returned no findings. Desktop/mobile remediation screenshots were visually inspected. |
| Pinned OCR container smoke | Passed | Real OCR with deterministic local OpenAI Chat, Responses and Anthropic servers; trusted rules, file policy, strict selections/citations, redaction, usage, shared concurrency, cancellation and cleanup. No actual provider/GitHub calls; Bedrock live integration is not established. |
| Pinned Trivy container smoke | Passed with explicit coverage gap | Secret/infrastructure detection, repository suppression resistance, redaction and socket denial. Missing/stale vulnerability database means complete dependency vulnerability detection remains unverified. |

The container checks used `code-mortis:pr44-smoke`, image `sha256:8ac6cbac0bdd6c0b646904fe00b4bb20f1ecf90b5fb4f68da9903555ab4a1bd3`, current backend mounted read-only, read-only rootfs, tmpfs scratch and `--network none`. Commands are reproducible:

```sh
docker run --rm --network none --read-only --tmpfs /tmp:mode=1777,size=256m --tmpfs /home/mortis/.opencodereview:uid=10001,gid=10001,mode=0700,size=32m --mount type=bind,src=/Users/carlosmontecinos/Projects/code-mortis/backend,dst=/m1-source,readonly --workdir /m1-source --env PYTHONPATH=/m1-source --env PYTHONDONTWRITEBYTECODE=1 code-mortis:pr44-smoke python tests/ocr_container_smoke.py
docker run --rm --network none --read-only --tmpfs /tmp:mode=1777,size=512m --mount type=bind,src=/Users/carlosmontecinos/Projects/code-mortis/backend,dst=/m1-source,readonly --workdir /m1-source --env PYTHONPATH=/m1-source --env PYTHONDONTWRITEBYTECODE=1 code-mortis:pr44-smoke python tests/security_scanner_smoke.py
```

The existing smoke image's Git 2.39.5 is below OCR's recommended 2.41; the smoke passes with that warning. Trivy is 0.76.0. No image was deployed or services restarted.

`graphify update .` refreshed the developer AST index; semantic extraction refreshed this record and ENGINE_PLAN.md. The local extractor retains its existing TSX warning at the `Limits & rules` heading in `settings.tsx` and omits JSON/non-code inputs that produce no AST nodes. The application build and production code-graph checks pass; this developer index is navigation, not proof of source correctness.

## 5. Remaining gates and M2 handoff

M2 implementation can begin locally. These gates still apply before enabling new inference or deploying the retirement.

- Integrate the immutable plan/task APIs with direct shared transports before enabling Mortis. Existing OCR orchestration does not yet use the new task ledger, and no speedup or quality parity is claimed.
- Implement connection-wide admission, transport token estimates, provider capability checks, cancellation/deadline propagation and structured responses in M2. Retain independent stage caps and uncertain consumption on every retry.
- Keep all retained shared protocols/options, Bedrock, custom endpoints, extra headers, timeout semantics and no-fallback behavior. The selected pilot account is OpenCode Go / `muse-spark-1.3-contributor`; the owner's uncapped aggregate pilot spend does not change application per-stage limits.
- Build deterministic Mortis file selection and coverage in M3. If legacy OCR's preview disagrees with mandatory or rename exclusions, the adapter now stops before inference rather than silently reviewing an unsafe subset. This explicit failure can reduce availability for those mismatches until selection is owned by Mortis.
- Preserve exact-revision graph retrieval and fail-open optional context; graph limits must not become PR review limits. Keep scanner inventory, deterministic scanners, redaction and scan budgets independent.
- Complete full workflow migration and remove residual native runtime/deployment dependencies only in their planned phases. Historical records and cleanup access must survive.
- Run the operational cutover above before deploying this shared-only version. M5 still requires real selected-model integration, effective Docker resources, calibrated cost/latency measurements and broader adjudicated quality evidence. M0's synthetic pilot cannot establish those results.
