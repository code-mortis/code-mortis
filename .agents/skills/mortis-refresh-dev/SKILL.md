---
name: mortis-refresh-dev
description: Refresh the Code Mortis local Docker development stack after source, dependency, environment, or migration changes. Select affected services, establish a safe drain window, and verify readiness. Use for requested local refreshes, not routine code editing or production deployment.
---

# Refresh Code Mortis development

Run from the Code Mortis repository root. Read `AGENTS.md`, the local setup in
`README.md` and the affected service definitions in `compose.dev.yaml`.

## Establish the target and scope

- Use `docker compose --env-file .env.dev -f compose.dev.yaml` for every development
  command. The local setup in `README.md` generates `.env.dev` for the instance.
- Inspect `git diff`, the requested revision, service state and enabled profiles.
  `config --services` and `ps` are sufficient for inventory; do not print resolved
  environment configuration or `.env.dev`, which can expose credentials.
- A refresh request authorizes the necessary local refresh, not paid test reviews,
  production changes, deleting volumes, changing automation settings, or a cutover.
  Never run the historical retirement commands as routine refresh steps.

## Select the smallest refresh

| Changed area | Required consideration |
| --- | --- |
| Mounted frontend source | Vite HMR normally suffices; check the browser before restarting. |
| Mounted API source | Uvicorn reload normally suffices; the same code may also be imported by workers. |
| Review, security, verification or summary workflows | Restart affected long-lived Celery consumers after draining. |
| Shared inference, settings or models | Trace imports across review, security, verification, summary and other affected workers; API reload alone is insufficient. |
| Jobs, queues or scheduling | Inspect `backend/code_mortis/jobs.py` routing and beat schedule; include affected consumers and scheduler. |
| Dependency lockfiles or Docker build inputs | Rebuild and recreate affected services; restart alone retains the old image. |
| Environment or Compose settings | Recreate affected services; restarting does not apply changed container configuration. |
| Additive migrations | Check upgrade compatibility, drain affected work and back up local data and its encryption key. Run the `migrate` service with the same development Compose options and verify successful completion before starting affected consumers. |
| Graph parser/extraction | Check `EXTRACTOR_VERSION` in `backend/code_mortis/schemas/code_graph.py`, parser/worker compatibility, and enabled graph profile. Reindexing is separate work; never relabel old snapshots. |

Do not start an optional service merely because it appears in Compose. Preserve the
existing profiles and pre-refresh running/stopped state of unrelated services.

## Drain before replacement

Inspect durable queued/running Jobs and Celery active/reserved/scheduled tasks using
the existing authorized environment access. Read `backend/code_mortis/jobs.py` for
current queue ownership and dispatch behavior. Missing worker replies are unknown,
not proof that the queue is idle. API readiness is not worker readiness.

If work is active or fresh work can be admitted during replacement, establish a
bounded quiet window. Let work finish and temporarily suspend the relevant admission
and dispatch paths where necessary. Record what must be restored.
Recheck immediately before replacement. An idle snapshot alone does not fence a
concurrent webhook, scheduled scan or retry. Stopping beat alone does not fence API/webhook
admission or already queued dispatch/sync work. Do not cancel active work, kill requests,
clear reservations, purge queues, or change persistent automation settings to force a
drain. If draining requires an additional decision or interrupted paid work, report
the concrete blocker rather than forcing the refresh.

Execute the targeted restart/recreation only after that condition is satisfied.
Preserve data volumes and encryption keys. Restore temporary admission/dispatch changes
and original service states even if the refresh fails; report any restoration failure.

## Verify and report

Check target container state, migration outcome, bounded redacted logs, API readiness,
and replies from the expected workers/queues. Verify the requested source or image
is actually loaded. Use existing harmless UI/read endpoints if needed; do not press
Review, Scan, Test connection, Resume or Publish to prove a refresh succeeded.

Report the revision, affected services, drain evidence, actions, restoration and
readiness checks. List unresolved items. Healthy containers establish startup only,
not real provider/GitHub integration or review quality.
