# Code Mortis implementation plan

The approved delivery is the complete self-hosted GitHub review product, in development and on a server. Shared APIs and personal ChatGPT and Claude Code subscriptions are all in scope for manual and automatic reviews. Finishing a phase does not finish the project. External integrations remain unverified until exercised with real accounts.

## Architecture

Keep FastAPI, Pydantic, SQLAlchemy, Alembic and PostgreSQL; Celery/Redis with separate review and synchronization queues; React, TypeScript, Vite, React Router, TanStack Query, Tailwind CSS and shadcn/ui. Use locked dependencies and exact OCR/official-client versions.

Production serves the compiled frontend and API from one HTTPS origin. Development uses an independent Compose project with Vite HMR in Docker at localhost:5173, backend reload, separate secrets/volumes and a separate GitHub App. `CM_PUBLIC_URL` is the callback origin; `CM_WEBHOOK_URL` is the independently reachable webhook destination. Native clients execute on the server with persistent per-connection profiles and no database or GitHub secrets.

## Delivery and acceptance

| Phase | Required result | Required evidence |
| --- | --- | --- |
| 1. Foundation | Capability inventory and separate complete server/development environments | Fresh startup, HMR and restart persistence |
| 2. GitHub | Protected registration, Authlib PKCE login, independent browser-bound single-use transactions, installation callback, permissions and recoverable errors | Real new-App and existing-App flow; multiple tabs, replay, expiration, cancellation, revocation and external installation |
| 3. Subscriptions | Official Codex app-server device authorization and native Claude Code authentication, status, cancellation and disconnect | Both clients authenticate and complete a delegated review on the server |
| 4. Unified reviews | OCR APIs and delegated subscriptions share normalized history and publication | Actual PR comments through OpenAI API, Claude API, OpenCode, a compatible endpoint and both subscriptions |
| 5. Recovery | Personal repository consent, permission revalidation, serialized consumption, quotas, cancellation, checkpoints and durable jobs | Manual/automatic reviews, worker restart, uncertain publication, duplicate webhook and commit changes without duplicate comments |
| 6. Complete UI | Connections, repositories, PRs, runs, findings and actionable operational states | Keyboard, desktop and mobile workflows, including failure recovery |
| 7. Upgrade | Additive migrations, existing-App update, documentation, backup and restoration | Clean installation and existing-data upgrade, then final deployment |

## Required behavior

- GitHub provides application identity and current repository permissions. Numeric membership identity survives username changes. An `installation_id` alone grants no access. App registration, login and installation are separate; request_oauth_on_install is disabled.
- Shared API connections support the pinned OCR catalog and OpenAI Chat Completions, Responses, Anthropic and Bedrock protocols, including OpenCode and compatible endpoints. Connection tests exercise tools. Reject incompatible settings before execution.
- Personal credentials stay under official-client management. The app exposes device authorization or an owner-only, expiring console for the exact native login command. It provides no general shell, custom Claude OAuth or generic API-key conversion.
- The owner explicitly authorizes each repository consuming a personal connection, including automation. Admin selection does not substitute for consent. Each run records owner, initiator, trigger and immutable configuration. Revalidate before execution. One personal execution at a time; quota, authentication and availability failures never switch provider silently. Revocation stops new consumption and cancels affected work.
- OCR selects files and trusted-base rules. Application-assigned result groups determine verifiable coverage. Validate schema, paths and line ranges. Preserve complete, partial, failed, skipped, superseded and cancelled states independently from publication.
- Untrusted PR content cannot load scripts, hooks, agent configuration or MCP servers. Native tools only read authorized context and cannot write or execute commands.
- Persist webhook deliveries and jobs before acknowledging them. Persist results before publishing COMMENT reviews. Reconcile uncertain writes before retrying; publication retries do not repeat inference. Check head commits again before publication.
- Findings have current state and immutable historical occurrences. Missing later means not observed. Resolve/reopen in either system, dismiss with a reason, display pending GitHub changes and recover lost events periodically.
- Preserve existing users, repositories, API credentials and history through additive migration. Document profile restoration/reconnection and structured diagnosis without secrets.
- Keep English UI and configurable comment language. Public SaaS, billing, GitLab and automatic code changes are excluded.

## Definition of done

All capabilities above work in development and on the server; both personal subscriptions complete manual and automatic reviews; the acceptance matrix has actual evidence. Mocked tests and unauthenticated client startup are recorded separately. Missing external accounts, permissions or quota mean pending validation, not completion. See [acceptance record](docs/acceptance.md).
