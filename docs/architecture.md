# Backend structure

The backend follows FastAPI's [multiple-file application pattern](https://fastapi.tiangolo.com/tutorial/bigger-applications/): focused `APIRouter` modules, shared dependencies, and explicit router registration. Workflow code lives in packages by domain so the HTTP layer and background workers can use the same implementation.

```text
backend/
├── code_mortis/
│   ├── main.py              # Public ASGI application, middleware, health and frontend
│   ├── native_runner.py     # Isolated official-client ASGI entry point
│   ├── jobs.py              # Celery entry point and durable job dispatch
│   ├── manage.py            # Environment setup and OpenAPI export CLI
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
│   ├── integrations/        # GitHub, OCR, model discovery, runner client, providers
│   ├── reviews/             # Review lifecycle, assessment, verification,
│   │                       # summaries, checks, context and review queries
│   ├── security_scans/      # Scan lifecycle, analysis, assessment and GitHub issues
│   ├── mcp/                 # OAuth, transports, tools, grant models and provenance
│   ├── api_tokens/          # Personal token lifecycle, exact route policy and provenance
│   └── schemas/             # Shared Pydantic contracts for HTTP, workers and runner
├── migrations/              # Additive Alembic revisions and metadata registration
└── tests/                   # Backend regressions and container smoke scripts
```

## Placement and imports

- Add HTTP operations to the corresponding module in `api/routers/` and register new routers in `api/router.py`. The composition router owns `/api/v1`; resource routers use relative paths. MCP discovery and OAuth retain their separate public paths.
- Import authentication and resource-access helpers from `api/dependencies.py`. A router must not import the application object or use another router as its source of shared permission checks.
- Put review and scan workflows in `reviews/` and `security_scans/`. HTTP handlers and Celery tasks call these modules; workflows do not import API routers. Use the existing SQLAlchemy session and transaction patterns rather than adding repository or service wrapper classes.
- Put external-system adapters in `integrations/` and shared infrastructure in `core/`. Provider metadata is a package resource at `integrations/providers.json`.
- Keep Pydantic contracts in `schemas/`, shared SQLAlchemy models in `models.py`, and MCP grant models in `mcp/models.py`. Alembic explicitly imports both model registries so all tables participate in migrations.
- Package `__init__.py` files contain only an optional docstring. Import concrete modules explicitly; do not make importing a schema or the native runner initialize the public API, Celery, or database models.
- MCP tools intentionally call the same explicit handlers as the browser. Keep their administrator and owner checks because direct Python calls do not execute FastAPI dependencies.

## Runtime compatibility and verification

The deployment entry points remain `code_mortis.main:app`, `code_mortis.native_runner:app`, `code_mortis.jobs:celery`, and `python -m code_mortis.manage`. Celery task names remain `mortis.execute`, `mortis.dispatch`, and `mortis.reconcile`, preserving already queued messages. Internal Python import paths changed; local scripts must use the packages above.

Run the backend suite and Ruff after moving modules. Export OpenAPI and regenerate frontend types with the commands in [AGENTS.md](../AGENTS.md); a structural refactor should preserve the public contract. CI path filters and their tests in `.github/` must follow moved files and cover newly added router modules.

The native runner keeps its restricted imports and receives no database or GitHub credentials. Container and real-account acceptance remain separate from local tests; see [operations](operations.md) and [acceptance](acceptance.md).

## Optional code graph parser

`code_mortis.code_graph.server:app` is a separate private ASGI entry point in `Dockerfile.code-graph`, enabled only by the code graph Compose overlay. Its disposable child runs `python -m code_mortis.code_graph.extract` on supplied bounded bytes. `code_graph/client.py` is the trusted worker transport and validates results against the submitted exact-revision manifest. Parser-facing schemas import only Pydantic and the standard library; grammar imports stay inside the isolated extraction module. The base application and native runner entry points are unchanged. Authorized acquisition and persistence jobs are the next checkpoint, not yet wired to this transport.
