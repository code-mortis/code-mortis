# Changelog

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
