ARG DOCKERHUB_REGISTRY=docker.io
FROM ${DOCKERHUB_REGISTRY}/library/node:24.11.0-bookworm-slim AS relay
RUN npm install --global --no-audit --no-fund smee-client@5.0.0
USER node
CMD ["sh", "-c", "exec smee --url \"$CM_WEBHOOK_URL\" --target http://api:8080/api/v1/webhooks/github"]

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
ARG TRIVY_VERSION=0.76.0
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates libseccomp2 && rm -rf /var/lib/apt/lists/*
RUN set -eu; case "$TARGETARCH" in amd64) arch=64bit ;; arm64) arch=ARM64 ;; *) exit 1 ;; esac; \
    asset="trivy_${TRIVY_VERSION}_Linux-${arch}.tar.gz"; \
    curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/${asset}" -o /tmp/trivy.tar.gz; \
    curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_checksums.txt" -o /tmp/trivy-checksums; \
    python -c 'import hashlib,pathlib,sys; rows=[r.split() for r in pathlib.Path("/tmp/trivy-checksums").read_text().splitlines()]; expected=next(r[0] for r in rows if r[-1].lstrip("*")==sys.argv[1]); assert hashlib.sha256(pathlib.Path("/tmp/trivy.tar.gz").read_bytes()).hexdigest()==expected' "$asset"; \
    mkdir /tmp/trivy; tar -xzf /tmp/trivy.tar.gz -C /tmp/trivy trivy LICENSE; \
    install -m 755 /tmp/trivy/trivy /usr/local/bin/trivy; \
    install -D -m 644 /tmp/trivy/LICENSE /usr/share/doc/trivy/LICENSE; \
    rm -r /tmp/trivy /tmp/trivy.tar.gz /tmp/trivy-checksums
COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --group code-graph --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev --group code-graph && groupadd -g 10001 mortis && useradd -u 10001 -g mortis -m mortis && mkdir -p /var/cache/mortis-trivy /var/lib/code-mortis/code-graph && chown -R mortis:mortis /home/mortis /var/cache/mortis-trivy /var/lib/code-mortis
COPY --chmod=755 docker/sync-worker.sh /usr/local/bin/code-mortis-sync
COPY --from=frontend /src/frontend/dist /app/frontend/dist
COPY LICENSE /app/
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER mortis
EXPOSE 8080
CMD ["uvicorn", "code_mortis.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
