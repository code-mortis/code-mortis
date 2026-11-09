# PR Code: high-priority review workflow

Status: **complete locally**, 2026-10-29. Implementation was authorized after planning. During delivery the user explicitly replaced personal Reviewed checkboxes with an automatic check on files already reviewed by Code Mortis, and subsequently reverted the viewport-filling height experiment. These corrections supersede the original manual-progress design.

## Outcome and scope

1. Automatically show **✓** on files with validated review coverage at the current PR base/head. No checkbox, toggle, manual marking, personal-progress endpoint or new persistence.
2. Navigate the complete filtered changed-file sequence using **Previous file / Next file** and scoped **Alt+Up / Alt+Down**.
3. Show current open-finding indicators, filter files with findings and open their saved evidence directly.
4. Preserve the original diff height: natural content height capped at 70vh, with internal scrolling. The viewport-filling experiment was reverted at the user's request.

The reference was [Greptile Noodle PR #285](https://app.greptile.com/noodle/github/wilfredinni/noodle/-/pull-2/285), inspected on 2026-10-29. It inspired navigation and indicators; Code Mortis's final semantics follow the user's corrections and its validated coverage invariant.

Read [AGENTS.md](../../../AGENTS.md), [Product](../../product.md), [Design system](../../design.md), [PR Code delivery](../../pr-code.md) and the [performance plan](performance.md) before changing this surface. Use Ponytail and Impeccable and preserve existing tokens, comboboxes, filters and evidence sheets. No commit, push, production deployment, model/review/index workload or permission changes are included.

## Decisions and implementation

### Automatic review checks

`reviews/code.py:reviewed_paths` reads the latest saved coverage manifest for the exact PR base/head. It validates selected identifiers, canonical paths and the complete nonoverlapping partition of completed/reused/failed/waived results. A file earns a check only when every selected item for that path completed or was reused. Run-level `completed` status, a model's own claim and the absence of findings do not establish file coverage. A partial run can still contain individually completed files.

The coverage projection is bounded to 2 MiB in SQL before materialization. Missing, malformed, contradictory, oversized or earlier-revision coverage yields no check. Current exclusions are reapplied to the returned file inventory, so hidden files never appear or affect the displayed count. A later exclusion change does not erase the historical fact that a still-visible file was reviewed at the same revision. Summary descriptions retain their separate, stricter saved-policy checks.

File rows always display their automatic check when eligible, with a tooltip explaining that reviewed does not mean free of findings. The toolbar count uses the entire authorized returned inventory, independent of filters. A limited inventory explicitly says “shown files.” The shared **Not reviewed** filter keeps files without confirmed current coverage. There is no manual action or automatic write on opening/scrolling/navigation.

The temporary personal-mark implementation, its PUT route and its new table/migration were removed after the user's correction. The final feature needs no schema upgrade. Existing reviews, findings, graph snapshots and MCP grants remain independent.

### File navigation and viewport

The selected-file heading holds compact Previous/Next actions and position, such as “3 of 12.” Navigation traverses all matching files across ten-row pages, respecting search, group, Not reviewed and With open findings. It preserves ordering, content view, revision and rename/removal mapping, resets source/definition offsets and occurrence selection, updates the URL and focuses the file heading. It never marks files or starts a review.

No wraparound: disable the unavailable boundary action. Hide the pair for zero/one matching file or a related graph file outside the sequence. **Alt+Up / Alt+Down** only operates inside Code and ignores editable fields, IME, additional modifiers, open overlays/comboboxes and repeated keys during loading. Prevent default only when a file changes. Tooltips show the bindings; touch targets remain usable.

The diff uses its original natural height capped at 70vh. There is no scroll/resize-driven height calculation. Scroll remains inside the code region; the existing 2,000-row/256 KiB patch bounds are unchanged.

### Finding indicators

Workspace metadata counts distinct finding IDs from immutable occurrence path/source SHA, matching ReviewRun base/head and current open status. Rename sides and current exclusions participate. Mutable Finding path/content and last-seen run IDs cannot establish an anchor. Historical/unknown evidence never becomes a current-file count; current out-of-hunk evidence can count but remains visibly unanchored when opened.

Inspection is bounded to **1,000 candidates**, plus one overflow sentinel. SQL withholds raw snapshots above **32 KiB** before Python materializes them. Evidence validation and credential-check context are shared across the batch; there are no per-file GitHub or per-occurrence secret queries. When bounds or validation prevent certainty, `complete: false` produces a lower bound such as `3+`, or `?`, never a misleading exact zero. The finding filter retains unknown files.

The row indicator is a sibling button beside file selection. Activating it selects Diff, refreshes evidence and opens the first eligible current open finding in the existing sheet. Inline anchors scroll into view; out-of-hunk placement remains explicit. Resolved/withheld races update metadata and show unavailable evidence when nothing eligible remains. Existing finding actions cancel older reads and invalidate workspace metadata; they do not imply review coverage.

### Contracts, ownership and performance

- Extend `schemas/pr_code.py:CodeWorkspace` with `file_states`, parallel to the bounded returned file inventory: `reviewed`, `open_count`, `complete`. Legacy overview/file readers remain compatible. Regenerate OpenAPI/types.
- Keep the existing browser-only POST workspace route, admission/publication authorization and source checks. No new route, PAT/MCP scope, grant, worker, service, dependency, embedding or model call.
- URL filters are `code_unreviewed=1` and `code_findings=1`. Reuse `pr-code.tsx`, `use-pr-code.ts`, FilterBar, Button, tooltips and Sheet. Filters/page navigation use the current inventory locally.
- Preserve the mounted cache's absolute 30-second source age, 5 MiB/10-entry bounds, at most one intent prefetch and purge on account, offline or access/revision changes. Mutable metadata does not extend retained source lifetime; no per-file polling.

## Delivery checkpoints

### A. Metadata and contracts

- [x] Inspect current callers, route policy, coverage and immutable evidence eligibility; add typed per-file metadata and filter URL keys.
- [x] Reuse saved validated coverage for automatic checks; remove the superseded manual-progress UI, endpoint, table and migration.
- [x] Keep metadata aligned when the 2 MiB envelope is trimmed; preserve browser-only authorization and regenerate API types.

### B. File navigation

- [x] Add Previous/Next, position and scoped shortcuts across the complete filtered inventory, including pages and boundary states.
- [x] Preserve views, exact revision, removed/renamed paths, back/reload, focus, cancellation and range resets; related graph neighbors have no sequence controls.
- [x] Restore the original 70vh maximum after feedback; retain mobile/desktop layout and contained overflow.

### C. Automatic reviewed indicators

- [x] Show passive checks and a bounded-inventory count from validated completed/reused file outcomes, without a checkbox or manual write.
- [x] Add Not reviewed through the shared filter; reject stale, incomplete, contradictory and oversized coverage and reapply current exclusions.
- [x] Verify opening, navigating and filtering do not change findings, review coverage or publication; preserve source-cache expiry and access-loss purge.

### D. Finding indicators

- [x] Batch bounded distinct-finding counts with current revision/rename/policy checks, credential-safe validation and explicit incomplete metadata.
- [x] Add accessible sibling indicators, With open findings filtering and direct saved-evidence navigation, including state-change races.
- [x] Test duplicates, historical/unknown evidence, withheld/oversized snapshots, 1,000-candidate bounds and count refresh after existing resolution actions.

### E. Verify and close

- [x] Run final selected isolated-DB/backend and browser regressions, route-policy checks, Ruff, generated types, build and diff checks.
- [x] Inspect the final real local UI and mocked light/dark desktop/intermediate/mobile states; verify automatic checks, absence of the checkbox and the diff's bottom alignment.
- [x] Repeat at least 20 real cached file switches and 20 uncached workspace reads plus a 60-second idle sample. Compare cached p95 ≤100ms, sampled warm-read p95 ≤1.2s and idle terminal-PR ≤6 calls/minute; report variability honestly.
- [x] Record the 300-file / 1,000-candidate / 2 MiB deterministic bounds and batch query count. Verify no new review/model/index workload.
- [x] Update delivery/operations/acceptance/design docs, refresh Graphify, restore normal HMR and verify local services and existing records/grants.

## Evidence and resume

Final corrected implementation: **158 selected backend tests passed** (`test_pr_code.py`, `test_code_graph_explorer.py`, `test_api_tokens.py`) and **76 Code browser tests passed** across desktop/mobile projects. A final focused rerun of the four finding-navigation/action cases passed after adding a URL guard against a late evidence response. Ruff, API type regeneration, production build and diff checks passed. Existing Vite chunk-size and Python deprecation warnings remain.

- Deterministic bound fixture: **300 files / 1,000 inspected 31.5 KiB candidates**, **0.936s**, **28 SQL statements**, **46,075 response bytes**, one batched occurrence query. Oversized raw evidence and coverage are withheld before materialization; forced 2 MiB envelope trimming retains file/metadata alignment. This measures bounded local work, not general eight-GB capacity.
- Final authorized backend probe: **20 warm uncached workspace reads**, five per Diff/Dependencies/Definitions/Source, **872.65ms median / 956.92ms p95**, maximum **1,134.34ms**. Initial cold Diff **2,405.31ms** includes acquisition. Two fresh GitHub authorization gates remain. The final probe's background job delta was **zero**; no model/review/index operation ran.
- Production frontend plus real local backend: **20 cached file switches**, **12.8ms median / 19.5ms p95**, **zero content requests**. A separate **60-second idle window made six API calls**: three workspace revalidations, two PR metadata reads and one repository refresh, with no hidden findings query. Idle workspace requests ranged about 0.87–1.36s; these are local network samples, not an SLO. This remains within the ≤100ms cached / ≤1.2s sampled warm-read / ≤6 idle calls acceptance budgets.
- Real local PR #34: **46 of 62 reviewed**, checks visible on eligible rows and **zero Reviewed checkboxes**. The recorded **922px** viewport / **501–906px** diff bounds describe the subsequently reverted height experiment. Its dedicated sizing test was removed; the original 70vh maximum is restored. Browser checks cover light/dark and 390/1024/1440px, filters, missing access, incomplete counts and existing source-age eviction.
- Local HMR is restored; all 14 local services are running with configured health checks healthy. The new table/route from the superseded manual prototype is absent, and Alembic remains at the pre-feature revision. Final Graphify refresh covers source and these delivery documents.

Reproducible timing command: run `scripts/check_pr_code_performance.py` inside the existing API environment with the authorized login, PR ID and path, using `--samples 5` for 20 warm reads plus four initial samples. Evidence contains only durations/counts/bounds, not source or credentials: [backend](../../evidence/pr-code-workflow-backend.json), [browser](../../evidence/pr-code-workflow-browser.json), [live UI bounds](../../evidence/pr-code-workflow-ui.json). Earlier manual-progress results are superseded.

The scheduler was drained before restarting the API and six affected workers. Before/after full hashes matched for **52 PRs, 180 runs, one graph configuration, 11 snapshots and four MCP grants**, with zero active jobs. Routine graph-configuration refreshes before the drain were not caused by this feature. The manual-progress prototype was subsequently retired locally; existing review history and grants were not recreated.

Deferred: personal/team manual checklists, folder trees, split diffs, generated tours/line explanations, agent handoff UI, GitHub reviewed-state sync, bulk marking, new “since last review” comparisons and graph Phase 5. No full-suite, hosted-CI, production or eight-GB capacity claim follows from local/mocked evidence.

Height revert verification: production build and **four focused desktop/mobile alignment and large-diff checks passed**. The real local browser reports no inline height and a **645.4px maximum at a 922px viewport**, restoring the original 70vh cap. The original 76-test result above preceded removal of the two viewport-filling cases.
