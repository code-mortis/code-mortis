# PR Code: implementation plan

Status: **implemented and verified locally**, 2026-10-29. This is the requested evolution of the browser Impact tab after code-graph Phase 4. Graph indexing, internal retrieval and Code MCP remain separate consumers; code-graph Phase 5 operational readiness remains open.

This document preserves the agreed scope, implementation order and completion evidence for future sessions. The user authorized implementation with “inicia” on 2026-10-29. This does not authorize model calls, indexing, reviews, commits, pushes or deployment. Read [AGENTS.md](../AGENTS.md), [PRODUCT.md](../PRODUCT.md), [DESIGN.md](../DESIGN.md), [architecture](architecture.md) and the [Phase 4 report](code-graph-phase4.md) before implementing.

## Outcome and reference

A reviewer opens a PR, understands its main changes, inspects the corresponding diff and saved findings, and follows related code when useful. The main task must work when the graph is disabled, missing, expired or still indexing, and for text formats without a structural parser.

The live reference inspected on 2026-10-27 was [Greptile Code for Noodle PR #285](https://app.greptile.com/noodle/github/wilfredinni/noodle/-/pull/285). Its 36 changed files were organized into five behavioral steps; short explanations navigated to diff locations, each step listed related implementation/test files, and Full diff supplied file-tree navigation and comment indicators. Review checkboxes were visible but not exercised. This establishes observed interaction, not Greptile's internal architecture, generation cost or review accuracy.

Use that task flow within Code Mortis's existing visual system. The initial delivery is **diff + existing summary groups + saved findings**, with graph navigation as an optional complement. A tour with newly generated explanations anchored to exact lines is deferred.

## Existing implementation to reuse

| Area | Current source and boundary |
| --- | --- |
| PR UI | [reviews.tsx](../frontend/src/reviews.tsx), [CodeGraphExplorer](../frontend/src/components/code-graph-explorer.tsx). Preserve PR actions, other tabs, finding sheets and return navigation. |
| Controls and rendering | Shared `FilterBar`, `SearchSelect`, `Status`, `Tooltip`, `Pagination` and [Code](../frontend/src/components/code.tsx). `CodeComparison` currently compares original/suggested snippets; it is not a complete PR-diff viewer. |
| Comparison | [code_graph/explorer.py](../backend/code_mortis/code_graph/explorer.py) authorizes exact saved PR base/head reads and validates immutable GitHub comparisons. Its metadata cache retains paths only, caps comparisons at 300 files and represents renames as removal/addition. Its overview currently stops when indexing is disabled. |
| Summary | [SummaryGroup / SummaryResult](../backend/code_mortis/schemas/common.py) and [reviews/summary.py](../backend/code_mortis/reviews/summary.py). Completed results already store titles, descriptions and exact inventory paths in `ReviewRun.summary`; they do not contain per-explanation line anchors. |
| Evidence | [models.py](../backend/code_mortis/models.py): `ReviewRun` records base/head; `Occurrence` records immutable snapshots; `Finding` records current resolution. The current PR findings search is a substring search over mutable finding fields, not an exact-path/revision occurrence lookup. |
| Graph | Existing browser graph query/index routes and bounded retrieval. Keep their repository opt-in, parser limits, source checks and independent MCP permissions. |

## Product and interaction decisions

- Rename the visible PR tab to **Code**. Default to **Diff** for the selected changed file, with additions/removals and original/new line numbers. Display the compared revisions compactly. Reading Code never starts indexing, summary generation or a review.
- Put changed-file search and selectable rows on the left, with the selected file and diff on the right. Reuse the existing joined rows and pagination first; a new tree widget is not required. Include change type and bounded additions/deletions where available.
- Offer **All changes** and the saved behavioral groups through the shared combobox. Selecting a group filters its files and shows its short stored description. Group membership can overlap; a file appears once in the current list. All changes remains available when summaries are absent, stale or incomplete. Unassigned files remain reachable there.
- Show only an exact-revision, policy-compatible completed summary. Opening a group navigates to files; do not fabricate explanation-to-line anchors. A missing summary leaves the diff usable without a prominent empty panel or an automatic model call.
- Show matching findings beside their diff locations and open the existing evidence sheet. Keep historical findings accessible with their original revision, rather than pinning them to unrelated current lines. Findings, Resolved and Dismissed tabs continue to own resolution/history workflows.
- Preserve Dependencies, Definitions and Source as secondary file views using the graph reader. Graph status and explicit indexing belong to those views. No graph-state banner or indexing action blocks the main diff.
- Follow the user's accepted Impeccable decisions: compact toolbar, shared comboboxes, badge tooltips for status explanations, no introductory heading, duplicate diagram or repeated selected-tab headings. Align Changed files and selected-file headers; align the file list with detail controls or the corresponding empty state. Keep the selected-file icon.
- Desktop uses the existing two-column layout. Narrow screens stack the same controls and content; selecting a file moves focus to its details without leaving keyboard users behind. Long paths wrap, code scrolls within its own region, and the whole page never scrolls horizontally. Check populated, unavailable and loading layouts, not only the happy path.
- Preserve existing `tab=impact` links as an alias for Code. Preserve existing `graph_*` path/page/revision state when opening those links; a legacy base selection must still open the merge-base source view rather than silently changing its meaning. New diff/group navigation gets explicit URL state. Other PR filters and return state remain untouched.

## Data and authorization contract

1. **Independent browser read.** Add the smallest PR-code workflow under `reviews/` and browser-session-only routes under the existing pulls router. Core diff/group/finding reads use current PR/repository authorization and work without the graph feature flag, graph opt-in, parser or retained graph objects. Reuse or narrowly extract existing comparison/authentication helpers; never weaken the graph reader to achieve this independence. Existing PAT and MCP grants gain no new access.
2. **Exact comparison.** Bind reads to repository identity, PR ID, saved base SHA, head SHA and server-resolved merge-base SHA. Render the PR comparison from merge base to head, not an arbitrary branch-tip comparison. Revalidate access, current exclusions and PR identity before returning content. A changed pair returns 409 and discards old content; access loss hides it. No substitution with another revision, latest default branch or an old cached source.
3. **Diff acquisition.** Extend the existing immutable GitHub comparison path to obtain and validate file patches. Preserve `previous_filename` for rename navigation when both paths are allowed. Validate either side of a rename against current exclusions. Patch absence alone does not prove a binary or unchanged file. Added/deleted files, renames, binary or unavailable patches and truncated comparisons need distinct truthful states. Use the existing GitHub link as recovery when content is not supplied; do not clone a repository or build a graph implicitly.
4. **Bounds and sensitive content.** Apply current exclusions before counts, paths, summaries or source reach the browser. Validate and credential-check the complete fetched patch before clipping what is displayed; withhold unsafe content. Set explicit upstream byte/time limits, per-file patch and rendered-line limits, and total inventory bounds in checkpoint A. Start from the existing 300-file comparison cap and ten-file pagination; reaching the cap remains visibly incomplete. Large/omitted content must never appear complete or unchanged. No arbitrary URL/ref fetch, repository code execution or credentials in logs. Use no-store responses and short-lived browser data; new navigation cancels obsolete requests. Any server cache is bounded, revision-keyed and never caches authorization.
5. **Summary selection.** Select a completed result for the same PR/base/head pair, deterministically choosing the latest matching result. Validate stored fields again. Confirm its generation exclusion policy is compatible with current policy; if this cannot be established, withhold the summary rather than leaking text derived from newly excluded content. Never attach an older summary just because it is the latest run. Missing/partial groups do not establish review coverage. Reusing saved groups consumes no new model tokens.
6. **Finding placement.** Query exact paths and immutable occurrences for the selected revision, with bounded results. Use the occurrence's validated source SHA, side and range to place it only where the displayed diff contains that location. Do not use a mutable `Finding` row, a path substring or `last_seen_run_id` alone as proof of an inline match. A later completed verification at the exact displayed head may supply a separate location for an older finding when its quoted lines match the displayed diff. Preserve original occurrence provenance; label the verification verdict separately from resolution and never treat a proposed suggestion as an applied fix. Batch and bound the lookup, deduplicate visible markers by finding ID, and preserve old occurrence links. Evidence without a validated review or verification location remains explicitly unanchored. Current resolution is a separate field; opening Code never changes it.
7. **Costs and persistence.** This delivery introduces no embeddings, pgvector, model generation, service or worker. Diff navigation still uses GitHub requests, CPU and network bandwidth. No token-savings or review-quality claim follows. Prefer existing persisted summaries/occurrences; no schema migration is expected. If implementation proves new durable data necessary, document why and use an additive migration rather than silently changing this scope.

## Delivery checklist

Complete checks with named evidence, not merely a UI mock.

Contract limits: 8 MiB decoded GitHub JSON, 20-second elapsed stream budget checked between chunks with five-second I/O timeouts, up to two attempts with the existing one-second retry delay; four immutable cached comparisons, 300 files, ten files per page. Patches are credential-checked in full before the 256 KiB patch and 2,000 rendered-row limits. Findings are capped at 100 occurrences, 32 KiB per sanitized snapshot. Summary groups require exact base/head and identical generation exclusions. Occurrence source_sha/path/range prove placement; missing provenance stays unanchored.

[GitHub compare documentation](https://docs.github.com/en/rest/commits/commits#compare-two-commits), checked 2026-10-29: first page contains at most 300 changed files for the whole comparison; renames supply previous_filename; patches may be absent. Missing patches are unavailable, never inferred binary or unchanged.

### A. Fix the read contract and boundaries

- [x] Trace the current pull routes, GitHub adapter, summary policy snapshot and occurrence provenance; record the exact fields usable for matches and the fallback for legacy data.
- [x] Define browser-only overview/file contracts with explicit revision identity, file/group selection, patch availability/truncation and bounded finding references. Keep related graph reads on their existing contract. Record numeric byte, line, time and pagination limits before implementation.
- [x] Confirm immutable comparison patch behavior and missing/large/renamed-file cases using adapter fixtures and the provider's documented contract. Do not claim a complete inventory above the comparison cap.
- [x] Fix the legacy URL mapping and empty/loading/error copy within the existing UI system. No new visual world or generic code-editor framework.

### B. Deliver usable PR diffs

- [x] Implement authorized, bounded diff metadata/content reads independently of graph readiness. Keep exact-revision, policy and credential checks at both admission and response boundaries.
- [x] Replace the Impact entry with Code, shared file search/pagination and the selected-file diff; preserve old links and neighboring PR behavior.
- [x] Reuse source presentation and installed diff utilities where suitable. Handle old/new line numbering, CRLF, Unicode, no-final-newline, additions, deletions and renames. Surface unavailable/truncated content and keep rendering bounded.
- [x] Prove that graph disabled, missing and indexing states, plus unsupported structural languages, do not block a supplied text diff. Prove navigation performs no index/review/model write.

### C. Connect existing explanations and findings

- [x] Expose matching saved groups with run/revision provenance, policy checks and no provider calls. Add the group combobox, description and file navigation; retain All changes for absent/unassigned content.
- [x] Add bounded exact-path/revision occurrence lookup and truthful diff placement. Reuse the existing finding sheet and current-status presentation. Keep older or unanchorable evidence accessible without remapping it.
- [x] Keep graph dependencies, definitions and retained source available as secondary views. Graph failure affects only those views; preserve current explicit administrator indexing and all graph/MCP boundaries.

### D. Verify, document and finish

- [x] Backend: real isolated DB plus stubbed GitHub responses for authorization loss, wrong repo/revision, changed head/base, policy changes, excluded rename sides, malformed/oversized/secret-bearing patches, missing patches, summary mismatch, occurrence provenance, pagination and cache behavior. Include graph-disabled operation and zero unexpected jobs/model calls.
- [x] Browser: mouse/keyboard/touch, group/file search and clear, reload/back/legacy URLs, exact diff anchors and historical evidence, stale response cancellation, access loss, loading/error/empty/large-file states, and working source/related navigation. Check header/list/empty-state alignment at desktop and intermediate widths and page overflow on mobile in light/dark themes.
- [x] Run appropriate backend checks and Ruff; export OpenAPI and regenerate `api.generated.ts`; run the frontend production build and focused Impact/Code plus neighboring PR tests. Broaden only for changes or unresolved failures that justify it.
- [x] Inspect a real authorized local PR separately from mocks. Measure first file load, repeated file/group navigation, payload size and browser responsiveness on representative and bounded-large comparisons. Record conditions and limitations; do not infer performance from mocked tests. Verify the diff works with graph unavailable without changing the user's repository opt-in just to create the condition.
- [x] Update PRODUCT.md, DESIGN.md, operations/acceptance and the Phase 4 handoff to describe the delivered behavior. Preserve historical evidence and the graph Phase 2 experimental boundary. Refresh Graphify after final code/docs. Reload only affected local services safely, if required, and verify existing records/grants remain intact.

## Deferred work

- Newly generated guided tours and explanation-to-hunk/line anchors. These need a separate schema, evidence validation, model budget and evaluation; existing summary groups are file-level only.
- Automatic reviewed-file indicators, file navigation and finding indicators are delivered in the [high-priority review workflow](pr-code-review-workflow-plan.md). The user replaced the originally proposed personal checklist with checks from validated saved review coverage. Step-level tour progress, new chat tools, third-party comment ingestion, general editor features, repository-wide tree browsing and an unbounded full-PR renderer remain deferred.
- New parser capabilities, symbol call graphs, graph rebuilds, embeddings, MCP scope/tool changes, provider experiments, commits/pushes and production release work.

## Completion and next-session handoff

The delivery is done when an authorized reviewer can open Code with no usable graph, inspect an available PR diff, navigate matching stored change groups and findings with truthful revision provenance, and optionally follow indexed relationships. Missing evidence stays explicit. No automatic inference or indexing is introduced. Checks B–D require observed evidence and stay open until that evidence exists.

All checkpoints A–D are complete locally. Read [the delivery report](pr-code.md) for exact contract decisions, implementation paths, test boundaries, real browser samples, resource limits and preserved records. Checks do not imply a full-suite rerun, hosted CI, production deployment, quality improvement or eight-GB capacity acceptance.

The requested [PR Code performance follow-up](pr-code-performance-plan.md) now takes priority for this browser surface. Its implementation and local acceptance are complete, with measured results and cache/security limits recorded there. Graph Phase 5 remains separate. The generated tour and other deferred work remain deferred; no further model invocation is authorized by this delivery.
