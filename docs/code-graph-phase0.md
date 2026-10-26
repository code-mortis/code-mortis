# Code graph Phase 0: feasibility and implementation decisions

Completed locally on 2026-10-26. This closes Phase 0 of the [feature plan](code-graph-plan.md). It adds an isolated experiment and evidence, not a production graph feature. No application dependencies, migrations, routes, permissions, review prompts, or deployment changed.

## Decision

Use **direct Tree-sitter parsing with conservative file resolution** for Phase 1. Start with file/definition navigation and relative import dependencies. Keep unresolved call sites explicit; do not advertise a reliable caller graph yet. The experiment demonstrates useful structure across twelve languages, not full semantic understanding of those languages.

Do not use Graphify 0.9.37's complete extracted graph as authoritative review evidence. The pinned version produced a confirmed false call marked `EXTRACTED` in the shadowing fixture, and missed calls through aliases/re-exports. Graphify remains useful for local developer navigation. A later adapter or fixed version can be reconsidered against the same fixtures.

Keep the feature opt-in and embedding-free. Choose a separate `/mcp/code` protected resource with `code:read` consent. Phase 1 builds internal storage/retrieval; Phase 3 implements this MCP decision.

## Reproduce and inspect the evidence

- [Evaluation harness](../scripts/code_graph_eval/run.py): exports regular tracked source blobs from local Git object databases, then starts fresh bounded containers.
- [Extractor comparison and assertions](../scripts/code_graph_eval/evaluate.py): direct parsing, the pinned Graphify API, adversarial checks, and measurements.
- [Inert fixtures](../scripts/code_graph_eval/fixtures.json): twelve languages, aliases, re-exports, cycles, shadowing, dynamic calls, malformed syntax, and execution traps.
- [Dockerfile](../scripts/code_graph_eval/Dockerfile) and [hashed requirements](../scripts/code_graph_eval/requirements.txt): pinned base image and evaluation-only dependencies.
- [Recorded JSON evidence](evidence/code-graph-phase0.json): corpus revisions/digests, script hashes, package versions, all twelve benchmark runs, three fixture states per candidate, and diagnostics.

From the repository root:

```sh
docker build -t code-mortis-code-graph-eval:phase0 scripts/code_graph_eval
python3 scripts/code_graph_eval/run.py --repo . --repo ../noodle --output graphify-out/code-graph-phase0-repeat.json
backend/.venv/bin/ruff check scripts/code_graph_eval --config backend/pyproject.toml
```

Omit the second `--repo` when Noodle is unavailable. To reproduce the recorded corpus, use local checkouts at the exact commits below. The harness reads each checkout's HEAD; it does not change branches, copy uncommitted files, execute checkout filters, or clone/upload repositories. Git modes other than regular files are excluded. Its corpus manifest digest covers ordered path, blob ID, and size entries. Local working changes to the experiment do not enter the benchmark corpus.

Image construction downloads public pinned packages. Extraction has no network, model clients, credentials, repository scripts, Git hooks, or MCP servers. The container receives only exported source and the evaluation directory, both read-only. Python runs in isolated mode; fixture modules are never imported. Dependencies are installed in the disposable image, not Code Mortis's backend environment.

## Candidate comparison

| Candidate | Evidence and packaging | Decision |
| --- | --- | --- |
| Graphify `graphifyy==0.9.37` | Apache-2.0 package metadata; 30 pinned installed packages in this evaluation, including NetworkX, NumPy, RapidFuzz, Tree-sitter and 25 grammar packages. Used `graphify.extract.extract(..., parallel=False)` directly. More relationships, but a false high-confidence call and substantially higher memory on these inputs. | Reject the complete graph as production evidence for now. Do not introduce Graphify CLI/config ingestion into the review worker. |
| Direct Tree-sitter `0.25.2` plus selected grammars | MIT metadata for the parser and twelve selected grammar packages; Python standard-library `ast` assists import parsing. No graph database, embedding SDK, language server, package installation, build, or repository execution during extraction. | Select this foundation. The small experiment is not a finished production adapter. Add only the selected parser packages to the production lockfile in Phase 1. |
| Python standard-library AST alone | Useful for Python, and used here for imports; does not supply the other language grammars. | Insufficient as the sole multi-language foundation. |
| SCIP/indexers | SCIP is a useful interchange format. Indexer prerequisites differ by language. Sourcegraph's TypeScript instructions install project dependencies before indexing; its precise-navigation docs describe reusing build configuration for complex projects. | Defer. A future indexer must work within the no-repository-execution boundary, with explicit resource and dependency handling. No SCIP benchmark was run and no claim is made that all SCIP indexers require builds. |

The pinned Graphify extraction API can create process pools by default; the experiment explicitly disables them. Its Fortran adapter can invoke `cpp` for uppercase Fortran extensions. Those extensions are outside this experiment's allowlist. Broad Graphify extraction is therefore not assumed to be subprocess-free. We did not execute its semantic/LLM pipeline, clustering, HTML generation, MCP ingestion, or CLI update path as part of the comparison.

The direct candidate imports only the grammars it encounters, although the shared comparison image contains all Graphify dependencies. Thus the measurements compare loaded extraction behavior, not minimal image size. Production should use a smaller dependency set and retain the required third-party notices. The experiment contains no copied vendor implementation.

Sources: [Graphify upstream](https://github.com/Graphify-Labs/graphify), [Tree-sitter Python bindings](https://github.com/tree-sitter/py-tree-sitter), [SCIP TypeScript setup](https://github.com/sourcegraph/scip-typescript#quick-start), and [Sourcegraph precise-navigation documentation](https://sourcegraph.com/docs/code-navigation/precise-code-navigation). Version/license observations above also come from the installed pinned package metadata, rather than assuming current upstream equals the evaluated release.

## Language capability matrix

This describes the **direct experiment**, not currently shipped Code Mortis support. “Definitions” means the selected named declaration kinds in `LANGUAGES`, plus JS/TS named arrow/function-expression variables. It does not mean all declarations or exports in a language. All twelve basic fixtures parsed and their expected named definitions were found; TSX and JSX component fixtures also passed.

| Language | Parse / named definitions | Import syntax | File target resolution | Symbol references | Calls |
| --- | --- | --- | --- | --- | --- |
| Python | Validated | `import`, `from ... import` | Unambiguous relative modules/packages only | Not resolved | Syntax sites, unresolved targets |
| TypeScript / TSX | Validated | Static imports and re-exports | Unambiguous relative files/index files only | Not resolved | Syntax sites, unresolved targets |
| JavaScript / JSX | Validated | Static imports and re-exports | Unambiguous relative files/index files only | Not resolved | Syntax sites, unresolved targets |
| Go | Validated | Captured | Unresolved | Not resolved | Syntax sites, unresolved targets |
| Rust | Validated | Captured | Unresolved | Not resolved | Syntax sites, unresolved targets |
| Java | Validated | Captured | Unresolved | Not resolved | Syntax sites, unresolved targets |
| C | Validated | Includes captured | Unresolved | Not resolved | Syntax sites, unresolved targets |
| C++ | Validated | Includes captured | Unresolved | Not resolved | Syntax sites, unresolved targets |
| C# | Validated | Using directives captured | Unresolved | Not resolved | Syntax sites, unresolved targets |
| PHP | Validated | Require/include syntax captured | Unresolved | Not resolved | Syntax sites, unresolved targets |
| Ruby | Validated | Not extracted by this baseline | Unresolved | Not resolved | Syntax sites, unresolved targets |
| Bash | Validated | Not extracted by this baseline | Unresolved | Not resolved | Command sites, unresolved targets |

Graphify also extracted the basic definitions in all twelve fixtures. It found the nine simple same-file helper calls in Go, Rust, Java, C, C++, C#, PHP, Ruby and Bash. That is a smoke check, not proof of their type/member resolution. Its Python fixture emitted one reference edge; the other fixtures do not establish broad reference support. Import/re-export relation counts for each language are saved under `probes.graphify.probe.initial.matrix` in the evidence.

Other Graphify grammars, including Kotlin, Swift, Scala, Lua, Elixir, Fortran, Julia and others, were not validated. Additional extensions such as `.mjs`, `.mts`, `.h` and `.cc` also need explicit adapter/fixture work. Unsupported files must remain visible as unsupported and continue through normal review behavior.

Known limits of the selected baseline:

- File imports do not establish that a particular imported name exists, is callable, or is exported. Named aliases and re-exports contribute file dependency paths only.
- No Python source-root guessing, JS/TS `paths` mapping, package/workspace resolution, dependency installation, extension substitution, or type checker. Ambiguous relative candidates stay unresolved.
- Dynamic dispatch, callbacks, decorators, monkey-patching, overloads, inheritance and runtime-generated names do not acquire resolved call edges. A decorator can change the callable; syntax alone does not settle that.
- Symbol identity includes file, byte offset and declaration kind within a snapshot. Cross-revision identity and fully qualified display names still need production design/validation.
- Parser errors yield `partial`, not “complete”. Nodes containing parse errors are omitted; surviving facts retain their file's partial status. The real corpora exposed parser gaps even in source that may be valid for its own toolchain.

## Adversarial and revision checks

| Case | Result and implication |
| --- | --- |
| Same-named definitions in different files | Direct IDs remain distinct and every returned edge endpoint exists. No global name matching is used for calls. |
| Python imported alias shadowed by a parameter | Graphify emits `shadow() -> same()` at `python/use.py:L13` with `EXTRACTED` confidence, even though `renamed` is the function parameter. The harness asserts this specific finding so a future version change requires reassessment. Direct parsing leaves the call unresolved. |
| Named import aliases and barrel re-exports | Direct file dependencies preserve both use-to-barrel and barrel-to-target links in Python/TS/JS. Graphify omitted the corresponding `entry()` call edges in these fixtures; an empty caller list is not proof of no callers. |
| Mutual import/call cycles | Both candidates terminate. Direct records both directed file dependencies; Graphify finds the Python/TS cycle calls. A future traversal must track visited IDs and apply depth/result limits. |
| Decorator, callback, unknown function and shadowing | Direct records syntax without inventing a callee. Graphify's missed/incorrect resolved relationships are not promoted to evidence. |
| Deleted target with unchanged callers | Full-snapshot re-extraction removes the direct target/dependency edges. Missing imports remain observable with a null target. Graphify's tested full scan also removes its old call to the deleted target. |
| Changed definition with unchanged caller bytes | Replacing Python `same` with `replacement` removes the old definition, retains the valid file dependency, and creates no fabricated symbol call. The saved caller SHA-256 is unchanged. |
| Malformed and unsupported input | Direct marks the malformed fixture partial, omits its broken definition, and marks unsupported input explicitly. Graphify prints a partial-syntax warning but has an empty `failed_sources` list; that list alone is not a complete parse-status contract. |
| Repository execution | A Python module that raises on execution is parsed without execution; a package postinstall marker and an instruction-file trap are not acted on. The marker remains absent. Container isolation supplies the stronger enforcement boundary. |

These checks compare **full snapshot rebuilds**, including Graphify's per-file cache reuse inside its extraction API. They do not certify incremental CLI updates. Inspection of `extract()` shows that `resolution_context_nodes/edges` supply unchanged targets, while emitted calls originate from files passed for extraction. Merely re-extracting a changed definition is not proof that unchanged incoming callers have been recomputed. Phase 1 therefore rebuilds the bounded corpus for each new revision, with atomic replacement. Defer artifact sharing and incremental invalidation until separately tested.

## Resource measurements

Linux aarch64 containers under Docker/OrbStack on the local Mac; Python 3.13.17. Docker reported 12 available CPUs and 5,227,761,664 bytes of VM memory. Each extraction container was limited to **one CPU**, no swap beyond its memory allowance, 32 PIDs and 120 seconds. Direct parsing ran with **256 MiB**; Graphify with **512 MiB**. Each had a read-only filesystem and a 128 MiB `/tmp` maximum, charged within the container limit.

Three fresh-container runs per candidate/repository, serially, with empty extraction caches. Source selection includes tracked regular files with the evaluator's fourteen extensions, including tests, but excludes manifests, other extensions, symlinks, submodules and uncommitted changes. No source file exceeded the harness's 1 MiB per-file filter. These are modest source corpora, not whole checkout/storage sizes or a representative industry benchmark.

| Corpus | Exact commit | Source files | Source bytes |
| --- | --- | ---: | ---: |
| Code Mortis | `d8a09f12956bf074cdc9f5db868ecf04895eb812` | 244 | 4,176,007 |
| Noodle | `2153c7745e66b72f48dee76b992e91b3be7c653f` | 653 | 6,275,421 |

| Corpus / candidate | Median seconds | Peak cgroup MiB | Peak process RSS MiB | Nodes / edges | Serialized JSON bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Code Mortis / direct | 1.215 | 81.9 | 90.6 | 3,252 / 3,283 | 8,962,901 |
| Code Mortis / Graphify | 4.508 | 226.3 | 232.4 | 3,184 / 15,312 | 5,169,954 |
| Noodle / direct | 1.227 | 77.0 | 83.6 | 3,831 / 5,998 | 13,330,769 |
| Noodle / Graphify | 6.176 | 487.7 | 502.9 | 4,078 / 16,571 | 4,932,933 |

Peak columns are maxima over the three runs. Cgroup v2 `memory.peak` includes memory charged to the container and all descendants, including tmpfs/page cache; RSS is reported separately and accounts shared mappings differently. Neither candidate spawned an extractor pool. Timing covers imports, extraction and JSON sizing, excluding Git export, Docker startup, database persistence, API latency, review work and image build. Host/page caches were not flushed, so these are empty-extractor-cache runs, not cold-machine measurements.

The outputs are **not equivalent graphs**: direct JSON includes 50,488 / 76,039 unresolved call-site records and expressions; Graphify returns more resolved/inferred relation kinds and omits an equivalent unresolved inventory. More edges does not establish more correct edges, and direct parsing does not demonstrate disk savings. Serialized JSON size is not a PostgreSQL size estimate.

Both grammars reported one partial file in Code Mortis (`frontend/src/settings.tsx`) and five in Noodle; exact paths and warnings are in the evidence. Zero `failed_sources` from Graphify must not erase these warnings. Three successful runs establish local feasibility only, not maximum capacity, compiler correctness, or production stability.

## Phase 1 defaults and boundaries

These are implementation decisions, not settings already added to the product:

| Area | Initial decision |
| --- | --- |
| Parser / relationships | Tree-sitter 0.25.2 with the tested grammar versions from the evaluation lock. Twelve-language syntax/definition tier; Python/TS/JS conservative relative-file dependencies. Unresolved calls/references stay explicit. No authoritative caller tool response until a resolver passes independent cases. |
| Update model | Full bounded rebuild per exact commit; atomic ready-snapshot publication. Preserve the previous ready snapshot on failure, always exposing its actual SHA. No cross-revision source substitution. |
| Isolation | A separate optional parser container, activated only with the feature. Follow the existing runner's explicit-context boundary without changing the native runner: no database/GitHub/model credentials, repository execution, Docker socket, or public port. Connect only the trusted worker and parser through a dedicated internal network for bounded input/output; deny external egress and access to database/Redis networks. Run non-root with read-only inputs, dropped capabilities and enforced process/memory limits. The worker owns authorization and jobs. Phase 1 must implement/test this transport and network boundary; the experiment uses the stricter offline `--network none` equivalent with mounted input. |
| Resource budget | One active extraction per instance, one CPU, 256 MiB total parser memory including children/tmpfs, 128 MiB temporary-space ceiling, 120-second job deadline. Enforce at the process/container boundary; timeouts/OOM must produce a visible failed/partial state and preserve prior evidence. |
| Admission limits | Start with 1 MiB per source file, 1,000 eligible source files and 8 MiB total source per snapshot. These conservative proposed ceilings exceed the largest measured corpus; they are not tested capacity guarantees. Oversized/excluded files and repository-limit failures need explicit status, never silent completeness. Revisit only with a measured corpus. The general evaluation harness permits up to 2,000 files / 32 MiB and is not the production admission policy. |
| Retention | At most two unpinned ready snapshots per repository: latest default-branch snapshot and most recent other requested revision. Expire the latter after seven days. Running consumers pin their exact snapshot; queue/cleanup must bound pinned/building snapshots before accepting more work. Retain required source/evidence citations with review records independently of graph cleanup. |
| Disk | Cap serialized extraction output at 32 MiB per snapshot before database insertion. Measure actual table/index/WAL/bloat and Git storage in Phase 1/2 before claiming a disk capacity. Do not infer DB memory needs from the JSON table. |
| MCP | Separate `/mcp/code`, `code:read`, independent off-by-default activation and repository consent. Revalidate current GitHub access on every read, including cache/traversal paths. Existing Reviews/Security grants and personal API tokens gain no implicit code access. |
| Tokens | Structural extraction consumes no model tokens. Retrieval added to prompts still consumes context; no savings claim until the Phase 2 review benchmark. |

The separate MCP resource follows the current domain/resource pattern in [server.py](../backend/code_mortis/mcp/server.py) and [auth.py](../backend/code_mortis/mcp/auth.py). Phase 3 must explicitly extend the domain types, discovery, settings, consent, redaction and permission tests. Reusing `/mcp/reviews` would blur raw-code consent with review-history access, so it is rejected for the initial implementation.

The 256 MiB result makes a modest parser plausible on an 8 GB deployment. **The full application on 8 GB remains unverified**, including concurrent review/security workers, PostgreSQL, Redis, runner, source checkouts and temporary files. That acceptance gate remains in Phase 2.

## Handoff

Phase 0 is complete: extractor comparison, capability matrix, adversarial/revision fixtures, bounded measurements and initial decisions are recorded. The subsequent [Phase 1 checklist and handoff](code-graph-plan.md) now records completed 1A contracts/persistence and the remaining parser, authorized indexing, retrieval and activation work. Preserve exact revision provenance and the limited capability tiers above. Do not copy the experimental script into the runtime without its missing authorization, durable state, admission, cancellation, retention and error contracts.

Validation: both fixture suites and twelve benchmark runs completed; the direct assertions and pinned Graphify regression observation passed. Ruff passes for the evaluation scripts. No full application test suite, hosted CI, real review/model benchmark, MCP integration, production deployment or 8 GB full-stack test was performed. No commit or push was made.
