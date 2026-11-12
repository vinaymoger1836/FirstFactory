# syntax=docker/dockerfile:1.7
# One Dockerfile, three targets: api, worker, cad-worker. Build context = repo root.
# Multi-arch: linux/amd64 and linux/arm64 (Oracle Ampere A1).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.23

FROM ${UV_IMAGE} AS uv

# ── base ───────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app
WORKDIR /app

# ── dependency builder (shared) ────────────────────────────────────
FROM base AS deps
COPY --from=uv /uv /usr/local/bin/uv
# Workspace manifests first, for layer caching.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/domain/pyproject.toml packages/domain/pyproject.toml
COPY packages/engines/pyproject.toml packages/engines/pyproject.toml
COPY packages/agents/pyproject.toml packages/agents/pyproject.toml
COPY packages/library/pyproject.toml packages/library/pyproject.toml

FROM deps AS api-build
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package fl-api --no-install-workspace
COPY packages packages
COPY apps/api apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package fl-api --no-editable

FROM deps AS worker-build
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package fl-worker --no-install-workspace
COPY packages packages
COPY apps/worker apps/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package fl-worker --no-editable

FROM deps AS cad-build
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package fl-worker --extra cad --no-install-workspace
COPY packages packages
COPY apps/worker apps/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package fl-worker --extra cad --no-editable

# ── api ────────────────────────────────────────────────────────────
FROM base AS api
COPY --from=api-build /app/.venv /app/.venv
COPY apps/api/alembic.ini /app/alembic.ini
COPY apps/api/migrations /app/migrations
# Library seed (docs/10): validated and loaded into Postgres by `fl-library load` in the
# compose `migrate` step. Read-only data; versioned YAML.
COPY library /app/library
ENV FL_LIBRARY_DIR=/app/library
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"]
CMD ["fl-api"]

# ── worker (engines: CP-SAT, SimPy, reports) ───────────────────────
FROM base AS worker
# WeasyPrint runtime: Pango/HarfBuzz + a base font.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
COPY --from=worker-build /app/.venv /app/.venv
USER app
ENV WORKER_ROLE=worker
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3   CMD ["python", "-c", "import sys,time; sys.exit(0 if time.time()-float(open('/tmp/fl-heartbeat').read())<30 else 1)"]
CMD ["fl-worker"]

# ── cad-worker (OCP/CadQuery; runs sandboxed, no network) ─────────
FROM base AS cad-worker
# OCCT (cadquery-ocp) links against OpenGL/X11 client libs even when headless.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgl1 libglu1-mesa libegl1 libxrender1 libxext6 libx11-6 libxi6 libsm6 \
      libfontconfig1 libfreetype6 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=cad-build /app/.venv /app/.venv
USER app
ENV WORKER_ROLE=cad-worker
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3   CMD ["python", "-c", "import sys,time; sys.exit(0 if time.time()-float(open('/tmp/fl-heartbeat').read())<30 else 1)"]
CMD ["fl-worker"]
