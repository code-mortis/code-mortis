@AGENTS.md

## graphify

Local checkouts can have a generated knowledge graph in `graphify-out/`.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code in a checkout with a graph, run `graphify update .` to keep it current (AST-only, no API cost).

## Subagent coordination

Maintainer checkouts can have local agent definitions in `.claude/agents/`. Use the roles below when configured.

Follow AGENTS.md. When the maintainer planning documents are available, use
`docs/README.md` to find them and read `docs/plans/engine/plan.md` before engine work.
Verify the owning source. Mortis Engine uses shared API connections. OCR and subscriptions are historical.
Preserve saved results and distinguish personal adoption from experimental acceptance.

In the primary chat, coordinate substantial development through the project agents.
Handle small, self-contained changes directly. Use explorer only when mapping is needed.
Give implementer the required behavior, acceptance criteria, owned files/modules and
relevant constraints. Include caller, contract, migration and regression-test changes.
Keep one writer per file; do not edit files while another agent owns them. A worktree
does not isolate shared databases, Redis, ports or services.

After implementation, delegate independent review of the stable diff and relevant callers:
workflow-reviewer for jobs, inference, persistence, review/security lifecycle or migrations;
security-reviewer for authorization, tokens, MCP, endpoints or source/process isolation;
contract-ui-reviewer for API/schema/frontend changes. Run only relevant reviewers.
Wait for their results, assess the evidence, and return confirmed fixes to implementer.
Use verification-runner for final checks after fixes. Delegate further review only when
the fixes introduce new risks or require checking a previous finding.

Assign exactly one owner of local database-backed tests at a time, including tests run
by the primary agent or implementer; transfer ownership explicitly to verification-runner.
Backend tests truncate shared mortis_test tables and clear the isolated Redis cache.
Subagents complete only their assignment and return to the parent; do not recursively
delegate. If a configured role/model is unavailable, report it and keep the work with
the coordinator rather than silently substituting a model or claiming an agent ran.

Require file/line evidence and a reachable failure scenario for review findings. Separate
confirmed defects from uncertainties, and mocked/local checks from live integration.
Do not publish, resolve GitHub issues/review threads, deploy, run live provider evaluations,
or start long benchmarks without explicit authorization for that action in the task.
The coordinator owns the final integration and reports checks, limitations and open risks.
