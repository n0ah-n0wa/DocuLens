# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# DocuLens API image (SPECIFICATIONS.md §56): multi-stage, digest-pinned, non-root, no secrets.
# Build from the repository root:
#   docker build -f docker/api.Dockerfile -t doculens-api .
#
# Determinism: base images and the Dockerfile frontend are digest-pinned; third-party Python
# deps come only from uv.lock (`uv sync --frozen --no-dev`). Debian security packages that are
# not yet in the pinned base digest are installed at exact versions (OPENSSL_* args) so rebuilds
# stay reproducible. Do not run an unpinned `apt-get upgrade`. Dependabot bumps PYTHON_DIGEST /
# UV_DIGEST / OPENSSL_* when bases or security pins need updates.
#
# The Lambda packaging variant (runtime interface client vs. web adapter) is undecided:
# docs/planning/open-questions.md OQ-2. This image runs the ASGI app under uvicorn.

ARG PYTHON_VERSION=3.12.14
ARG PYTHON_DIGEST=sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG UV_VERSION=0.12.17
ARG UV_DIGEST=sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc
# bookworm-security (CVE-2026-63072, CVE-2026-63076); drop when PYTHON_DIGEST includes these.
ARG OPENSSL_VERSION=3.0.22-1~deb12u1

# ---- uv binary (build args expand in FROM, not in COPY --from) --------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION}@${UV_DIGEST} AS uv

# ---- builder: resolve and install production deps into an isolated venv ----------------------
FROM python:${PYTHON_VERSION}-slim-bookworm@${PYTHON_DIGEST} AS builder
COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# 1. Third-party dependencies only (layer cached until lockfile or manifests change).
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/
COPY apps/api/pyproject.toml apps/api/
COPY services/document-worker/pyproject.toml services/document-worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package doculens-api

# 2. Workspace packages, installed non-editable so the venv is self-contained.
COPY packages/core packages/core
COPY apps/api apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package doculens-api \
    && find /opt/venv -type d -name '__pycache__' -prune -exec rm -rf {} + \
    && rm -rf /opt/venv/lib/python*/site-packages/pip \
             /opt/venv/lib/python*/site-packages/pip-*.dist-info \
             /opt/venv/lib/python*/site-packages/setuptools \
             /opt/venv/lib/python*/site-packages/setuptools-*.dist-info \
             /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.*

# ---- runtime: only the interpreter + production venv; no build tools, no uv --------------------
FROM python:${PYTHON_VERSION}-slim-bookworm@${PYTHON_DIGEST} AS runtime
ARG OPENSSL_VERSION

# Non-root user, root-owned application tree, pinned Debian security updates for OpenSSL.
# HOME is a writable directory under /home/app; /opt/venv stays root-owned and non-writable.
RUN groupadd --gid 1001 app \
    && useradd --uid 1001 --gid app --home-dir /home/app --shell /usr/sbin/nologin --no-create-home app \
    && mkdir -p /home/app /app \
    && chown app:app /home/app \
    && chmod 755 /home/app /app \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        "libssl3=${OPENSSL_VERSION}" \
        "openssl=${OPENSSL_VERSION}" \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
    && rm -rf /usr/local/lib/python*/ensurepip \
    && rm -rf /usr/local/lib/python*/site-packages/pip* \
    && rm -rf /usr/local/lib/python*/site-packages/setuptools* \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

COPY --from=builder --chown=root:root /opt/venv /opt/venv

# Alembic scripts for the migrate Lambda (same image, OQ-23).
COPY --chown=root:root packages/core/alembic.ini /opt/doculens/alembic.ini
COPY --chown=root:root packages/core/alembic /opt/doculens/alembic

ENV PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONSAFEPATH=1

LABEL org.opencontainers.image.title="doculens-api" \
      org.opencontainers.image.description="DocuLens HTTP API" \
      org.opencontainers.image.source="https://github.com/doculens/doculens"

WORKDIR /app
USER app
EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]

# Local/dev CMD is uvicorn. Lambda overrides image_config to:
#   entry_point = ["/opt/venv/bin/python", "-m", "awslambdaric"]
#   command     = ["doculens_api.lambda_handler.handler"]  (API)
#                 ["doculens_api.migrate_handler.handler"] (migrate)
# Settings and secrets are injected at runtime (env / Secrets Manager); nothing secret is baked in.
CMD ["uvicorn", "doculens_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]
