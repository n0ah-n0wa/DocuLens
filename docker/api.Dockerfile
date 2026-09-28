# syntax=docker/dockerfile:1.7
# DocuLens API image (SPECIFICATIONS.md §56): multi-stage, digest-pinned, non-root, no secrets.
# Build from the repository root:  docker build -f docker/api.Dockerfile -t doculens-api .
#
# The Lambda packaging variant (runtime interface client vs. web adapter) is undecided:
# docs/planning/open-questions.md OQ-2. This image runs the ASGI app under uvicorn.

# Exact interpreter patch so that builds are reproducible; Dependabot (docker ecosystem) bumps it.
# Digests pin the bit-for-bit base layers (tag alone can move under a registry rebuild).
ARG PYTHON_VERSION=3.12.14
ARG PYTHON_DIGEST=sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG UV_VERSION=0.12.17
ARG UV_DIGEST=sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc

# ---- uv binary (build args expand in FROM, not in COPY --from) --------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION}@${UV_DIGEST} AS uv

# ---- builder ---------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm@${PYTHON_DIGEST} AS builder
COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# 1. Third-party dependencies only (cached until a lockfile or manifest changes).
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
    uv sync --frozen --no-dev --no-editable --package doculens-api

# ---- runtime ---------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm@${PYTHON_DIGEST} AS runtime

# Apply Debian security updates (OpenSSL) and drop unused system pip from the base image.
# Runtime installs come from the uv-built /opt/venv only; system pip is not needed.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/lib/python*/ensurepip \
    && rm -rf /usr/local/lib/python*/site-packages/pip* \
    && rm -rf /usr/local/lib/python*/site-packages/setuptools* \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* \
    && groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app \
    && chown app:app /app

COPY --from=builder --chown=app:app /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "doculens_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]
