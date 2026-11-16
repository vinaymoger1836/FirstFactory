"""``fl-agents setup`` (checkpoint tables) and ``fl-agents worker`` (queue ``pipeline``)."""

from __future__ import annotations

import argparse
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

from fl_agents.queue import QUEUE_PIPELINE, app, ensure_schema
from fl_agents.settings import get_settings

log = logging.getLogger("fl_agents")

HEARTBEAT_EVERY_S = 10
HEARTBEAT_TTL_S = 60
HEARTBEAT_FILE = Path("/tmp/fl-heartbeat")  # noqa: S108 (per-container liveness file)


def _versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in ("procrastinate", "langgraph", "langgraph-checkpoint-postgres", "litellm"):
        with contextlib.suppress(PackageNotFoundError):
            out[p] = version(p)
    return out


async def _heartbeat() -> None:
    s = get_settings()
    redis = Redis.from_url(s.redis_url)
    libs = _versions()
    try:
        while True:
            beat = {
                "ts": time.time(),
                "role": "agents",
                "queue": QUEUE_PIPELINE,
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "arch": platform.machine(),
                "python": platform.python_version(),
                "llm_mode": s.llm_mode,
                "libs": libs,
            }
            await asyncio.to_thread(HEARTBEAT_FILE.write_text, str(beat["ts"]))
            try:
                await redis.set("fl:heartbeat:agents", json.dumps(beat), ex=HEARTBEAT_TTL_S)
            except Exception as exc:
                log.warning("heartbeat failed: %s", exc)
            await asyncio.sleep(HEARTBEAT_EVERY_S)
    finally:
        await redis.aclose()


async def _wait_for_db(timeout_s: float = 120) -> None:
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
    log.exception(msg)
    logging.shutdown()
    os._exit(1)


async def _worker() -> None:
    from fl_agents.pipeline import runner  # registers the pipeline_advance task

    s = get_settings()
    async with app.open_async():
        try:
            await _wait_for_db()
            await ensure_schema()
            await runner.pipeline_graph()  # build once; fails fast on graph errors
            hb = asyncio.create_task(_heartbeat())
            log.info("agents worker: queue=%s llm_mode=%s", QUEUE_PIPELINE, s.llm_mode)
            await app.run_worker_async(
                queues=[QUEUE_PIPELINE],
                concurrency=s.pipeline_concurrency,
                name="agents",
                delete_jobs="successful",
            )
            hb.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            _die("agents worker stopped on a fatal error; exiting for restart")


async def _setup() -> None:
    from fl_agents.pipeline import runner

    await runner.setup_checkpointer()
    log.info("LangGraph checkpoint tables are ready")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fl-agents", description=__doc__)
    ap.add_argument("command", choices=["setup", "worker"])
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    if args.command == "setup":
        asyncio.run(_setup())
        return 0
    try:
        asyncio.run(_worker())
    except KeyboardInterrupt:
        pass
    except Exception:
        _die("agents worker crashed; exiting for restart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
