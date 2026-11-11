"""Worker entry point. WORKER_ROLE=worker | cad-worker selects the queue."""

import asyncio
import contextlib
import json
import logging
import os
import platform
import socket
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from redis.asyncio import Redis

from fl_worker.queue import QUEUE_CAD, QUEUE_ENGINES, app, ensure_schema

log = logging.getLogger("fl_worker")

HEARTBEAT_EVERY_S = 10
HEARTBEAT_TTL_S = 60
# Local liveness file for the container HEALTHCHECK (per-container, never stale across restarts).
HEARTBEAT_FILE = Path("/tmp/fl-heartbeat")  # noqa: S108 (tmpfs inside the container)


def _versions(pkgs: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pkgs:
        with contextlib.suppress(PackageNotFoundError):
            out[p] = version(p)
    return out


async def heartbeat(role: str, queue: str) -> None:
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    libs = _versions(("procrastinate", "ortools", "weasyprint", "cadquery", "cadquery-ocp"))
    key = f"fl:heartbeat:{role}"
    try:
        while True:
            beat = {
                "ts": time.time(),
                "role": role,
                "queue": queue,
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "arch": platform.machine(),
                "python": platform.python_version(),
                "libs": libs,
            }
            await asyncio.to_thread(HEARTBEAT_FILE.write_text, str(beat["ts"]))
            try:
                await redis.set(key, json.dumps(beat), ex=HEARTBEAT_TTL_S)
            except Exception as exc:
                log.warning("heartbeat failed: %s", exc)
            await asyncio.sleep(HEARTBEAT_EVERY_S)
    finally:
        await redis.aclose()


async def wait_for_db(timeout_s: float = 120) -> None:
    """Postgres may be starting or in crash recovery; retry instead of dying half-initialised."""
    deadline = time.monotonic() + timeout_s
    delay = 1.0
    while True:
        try:
            await app.connector.execute_query_async("SELECT 1")
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                raise
            log.warning("database not ready (%s); retrying in %.0f s", type(exc).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


def _die(msg: str) -> None:
    """Exit hard so the container restart policy brings up a clean process.

    Used instead of unwinding: pool teardown after a lost DB connection can hang, and a
    live process without a heartbeat is worse than a restart (observed in P0).
    """
    log.exception(msg)
    logging.shutdown()
    os._exit(1)


async def main() -> None:
    role = os.environ.get("WORKER_ROLE", "worker")
    queue = QUEUE_CAD if role == "cad-worker" else QUEUE_ENGINES
    async with app.open_async():
        try:
            await wait_for_db()
            await ensure_schema()
            hb = asyncio.create_task(heartbeat(role, queue))
            # docs/11 §1: one job at a time per worker on the 2-OCPU VM.
            # The stalled-job sweep is deferred (deduplicated in Postgres) by any worker
            # but queued on `engines`, so only the main worker runs it.
            await app.run_worker_async(
                queues=[queue], concurrency=1, name=role, delete_jobs="successful"
            )
            hb.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            _die("worker stopped on a fatal error; exiting for restart")


def run() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        _die("worker crashed; exiting for restart")


if __name__ == "__main__":
    run()
