# Acceptance record

Implementation session: 2026-10-06, America/Santiago. This record distinguishes implemented code, local integration checks and authenticated external evidence. The complete project is **not yet accepted**: real account authorization, reviews and final server deployment are outstanding.

## Implemented scope

| Capability | Implementation |
| --- | --- |
| Separate environments | Production Compose serves compiled React through FastAPI; development Compose has Docker Vite/HMR, backend reload, independent database/secrets/profile volumes and Smee relay. |
| GitHub flows | Independent registration, Authlib PKCE login and installation callbacks; per-attempt browser binding, expiry and single-use consumption; membership and current repository permission checks; recoverable UI errors. |
| Shared API connections | Existing encrypted CRUD and OCR tool-call testing retained; 29 pinned native presets plus OpenCode and custom protocols. |
| Personal accounts | Official Codex device authorization and Claude Code login console; owner-only status/cancel/disconnect, private persistent profiles, no generic-token conversion. |
| Subscription automation | Explicit repository consent, owner access revalidation, per-connection serialization, blocked quota/session states, cancellation on revoke and no silent fallback. |
| Unified review history | OCR review and OCR delegation use the same normalized runs, findings and publisher; application-counted file coverage and checkpoints; independent publication state. |
| GitHub comments and resolution | Existing COMMENT publication, reconciliation markers, immutable occurrences, thread resolve/reopen and periodic recovery retained. |
| UI and operations | Personal connection screens, explicit consent, manual connection selection, blocked-run resume and progress; additive migration, upgrade utility, backups and diagnosis documented. |

Native clients enforce elapsed time and explicit input-context bytes. They do not expose OCR's hard token limit; this difference is visible in repository settings and documentation. The pending live checks below include native model behavior and malicious-context isolation, not just transport startup.

## Completed local checks

| Check | Evidence | What it does not establish |
| --- | --- | --- |
| Backend | 40 pytest cases passed against isolated PostgreSQL/Redis. Ruff passed. | External GitHub or model availability. |
| Browser | 24 Playwright checks passed on built frontend, desktop 1440×1000 and mobile 390×844. Document and dialog overflow checked; secret input, native OSC links, console close, recovery and member visibility exercised. | Real provider authentication; these API responses are fixtures. |
| API contract | OpenAPI exported and TypeScript regenerated; frontend production build passed. | Runtime external protocol compatibility. |
| Real OCR | OCR 1.12.11 completed tool-call probe and trusted-base review against a deterministic local Anthropic endpoint in a network-disabled, read-only container. Delegation preview/rule scope and checkpoint reuse passed. | Real model inference or native subscription review. |
| Provider catalog | Compared every native preset's protocol/base URL to OCR v1.12.11 commit a758d9cbfb689937c7857ad64b2dd66adb58c0c2; all 29 matched. OpenCode and custom are additional entries. Four configured protocol paths have local tests. | Every remote provider's credentials/models. |
| Native binaries | Codex 0.160.1 and Claude Code 2.1.292 version checks, strict Codex app-server initialization and empty-profile account status passed inside the runner. | A connected subscription. |
| Native authorization startup | Real internet calls started and cancelled ChatGPT device authorization. Official Claude login reached its authorization URL and cancelled with terminal process/lock cleanup. No account was authenticated and no paid review was run. | Successful consent, renewal or inference. |
| Docker development | All services started from a fresh independent database. Same-origin health/API proxy and real Vite CSS HMR passed at localhost:5173. Official Smee client connected to a dedicated channel. | Delivery from the development GitHub App. |
| Development bootstrap | The actual `.env.dev` token was accepted through Vite with HTTP 200. The user completed GitHub App registration; the API now reports `configured: true`, no configuration errors, and the browser shows Continue with GitHub. Two focused desktop/mobile checks verify that the form sends the token in `x-setup-token` while the JSON body contains only `organization`. | Successful login, installation or webhook delivery. |
| Docker server | A disposable copy of production Compose started at localhost:8089 with fresh credentials/volumes. Compiled frontend, diagnostics, migrations and all service health checks passed. Database and runner-file markers survived service restarts and were removed afterward. | Public HTTPS deployment or production account integration. |
| Existing-data upgrade | Restored a logical backup from an offline copy of the stopped original PostgreSQL volume into a separate database, then migrated to 20261006_connections. Fingerprints verified 59 existing records across 14 tables unchanged. Old OAuth attempts were intentionally invalidated. | Existing GitHub registration updates or a live login after upgrade. The original instance had zero API connections, runs and findings; simulated tests cover those objects. |
| Frontend finish | Fresh Impeccable reviewer returned `ship` for the local UI extension with no material fixes. Screenshots were validated; incumbent typography/tokens retained. | Whole-product or authenticated integration acceptance. |
| Design documentation | Independent documenter verified all 40 light/dark semantic colors and 51 sidecar color keys against the implementation; no checked drift, no design files changed. | Uninspected visual variants or external behavior. |

Non-blocking local warnings: upstream Authlib/Starlette deprecate their httpx integration in favor of httpx2; Vite reports a minified chunk above 500 kB. Neither warning was suppressed. CI configuration now includes native-client container checks; hosted CI has not run for this uncommitted working tree.

## Reproduce container checks

```sh
docker build -t code-mortis:test .
docker run --rm --network none --read-only --tmpfs /tmp:mode=1777,size=256m --tmpfs /home/mortis/.opencodereview:uid=10001,gid=10001,mode=0700,size=32m code-mortis:test python tests/ocr_container_smoke.py

docker build -f Dockerfile.runner -t code-mortis-runner:test .
docker run --rm --network none --read-only --tmpfs /tmp:mode=1777,size=256m --tmpfs /profiles:uid=10001,gid=10001,mode=0700,size=64m --tmpfs /home/mortis:uid=10001,gid=10001,mode=0700,size=64m code-mortis-runner:test python -m tests.native_runner_smoke
```

For official auth-start/cancel checks, remove `--network none` from the last command and append `--auth-start`. This still uses temporary empty profiles; it never reads the operator's profiles. Full authenticated reviews must use the app and its own persistent connections.

With an unconfigured development stack running, `cd frontend && bun tests/dev-smoke.mjs` verifies HMR and the actual API proxy. For restore verification, follow the isolated-copy procedure in operations and use `backend/tests/restore_smoke.py`; it refuses other source/target container names and refuses overwriting an existing dump/database.

## Required external acceptance, still pending

The user designated **wilfredinni/noodle** for review tests and reported having some provider accounts. The available open PR observed via the read-only GitHub CLI was [#259](https://github.com/wilfredinni/noodle/pull/259). No test comment has been published in this implementation session. Available account types still need confirmation; do not paste keys into chat.

| Scenario | Remaining evidence |
| --- | --- |
| New development App | Registration completed. Complete login/install from localhost:5173 and receive its signed webhook through the relay. |
| Existing production App | Apply the documented registration fields, then login and installation discovery without recreating App/data. |
| OAuth failures | Real cancellation, expired/replayed callback and concurrent tabs. Local deterministic cases pass. |
| OpenAI API | Connection tool round trip, manual and automatic review, published COMMENT review URL. |
| Claude API | Same evidence using the Anthropic protocol. |
| OpenCode API | Same evidence with the protocol appropriate to its selected model. |
| Compatible endpoint | Same evidence against the user's actual compatible server. |
| ChatGPT subscription | Complete device consent, verify reconnect/renewal, manual and explicitly consented automatic reviews on server, quota/account status, comments. |
| Claude Code subscription | Complete native terminal login, verify reconnect/renewal, manual and explicitly consented automatic reviews on server, comments. |
| Security isolation | Real native review of adversarial repository instructions; verify no unauthorized file access, commands, writes or cross-account consumption. Local permission/env/schema tests pass. |
| Recovery and resolution | Actual interruption, lost publication response, new commit, repeated webhook, GitHub-to-app and app-to-GitHub thread resolution/reopen with periodic repair. Mocked failure paths pass. |
| Final deployment | Configured remote server with HTTPS, private runner networking, backup/profile restoration and repeat manual/automatic smoke after upgrade. |

A missing account, permission or quota leaves that row pending. It does not remove the feature from the agreed scope and does not count as a passing integration test.
