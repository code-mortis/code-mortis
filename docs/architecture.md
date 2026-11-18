# Backend structure

The backend follows FastAPI's [multiple-file application pattern](https://fastapi.tiangolo.com/tutorial/bigger-applications/): focused `APIRouter` modules, shared dependencies, and explicit router registration. Workflow code lives in packages by domain so the HTTP layer and background workers can use the same implementation.

```text
backend/
├── code_mortis/
│   ├── main.py              # Public ASGI application, middleware, health and frontend
│   ├── jobs.py              # Celery entry point and durable job dispatch
│   ├── manage.py            # Environment, OpenAPI export and explicit retirement CLI
│   ├── models.py            # Shared SQLAlchemy model registry
│   ├── api/
│   │   ├── router.py        # Explicit /api/v1 router composition
│   │   ├── dependencies.py  # Session, user, administrator and resource access
│   │   ├── oauth.py         # Browser-bound GitHub authorization attempts
│   │   ├── presenters.py    # Shared public response serialization
│   │   └── routers/         # Auth, repositories, pulls, runs, findings,
│   │                       # connections, subscriptions, installations,
│   │                       # members, settings, analytics, security and webhooks
│   ├── code_graph/          # Optional isolated parser, syntax rules and trusted worker transport
│   ├── core/                # Settings, database, encryption, ordering, telemetry
│   ├── integrations/        # GitHub, shared inference transports, discovery, providers
│   ├── reviews/             # Review lifecycle, assessment, verification,
│   │                       # summaries, checks, context and review queries
│   ├── security_scans/      # Scan lifecycle, analysis, assessment and GitHub issues
│   ├── mcp/                 # OAuth, transports, tools, grant models and provenance
│   ├── api_tokens/          # Personal token lifecycle, exact route policy and provenance
│   └── schemas/             # Shared Pydantic contracts for HTTP, workers and parser
├── migrations/              # Additive Alembic revisions and metadata registration
└── tests/                   # Backend regressions and container smoke scripts
```

## Placement and imports

- Add HTTP operations to the corresponding module in `api/routers/` and register new routers in `api/router.py`. The composition router owns `/api/v1`; resource routers use relative paths. MCP discovery and OAuth retain their separate public paths.
- Import authentication and resource-access helpers from `api/dependencies.py`. A router must not import the application object or use another router as its source of shared permission checks.
- Put review and scan workflows in `reviews/` and `security_scans/`. HTTP handlers and Celery tasks call these modules; workflows do not import API routers. Use the existing SQLAlchemy session and transaction patterns rather than adding repository or service wrapper classes.
- Put external-system adapters in `integrations/` and shared infrastructure in `core/`. Provider metadata is a package resource at `integrations/providers.json`.
- Keep Pydantic contracts in `schemas/`, shared SQLAlchemy models in `models.py`, and MCP grant models in `mcp/models.py`. Alembic explicitly imports both model registries so all tables participate in migrations.
- Package `__init__.py` files contain only an optional docstring. Import concrete modules explicitly; do not make importing a schema or the isolated graph parser initialize the public API, Celery, or database models.
- MCP tools intentionally call the same explicit handlers as the browser. Keep their administrator and owner checks because direct Python calls do not execute FastAPI dependencies.

## Runtime compatibility and verification

The application deployment entry points are `code_mortis.main:app`, `code_mortis.jobs:celery`, and `python -m code_mortis.manage`. Celery task names remain `mortis.execute`, `mortis.dispatch`, and `mortis.reconcile`, preserving already queued messages. Internal Python import paths changed; local scripts must use the packages above.

Run the backend suite and Ruff after moving modules. Export OpenAPI and regenerate frontend types with the commands in [AGENTS.md](../AGENTS.md); a structural refactor should preserve the public contract. CI path filters and their tests in `.github/` must follow moved files and cover newly added router modules.

Mortis PR execution lives in `reviews/mortis.py`, `reviews/planning.py` and `reviews/engine.py`; security execution remains in `security_scans/`. Both use the shared inference gateway and durable analysis/admission ledgers. Model tools receive only bounded, authorized exact-revision source; repository scripts, hooks, agent instructions and MCP configuration are never executed. `core/engine_retirement.py` settles unfinished legacy analysis explicitly, and the additive migration changes only future repository selections. Historical run engines and immutable plans remain readable. Retired execution stays blocked. An explicitly requested new summary uses its own current Mortis job snapshot rather than the original review engine. Container and real-account acceptance remain separate from local tests; see [operations](operations.md) and [acceptance](acceptance.md).

## Optional code graph parser

`code_mortis.code_graph.server:app` is a separate private ASGI entry point in `Dockerfile.code-graph`. The parser and `code-graph-worker` live in the normal production/development Compose files under `COMPOSE_PROFILES=code-graph`. The graph worker reuses the application image and consumes only the `code-graph` queue at concurrency one. `code_graph/service.py` dispatches index, refresh and cleanup work through existing durable Jobs with fresh repository authorization, attempt fencing and atomic immutable publication. Reviews retain their separate queue and worker. Old graph messages delivered to the review queue are acknowledged without a claim; the dispatcher requeues the same durable Job on its current route.

`code_graph/store.py` owns the bare Git object store and shared-volume lock. The trusted graph worker mounts `code-graph-data` for writing; the trusted API mounts the same volume read-only for external code navigation. Readers take shared locks without creating directories or lock files, while acquisition and retention take the exclusive lock. The isolated parser does not see retained objects. The parser's disposable child runs `python -m code_mortis.code_graph.extract` on bounded supplied bytes. `code_graph/client.py` validates results against the submitted exact-revision manifest. Parser-facing schemas import only Pydantic and the standard library; grammar imports stay inside the isolated extraction module. The worker and parser have separate resource ceilings and a private transport network.

Browser administrators can opt repositories in and manage indexing through the existing repository settings. `code_graph/retrieval.py` provides bounded, authorized exact-revision queries, with source reads and cleanup sharing the store lock. Eligible Mortis PR reviews use `reviews/graph_context.py` to select bounded one-hop imports from PostgreSQL, then read and validate the same blobs in the review's existing exact-commit Git copy. Metadata retrieval takes a shared snapshot row lock without touching the graph source volume; source/text retrieval keeps the filesystem lock. Missing PR revisions are requested through existing graph refresh jobs, without waiting. New Mortis plans freeze graph eligibility and attempted source selections. Required assigned source and diff keep priority over optional graph context; missing/incompatible context and prompt-budget omissions are recorded. Existing token/MCP eligibility restrictions remain, and historical selections are never silently expanded.

`mcp/code_tools.py` exposes independent `code:read` tools on `/mcp/code`. It reuses shared retrieval with fresh grant, token, membership, installation, repository opt-in and GitHub checks. Source is checked for credential material before slicing; unsafe content is withheld, and raw source is never saved in MCP audit/operation rows. Code reads cannot admit graph jobs, consume a model or access review/security history. Existing Reviews/Security grants and personal API tokens do not authorize code reads. The API mount is read-only in both existing Compose files; no additional service, image or migration is needed.

Assessment and verification call `reviews/graph_context.discovery` before each inference round. Optional current-head paths reuse `required_paths` / `required_sources`; graph relationships never become citation evidence by themselves. The original/current source maps, history identity, publication and fix-verification rules remain authoritative. Mortis initial PR analysis passes graph-selected source into the actual prepared inference requests, bounded by the existing source and prompt budgets. Saved omissions from older engines remain part of the review history. Security scans do not currently consume this PR graph enrichment. See [phase 2](plans/code-graph/phase-2.md).
