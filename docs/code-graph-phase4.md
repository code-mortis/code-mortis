# Code graph Phase 4: PR impact explorer

The existing PR detail now has an **Impact** tab. It connects changed paths to supported file imports, indexed definitions, original source and the PR's saved findings. It uses the existing design system and graph reader; no dependency, migration, service, embedding or model call was added. Review enrichment remains experimental under Phase 2's failed broader-rollout gate.

## Read and indexing contract

- Three browser-session-only routes live under `/api/v1/pulls/{pr_id}/code-graph`: GET overview, POST `/query`, and administrator-only POST `/index`. PAT and MCP scopes are unchanged. Reads reuse current membership, opted-in repository access and fresh GitHub identity. Indexing also requires GitHub write access.
- The server resolves the saved PR's base/head pair. Query and index requests include that pair and reject changes with 409. The immutable GitHub comparison supplies the merge-base SHA and changed files, so base navigation describes the actual PR starting point rather than an unrelated branch-tip diff.
- The comparison payload is validated. Exclusions apply before visible counts, search and ten-file pagination. Renames appear as removal/addition. GitHub returns at most 300 comparison files; reaching 300 conservatively marks the list incomplete. Filtering only searches returned paths. See [GitHub's comparison contract](https://docs.github.com/en/rest/commits/commits#compare-two-commits).
- Head and merge-base snapshots are resolved independently under the current parser/policy identity. Missing, failed or expired revisions never substitute another commit. An administrator can explicitly index the selected revision using the existing dedicated graph worker. Opening or navigating Impact does not enqueue work.
- Retention keeps the latest default branch plus up to ten recent usable unpinned revisions, with the latter expiring after seven days. This replaces the original one-other limit, which made indexed PRs evict each other. Beyond the cap, the oldest non-default revision is retired. Existing 256 MiB per-repository and 1 GiB instance Git-store limits and active-consumer locks remain; historical PRs are not pinned indefinitely.
- File relations are bounded to one hop and 100 edges, with a truncation notice. Definitions use 100-result pages; source uses the existing 64 KiB page and original UTF-8 byte cursors. Source is read only from the pinned tree/blob after full-blob credential checks. Credential material is withheld, including when outside the requested slice. Busy or missing objects remain explicitly unavailable.
- Browser navigation freshly checks GitHub access once before reading and once before publication; the shared local reader avoids repeating those checks for each snapshot. MCP and review readers keep their independent authorization boundary. A bounded in-process cache holds only validated immutable comparison metadata for 32 exact repository/base/head identities; current exclusions and fresh permissions apply on every request. It contains no source, token, session or permission decision. Publication rechecks current access, policy and PR identity. Policy/access/PR changes discard the response. Existing no-store response headers and graph query caches discarded on unmount apply. An open tab rechecks every 30 seconds; received 403/409 errors hide previously displayed graph content. Existing PR permission polling remains in place.

## Interface

Impact retains the PR header, review actions and existing finding tabs. URL parameters preserve path filter, changed-file page, selected file and head/base selection. Selecting a removed path switches to the merge base. Relative file imports have a small diagram and an equivalent keyboard-accessible list; selecting a related path follows it within the same revision. Definitions open their original source span, and source pages display commit, blob, line and byte provenance.

Links search the current PR's Open, Resolved or Dismissed findings through their existing authorized views and evidence sheet. The selected graph commit does not rewrite finding history or prove that a historical finding applies to this commit. Dependencies indicate candidates for inspection, not confirmed defects or resolved callers. Supported imports remain conservative Python and JavaScript/TypeScript relative paths; definitions cover the existing tested adapters. Local uncommitted files are absent.

## Validation boundaries

Frontend production build and **1,410 Playwright tests passed, with two existing skips**. The 14 new desktop/mobile cases exercise keyboard navigation, source and definition pagination, URL state, findings links, disabled/missing/paused/queued states, explicit administrator indexing and disappearance after access/revision errors. API responses are mocked in these browser tests.

Full-page production-build captures at 1440px, 390px and the observed user width of 2544px were inspected with light/dark coverage. The fresh Impeccable finish reviewer returned **ship**, with no material fixes. Its documenter updated only delivered-feature prose and the local surface brief. The existing design sidecar and tokens remain unchanged; pre-existing sidecar drift is report-only. The mechanical detector returned no findings.

**2,946 backend tests passed**, with 637 existing warnings in 1,007.51 seconds, against the isolated PostgreSQL/Redis test stack. Coverage includes real Git fixture acquisition/parsing, browser/PAT separation, malformed comparisons, exclusions, request bounds, exact revision refusal, access/policy/PR changes during reads, credential withholding, retained-object loss and administrator indexing. External GitHub/provider replies are stubbed there. Ruff, OpenAPI/type generation and `git diff --check` passed. The existing large-JavaScript-chunk build warning remains.

The [local development evidence](evidence/code-graph-phase4-development.json) separately records a real workflow check for `wilfredinni/code-mortis` PR #41. Fresh GitHub permission and immutable-comparison requests returned 28 visible changed paths. The requested head `d3b1b3eb9cbc1f4bdea2bee33acbfe99c710a456` was initially unavailable; explicit admission reached the dedicated graph worker, and real Git acquisition/parsing completed in 68.91 seconds with a partial snapshot. Navigation through `frontend/src/analytics.tsx` returned 13 file relations, ten definitions and an original source span verified byte-for-byte against its retained blob. Merge-base navigation remained unavailable without falling back to the new head or retained default branch.

This real check called the same workflow functions inside the development API container using the opted-in administrator and real GitHub authorization. It did not manufacture a browser session or grant. The native Chrome automation could read the window but timed out twice on actions and reset its kernel, so **an authenticated real-browser walkthrough was not completed**. This limitation does not turn mocked browser coverage into real end-to-end evidence.

After stopping the scheduler, all six workers reported zero active/reserved/scheduled work and their consumers were paused. API, frontend and six workers were recreated, then the scheduler resumed. Before/after counts, graph table hashes and MCP grant hashes matched. All 14 services run, the API is ready, all six expected worker queues are present, and the three live browser endpoints reject unauthenticated requests with 401 and `Cache-Control: no-store`. Existing 180 reviews, 354 findings, 377 occurrences and 31 security scans remain; only the explicitly requested PR snapshot was added before the reload. The original default-branch snapshot is retained.

The local AST graph refreshed to 4,489 nodes, 16,863 edges and 218 communities. Its existing partial `settings.tsx` extraction warning and 25 zero-node JSON-like inputs remain developer-tool limitations. The bounded eight-document semantic refresh verified current source hashes, caches and manifests, preserving the existing AST and unrelated content; the final navigation graph has 4,511 nodes and 16,888 edges. Graphify is navigation, not runtime evidence.

## Navigation and retention follow-up

The initial implementation repeatedly authorized the same browser operation against GitHub: six fresh checks for overview and four for file reads. Each local Git subprocess also waited 200 ms after completion. Browser reads now use one fresh check before and one after the whole operation; revocation, PR changes, exclusions, credential checks and exact-policy matching still discard content. Subprocess waits wake on exit while preserving cancellation, timeouts, output bounds and cleanup. Repeated navigation reuses bounded immutable comparison metadata, never cached permissions or source.

Measured in the development API process with real GitHub authorization and the same PR #38 path, `frontend/src/components/ui/button.tsx`:

| Operation | Before | After |
| --- | ---: | ---: |
| First overview | 4.773 s | 2.932 s |
| Repeated overview | Not separately measured | 1.364 s |
| Dependencies | 2.373 s | 1.260 s |
| Definitions | 2.233 s | 1.144 s |
| Source | 2.857 s | 1.214 s |
| Three local Git reads inside source | 0.611 s | 0.008 s |

These are individual workflow measurements, not browser paint metrics or a statistical latency guarantee. The remaining roughly one second is mostly the two live GitHub permission checks. The first overview must still obtain its immutable comparison; a restarted process starts with an empty metadata cache.

The original retention policy evicted one PR when another non-default revision was indexed. The new ten-revision bound is tested for coexistence, eviction, expiry, exact source and orphan refs. Local PRs #38 and #41 now have both merge-base and head snapshots available together with the default branch, with original source successfully read from all four. They remain explicitly partial indexes. Three missing snapshots were admitted through the existing browser workflow and graph worker; extraction took 5.29–6.39 seconds, separately from queue wait. Other never-indexed or expired revisions still need **Index selected revision**.

The follow-up passed 298 focused backend checks, then 1,013 checks including the final comparison-cache tests and shared verification, background and security workflows (the explorer cases overlap between runs). The latter run reported 560 existing warnings. All 14 focused desktop/mobile browser tests and the production build passed. Updated light/dark captures were inspected, Ruff and diff checks passed, and the Impeccable detector found no issues. Browser APIs remain mocked; real GitHub reads and index recovery are recorded separately in the [follow-up evidence](evidence/code-graph-navigation-development.json).

Local restart drained all six workers first. All 14 services returned, the API readiness and unauthenticated no-store checks passed, and application counts, snapshot/fact hashes and the MCP-grant hash were preserved across restart. The repository-readiness hash changed while scheduled refresh ran between captures; the snapshots were unchanged. Final graph growth is limited to the three explicitly recovered snapshots. No model inference, new repository opt-in, new grant, commit, push or production deployment occurred.

## Indexing status and JSX parser follow-up

The seven retained local snapshots had all been marked partial. The default revision contained 239 fully parsed files, one partial file, 72 unsupported formats and 80 configured exclusions, with no failed files. `settings.tsx` was downloaded in full; Tree-sitter incorrectly rejected the valid literal JSX text `Limits & rules`, which caused the containing declaration to be withheld. This matches the upstream [bare-ampersand issue](https://github.com/tree-sitter/tree-sitter-javascript/issues/366); the upstream [TypeScript correction](https://github.com/tree-sitter/tree-sitter-typescript/pull/371) was still open when inspected.

Extractor **tree-sitter/0.25.2-mortis/2** retries only recoverable JSX literal-text errors, masking ampersands with equal-length ASCII spaces in a temporary parser input. The retry is accepted only when the entire resulting tree has no errors. Expressions, attributes and markup delimiters are not masked; ambiguous recovery and real syntax errors stay partial. The original bytes, blob hashes, UTF-8 offsets, line numbers and returned source are preserved. This is a narrow compatibility workaround, not a complete fix for every upstream JSX parsing gap; remove it when a validated upstream release replaces the pinned grammar. No new dependency was added and the repository file was not rewritten to appease the parser.

Snapshot status now reflects eligible-file processing. Unsupported formats alone produce `ready`, while parser/encoding failures and size omissions produce `partial`. Shared coverage includes parsed, partial, failed, unsupported and oversized counts plus reasons; configured exclusions remain hidden. `coverage.complete` remains false if any visible file is not fully parsed, including unsupported formats, so MCP and other consumers retain the coverage boundary. Existing snapshots are immutable and the version bump requires real re-extraction.

Repository configuration and Impact display **Indexed** / **Indexed with warnings** alongside the coverage counts and specific reason text. The backend keeps its established state values. The rendered mobile and desktop captures preserve the existing controls, themes and status colors.

The focused checks passed **176 backend tests**, followed by **107 review, stage, worker and storage tests**. Regression cases cover JSX/TSX literal text, entities, nested elements, Unicode/CRLF, original ranges, genuine and ambiguous syntax errors, unsupported formats, exclusions and encoding/size omissions. **900 browser tests** across Impact and workspace passed against mocked APIs. Production build, OpenAPI/type generation, Ruff and diff checks passed; the existing large bundle warning remains. Real local parser/worker reconstruction and preserved application counts are recorded in [coverage follow-up evidence](evidence/code-graph-coverage-development.json). This is not an authenticated real-browser walkthrough or production validation.

## Next session

Use `code_graph/explorer.py`, `schemas/code_graph_explorer.py`, the three pull router endpoints and `components/code-graph-explorer.tsx` as the Phase 4 entry points. `test_code_graph_explorer.py` exercises real Git fixtures against the isolated database with stubbed external GitHub replies; `code-graph-explorer.spec.ts` covers the UI. Shared retrieval/storage/worker guarantees remain authoritative.

Phase 5 is operational release readiness. Hosted CI, production deployment, larger-repository capacity and review-quality improvement remain separate checks. Do not repeat the frozen Phase 2 experiment, widen grants or opt in other repositories implicitly. No commit, push, provider evaluation or production change is part of this phase.
