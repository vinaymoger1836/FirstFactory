"""Identical tasks for both queues. Events are appended to a JSONL file shared by processes."""

import asyncio
import json
import os
import time

import procrastinate
from arq.connections import RedisSettings

EVENTS = "/tmp/events.jsonl"


def rec(queue: str, kind: str, **kw: object) -> None:
    with open(EVENTS, "a") as f:
        f.write(json.dumps({"q": queue, "kind": kind, "t": time.time(), **kw}) + "\n")


# ── arq ────────────────────────────────────────────────────────────
async def arq_noop(ctx: dict, t_enq: float) -> None:
    rec("arq", "start", job="noop", lat=time.time() - t_enq)


async def arq_sleepy(ctx: dict, secs: float, tag: str) -> None:
    rec("arq", "start", job=tag, attempt=ctx["job_try"])
    await asyncio.sleep(secs)
    rec("arq", "done", job=tag)


class WorkerSettings:
    functions = [arq_noop, arq_sleepy]
    redis_settings = RedisSettings(host=os.environ.get("REDIS_HOST", "redis"))
    job_timeout = int(os.environ.get("ARQ_JOB_TIMEOUT", "30"))
    max_jobs = 1
    poll_delay = float(os.environ.get("ARQ_POLL_DELAY", "0.5"))


# ── procrastinate ──────────────────────────────────────────────────
app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=os.environ.get("PG_DSN", ""))
)


@app.task(name="noop")
async def proc_noop(t_enq: float) -> None:
    rec("proc", "start", job="noop", lat=time.time() - t_enq)


@app.task(name="sleepy", pass_context=True)
async def proc_sleepy(context: procrastinate.JobContext, secs: float, tag: str) -> None:
    rec("proc", "start", job=tag, attempt=context.job.attempts)
    await asyncio.sleep(secs)
    rec("proc", "done", job=tag)
