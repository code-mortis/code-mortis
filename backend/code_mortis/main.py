import logging
import secrets
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from redis import Redis
from sqlalchemy import text

from code_mortis.api.router import router
from code_mortis.core.config import settings
from code_mortis.core.db import engine
from code_mortis.core.telemetry import init_sentry
from code_mortis.integrations.github import GitHubError, close_http_client
from code_mortis.mcp.server import install_mcp
from code_mortis.schemas.common import BrowserSentryConfig

init_sentry("api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        close_http_client()


app = FastAPI(
    title="Code Mortis", version="0.1.2", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.include_router(router)
install_mcp(app)


@app.exception_handler(GitHubError)
def github_error(request: Request, exc: GitHubError):
    if exc.status >= 500:
        sentry_sdk.capture_exception(exc, tags={"request_id": getattr(request.state, "request_id", "unavailable")})
    return JSONResponse({"detail": str(exc)}, status_code=exc.status if exc.status < 500 else 502)


@app.exception_handler(ValueError)
def invalid_input(request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc) or "This request could not be accepted. Check the values and try again."}, status_code=422)


def validation_message(error):
    kind, context = error["type"], error.get("ctx", {})
    if kind == "extra_forbidden":
        return "The request contains an unsupported field. Refresh the page and try again."
    if kind == "json_invalid":
        return "The request contains invalid JSON. Check quotes, commas, and braces."
    labels = {"delegated_context_bytes": "Native context budget (bytes)", "timeout_seconds": "Timeout (seconds)",
              "base_url": "Endpoint URL", "api_key": "API key"}
    path = [labels.get(part, str(part).replace("_", " ")) for part in error["loc"]
            if part not in {"body", "query", "path", "int", "str", "bool", "none", "float"}]
    field = " / ".join(path) or "Request"
    field = field[0].upper() + field[1:]
    messages = {
        "missing": "This field is required.",
        "int_type": "Enter a whole number.",
        "int_parsing": "Enter a whole number.",
        "int_from_float": "Enter a whole number without decimals.",
        "bool_type": "Choose true or false.",
        "bool_parsing": "Choose true or false.",
        "string_type": "Enter text.",
        "dict_type": "Enter a JSON object with keys and values.",
        "model_attributes_type": "Send a JSON object with the required fields and the Content-Type: application/json header.",
        "list_type": "Enter a list of values.",
        "string_pattern_mismatch": "Use the required format for this field.",
    }
    if kind in {"greater_than_equal", "less_than_equal"}:
        bound = "ge" if kind == "greater_than_equal" else "le"
        message = f"Enter a value {'of at least' if bound == 'ge' else 'no greater than'} {context[bound]:,}."
    elif kind in {"string_too_short", "string_too_long"}:
        bound = "min_length" if kind == "string_too_short" else "max_length"
        count = context[bound]
        message = f"Use {'at least' if bound == 'min_length' else 'no more than'} {count:,} {'character' if count == 1 else 'characters'}."
    elif kind == "literal_error":
        message = f"Choose {context['expected']}."
    else:
        message = messages.get(kind, error["msg"].removeprefix("Value error, "))
    return f"{field}: {message}"


@app.exception_handler(RequestValidationError)
def invalid_request(request: Request, exc: RequestValidationError):
    # Pydantic's default response echoes raw input, which may contain provider credentials.
    # Optional values produce an extra null-branch error that obscures the field's actual constraint.
    errors = [error for error in exc.errors() if error["type"] != "none_required"]
    details = " ".join(dict.fromkeys(validation_message(error) for error in errors)) or "Check the request fields and try again."
    return JSONResponse({"detail": details}, status_code=422)


@app.exception_handler(Exception)
def unexpected_error(request: Request, exc: Exception):
    reference = getattr(request.state, "request_id", "unavailable")
    sentry_sdk.capture_exception(exc, tags={"request_id": reference})
    logging.getLogger(__name__).error("Request %s failed (%s)", reference, type(exc).__name__)
    return JSONResponse({"detail": "Code Mortis could not complete this request. Try again. "
                         f"If it keeps failing, share request reference {reference} with the administrator."},
                        status_code=500, headers={"Cache-Control": "no-store", "X-Request-ID": reference})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    request.state.request_id = secrets.token_hex(12)
    response = await call_next(request)
    sentry_origin = settings().sentry_frontend_origin
    connect_src = "connect-src 'self'" + (f" {sentry_origin}" if sentry_origin else "")
    response.headers.update(
        {
            "X-Request-ID": request.state.request_id,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://avatars.githubusercontent.com; {connect_src}; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://github.com",
        }
    )
    if request.url.path.startswith(("/api/", "/mcp/", "/oauth/", "/.well-known/")):
        response.headers["Cache-Control"] = "no-store"
    if settings().public_url.startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        with engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        Redis.from_url(settings().redis_url, socket_timeout=2).ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.get("/api/browser-sentry")
def browser_sentry() -> BrowserSentryConfig | None:
    config = settings()
    if not config.sentry_frontend_dsn:
        return None
    return BrowserSentryConfig(
        dsn=config.sentry_frontend_dsn,
        environment=config.sentry_environment,
        release=config.sentry_release,
    )


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    root = settings().frontend_dir.resolve()
    asset = (root / path).resolve()
    if path.startswith(("api/", "health/", "oauth/", ".well-known/", "mcp/reviews", "mcp/security", "mcp/code")):
        return JSONResponse({"detail": "This API endpoint does not exist. Check the URL and try again."}, status_code=404)
    if asset.is_relative_to(root) and asset.is_file():
        return FileResponse(asset)
    index = root / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(
        {"detail": "Frontend build not found. Run bun run build in frontend/."}, status_code=503
    )
