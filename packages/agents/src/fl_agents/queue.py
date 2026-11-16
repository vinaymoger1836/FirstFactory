"""Procrastinate app shared by the API and the agents worker (ADR-019, ADR-024).

Jobs for other workers are deferred **by task name**, so neither the API nor the agents
service imports CAD or engine code:

- ``pipeline_advance`` (queue ``pipeline``, agents service): run or resume a project graph.
- ``cad_extract`` (queue ``cad``, cad-worker sandbox): STEP/STL → tree, bboxes, GLB.
- ``pdf_extract`` (queue ``engines``, worker, no egress): PDF → page text.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import procrastinate
import psycopg

from fl_agents.settings import get_settings

__all__ = [
    "QUEUE_CAD",
    "QUEUE_ENGINES",
    "QUEUE_PIPELINE",
    "app",
    "defer_advance",
    "defer_cad_extract",
    "defer_pdf_extract",
    "ensure_schema",
    "with_app_name",
]

log = logging.getLogger(__name__)

QUEUE_PIPELINE = "pipeline"
QUEUE_CAD = "cad"
QUEUE_ENGINES = "engines"


def with_app_name(url: str, name: str) -> str:
    """Add ``application_name`` to a libpq URL (P0 watch item: one name per service)."""
    parts = urlsplit(url)
    sep = "&" if parts.query else ""
    return urlunsplit(
        parts._replace(query=parts.query + sep + urlencode({"application_name": name}))
    )


def _make_app(app_name: str) -> procrastinate.App:
    return procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            conninfo=with_app_name(get_settings().queue_database_url, app_name)
        )
    )


app = _make_app("fl-queue")


async def defer_advance(project_id: str, thread_id: str, resume: dict[str, Any] | None) -> int:
    """Queue one step of a project's pipeline. One job per project at a time (lock)."""
    return await app.configure_task(
        name="pipeline_advance",
        queue=QUEUE_PIPELINE,
        lock=f"project:{project_id}",
        retry=1,  # docs/02 §7: a crashed job is retried once; the checkpoint resumes the graph
    ).defer_async(project_id=project_id, thread_id=thread_id, resume=resume)


async def defer_cad_extract(project_id: str, attachment_id: str) -> int:
    return await app.configure_task(name="cad_extract", queue=QUEUE_CAD, retry=1).defer_async(
        project_id=project_id, attachment_id=attachment_id
    )


async def defer_pdf_extract(project_id: str, attachment_id: str) -> int:
    return await app.configure_task(name="pdf_extract", queue=QUEUE_ENGINES, retry=1).defer_async(
        project_id=project_id, attachment_id=attachment_id
    )


async def ensure_schema() -> None:
    """Apply procrastinate's schema once (same advisory lock as fl_worker, P0 §4.1)."""
    async with (
        await psycopg.AsyncConnection.connect(
            get_settings().queue_database_url, autocommit=False
        ) as conn,
        conn.transaction(),
    ):
        await conn.execute("SELECT pg_advisory_xact_lock(424242)")
        cur = await conn.execute("SELECT to_regclass('procrastinate_jobs') IS NOT NULL")
        row = await cur.fetchone()
        if not (row and row[0]):
            log.info("applying procrastinate schema")
            await conn.execute(app.schema_manager.get_schema())
