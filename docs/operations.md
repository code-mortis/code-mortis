# Operations

## Service checks

`GET /health/live` checks the API process. `GET /health/ready` checks PostgreSQL and Redis connectivity. Neither proves workers, GitHub, or a model provider are healthy. Use `docker compose ps`, `docker compose logs --tail=100 review-worker sync-worker scheduler`, the connection test, and Settings' background errors to inspect those.

Queued reviews usually mean the review worker or scheduler is stopped. A failed connection test means OCR could not complete a tool call using that connection. A publication failure can be retried from the run page without paying for another model review. Failed resolution can be retried from the finding panel.

Worker logs contain job IDs, kinds, and error classes. Raw provider output is not logged or retained. Configuration snapshots contain provider metadata and public options, but no API keys or extra-header values. Do not put credentials into model identifiers, background text, rules, provider options, or URLs.

## Backup and restore

PostgreSQL is the source of truth. Redis can be recreated because pending work is persisted in PostgreSQL. Back up `.env` separately in encrypted storage; database ciphertext is unrecoverable without `CM_ENCRYPTION_KEY`. A SQL backup contains sensitive review data even though credential fields are encrypted.

Create a backup with private permissions:

```sh
umask 077
docker compose exec -T postgres pg_dump -U mortis -d mortis -Fc > mortis.dump
```

Restore into a new, empty installation using the original encryption key. Start only PostgreSQL and Redis, restore, then start the app services. Do not start review workers until the restore is complete.

```sh
docker compose up -d postgres redis
docker compose exec -T postgres pg_restore -U mortis -d mortis --no-owner --exit-on-error < mortis.dump
docker compose up -d
```

Test restored instances behind a private network before accepting webhooks or dispatching jobs. Disable or remove pending jobs only as an intentional operator action after inspecting them. Never run two active copies of the same restored instance against GitHub.

## Upgrades

Back up first. Pull the application version you intend to deploy, rebuild, and run migrations before starting the new API and workers. Stop workers for schema-changing upgrades and allow in-flight work to finish or cancel it in the app. `docker compose run --rm migrate` applies Alembic migrations. The image pins OCR and dependency lockfiles so engine and schema upgrades are explicit.

Keep one scheduler. Container scaling alone does not make simultaneous review jobs safe: each review worker has a single OCR configuration directory, so its concurrency must remain one. A future distributed review runner would need job-level isolation and coordinated cancellation.

## GitHub permissions and access

The app requests Contents read, Pull requests write, and Metadata read. Installation events are implicit GitHub subscriptions. Pull request, review comment, and review thread events are explicit. Only installations connected by an instance administrator are processed.

An allowed team member still needs access to each repository on GitHub. Manual reviews and resolution changes require push access; instance configuration also requires the Code Mortis administrator role. Removing a team member disables their tokens and sessions. The initial owner is bound to their numeric GitHub ID after first sign-in, preventing later username reuse from granting access.

Resolution webhooks are reconciled against GitHub's current thread state instead of trusting event order. Missed events are repaired periodically. Findings without inline threads are resolved locally. If a remote thread is deleted, synchronization reports failure and retains history rather than pretending the deletion resolved the finding.

## Deployment boundaries

This release is for an owner and a trusted, explicitly allowed team. It is not a multi-tenant service. Keep the database, Redis, and worker networks private. The application port binds to loopback; terminate HTTPS at your reverse proxy. Keep secure cookies enabled on remote deployments. The bootstrap token is accepted only until the GitHub App is configured.

Repository content and model output are untrusted. The application never runs repository scripts or applies generated changes. Resource budgets limit review cost and duration; model output can still be wrong and needs human review. API provider keys should have the least privileges and budget needed for reviews.
