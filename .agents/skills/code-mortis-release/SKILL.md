---
name: code-mortis-release
description: Prepare or audit a stable Code Mortis App release and synchronize its public documentation. Publish or recover the Docker Hub image and GitHub Release assets when the maintainer requests those actions.
---

# Code Mortis release

Read the repository guidance, installation instructions, and release workflow from the candidate checkout. Use the checked-out workflows and scripts for exact triggers, paths, and artifact names. [Repository guidance](../../../AGENTS.md) · [README](../../../README.md) · [Release workflow](../../../.github/workflows/release-images.yml)

## Prepare the release

- Verify the last published stable GitHub Release and its tag commit, and check Docker Hub for the corresponding image and any later image awaiting release assets. Confirm both surfaces before calling this the first release. Identify the candidate SHA and inspect the diff from the published baseline, including any in-scope working changes when preparing a local candidate.
- Use the requested stable version or propose `X.Y.Z` from the verified changes. Draft release notes and a matching changelog entry. Check the repository's version references and update those that track the App release. The Engine and Action have separate versions.
- Use the public surface map to audit each surface against the candidate. Update the affected material and include the outcome of the audit in the preparation handoff.
- Run the relevant local checks and record hosted CI for the candidate SHA separately. Mark missing checks as pending and distinguish workflow path-filter skips from failures. [Verify a Code Mortis change](../mortis-verify-change/SKILL.md)

## Public surface map

App paths are relative to the Code Mortis root. Site paths are relative to the `mortis-site` checkout.

| Surface | Files to review | What to reconcile |
| --- | --- | --- |
| README | `README.md` | Match supported features, setup commands, image tags, and downloadable files to `docker/Dockerfile`, the Compose files, `backend/code_mortis/manage.py`, and the release workflow. |
| Changelog | `CHANGELOG.md` | Summarize verified user-facing changes since the last release. Include migrations, configuration changes, and upgrade steps when applicable. Create the changelog with the candidate entry if the file is absent. |
| Repository agent guidance | `AGENTS.md` | Check repository layout, verification commands, and runtime invariants against the candidate and current workflows. Update instructions affected by the release. |
| Maintained skills | Tracked skills under `.agents/skills/` | Check each skill's commands, paths, and supported workflows against the candidate. Update the affected skills and record which ones were reviewed. |
| Public documentation site | `src/content/docs/docs/app/`, `src/content/docs/docs/index.mdx`, `src/components/ReviewDesk.astro`, `src/components/review-desk/content.ts` | Align the App guides, checked App revision, homepage examples, and release links. |

Match each change to its owning App guide.

| Change | App evidence | Site guide in `src/content/docs/docs/app/` |
| --- | --- | --- |
| Installation, environment, and upgrades | `.env.example`, `compose.yaml`, `compose.dev.yaml`, `compose.coolify.yaml`, `backend/code_mortis/manage.py`, `backend/migrations/versions/` | `deployment.md` |
| Automatic and incremental reviews, findings, and follow-up | `backend/code_mortis/reviews/`, `frontend/src/reviews.tsx`, `frontend/src/settings.tsx` | `reviews.mdx` |
| Security scans, Trivy, and coverage | `backend/code_mortis/security_scans/`, `docker/Dockerfile`, `frontend/src/security.tsx` | `security.mdx` |
| Code Graph indexing and review context | `backend/code_mortis/code_graph/`, `backend/code_mortis/reviews/graph_context.py`, `frontend/src/components/code-graph-settings.tsx`, `docker/sync-worker.sh` | `code-graph.md` and the affected review guidance |
| REST API and MCP | `backend/code_mortis/api/`, `backend/code_mortis/mcp/`, `backend/code_mortis/schemas/`, `collections/code-mortis/` | `mcp-and-api.md` |
| App overview and bundled Engine | `backend/pyproject.toml`, `backend/uv.lock`, `README.md` | `index.mdx` |

If a site route or heading moves, check `src/data/doc-sections.ts`, `src/data/doc-bookmarks.ts`, and `tests/site.test.ts` for navigation and bookmark compatibility.

## Update and validate the public documentation

- Locate `code-mortis/mortis-site`, which serves `codemortis.dev`. The checkout may be at `../mortis/mortis-site`, `../mortis-site`, or a path supplied by the maintainer. Confirm its remote, README, package scripts, and working-tree status before editing. If the checkout is unavailable, prepare the App changes and report the missing site work.
- Use the public surface map to choose the affected pages. Review their commands, recommended versions, and release links against the candidate.
- Use the site's current validation scripts. Its tests read `dist/`, so build before running them. Verify the affected rendered pages and links. Deliver the site diff alongside the App diff. Keep deployment of docs that link to new assets pending until those assets are public and the site deployment is authorized.

```sh
bun install --frozen-lockfile
bun run check
bun run build
bun test
```

## Publish when requested

- Confirm that the maintainer authorized publication. For preparation or audit, hand off the proposed tag, candidate SHA, release notes, both diffs, and verification results.
- Before pushing `vX.Y.Z`, verify the exact candidate SHA is on the current `origin/main` and the relevant hosted checks passed for that SHA. If required evidence is missing, obtain it using the existing workflows or report the blocker. Check the tag on GitHub and the target version in Docker Hub for a prior attempt before creating anything.
- A push of a stable `vX.Y.Z` tag starts `Release Docker image`. The workflow tests `linux/amd64` and `linux/arm64` separately and combines the tested digests under one `code-mortis:X.Y.Z` tag in the configured namespace. Code Graph ships in that image, and indexing is enabled per repository in Settings. The workflow smoke-tests a fresh install, then creates the GitHub Release using the matching `## X.Y.Z` section in `CHANGELOG.md` and attaches the two install files. Follow the workflow run for the tagged commit through publication.

## Verify and recover

- Inspect the public Docker Hub manifest with `docker buildx imagetools inspect` and confirm both Linux architectures and the release digest. Download `code-mortis-vX.Y.Z-compose` from the successful run for the tag. Confirm `compose.yaml` references that release image and `manage.py` comes from the same artifact.
- Smoke-test a fresh install in a disposable Compose project with a generated `.env` and an unused loopback port. For a loopback HTTP origin, generate the environment with a placeholder HTTPS `--webhook-url` such as `https://webhooks.example.test/code-mortis` so the setup form can load. Pull the published image, start the stack without a build, and verify `/health/ready` and the initial setup page without registering a GitHub App. Remove only the disposable project's resources after the check. Verify the `compose.yaml` and `manage.py` download links on the GitHub Release created by the workflow.
- For a partial publication, match the tag commit, workflow run, and public image digest before choosing what to retry. If the image is already public, preserve the tag and reuse the verified artifact to finish the missing step. With the original verified digest artifacts still available, the publish job can recreate a missing Compose artifact when the manifest matches. If the manifest differs or the required release evidence cannot be recovered, report the blocker before changing public assets.
- Report the release tag and SHA, image digest and platforms, smoke-test result, GitHub asset links, and the status of the site diff and deployment.
