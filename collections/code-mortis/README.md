# Code Mortis in Noodle

98 requests for the current Code Mortis REST API, with session authentication,
automatic CSRF, response status assertions, and a read-only smoke suite. Tested
with Noodle 0.10.2. Commands below run from the repository root.

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
   an allowed Code Mortis member. In browser developer tools, open
   **Application → Storage → Cookies** (Firefox: **Storage → Cookies**). Copy
   only the value of `cm_session`, without `cm_session=` or surrounding quotes.
   It is HttpOnly, so `document.cookie` cannot read it.

3. Store the value using Noodle's hidden prompt:

   ```sh
   noodle secret set cm_session --env development --collection collections/code-mortis
   ```

   Use `--env server` for that environment. The `.env` files contain only secret
   declarations; the value lives in the OS credential vault. For unattended
   execution, supply the same-named process environment variable `cm_session`
   through your secret manager. Never paste the value into YAML, a command
   argument, or a committed dotenv file.

4. Verify the session, then run the smoke suite:

   ```sh
   noodle request run api/session/me --collection collections/code-mortis --env development
   noodle collection run collections/code-mortis --env development --tag smoke --fail-fast
   ```

   The smoke suite has 13 GET requests: health/readiness/setup, identity,
   repositories, PR inbox, provider catalog, connections, security repositories,
   and analytics. It requires no resource IDs or provider credentials.

There is no password login or GitHub-PAT bearer authentication for these REST
routes. GitHub login is browser-bound; starting it in Noodle cannot finish a
login that began in another browser. The collection therefore reuses your
existing application session. `/api/v1/auth/me` supplies the CSRF token
automatically before each write. A 401 means sign in again and replace the
stored cookie. A 403 can mean missing administrator, owner, or repository
permissions; CSRF does not bypass those checks. Logging out in this collection
also invalidates the browser session whose cookie you copied.

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

Choose `connection_id` from `api/connections/connections`. Connection IDs also
fill the personal-subscription routes' `identity` parameter. Choose `run_id`,
`scan_id`, and other IDs from the corresponding detail/list responses. Starting
a review, starting/retrying a scan, and creating a connection capture the
returned ID. A single send saves it in the selected environment; an ordered
collection run keeps it transient for later requests in that run.

Optional query defaults are present but disabled so the server keeps its
normal filtering behavior. Enable or add query rows in Noodle when needed;
repeat the same key for multi-value filters. `section=inventory` selects scan
coverage; other supported sections are `excluded`, `units`, and `problems`.

## Send writes deliberately

`smoke` contains only reads. Requests are also tagged `read` or `write`. **An
unfiltered collection run includes writes**, including logout, deletion,
revocation, settings replacement, model work, and GitHub publication. Select an
individual request or a specific group of requests after reviewing their bodies.

- **Repository configuration:** `update-repository` replaces settings. Read the
  repository first and edit the whole body to preserve the settings you want.
  Its example disables reviews and automatic work and supplies finite limits.
- **Models:** set `connection_id` and, for scans, optionally `model`. Reviews,
  scans, background generation, and connection tests can consume provider quota.
  Scan examples use concurrency one and finite timeout/token settings; token
  budgets are not hard spending caps. Each scan send generates a new request
  key. To retry an uncertain HTTP submission idempotently, reuse its prior key.
- **Shared API connections:** the create example uses OpenAI Responses. Set
  `connection_name`, `provider_base_url`, and `model`, and store
  `provider_api_key` with `noodle secret set` before sending. Edit provider and
  protocol together for another service. The edit request omits `api_key` to
  retain the saved credential. Personal ChatGPT/Claude Code connections use the
  same create route with `kind`, `name`, and `protocol: native`; finish their
  provider authorization through the normal application flow.
- **Security changes:** select `security_finding_id`, then send
  `api/security/finding-detail`. It captures `finding_version` for optimistic
  concurrency. Refresh it after each change; a stale version returns 409.
  Edit `reason` and the desired state before sending. Issue publication also
  needs the reviewed title/body and the exact visibility/version from
  `api/security/issue-preview`; copy `recovery_token` when the preview provides
  one. Bulk publication needs the correct version for every draft. Reapproval
  additionally requires the existing operation ID and approval revision.
- **MCP management:** these are the application's session-authenticated settings,
  client, grant, consent, and approval routes. They require real pending IDs and
  normal administrator/user permissions. Consent and approval examples deny by
  default. Review the exact preview before changing them to approve. Settings
  updates replace the supplied instance switches and allowed-domain list.

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

The tests check every route/body against the application's current OpenAPI and
run the actual Noodle binary over HTTP against the isolated test database,
covering smoke reads, cookie/CSRF writes, missing/expired sessions, and logout.
The HTTP check is skipped when Noodle is not installed. GitHub permissions are
stubbed in that check; it does not establish live provider or GitHub integration.

Response history and the cookie jar are disabled for this collection. The
session is sent explicitly from the selected secret. `.timeline/` and optional
`*.local.env` environments are ignored by Git. Response data and files saved
with `--output` can still contain private application data.
