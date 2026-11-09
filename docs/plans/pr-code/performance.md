# PR Code navigation performance

Status: **implemented and verified locally**, 2026-10-29. Requested after slow Code, files, Diff and Dependencies navigation; authorized by the user's “go”. All implementation checkpoints below are complete. This follows [PR Code A–D](plan.md), not graph Phase 5 or generated tours. No commit, push, production deployment, review, indexing or model operation was started by this task.

The diagnosis below records the **pre-change baseline**. Current measurements and remaining limits are in [Delivered results](#delivered-results).

## Diagnosis

The pre-change implementation was functionally usable but repeatedly waited on GitHub authorization and discarded useful browser results. This is a request-flow problem before it is a parser, database, rendering or server-capacity problem. The earlier delivery's functional passes did not establish acceptable interaction latency.

Evidence: [sanitized measurements](../../evidence/pr-code-performance-2026-10-29.json). The investigation used the existing signed-in local browser on Code Mortis PR #34 (`frontend/src/inbox.tsx`) and disposable instrumentation inside the API container. It did not patch or restart the running application, disable access checks, change opt-ins or issue review/index/model operations.

### Measured baseline

| Operation | Baseline warm median | Disposable connection-pool median | Samples per mode |
| --- | ---: | ---: | ---: |
| Code overview | 1,188 ms | 893 ms | 3 |
| File diff | 1,201 ms | 894 ms | 3 |
| Dependencies | 1,281 ms | 1,007 ms | 3 |
| Source | 1,248 ms | 916 ms | 3 |

- **95.2%** of the baseline warm workflow time was inside the two `authorize_repo(..., fresh=True)` calls. Each performs a GitHub repository-permission request; a cached immutable diff does not remove those checks. The extra non-fresh `pr_access` check was usually a short cache hit in this sample, but can also request GitHub on expiry.
- Diff parsing: **0.19 ms median**. Saved finding lookup/checks: **19.01 ms**. Local graph reading: **47.03 ms**. Retained blob reading: **4.55 ms**. SQL execution per warm operation was roughly **10–45 ms median**. These stages are nested; do not sum all stage timings as independent costs.
- Reopening Code with Dependencies selected took **3,899 ms** from the first Code request start to the final dependency response. The completed requests were sequential: Code overview **1,347 ms**, graph overview **1,217 ms**, dependency query **1,302 ms**. Their payloads were only **927 / 2,616 / 2,830 bytes**. This was already a warm server comparison, not an initial clone or index.
- A cold workflow Code overview took **2,766 ms**; then a cold graph overview took **2,985 ms**. Each independently minted its repository read token and fetched the **same GitHub comparison**. The warmed graph overview dropped to **1,233 ms**. The diff and graph metadata LRUs currently do not share this work.
- In a 60-second browser sample, the idle Code/Dependencies page made **41 API requests**: 12 PR details, 12 hidden findings, 11 repository/sidebar refreshes, and two each for Code overview, graph overview and graph query. Request durations add to the configured intervals; this is an observed count, not a fixed cadence guarantee. Some competed for the existing permission-refresh lock.
- The running stack had 14 services, a 4.869 GiB Docker limit, API RAM around 293 MiB and a sampled API CPU of 17.2%. This is not an eight-GB capacity test, but it gives no basis for prescribing more RAM as the first fix.

### What the A/B test establishes

The baseline GitHub adapter called top-level `httpx.request` and `httpx.stream`, which recreate connections. Reusing one `httpx.Client` in the disposable probe reduced warm medians by about **21–26%**, while keeping the existing fresh permission gates. Switching back to the baseline returned operations to about 1.24–1.48 seconds. Pooling helps but leaves about 0.9–1.0 seconds per request, so it cannot alone fix a three-request waterfall or repeated visits.

The probe was small and ran alongside the existing browser/scheduler. One pooled dependency sample shared an in-flight permission refresh. Eight background `sync_summary` jobs appeared during the probe; no graph/index/review/model-generation job delta was observed. The probe measures workflow time, not full HTTP/UI latency, and does not establish production percentiles. That disposable experiment did not change the app; the delivered implementation is recorded below.

## Baseline source map

| Concern | Baseline source |
| --- | --- |
| Recreated HTTP connections and five-second permission cache | [integrations/github.py](../../../backend/code_mortis/integrations/github.py): `request`, `permission_request`, `authorize_repo`. `fresh=True` deliberately bypasses a prior completed success; only a refresh completed since a caller began waiting is shared. |
| Two fresh gates | [code_graph/explorer.py](../../../backend/code_mortis/code_graph/explorer.py): `context` and `finish`; [service.py](../../../backend/code_mortis/code_graph/service.py): `authorize`. |
| Duplicate compare caches | [reviews/code.py](../../../backend/code_mortis/reviews/code.py): `comparison` caches four patch-bearing comparisons; explorer `comparison_metadata` caches 32 path-only results from another upstream request. |
| Sequential browser requests and zero retention | [pr-code.tsx](../../../frontend/src/components/pr-code.tsx), [code-graph-explorer.tsx](../../../frontend/src/components/code-graph-explorer.tsx): `staleTime: 0`, `gcTime: 0`, query ownership under unmounting file/tab content, overview prerequisites before graph content. |
| Hidden polling | [reviews.tsx](../../../frontend/src/reviews.tsx): PR details and findings every five seconds regardless of selected PR tab; [app-sidebar.tsx](../../../frontend/src/components/app-sidebar.tsx) repository polling through [api.ts](../../../frontend/src/api.ts). |
| Extra refetch behavior | [main.tsx](../../../frontend/src/main.tsx): default window-focus refetch and development StrictMode. Zero-byte aborted requests were observed; they are not counted as completed transfers, but cancellation does not prove a synchronous backend task stopped. |
| Shared lifecycle/blast radius | GitHub adapter is also used by workers, token routes and MCP. Existing MCP installation owns application lifespan integration. Transport ownership must fit those entry points rather than replacing their lifecycle wholesale. |

## Decisions and boundaries

1. Preserve fresh GitHub authorization at admission and publication for every new backend content response, including bundled responses. Keep local session/membership/repository identity, PR SHA, exclusion, graph opt-in and final credential checks. **Do not make it fast by silently changing `fresh=True` to cached authorization, increasing the permission TTL or removing the final check.**
2. Reuse already-authorized content only in a small memory cache owned by the current mounted Code workspace. No localStorage, IndexedDB, service worker or cross-user sharing. Preserve `no-store` HTTP responses. This deliberately changes per-file/view eviction while preserving eviction on leaving Code, PR/account change and access loss.
3. Keep the current compact UI, shared combobox, file rows, aligned headers, focus behavior and exact-revision source. No redesign, tree widget, new dependency, embeddings, extra service or worker.
4. First eliminate duplicated network work and request dependencies. Optimize queries, credential scanning or rendering only if new measurements show they dominate after those changes. Do not add database indexes or virtualize 2,000 rows based on speculation.
5. Transport improvement and result reuse are different: pooled connections still perform fresh authorization; cached browser results are data previously returned to the same mounted workspace. A metadata refresh must never extend an old source result's lifetime without actually revalidating that result.

## Implementation order

### A. Baseline and reproducible cases

- [x] Measure the actual local page, warm/cold workflow stages, polling, duplicate comparison acquisition and disposable transport A/B. Preserve sanitized evidence and its limitations.
- [x] Add a small repeatable diagnostic for the final before/after comparison, reporting route/stage duration, GitHub call counts, cache hits, response bytes and click-to-content timing. Keep source, tokens, headers and credential-bearing URLs out of diagnostics. Instrument locally or behind the existing development/telemetry boundary; do not add production-sensitive debug responses.

### B. Reuse GitHub HTTP connections

- [x] Use a bounded, process-owned HTTPX client/transport for the GitHub API's normal and streamed request paths. Preserve timeouts, decoded byte cap, redirects policy, retry/rate-limit mapping, ETags and sanitized errors. Start with keepalive capacity appropriate to current concurrency, not an unbounded pool.
- [x] Pass tokens and request headers per request; no identity-specific default auth or mutable shared headers. Verify cookie/auth isolation across users and installations. Create clients after worker fork or associate them with the owning PID; close them through existing API/MCP and worker lifecycle hooks. Do not introduce a client created before Celery forks or globally patch HTTPX.
- [x] Run existing GitHub/permission/token/MCP checks plus focused connection-reuse, concurrent identity isolation, fork/lifecycle, streaming, timeout and rate-limit cases. Repeat the A/B conditions before claiming a speedup. Restart only affected services after draining work when implementation reaches local acceptance.

### C. Fetch the current screen in one authorized response

- [x] Narrowly share immutable, validated comparison acquisition between the browser diff and graph workflows. Use the existing decoded byte/inventory limits; keep fresh access/exclusion checks outside the cache. Preserve rename meaning for both consumers. One repository/base/head cache miss must not cause two token exchanges and two compare downloads when opening related views.
- [x] Add/extend the browser Code bootstrap contract so the visible file inventory, saved group metadata, exact comparison identity and **one requested initial file view** can arrive together. Resolve the requested file on the server, including default selection, query/group filters, legacy graph paths and base revisions. A graph-disabled/unavailable initial graph view returns its truthful state while the file inventory remains usable. No automatic indexing.
- [x] Return the bounded authorized path inventory once for local search, groups and ten-row pagination instead of reauthorizing an identical inventory for each keystroke/group change. Maximum 300 files and a 2 MiB metadata envelope; deduplicate group membership and avoid repeating long paths in multiple group payloads. If either bound is reached, expose the applicable limit explicitly and search only returned paths. Keep summary revision/policy checks and provenance. The initial file payload keeps its current independent limits.
- [x] Subsequent file/view reads return the requested content plus the status/configuration needed to display it. Do not require a separate graph-overview request before a graph query whose response already describes readiness. Prefer extending browser contracts and reusing local reader functions; keep existing MCP routes/scopes and graph mutations unchanged.
- [x] Authorize the assembled response before and after the whole bounded read. Recheck PR/repository/policy identity at the final boundary and apply full-source credential checks before slicing. Merge contracts without nesting public readers that each repeat both remote authorization gates. Regenerate OpenAPI/types and retain legacy URL behavior.

### D. Keep navigation fast within the current Code workspace

- [x] Move data ownership above the file/view panels that unmount. Use the existing TanStack cache, with a current-PR memory budget of **5 MiB / at most 10 content entries**, rather than a second cache framework. Retain only the currently mounted Code workspace; purge on leaving it. Oversized individual responses remain displayable but are not retained for reuse if they exceed the budget.
- [x] Key data by account/session context, repository/PR, base/head/merge base, policy, path, view, graph snapshot/extractor identity, offsets and source range as applicable. Retain a successful response for at most **30 seconds from its own validated fetch**, not 30 seconds from its last visit. A cached revisit must show no loading flash. Expired data must revalidate before redisplay; a metadata heartbeat alone cannot renew old source.
- [x] Centralize Code access/revision/status refresh rather than polling overview, graph overview and file independently. Refresh the active content on its established maximum age; revalidate promptly on focus after absence. Use the active response to update shared status. On 401/403/409, logout, changed PR/policy or graph disable, cancel pending reads and remove all affected cached source, including hidden panels and findings. Guard against late responses restoring cleared content. Keep network-failure behavior explicit and do not introduce offline code access.
- [x] Stop detailed findings polling while Code/history is active without losing top-level tab counts or finding-sheet freshness. Reuse lightweight counts or make their lightweight contract explicit. Slow or suspend sidebar/repository polling when idle; retain prompt updates for actual review actions, active runs, grants, repository configuration and manual refresh. Preserve live behavior of neighboring screens.
- [x] Add at most one intent-based prefetch for the next hovered/focused file or view, after a short dwell, using the same bounded cache and authorization. No page-wide source preloading, no prefetch before access is established, no poll per retained entry. Cancel obsolete prefetches and skip hidden/offline contexts. Keep an uncached file's header and controls responsive while its exact content loads; never show another file's source under the new heading.

### E. Validate the result, not just the tests

- [x] Backend cases: fresh admission/publication access loss, exclusions and renamed paths, current-user isolation, concurrent requests, policy/PR changes during assembly, credential material outside a source slice, cache eviction, comparison cap, graph disable, lifecycle and timeout behavior. Ensure reads/prefetch do not enqueue review, graph or generation work.
- [x] Browser cases: Code entry, first graph view, file A→B→A, Diff→Dependencies→Definitions→Source→Diff, search/group/pagination, back/reload, legacy base links, logout and access loss while data is cached, policy refresh, expiry during inactivity and late response races. Mouse/keyboard/touch, both themes and 390/1024/1440px must retain the implemented Impeccable layout and focus behavior.
- [x] Measure at least 20 warm interactions for each primary content/navigation scenario plus an explicitly cold comparison acquisition against the authenticated local app. Search/group/pagination and safety cases also have deterministic browser coverage; they are not each a separate 20-sample live benchmark. Separate dev StrictMode from a production frontend build with a real backend; keep mocks for deterministic regressions/render stress. Record medians, p95, GitHub calls, payloads and before/after idle requests under comparable network/background load.
- [x] Run focused backend/frontend regression checks, Ruff, OpenAPI/types and build; inspect the real UI. Record remaining warning/boundary conditions, safely reload affected services and verify records/grants. Update PR Code operations/acceptance and Graphify only for delivered behavior. No commit, push, hosted CI invocation or deployment is implied by this plan.

## Acceptance targets

These guided implementation. The content, cache, request-count and idle targets have local measurements below; local inventory controls have request-flow/regression coverage, not a separate live latency percentile. These are not production SLOs.

| Interaction | Target |
| --- | --- |
| Revisit a retained file/view within its 30-second validity | Click-to-content p95 **≤100 ms**; zero blocking network requests and no loading flash. |
| Search, group selection and pagination within a validated inventory | Local update **≤100 ms after the existing search debounce**, without an additional permission round trip per interaction. |
| First uncached view, with comparison already cached server-side | **One content response**, normally two fresh remote checks within it; aim local p95 **≤1.2 seconds**, measured under comparable conditions. No metadata→status→content waterfall. |
| Code entry or first graph view | One Code bootstrap with the initial requested view, not three sequential Code/graph calls. If comparison acquisition is cold, report it separately and show usable navigation immediately when its response arrives. |
| Idle Code page on a terminal PR | Target **≤6 periodic API calls/minute** for its visible workspace/sidebar, excluding user input and active operations. No hidden detailed-findings polling. |
| Memory and safety | Current-PR cache ≤5 MiB/10 entries; no persistence/cross-account reuse; bounded expiry/invalidation tests pass; existing authorization and credential boundaries remain. |

Do not promise instant first reads over a remote permission service. The measured connection pool still needs roughly 0.9 seconds per warm workflow. If the targets remain unattainable, publish the new breakdown before proposing a separate, explicit permission-lease/revocation-window change. Such a security-contract change is **not part of this plan**.

## Delivered results

The implementation uses a lazy process-owned GitHub HTTP pool, one shared bounded comparison cache, and browser-session-only POST `/pulls/{id}/code/workspace`. One response returns the current bounded inventory, saved group membership, exact identities and requested view. Both fresh authorization boundaries remain. Legacy reads and MCP authorization are unchanged.

`frontend/src/lib/use-pr-code.ts` owns one TanStack cache for the mounted Code workspace, with a 30-second absolute age from each validated response and a 5 MiB / 10-content-entry retention budget. A single oversized active result can be displayed but is evicted on navigation. Revalidation refreshes the visible view; hidden views do not poll. One 200ms hover/focus intent may prefetch. Account/session changes, leaving Code, access/revision errors and offline events purge retained data. Local inventory filtering and pagination keep controls usable while any newly selected content loads. No persistent browser storage is used.

Detailed findings stop polling while Code/history is selected. PR metadata supplies tab counts; open PRs or active reviews retain five-second updates, terminal idle PRs use 30 seconds, and the sidebar uses five seconds for known active work or 60 seconds while idle. Existing mutation invalidation remains on the application query client.

### Real local measurements

Sources: [backend samples](../../evidence/pr-code-performance-after-2026-10-29.json), [144 browser clicks](../../evidence/pr-code-browser-after-2026-10-29.json), and [derived statistics, idle requests and restart hashes](../../evidence/pr-code-performance-acceptance-2026-10-29.json). Browser clicks used a temporary production frontend build on the existing local origin with the real authenticated backend. Normal Vite HMR was restored and the real Dependencies screen was verified afterward.

| Scenario | Samples | Median | p95 | Request behavior |
| --- | ---: | ---: | ---: | --- |
| Reopen Code with Dependencies | 20 | 967 ms | 1,188 ms | One workspace response per entry; baseline was one 3,899ms trace with three sequential responses. |
| Retained, still-valid view switch | 72 | 25 ms | 28 ms | No blocking content request. |
| All repeated view switches, including expiry | 80 | 26 ms | 913 ms | Eight revalidations; absolute expiry was not hidden or extended. |
| Retained, still-valid file switch | 35 | 29 ms | 31 ms | No blocking content request. |
| All repeated file switches | 40 | 29 ms | 930 ms | Five initial/expired reads. |
| Warm backend Diff | 20 | 894 ms | 980 ms | Two fresh gates per workspace response. |
| Warm backend Dependencies | 20 | 917 ms | 985 ms | Two fresh gates per workspace response. |
| Warm backend Definitions | 20 | 902 ms | 944 ms | Two fresh gates per workspace response. |
| Warm backend Source | 20 | 900 ms | 958 ms | Two fresh gates per workspace response. |

The cold comparison acquisition took **2,862 ms** and fetched one token and comparison. The raw capture labels all four initial views “cold”, but only the first Diff acquired the comparison; the repeatable probe now distinguishes the other initial views. Warm probe responses were about 10–24 KiB. Its job-count delta was empty.

A separate idle terminal-PR window made **5 API calls in 60 seconds**, down from 41: two workspace revalidations, two PR metadata reads and one sidebar repository refresh. No findings query ran. The idle refreshes happened to take about 1.45 seconds, illustrating network variability; the 1.2-second target is a sampled local navigation result, not a guarantee.

The baseline entry is one trace, not a matched distribution. These measurements do not establish production percentiles or eight-GB capacity. Search/group/page changes are local inventory operations; a change that selects a different uncached file still requests that file's content. First or expired content remains dependent on live GitHub authorization. No permission lease or longer permission TTL was introduced.

### Validation and reload

- Backend focused run: **209 passed**. Broader GitHub/permission/token/MCP/Code regressions: **434 passed, one failed** on the browser-route policy inventory; the three session-only Code routes were added and the final affected selection passed **144 checks**. Two direct PAT/MCP provenance cases were additionally strengthened for the workspace reader and passed. Counts overlap; they are not additive or full-suite evidence.
- Code browser selection: **60 passed** across desktop/mobile, including cache age, late cancellation, account changes, offline purge, policy/access errors, count/byte eviction, source paging, groups and diff evidence. Neighbor selection: **962 passed, 10 failed** initially; open-PR polling and count fixtures were corrected and all **10 failed cases passed** on rerun. Existing screenshot cases cover light/dark, 390/1024/1440px, keyboard/touch and the unchanged shared UI. Real dark desktop and mocked light mobile renders were inspected.
- Ruff, OpenAPI export/type regeneration, TypeScript and production build passed. The existing Vite chunk-size warning and Python test deprecations remain; no dependency was added. Mocked access-loss/account-switch tests do not mean a real GitHub grant was revoked.
- Scheduler/workers were drained, then the API, six affected workers and scheduler were reloaded. **14 services** returned running with configured health checks healthy. Full row hashes matched across the drained restart for **52 PRs, 180 review runs, one graph configuration, 11 graph snapshots and four MCP grants**, with zero active jobs on both sides. Existing synchronization before the drain was allowed to finish. No schema migration or data/grant recreation was needed.
- The repeatable backend probe runs inside the existing API environment and emits timings, counts and sizes only. Browser evidence contains timings/bytes only; no raw source, cookies, headers or tokens are saved. Local Graphify is refreshed separately from the product's graph index.

## Resume and deferred work

A–E are complete locally. Start a later session with this report and `scripts/check_pr_code_performance.py`; do not repeat implementation or authorize new workloads from checked boxes. Graph Phase 5 and production rollout remain separate.

Deferred unless evidence changes: new database indexes, a graph database/vector store, larger servers, a new worker, broad repository prefetch, virtualized diff/editor dependencies, WebSockets, permission TTL changes, generated summaries/tours, model evaluation and release work.

Primary references checked for this investigation: [HTTPX connection pooling](https://www.python-httpx.org/advanced/clients/), [TanStack request waterfalls](https://tanstack.com/query/latest/docs/framework/react/guides/request-waterfalls), and [TanStack stale/inactive query defaults](https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults). These support the mechanisms; the measured Code Mortis figures above come from the local evidence file.
