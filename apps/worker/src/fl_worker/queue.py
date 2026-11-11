"""Job queue (procrastinate on Postgres). Decision: docs/phase-reports/P0.md spike e, ADR-019."""

import logging
import os
import platform

import procrastinate
import psycopg

log = logging.getLogger(__name__)

QUEUE_ENGINES = "engines"
QUEUE_CAD = "cad"

app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=os.environ.get("QUEUE_DATABASE_URL", ""))
)


@app.task(name="ping", queue=QUEUE_ENGINES)
async def ping() -> dict[str, str]:
    return {"arch": platform.machine(), "role": os.environ.get("WORKER_ROLE", "worker")}


@app.task(name="cad_ping", queue=QUEUE_CAD)
async def cad_ping() -> dict[str, str]:
    from importlib.metadata import version

    import OCP  # noqa: F401  (proves the native lib loads)

    return {"arch": platform.machine(), "cadquery_ocp": version("cadquery-ocp")}


@app.task(name="retry_stalled_jobs", queue=QUEUE_ENGINES, queueing_lock="retry_stalled_jobs")
async def retry_stalled_jobs(timestamp: int) -> int:
    """Re-queue jobs whose worker stopped heart-beating (crash recovery, docs/02 §7).

    Procrastinate detects dead workers by heartbeat (default: stalled after 30 s) but does
    not retry their jobs by itself; this sweep does. docs/02 §7 says "job retried once":
    jobs carry a retry limit set at defer time, and the sweep respects it.
    """
    stalled = list(await app.job_manager.get_stalled_jobs())
    for job in stalled:
        log.warning(
            "retrying stalled job %s (%s), attempts=%s", job.id, job.task_name, job.attempts
        )
        await app.job_manager.retry_job(job)
    await app.job_manager.prune_stalled_workers(seconds_since_heartbeat=60)
    return len(stalled)


# Only the main worker schedules the sweep; two deferrers would race on the queueing lock.
if os.environ.get("WORKER_ROLE", "worker") == "worker":
    app.periodic(cron="* * * * *")(retry_stalled_jobs)


async def ensure_schema() -> None:
    """Apply procrastinate's schema once, safely with several workers starting together.

    Uses one dedicated connection and a transaction-scoped advisory lock. (A session lock
    taken and released through the pool can be released on a different connection, leak,
    and block the next worker forever. Observed in P0.)
    """
    async with (
        await psycopg.AsyncConnection.connect(
            os.environ.get("QUEUE_DATABASE_URL", ""), autocommit=False
        ) as conn,
        conn.transaction(),
    ):
        await conn.execute("SELECT pg_advisory_xact_lock(424242)")
        cur = await conn.execute("SELECT to_regclass('procrastinate_jobs') IS NOT NULL")
        row = await cur.fetchone()
        if not (row and row[0]):
            log.info("applying procrastinate schema")
            await conn.execute(app.schema_manager.get_schema())
