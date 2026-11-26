# Changelog

## 0.1.2

- PR summary comments stay pending while Code Mortis retries temporary GitHub server errors. If all five sync attempts fail, the App shows the error.
- The repository list, PR inbox, analytics, and security findings use fewer database queries or smaller parameter lists when loading large result sets.
- Coolify now checks each worker and the scheduler. The sync worker's health check covers both repository sync and Code Graph indexing.
- The App bundles [Mortis Engine 0.1.7](https://github.com/code-mortis/mortis-engine/releases/tag/v0.1.7) and preserves line references when resuming saved review plans. The first automatic incremental review after upgrading analyzes eligible files within the configured limits to establish a new baseline.

### Upgrade

Back up PostgreSQL and `CM_ENCRYPTION_KEY`. Download `compose.yaml` and `manage.py` from the v0.1.2 GitHub Release into your existing Compose project, keeping its `.env` and volumes. Run `docker compose pull` followed by `docker compose up -d --wait --remove-orphans`. The migration adds an index for publishing review findings. The release image is `docker.io/codemortis/code-mortis:0.1.2` for Linux `amd64` and `arm64`.

For Coolify, select the v0.1.2 image in `compose.coolify.yaml` after publication and redeploy the existing resource. [Coolify deployment instructions](https://github.com/code-mortis/code-mortis#deploy-with-coolify)

## 0.1.1

- Configure browser Sentry reporting with `CM_SENTRY_FRONTEND_DSN` in the running App. Recreate the API container after changing it. The same image can be used across deployments with different Sentry settings.
- Code Graph refreshes back off and retry when GitHub rate-limits requests or returns a temporary server error. The graph status now distinguishes those failures from revoked repository access. GitHub App token exchanges and PR review-thread lookups also retry temporary server errors.
- Bundle [Mortis Engine 0.1.6](https://github.com/code-mortis/mortis-engine/releases/tag/v0.1.6). The first automatic incremental review after upgrading analyzes all eligible files within the configured limits to establish a new baseline.
- Coolify deployments use the published App image for the API, migrations, workers, and scheduler.

### Upgrade

Download `compose.yaml` and `manage.py` from the v0.1.1 GitHub Release. Back up PostgreSQL and `CM_ENCRYPTION_KEY`, replace both files in your existing Compose project, and keep its `.env` and volumes. Run `docker compose pull` and `docker compose up -d --wait --remove-orphans`. The release image is `docker.io/codemortis/code-mortis:0.1.1` for Linux `amd64` and `arm64`.

For Coolify, use the [README instructions](https://github.com/code-mortis/code-mortis#deploy-with-coolify) and select the v0.1.1 image in your deployment overlay after it is published.

## 0.1.0

First stable release of the self-hosted Code Mortis App.

- Review GitHub pull requests on demand or automatically with your own GitHub App. Get inline comments, a check for each run, and a saved record of findings and file coverage. Opt in to incremental automatic reviews to reuse compatible coverage for unchanged files.
- Scan a pinned commit from the default branch with Mortis Engine and Trivy. Review dependency, secret, and configuration findings alongside AI findings. Triage findings, inspect coverage, and download Markdown or JSON reports.
- Navigate PR diffs and saved summaries, define repository review rules, and follow findings through subsequent commits. Track activity and model usage in the workspace.
- Enable Code Graph per repository to explore indexed imports and give reviews context from a matching revision. The graph worker and parser ship in the App image.
- Connect agents to scoped MCP endpoints for reviews, security, and indexed code. Use personal tokens and the Noodle collection for REST API access.
- Configure shared AI connections for OpenAI-compatible APIs, Anthropic, or Amazon Bedrock. This version bundles Mortis Engine 0.1.5 and Trivy 0.76.0.

### Install

Download `compose.yaml` and `manage.py` from the v0.1.0 GitHub Release. The Compose file uses `docker.io/codemortis/code-mortis:0.1.0` for Linux `amd64` and `arm64`. Follow the [deployment guide](https://codemortis.dev/docs/app/deployment/) to configure HTTPS, GitHub, and your AI connection.

If you already run a source deployment, back up PostgreSQL and the encryption key. Keep the existing `.env` and volumes when replacing the Compose file. The migration service applies schema upgrades on startup. Use `--remove-orphans` with `docker compose up` to remove the retired standalone graph containers.
