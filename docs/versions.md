# Versions

Pinned versions and where they are pinned. Upgrades happen only between phases (docs/14 §3.8).
Every dependency below has a permissive licence (MIT, BSD, Apache-2.0, ISC, PSF, LGPL for
WeasyPrint's system libraries) unless noted.

**Supply-chain guards** (P0):
- Python: `exclude-newer = "2026-10-28"` in the root `pyproject.toml`. uv ignores anything
  published after that date. Move it forward deliberately between phases.
- JS: `minimumReleaseAge: 20160` (14 days) in `pnpm-workspace.yaml`. Only `esbuild` may run
  install scripts (`allowBuilds`).
- Lockfiles: `uv.lock` and `pnpm-lock.yaml` are committed. Docker builds use `uv sync --frozen`.

Last updated: 2026-11-12 (P1). The `exclude-newer` date was not moved in P1.

## Toolchain

| Tool | Version | Where |
|---|---|---|
| Python | 3.12 (3.12.16 in images; 3.12.10 on dev host) | `requires-python`, `python:3.12-slim-bookworm` |
| uv | 0.12.23 | `ghcr.io/astral-sh/uv:0.12.23` in the Dockerfile |
| Node | ≥ 22 (dev host 24.15.0) | `package.json` engines |
| pnpm | 11.18.0 | `packageManager` |
| Docker Engine / Compose | 29.6.2 (Desktop) | dev host |

## Container images (all published for amd64 + arm64; checked with `docker manifest inspect`)

| Image | Tag | Note |
|---|---|---|
| python | 3.12-slim-bookworm | base for api / worker / cad-worker |
| pgvector/pgvector | 0.8.1-pg16 | PostgreSQL 16 + pgvector 0.8.1 |
| redis | 7.4-alpine | BSD-3 licence. Redis 8 changed licence terms, so we stay on 7.4. |
| caddy | 2.10-alpine | reverse proxy |

## Python (resolved in `uv.lock`)

| Package | Version | Used by |
|---|---|---|
| fastapi | 0.162.0 | api |
| starlette | 1.10.0 | api |
| uvicorn | 0.54.0 | api |
| sse-starlette | 3.6.0 | api (SSE) |
| pydantic | 2.14.1 | all |
| pydantic-settings | 2.16.1 | api, worker, agents |
| sqlalchemy | 2.1.6 | api |
| asyncpg | 0.32.0 | api |
| alembic | 1.20.1 | api migrations |
| redis (redis-py) | 8.1.0 | api, worker |
| procrastinate | 3.10.0 | worker queue (spike e, ADR-019) |
| psycopg / psycopg-binary / psycopg-pool | 3.3.6 / 3.3.6 / 3.3.3 | worker queue; `fl-library` loader (api image, P1). LGPL-3.0 (dynamic use) |
| pint | 0.26.2 | domain: unit validation (P1). BSD-3. Pulls flexcache 0.3, flexparser 0.4, platformdirs 4.13.6 (MIT/BSD) |
| pyyaml | 6.0.3 | `fl-library`: catalog/pack YAML (`safe_load` only) (P1). MIT |
| ortools | 9.15.6755 | worker (CP-SAT) |
| weasyprint | 70.0 | worker (PDF) |
| cadquery | 2.8.0 | cad-worker |
| cadquery-ocp | 7.9.3.1.1 (OCCT 7.9.3) | cad-worker. cadquery 2.8 caps OCP below 8.x. |
| trimesh | 5.2.0 | cad-worker (GLB check) |
| casadi / nlopt / ezdxf | 3.8.2 / 2.11.0 / 1.4.5 | cadquery dependencies |
| ruff | 0.17.3 | dev |
| mypy | 2.4.0 | dev |
| httpx | 0.28.1 | dev |

## Spike-only Python (not in the workspace lock; run with `uv run --no-project --with …`)

| Package | Version | Spike | Outcome |
|---|---|---|---|
| sysmlpy | 0.98.0 | d | Validator for P8 (parse + `analyze`). Pin exactly: releases are frequent. |
| sysml2kit | 0.4.0 (pre-alpha) | d | Not adopted (see P0 report) |
| antlr4-python3-runtime | 4.13.2 | d | sysmlpy dependency |
| arq | 0.28.0 | e | Not adopted (maintenance-only; pins redis<6) |
| litellm | 1.106.5 (latest before the cutoff) | c | Spike written; not yet run (no API keys) |
| psutil | 7.1.3 | e | bench only |

## Web (resolved in `pnpm-lock.yaml`)

| Package | Version |
|---|---|
| react / react-dom | 19.3.1 |
| react-router | 7.18.5 |
| vite | 7.3.7 |
| @vitejs/plugin-react | 5.2.0 |
| typescript | 5.9.3 |
| eslint | 9.39.5 |
| typescript-eslint | 8.72.1 |
| eslint-plugin-jsx-a11y | 6.10.2 |
| eslint-plugin-react-hooks | 5.2.0 |
| json-schema-to-typescript | 15.0.4 (P1; dev dependency of `packages/ts-types`). MIT. Pulls prettier 3.9.10 and @apidevtools/json-schema-ref-parser 11.9.3 (MIT) |

## GitHub Actions (major tags; pin to commit SHAs before enabling pushes, P9)

actions/checkout@v5 · docker/setup-qemu-action@v3 · docker/setup-buildx-action@v3 ·
docker/login-action@v3 · docker/metadata-action@v5 · docker/build-push-action@v6

## Spike results with versions (summary; details in docs/phase-reports/P0.md)

| Spike | amd64 | arm64 (QEMU-emulated on x86 host) |
|---|---|---|
| a. CadQuery 2.8.0 / OCP 7.9.3.1.1: STEP → solids + bboxes → GLB | pass | pass (identical output) |
| b. OR-Tools 9.15.6755 CP-SAT + WeasyPrint 70.0 | pass | pass |
| c. LiteLLM roles + token bucket | bucket logic only; provider calls need keys | n/a (pure Python) |
| d. SysML: own serializer vs sysml2kit 0.4.0, checked by sysmlpy 0.98.0 | own serializer recommended | n/a (pure Python) |
| e. Queue: procrastinate 3.10.0 vs arq 0.28.0 | procrastinate recommended | n/a (pure Python) |
