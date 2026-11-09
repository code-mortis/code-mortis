# Code graph Phase 3: external read access

Local implementation adds an independent, default-off `/mcp/code` resource with only `code:read`. It does not change the failed Phase 2 quality gate, enable new repository graphs, or establish token savings. The visual explorer remains Phase 4. This change adds no dependency, migration, service, embedding or vector database.

## Authorization and storage

An administrator enables Code MCP in Settings. Each user separately consents to a client and explicit repositories that already have code graphs enabled. Reviews, Security, management and personal API tokens gain no code access. Code cannot read review/security history, manage the instance, change repository configuration, enqueue graph work or invoke models.

Every tool checks its current token, grant and selected repositories. Shared graph reads verify the requesting user, membership, active repository/installation, repository opt-in and fresh GitHub repository identity. Reads recheck access and policy after retrieval; a grant/user/token/policy change during a read discards its content. No source response cache or MCP operation replay record is created. Existing audit rows contain only tool, outcome and duration.

The graph worker remains the sole writer of retained objects. The trusted API mounts the existing `code-graph-data` volume read-only in the normal production/development Compose files. Shared source locks coordinate with the writer's exclusive acquisition/retention lock. Readers never create stores/lock files, fetch Git objects, check out files, execute repository instructions or replace a missing revision with another one. The parser and native runner still have no retained-object mount. Metadata queries do not need the volume.

## Navigation contract

| Tool | Behavior |
| --- | --- |
| `code_list_repositories` | Discover currently permitted, selected, opted-in repository IDs/names. At most the grant's 100 selected repositories. |
| `code_index_status` | Inspect an exact SHA or the last observed default-branch SHA; report readiness, tested definition languages, supported relationships and visible coverage. |
| `code_search` | Browse definitions, search exact symbol names/keys or a single-line literal text query. |
| `code_get_symbol` | Resolve a returned symbol key within the requested repository/revision and attach its bounded original source span. |
| `code_get_source` | Read a bounded UTF-8 byte range of a verified regular blob from the exact pinned tree. |
| `code_get_relations` | Follow conservative resolved file imports, with direction, depth and original import provenance. Callers/references are explicitly unsupported. |
| `code_change_impact` | Compare explicit indexed base/head commits and show potential incoming dependencies in both. Renames appear as removal/addition; edges never imply a confirmed defect. |

All navigation takes exact 40-character commit SHAs. Status without a SHA selects only the last observed default-branch identity, never an older ready fallback. The response identifies its commit, snapshot, policy and extractor. Missing or failed snapshots do not return another revision's facts. Local uncommitted edits are absent. Impact does not access PR/review history.

Bounds: 100 search results, ten source files and 64 KiB per source/text page; two traversal hops, 500 visited files and 100 returned edges; ten changed paths per impact page. Definition cursors use result offsets; text cursors use a visible-file offset and original UTF-8 byte offset. Reuse the returned cursor with the same revision/query. Source reads continue from `next_cursor.start_byte` with the same path/revision. Every page repeats authorization; a cursor grants nothing. Truncated traversal requires narrowing paths/depth, rather than suggesting an exhaustive result.

Exclusions apply before enumeration, traversal, comparison, pagination and coverage counts. Source and derived names are labeled untrusted. The full blob is checked using existing credential sanitization before any source/text slicing, preventing split-secret disclosure. Credential-bearing source is withheld with a sanitized error; metadata can remain readable. Missing/busy objects report `source_state: unavailable`; retry the same revision. These reads consume server CPU/I/O and the external agent's context tokens, but no model calls on Code Mortis.

## Local verification

- An initial 187-case graph/MCP/review regression selection passed against the isolated PostgreSQL/Redis stack with real fixture Git objects and extraction. GitHub/provider responses are stubbed. The full backend run passed **2,916 tests** with 637 existing warnings in 900.32 seconds. A subsequent narrow source check preserves normal CRLF endings while still withholding credential material; all 49 final MCP/graph regression checks passed after that refinement. The full suite was not repeated for this one-line normalization check.
- All 1,396 frontend tests passed, with two existing skips. The focused MCP selection passed 32 tests on desktop/mobile. It covers separate settings, installation instructions, selected code repositories/read scope, disabled approval without a scope, and existing grant/consent behaviors. These use mocked APIs against the production build.
- TypeScript/production build, OpenAPI generation, generated API types and Ruff passed. Development, production and Coolify Compose configuration checks verify the API read-only mount, worker-only write access and parser/runner isolation. The existing large-JavaScript-chunk build warning remains.
- Rendered MCP settings, Code installation instructions and code consent were captured at desktop/mobile sizes with light/dark coverage. Impeccable's fresh finish reviewer returned **ship**, with no material fixes, and its documenter verified that the eight clean captures preserve the incumbent design. The unchanged design sidecar remains intact. A pre-existing omission of API tokens in the general Settings documentation was recorded without expanding this change.
- Local API, frontend, six workers and scheduler were recreated after pausing dispatch and observing zero active/reserved/scheduled tasks on all workers. Application counts and hashes of all five graph tables matched before/after. The API verified its read-only mount, the original pinned SHA and all 392 tree entries. The graph worker alone has a writable mount; parser and native runner have none. Readiness, public code-resource discovery, unauthenticated refusal (401), and unknown-route refusal (404) passed. See the [development evidence](../../evidence/code-graph-phase3-development.json) for worker/health details.

No commit, push, hosted CI, production rollout, new repository opt-in or provider evaluation was performed. The previous 2C corpus/results remain frozen.

## Real external client

Passed on 2026-10-26 local time (2026-10-27 02:36 UTC) using MCP Python SDK 2.3.0 over Streamable HTTP and the running development application, with real GitHub permission checks and retained repository objects. The user explicitly authorized local activation and the temporary `code:read` connection for `wilfredinni/code-mortis`; the assistant completed the actual Chrome consent form on their behalf. The initial request expired without creating a grant; a new DCR/PKCE request completed successfully. No browser session or grant was manufactured in the database.

The client listed all seven tools, then called repository discovery, status, definition search, symbol lookup, file relations and source. It followed `frontend/src/mcp.tsx` to `frontend/src/components/ui/button.tsx`, read 2,386 original source bytes and verified the same commit/snapshot throughout. The [client evidence](../../evidence/code-graph-phase3-client.json) stores identities and hashes, without source bodies or token values. Change-impact comparisons use real Git fixtures in isolated transport tests; this real repository has only one retained snapshot, so this check does not establish a live two-revision comparison.

The temporary grant was revoked in `finally`; persisted grant scope/repository, six successful audit records, zero operation replay rows and the revoked browser state were verified. Existing Reviews/Security grants remained active, and Code MCP remains enabled as authorized. Application counts and snapshot/file/symbol/import hashes remained unchanged. Repository readiness metadata had a scheduled check before this grant, so its full-row hash changed independently. No model call or permanent agent connection was created.

The reproducible external check imports only the installed MCP SDK and HTTP/standard-library clients. It performs DCR, PKCE and an explicit browser consent flow, then lists tools/repositories, reads status, searches a definition, follows a file relation and reads its source at the expected commit. It stores token values only in memory, omits source bodies from the evidence and revokes its temporary grant in `finally`. Registered client metadata remains visible in Settings. It does not configure any permanent agent connection or claim compatibility for other clients.

Run from `backend/` after enabling Code MCP, then open the printed URL and select only the intended repository:

```sh
uv run python ../scripts/check_code_mcp.py \
  --origin http://localhost:5173 \
  --repository-id 1407742623 \
  --path frontend/src/mcp.tsx \
  --output ../docs/evidence/code-graph-phase3-client.json
```

The retained Code Mortis snapshot currently identifies `d8a09f12956bf074cdc9f5db868ecf04895eb812`; it is not the uncommitted implementation. Do not manufacture browser sessions/grants in the database to close this check.

## Development graph freshness

The final AST refresh rebuilt local navigation with 4,404 nodes, 16,598 edges and 227 communities. The existing warnings remain: 24 JSON-like files produced no nodes and `settings.tsx` was partially extracted. A bounded semantic refresh covers the eight changed Markdown documents; Graphify is navigation only, not product or correctness evidence.
