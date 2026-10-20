# The app image: the CLI, API, MCP server and scheduled jobs all run from it (compose.yaml).
# Python comes from uv, pinned by .python-version, as on every other machine.

# The dashboard (frontend/): Node only builds it; the app image serves the static files.
FROM --platform=$BUILDPLATFORM node:24-trixie-slim@sha256:7f486cabe892bff8fef04c0f7da8dba84a560be3e4e2740b7a86afc57ebec315 AS dashboard
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM debian:trixie-slim

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.50
ARG SUPERCRONIC_SHA256_arm64=a1c0af9872e8de89a1ce5c1b596e26c42917d5a2cf914d98faee2e28156899e2
ARG SUPERCRONIC_SHA256_amd64=d2d7c97fbfefc763d5bbcbbb3f8d61f9dd3410ce5d05a737c414994b80b8cdd0

# postgresql-client and rclone for deploy/backup.sh; supercronic for the schedule
# (deploy/cron.sh).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl postgresql-client rclone \
    && rm -rf /var/lib/apt/lists/* \
    && eval "sha=\$SUPERCRONIC_SHA256_${TARGETARCH}" \
    && curl -fsSLo /usr/local/bin/supercronic \
       "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
    && echo "${sha}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# Only the scripts the containers run; settings and the compose file stay outside.
COPY deploy/backup.sh deploy/cron.sh ./deploy/
RUN uv sync --frozen --no-dev
COPY --from=dashboard /frontend/dist ./frontend/dist

# There's no .git in the image: the research trial log reads the commit from here.
ARG CODE_VERSION
# Unbuffered: job output reaches docker logs as it happens.
ENV FI_CODE_VERSION=${CODE_VERSION} PATH=/app/.venv/bin:$PATH HOME=/tmp PYTHONUNBUFFERED=1
CMD ["fin-intel", "--help"]
