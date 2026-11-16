"""Server-sent events: stage progress for a project (docs/02 §3), plus the P0 demo.

``GET /projects/current/events`` authenticates with the ``X-Project-Token`` header (the web
app reads the stream with fetch, since EventSource cannot send headers). It replays events
after ``Last-Event-ID`` (or ``?after=``) from the ``stage_event`` table, then waits on
Postgres ``LISTEN fl_events`` for new ones (the agents worker inserts them).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Header, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from fl_api.db import store
from fl_api.routes.projects import CurrentRow
from fl_api.settings import get_settings

router = APIRouter(prefix="/events", tags=["events"])
project_router = APIRouter(prefix="/projects/current", tags=["events"])

STEPS = ["load_inputs", "plan", "tool_loop", "draft", "validate", "compute", "summarise"]
POLL_FALLBACK_S = 15.0


@router.get("/demo")
async def demo(
    steps: int = Query(default=len(STEPS), ge=1, le=len(STEPS)),
    interval_ms: int = Query(default=300, ge=10, le=5000),
) -> EventSourceResponse:
    async def gen() -> AsyncIterator[ServerSentEvent]:
        for i, name in enumerate(STEPS[:steps], start=1):
            await asyncio.sleep(interval_ms / 1000)
            payload = {"step": name, "index": i, "total": steps, "pct": round(100 * i / steps)}
            yield ServerSentEvent(data=json.dumps(payload), event="progress", id=str(i))
        yield ServerSentEvent(data=json.dumps({"status": "awaiting_approval"}), event="done")

    return EventSourceResponse(gen(), ping=15)


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _event(e: dict[str, Any]) -> ServerSentEvent:
    data = {
        **(e["payload"] or {}),
        "stage": e["stage"],
        "run_id": str(e["run_id"]) if e["run_id"] else None,
        "at": e["created_at"].isoformat(),
    }
    return ServerSentEvent(data=json.dumps(data, default=str), event=e["kind"], id=str(e["id"]))


@project_router.get("/events")
async def project_events(
    request: Request,
    row: CurrentRow,
    last_event_id: Annotated[str | None, Header()] = None,
    after: int | None = Query(default=None, ge=0),
) -> EventSourceResponse:
    pid = row["id"]
    start = after if after is not None else 0
    if after is None and last_event_id and last_event_id.isdigit():
        start = int(last_event_id)

    async def gen() -> AsyncIterator[ServerSentEvent]:
        cursor = start
        wake = asyncio.Event()

        def on_notify(_c: object, _pid: int, _ch: str, payload: str) -> None:
            if payload == str(pid):
                wake.set()

        conn = await asyncpg.connect(_dsn(), server_settings={"application_name": "fl-api-sse"})
        try:
            await conn.add_listener("fl_events", on_notify)
            while not await request.is_disconnected():
                wake.clear()  # before reading, so a notify during the read is not lost
                for e in await store().events_after(pid, cursor):
                    cursor = e["id"]
                    yield _event(e)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=POLL_FALLBACK_S)
        finally:
            await conn.close()

    return EventSourceResponse(gen(), ping=15)
