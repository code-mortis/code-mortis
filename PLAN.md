# Code Mortis implementation plan

Build an MIT-licensed, self-hosted GitHub PR review application. Each instance has an owner, allowed team members, its own GitHub App, and shared API connections. Work is organized around repositories, pull requests, historical runs, findings, and resolution activity.

## Agreed stack

- FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL.
- Celery and Redis, with separate review and synchronization queues. PostgreSQL owns job state and recovery.
- React, TypeScript, Vite, React Router, TanStack Query, **Tailwind CSS and shadcn/ui**. Bun and uv manage dependencies.
- Pinned Open Code Review CLI 1.12.11; call it as an external process rather than importing internal Go APIs.
- Docker Compose for self-hosting, one backend image reused by API, workers, and scheduler. FastAPI serves the frontend build.

## First release

1. Bootstrap-token-protected setup, GitHub App manifest registration, installation discovery, GitHub login, explicit membership, and repository permission checks.
2. Admin-managed encrypted API connections covering OCR's native providers and supported custom protocols, including OpenCode API. Repository settings choose connection, model, effort, exclusions, review language, business context, and limits.
3. Manual and automatic full-PR reviews. Pin base/head SHAs and settings for each run. Ignore draft PRs automatically, require a repository writer for external-fork runs, and supersede stale runs.
4. Preserve normalized findings and sanitized OCR coverage/output. Distinguish completed, partial, failed, cancelled, superseded, and skipped execution from independent publication state.
5. Publish COMMENT reviews with validated inline locations and a summary. Use stable markers and reconcile before retries. Never rerun OCR merely to retry GitHub publication.
6. Separate stable findings from immutable per-run occurrences. Conservatively match repeated findings. Missing from a later run means not observed, never automatically fixed.
7. Resolve/reopen in either app or GitHub, with durable pending actions, canonical reconciliation, and activity records. Dismissal is an explicit app reason that resolves a linked thread. Findings without a GitHub thread have local status.
8. Compact repositories, PRs, runs, finding details, and settings UI. Support keyboard interaction, responsive layouts, and system/light/dark themes. No fabricated production data.
9. Operational docs, migrations, backups, structured logs, health checks, pinned images, and focused automated checks.

## Security boundaries

GitHub remains application identity. Read access requires current repository access; run/status mutations require write access. Installation tokens perform bot writes. Validate webhook signatures, deduplicate delivery IDs, and persist work before acknowledging it. Encrypt credentials with an operator-managed key outside PostgreSQL. Do not expose credentials in logs or API responses. Only trusted instance configuration controls provider endpoints and tools. Review repository data without executing hooks, project scripts, submodules, or project-controlled MCP servers. Use explicit base-revision review rules.

## Following release

Personal OpenAI/ChatGPT and Claude Code subscription connections for manual reviews. Automatic reviews keep shared API connections. Use official Sign in with ChatGPT/Codex app-server, official unmodified Claude Code authentication, OCR delegation, isolated read-only runners, and the existing normalized result/publication pipeline. No inactive sign-in controls in the first release.

## Validation

Test access control and revoked access; invalid/duplicate/out-of-order webhooks; complete/partial/failed/malformed OCR output; cancellation and commit changes; interrupted jobs and uncertain GitHub writes; publication-only retries; both resolution directions; immutable history; safe paths and credentials; protocol adapters; clean migration and backup restoration; and the primary browser workflows. A live provider and GitHub App are required to claim live end-to-end integration.

Public SaaS, billing, auto-fixes, GitLab, and mascot work are excluded.
