# M5 — evaluation and reliability evidence

M5 is in progress: the corrected second feasibility run completes all Mortis quality cases and recovers the first pilot's missed high-severity PR defect, but **acceptance has not passed**. Latency targets remain unmet, and the visible synthetic pilot cannot establish representative quality or reliability. This record retains both runs, distinguishing local tests, actual provider results and remaining acceptance work. Public Mortis availability remains closed; this milestone does not authorize M6 adoption.

## Frozen pilot and execution amendments

The original [pilot profile](scripts/engine_eval/pilot_profile.json), [corpus manifest](scripts/engine_eval/pilot_manifest.json) and sources remain unchanged. The owner selected **Opencode Go / `muse-spark-1.3-contributor`**, uncapped aggregate spending, the current local Docker environment and temporary pausing of local development services for measurements. No GitHub publication or production history changes are part of the evaluation.

The following execution details were decided before live M5 outputs were inspected. They extend the preparation profile explicitly; they are not changes to the fixtures or acceptance criteria:

- Bind the actual saved shared connection, using provider `opencode`, protocol `openai-responses` and `https://opencode.ai/zen/go/v1`. The preparation profile's proposed endpoint is not substituted for the saved endpoint. Preserve the selected model and its 500,000-token prompt ceiling.
- Both arms receive the same effective provider settings for each workload: a 600-second request timeout, medium reasoning for PR work and high reasoning for security work. These match the production workload adapters. The saved connection itself is unchanged. Record the effective options in each case identity.
- Run the existing sequential production review compatibility path, including analysis, assessment, available fix verification and summary. The fixtures contain no prior findings, so their fix-verification work may be empty. This pilot does **not** measure the default version-2 parallel job pipeline, queue latency or historical fix-verification performance.
- Use one arm and one case at a time. The capture proxy enforces at most four physical upstream HTTP requests; production admission's eight logical slots are a different mechanism. Preserve OCR's exclusive admission lease. Do not infer aggregate concurrency from that lease's slot count.
- Preserve and record the actual production retry policies: the pinned OCR binary makes up to six HTTP attempts on a known non-consuming 429 response (five retries), while Mortis makes up to three (two retries). Both bounds were measured with a local fake provider before live outputs. This explicitly supersedes the preparation profile's one-retry selection. The capture proxy adds no retries, stops new forwarding on unknown consumption and caps retained provider events at 256 per case; already in-flight responses are still accounted for.
- Use the same immutable candidate image for both arms, derived from the frozen incumbent image and current locked Python dependencies. Keep its pinned OCR, Git and Trivy binaries. The security arm's whole worker uses the stricter scanner ceiling of two CPUs and 1 GiB; PR workers use four CPUs and 2 GiB. Limits and read-only isolation are inspected before dispatch.
- Source snapshots and local worker caches start cold for each arm. Provider-side cache behavior is observed, not asserted cold. Capture buffers streamed responses until completion; it cannot establish first-token latency.
- Security advisories and checks are an explicit exception to cold caches: a fresh immutable snapshot is preloaded on disk and mounted read-only in both arms. The actual advisory database is larger than the original 512 MiB temporary filesystem. Keep the 512 MiB tmpfs and 1 GiB scanner memory limits, but account for the disk-backed snapshot under a 3 GiB cache ceiling and the existing 8 GiB working-data ceiling. Verify advisory/check content hashes and freshness before and after the real scanner, before allowing AI work. This is not a cold advisory-download benchmark.
- Retain all 40 scheduled outcomes. The 17 quality microcases produce 34 possible live arm runs. The three reliability fixtures require the separate failure-injection harness and are never counted as successful quality runs.

The scorer and adjudication protocol are frozen separately before live results. The original synthetic fixtures were visible to implementers in M0; they are a feasibility pilot, **not a sealed held-out sample**. No prompt or heuristic tuning on pilot outputs is permitted. A successful pilot cannot establish the broader quality or performance acceptance gates by itself.

## Implemented tooling

- [Runtime](scripts/engine_eval/runtime.py): real review and security services against isolated PostgreSQL/Redis, inert Git exports, exact source/scope/settings checks, a read-only synthetic GitHub adapter and explicit publication rejection. Worker inputs exclude truth, oracles and the scorer.
- [Capture](scripts/engine_eval/capture.py): selected-endpoint/model forwarding, bounded request and response bodies, durable started/finished accounting, no fallback, and a stop on unknown consumption, authentication or quota failure. Raw prompts, credentials and provider bodies are not capture events.
- [Container runner](scripts/engine_eval/run.py) and [image](scripts/engine_eval/Dockerfile): immutable images, disposable services, nonroot/read-only workers, CPU/memory/swap/PID/tmpfs limits, bounded output, deadlines, host-quiescence checks and cleanup of only containers created by the evaluation.
- [Scorer](scripts/engine_eval/score.py) and [protocol](scripts/engine_eval/scoring_protocol.json): immutable seals, arm-blinded packets, independent semantic judgments, paired identity checks, all-attempt accounting and family-clustered bootstrap intervals. Insufficient or degenerate samples remain inconclusive.
- [Reliability checks](backend/tests/test_engine_reliability.py): real local Celery process loss, duplicate delivery, durable usage boundaries and uncertain-publication reconciliation, with synthetic provider responses.

## Verification and results — first pilot

The first pilot used product revision `9378d3789528f1625d3f43ab916a0b85c93ee304` without changing production Python, database schema, API or UI behavior. Its evaluation image is `sha256:36f1edd1cf707429942f2a84fa28c2a3126e391285d1c3d9c5c149c914a08153`, retaining OCR `v1.13.1 (8eb8334+cm-context-scope-v2)`, Python 3.13.17, Git 2.39.5 and Trivy 0.76.0. The corrections for the second pilot are recorded separately below.

| Check | Observed evidence |
| --- | --- |
| Offline harness/scorer/capture/scanner tests | `backend/.venv/bin/python -m unittest discover -s scripts/engine_eval -p 'test_*.py' -q`: **58 pass, 18.586 s** with local loopback access. The initial sandboxed invocation could not bind the fake HTTP servers; its 15 setup errors are retained separately. |
| Backend evaluation and worker-loss checks | From `backend`, `.venv/bin/python -m pytest -q tests/test_engine_evaluation.py tests/test_engine_reliability.py`: **36 pass, 27.70 s**. Provider responses are synthetic; Git, PostgreSQL, Redis, Celery subprocesses and process kills are real. |
| Existing reliability/auth/publication regression | **335 pass, 64.23 s** across the focused existing modules. This is not a rerun of the entire M4 backend/browser suite. |
| Final Docker input preflight | **34 validated quality arms**, six explicit reliability-fixture placeholders, no provider calls or credential mount. Migrations, nonroot/read-only execution, bounds, source identities and read-only scanner mounts pass. |
| Actual pinned PR workflows with fake provider | Both arms complete analysis, assessment and summary and persist one finding with 1/1 coverage. OCR reports seven requests/105 synthetic tokens; Mortis reports three/45. These are fixture responses, not model quality or performance results. |
| Actual pinned security workflows with fake provider | Both arms complete real Trivy, AI and persistence with 1/1 coverage, one request/15 synthetic tokens and unchanged scanner hashes. No GitHub writes or external inference. |
| Actual scanner under resource limits | Fresh official advisory/check snapshot, **1,678,134,984 bytes**, SHA-256 `33b3d9d393a4e04b84fd955bb30be1550b8b343688d5f06497f4858e5fc5f50d`. All three scanners pass offline with the read-only cache. Memory peaks at **1 GiB**, with reclaim pressure and **zero OOM/OOM-kill events**; spare memory capacity is not established. |
| Crash-ledger export | The bounded allowlisted SQL export executes successfully against disposable PostgreSQL. Export failure/truncation preserves the database and identifies recovery steps rather than discarding its only consumption evidence. |
| Static checks | Ruff on the new evaluation scripts/tests and `git diff --check` pass. No API/frontend regeneration is needed. |
| Local Graphify index | AST refresh completed. The developer tool reports 87 zero-node files (mostly JSON) and a partial parse of unchanged `frontend/src/pages/settings.tsx` at line 480. Its >5,000-node visualization uses an aggregated view. These are local navigation limitations, not production code-graph test failures. |

The final pre-output scoring seal binds scorer SHA-256 `b7f7a0673101ba90731b7c48c78d64888c1de9f8aa07bc6cb8002d9d36c70d05`; earlier pre-output seals remain retained. Live results are recorded separately below. The acquisition adapter uses frozen local Git snapshots, so queue and real GitHub acquisition/publication latency are outside this comparison.

The 335-case regression command, run from `backend`, was:

```sh
.venv/bin/python -m pytest -q tests/test_workflow_inference.py tests/test_llm_execution.py tests/test_inference_admission.py tests/test_mortis_workflow.py tests/test_mortis_review.py tests/test_mortis_security.py tests/test_mortis_summary_background.py tests/test_parallel_stages.py tests/test_background.py tests/test_pr_summary.py --maxfail=1
```

Those checks exercise production logic with real local database/Git state and mocked external services. They do not establish authenticated GitHub publication, real provider failure recovery or live scanner coverage.

The process-loss tests use real Celery **solo** workers and background jobs. SIGKILL is injected before claim, after claim/before HTTP, after the provider response/before ledger settlement, and after ledger settlement/before workflow persistence. These are four distinct boundaries. The response-before-settlement case keeps its full unknown reservation and active admission; the settled case retains its validated result and 12 known synthetic tokens. Duplicate delivery does not dispatch again. Recovery explicitly fails the unfinished background draft. Active admissions are released only after confirmed process exit; automatic reclamation is not established.

## Live pilot: measured outcome

Run `mortis-m5-20261107-pilot-01` used the exact shared Go connection and returned model `muse-spark-1.3-contributor`. The live manifest SHA-256 is `a3a0a1401115d4795bf614265161a77a68663e94cfa014697e342b7b18100b64`; verified resource identity is `d18fa37c966ef59e15b5b14d703aa574742a178b1269d945dba5c39631a03b51`.

All **34 quality arms** ran: **33 completed, one failed**. Six reliability-fixture slots remain explicitly `unsupported_fixture`; the independent fault tests above are not represented as successful executions of those fixture slots. All 40 scheduled outcomes and all failed work remain in the artifacts. No arm was selectively retried, no prompts/heuristics were tuned, and the engine/scorer remained frozen during the run.

The durable capture and independently recovered ledger agree on **139 provider requests and 678,933 reported tokens**. Every forwarded response was HTTP 200; no capture event had unknown consumption. Cache-write counts, invoice cost, region and an immutable underlying model version were not supplied. The scorer's conservative `unknown_consumption` blocker includes these missing usage components; it does not mean the reported total tokens or request count were lost. Do not add the engine ledger subtotal to the proxy subtotal: those records overlap.

Two fresh, arm-blinded Sol 6.1 high agents judged disjoint PR/security packets against the frozen source and defects. All **25 findings** matched, all citations were valid, and neither judge reported a false positive or unresolved match. These are independent agent judgments on a visible synthetic corpus, not human/expert or representative held-out acceptance.

| Workload / engine | Completed arms | Frozen defects found | False positives | Reported tokens, including failures | Requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| PR / OCR | 11/11 | 8/8 | 0 | 432,489 | 80 |
| PR / Mortis | 10/11 | 7/8 | 0 | 110,304 | 31 |
| Security / OCR | 6/6 | 5/5 | 0 | 75,869 | 13 |
| Security / Mortis | 6/6 | 5/5 | 0 | 60,271 | 15 |

Mortis PR file coverage is **12/13 assigned units**; OCR covers 13/13. Both security arms cover all assigned units. The missed PR defect is high severity, so the plan's no-high-severity-loss and complete-coverage gates fail on this pilot regardless of confidence intervals.

The frozen nearest-rank method compares only pairs with complete required workflows for latency, while retaining failures separately and including their consumption above:

| Workload | Eligible complete pairs | Mortis/OCR p50 latency | Mortis/OCR p95 latency | Plan targets |
| --- | ---: | ---: | ---: | --- |
| PR | 10 | **1.26×** | **1.47×** | ≤0.50× p50; ≤0.70× p95 |
| Security | 6 | **0.76×** | **2.66×** | ≤0.50× p50; ≤0.70× p95 |

Neither workload meets the point latency targets. The family bootstrap is inconclusive because the samples are tiny; no speedup or quality-parity claim is allowed. Pair identities have **zero mismatches**. All-attempt timings, failed durations, phases and intervals remain in the score artifact; the table does not turn an incomplete run into a successful fast result.

PR workers peaked at approximately 217 MiB for OCR and 153 MiB for Mortis. Both security workers reached their **1 GiB ceiling**. There were **zero OOM kills**, but security memory headroom is unproven. Pausing other containers made Docker quiescent without releasing their resident memory. This is not a combined-capacity benchmark.

All **16** previously running local development/test containers were restored after **21 minutes 46 seconds**. The bounded final ledger export succeeded without truncation; disposable evaluation containers/networks were removed. The task-created plaintext credential file was deleted. No GitHub publication, production data changes, saved-connection changes or public Mortis enablement occurred.

## Findings to address before acceptance

1. **PR result-validation failure:** Mortis `pr-08` made two successful HTTP requests consuming 11,336 reported tokens. Both durable results were `feedback/invalid_result`; the correction allowance ended with zero validated coverage and a skipped summary. Existing diagnostics discard the specific `ValueError` reason, and capture deliberately omits raw provider bodies. The evidence cannot distinguish a malformed final result from invalid evidence/tool arguments. Add bounded sanitized validation-reason diagnostics and independent development regressions before attempting a new versioned evaluation; preserve the strict validator. The legacy user-facing error also incorrectly names OCR for this Mortis run.
2. **Security tail latency:** Mortis `sec-05` took 115.827 s versus OCR's 18.233 s. The difference is concentrated in analysis (+30.031 s) and assessment (+67.998 s); Trivy was slightly faster for Mortis. Mortis used six calls versus four, with two observed evidence-expansion passes and sequential assessment calls. These were validated expansions, not failed schema repairs or transport retries. Provider-duration sums overlap and must not be interpreted as wall-clock latency. Develop separate cross-file fixtures to investigate evidence packing and assessment scheduling; do not tune on this pilot.
3. **First-result timing correction:** the frozen harness counted any settled ledger result, including validation feedback, as `first_validated`. That metric is **invalid for this run and excluded from conclusions**; terminal durations, coverage, usage and paired terminal comparisons are unaffected. The post-run harness counts only accepted, contract-validated terminal `final`/`validated` outputs, excluding nonempty `required_paths` or `required_sources` continuation requests. Its focused module now has **27 passing tests, 10.33 s**, including rejected settlements, feedback/expansion and stable first-terminal timestamps. The versioned `metric-validity` records retain the corrected runtime hashes; final import ordering also passes the backend Ruff configuration. This measures a validated terminal task output, not whole-workflow coverage or a true positive. Rebuild the image before future runs; the live image above intentionally retains the frozen implementation. Artifacts and scorer are not rewritten, and OCR still has no equivalent observed first-result metric.

## Retained evidence and replay

Local, ignored artifacts are retained under `data/engine-eval/m5-20261107/` with SHA-256 inventories. Principal records are `live-01/results.json`, `live-01/recovered-ledger.json`, `live-manifest.json`, `effective-resource-proof.json`, `scoring-freeze-v3.json`, `score-adjudicated.json`, both independent judgment files and `pause-restore.json`. Earlier dry runs, failed setup checks and seals remain separate. The large read-only scanner cache remains at `/private/tmp/code-mortis-engine-trivy-m5-20261107/cache`; its content identity and preflight evidence are retained in the artifact directory. Never mount the blind packet, key, judgments or scorer truth into an engine worker.

The repository scripts support provider-free validation by default; `run.py --live` additionally requires the explicitly authorized immutable manifest, private server-side credential file, pinned image/cache and a quiet host. A future run needs a new output directory, a fresh-enough scanner snapshot, a new code/image seal after changes, and another versioned run manifest. Replaying the frozen scorer requires no provider calls:

```sh
backend/.venv/bin/python scripts/engine_eval/score.py score \
  --results data/engine-eval/m5-20261107/live-01/results.json \
  --judgments data/engine-eval/m5-20261107/judgments-pr.json \
  --judgments data/engine-eval/m5-20261107/judgments-security.json \
  --output /tmp/mortis-m5-score-replay.json
```

## Corrections and second pilot — 2026-11-07

The owner authorized fixing the validation and latency problems and rerunning the unchanged pilot. The following changes were derived from source inspection and independent development fixtures, not from pilot truth or prompt tuning:

- PR contract failures now retain bounded diagnostics with fixed reason codes, whitelisted field paths and static guidance. The existing single repair receives those diagnostics; failed report units retain them too. Rejected provider text and unexpected field names are not retained. Legacy settled feedback without diagnostics regenerates its original repair request and hash. Wire-schema failures still stop at the transport boundary; this does not turn every malformed response into a repair.
- Exact supplied source text with a terminal newline now passes `existing_code` validation through the existing `quote_matches` helper. Nonblank content and assigned-line boundaries remain mandatory. Repository CRLF text is already normalized in the supplied context; unsupported output text is not treated as equivalent arbitrarily. The first pilot's precise rejection remains unknown because the rejected response was deliberately not retained.
- Mortis security analysis/assessment can supply complete permitted, redacted source for small snapshots before the first call: at most 16 files and 32 KiB, also bounded by the actual serialized transport request, prompt ceiling and token allowance including output. Otherwise it keeps focused evidence and the existing explicit expansion path. It never changes assigned coverage, exclusions, scanner inventory, graph opt-in, assessment history or dependency ordering. Source reads/redaction consume the task deadline.
- New security scans pin `security-task/v3`; existing scans without a pin retain v2 and focused evidence, including explicit retries. Each new task atomically persists its focused/packed choice before inference. Replays restore that choice even if peer consumption changes the remaining allowance. A settled request can replay at zero remaining allowance; the durable gateway still refuses fresh inference without sufficient budget. No raw request is added to checkpoints.
- The real-container smoke found a separate persistence mismatch: the existing public category contract permits 50 characters while `findings.category` allowed only 30. Additive migration `20261107_finding_category` widens the column to 50 without rewriting history. The public schema is unchanged; 51 characters remain invalid. Failed Mortis reviews also name Mortis correctly instead of OCR.
- The evaluation runtime pins the same security task contract as new production scans. Its corrected first-result timer is included in the new image; the first pilot's invalid first-result observations remain excluded. No corpus source, scorer, adjudication threshold, model, reasoning setting or resource ceiling was changed in response to pilot outputs.

| Corrected-path check | Verified evidence |
| --- | --- |
| PR validation, expansion, graph, workflows and evaluator regression | **321 pass, 84.84 s**. Includes strict source bounds, safe diagnostics, one repair, legacy replay and graph/exclusion contracts. |
| Final security/restart regression | **29 pass, 9.94 s**, plus **473 repository-security tests pass, 28.97 s**. Real local HTTP and durable ledger tests establish independent overlap, ordered persistence, unknown-usage/cancellation/revocation stops, legacy checkpoint reuse and worker-loss replay after peer consumption. Scanner/GitHub behavior in these unit tests is mocked. |
| Category migration and current runtime regression | **30 pass, 11.02 s**, including old-row preservation, saving 50 characters through `save_analysis`, rejecting 51, and both evaluation arms pinning the current security contract. This overlaps the earlier evaluator module; counts are not additive. |
| Final-image Docker preflight | All **150** copied production/dependency/runtime files match the sealed source inventory; **34** quality arms validate offline and six fault slots remain explicit placeholders. Disposable database migration, resource inspection, readonly input/cache mounts and both real PR/security workflow smokes pass. |
| Actual HTTP contract repair and persistence | Independent arbitrary-source smoke rejects an incorrect `existing_code`, records `existing_code_mismatch`, performs exactly one repair, completes analysis/assessment/summary and stores the full 50-character category: **1/1 coverage, four requests, 60 synthetic tokens**. Rejected quote text is absent from the retained ledger. |
| Strict wire-schema rejection | A separate wrong-type response is rejected as `malformed_response` with **15 known synthetic tokens**, no automatic retry and no first validated result. Retained separately from successful contract repair. |
| Static checks | Ruff on all changed production files, migration, tests and runtime passes; `git diff --check` passes. No API or frontend contract change. |

The corrected immutable image is `sha256:1482bbb753c763c7f2da6a6a19ae08eb2dcdaf139e1eb7e73302654f3e8123e1`, built on the first pilot's pinned image. Source inventory SHA-256 is `8fea360509c7936968d55a3e172e9946c77955f9f3f82cdcae5287161556ba68`; inspected resource proof SHA-256 is `c773736bc5ef4fa22d61d56b92cfbb08c6b49f47d8a976442168fc153d6bd6eb`. The scorer remains `b7f7a0673101ba90731b7c48c78d64888c1de9f8aa07bc6cb8002d9d36c70d05`, and the original corpus, scanner snapshot, provider settings and sequential compatibility profile are unchanged. Superseded preflight images and failed synthetic checks are retained separately; they are not live attempts.

The second run, `mortis-m5-20261107-pilot-02`, retained all **40** scheduled outcomes: **33 completed quality arms, one partial OCR security arm and six unsupported fault-fixture slots**. Every Mortis quality arm completed (**17/17**) with full validated coverage. All **142 HTTP requests returned 200**, with **698,864 reported total tokens** accounted for and no unknown-consumption events or ledger attempts. The overlapping Mortis ledger subtotal is **171,295**, not an additional charge. Both runs together retain **281 requests / 1,377,797 reported tokens**. Billing, cache-write usage, immutable underlying model version and provider region remain unknown; the scorer's conservative `unknown_consumption` blocker includes those missing fields and unsupported fixture slots, not a hidden loss of the observed request-token ledger.

Two fresh Sol 6.1 high agents independently reviewed the arm-blinded packet. The final judgments contain **24 confirmed matches, zero false positives and one uncertain baseline PR finding**. That finding describes a supported defect but points to the current docstring instead of its relevant import; the initial PR judgment is retained as v1, and the corrected create-only v2 is scored. No truth or scoring threshold changed. The security reviewer also recorded imprecise title/example wording in two otherwise supported matches. This is agent adjudication of visible synthetic examples, not human/expert acceptance.

| Workload / engine | Completed quality runs | Validated units | Confirmed frozen defects | Requests | Reported tokens |
| --- | --- | --- | --- | --- | --- |
| PR / OCR | 11/11 | 13/13 | 7/8, plus one uncertain finding | 80 | 438,637 |
| PR / Mortis | 11/11 | 13/13 | **8/8**, no pending findings | 34 | 119,628 |
| Security / OCR | 5/6, one partial assessment | 7/7 | 4/5 | 15 | 88,932 |
| Security / Mortis | 6/6 | 7/7 | **5/5**, no pending findings | 13 | 51,667 |

The partial OCR `sec-04` assessment remained uncertain and withheld its proposed finding; file coverage alone did not make the scan complete. It remains in quality/accounting results and is excluded from completed-pair latency. The uncertain PR citation remains pending, rather than being forced into either a true-positive or false-positive classification. Mortis has no observed high-severity loss in this rerun; critical defects and representative holdout strata are still absent.

| Completed-pair terminal comparison | Eligible pairs | Mortis/OCR p50 | Mortis/OCR p95 | Targets |
| --- | --- | --- | --- | --- |
| PR | 11 | **1.1132×** | **0.7260×** | ≤0.50× / ≤0.70× — **not met** |
| Security | 5 | **1.1727×** | **0.7261×** | ≤0.50× / ≤0.70× — **not met** |

PR median terminal time is **41.972 s Mortis / 37.704 s OCR**; security is **36.240 s / 30.904 s** in the eligible pairs. Paired identities have **zero mismatches**. Family-bootstrap one-sided 95% upper ratios remain inconclusive: PR p50 **2.1012×**, p95 **1.0006×**; security p50 **2.0419×**, p95 **1.3596×**. Token consumption is below the paired baseline, but the latency objectives still fail. Do not turn the lower observed tail ratios into an accepted speedup claim.

The former Mortis `pr-08` failure now completes analysis, assessment and summary with **1/1 coverage, three requests and 12,027 tokens**, without a repair in that live attempt. The blinded finding matches the previously missed defect. This verifies the rerun outcome; it does not reconstruct the original rejected response.

For `sec-05`, Mortis now makes **four calls / 18,127 tokens**, with no continuation request, versus six calls / 26,162 tokens in the first run. Its full terminal time is **79.606 s**, versus **115.827 s** previously; analysis takes **23.157 s**, assessment **53.008 s**, and Trivy **3.227 s**. Both analysis tasks persisted packed context; their resulting evidence already supplied the assessment sources, so those tasks needed no additional packing or expansion. Dependent assessments remain ordered. OCR's same case changed from **18.233 s** to **109.634 s**, demonstrating substantial run-to-run variation: these observations support the eliminated round trips, not a causal or statistically established latency improvement. Medians remain slower and both tail targets still fail.

There were **zero OOM kills**. Peak PR memory was **224,399,360 bytes**; security again reached its entire **1 GiB** ceiling with reclaim pressure, so capacity headroom is unproven. The 16 originally running containers were restored after **25 min 10.5 s** of measurement. The temporary provider credential file was removed, disposable evaluation resources were cleaned up and the saved shared connection remained unchanged. Afterward, local jobs drained, a private **47,829,852-byte** development backup was created, pending additive migrations reached `20261107_finding_category`, and nine affected local services restarted. Local API readiness is healthy and the database category width is verified as 50. This is a local development refresh, not a production deployment or Mortis enablement.

Second-run artifacts are retained separately under `data/engine-eval/m5-20261107-pilot-02/`: `live-02/results.json`, the recovered ledger, `live-manifest.json` (SHA-256 `a300bb20f8d7ccd1ec99c1828e7da5277ceb588e40bfb8b09078c2a344b7755e`), `preflight-02/`, `validation-smoke-03/`, `score-adjudicated.json`, `judgments-pr-v2.json`, `judgments-security.json`, pause/restoration and local-refresh records, plus the final artifact hash inventory. Superseded preflights, the initial PR judgment and synthetic failures remain retained. The private database backup stays outside this evaluation directory; no provider credential is retained. Normal task artifacts include safe validation diagnostics; the crash-only SQL allowlist still omits task-result diagnostics while retaining usage and attempt error codes.

Replay the final score without provider calls by changing the earlier command's paths to this directory and using `judgments-pr-v2.json`. All sources, scopes and scoring rules remain frozen; **M5/M6 acceptance remains closed**.

## Remaining acceptance work

- Continue latency work against independent development fixtures: the bounded source round trips are removed in the observed outlier, but median and p95 targets still fail. Investigate assessment/model-call time and the existing parallel pipeline without dropping required assessment, verification, summaries or source checks. Retain both completed pilots; no selective rerun, prompt tuning on their truth or weakened gate is permitted.
- Prepare and seal independent representative held-out cases before inspecting their outputs. Broader targets remain 100 PR and 40 security cases, historical and critical defects, large changes, other languages and independent expert adjudication.
- Exercise the default parallel review pipeline and retained historical verification workflows in the live comparison.
- Extend real process-kill coverage to full PR/security pipelines, prefork workers and mixed workloads. Live paid-provider kills, real-account permission/head/cancellation/publication failure recovery and automatic reclamation of quarantined admissions remain unverified.
- Compare graph-on/off and exact-revision graph gaps separately; graph enrichment remains opt-in.
- Execute the six retained reliability-fixture slots through an explicit fault-fixture adapter; separate background-process tests do not complete those slots.
- Measure repeated cold and warm runs and overlapping PR/security/graph workloads; a quiet single-case pilot does not establish combined capacity.
- Record quality, reliability and performance gates without weakening thresholds or treating missing coverage/accounting as success. M6 remains closed until acceptance.
