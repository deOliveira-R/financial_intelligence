# The app image: the CLI, API, MCP server and scheduled jobs all run from it (compose.yaml).
# Python comes from uv, pinned by .python-version, as on every other machine.
FROM debian:trixie-slim

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.50
ARG SUPERCRONIC_SHA256_arm64=a1c0af9872e8de89a1ce5c1b596e26c42917d5a2cf914d98faee2e28156899e2
ARG SUPERCRONIC_SHA256_amd64=d2d7c97fbfefc763d5bbcbbb3f8d61f9dd3410ce5d05a737c414994b80b8cdd0

# postgresql-client and rclone for deploy/backup.sh; supercronic runs deploy/crontab (cron
# made for containers: no root, no mail, logs to stdout, never overlaps a running job).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl postgresql-client rclone \
    && rm -rf /var/lib/apt/lists/* \
    && eval "sha=\$SUPERCRONIC_SHA256_${TARGETARCH}" \
    && curl -fsSLo /usr/local/bin/supercronic \
       "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
    && echo "${sha}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY deploy ./deploy
RUN uv sync --frozen --no-dev

# There's no .git in the image: the research trial log reads the commit from here.
ARG CODE_VERSION
ENV FI_CODE_VERSION=${CODE_VERSION} PATH=/app/.venv/bin:$PATH HOME=/tmp
CMD ["fin-intel", "--help"]
