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

FROM ${DOCKERHUB_REGISTRY}/golang:1.26.9-bookworm AS ocr
ARG TARGETARCH
WORKDIR /src
ADD --checksum=sha256:71dd1e101b77385ec1aa233de458c5325139e67d947f303e3f80bf32676f7621 https://codeload.github.com/alibaba/open-code-review/tar.gz/8eb83340a487b1150af00701b94f01af97e4b4d6 /tmp/ocr.tar.gz
RUN tar -xzf /tmp/ocr.tar.gz --strip-components=1 && rm /tmp/ocr.tar.gz
COPY build/ocr/ /tmp/mortis-ocr/
RUN git apply --check /tmp/mortis-ocr/diagnostics.patch && git apply /tmp/mortis-ocr/diagnostics.patch && git apply --check /tmp/mortis-ocr/redaction.patch && git apply /tmp/mortis-ocr/redaction.patch && cp /tmp/mortis-ocr/*.go internal/llm/
ENV CGO_ENABLED=0 GOFLAGS=-p=2
RUN go test ./internal/llm -run 'TestCodeMortis|TestOpenAIResponsesClient'
RUN GOARCH=$TARGETARCH go build -trimpath -ldflags '-s -w -X main.Version=v1.13.1 -X main.GitCommit=8eb8334+cm-context' -o /out/ocr ./cmd/opencodereview

FROM ${DOCKERHUB_REGISTRY}/library/python:3.13-slim-bookworm AS runtime
ARG TARGETARCH
ARG TRIVY_VERSION=0.76.0
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ocr /out/ocr /usr/local/bin/ocr
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
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev && groupadd -g 10001 mortis && useradd -u 10001 -g mortis -m mortis && mkdir -p /home/mortis/.opencodereview /var/cache/mortis-trivy && chown -R mortis:mortis /home/mortis /var/cache/mortis-trivy
COPY --from=frontend /src/frontend/dist /app/frontend/dist
COPY THIRD_PARTY_NOTICES.md LICENSE /app/
COPY LICENSES/ /app/LICENSES/
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER mortis
EXPOSE 8080
CMD ["uvicorn", "code_mortis.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
