# Code Mortis

Self-hosted GitHub pull request reviews and repository security scans powered by [Mortis Engine](https://github.com/code-mortis/mortis-engine).

![Code Mortis overview with sample review findings, security findings, and repository activity](assets/code-mortis-overview.svg)

## Features

- Automatic and on-demand PR reviews with inline comments and GitHub checks from your own GitHub App. Incremental automatic reviews can reuse validated coverage for unchanged files.
- Repository security scans combining AI analysis with Trivy checks.
- Findings triage, run history, coverage, and model usage in one workspace.
- Shared model connections for OpenAI-compatible APIs, Anthropic, and Amazon Bedrock.
- Built-in code graph indexing for PR context and code navigation, enabled per repository in Settings.
- Scoped REST API tokens and one MCP connection for reviews, security, indexed code, and optional management. A [Noodle collection](collections/code-mortis/README.md) is included for the REST API.

## Connect an MCP client

In **Settings → MCP access**, enable MCP and the tool groups your client needs. Copy the server URL (`https://your-host/mcp`) into a Streamable HTTP client and complete OAuth in the browser. Choose each group's permissions and repositories on the consent screen. Code tools require an enabled repository graph. Only administrators can grant instance management, which applies across the instance.

Code Mortis supports Skills, Tasks, and stateless elicitation on MCP revision `2026-07-28`. The server publishes three read-only guides for explaining a PR review, prioritizing saved security findings, and tracing change impact between indexed commits. Discover them through MCP Skills or call `get_workflow_guide` to list the guides available to your grant. Pass a guide name to read it.

Clients that support MCP Tasks can follow manual reviews, repository scans, and repository background drafts after reconnecting. Grant both read and run access for the relevant group. Other clients receive the saved run, scan, or job ID. Use that ID with `reviews_get_run`, `security_get_scan`, or `reviews_get_background_status`, respectively. Background generation requires an administrator.

On clients supporting stateless elicitation, a scan can ask which shared connection to use, and a finding triage action can ask for a missing reason. An issue publication can open the existing Code Mortis approval page. You can also supply the fields directly and use the approval link returned by the tool.

Upgrading from v0.1.2 or earlier turns MCP off, revokes the old grants, and cancels unfinished work authorized by those grants. If you need the results of pending MCP jobs, wait for them to finish before upgrading. Afterwards, re-enable MCP and the desired tool groups, then replace the old client entries with one connection to `/mcp` and authorize it.

Each API process allows two concurrent MCP transport or OAuth setup requests (`CM_MCP_MAX_CONCURRENT_REQUESTS`). When busy it returns HTTP 429 with `Retry-After: 2`. Its separate database pool defaults to six connections (`CM_MCP_DB_POOL_SIZE`), zero overflow, and a one-second checkout timeout. Include this limit for each API process when sizing PostgreSQL alongside web and worker pools. Browser administration and OAuth endpoint discovery remain available during MCP overload.

## Develop locally

Use Docker Compose and Python 3.12+. Create a [Smee channel](https://smee.io/) to relay GitHub webhooks to your local stack.

```sh
python3 backend/code_mortis/manage.py init-env \
  --output .env.dev --owner YOUR_GITHUB_LOGIN \
  --url http://localhost:5173 --webhook-url https://smee.io/YOUR_CHANNEL

docker compose --env-file .env.dev -f compose.dev.yaml --profile webhooks up -d --build
```

Open [localhost:5173](http://localhost:5173), enter `CM_BOOTSTRAP_TOKEN` from `.env.dev`, and register a development GitHub App. The frontend and API reload on edit. Restart the affected worker or scheduler after changing its code.

The engine comparison, code graph benchmarks, and PR navigation benchmark live
in the companion `mortis-benchmarks` project, alongside their recorded results.

## Install from Docker Hub

Download `compose.yaml` and `manage.py` from the same [GitHub release](https://github.com/code-mortis/code-mortis/releases) and put them in one directory. The Compose file pulls the versioned [Code Mortis image on Docker Hub](https://hub.docker.com/r/codemortis/code-mortis/tags) for Linux `amd64` and `arm64`.

Use Docker Compose and Python 3.12 or newer. Point an HTTPS reverse proxy at `127.0.0.1:8080` and use its public origin when initializing the environment.

```sh
python3 manage.py init-env --owner YOUR_GITHUB_LOGIN --url https://reviews.example.com
docker compose pull
docker compose up -d --wait
```

Open the configured URL and enter `CM_BOOTSTRAP_TOKEN` from `.env`. Register and install your GitHub App, then add a shared AI connection. Code Graph ships in the image. Enable it in repository settings to start indexing. See the [deployment guide](https://codemortis.dev/docs/app/deployment/) for the full setup.

To upgrade an existing deployment or move from source builds to the release image, back up PostgreSQL and `CM_ENCRYPTION_KEY`. Replace `compose.yaml` and `manage.py` in the existing project directory, keeping its `.env` and volumes. Run `docker compose pull` followed by `docker compose up -d --wait --remove-orphans`. The migration service applies any database upgrades, and `--remove-orphans` removes the retired graph containers from older source deployments.

For optional Sentry reporting in App v0.1.1 or later, set the public `CM_SENTRY_FRONTEND_DSN` in `.env` and recreate the API container. Set `CM_SENTRY_DSN` separately for backend reporting. `CM_SENTRY_ENVIRONMENT` and `CM_SENTRY_RELEASE` apply to both.

## Deploy with Coolify

Point an HTTPS hostname at your Coolify server and run its Traefik proxy. Create a Git-based Docker Compose application with the repository root as the base directory and `/compose.yaml` as the Compose location. Enable **Raw** Compose deployment so the overlay provides the proxy labels and selects the published App image. [Coolify Docker Compose guide](https://coolify.io/docs/applications/builds/docker-compose)

Generate the environment from a local checkout:

```sh
python3 backend/code_mortis/manage.py init-env \
  --owner YOUR_GITHUB_LOGIN --url https://reviews.example.com
```

Copy the generated `.env` values into Coolify’s **Environment Variables** and add `CM_PUBLIC_HOST=reviews.example.com`. Use the same hostname in both settings. Keep `.env` out of Git and back up `CM_ENCRYPTION_KEY` with your database backups.

Set **Custom Build Command** to:

```sh
docker compose -f compose.yaml -f compose.coolify.yaml pull api
```

Set **Custom Start Command** to:

```sh
docker compose -f compose.yaml -f compose.coolify.yaml up -d --no-build --wait --remove-orphans
```

For an existing deployment, keep the same Coolify resource, environment variables, and mounted volumes. The API and workers wait for database migrations on startup. Coolify checks that each worker responds and that the scheduler can reach Redis and has updated its schedule recently. For a new deployment, open your HTTPS URL, enter `CM_BOOTSTRAP_TOKEN`, register and install your GitHub App, add a model connection, and enable reviews in repository settings.

To upgrade the App in Coolify, set the shared image anchor in `compose.coolify.yaml` to the published version you want to deploy, then redeploy the existing resource. With App v0.1.1 or later, set `CM_SENTRY_FRONTEND_DSN` in Coolify's runtime environment and redeploy to change browser reporting.

## Publish a Docker image

The public image repository is `codemortis/code-mortis`. Set the GitHub Actions variable `DOCKERHUB_NAMESPACE` to `codemortis`, and configure `DOCKERHUB_USERNAME` and the `DOCKERHUB_TOKEN` secret with write access to the repository.

Add release notes under the matching `## X.Y.Z` heading in `CHANGELOG.md`, then push the stable tag `vX.Y.Z` from `main`. The workflow tests both Linux architectures, publishes the versioned Docker Hub image, and checks a fresh install. It then creates the GitHub Release with those notes and attaches `compose.yaml` and `manage.py`.

Licensed under [Apache 2.0](LICENSE).

[Sponsor Code Mortis on GitHub](https://github.com/sponsors/wilfredinni).
