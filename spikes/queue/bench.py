"""Queue spike: arq+Redis vs procrastinate (Postgres). Run via `docker compose -f compose.yml up`.

Measures, per queue, with one worker process at concurrency 1 and library defaults:
  1. enqueue→start latency for 200 jobs enqueued 20 ms apart
  2. drain time for a 500-job burst
  3. idle worker RSS
  4. crash recovery: SIGKILL the worker mid-job, start a new one, time until the job completes
"""

import asyncio
import json
import os
import platform
import signal
import statistics
import subprocess
import sys
import time
from importlib.metadata import version

import psutil
import tasks
from arq import create_pool
from arq.connections import RedisSettings

ARQ_WORKER = [sys.executable, "-m", "arq", "tasks.WorkerSettings"]
PROC_WORKER = [sys.executable, "proc_worker.py"]
N_BURST = 200


def events(q: str) -> list[dict]:
    if not os.path.exists(tasks.EVENTS):
        return []
    with open(tasks.EVENTS) as f:
        return [e for e in map(json.loads, f) if e["q"] == q]


def reset_events() -> None:
    if os.path.exists(tasks.EVENTS):
        os.remove(tasks.EVENTS)


async def wait_for(pred, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


def rss_mb(pid: int) -> float:
    p = psutil.Process(pid)
    return round(sum(c.memory_info().rss for c in [p, *p.children(recursive=True)]) / 2**20, 1)


async def run_queue(name: str, worker_cmd: list[str], enqueue, sweeper=None, env=None) -> dict:
    res: dict = {}
    reset_events()
    w = subprocess.Popen(worker_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    await asyncio.sleep(3)

    # 1. latency
    for _ in range(200):
        await enqueue("noop", time.time())
        await asyncio.sleep(0.02)
    await wait_for(lambda: len([e for e in events(name) if e["job"] == "noop"]) >= 200, 300)
    lats = sorted(e["lat"] * 1000 for e in events(name) if e["job"] == "noop")
    res["latency_ms"] = {
        "n": len(lats),
        "p50": round(statistics.median(lats), 1),
        "p95": round(lats[int(len(lats) * 0.95) - 1], 1),
        "max": round(lats[-1], 1),
    }

    # 2. burst drain
    reset_events()
    t0 = time.time()
    for _ in range(N_BURST):
        await enqueue("noop", time.time())
    t_enq = time.time() - t0
    drained = await wait_for(lambda: len(events(name)) >= N_BURST, 300)
    res["burst"] = {
        "jobs": N_BURST,
        "drained": drained,
        "enqueue_s": round(t_enq, 2),
        "drain_s": round(time.time() - t0, 2),
        "jobs_per_s": round(N_BURST / (time.time() - t0), 1),
    }

    # 3. idle RSS
    await asyncio.sleep(2)
    res["idle_worker_rss_mb"] = rss_mb(w.pid)

    # 4. crash recovery
    reset_events()
    await enqueue("sleepy", 15, "crash")
    await wait_for(lambda: any(e["job"] == "crash" for e in events(name)), 30)
    await asyncio.sleep(2)
    w.send_signal(signal.SIGKILL)
    w.wait()
    t_kill = time.time()
    w = subprocess.Popen(worker_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    stop = asyncio.Event()
    sweep_task = asyncio.create_task(sweeper(stop)) if sweeper else None
    ok = await wait_for(
        lambda: any(e["kind"] == "done" and e["job"] == "crash" for e in events(name)), 150
    )
    stop.set()
    if sweep_task:
        await sweep_task
    starts = [e for e in events(name) if e["kind"] == "start" and e["job"] == "crash"]
    res["crash"] = {
        "recovered": ok,
        "attempts_seen": len(starts),
        "restart_after_kill_s": round(starts[-1]["t"] - t_kill, 1) if len(starts) > 1 else None,
        "note": "job sleeps 15 s; worker SIGKILLed 2 s after start; new worker started at once",
    }
    w.terminate()
    w.wait()
    return res


async def main() -> None:
    out: dict = {
        "arch": platform.machine(),
        "python": platform.python_version(),
        "versions": {p: version(p) for p in ("arq", "procrastinate", "redis", "psycopg")},
    }

    # arq
    pool = await create_pool(RedisSettings(host=os.environ["REDIS_HOST"]))
    await pool.flushdb()

    async def arq_enqueue(kind: str, *args):
        await pool.enqueue_job(f"arq_{kind}", *args)

    for poll in ("0.5", "0.05"):
        await pool.flushdb()
        env = {**os.environ, "ARQ_POLL_DELAY": poll}
        key = f"arq_poll_{poll}"
        out[key] = await run_queue("arq", ARQ_WORKER, arq_enqueue, env=env)
        out[key]["job_timeout_s"] = tasks.WorkerSettings.job_timeout

    # procrastinate
    app = tasks.app
    async with app.open_async():
        await app.schema_manager.apply_schema_async()

        async def proc_enqueue(kind: str, *args):
            if kind == "noop":
                await tasks.proc_noop.defer_async(t_enq=args[0])
            else:
                await tasks.proc_sleepy.defer_async(secs=args[0], tag=args[1])

        async def sweeper(stop: asyncio.Event):
            # What production would run as a periodic task: retry jobs of dead workers.
            while not stop.is_set():
                for job in await app.job_manager.get_stalled_jobs():
                    await app.job_manager.retry_job(job)
                await asyncio.sleep(2)

        out["procrastinate"] = await run_queue("proc", PROC_WORKER, proc_enqueue, sweeper)
        out["procrastinate"]["heartbeat_s"] = 10
        out["procrastinate"]["stalled_after_s"] = 30

    print("SPIKE_RESULT " + json.dumps(out))


if __name__ == "__main__":
    asyncio.run(main())
