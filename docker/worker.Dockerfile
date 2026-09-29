# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# DocuLens document-processing worker image (SPECIFICATIONS.md §56).
# Build from the repository root:
#   docker build -f docker/worker.Dockerfile -t doculens-worker .
#
# Determinism: base images and the Dockerfile frontend are digest-pinned; third-party Python
# deps come only from uv.lock (`uv sync --frozen --no-dev`). Debian security packages that are
# not yet in the pinned base digest are installed at exact versions (OPENSSL_* args) so rebuilds
# stay reproducible. Do not run an unpinned `apt-get upgrade`. Dependabot bumps PYTHON_DIGEST /
# UV_DIGEST / OPENSSL_* when bases or security pins need updates.
#
# Worker compute target (Lambda container vs. ECS) is undecided: docs/planning/open-questions.md OQ-2.

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

COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/
COPY apps/api/pyproject.toml apps/api/
COPY services/document-worker/pyproject.toml services/document-worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package doculens-worker

COPY packages/core packages/core
COPY services/document-worker services/document-worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package doculens-worker \
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

ENV PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONSAFEPATH=1

LABEL org.opencontainers.image.title="doculens-worker" \
      org.opencontainers.image.description="DocuLens document-processing worker" \
      org.opencontainers.image.source="https://github.com/doculens/doculens"

WORKDIR /app
USER app
STOPSIGNAL SIGTERM

# Queue workers expose no HTTP port; liveness is “PID 1 still running” (the worker process).
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os; os.kill(1, 0)"]

# Settings and secrets are injected at runtime (env / Secrets Manager); nothing secret is baked in.
CMD ["python", "-m", "doculens_worker"]
