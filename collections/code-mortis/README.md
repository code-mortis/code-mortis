# Code Mortis in Noodle

54 requests for the Code Mortis REST API operations available to personal API
tokens, plus 3 unauthenticated health/setup requests. Tested with Noodle
0.10.2. Commands below run from the repository root.

## Start and authenticate

1. Start the normal development stack as described in the repository README.
   The `development` environment uses `http://localhost:5173`. The `server`
   environment uses the local production port, `http://localhost:8080`. For a
   hosted instance, change `base_url` to its exact HTTPS origin, without a
   trailing slash or `/api/v1` suffix:

    ```sh
    noodle environment set base_url https://reviews.example.com --env server --collection collections/code-mortis
    ```

2. Open that exact origin in your browser and sign in with GitHub. You must be
   an allowed Code Mortis member. Go to Settings → API tokens and create a
   token: pick a name, an expiration (default 30 days, maximum 365 days), the
   permissions the workflow needs, and every repository it may touch. Issuing
   requires a GitHub callback completed within the previous 15 minutes; sign
   in again if the form asks for reauthentication. The full token is shown
   once. Copy it immediately; closing the panel loses it permanently and a
   replacement must be created.

3. Store the value using Noodle's hidden prompt:

    ```sh
    noodle secret set cm_api_token --env development --collection collections/code-mortis
    ```

    Use `--env server` for that environment. The `.env` files contain only
    secret declarations; the value lives in the OS credential vault. For
    unattended execution, supply the same-named process environment variable
    `cm_api_token` through your secret manager. Never paste the value into
    YAML, a command argument, or a committed dotenv file. Never paste a
    browser `cm_session` cookie here: this collection sends no cookies and a
    cookie value is rejected before anything is sent.

4. Verify the token, then run the smoke suite:

    ```sh
    noodle request run api/auth/token --collection collections/code-mortis --env development
    noodle collection run collections/code-mortis --env development --tag smoke --fail-fast
    ```

    The smoke suite has 14 read-only GET requests: health/readiness/setup,
    token identity, repositories, PR inbox, provider catalog, connections,
    security repositories, and analytics. It performs no model execution and
    no GitHub publication. It needs a token with the read permissions behind
    those requests (`repositories:read`, `reviews:read`, `connections:read`,
    `security:read`, `analytics:read`) and an explicit repository selection;
    results are limited to the selected repositories. A 401 means the token
    is missing, malformed, expired, or revoked, so create a replacement and
    store it again. A 403 means the token lacks the permission or the current
    repository access for that request.

There is no password login, no cookie fallback, and no CSRF prefetch in this
collection. Every request under `api/` inherits a bearer credential from the
folder configuration; public requests under `public/` send no credential. A
missing or invalid token never falls back to a browser session.

## Select resources

Request and folder names appear under `public/` and `api/`. To browse them:

```sh
noodle collection list collections/code-mortis
```

Resource variables start as `REPLACE_ME` and fail before sending until you
choose their values. Use IDs returned by Code Mortis, including its internal PR
ID, rather than a GitHub PR number. Review finding IDs and security finding IDs
are separate variables.

```sh
noodle request run api/repositories/repositories --collection collections/code-mortis --env development --body
noodle environment set repository_id 123 --env development --collection collections/code-mortis
noodle request run api/repositories/pull-requests --collection collections/code-mortis --env development --body
noodle environment set pr_id YOUR_INTERNAL_PR_ID --env development --collection collections/code-mortis
noodle request run api/pulls/pull-detail --collection collections/code-mortis --env development --body
```

Choose `connection_id` from `api/connections/connections`. Choose `run_id`,
`scan_id`, and other IDs from the corresponding detail/list responses. Starting
a review, starting/retrying a scan, and retrying a review capture the returned
ID. A single send saves it in the selected environment; an ordered
collection run keeps it transient for later requests in that run.

Optional query defaults are present but disabled so the server keeps its
normal filtering behavior. Enable or add query rows in Noodle when needed;
repeat the same key for multi-value filters. `section=inventory` selects scan
coverage; other supported sections are `excluded`, `units`, and `problems`.

## Send writes deliberately

`smoke` contains only reads. Requests are also tagged `read` or `write`. **An
unfiltered collection run includes writes**, including finding triage, review
runs, and scan execution. Select an individual request or a specific group of
requests after reviewing their bodies.

- **Permissions:** each request needs its documented permission. Reads need
  `repositories:read`, `reviews:read`, `security:read`, `connections:read`,
  or `analytics:read`. Finding and PR updates need `reviews:write` or
  `security:write`. Starting, retrying, cancelling, or publishing runs and
  scans needs `reviews:run` or `security:run`, which can consume provider
  quota and publish GitHub comments. The token creation screen explains these
  effects; grant execution permissions only for tokens that need them.
- **Repositories:** every repository touched must be in the token selection,
  and your GitHub permission is rechecked. New repositories never become
  accessible automatically; create a new token or a replacement with the
  extended selection.
- **Models:** set `connection_id` and, for scans, optionally `model`. Reviews
  and scans can consume provider quota. Scan examples use concurrency one and
  finite timeout/token settings; token budgets are not hard spending caps.
  Each scan send generates a new request key. To retry an uncertain HTTP
  submission idempotently, reuse its prior key.
- **Security changes:** select `security_finding_id`, then send
  `api/security/finding-detail`. It captures `finding_version` for optimistic
  concurrency. Refresh it after each change; a stale version returns 409.
  Edit `reason` and the desired state before sending.
- **Reviews:** `reviews:write` covers resolving and reopening findings and
  ignoring PRs. Ignoring a PR stops its unfinished work. `reviews:run`
  covers starting, retrying, cancelling, and publishing runs.

Revoking the token in Settings → API tokens stops its clients and its
unfinished token-originated work. Completed results stay. The next request
with a revoked token fails; store a replacement.

## Browser-only workflows

The following stay in the browser and have no runnable request in this
collection:

- Token management itself (Settings → API tokens list, create, revoke).
- Sign-in, sign-out, and `/auth/me`.
- Repository configuration and background generation/status.
- Connection creation, edits, deletion, tests, draft-model discovery,
  personal-provider authorization, grants, console tickets, and disconnect.
- Installation and team administration, member management, instance
  settings.
- MCP management, consent, and approvals.
- Security GitHub-issue preview, creation, synchronization, and reapproval.

```text
GET  /api/v1/auth/me                        # browser session identity
POST /api/v1/auth/logout                    # browser sign-out
GET/POST/DELETE /api/v1/api-tokens          # Settings → API tokens
PUT  /api/v1/repositories/{id}              # repository configuration
POST /api/v1/repositories/{id}/background   # background generation
POST /api/v1/connections                    # shared connection setup
GET  /api/v1/settings                       # instance settings
POST /api/v1/members                        # team administration
```

OAuth callbacks, GitHub App registration/install redirects, signed webhooks,
WebSocket consoles, and the MCP wire protocol are outside this collection.
Use their normal browser or service flows. Public OAuth discovery/token routes
are not authentication for the browser REST API.

## Check the collection

```sh
noodle collection audit collections/code-mortis --json
docker compose -f compose.test.yaml up -d --wait
cd backend
uv run python -m pytest -q tests/test_noodle_collection.py
```

The tests compare the collection against the implemented PAT route policy and
the application's current OpenAPI, then run the actual Noodle binary over HTTP
against the isolated test database: token issuance through a real browser
session, bearer reads, a reversible write, invalid/expired/revoked refusal,
missing-scope and missing-repository refusal, session-only rejection, and no
cookie/CSRF fallback. A focused browser cookie and CSRF regression is kept in
the same test file through direct HTTP calls. GitHub permissions are stubbed
in that check; it does not establish live provider or GitHub integration. That
HTTP check is skipped when Noodle is not installed.

Response history retains up to 50 entries; the cookie jar is disabled. The
token is sent explicitly from the selected secret. `.timeline/` and optional
`*.local.env` environments are ignored by Git. Response data and files saved
with `--output` can still contain private application data.
