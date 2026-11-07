# Code Mortis engine replacement plan

Date: 2026-11-07

Status: approved implementation roadmap; phase delivery and acceptance remain pending.

M0 audit update (2026-11-07): five of six items are verified at `0cfebd5398ac25c98dcb37d99bb39eda95fcb897`. See [the evidence and migration inventory](ENGINE_M0.md) and [evaluation protocol v1](ENGINE_EVAL_V1.md). The actual independently adjudicated pilot corpus and priced/capped live-run manifest remain pending; M0 is in progress and M1 has not started.

Source baseline inspected: `9277e5f` (`feat(code-graph): support configurable indexing limits`). Recheck the owning source when starting each phase; this baseline is evidence of inspected behavior, not a promise that the checkout has remained unchanged.

This document replaces the earlier engine-replacement proposal. It incorporates shared-only connections, the new mandatory/additional exclusions and graph limits, and compatibility with the existing code graph. It supersedes the target-state requirements to retain OCR and personal subscriptions in [PLAN.md](PLAN.md) and [PRODUCT.md](PRODUCT.md). Those files still describe existing delivered behavior until the corresponding migration phases complete.

Saving this plan does not implement the engine, run a paid evaluation, retire a service, migrate production, or establish performance gains.

## 1. Objective and decisions

Build an independently implemented Code Mortis engine for PR review and security analysis, using original code and prompts. Preserve all remaining PR review, security scanner, navigation, integration, and operational features. Subscription execution is the only intentional feature removal.

Priorities, in order:

1. Preserve finding quality, security boundaries, immutable history, and truthful coverage.
2. Make execution recoverable and predictable.
3. Reduce end-to-end latency and provider consumption.
4. Simplify operation by removing OCR and subscription infrastructure.

Use shared API connections only. Retire ChatGPT/Claude Code subscription execution, official native-client authentication, personal execution grants, and the native runner. Preserve historical subscription runs and findings. Personal API tokens and MCP are separate features and remain supported.

Deliver the complete replacement through staged adoption. A temporary browser-admin repository engine selector permits comparison and explicit rollback. Never switch engine, provider, connection, or model automatically after a failure.

Keep the existing Python/FastAPI backend, Pydantic, SQLAlchemy/PostgreSQL, Celery/Redis, React frontend, and locked dependencies. Keep Trivy and the existing isolated code graph. Add no orchestration framework, vector database, embedding service, or generic plugin framework for this replacement.

The first Mortis release must satisfy the complete feature contract below. Richer semantic graphs, embeddings, cross-commit inference reuse, automatic model routing, and repository-code execution are outside this plan.

## 2. Ideas selected from the investigation

These are design ideas to implement independently, not upstream code or prompts to copy. Their inclusion does not assert a measured advantage for Code Mortis.

| Reference | Idea to implement independently |
| --- | --- |
| [Alibaba Open Code Review](https://github.com/alibaba/open-code-review) | Deterministic scope, explicit work assignments, validated file coverage, checkpoints, and staged candidate filtering. |
| [PR-Agent](https://github.com/The-PR-Agent/pr-agent) | Efficient diff packing, short execution paths for small PRs, bounded splitting for large changes, and preservation of successful work after partial failure. |
| [Kodus](https://github.com/kodustech/kodus-ai) | Focused analysis tasks, bounded parallel execution, evidence retrieval on demand, verification, and evaluation through production execution paths. |
| [Qodo ecosystem](https://github.com/qodo-ai) | Relevant project rules, repository/history context, and validation of generated results. Public integrations inform the design without assuming hosted engine internals are public. |

Risk prioritization changes execution order and additional investigation depth. It cannot silently reduce required coverage or turn a partially analyzed repository into a clean result.

## 3. Feature-preservation contract

M0 must connect every retained capability to its current implementation, replacement component, and acceptance check. This matrix is the minimum inventory; source inspection must identify any additional current behavior before implementation removes its owner.

| Area | Required preserved behavior |
| --- | --- |
| Shared connections | Existing provider catalog and supported protocols; model discovery and overrides; connection tests that exercise tools; custom endpoints, headers, and supported options; encrypted credentials. |
| PR reviews | Manual and automatic triggers, bot settings, exclusions, trusted-base project rules, sensitivity, effort, language, file limits, budgets, cancellation, superseded runs, and all terminal states. |
| Findings | Candidate assessment, deduplication, immutable occurrences, current resolution, dismissal with a reason, GitHub synchronization, and evidence-based fix verification. |
| Presentation | Inline findings, existing treatment of non-publishable locations, summaries, diagrams, file groups, fix prompts, progress, consumption, and analytics. |
| Security scans | Trivy vulnerability, misconfiguration, and secret scanning; semantic analysis; assessments; advisory freshness and grades; triage; dependency and semantic fix verification; reports and approved GitHub issue publication. |
| Code navigation | PR Code, definitions, dependencies, retained source, finding navigation, and independent Code MCP access. |
| Integrations | Reviews/Security MCP, personal API tokens, GitHub authorization, provenance, revocation, existing consent and approval boundaries, and audit history. |
| Operations | Durable jobs, cancellation/restart recovery, publication reconciliation, additive upgrades, backup/restore, and actionable diagnostics without secrets. |

Non-negotiable invariants:

- A finding absent from a later result is not observed, not fixed. Preserve original evidence and current resolution separately.
- Coverage comes from validated assigned work, never model claims. Incomplete coverage is never clean.
- Persist validated results before publication. Publication retries must not repeat inference.
- Revalidate GitHub permissions before execution; an installation ID alone grants no access.
- Revalidate token/grant provenance, cancellation, and the PR head before further consumption and publication.
- Secrets remain server-side and encrypted. Preserve custom-endpoint restrictions, including `CM_ALLOW_PRIVATE_ENDPOINTS` and `CM_TRUSTED_ENDPOINT_HOSTS`.
- Repository source is untrusted data. Do not execute repository scripts, hooks, agent instructions, or MCP servers. Model tools read only explicitly authorized, bounded evidence.
- Do not resolve GitHub issues or review threads as an implementation side effect.
- Keep English UI, configurable report/comment language, and existing accessibility behavior.

## 4. Retire subscriptions safely

Subscription removal is a migration workstream, not a change to historical interpretation.

- Restrict new connection creation and execution to shared API connections.
- Remove subscription login, device authorization, console tickets, subscription model discovery, and repository-specific personal-execution consent flows.
- Remove subscription choices, management UI, native execution settings, and related user-facing guidance.
- Stop admitting new subscription work. Drain or explicitly cancel existing work before removing the runtime; fence old workers so they cannot start new consumption afterward.
- Mark repositories referencing a subscription connection as requiring selection of a shared connection. Do not select one automatically or silently change billing ownership.
- Preserve historical connection labels, immutable configuration, run ownership/initiator/trigger, findings, consumption, publication records, and audits.
- Reject attempts to resume retired subscription execution. A new run requires an explicitly selected shared connection.
- Remove native clients, `native_runner.py`, runner client/runtime dependencies, runner network/configuration/health checks, and live profile dependencies after the drain gate. Provide an explicit credential/profile retirement procedure without deleting unrelated history or backups.
- Keep historical schema readers compatible. Use additive migrations rather than dropping records or columns needed to interpret old runs.
- Preserve personal API token issuance, expiry, repository scopes, and revocation. Preserve MCP consent and authorization independently.

An OCR rollback after this migration is OCR with shared connections. It must not reactivate subscriptions.

## 5. Exclusions and limits are authoritative

### 5.1 One review/graph file policy

Reuse [core/file_policy.py](backend/code_mortis/core/file_policy.py). Do not duplicate its matching behavior inside the engine.

At the inspected baseline, policy version 2 provides mandatory exclusions plus additional deny-only patterns. Mandatory exclusions cover media, archives/binary documents, compiled artifacts, dependency/build/cache directories, minified files, source maps, lockfiles, sensitive paths/names, and agent instructions. Treat the implementation as the exhaustive list.

Additional patterns retain the current comma/newline parsing, normalization, 4,000-character bound, matching semantics, and rejection of `!` inclusion overrides. Preserve canonical relative-path validation and the current distinction between case-insensitive mandatory matching and custom-pattern matching. Sensitive-name patterns also match otherwise ordinary source filenames; include that behavior in the frozen scope and regression fixtures rather than silently changing it during migration.

Enforce policy at every evidence boundary: initial selection, discovery, reads, searches, diffs, graph context, summaries, background generation, assessment, and fix verification. Check both original and destination paths for renames. Custom includes and rules cannot restore prohibited access.

Preserve existing custom-rule behavior, including trusted-base loading, rule order and applicable path matching, include/exclude behavior, system-rule merging, and compatibility with existing rule configuration filenames. Implement the built-in catalog and prompts independently. Mandatory access restrictions and the selected reporting sensitivity remain authoritative over project instructions.

### 5.2 Independent limit domains

| Domain | Default / maximum | Required behavior |
| --- | --- | --- |
| PR `settings.max_files` | `null` is unlimited; configured values are positive integers | Count eligible files after selection. Exceeding the limit skips PR analysis and dependent assessment/verification; never silently review the first N. Preserve independently budgeted summary generation, as verified by the M0 baseline. |
| Graph `code_graph_limits.max_files` | 1,000 / 5,000 | Count admitted supported source, not every tree entry. |
| Graph `max_file_bytes` | 1 MiB / 4 MiB | Skip an oversized supported file with explicit `file_too_large` status/warning. |
| Graph `max_source_bytes` | 8 MiB / 32 MiB | Fail indexing before blob reads when admitted source exceeds the total. |
| Graph tree entries | 20,000 hard limit | Independent bound over the tree inventory, including entries not admitted for parsing. |
| Graph transport/result | 64 MiB request / 32 MiB result | Preserve bounded transport and validation. |
| Graph extraction | 100,000 facts; 120 seconds | Preserve extraction, storage, and process-resource limits. |
| Model execution | Existing independently configured stage budgets, deadlines, prompt ceilings, and concurrency | These are not indexing limits. Preserve unlimited settings and legacy snapshots without reinterpreting them. |
| Security scanning | Existing scanner/inventory/evidence limits | Separate from review exclusions and graph admission. |

Graph limit values remain strict integers; individual file size cannot exceed total source size. Limits live in `Repository.code_graph_limits`, independently of review settings and inference budgets.

Graph admission order remains: validate inventory and paths, apply exclusions, identify parser support, mark individually oversized supported files, check admitted count/total size, then read admitted blobs. Unsupported formats retain metadata; their bodies do not consume parser source budgets. They may still be eligible for bounded source navigation and PR analysis under those operations' own constraints.

Preserve explicit unsupported/partial/failed/oversized states. A failed refresh retains the prior usable index, but that index must not masquerade as matching a new revision or policy.

### 5.3 Correct chunking semantics

Split already-admitted source into bounded model-context ranges when necessary. Chunking must never bypass an exclusion or an admission limit applicable to that operation.

A graph size limit does not automatically exclude a file from PR review. Conversely, the engine cannot split a file to evade a source limit that does govern the operation. Preserve source/line identity and complete accounting of all assigned content; do not silently clip required input.

Markdown, MDX, configuration, and other eligible text remain reviewable even if the structural parser does not support them. Graph coverage and review coverage remain separate.

## 6. Immutable plans and source access

Before inference, persist a deterministic plan containing:

- Repository identity, initiator, authorization provenance, trigger, and selected shared connection.
- Base, merge-base, and head revisions, or the security scan's pinned revision.
- Engine, planner, prompt, rule, schema, and file-policy versions.
- Effective settings, exclusions, sensitivity, effort, language, budgets, deadlines, and concurrency.
- Selected files/ranges, exclusions with reasons, required work units, dependencies, and relevant input hashes.
- Graph eligibility, graph policy identity, and selected supporting evidence when available.

Preview, limit checks, execution, and coverage use the same selection. Models cannot select the required denominator or declare it complete.

Use stable ordering and task identities. Resuming work cannot silently substitute different context, rules, or revisions. Store evidence-expansion selections and hashes as they occur; a retry must either reproduce them or record why they are unavailable.

Move reusable Git, process, path, and snapshot helpers out of `integrations/ocr.py` into neutral existing infrastructure modules. Preserve their validation, credential isolation, cancellation, and error handling. Reuse authorized Git objects within a run, with restrictive permissions, atomic readiness, and terminal/orphan cleanup. Do not expose worker credentials or object-store paths to model tools.

Provide only bounded inventory, source-range reads, diff reads, literal search, and graph-backed definitions/relationships as needed. Every read is tied to a repository, exact revision, purpose, and policy. Validate blob hashes and returned locations; authorize before returning evidence. Review and security callers retain distinct policies and redaction requirements.

## 7. Compatibility with the existing code graph

Reuse [code_graph/retrieval.py](backend/code_mortis/code_graph/retrieval.py), [code_graph/service.py](backend/code_mortis/code_graph/service.py), [code_graph/store.py](backend/code_mortis/code_graph/store.py), and [reviews/graph_context.py](backend/code_mortis/reviews/graph_context.py). Do not replace the graph implementation.

| Graph state | Engine behavior |
| --- | --- |
| Exact revision and policy available | Use bounded definitions, file relationships, and relevant context. |
| Partial graph | Use available facts and preserve explicit gaps. |
| Missing graph | Continue analysis; preserve authorized asynchronous indexing behavior without waiting. |
| Wrong revision, extractor, or policy | Do not substitute it. |
| Unsupported format or relationship | Use ordinary authorized evidence retrieval where appropriate; report the limitation. |
| Disabled or access revoked | Do not retrieve graph context. |

Required integration rules:

- Match repository, exact commit, extractor version, and policy digest, including exclusions and graph limits.
- Preserve `code-graph/v2`, the updated extractor contract, strict request/result validation, and current limits.
- Preserve browser-admin graph settings, omitted-field compatibility for older clients, paused indexing, previous-index retention, structured failure diagnostics, and oversized-file warnings.
- Recheck authorization and policy before returning source. A retained older index is not current evidence.
- Keep the graph worker as the retained-object writer. Parser isolation, reader locks, and retention protections remain intact. Never hold graph locks during inference.
- Use graph relationships to nominate evidence. Validate actual source before citing it; a relationship alone does not establish a defect or fix.
- Required assigned source and diff context take priority over optional graph enrichment.
- Graph completeness must not determine PR or security completeness.
- Code MCP retains independent `code:read` consent, repository opt-in, read-only tools, and no authority to start models/indexing through reads.
- Do not widen PAT/MCP-origin review eligibility or historical behavior implicitly.
- Preserve PR Code browsing independently of graph enablement or parser support. Reading Code must not start inference or indexing.

Shared-API graph enrichment is a first-release requirement because native subscription execution is being removed. Replace the native-specific integration/version with an engine-neutral context contract; retain readers for historical `graph-native/v1` records. Assessment and verification continue to request source through their existing evidence mechanism.

Initially preserve enrichment bounds: three related files, 12 KiB per selection, and 48 KiB across initial review enrichment. Preserve retrieval bounds separately: 100 results, 500 visited nodes, ten source paths, 64 KiB source output, and depth at most two. Larger indexing limits do not automatically expand model context or retrieval output.

The inspected graph supports definitions and conservative Python/JavaScript/TypeScript file imports. Resolved callers and references are unsupported. Do not represent file-import relationships as resolved calls or confirmed impact.

Graph enrichment remains experimental and opt-in. The previous evaluation did not establish benefit. Evaluate graph on/off through the actual new pipeline before broadening use or raising context bounds. The local Graphify developer index is not the production code graph and is never introduced into model execution.

## 8. Analysis pipeline

Execution order:

`Pinned snapshot -> policy and limits -> deterministic tasks -> analysis -> validation -> assessment/verification -> saved results -> publication`

Pack small related changes into as few requests as practical. Partition large changes deterministically using input size, directories, tests, imports, and applicable rules. Preserve full accounting and enough context to analyze each assigned unit.

Every selected unit receives baseline correctness, security, reliability, and performance analysis. Add focused investigation when a concrete risk or missing evidence warrants it; do not run a fixed collection of specialist agents on every file.

Start with at most one additional evidence round for low effort, two for medium, and four for high, with at most ten requested sources per round. Preserve stricter existing workflow limits. These cap expansion, not required input. Every request consumes the selected stage's budget and deadline.

Validate result shape, task identity, assigned file scope, source revisions, paths, line ranges, and evidence references. Findings must establish a reachable trigger, observable consequence, and supporting code under the selected sensitivity. Preserve existing rules for introduced defects and publishable locations.

A completed model response is not automatically a completed task. A valid empty result can complete assigned work only after all required result and coverage checks pass. Invalid or missing results remain incomplete/failed.

Allow one bounded schema-repair attempt when budget permits. Do not repair endlessly, silently downgrade analysis, or mark exhausted work clean.

Assessment remains separate from discovery. Preserve exact deduplication, historical identity, conflicting evidence, independently versioned assessments, and evidence-based fix verification. Never infer resolution from absence. Supporting source does not expand the set of locations the task is authorized to report.

## 9. Shared-provider transports

Use pooled direct clients for the existing supported protocols: OpenAI Chat Completions, OpenAI Responses, Anthropic, and Anthropic Bedrock. Preserve existing compatible endpoints and provider configurations supported through these protocols, including the existing OpenCode integration behavior.

Preserve model discovery, repository/model overrides, custom headers, supported request options, tool-capability tests, usage reporting, and actionable errors. Validate incompatible combinations before execution. Reuse installed transport infrastructure; pin any required SDK dependency through the existing lockfile process.

Use strict structured output where supported and validated tool/result contracts elsewhere. Preserve refusals, cancellation, rate-limit handling, deadlines, and bounded retry behavior. Connection tests must exercise the actual tools/result path rather than only checking reachability.

Never change provider, model, connection, or protocol to recover silently. Keep secrets encrypted and server-side, preserve endpoint restrictions, and keep Bedrock credentials confined to the appropriate trusted worker.

## 10. Durable execution and budget accounting

Reuse existing durable jobs and [review stage coordination](backend/code_mortis/reviews/pipeline.py). Add only the persistence needed for task-level recovery:

| Record | Purpose |
| --- | --- |
| Analysis task | Stable identity, owner run/scan/job, input hash, dependencies, assigned scope, state, and validated result reference. |
| Analysis attempt | Lease/claim ownership, provider request identity when available, reservations, reported usage, timings, and sanitized diagnostics. |

Fence updates by execution ownership. Stale workers cannot overwrite later attempts or publish after losing their claim. Keep network calls outside database transactions and long-held locks.

Persist validated results before publication; preserve successful tasks when another task fails. Revalidate permissions, token/grant provenance, cancellation, and head revision before further consumption/publication. Reconcile uncertain publication before retrying; do not repeat inference to recover a failed GitHub write.

Preserve independent analysis, assessment, verification, and summary budgets, plus existing scan budgets. Atomically reserve expected input and permitted output before dispatch, then reconcile reported consumption. Charge failed/repair calls appropriately. Unknown usage remains explicit and conservatively reserved; it cannot reset a finite budget or trigger automatic repeat consumption.

Budget enforcement is an application control, not a guarantee about provider billing. Retry only where the response/state establishes safe retry behavior; ambiguous consumption requires an explicit recoverable state.

Keep complete, partial, failed, skipped, cancelled, superseded, and publication states distinct. Progress derives from persisted validated outcomes, not model narration or log parsing.

## 11. Performance design

Start with the existing defaults of two concurrent PR analysis requests and four security analysis requests, configurable within the current 1-8 bounds. Add a shared connection-wide ceiling, initially eight, covering all inference stages so simultaneous jobs cannot multiply their per-run limits unchecked. Apply fair admission across runs. During coexistence, reserve capacity conservatively for legacy OCR execution too.

Optimize in this order:

1. Remove OCR subprocess startup, temporary inference repositories/configuration, and global configuration-lock contention from Mortis execution.
2. Reuse authorized Git snapshots within a run and pooled HTTP connections.
3. Pack small tasks efficiently and parallelize independent work within resource/budget limits.
4. Retrieve additional evidence only when needed; keep required source intact.
5. Reuse validated results only when all relevant input, model, rule, policy, and evidence identities match.
6. Stop obsolete work promptly after cancellation, access loss, or changed PR heads.

Cross-commit inference reuse and automatic model routing are deferred. Do not promise a speedup from cache hits that cannot be obtained under equivalent inputs.

Keep graph indexing off the review critical path. Measure combined review, security, and graph resource use under the same deployment ceilings.

Record queue time, snapshot preparation, planning, provider latency, evidence retrieval, validation, assessment, verification, summary, publication, tokens, peak memory, and CPU. Measure time to useful validated results and full terminal completion separately.

## 12. Preserve the security scanner

Keep Trivy as the deterministic scanner and replace OCR-dependent model execution beneath existing [security orchestration](backend/code_mortis/security_scans/service.py).

Preserve vulnerability, misconfiguration, and secret coverage; advisory freshness; offline/isolation protections; repository-suppression restrictions; semantic analysis; evidence expansion; severity; assessments; accepted-risk states; immutable history; grades; dependency advisory checks; deterministic fix checks; semantic fix verification; reports; analytics; MCP operations; and exact-draft GitHub issue approval.

Secret detection and redaction must precede any relevant source reaching a model. Retain separate coverage/freshness/assessment/resolution states. A stale advisory database, failed tool, or incomplete AI stage cannot become a clean scan.

PR exclusions must not remove lockfiles or other security inputs. Graph indexing limits must not shrink scanner inventory or analysis coverage. If graph hints are used, load evidence through the security snapshot's own authorization, limits, and redaction path. Raw graph source must not bypass these controls.

Use the same transport/task infrastructure where its contracts fit, but retain distinct PR and security policies, schemas, evidence semantics, and finding identities.

## 13. Public contracts and coexistence

Add a temporary browser-admin repository setting `analysis_engine = ocr | mortis`. Existing repositories default to OCR until rollout. Snapshot the setting into new reviews, scans, and other engine-dependent jobs. Existing work retains its original engine identity.

Keep analysis engine identity separate from historical connection/runtime fields. Missing historical metadata means legacy behavior; do not reinterpret it as Mortis.

Initially retain the public `ocr` stage key for compatibility while presenting it as Analysis in the UI. Any later API rename requires explicit versioning. Preserve historical report readers and existing downstream consumers.

Use additive migrations. Regenerate OpenAPI and frontend API types; never hand-edit generated types. Preserve saved `code_graph_limits` when older clients omit that field.

An engine change affects future work only. Never silently resume an old task under another engine. Comparison runs use isolated results and never publish, alter finding resolution, or contaminate production history.

## 14. Phase-by-phase delivery

Checklists record verified work only. Existing exclusions/graph functionality is a foundation to preserve, not evidence that a replacement phase has passed. Complete the exit gate and record evidence before marking a phase done.

| Phase | Depends on | Deliverable | Exit requirement |
| --- | --- | --- | --- |
| M0 | None | Frozen scope, feature matrix, migration inventory, evaluation protocol | Every retained feature and intentional removal has a mapped acceptance check. |
| M1 | M0 | Shared-only admission; neutral source helpers; plans, persistence, budgets, leases | Upgrade/history, policy boundary, restart, and cancellation checks pass. |
| M2 | M1 | Direct shared-provider transports | Protocol contracts pass; actual-provider evidence is separately recorded. |
| M3 | M2 | PR engine and shared-API graph integration | Complete scope accounting and graph-on/off/unsupported-format checks pass. |
| M4 | M3 | Full workflow and security parity | Feature matrix passes; no active Mortis path requires OCR. |
| M5 | M4 | Capped evaluation and failure/resource testing | Quality and reliability gates pass; performance is measured. |
| M6 | M5 | Explicit canary adoption | Representative real reviews/scans complete without blocking defects. |
| M7 | M6 and rollback window | Mortis default and legacy runtime retirement | No active legacy work; upgrade/recovery pass; history remains readable. |

### M0 — Freeze requirements and evaluation

- [x] Reinspect the current checkout and record the source baseline and relevant differences from `9277e5f`. [M0 evidence §1](ENGINE_M0.md#1-baseline-and-executed-evidence): baseline `0cfebd5`; no application runtime changes, only plan/test follow-ups.
- [x] Map every feature in section 3 to source, replacement ownership, and an acceptance case. [PR/shared, security and graph matrices](ENGINE_M0.md#3-retained-prshared-connection-feature-map) map existing checks and identify replacement-specific gaps; a mapped test is not automatically a newly executed test.
- [x] Inventory all OCR callers, including Git/process helpers, connection testing, background generation, graph indexing, summaries, assessment, verification, and security. [OCR inventory](ENGINE_M0.md#6-ocr-dependency-inventory) includes saved-result snapshot paths, scope previews, common structured transport, and neutral helper imports.
- [x] Inventory subscription routes, jobs, settings, grants, profile/runtime dependencies, historical readers, and deployment configuration. [Retirement inventory and M1 decisions](ENGINE_M0.md#7-subscription-retirement-inventory-and-m1-decisions) cover indirect MCP/graph dependencies, standalone generation jobs, old-worker fencing, and historical/shared summary retries.
- [x] Freeze representative scope fixtures under the finalized new exclusions and limits. [Scope v1](backend/tests/fixtures/engine_scope_v1.json) freezes 32 path cases, six graph scenarios, four review gates and graph bounds; [runnable contract](backend/tests/test_engine_scope_contract.py) verifies them. Broader existing scope assets remain listed in [M0 evidence §2](ENGINE_M0.md#2-frozen-scope-contract).
- [ ] Version the evaluation protocol, independent ground truth, cost limits, quality gates, and resource conditions before model calls. [Protocol v1](ENGINE_EVAL_V1.md) defines the method, quality gates and zero-call/zero-token/USD-0 default. Actual independently adjudicated pilot cases/splits, selected shared model and price snapshot, finite live-run caps, and reproducible benchmark resource manifest remain open. No paid call was made or authorized by this audit.

Verification: 1,034 baseline tests and 278 final scope/PR Code tests passed (1,200 distinct cases; overlaps counted once), plus pinned OCR/local-provider and synthetic Trivy container checks and changed-test Ruff. The Trivy check explicitly had missing/stale vulnerability data; it verifies secret/misconfiguration/redaction/isolation and honest partial coverage, not complete dependency vulnerability detection. [Exact commands, limits and gaps](ENGINE_M0.md#1-baseline-and-executed-evidence).

M1 handoff requirements are recorded in [M0 §8](ENGINE_M0.md#8-gaps-and-phase-gates). M0 remains in progress; its final item is not marked complete merely because protocol design and deterministic fixtures exist. M1/runtime retirement has not started.

Exit: no unmapped retained capability or ambiguous intentional removal; comparison cannot obtain an apparent speedup by analyzing less source.

### M1 — Shared execution foundation

- [ ] Reject new subscription creation/execution and provide explicit shared-connection remediation for affected repositories.
- [ ] Implement a safe drain/cancellation and stale-worker fence for existing subscription work.
- [ ] Preserve historical reads and keep PAT/MCP features independent.
- [ ] Move reusable OCR-owned source helpers to neutral modules without weakening validation.
- [ ] Implement immutable plans, stable task identities, additive persistence, leases, and budget reservations.
- [ ] Apply shared review/graph exclusions at all evidence boundaries while retaining the scanner's own policy.
- [ ] Add engine selection and compatibility snapshots without changing existing jobs.
- [ ] Verify upgrades containing old OCR/subscription history and old clients that omit graph settings.

Exit: authorization, scope, budgets, restart/cancellation, and history preservation pass before new inference is enabled.

### M2 — Shared-provider transports

- [ ] Implement and contract-test all currently supported API protocols and options.
- [ ] Preserve discovery, connection tests, headers, custom endpoints, tool support, and selected-model behavior.
- [ ] Validate malformed responses, refusals, partial streams, rate limits, auth/quota failures, timeouts, cancellation, and unknown usage.
- [ ] Verify atomic admission across concurrent requests and independent stage budgets.
- [ ] Record actual-provider checks separately from mocked protocol fixtures, with explicit consumption caps.

Exit: no unsupported existing shared-connection feature and no silent fallback; actual integration gaps remain visible until verified.

### M3 — PR analysis and code graph

- [ ] Implement deterministic selection, trusted-base rules, custom-rule compatibility, and before-inference file gating.
- [ ] Implement batching, bounded splitting, source expansion, schema validation, and validated coverage.
- [ ] Preserve sensitivity, effort, language, cancellation, supersession, progress, and terminal states.
- [ ] Introduce engine-neutral graph context for shared API analysis and keep historical graph metadata readable.
- [ ] Preserve exact revisions/policy digests, optional context bounds, graph failures, paused indexing, and independent Code MCP permissions.
- [ ] Check renames, deleted files, mandatory/custom exclusions, documentation/configuration, oversized input, and unsupported parser languages.

Exit: every selected unit has a truthful outcome; graph availability cannot determine the review denominator or block ordinary review execution.

### M4 — Complete workflows and security

- [ ] Integrate candidate assessment, exact deduplication, history reconciliation, and fix verification.
- [ ] Preserve summaries, diagrams, file groups, fix prompts, and generated project background.
- [ ] Replace security AI calls while retaining Trivy, freshness, secret redaction, assessments, grades, triage, and fix checks.
- [ ] Preserve GitHub publication/reconciliation, exact issue-draft approval, analytics, reports, PR Code, MCP, and PAT operations.
- [ ] Regenerate contracts/types and verify existing frontend workflows and accessibility.
- [ ] Search runtime imports/commands to prove Mortis paths—including graph callers—no longer depend on OCR execution.

Exit: the full retained-feature matrix passes. Subscription UI/runtime removal can finish only after its drain and history gates.

### M5 — Quality, reliability, and performance

- [ ] Run the capped pilot before broad evaluation and retain all failed attempts/consumption.
- [ ] Freeze held-out cases and adjudication before results are inspected.
- [ ] Run paired comparisons through production code paths with identical revisions, eligible scope, models, and budgets.
- [ ] Evaluate graph-on/off separately and preserve its opt-in boundary.
- [ ] Inject worker loss, duplicate delivery, cancellation, revocation, changed heads, provider failures, and uncertain publication/consumption.
- [ ] Measure cold/warm execution and combined review/security/graph resource use.
- [ ] Record quality and reliability gate results; expand inadequate samples rather than weakening gates.

Exit: section 15 gates pass; performance claims have reproducible measurements.

### M6 — Canary rollout

- [ ] Enable Mortis only on explicitly administrator-selected repositories.
- [ ] Observe at least seven days, 20 PR reviews, and five security scans; add cases for workflows the sample misses.
- [ ] Check provider failures, partial coverage, queue pressure, publication, history, resource use, and actual consumption.
- [ ] Exercise an explicit future-work rollback to OCR with shared connections.
- [ ] Confirm no automatic engine/provider fallback and no duplicate publication or resolution changes.

Exit: no blocking quality, reliability, authorization, or feature-parity defect in representative real workflows.

### M7 — Default and retirement

- [ ] Switch the default only after canary acceptance; retain an explicit OCR/shared-only rollback path for at least 14 days.
- [ ] Drain active OCR work before deleting its runtime dependencies.
- [ ] Remove OCR binaries, patches, temporary configuration paths, CI/image requirements, and obsolete health checks after all callers are migrated.
- [ ] Remove remaining subscription clients/runner/configuration and document credential/profile retirement after the separate subscription drain.
- [ ] Keep historical OCR/subscription report readers and immutable data.
- [ ] Update PLAN, PRODUCT, architecture, operations, installation, backup/restore, and acceptance documentation to the implemented shared-only product.
- [ ] Verify clean install, existing-data upgrade, restart, backup/restore, and shared-connection execution without OCR/native clients.

Exit: all required work is complete; retirement is not declared merely because the main review path stopped calling OCR.

## 15. Evaluation and acceptance protocol

Start with a capped pilot of 12 PR cases and eight security cases. Keep development fixtures separate; the initial development corpus can use 20 PR cases and ten security cases. Do not tune on the held-out evaluation set.

The broader target is 100 PR cases and 40 security cases, with three paired repetitions where practical. Both engines use identical pinned revisions, finalized exclusions, eligible scope, selected models, settings, and resource conditions. Record cache state and source manifests. Set explicit token/spending caps before live execution; preparing the harness does not authorize uncapped calls.

Comparison runs must never publish GitHub comments/issues, change resolution, or write into production finding history. Preserve failed attempts and their usage rather than rerunning them out of the accounting.

Include clean cases, independently adjudicated real defects, cross-file defects, large changes, documentation/configuration, renames/deletions, excluded source, missing evidence, unsupported formats, and graph gaps. Evaluate graph-on/off through the same source-selection and execution code used in production.

Quality and reliability gates:

- Precision and recall are no worse than the baseline under the frozen protocol. Define aggregation and uncertainty before running the evaluation; repetitions of one case are not independent new defects.
- No confirmed high/critical finding is lost in the evaluation corpus.
- Every selected unit has an explicit validated outcome.
- Excluded, unsupported, failed, and incomplete work never becomes clean coverage.
- No duplicate publication, silent finding resolution, or historical rewrite.
- No authorization, secret-handling, endpoint restriction, or fallback regression.
- Cancellation, worker restart, revocation, rate limits, and uncertain-consumption cases remain correct.
- If evidence cannot establish parity, enlarge the evaluation; do not weaken the gate.

Performance objectives are 50% lower median latency and 30% lower p95 latency with provider consumption no greater than baseline. Measure PR and security workloads separately, including full end-to-end completion and individual stages. These are targets, not established gains. Report variation and resource conditions; lower scope, warmer caches, or weaker analysis cannot count as an improvement.

Minimum regression cases:

| Boundary | Required checks |
| --- | --- |
| Scope | Mandatory patterns, custom exclusions, prohibited inclusion override, canonical/unsafe paths, uppercase mandatory matches, both rename sides, supporting reads/search/diffs. |
| Review limits | Exact boundary, one over, unlimited, all-excluded input, same selection for preview/gate/execution, dependent-stage skip behavior. |
| Graph admission | Excluded/media/unsupported metadata does not consume parser source budgets; exact bounds; oversized warning; count/total fail before reads; independent tree guard. |
| Graph identity | Changed exclusions/limits invalidate matching identity; wrong commits are never substituted; previous index retained; pause preserved; old clients do not erase limits. |
| Graph context | Required source takes priority; bounded enrichment; missing/partial/disabled/stale graphs; authorization loss; no unsupported caller claims; legacy context metadata remains readable. |
| Scanner separation | PR/graph exclusions do not remove lockfile/security coverage; secret redaction precedes AI; incomplete/freshness failures remain explicit. |
| Recovery | Worker loss, duplicate jobs/webhooks, stale claims, budget races, ambiguous usage, retries, cancellation, supersession, permission/token/grant revocation. |
| Publication/history | Persist before publish, uncertain-write reconciliation, no repeated inference, no duplicate comments/issues, absence is not fixed. |
| Shared-only migration | No new/resumed subscription execution, explicit repository remediation, old history survives, PAT/MCP unaffected, rollback cannot reactivate subscriptions. |

Use existing backend/frontend test infrastructure. Run checks appropriate to each change and record exact commands/results. Mocked tests and unauthenticated startup do not establish real-provider or real-GitHub integration.

## 16. Code ownership and implementation starting points

| Responsibility | Existing owners to inspect/reuse |
| --- | --- |
| File policy and limits | `core/file_policy.py`, `schemas/common.py`, `schemas/code_graph.py`, repository settings router and frontend |
| PR orchestration/history | `reviews/service.py`, `reviews/pipeline.py`, `reviews/assessment.py`, `reviews/verification.py`, `models.py` |
| Shared inference and source access | `integrations/ocr.py`, `reviews/context.py`, existing HTTP/provider discovery infrastructure; move only reusable helpers to neutral owners |
| Summaries/background | `reviews/summary.py`, `reviews/background.py` |
| Security | `security_scans/engine.py`, `service.py`, `scanner.py`, `assessments.py`, `issues.py`, `analytics.py`, `schemas/security.py` |
| Code graph | `code_graph/store.py`, `service.py`, `retrieval.py`, `client.py`, `reviews/graph_context.py`, graph schemas and parser isolation |
| Subscription retirement | Connection/subscription routers and schemas, `reviews/delegated.py`, `integrations/runner_client.py`, `native_runner.py`, subscription UI, Docker/Compose and CI |
| Public compatibility | API presenters/routers, `mcp/`, `api_tokens/`, generated OpenAPI/frontend types, review/security/code pages and tests |

Organize new engine code according to [the existing architecture](docs/architecture.md). Use a small analysis module/package only for shared execution logic that has real callers; do not build a framework around hypothetical adapters or future features. Retain purpose-specific review/security orchestration.

## 17. Phase evidence and handoff

For each completed phase, append a dated record containing the source revision, delivered checklist items, exact verification commands/results, real versus mocked integration evidence, measured consumption/resources where applicable, remaining limitations, and the next phase. Leave unchecked work unchecked.

| Phase | State | Evidence |
| --- | --- | --- |
| M0 | In progress — 5/6 verified | [2026-11-07 audit](ENGINE_M0.md): source/acceptance maps, OCR/subscription inventory, frozen scope and 1,200 distinct passing pytest cases. [Protocol v1](ENGINE_EVAL_V1.md) exists; actual adjudicated quality corpus and live-run manifest pending. |
| M1 | Not started | Pending |
| M2 | Not started | Pending |
| M3 | Not started | Pending |
| M4 | Not started | Pending |
| M5 | Not started | Pending |
| M6 | Not started | Pending |
| M7 | Not started | Pending |

Continue M0 by closing the remaining evaluation-corpus/run-manifest item; see ENGINE_M0.md for verified foundations, concrete M1 decisions, and unresolved gates. Read AGENTS.md, this plan, PLAN.md, PRODUCT.md, and the owning source before changes. Preserve the shared-only target, current exclusions/limits, independent scanner policy, exact-revision graph compatibility, additive history migration, and all remaining product features. Do not start live comparisons until their explicit consumption cap and evaluation protocol are set. Do not mark a phase complete from mocked integration evidence alone.
