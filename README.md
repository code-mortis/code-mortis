# Code Mortis

Self-hosted GitHub pull request reviews and repository security scans powered by Mortis Engine.

## Features

- Automatic and on-demand PR reviews with inline comments and GitHub checks from your own GitHub App.
- Repository security scans combining AI analysis with Trivy checks.
- Findings triage, run history, coverage, and model usage in one workspace.
- Shared model connections for OpenAI-compatible APIs, Anthropic, and Amazon Bedrock.
- Optional code graph indexing for PR context and code navigation.
- Scoped REST API tokens and MCP access for reviews, security, and indexed code. A [Noodle collection](collections/code-mortis/README.md) is included for the REST API.

## Develop locally

Use Docker Compose and Python 3.12+. Create a [Smee channel](https://smee.io/) to relay GitHub webhooks to your local stack.

```sh
python3 backend/code_mortis/manage.py init-env \
  --output .env.dev --owner YOUR_GITHUB_LOGIN \
  --url http://localhost:5173 --webhook-url https://smee.io/YOUR_CHANNEL

docker compose --env-file .env.dev -f compose.dev.yaml --profile webhooks up -d --build
```

Open [localhost:5173](http://localhost:5173), enter `CM_BOOTSTRAP_TOKEN` from `.env.dev`, and register a development GitHub App. The frontend and API reload on edit. Restart the affected worker or scheduler after changing its code.

## Deploy with Coolify

Point an HTTPS hostname at your Coolify server and run its Traefik proxy. Create a Git-based Docker Compose application with the repository root as the base directory and `/compose.yaml` as the Compose location. Enable **Raw** Compose deployment so the overlay provides the proxy labels. [Coolify Docker Compose guide](https://coolify.io/docs/applications/builds/docker-compose)

Generate the environment from a local checkout:

```sh
python3 backend/code_mortis/manage.py init-env \
  --owner YOUR_GITHUB_LOGIN --url https://reviews.example.com
```

Copy the generated `.env` values into Coolify’s **Environment Variables** and add `CM_PUBLIC_HOST=reviews.example.com`. Use the same hostname in both settings. Keep `.env` out of Git and back up `CM_ENCRYPTION_KEY` with your database backups.

Set **Custom Build Command** to:

```sh
docker compose -f compose.yaml -f compose.coolify.yaml build
```

Set **Custom Start Command** to:

```sh
docker compose -f compose.yaml -f compose.coolify.yaml up -d
```

Deploy, then open your HTTPS URL and enter `CM_BOOTSTRAP_TOKEN`. Register and install your GitHub App on the repositories you want to connect, add a model connection, and enable reviews in repository settings.

Licensed under [Apache 2.0](LICENSE).
