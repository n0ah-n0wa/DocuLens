# syntax=docker/dockerfile:1.7
# DocuLens document-processing worker image (SPECIFICATIONS.md §56).
# Build from the repository root:  docker build -f docker/worker.Dockerfile -t doculens-worker .
#
# Worker compute target (Lambda container vs. ECS) is undecided: docs/planning/open-questions.md OQ-2.

# Exact interpreter patch so that builds are reproducible; Dependabot (docker ecosystem) bumps it.
ARG PYTHON_VERSION=3.12.14
ARG UV_VERSION=0.12.17

# ---- uv binary (build args expand in FROM, not in COPY --from) --------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---- builder ---------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
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
    uv sync --frozen --no-dev --no-editable --package doculens-worker

# ---- runtime ---------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY --from=builder --chown=app:app /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER app

CMD ["python", "-m", "doculens_worker"]
