import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from redis import Redis
from sqlalchemy import text

from .api import router
from .config import settings
from .db import engine
from .github import GitHubError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(
    title="Code Mortis", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json"
)
app.include_router(router)


@app.exception_handler(GitHubError)
def github_error(request: Request, exc: GitHubError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status if exc.status < 500 else 502)


@app.exception_handler(ValueError)
def invalid_input(request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(RequestValidationError)
def invalid_request(request: Request, exc: RequestValidationError):
    # Pydantic's default response echoes raw input, which may contain provider credentials.
    details = "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors())
    return JSONResponse({"detail": details}, status_code=422)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://avatars.githubusercontent.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://github.com",
        }
    )
    if request.url.path.startswith("/api/"):
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


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    root = settings().frontend_dir.resolve()
    asset = (root / path).resolve()
    if path.startswith(("api/", "health/")):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if asset.is_relative_to(root) and asset.is_file():
        return FileResponse(asset)
    index = root / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(
        {"detail": "Frontend build not found. Run bun run build in frontend/."}, status_code=503
    )
