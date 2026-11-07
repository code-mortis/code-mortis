# M2 — Shared-provider transports and admission

Implementation and evidence recorded November 7, 2026. This implements the transport milestone in [ENGINE_PLAN.md](ENGINE_PLAN.md); it does not enable Mortis PR analysis or replace the security workflow. Those remain M3/M4. No upstream code or prompts were copied.

## Implemented

| Area | Implementation and boundary |
| --- | --- |
| Protocols | Direct asynchronous OpenAI-compatible Chat, OpenAI Responses, Anthropic Messages and Anthropic Bedrock InvokeModel. One explicitly selected shared connection/model; no protocol, model or provider fallback. |
| Transport | A reusable HTTP pool per run/event loop, at most eight connections, explicit credentials per request, no environment proxy/auth inheritance or redirects. Request/response byte limits and wall deadlines bound inference. No subprocess, temporary provider configuration or global filesystem lock on the Mortis transport path. |
| Options | Existing endpoint gates, encrypted API keys/headers, custom auth header, timeout, prompt ceiling, safe custom/dotted body options, strict-tool flags, reasoning settings, AWS profile/region and configured retry eligibility. Context, selected model, stream mode and output budgets cannot be overridden by custom body settings. |
| Tools and schemas | Original provider-neutral request/result contracts; local JSON-schema validation, complete tool-call/result linkage and required reasoning-state replay. The transport returns requested tools; it never executes tools or repository instructions. Workload evidence validation and redaction are mandatory caller responsibilities. |
| Bedrock | Worker-only AWS credential chain, refreshable credentials and SigV4 signatures; explicitly configured bearer credentials also supported. SDK session caching avoids repeated setup; HTTP inference remains asynchronous. No AWS credentials reach the native runner. |
| Errors and usage | Refusals, invalid/truncated/partial responses, unexpected streams, authentication/quota failures, rate limits, transport errors and cancellation remain explicit failures. Valid usage/request IDs survive rejected results. Missing/inconsistent usage stays unknown. |
| Durable execution | `llm.execute_task` binds the request hash to an immutable plan, requires fresh external authorization and a workload result validator, reserves an entire request, renews ownership, and rejects stale evidence. A supplied pool is reused for concurrent tasks. |
| Admission | PostgreSQL connection-row serialization atomically combines capacity admission and stage reservation. Ceiling eight across new-engine stages; immutable per-run concurrency can only be tightened. Runs take turns, saturated runs yield, and a waiting whole-capacity OCR request eventually drains the connection. |
| Legacy coexistence | Every production `ocr.configuration` scope reserves all eight slots until subprocess termination and cleanup. Offline adapter-only smokes explicitly use the private configuration helper; they do not claim database admission coverage. |
| Connection tests | The existing background job now uses the new engine for a two-request, validated tool round trip. It requires a saved shared connection, uses one pool/session and total request deadline, and fences success/failure/recovery against changed connection configuration and stale job attempts. Model discovery retains its existing API and metadata-only behavior. |
| Persistence | Additive `20261107_inference_admission` migration. Existing plans, attempts, findings and historical review/scan reports are retained. No public API schema or frontend changes. |

Main code: `schemas/inference.py`, `integrations/llm.py`, `integrations/llm_http.py`, `integrations/llm_bedrock.py`, `core/inference_admission.py`, and the existing analysis ledger. Direct dependencies are JSON Schema validation and botocore; `uv.lock` pins the resolved versions.

## Reliability and consumption rules

1. At most three automatic attempts per gateway invocation, only following a known rejection/pre-dispatch failure with zero consumption. Respect configured retry codes and provider backoff within the original deadline. Authentication, quota exhaustion, ambiguous failures and unknown usage never trigger automatic retries or fallback.
2. Every retry gets a fresh atomic capacity/stage reservation and retains the previous attempt. Analysis, assessment, verification, summary and security budgets remain independent. Completed results do not silently resolve findings.
3. Input reservations conservatively use encoded bytes plus framing allowance; output reservations use an explicit wire limit. These are admission estimates, not an exact provider tokenizer or guaranteed invoice ceiling. Reported actual usage settles the reservation; unknown usage retains it and blocks further automatic spending in that stage until explicitly acknowledged.
4. Cancellation awaits local HTTP teardown before releasing capacity. A disconnected HTTP client cannot prove the provider stopped remote computation, so ambiguous consumption remains unknown. A worker that disappears leaves its active admission reserved even after lease expiry. Timer expiry alone never makes another request safe.
5. Recovery requires establishing that the old worker/request is stopped, then calling `core.inference_admission.release_connection(admission_id)`. This releases capacity only. Unknown budget charges require the separate explicit ledger acknowledgment/retry operation. There is no automatic recovery UI in M2.
6. The gateway requires an authorization callback before admission, before dispatch and before result acceptance. M3/M4 must connect the existing GitHub/PAT/MCP authorization functions and workload validators; the callback contract and local ownership checks do not establish real GitHub integration by themselves.

## Verification

Tests use isolated local Docker PostgreSQL/Redis (`mortis_test`, ports 55439/56389). HTTP fixtures and SDK requests are simulated unless explicitly labeled live below. Database test processes run sequentially because the shared test fixture truncates tables.

| Check | Evidence |
| --- | --- |
| Admission, foundation and gateway | 77 cases passed in the focused run: real PostgreSQL transactions, sixteen simultaneous claimants, eight concurrent HTTP fixtures across four runs, per-run limits, all five budgets, fair/weighted admission, unknown usage, lock-wait expiry, authorization, stale attempts and actual HTTP close before release. |
| HTTP protocols and connection jobs | Initial combined run: 102 passed, covering 84 HTTP cases and 18 connection-test/job cases; subsequent final transport hardening is included in the final checks below. |
| Bedrock | Initial 27 isolated SDK/HTTP cases passed; exact URL/signature compared with a real SDK request intercepted before transmission, credential refresh/cache, partitions, bearer, tool/schema/usage behavior and cancellation. Final option-hardening tests are included below. |
| OCR coexistence/regression | 221 cases passed. A real child process is killed and configuration removed before capacity release. Existing review/transport/limits behavior is retained. |
| Pinned OCR Docker smoke | Passed with current backend mounted read-only, no network and no credentials. Real pinned OCR used deterministic local protocol servers for reviews, assessments, verification, rules/exclusions, redaction, cancellation and tool delivery. Admission is tested separately in PostgreSQL. Existing Git 2.39.5 versus recommended 2.41 warning remains. |
| Broad backend regression | **3,267 passed**, one migration-fixture failure and 21 setup errors, 392.90 s. The graph history fixture mistakenly included the new admission table among historical tables; corrected its table selection. The isolated PostgreSQL tmpfs filled at the end of the run; only the disposable test PostgreSQL container was recreated. |
| Fresh final regression | **536 passed**, 38.59 s, after all fixes: all four transports, connection-test Jobs, gateway, admission, foundation, graph storage/migrations and verification. Includes every failed/errored case from the broad run plus final immutable-request and repeated-cancellation regressions. No failures remain in this selection. |
| Static/API checks | Ruff and `git diff --check` passed. OpenAPI export and `bun run api:types` passed with no generated API diff. |

These selections overlap; do not add their counts as unique tests. No frontend behavior changed, so browser/visual suites are not new M2 evidence. Trivy advisory freshness and complete dependency scanning remain the M0/M1 gaps; M2 does not claim to verify them.

Final checks include a detached validated request snapshot before asynchronous authorization, fresh connection-test fencing at every request, and cleanup that survives repeated cancellation while waiting for a database claim or closing HTTP. The broader process loaded code before these final hardening changes; the fresh 536-case run covers their owning modules and callers. A second uninterrupted full-suite run was not performed.

Graphify's local developer index was refreshed for code and the changed plan/evidence documents. Its existing `settings.tsx:480` and zero-node JSON warnings are not new production graph failures. M3 still owns integration of the production code graph with Mortis review context.

## Actual-provider check: OpenCode Go

The owner identified saved **Opencode Go**, protocol **`openai-responses`**, model **`muse-spark-1.3-contributor`**, and previously selected **uncapped aggregate consumption** and the current local Docker environment. The saved endpoint was `https://opencode.ai/zen/go/v1`, with `prompt_token_ceiling=500000`. No saved endpoint, model, key or option was changed. The pilot profile's documented `/inference/go/openai/v1` address differs; bind the manifest to the actual selected endpoint before M5 rather than silently replacing it.

The check ran inside the local review-worker container, reading the encrypted connection only there. No active local Jobs were observed at preflight. Only original synthetic probe input was sent; no repository source, pilot truth or credentials appeared in output. This was a direct transport capability check, not a durable production workflow or resource-controlled benchmark. The full pilot remains unrun.

| Request | Observed result | Reported token usage |
| --- | --- | --- |
| Initial named-tool probe, twice including diagnostic reproduction | HTTP 400: this model accepts only `tool_choice: auto`; named, required and none are unsupported. No automatic retry/fallback. Usage was not reported. | Unknown/unreported; no invoice claim. |
| Automatic tool call | Passed, exactly one schema-validated `mortis_probe` call. | 546 input + 314 output = 860; 242 reasoning tokens included in output. |
| Tool-result acknowledgment | Passed, exact synthetic result returned with reasoning replay and same session/pool. | 916 input + 257 output = 1,173; 497 cache-read tokens within input; 242 reasoning tokens within output. |
| Structured JSON result | Passed local schema validation and expected value. | 16 input + 167 output = 183; 153 reasoning tokens within output. |

The successful tool round trip took 5.155 seconds total; the structured response took 1.277 seconds. These are single smoke observations under existing local Docker conditions, **not latency distributions, quality results, speedup or SLO evidence**. Successful requests reported 2,216 total tokens. Go allowance usage, actual dollars, remaining allowance and stable underlying model revision were not independently verified. No other real provider or AWS account was exercised.

The connection probe now requests automatic tool selection and validates the returned call/result. Callers explicitly requesting an unsupported tool choice still receive a failure; the transport never downgrades their request silently. Likewise, a provider lacking requested schema/effort features fails explicitly rather than silently discarding them.

## Remaining gates before M3 and deployment

- M3 may proceed with the tested internal transport/gateway. Mortis remains unavailable as a PR/security engine until the later workload milestones implement selection, trusted context, graph integration, evidence validation, coverage and publication. Existing OCR review/scanner behavior remains selected.
- Test actual OpenAI Chat/Responses, Anthropic and Bedrock deployments, custom-header endpoints and meaningful failure scenarios before claiming universal live compatibility. Mock fixtures establish wire contracts, not account/model support.
- DNS validation and AWS credential discovery run outside the event loop. Cancellation prevents subsequent inference, but the underlying resolver/SDK thread can finish later, and `asyncio.run` executor shutdown can outlast an asynchronous deadline. OS/SDK credential-resolution bounds require deployment validation.
- Stop/drain old workers before migrating/restarting: old binaries do not participate in new admission. Apply the additive migration and rebuild images with locked dependencies before starting the new workers. M2 did not migrate/restart development services or deploy production; the live probe loaded source in the existing container only.
- Active worker-loss admissions require the explicit recovery procedure above. This favors bounded spending over automatic availability and needs an operational drill before rollout.
- The initial connection ceiling is eight. The pilot profile selected four: enforce that lower profile ceiling and all CPU/RAM/PID/deadline/scanner limits in the M5 harness before collecting performance results. Uncapped spending does not remove concurrency, deadline or retry bounds.
- Preserve the M0/M1 gates: shared-only cutover and historical data, fresh scanner databases, complete pilot manifest/version pins, actual-provider coverage, expanded adjudicated quality corpus and measured p95/precision/recall.

Protocol references used to verify wire behavior: [OpenAI Chat](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), [Responses](https://developers.openai.com/api/reference/resources/responses/methods/create), [function calling](https://developers.openai.com/api/docs/guides/function-calling), [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create), [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [thinking replay](https://platform.claude.com/docs/en/build-with-claude/thinking), [DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/), [Bedrock InvokeModel](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModel.html), [AWS API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html), [AWS credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html) and [Bedrock structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html).
