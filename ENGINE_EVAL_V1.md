# Mortis engine evaluation protocol v1

Protocol: `mortis-engine-eval/v1.1` (filename retained). Revision 1.1, 2026-11-07: freezes the synthetic pilot and records the owner’s explicit uncapped spending choice and local Docker selection. M0 freezes preparation/design; authenticated transport and effective runtime preflight belong to M2/M5 before live execution.

Frozen design date: 2026-11-07. Source baseline: `0cfebd5398ac25c98dcb37d99bb39eda95fcb897`.

Status: requirements, scope contract, 20-case synthetic pilot, independent agent adjudication and owner-selected preparation profile are frozen. See [pilot evidence and limitations](ENGINE_PILOT.md). The production-path harness, authenticated model validation, runtime version/resource enforcement and broader representative corpus remain pending; neither engine has passed the comparison.

## 1. Default execution and authorization

The default is preparation, validation, and replay only: **zero external model requests, zero tokens, and USD 0**. No provider credentials or production history are required for that mode. A live mode must fail before dispatch unless a separate run manifest contains a frozen corpus digest, exact provider/model/configuration, explicit spending policy, a price snapshot, effective resource limits, and authorization for that run. Default spending remains zero. An explicitly recorded owner choice may set aggregate caps to null in `explicit_owner_uncapped` mode; missing fields or an ordinary null budget are never that choice. The owner selected this uncapped mode for the pilot. This preparation does not dispatch requests.

The historical graph experiment's subscription spending authorization does not apply. This protocol authorizes no GitHub publication, issue/thread resolution, real-repository scan, graph opt-in, deployment, or production-history mutation. The harness must use disposable evaluation persistence and disable publishing at the application adapter boundary while exercising the production analysis/validation paths. Check isolation and cost guards with fake providers before allowing live requests.

## 2. Case and ground-truth records

Each case must record:

- Stable `case_id`, independent `family_id`, workload (`pr` or `security`), and corpus split.
- Immutable repository identity and base/head/merge-base or scan/default-branch SHAs, plus tree/blob identities.
- Complete selected source/diff or explicitly accounted source chunks, allowed paths/ranges, renames/deletions, and expected coverage denominator.
- Exclusions, unsupported/skipped units with reasons, policy/rule/schema/task-contract versions and hashes.
- Connection protocol/model/options, sensitivity, effort, language, per-stage limits, concurrency, and graph eligibility/state/policy/limits/extractor identity.
- Source provenance, storage location/access constraints, and hashes. Do not commit credentials or private source merely to make a public manifest self-contained.

Derive the immutable case digest from canonical sorted JSON; arm and repetition identifiers are separate. A case's identity changes when its source, scope, settings, or truth changes. Do not hide changed cases under the same frozen corpus digest.

Ground truth must be independent of the engines being compared. A truth record includes defect identity, reachable trigger, consequence, severity, affected boundary, allowed finding locations, exact original/current evidence, and a reproducer or explicit inspected proof. Include a negative/fixed control when meaningful. An accepted OCR comment is not an oracle by itself.

Two reviewers independently adjudicate truth before engine outputs are revealed; record disagreement and its resolution by an independent adjudicator. Engine output review is blinded to arm identity. Keep truth, fix descriptions, and reproducer/oracle content out of model context unless they were already legitimate inputs to the reviewed revision. Disputed cases stay outside the frozen evaluable corpus until resolved; do not relabel after observing outputs to favor an engine.

Development target: 20 PR and ten security cases. Held-out pilot target: 12 PR and eight security cases. Broader target: 100 PR and 40 security cases, with three paired repetitions where practical. Related defects/fixes and cases from the same bug family stay in one split. Repetitions and fixed controls do not increase the count of independent defects.

Include clean cases, real high/critical defects, cross-file defects, large changes, documentation/configuration, renames/deletions, sensitive-name source, excluded inputs, unavailable evidence, unsupported formats, graph gaps, stale scanner advisories, and no-supported-target cases. Separately score deterministic reliability/failure injection. The recall denominator contains all eligible adjudicated defects, including ones missed by both engines.

The [pilot manifest](scripts/engine_eval/pilot_manifest.json) now pins 12 PR/eight security synthetic cases and their candidate pilot split: 17 quality microcases and three separately scored reliability fixtures. The latter include injected unavailable-source/scanner state and never enter quality precision/recall denominators. [Two nonauthor agent reviews per case and parent adjudication](scripts/engine_eval/reviews/adjudication.json) preserve initial judgments/disagreements and scope amendments; all 40 head/control checks pass. Agent-reviewed synthetic microcases are not human-adjudicated historical defects or a sealed holdout. Source, truth, controls and oracles are separated during export. See [limitations and missing broader strata](ENGINE_PILOT.md#case-inventory). The [scope fixture](backend/tests/fixtures/engine_scope_v1.json) remains a separate deterministic boundary contract.

## 3. Paired execution

Compare pinned OCR with shared connections against Mortis through production snapshot acquisition, selection, orchestration, evidence retrieval, validation, and result persistence. Keep selected source, revisions, provider/model/options, project rules, sensitivity, effort, language, budgets, deadlines, and declared resource ceilings equivalent. Reject a manifest/selection mismatch before paid inference and record it as a correctness failure.

Use fixed randomization seed `20261107` for arm order within family/repetition. Separate cold/warm source and graph caches; record provider cache behavior where observable. No result reuse across arms. Keep failures and interrupted attempts in the ledger; never choose the best repetition.

PR graph-on/off comparisons form separate strata. OCR's initial shared-API `unsupported_adapter` is explicit; it is not relabeled as graph support. The paired end-to-end product comparison can include Mortis's new optional context behavior, but its changed evidence volume and cost must be reported. Use graph-off equal-context comparisons to isolate engine execution improvements. Required review scope is identical across graph arms. Security stays graph-free unless its separate hint adapter is explicitly included in a versioned run; any hint uses security snapshot authorization/redaction.

Preserve complete required source; the old graph experiment's excerpts/custom assessment task do not substitute for the actual review contract. Graph indexing time is recorded separately and is not silently removed from a cold-start product measurement. Publication latency can use a deterministic sandbox adapter; real GitHub publication remains separate acceptance and is never enabled implicitly by evaluation.

## 4. Scoring and uncertainty

Match validated findings to adjudicated defects using cause, trigger, consequence, and semantically supporting evidence. One finding matches at most one truth defect; duplicate reports of the same root cause count once for recall and as extra invalid reports for precision. Invented evidence, invalid citations, unsupported speculative claims, and unmatched claims are false positives. Uncertain/no-result is not a true positive. Preserve malformed responses, refusals, incomplete units, and missing evidence as separate outcomes rather than clean results.

Per workload, report micro precision `TP / (TP + FP)`, micro recall `TP / (TP + FN)`, per-case macro recall, clean-case false-positive incidence, severity-stratified recall, and validated coverage. A zero denominator is undefined, not 100%. Record every high/critical truth identity found or missed. Citation bounds/source existence and semantic evidence support are separate measures. A fix needs positive supporting proof; absence of a finding is insufficient.

Aggregate all repetitions without selecting winners. Compute paired Mortis-minus-OCR differences, keeping states and repetitions clustered within independent case/defect family. Freeze one-sided 95% confidence bounds before outputs: family bootstrap with 10,000 resamples and seed `20261107` is the descriptive default. Tiny, no-event, or all-identical samples cannot claim certainty from a degenerate bootstrap; require conservative/exact sensitivity analysis or report the comparison inconclusive. Preserve the method and aggregation when expanding the sample.

Quality acceptance requires no worse point precision/recall and lower confidence bounds at least zero for both quality differences, no confirmed high/critical loss, and no invariant violation. If the evidence cannot establish the claim, the result is inconclusive; add independent cases under the same protocol or a transparently versioned extension. Do not widen tolerance, discard failed attempts, or change scoring after outcomes. A small pilot establishes feasibility and failure modes, not statistical parity.

## 5. Reliability gates

- Every assigned unit has a unique final validated outcome; incomplete, failed, unsupported, or excluded work never becomes clean coverage.
- No duplicate publication, silent resolution, history rewrite, unauthorized evidence, exposed secret, weakened endpoint boundary, or silent provider/engine/model fallback.
- Worker loss, duplicate delivery, stale claim, cancellation, revocation, changed head, authentication/quota/rate limits, timeouts, refusals, malformed results, repair, and unknown usage remain explicit.
- Preserve successful task results and failed-call consumption. Unknown consumption blocks automatic redispatch; uncertain publication reconciles saved output without inference.
- Shared-only retirement guards cover stored jobs and delayed retries, not only HTTP forms. Historical runs and independent PAT/MCP permissions survive.

These gates need tests against Mortis in addition to the incumbent evidence in [ENGINE_M0.md](ENGINE_M0.md).

## 6. Cost and resource manifest

Before any live run, replace the zero-call default with an explicitly selected run manifest containing request/token/USD budget policy, per-stage/per-case/arm/repetition settings, provider/model price snapshot and date, finite request deadlines/retries and maximum simultaneous reservations. Account for cache/reasoning/tool charges where applicable. Finite caps remain supported and enforced when selected. The pilot owner explicitly chose no aggregate caps; [the profile](scripts/engine_eval/pilot_profile.json) records that exception without removing accounting, bounded execution or provider quotas. Unknown/placeholder values are never represented as measured billing or effective resource limits.

Persist attempts and atomically reserve expected input plus permitted output before dispatch. Reconcile actual usage; include repairs, expansion, failures, cancellation, and retries. Unknown usage remains conservatively reserved and stops further spend. Record request IDs, requested/returned usage fields, estimates versus observed billing, and the maximum reservation. Bytes are not tokens, and application controls do not guarantee provider billing behavior.

Freeze deployment image/dependency/platform versions, CPU/memory/swap/PID/disk/tmp ceilings, DB/Redis configuration, concurrency, workload sizes, and overlapping-work schedule. Preserve graph parser/worker isolation and the same declared host ceiling for both arms. Do not reuse an old resource experiment's measurements as the new resource manifest.

The observed M0 test environment was Darwin arm64 with Docker aarch64, 12 CPUs and 5,227,761,664 available bytes. The OCR/Trivy smoke container had no external network or production mounts. This is test-environment evidence, not a chosen benchmark platform or demonstrated production capacity. The owner subsequently selected this local Docker target and OpenCode Go / `muse-spark-1.3-contributor`, with no aggregate spending cap. The [dated preparation profile](scripts/engine_eval/pilot_profile.json) pins public prices, incumbent image/service identities and chosen ceilings. Those ceilings still require enforcement and observation before a live benchmark; actual account allowance, returned model version and implementation image/schema/rule hashes remain M2/M5 preflight tasks.

## 7. Performance reporting

Measure queue, acquisition, planning, provider, retrieval/expansion, validation, assessment, verification, summary, and sandbox publication durations. Report time to first useful validated result and full terminal completion separately, plus CPU time, sampled/available peak memory, OOM/restarts, disk/tmp/DB growth, requests, tasks, passes, and tokens.

Keep failed/cancelled durations in failure reports; do not discard them or assign zero. Report PR/security medians and nearest-rank p95 separately, with paired family uncertainty and cold/warm strata. Targets are median ratio at most 0.50, p95 ratio at most 0.70, and consumption no greater than baseline. These are objectives, not measurements. Equal scope and completed required work are prerequisites for any speedup claim.

## 8. Remaining release gates

M0's combined item is complete as requirements/pilot preparation: actual synthetic cases, candidate split, independent agent adjudication, selected shared model, price/spending policy and reproducible preparation/resource profile exist. [ENGINE_PILOT.md](ENGINE_PILOT.md) records the exact boundary and owner's choices. Revision 1.1 makes explicit that runtime-specific pins and authenticated/effective-resource checks occur after implementation in M2/M5; they are not claimed complete in M0.

M1's source-only foundations require no live provider calls or further owner model/budget/host decisions. Before live integration/evaluation, the implementation harness and fake-provider accounting/isolation checks must pass, bind the saved shared connection and verify its account/model availability, and record effective limits plus all implementation/advisory identities. A connection self-test uses a separate manifest; it does not establish corpus quality or implicitly start the broader evaluation. No live provider behavior, performance gain or feature parity is claimed by M0.
