import json
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from fl_api import __version__
from fl_api.db import engine
from fl_api.settings import get_settings

router = APIRouter(tags=["health"])

HEARTBEAT_KEY = "fl:heartbeat:{name}"


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. No dependencies touched."""
    return {"status": "ok", "service": "api", "version": __version__}


@router.get("/health/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: Postgres (+pgvector), Redis and worker heartbeats."""
    settings = get_settings()
    checks: dict[str, Any] = {}

    try:
        async with engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
            ext = await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            server = await conn.scalar(text("SHOW server_version"))
        checks["postgres"] = {"ok": True, "server_version": server, "pgvector": ext}
        if ext is None:
            checks["postgres"]["ok"] = False
            checks["postgres"]["error"] = "pgvector extension missing (run migrations)"
    except Exception as exc:  # report, don't crash the health endpoint
        checks["postgres"] = {"ok": False, "error": type(exc).__name__}

    redis = request.app.state.redis
    try:
        await redis.ping()
        checks["redis"] = {"ok": True}
    except Exception as exc:
        checks["redis"] = {"ok": False, "error": type(exc).__name__}

    now = time.time()
    for name in settings.required_workers:
        try:
            raw = await redis.get(HEARTBEAT_KEY.format(name=name))
        except Exception:
            raw = None
        if raw is None:
            checks[name] = {"ok": False, "error": "no heartbeat"}
            continue
        beat = json.loads(raw)
        age = round(now - float(beat["ts"]), 1)
        checks[name] = {
            "ok": age <= settings.heartbeat_max_age_s,
            "age_s": age,
            **{k: v for k, v in beat.items() if k != "ts"},
        }

    ok = all(c["ok"] for c in checks.values())
    response.status_code = 200 if ok else 503
    return {"status": "ok" if ok else "degraded", "checks": checks}
