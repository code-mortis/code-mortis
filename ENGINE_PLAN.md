# Code Mortis engine replacement plan

Date: 2026-11-07

Status: approved implementation roadmap; M0–M4 implemented locally; M5 engineering scope approved on existing evidence (2026-11-08). Frozen experimental acceptance remains `not_established`. The owner authorizes personal early adoption and complete OCR retirement; broader rollout acceptance is not established.

Milestone update (2026-11-08): M0–M4 are implemented locally. The corrective M5 live comparison preserves **152/152 executions: 138 completed, 14 fully executed uncertain security assessments and zero failed executions**. All resource and accounting records reconcile, with no unfinished required work under the prospective approved rule. The earlier 152-arm run remains unchanged, including its six failures and three Mortis high/critical losses. Historical favorable pilots do not override the larger comparisons. Provider latency stays excluded from performance acceptance.

General source-tool and response-allowance corrections are applied while preserving old saved requests. Final-source evidence covers **3,677 distinct backend checks plus four subtests across retained executions**, 13 host skips, **10 physical worker-loss checks**, **119 evaluator checks across retained executions**, a successful mixed PR/security/graph check and **eight operational preflight arms**. Fresh independent agents reviewed all 67 corrective findings. PR precision/recall are **100% for both engines**; security recall is **12/19 for both**, precision is unresolved, and Mortis still has **three high/critical losses**. Three security judgments remain unresolved under the original excerpt-only contract; six paired cases regress in token consumption, three also in request count. All **21 scoped reliability checks** validate. The same corpus is reused as finite-regression evidence, not a fresh held-out test of the corrected binary. **Frozen M5 experimental acceptance is not established; the separate engineering scope is owner-approved below. The subsequent personal early-adoption decision below authorizes local Mortis-only activation without converting the experimental result into a pass.** See [M5 evidence and remaining gaps](ENGINE_M5.md) and the phase checklists below.

Owner-directed stop (2026-11-08): the performance controller stopped **exactly after execution 96, before 97**, at **22:13:57 UTC**. Two complete 48-execution blocks are retained; block 3 never started, no started block is incomplete, and 864 scheduled executions remain unrun. Accounting exports and cleanup pass; all 16 original services are running and unpaused. Use only these two complete blocks for a **preliminary descriptive comparison**, with provider latency excluded. Pooled PR median is OCR **20.406 s** versus Mortis **10.611 s**; security is **5.393 s** versus **5.524 s**. The PR gain comes from clean cases; historical PR medians are essentially unchanged. Some security profile/cell medians exceed the frozen 5% point margin. Two blocks cannot establish statistical acceptance. **Do not start another long benchmark, rerun the live comparison or make new provider calls without asking the owner.** Frozen thresholds and the 20-block requirement remain unchanged.

Post-stop investigation checkpoint (2026-11-08, before the subsequent correction request): three Sol 6.1 high agents traced all three severe losses and six consumption-regression pairs using existing source, checkpoints, validated replies, judgments and accounting. **No generic engine defect was established by those historical traces, so that investigation made no runtime or prompt patch.** All three truth causes appeared in analysis: one confirmed finding overstated its consequence; two proposals remained explicitly uncertain for missing caller/impact evidence. All were preserved correctly. The six consumption pairs add 39,980 tokens and five requests; those requests exactly match five additional candidate assessments. All 21 physical calls in those pairs are unique and accounted, with no retry, repair, duplicate dispatch or missing-source loop. Detailed results are in ENGINE_M5.md and `data/engine-eval/m5-20261108-poststop-investigation/`.

Subsequent owner-requested corrections (2026-11-08): new Mortis scans pin **`security-task/v4`**. Analysis-loaded supporting files now survive into assessment even when the proposal does not cite them. Bounded, cached Python reference/import hints help the existing source-request loop discover relevant context. Original instructions now explicitly check dependencies, guards, required versus granted authority, supported local consequences and unchanged proposal impact before confirmation, and request concise results. Exact repeated evidence quotes use lossless source references only when smaller and revision-safe; all full source blocks and independent candidate assessments remain. Assigned-file citation validation is strengthened. Legacy scans/retries and OCR keep their original contracts, high reasoning and output allowances are unchanged, and cancellation retains known usage. **557 affected local tests pass; backend Ruff passes.** These are implemented and locally verified corrections, not a new provider-quality result or proof that the six historical token regressions disappear. Historical evidence and the scoped M5 approval remain intact; no new provider calls, long benchmarks, service pauses or deployment occurred. See the final offline-corrections record in ENGINE_M5.md.

Owner decision (2026-11-08): **“Scoped approval using current runs; resolve or explain remaining issues.”** M5's engineering scope is approved using the completed live comparison, scoped reliability checks and two complete performance blocks as preliminary evidence. The severe-loss and consumption investigations satisfy the explanation condition; no generic engine bug requiring repair was confirmed. The three severe losses, unresolved judgments, six consumption regressions and incomplete statistical sample remain explicit limitations, not repaired or passed findings. The historical sealed score, model verdicts, original judgments, provider-latency exclusion and all retained results remain unchanged. This closes the scoped engineering milestone only; M6 activation and production adoption require a separate instruction.

Acceptance revision (2026-11-08, owner-approved): require preserved quality and features, validated reliability, and **no meaningful performance regression**. Keep **50% lower median latency as a stretch target**; the previous 30% lower p95 objective is also a stretch target. Neither speedup is required to close M5. Section 15 defines the prospective acceptance rule. The frozen protocols, scorer, pilot artifacts and historical results in ENGINE_M5.md remain unchanged; this revision does not retrospectively pass any run or reopen M6.

Quality-method revision (2026-11-08, owner-approved): use **frozen-corpus acceptance** for the prospective M5 quality comparison. Observed precision and recall must be no worse than OCR in every declared stratum of the independently reviewed sealed corpus; all confirmed high/critical defects and all reliability requirements remain mandatory. Report family confidence bounds and their limitations, without treating a finite-corpus pass as population-wide parity. M6 supplies broader real-workflow validation. This fixes the earlier rule under which even equally perfect results remain inconclusive; it does not rescore historical pilots or change the performance margins, confidence gate, source scope or required work.

Evaluation scope amendment (2026-11-08, owner-directed): target the first rollout at **Code Mortis**; accept **independent agent sign-off**, explicitly labeled as agent review rather than human/external-expert review. Exclude external provider response latency from the new performance gate. Measure the production workflows against deterministic local provider responses, keeping queueing, scheduling, source/graph/scanner work, parsing, validation, persistence, assessment, historical verification and summaries. Live OpenCode Go runs still establish model-dependent quality/reliability; their elapsed times are descriptive. Do not subtract summed concurrent provider durations from wall time. After separate OCR calibration and before the paired diagnostic, freeze engine-overhead margins: p50 may add at most **5% and 1 second**; p95 may add at most **10% and 2 seconds**, with both absolute and relative caps required. Full representative acceptance remains pending.

Source baseline inspected: `9277e5f` (`feat(code-graph): support configurable indexing limits`). Recheck the owning source when starting each phase; this baseline is evidence of inspected behavior, not a promise that the checkout has remained unchanged.

This document replaces the earlier engine-replacement proposal. It incorporates shared-only connections, the new mandatory/additional exclusions and graph limits, and compatibility with the existing code graph. It supersedes the target-state requirements to retain OCR and personal subscriptions in [PLAN.md](PLAN.md) and [PRODUCT.md](PRODUCT.md). Those files still describe existing delivered behavior until the corresponding migration phases complete.

Saving this plan does not implement the engine, run a paid evaluation, retire a service, migrate production, or establish performance gains.

## Personal early adoption and OCR retirement — 2026-11-08

The owner explicitly authorizes Mortis as the **sole executable PR review and security engine in this personal installation**, accepting the known limitations and addressing problems as they appear. This supersedes the earlier requirement to keep an executable OCR rollback for 14 days or wait for M6 canary completion before this local adoption. It does not authorize production deployment, additional provider calls or a long benchmark. It does not retroactively pass M5, revise thresholds or resolve uncertain judgments.

- [x] Remove OCR/native CLI installation, adapters, delegated execution, runner image/service/network configuration, obsolete CI and active session collectors. Retain report readers, historical engine labels, output-policy aliases, compatible trusted-base rules and the historical `ocr` stage key.
- [x] Route new PR/security, assessment, verification, summary and project-background work through Mortis and shared API connections only; API/MCP/manual/scheduled work has no automatic engine/provider fallback. Remove the engine selector while retaining rules, limits and exclusions.
- [x] Add `20261108_mortis_only`: select Mortis only in mutable future repository settings, without rewriting saved plans, runs or findings. Old OCR API/MCP/webhook resumes and assessment retries reject before changing saved work; worker deliveries stop explicitly. New summaries on historical runs use a separate Mortis job and preserve the original configuration. Exact GitHub repository identity remains mandatory for settings writes.
- [x] Add idempotent `retire-ocr` settlement with original progress/report snapshots, known/unknown consumption retained, completed outputs preserved, pending tasks fenced, and stranded admission slots released only by the operator after all consumers/requests stop. Lock/reload the owner before progress updates to preserve concurrent new-summary work.
- [x] Preserve the existing graph parser, store, navigation, permissions and exclusions. Test exact-revision graph source reaching the actual PR gateway payload without displacing assigned code, plus bounded indexing/PR/security coexistence. Security graph enrichment remains **absent and outside scope**.
- [x] Preserve all 152 live executions, independent judgments, accounting and the 96 stopped performance executions; no new evaluation run or provider request.
- [x] Complete local regression and corrected-fixture follow-ups, image build, additive local migration, history verification and restart. Final evidence and Graphify refresh are recorded in the matching retirement section of ENGINE_M5.md.

Local use: `wilfredinni/code-mortis` remains enabled with **Opencode Go / muse-spark-1.3-contributor**. Start a new manual Review or Security scan; no engine selection is needed. Automatic reviews are intentionally held off locally to prevent unapproved model calls. Re-enabling automation is a separate owner action. All 15 retained services run, the six workers respond, and the removed runner profile volume remains intact.

Known limits remain visible: three high/critical defects lack correct confirmation, three independent judgments remain uncertain, and six historical pairs use more tokens than OCR. The 14 fully executed uncertain product assessments were eligible for scoring under the approved prospective rule; they do not settle independent-review uncertainty. The v4 source/policy/payload changes have offline regression evidence only. They have not proven recovery of those exact misses or token deltas. Two complete performance blocks remain preliminary and exclude provider latency.

## 1. Objective and decisions

Build an independently implemented Code Mortis engine for PR review and security analysis, using original code and prompts. Preserve all remaining PR review, security scanner, navigation, integration, and operational features. Subscription execution is the only intentional feature removal.

Priorities, in order:

1. Preserve finding quality, security boundaries, immutable history, and truthful coverage.
2. Make execution recoverable and predictable.
3. Avoid meaningful regression in engine-controlled execution time, then reduce observed end-to-end latency and provider consumption.
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
| M5 | M4 | Budget-policy-controlled evaluation and failure/resource testing | Quality, reliability, and predeclared performance non-regression gates pass; stretch speedups are optional. |
| M6 | Owner authorization for real workflows | Broader validation after personal adoption | Representative real reviews/scans complete without blocking defects; not yet established. |
| M7 | Explicit personal early-adoption decision | Mortis-only default and legacy retirement | Local upgrade/restart/history checks pass; original M6/14-day OCR rollback prerequisite waived by the owner. |

### M0 — Freeze requirements and evaluation

- [x] Reinspect the current checkout and record the source baseline and relevant differences from `9277e5f`. [M0 evidence §1](ENGINE_M0.md#1-baseline-and-executed-evidence): baseline `0cfebd5`; no application runtime changes, only plan/test follow-ups.
- [x] Map every feature in section 3 to source, replacement ownership, and an acceptance case. [PR/shared, security and graph matrices](ENGINE_M0.md#3-retained-prshared-connection-feature-map) map existing checks and identify replacement-specific gaps; a mapped test is not automatically a newly executed test.
- [x] Inventory all OCR callers, including Git/process helpers, connection testing, background generation, graph indexing, summaries, assessment, verification, and security. [OCR inventory](ENGINE_M0.md#6-ocr-dependency-inventory) includes saved-result snapshot paths, scope previews, common structured transport, and neutral helper imports.
- [x] Inventory subscription routes, jobs, settings, grants, profile/runtime dependencies, historical readers, and deployment configuration. [Retirement inventory and M1 decisions](ENGINE_M0.md#7-subscription-retirement-inventory-and-m1-decisions) cover indirect MCP/graph dependencies, standalone generation jobs, old-worker fencing, and historical/shared summary retries.
- [x] Freeze representative scope fixtures under the finalized new exclusions and limits. [Scope v1](backend/tests/fixtures/engine_scope_v1.json) freezes 32 path cases, six graph scenarios, four review gates and graph bounds; [runnable contract](backend/tests/test_engine_scope_contract.py) verifies them. Broader existing scope assets remain listed in [M0 evidence §2](ENGINE_M0.md#2-frozen-scope-contract).
- [x] Version the evaluation protocol, independent ground truth, spending policy, quality gates, and resource conditions before model calls. [Protocol v1.1](ENGINE_EVAL_V1.md) and [pilot handoff](ENGINE_PILOT.md) freeze 12 PR/eight security synthetic cases, two nonauthor agent reviews per case, 40 head/control checks, deterministic source identities and the [owner-selected profile](scripts/engine_eval/pilot_profile.json). The owner explicitly chose no aggregate cap; accounting and bounded execution remain. Runtime image/schema/rule pins, account binding and effective-resource checks are M2/M5 preflight, not claimed completed. No external inference ran.

Verification: 1,034 baseline tests and 278 final scope/PR Code tests passed (1,200 distinct cases; overlaps counted once), plus pinned OCR/local-provider and synthetic Trivy container checks and changed-test Ruff. The Trivy check explicitly had missing/stale vulnerability data; it verifies secret/misconfiguration/redaction/isolation and honest partial coverage, not complete dependency vulnerability detection. [Exact commands, limits and gaps](ENGINE_M0.md#1-baseline-and-executed-evidence).

M1 handoff requirements are recorded in [M0 §8](ENGINE_M0.md#8-gaps-and-phase-gates). M0 preparation is complete; [pilot limitations and live prerequisites](ENGINE_PILOT.md) remain explicit. M1 local shared-only admission and foundation implementation is recorded in [ENGINE_M1.md](ENGINE_M1.md); operational cutover has not been performed.

Exit: no unmapped retained capability or ambiguous intentional removal; comparison cannot obtain an apparent speedup by analyzing less source.

### M1 — Shared execution foundation

- [x] Reject new subscription creation/execution and provide explicit shared-connection remediation for affected repositories. Shared settings, manual overrides, scans and delayed jobs use exact API-kind admission; retired references remain readable.
- [x] Implement a safe drain/cancellation and stale-worker fence for existing subscription work. New binaries reject delayed native inference; the idempotent retirement command preserves saved results, uncertain usage and publication. **Deployment still requires stopping old binaries and invalidating runner access**; see [cutover procedure](ENGINE_M1.md#3-subscription-cutover-procedure).
- [x] Preserve historical reads and keep PAT/MCP features independent. New shared summary attempts retain original history and use independent authorization through overview synchronization.
- [x] Move reusable OCR-owned source helpers to neutral modules without weakening validation. Process, Git, path, source and snapshot helpers now live in `core/`; graph/scanner helper paths no longer import OCR.
- [x] Implement immutable plans, stable task identities, additive persistence, leases, and budget reservations. Independent stage caps, retained unknown usage, lock-wait expiry, cancellation and stale-attempt checks are covered. Connecting these primitives to inference remains M2–M4.
- [x] Apply shared review/graph exclusions at all evidence boundaries while retaining the scanner's own policy. Original/destination rename checks cover required and optional evidence; OCR scope disagreements fail before inference.
- [x] Add engine selection and compatibility snapshots without changing existing jobs. OCR remains available; Mortis is explicitly unavailable until implemented, with no fallback.
- [x] Verify upgrades containing old OCR/subscription history and old clients that omit graph settings. Additive migration/immutable input tests preserve history; omitted engine and graph settings retain saved values.

Local verification is complete: [ENGINE_M1.md](ENGINE_M1.md#4-verification) records 3,078 passing backend cases plus the fresh 181-case final follow-up, passing browser evidence, isolated OCR/Trivy smokes and remaining gates. No new Mortis inference is enabled.

Exit: authorization, scope, budgets, restart/cancellation, and history preservation pass before new inference is enabled. The local implementation does not establish a successful production cutover or live provider integration.

### M2 — Shared-provider transports

- [x] Implement and contract-test all four currently supported API protocols and options. Direct pooled HTTP/Bedrock transports retain exact model selection, tools/schemas, reasoning replay, safe custom options and explicit capability failures.
- [x] Preserve discovery, connection tests, headers, custom endpoints, tool support, and selected-model behavior. Discovery stays metadata-only; connection-test Jobs now use the new transport with changed-configuration and stale-attempt fencing.
- [x] Validate malformed responses, refusals, partial streams, rate limits, auth/quota failures, timeouts, cancellation, and unknown usage. Only known-zero safe failures may retry within three attempts and the original deadline; unknown consumption remains reserved.
- [x] Verify atomic admission across concurrent requests and independent stage budgets. Connection ceiling eight, per-run limits, fair turns, OCR whole-capacity reservation, retained worker-loss admissions and independent stage accounting are covered with real PostgreSQL tests.
- [x] Record actual-provider checks separately from mocked protocol fixtures, with an explicit consumption policy. Owner-approved uncapped OpenCode Go smoke passed tool round trip and structured output; the full pilot and other live providers remain unverified.

[ENGINE_M2.md](ENGINE_M2.md) records implementation, tests, actual-provider evidence and remaining gates. The Go model supports automatic tool selection only; explicit unsupported choices fail without fallback. M3/M4 add the internal PR and security workflow adapters; public availability stays gated for M5/M6 acceptance. Before deployment, rebuild locked dependencies, apply the additive migration and drain old workers; before M5, enforce the pilot's lower connection ceiling and resource profile.

Exit: no unsupported existing shared-connection feature and no silent fallback; actual integration gaps remain visible until verified.

### M3 — PR analysis and code graph

- [x] Implement deterministic selection, trusted-base rules, custom-rule compatibility, and before-inference file gating. The neutral planner freezes exact Git objects, rules, scope and unit identities; settings precedence, confined rule references and all-or-skip file limits are covered.
- [x] Implement batching, bounded splitting, source expansion, schema validation, and validated coverage. Bounded concurrent execution uses M2 admission and immutable evidence rounds; only validated unit results count toward complete files. Successful candidates survive partial failures without marking incomplete files clean.
- [x] Preserve sensitivity, effort, language, cancellation, supersession, progress, and terminal states. Requests preserve preferences; authorization/target fencing, deadlines, cumulative usage, capacity waits and replay are exercised through the real durable adapter with mocked external responses.
- [x] Introduce engine-neutral graph context for shared API analysis and keep historical graph metadata readable. New `graph-context/v2` uses neutral source helpers; `graph-native/v1` history remains supported. The public engine gate stays closed until downstream parity.
- [x] Preserve exact revisions/policy digests, optional context bounds, graph failures, paused indexing, and independent Code MCP permissions. Graph evidence remains optional and bounded; prompt omissions and lost evidence on resume are explicit, with completed work replayed without new inference or changed requests.
- [x] Check renames, deleted files, mandatory/custom exclusions, documentation/configuration, oversized input, and unsupported parser languages. Scope tests include advanced-base excluded renames at both initial selection and evidence reads, lossless large-deletion splitting, API/native limit separation and graph-independent coverage.

[ENGINE_M3.md](ENGINE_M3.md) records the delivered components, exact verification and remaining gates: 3,477 passing backend cases across three isolated shards, followed by 320 passing focused cases after the final packing refinement; Ruff and API regeneration pass. M4 now covers assessment, verification, summaries and security integration and retained-workflow regression. M5 owns real-provider quality/performance comparisons and fault-injection acceptance; M6 owns opt-in canary enablement. M3 does not establish live workflow parity or measured speed gains.

Exit: every selected unit has a truthful outcome; graph availability cannot determine the review denominator or block ordinary review execution.

### M4 — Complete workflows and security

- [x] Integrate candidate assessment, exact deduplication, history reconciliation, and fix verification. Mortis uses durable structured tasks with current history/finding fences, exact original/current sources, bounded evidence/repair, known-usage replay and no absence-as-resolution. Complete sequential/parallel workflow tests retain finding identity across new, absent and repeated observations.
- [x] Preserve summaries, diagrams, file groups, fix prompts, and generated project background. Independent limits, explicit retries on historical OCR runs, pinned background scope/redaction and diagram validation are covered. Interrupted unfinished summary/background jobs fail explicitly without automatic spending; saved completed summaries recover to a terminal stage. Caller deadlines also bound inline summaries.
- [x] Replace security AI calls while retaining Trivy, freshness, secret redaction, assessments, grades, triage, and fix checks. Scanner-specific evidence/policy remains independent of PR exclusions; all security AI phases use one durable scan allowance. Gateway-backed tests cover freshness, unknown usage, revocation and original/current fix evidence; real Trivy/provider execution remains M5.
- [x] Preserve GitHub publication/reconciliation, exact issue-draft approval, analytics, reports, PR Code, MCP, and PAT operations. New Mortis workflows exercise saved results through real API/OAuth MCP/PAT readers without extra inference. Existing publishers, issue approval/version checks and authorization contracts remain; external GitHub operations are mocked.
- [x] Regenerate contracts/types and verify existing frontend workflows and accessibility. OpenAPI/types regenerate without drift; frontend build passes. Full desktop/mobile Playwright regression passes, including eight new Mortis completed/partial history cases; stage-label screenshots were inspected.
- [x] Search runtime imports/commands to prove Mortis paths—including graph callers—no longer depend on OCR execution. Source audit and a fresh-process import self-check cover jobs, review/security workflows and graph context. OCR helpers load only in explicit legacy paths; historical stage/report formats remain compatible.

Exit: the full retained-feature matrix passes. Subscription UI/runtime removal can finish only after its drain and history gates.

[ENGINE_M4.md](ENGINE_M4.md) records the implementation, exact checks and remaining gaps: **3,529 backend cases pass across three isolated shards; 1,450 browser cases pass with two existing Sentry skips**. Ruff, frontend build and API regeneration pass. At the M4 checkpoint public availability was closed; the subsequent personal adoption decision supersedes that gate locally. Broader live quality/performance and canary acceptance remain separate. Unfinished summary/background crash recovery is deliberately conservative, per-call workflow HTTP pooling has not been optimized, and internal pre-M4 plans are not rewritten. No live superiority or deployment is claimed.

### M5 — Quality, reliability, and performance

M5 closes when the required quality, reliability, and performance non-regression gates in section 15 pass on sufficient evidence. The historical pilot entries below retain their original speedup targets and conclusions. Missing a stretch target does not block acceptance; incomplete evidence or an unresolved meaningful regression does.

- [x] Run the explicitly configured pilot before broad evaluation and retain all failed attempts/consumption. The first run retains 34 quality arms (33 completed, one failed), 139 requests / 678,933 reported tokens. The corrected second run retains 34 quality arms (33 completed, one partial OCR security assessment), 142 requests / 698,864 reported tokens; all 17 Mortis quality arms complete. Both retain all 40 scheduled outcomes, including six explicit unsupported reliability-fixture slots each. Separate fault tests do not count as execution of those slots.
  - [x] Implement a disposable production-service harness, fixed-endpoint accounting proxy, bounded Docker runner and sanitized crash-ledger export. All 34 quality arm inputs pass the provider-free Docker preflight; all 40 scheduled outcomes are retained, including six separate reliability-fixture placeholders.
  - [x] Bind the selected saved shared connection and document pre-output execution amendments in [ENGINE_M5.md](ENGINE_M5.md). The frozen corpus and original preparation profile remain unchanged.
  - [x] Preserve strict PR validation while retaining bounded safe diagnostics and supplying them to the existing one-repair path. Accept exact supplied source newlines through the shared matching helper; preserve legacy settled replay and unknown-usage stops. The formerly failing live PR case now completes without repair and recovers its high-severity defect.
  - [x] Reduce optional security evidence round trips with complete permitted/redacted context for small snapshots, subject to existing prompt/token/deadline limits. Pin new/legacy task contracts and persist packing choices before dispatch; real local HTTP/ledger tests verify restart replay after peer consumption without another provider call. Preserve dependency ordering, history, scanner scope and graph opt-in.
  - [x] Align persisted finding categories with the existing 50-character public contract through additive migration `20261107_finding_category`; verify existing history survives and invalid 51-character values remain rejected. Apply pending additive migrations to the backed-up local development database and refresh affected local services; no production deployment or public Mortis enablement.
  - [x] Freeze and run a second immutable image with all 150 copied source/dependency/runtime files verified, all 34 quality inputs validated offline, both full-workflow smokes and an actual HTTP source-repair/50-character persistence smoke. Preserve the original corpus, model, reasoning, resource ceilings and scorer; retain superseded preflights and failures separately.
  - [x] Investigate median latency using retained timing/usage metadata and independent source review. Provider inference dominates; local ledger overhead is hundreds of milliseconds. Distinguish ratios of arm percentiles from median per-case paired differences, retain partial outcomes, and record existing cache hits instead of claiming missing affinity prevented caching.
  - [x] Add stable Responses cache affinity with explicit overrides and unchanged replay/budget/schema contracts. Replace fixed waves only for parallel unlimited-budget Mortis security analysis with bounded rolling submissions; preserve authorization, unknown-usage stops, deterministic collection, finite-budget/OCR behavior and assessment/verification ordering. Verify **997 focused tests plus four subtests**, independent review and lint; the tiny pilot cannot demonstrate the multi-wave scheduling benefit.
  - [x] Freeze third image/source/resources and pass all 34 provider-free quality preflights, real scanner/full-workflow smokes, and the selected Go protocol check (**three requests / 2,067 reported tokens**, separate from the pilot). Keep scorer, corpus, model, reasoning, stages and resource ceilings unchanged.
  - [x] Run the frozen third pilot and retain all 40 outcomes: **34 completed quality arms**, six unsupported fault slots, **135 requests / 660,170 reported tokens**, zero unknown provider/ledger consumption and zero pair mismatches. Both engines preserve all validated coverage. No selective reruns or model/effort/schema changes.

- [x] Freeze independently reviewed held-out quality cases and explicit evidence roles before outputs. The owner-approved finite comparison uses **76 Code Mortis source-derived Python contract cases, 38 per workload across 19 causal families**, with separate functional/source-history evidence for the other required strata. Four contradictory cases remain excluded and retained. This is not whole-PR cleanliness or live quality across all languages. Preserve the broader 100 PR/40 security target for subsequent validation without inflating source-family counts. Two independent agents are designated before outputs; arm-blinded finding judgments remain required after execution.
  - [x] Freeze the pilot scorer, semantic adjudication rules, paired identity checks and family bootstrap policy. Label the implementer-visible synthetic pilot correctly; it is not a sealed holdout.
  - [x] Independently adjudicate the first pilot's 25 persisted findings with two fresh arm-blinded agents. All match frozen defects; Mortis PR recall was 7/8 versus OCR's 8/8, including one high-severity loss. Both security arms found 5/5. Retain this evidence unchanged.
  - [x] Freshly adjudicate the second pilot: 24 confirmed matches, zero false positives and one uncertain OCR PR source anchor. Mortis confirms all 8 PR and 5 security defects; OCR confirms 7 PR plus one pending finding and 4 security defects, with one partial security assessment. Retain initial/corrected judgment versions; do not force uncertain evidence into a match. No representative or human/expert acceptance is claimed.
  - [x] Independently adjudicate the third pilot with two fresh arm-blinded agents: **26 matches, zero false positives, zero uncertain findings**. Both engines confirm all 8 PR and 5 security defects. This visible synthetic pilot does not establish representative quality acceptance.
- [ ] Run paired comparisons through production code paths with identical revisions, eligible scope, models, and budgets. Include the default parallel PR pipeline, queue-to-terminal completion, historical fix verification and security scans larger than the concurrency window. Separate live-provider quality/reliability from controlled-local-provider performance; retain intended-rollout provider/GitHub integration evidence separately from mocks.
  - [x] Complete the graph-off sequential production-service comparison with zero pair-identity mismatches, fixed shared connection/options, source scope, explicit spending policy and equivalent resource ceilings. Retain all failures. Default parallel scheduling and historical verification performance remain unmeasured.
  - [x] Add a separately versioned workflow harness with actual Linux Celery prefork/Redis delivery and production dispatch/stages. Bind history and execution conditions, retain every stage's ledger, measure from queued persistence, and fence publication before claiming side-effect jobs. Verify two consecutive arms preserve earlier history, a full historical PR completes all stages, and nine security tasks span the concurrency window. Eleven new queue cases pass across retained runs; these synthetic correctness checks do not establish calibrated performance or live quality.
- [x] Verify graph-on/off functionality and preserve its opt-in boundary. Real parser/index checks cover graph-on, graph-off and an exact-head gap: Mortis includes bounded exact-head evidence when available and never substitutes an older revision. Security retains its independent scanner policy and does not claim graph enrichment. These are functional checks; repeated performance by graph/cache cell remains below.
- [x] Exercise the declared worker-loss, duplicate-delivery, cancellation, revocation, changed-head, provider-failure and uncertain-publication/consumption contracts. The following records distinguish physical loss from logical injections and mocked GitHub recovery; real-account rollout behavior remains M6.
  - [x] Exercise real local Celery process loss and duplicate delivery with synthetic provider responses, plus durable accounting and uncertain-publication recovery. Preserve unknown consumption and prevent automatic repeated inference/publication.
  - [x] Execute all six outstanding original reliability-fixture arms through an explicit adapter in isolated Docker. Missing PR source fails closed with 0/2 validated units; stale scanner data and unsupported security targets remain partial. Preserve the original pilot's unsupported slots and all failed setup attempts. This proves truthful incompleteness, not successful partial-result recovery or live scanner quality.
  - [x] Inject physical Linux prefork child loss in complete PR/security workflows after the HTTP response and after durable ledger persistence: four cases pass. Confirm process death before releasing unsettled capacity, retain unknown/reserved versus known consumption, and never repeat analysis automatically. PR settled-result recovery completes; interrupted security work remains explicitly partial and requires deliberate retry.
  - [x] Extend physical Linux prefork loss to PR assessment/verification at response and settled-ledger boundaries, and explicitly retry interrupted security scans through the real service. Eight physical cases pass; security retry preserves its parent/history and reaches completion. A further 62 focused checks cover paid-response cancellation/revocation/head-change, authorization, provider failures and uncertain publication. PR assessment incompleteness remains visible as warnings/neutral check status; interrupted verification never silently resolves findings. Physical security assessment/verification loss and real-account ambiguous GitHub publication are not claimed by these tests.
  - [x] Reproduce and fix a real concurrent-stage plan deadlock. Two jobs inserted plans referencing the same owner, then both attempted to upgrade the foreign-key row locks. The shared planner now locks its review/scan owner before inserting a new plan. The real PostgreSQL regression fails before the fix and passes after it; 184 focused gateway/admission/planner/replay/authority cases pass. No inference retry or consumption rule changed. The earlier mixed-workload failure remains unlinked because its exact database exception was not captured.
- [ ] Measure repeated cold/warm execution and combined review/security/graph resource use under frozen CPU/RAM/concurrency, token and deadline limits. Verify combined capacity and characterize memory pressure/headroom; the security worker reaching 1 GiB without an OOM does not establish spare capacity.
  - [x] Measure one cold-source sequential run per quality arm, with fresh pinned read-only scanner advisories, cgroup limits, retained resource evidence and zero OOM kills. Security reaches its 1 GiB ceiling; spare capacity is unproven. Restore all 16 paused local services after measurement.
  - [x] Repeat the same resource profile for the corrected run: zero OOM kills, PR peak 224,399,360 bytes and security again at 1 GiB. Restore all 16 original containers after 25 min 10.5 s; remove the task-created provider credential and disposable evaluation resources. This remains a single-case quiet-Docker profile, not combined capacity.
  - [x] Preserve third-run resource limits: PR peak **227,782,656 bytes**, security **1 GiB with memory pressure**, zero OOM kills. Restore all 16 containers after **22 min 38.1 s**, remove the private credential, verify no secret bytes in retained artifacts, and refresh nine idle local development services; API readiness returns 200. No production deployment.
  - [x] Complete a separate quiet-Docker controlled diagnostic: **five pairs per workload / 20 completed arms**, real OCR/Trivy, zero-delay local responses, unchanged bounded resources and zero external provider calls. PR p50/p95 OCR **20.153/20.357 s**, Mortis **10.378/10.407 s**; security OCR **5.459/5.695 s**, Mortis **5.294/5.349 s**. All descriptive points satisfy the predeclared caps. The PR trace includes an extra OCR finalizer polling cycle; these are not general speedup or acceptance claims. One visible family per workload leaves uncertainty/representative strata unresolved. Restore all 16 services after **4 min 27.7 s**.
  - [x] Complete a quiet mixed functional probe with concurrent two-file PR history, nine-task security history and a real 123-file/9,600-function graph index. Required stages and graph indexing complete, with zero OOM kills; all 16 original services are restored. Observed aggregate cgroup memory peaks at approximately 1.78 GB on the 5.23 GB Docker VM. Security still reaches its 1 GiB limit with reclaim pressure; this proves completion under the tested overlap, not spare security-worker capacity. The earlier failed probe is retained. Repeat against the final plan-lock fix before binding final acceptance evidence.
  - [x] Repeat that mixed check on the final image after the plan-lock fix. Exact source, fixture and resource bindings match; required work completes with zero OOM kills and approximately **1.79 GB** peak aggregate owned-container cgroup memory. All 16 services restore after **52.9 s**. Then pass all **eight operational preflight arms** across both engines and four profiles, including graph-on/warm and exact-revision-gap/cold; accounting exports, cleanup and restoration pass. Those diagnostic timings are excluded from acceptance.
- [x] Version and freeze the prospective protocol, scorer, fixtures, schedules and resources before acceptance outputs. Seal `70fb42e2cdc62a6b9e0455b360620c5ff62d3efb82aed4ba39f5bf8b05266a12` binds **152 live-quality arms** and **960 controlled-performance arms in 20 blocks**. The independent pre-output audit passes; required work/accounting and per-profile performance checks remain fail closed. The old provider-inclusive 8.8% security median difference does not decide this gate.
  - [x] Implement a new prospective acceptance scorer with separate live-quality and controlled-performance schedules/results, immutable seals, family uncertainty, real work/resource/usage checks and designated independent agent sign-off. Fast live-provider timings cannot pass the performance gate; controlled runs must bind local transport evidence and nontrivial work. The draft protocol remains deliberately unfreezable until actual corpus, sampling, fixture, resource and reviewer identities are supplied. Original scorer/protocol/corpus bytes are unchanged.
  - [x] Run a separate quiet-Docker OCR-only calibration with five repetitions per workload, real OCR/Trivy and zero-delay loopback responses. PR p50/p95: **20.180/20.434 s**; security: **5.647/6.067 s**. Freeze p50 **5% and 1 s**, p95 **10% and 2 s**, both required, before paired diagnostic outputs. These caps reject an added 10-second dispatcher cycle; scanner variation requires more samples, not a relaxed limit. Restore all 16 services after **2 min 45.7 s**. One visible family per workload is calibration evidence, not representative acceptance.
  - [x] Record the owner-approved prospective method amendments before acceptance outputs. Quality requires no observed precision/recall regression on the independently reviewed frozen corpus, no missed confirmed high/critical defects and complete reliability evidence; uncertainty remains descriptive. Performance uses randomized paired execution blocks over a frozen workload matrix because historical PR source lineage does not establish 20 independent families. Keep the nominal one-sided 95% requirement and both original latency caps. Delegated engineering judgment selects 20 complete blocks, with no early acceptance or extension until pass; independent review recommends 60 for better tail resolution. Report the 20-block limitation and restrict claims to this tested matrix. Original pilot protocols/results remain unchanged.
- [x] Record quality, reliability and performance gate results, investigate the severe losses and consumption regressions, and document the owner-approved engineering closure using current runs. The original experimental gates remain unestablished; their incomplete samples and uncertainties are not passes. Additional execution and M6 activation require separate authorization.
  - [x] Record the first pilot’s observed coverage/high-severity-loss failure and unmet original speedup targets. Complete-pair Mortis/OCR ratios are PR p50 **1.26×** / p95 **1.47×**, security p50 **0.76×** / p95 **2.66×**. Small visible samples remain statistically inconclusive; no quality-parity or speedup claim is allowed.
  - [x] Correct the new harness's first-result timer after the run with focused regressions. Frozen `first_validated` observations are invalid and excluded; terminal comparisons and raw artifacts remain unchanged.
  - [x] Record the second pilot's restored Mortis coverage/recall and gap against the original speedup targets. Completed-pair Mortis/OCR ratios are PR p50 **1.1132×** / p95 **0.7260×** (11 pairs), security p50 **1.1727×** / p95 **0.7261×** (five pairs; partial OCR assessment excluded). The observed security outlier uses four calls rather than six, but medians remain slower and neither tail target passes. Tiny visible samples and missing acceptance strata prohibit a speedup or rollout claim.
  - [x] Record third-run nearest-rank p50/p95 ratios: PR **0.8897× / 0.7174×** (11 pairs), security **1.0876× / 0.6749×** (six pairs). PR p50 is 11.0% lower; security p50 is 8.8% higher. Only the original security p95 speedup point target passes; bootstrap/representative acceptance remain inconclusive. These historical comparisons do not decide the revised non-regression gate. No causal cache benefit is established: the single Mortis PR continuation and all other PR calls report zero cache reads. The larger-scan scheduling benefit requires a separate measurement.
  - [x] Record the earlier full backend regression: **3,649 passed, nine host-only skips, four subtests passed**. The corresponding isolated OCR/Linux prefork checks ran separately; that evaluator suite passes **90 unittest cases**. These remain historical evidence, separate from later source corrections and acceptance runs below.
  - [x] Verify the final plan-lock source across **3,655 backend checks and four subtests**, with 13 host skips retained. The first run passes 3,450 checks before the disposable test PostgreSQL filesystem reports ENOSPC at 205 setups; after resetting only that temporary test database, all 205 blocked checks pass. This is combined coverage across two retained runs, not one uninterrupted pass. Source hashes do not drift. **112 evaluator tests**, 40 final acceptance tests, six quality-adapter checks and lint pass; focused counts overlap rather than add to the full suite.
  - [ ] Establish acceptance from the sealed comparisons. Preserve the original incomplete 48-execution performance experiment and both independently reviewed 152-execution live comparisons. The corrective performance experiment stops at the owner-requested 96 executions and remains preliminary; its earlier 48-execution predecessor is not pooled. The owner explicitly authorized the sealed source transfer for live quality, and private scoring truth remained local. Both live comparisons are finished; do not rerun them. Their elapsed times are excluded from performance acceptance. Current quality/consumption blockers and the incomplete frozen performance sample remain below; no further long benchmark or provider calls without new owner approval.
    - [x] Independently approve a separate performance controller v2 with a read-only idle wait before each block (five-second polling, 900-second maximum). All original workload, source, resource and statistical bindings remain unchanged; its self-checks, lint and metadata validation pass. The fresh 960-slot corrective experiment starts at **21:31:01 UTC on November 8**, excluding the original 48 arms and all diagnostics. Its final results remain pending.
    - [x] Preserve and independently adjudicate the original 152-arm quality experiment, completed at **18:52:51 UTC on November 8**: 134 completed, 12 partial security assessments with full source coverage, and six failed Mortis analyses. All 152 resource checks and capture/ledger reconciliations pass; no unknown consumption or unstarted slots. Independent agents reviewed all 67 findings. The unchanged strict score does **not** establish parity: Mortis PR recall is 18/19 versus OCR 19/19; security recall is 15/19 versus OCR 11/19, with one unresolved OCR finding. Mortis has three high/critical losses, and paired-consumption regressions remain. Retain the full result and original scorer; later eligibility changes cannot make it pass retroactively.
    - [x] Verify the applied general PR source-tool correction through final-source DB, physical replay and live checks. The original failed PR arm retains 0/1 coverage and known charges. New plans withhold `read_source` when the final permitted response must conclude; saved plans retain their original policy and request hashes. Final-source DB checks and physical new/legacy continuation replay pass. All 76 corrected PR executions complete; the previously failing case now validates 1/1 source units with three requests. This is operational recovery, not a causal ablation or an accuracy verdict.
    - [x] Verify restored per-response allowance parity through final-source DB, physical replay and live checks. New plans now use OCR's **16,384 output tokens** across PR/security API stages and matching security packing/admission; old plans retain **8,192**. Unknown policy versions fail closed. Aggregate owner budgets, evidence rounds and validation remain unchanged. The exact combined patch was independently frozen before original finding judgments were exposed, then applied with before/after source hashes verified. All 152 corrected live executions finish without analysis failures or unfinished required work; accuracy remains subject to independent adjudication. Evidence is under `data/engine-eval/m5-20261108-completion/recovery/output-cap-parity-proposal/` and `data/engine-eval/m5-20261108-corrective-01/`.
    - [x] Record the owner's prospective uncertainty decision: fully executed semantic security assessment uncertainty may enter quality scoring only with full source coverage, every required task executed, known reconciled usage and valid resources. It stays partial in the product; missed defects and high/critical loss still count. Failed or unfinished execution remains ineligible. Apply this explicit versioned rule only to the next frozen run.
    - [x] Verify the corrected source locally: 3,675 backend checks initially pass with two finite-budget fixture failures and 13 host skips; update only those fixture budgets for the larger response reservations, then all 70 checks in the four affected modules pass. This covers 3,677 distinct checks and four subtests across retained executions. Ten real Linux prefork loss/replay checks pass, including both new and old saved request policies. All 119 evaluator checks pass across the retained run and 15 loopback tests rerun with socket permission; initial interpreter/setup errors remain recorded. Backend lint passes. No production code changed to make the fixture tests pass.
    - [x] Reverify mixed review/security/graph execution and all eight diagnostic preflight arms on the corrected image. The mixed helper initially hits a fresh-database initialization race; preserve its failure, start the unchanged graph initializer before other writers, and wait read-only for its exact marker. Three helper checks and the fresh mixed run pass, preserving concurrent graph/workflow execution and the database safety guard. PR covers 2/2 files, security 9/9 tasks, all 16 original services are restored, and no OOM occurs. Security still reaches its 1 GiB ceiling with reclaim pressure; no spare-capacity claim is established. These checks are not the 960-arm performance experiment.
    - [x] Complete and independently review the corrected 152-execution live comparison. Both engines complete all 38 PR cases and have 31 completed plus seven fully executed uncertain security cases. All 152 resource/accounting records pass; zero unfinished required work, unknown consumption or accounting-proof gaps. Preserve all 67 original finding judgments, including three unresolved security judgments. The corpus provides **reused finite-regression evidence**, not a fresh holdout.
    - [x] Honor the owner's stop at exactly execution 96 before 97. Retain both complete 48-execution performance blocks, every accounting export and the unchanged 960-slot schedule. No block 3 or partially executed block exists. Controller exit 130 reflects the requested SIGTERM/KeyboardInterrupt, not a workflow failure. Verify no evaluation workers/containers/networks remain and all 16 original services are running/unpaused; six configured health checks are healthy. The other ten services have no configured health check.
    - [x] Record complete-block preliminary latency only, excluding external provider latency, old experiments, live timings and operational preflights. Keep all 36 graph/cache/profile comparisons in the artifact, with two or four paired observations per cell. No confidence-based experimental acceptance is claimed; the later scoped engineering approval is separate. Frozen p50/p95 caps and the 20-block requirement remain unchanged.
    - [x] Investigate all three unresolved independent security judgments against existing immutable source. Two agents confirm the local observations but find that the archived token caller rejects missing saved scopes and the OAuth response builder drops blank fields/rebuilds its query. These support separate source-lineage rebuttals of the broad claims. Those callers are outside the frozen snippets, so preserve the original uncertain scores and disclose the context-transfer limit. This is distinct from the 14 approved fully executed product-assessment uncertainties.
    - [x] Bind and score the final retained evidence without changing the frozen rule. All 21 scoped reliability checks and 67 independent review bindings validate. PR precision/recall are 100% for both engines; security recall is 12/19 for both, with unresolved precision (one OCR/two Mortis judgments). Mortis retains three high/critical losses. Six paired cases regress in tokens, three also in requests. All 72 performance bounds remain inconclusive; 864 slots/18 blocks are missing. The score is `not_established`, with all seven blockers retained and source-context rebuttals separate from the scored judgments.
    - [x] Investigate the three severe losses through analysis, assessment, persistence and semantic matching. `sec-103` persists a confirmed finding but overstates low-privilege escalation; `sec-110` and `sec-137` retain their correct local proposals as uncertain because caller/impact evidence is absent. The additional `datetime` allegation in `sec-110` is a model false positive despite supplied dependency context. Independent source/hash/AST/citation and checkpoint checks find no generic engine drop, validation or persistence bug. No finding was automatically confirmed and no score or truth was rewritten.
    - [x] Investigate all six consumption pairs and independently verify stage/event arithmetic, physical IDs, task attempts and candidate counts. The six pairs add 39,980 tokens and five requests; the extra requests exactly match extra distinct candidate assessments. All 21 physical requests succeed with known usage and unique IDs, each Mortis task has one completed attempt, and no correction, retry, continuation or missing-source loop occurs. Mortis inputs are smaller in every pair; additional model output outweighs that saving. Ordinary model variation and prompt/format influence cannot be separated from one retained response pair. No generic consumption bug or safe feature-preserving runtime patch is established.
    - [x] Implement the subsequently requested security corrections in a new, pinned v4 contract: retain uncited analysis context for assessment, offer bounded exact-snapshot Python navigation hints, screen proposals against supplied dependencies/guards and supported consequences, require support for the unchanged material allegation, and validate a current citation on the assigned file. Preserve uncertainty, all distinct assessments, exclusions, redaction, original findings, old retries and high reasoning/output allowances.
    - [x] Implement lossless reduction of repeated evidence on the wire, with exact line/quote/revision matching, historical-provenance guards and a smaller-bytes requirement. Original source blocks and stored findings remain unchanged; shorter-quote replacements that would increase bytes are skipped. Add concise-output and evidence-based proposal-screening instructions without reducing reasoning or skipping assessment. Synthetic wire check: 12,962 → 4,984 bytes; this is not total-token or historical six-case savings.
    - [x] Verify the final corrections locally: 557 tests pass across scanner/service, durable workflow and four new correction modules; backend-wide Ruff passes. Tests cover context transfer, pinned replay metadata, v2/v3/v4 retry compatibility, OCR preservation, assigned-file citations, bounded/cancelled navigation, all-candidate retention, historical quote fidelity and known usage on checkpoint cancellation. No provider calls or long benchmarks; initial sandbox loopback setup errors were stopped and rerun against the isolated test DB with local socket permission.
    - [x] Record the owner's scoped engineering approval on 2026-11-08: “Scoped approval using current runs; resolve or explain remaining issues.” The investigations explain all three severe losses and six consumption regressions; no confirmed generic engine bug remains to repair from this investigation. Retain the known model-quality/context/consumption exceptions, historical `not_established` score and preliminary two-block performance scope. No statistical pass, defect-recovery claim or M6/public activation follows from this decision.
    - [ ] Full experimental acceptance remains unestablished, separately from the approved engineering milestone. The owner stopped the long run; 18 blocks/864 executions remain unrun and the quality/consumption gaps remain recorded. Do not resume, pool experiments, loosen thresholds, replace uncertain judgments with passes or launch another long benchmark/provider run without new owner approval.

Engineering exit: met by the owner's scoped approval on 2026-11-08, using existing runs and completed explanation of the remaining issues. The original experimental exit—passing all section 15 quality, reliability and predeclared performance non-regression gates—is not met and remains separately recorded as `not_established`. The 50% median/30% p95 stretch goals are optional; any claimed improvement requires reproducible measurements. The later explicit personal adoption instruction waives the canary prerequisite for this local installation; broader acceptance remains open.

[ENGINE_M5.md](ENGINE_M5.md) separates historical pilots, both larger live comparisons, corrected-source reliability evidence and the owner-stopped two-block performance experiment. Local graph compatibility, the bounded mixed workload and scoped reliability checks are verified; broad deployment headroom and production integration are not. Severe-loss and consumption diagnosis is complete with no confirmed generic engine bug. The owner has approved the explained engineering scope; any further context supplement or adjudication must preserve the original score. Performance remains preliminary because the frozen sample is incomplete; live-provider timing never decides that gate. Cache benefits and stretch speedups remain optional and unproven. Personal local activation is authorized in the retirement decision above; no general production acceptance is claimed.

### M6 — Broader validation after personal adoption

The owner authorized local early adoption before this broader gate. None of the following observation checks is silently marked complete by that decision.

- [ ] Observe at least seven days, 20 PR reviews and five security scans when the owner authorizes real usage; add missing workflows.
- [ ] Check provider failures, partial coverage, queue pressure, publication, history, resource use and actual consumption.
- [ ] Verify no provider fallback, duplicate publication or silent resolution in representative real workflows.
- [ ] Revalidate model-dependent severe-loss and consumption behavior separately if the owner authorizes further inference. Keep original scores and judgments unchanged.

Exit: no blocking quality, reliability, authorization or feature-parity defect in representative real workflows. OCR execution is retired; recovery means stopping admission and repairing Mortis or restoring a separately reviewed application/database backup, never reinterpreting saved jobs or automatically switching engine/provider.

### M7 — Personal default and retirement

The owner explicitly supersedes the original canary-first/14-day executable OCR rollback requirement for this personal installation.

- [x] Select Mortis as the only future engine; reject old saved OCR execution instead of converting it.
- [x] Remove OCR binaries, patches, temporary configuration/session paths, adapters and CI/image requirements.
- [x] Remove subscription clients/runner/configuration and preserve historical profiles outside current runtime mounts.
- [x] Keep historical OCR/subscription readers, identities, findings and immutable data.
- [x] Update PLAN, PRODUCT, architecture, operations, installation, backup/restore and acceptance documentation for the shared-only product.
- [x] Finish local migration, restart, historical-data preservation and offline container checks; exact results are recorded in ENGINE_M5.md and `data/engine-retirement/20261108/`.
- [ ] Real provider/GitHub execution, full backup restoration and broader production validation remain separate; do not infer them from local mocked checks.

Exit for this authorized personal adoption: complete implementation and local cutover, with limits disclosed. This is not M5 experimental acceptance or a production deployment.

## 15. Evaluation and acceptance protocol

### Approved engineering scope using existing runs

On 2026-11-08, the owner explicitly selected **“Scoped approval using current runs; resolve or explain remaining issues.”** M5's implemented engineering work and scoped reliability evidence are approved using the retained 152 live executions and two complete performance blocks as preliminary performance evidence. The severe-loss/consumption investigation is complete and found no justified engine patch, satisfying the resolve-or-explain condition through the recorded explanations. This decision accepts those explained limitations for this engineering milestone: three severe semantic losses, unresolved judgments, six per-pair consumption regressions and an incomplete statistical performance sample. They remain visible; no model-quality improvement or full experimental pass is implied.

The engineering decision is separate from the frozen experimental decision, which remains **`not_established`**. The historical protocol, thresholds, truth, judgments and score are unchanged. Statistical non-regression, general production capacity, public adoption and M6 validation remain unestablished. Further provider calls or another long benchmark still require owner approval. The original measurement rules below continue to describe what the retained experimental score does and does not establish; their unmet gates are not silently marked passed by the engineering decision.

Start with the frozen pilot of 12 PR cases and eight security cases under its explicit spending policy. The owner selected no aggregate cap; the three completed pilot runs are recorded in ENGINE_M5.md, and each future run needs its own frozen manifest. Keep development fixtures separate; the initial development corpus can use 20 PR cases and ten security cases. Do not tune on the held-out evaluation set.

The broader target is 100 PR cases and 40 security cases, with three paired repetitions where practical. Both engines use identical pinned revisions, finalized exclusions, eligible scope, selected models, settings, and resource conditions. Record cache state and source manifests. Set explicit token/spending policy before live execution; finite caps are the default unless the owner explicitly selects uncapped mode, as recorded for this pilot. Preparing the harness does not start live calls.

Comparison runs must never publish GitHub comments/issues, change resolution, or write into production finding history. Preserve failed attempts and their usage rather than rerunning them out of the accounting.

Include clean cases, independently adjudicated real defects, cross-file defects, large changes, documentation/configuration, renames/deletions, excluded source, missing evidence, unsupported formats, and graph gaps. Evaluate graph-on/off through the same source-selection and execution code used in production.

Quality and reliability gates:

- Observed precision and recall on the independently reviewed frozen corpus are no worse than the baseline in every declared stratum. Define aggregation and report uncertainty before running the evaluation; repetitions of one case are not independent new defects. Under the owner's prospective amendment, confidence bounds describe generalization uncertainty and do not replace this finite-corpus comparison. Undefined metrics, unresolved judgments and missing planned cases still block acceptance.
- No confirmed high/critical finding is lost in the evaluation corpus.
- Every selected unit has an explicit validated outcome.
- Excluded, unsupported, failed, and incomplete work never becomes clean coverage.
- No duplicate publication, silent finding resolution, or historical rewrite.
- No authorization, secret-handling, endpoint restriction, or fallback regression.
- Cancellation, worker restart, revocation, rate limits, and uncertain-consumption cases remain correct.
- If evidence cannot establish parity on the frozen corpus, enlarge the evaluation or address the defect; do not discard unfavorable cases or weaken the gate. A finite-corpus pass does not establish population-wide correctness.

Prospective assessment-uncertainty amendment (owner-approved, November 8): a security assessment that fully executes but returns a semantic `uncertain` verdict may enter live quality scoring only with complete assigned-source and checkpoint inventories, retained candidate evidence, complete verification, known reconciled consumption and valid resources. Recompute and bind this proof from retained runtime records. The product remains partial; missed defects still reduce recall, and high/critical losses remain blocking. Failed or unfinished execution, uncertain verification and unresolved independent judgments remain ineligible. Controlled performance still requires completed workflows. Preserve the original strict scorer/results; this rule applies only to the newly sealed version.

Required performance gate: **no meaningful regression against OCR in engine-controlled execution time**, excluding external provider latency as the owner requested. Use deterministic local provider responses with zero configured response delay and equivalent semantic work for both engines; retain the small local response/measurement overhead as a disclosed part of the setup. Measure queue-to-terminal duration through the actual production workflows, including all required stages. Do not use a no-findings response to bypass assessment or historical verification, and do not count controlled responses as model-quality evidence. The performance fixture inventory is visible and separate from held-out quality truth. Live-provider full elapsed times and request timings remain descriptive; subtracting summed concurrent request durations is not an acceptable engine-overhead estimator.

Evaluate PR and security separately, with p50 and p95 reported for each predeclared workload, graph and cold/warm stratum; an improvement in one workload or percentile cannot cancel a meaningful regression in another. Keep provider consumption no greater than the paired baseline in the separately measured live-quality workload and remain within the same frozen resource, budget and deadline limits. Retain failures, incomplete work and their consumption separately; they never count as successful fast completions.

For the selected owner-uncapped profile, require known physical request counts and total/input/output tokens, reconciled with capture events and the durable ledger; compare paired requests and total tokens. Missing invoice dollars or unsupported cache/reasoning breakdowns remain descriptive unknowns, never zero-cost claims. They do not turn otherwise known token consumption into an unknown dispatch. Unfinished/unknown events, inconsistent totals, unsettled reservations and unknown ledger usage still block acceptance. Any finite cap requires observable accounting for its actual unit; no unobservable USD cap may be declared enforced.

Before new acceptance outputs are inspected, version the protocol/scorer and freeze absolute and relative engine-overhead margins, their combination rule, sample/repetition requirements and the one-sided 95% confidence-bound decision for each required comparison. The owner delegated the recommended tolerance choice. The separate controlled OCR calibration and engineering decision now fix p50 margins at **1,000 ms and 5%**, and p95 at **2,000 ms and 10%**, with **both caps required** in every declared PR/security comparison. This permits small execution overhead without permitting another 10-second dispatch interval; larger scanner variability calls for more samples rather than wider limits. The decision is retained in `data/engine-eval/m5-20261108-finish/materiality-decision.json`, frozen before the paired diagnostic. It neither fits the old live-provider 8.8% median difference nor makes the visible calibration representative. The earlier proposed live-user-wait margins are superseded.

For the owner-approved fixed-matrix performance method, the sampling units are independently reset paired execution blocks, not historical PR families. Freeze the complete randomized workload/profile/graph/cache order before execution and resample whole blocks. Require every cell and every profile to pass separately. The prospective engineering design uses 20 complete blocks and reports the independent recommendation of 60 for better p95 resolution. The nominal one-sided 95% bootstrap bound is approximate and conditional on the frozen matrix; it is not an exact finite-sample coverage guarantee or a population claim. Missing/invalid blocks, degenerate bounds and inconclusive results block acceptance; do not stop early or extend the same experiment until it passes.

Every acceptance seal must bind independently reviewed ground truth, actual source-family identities for descriptive quality uncertainty, fixture/resource identities, graph/cache strata, block/sample sizes and designated reviewers. Those bindings are verified for the retained corrected run; its results do not pass the gates. A performance comparison passes only when its one-sided confidence bound rules out regression beyond the frozen caps. Preserve independently designated agent sign-off as the owner's accepted review method, without claiming human review or population-wide correctness. Bind the evidence role for every required stratum: finite live quality, functional contract/workflow, or static historical review. Functional tests and inert source exports cannot establish model accuracy on additional strata.

Stretch goals: **50% lower median latency** and **30% lower p95 latency**, especially where bounded parallelism can benefit larger workloads. These guide optimization and are not M5 release requirements. Missing them alone must not block a replacement that passes the required quality, reliability and performance non-regression gates. Never reduce source coverage, reasoning effort, validation, assessment, verification, summaries or security boundaries to reach a speed target.

Report stage timings, full terminal completion, provider consumption, cache state, uncertainty and effective resource conditions. Lower scope, warmer caches or weaker analysis cannot count as an improvement. Preserve all existing pilot protocols, scores, artifacts and historical conclusions. Apply the revised rule prospectively through a new version; do not rescore the visible pilot into an acceptance pass. Updating this roadmap does not itself alter the evaluation harness or authorize adoption.

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
| Shared inference and source access | `integrations/llm.py`, `llm_http.py`, `llm_bedrock.py`, `schemas/inference.py`, `core/analysis.py`, `core/inference_admission.py`, `core/source.py`; retain `integrations/ocr.py` for staged coexistence and existing provider discovery |
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
| M0 | Complete — 6/6 preparation items | [Source audit](ENGINE_M0.md), [protocol v1.1](ENGINE_EVAL_V1.md), [20-case synthetic pilot, adjudication and owner-selected profile](ENGINE_PILOT.md). Offline fixture/export checks pass; live behavior and broader quality acceptance are pending. |
| M1 | Complete — local implementation and verification, 8/8 items | [Implementation, cutover procedure and verification](ENGINE_M1.md). The later personal-adoption cutover is recorded in ENGINE_M5.md. |
| M2 | Complete — local implementation and verification, 5/5 items | [Transports, admission, final 536-case regression, live Go smoke and remaining gates](ENGINE_M2.md). Other live providers and production deployment remain unverified. |
| M3 | Complete — local implementation and verification, 6/6 items | [PR planner/executor, durable adapter, graph compatibility, regression evidence and M4 gaps](ENGINE_M3.md). Personal local routing is activated by the later adoption decision; broader acceptance remains pending. |
| M4 | Complete — local implementation and verification, 6/6 items | [Full retained workflows, shared gateway, 3,529 backend/1,450 browser passes and M5 gaps](ENGINE_M4.md). Personal local routing is activated by the later adoption decision; experimental quality/performance and broader canary acceptance remain unestablished. |
| M5 | Engineering scope approved on existing runs — frozen experimental acceptance `not_established` | [Verified results and remaining blockers](ENGINE_M5.md). Corrective live: 152 executions, no execution failures, 14 fully executed uncertain assessments, 67 independent judgments. Corrected-source evidence: 3,677 backend checks plus four subtests across retained runs, 13 skips, 119 evaluator checks, 10 physical loss/replay checks, mixed graph/workflow probe. Performance stopped exactly at 96: two complete blocks, preliminary only, external provider latency excluded. All 16 services restored; no new long benchmark or provider calls without owner approval. |
| M6 | Personal early adoption authorized; broader observation not established | Local Mortis-only use is enabled. Real-workflow observation, model-dependent recovery and general production validation remain pending; new provider calls require owner authorization. |
| M7 | Personal local retirement implemented and verified | OCR/native runtimes and engine selector removed; additive migration and explicit old-job settlement applied; historical results unchanged (three routine scan-freshness metadata updates recorded); 15 retained services restored and six workers respond. [Current evidence and boundaries](ENGINE_M5.md#personal-early-adoption-and-complete-ocr-retirement--2026-11-08). Full backup restoration, real external execution and production deployment remain unverified. |

Next session: read the personal-adoption/retirement record at the end of ENGINE_M5.md. This installation now uses Mortis only through shared connections; OCR/native execution cannot be resumed. Code Mortis remains enabled on Opencode Go / muse-spark-1.3-contributor, with automatic reviews held off to prevent unapproved calls. The smallest next step is an owner-authorized manual PR review or security scan, followed by inspection of coverage, graph omissions and consumption. Security-task/v4 corrections and retirement have local regression evidence; the historical three severe misses, three independent uncertain judgments and six token regressions remain unproven under the new source. The old local Trivy advisory cache requires a successful refresh before dependency coverage can be complete.

Preserve all 152 live executions, original judgments and every performance record. Expanded-context adjudication must be separately versioned without silently changing frozen truth. Do not resume the stopped experiment or start another long benchmark/live rerun/provider calls without asking the owner. Keep the frozen thresholds, 20-block requirement and optional stretch goals unchanged. Earlier ENGINE_M0–M4 and ENGINE_PILOT records remain historical evidence on their pinned sources. Read AGENTS.md, PLAN.md, PRODUCT.md and owning source before changes. Preserve shared-only execution, exclusions/limits, independent scanner policy, exact-revision PR graph compatibility and additive history. M5 experimental acceptance remains `not_established`; personal adoption is explicitly authorized and implemented, while broader acceptance and production deployment remain open.
