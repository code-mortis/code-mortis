ARG DOCKERHUB_REGISTRY=docker.io
FROM ${DOCKERHUB_REGISTRY}/oven/bun:1.4.2 AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
ARG CM_SENTRY_FRONTEND_DSN=""
ARG CM_SENTRY_ENVIRONMENT=production
ARG CM_SENTRY_RELEASE=""
ENV VITE_SENTRY_DSN=$CM_SENTRY_FRONTEND_DSN VITE_SENTRY_ENVIRONMENT=$CM_SENTRY_ENVIRONMENT VITE_SENTRY_RELEASE=$CM_SENTRY_RELEASE
RUN bun run build

FROM ${DOCKERHUB_REGISTRY}/library/python:3.13-slim-bookworm AS runtime
ARG TARGETARCH
ARG OCR_VERSION=1.12.11
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN set -eu; arch="$TARGETARCH"; \
    curl -fsSL "https://github.com/alibaba/open-code-review/releases/download/v${OCR_VERSION}/opencodereview-linux-${arch}" -o /tmp/ocr; \
    curl -fsSL "https://github.com/alibaba/open-code-review/releases/download/v${OCR_VERSION}/sha256sum.txt" -o /tmp/checksums; \
    python -c 'import hashlib,pathlib,sys; n="opencodereview-linux-"+sys.argv[1]; rows=[r.split() for r in pathlib.Path("/tmp/checksums").read_text().splitlines()]; expected=next(r[0] for r in rows if r[-1].lstrip("*").split("/")[-1]==n); assert hashlib.sha256(pathlib.Path("/tmp/ocr").read_bytes()).hexdigest()==expected' "$arch"; \
    install -m 755 /tmp/ocr /usr/local/bin/ocr; rm /tmp/ocr /tmp/checksums
COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev && groupadd -g 10001 mortis && useradd -u 10001 -g mortis -m mortis && mkdir -p /home/mortis/.opencodereview && chown -R mortis:mortis /home/mortis
COPY --from=frontend /src/frontend/dist /app/frontend/dist
COPY THIRD_PARTY_NOTICES.md LICENSE /app/
COPY LICENSES/ /app/LICENSES/
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER mortis
EXPOSE 8080
CMD ["uvicorn", "code_mortis.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
