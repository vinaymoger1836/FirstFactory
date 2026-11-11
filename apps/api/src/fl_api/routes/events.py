"""SSE demo: the shape later used for stage-run progress (docs/02 §3)."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

router = APIRouter(prefix="/events", tags=["events"])

STEPS = ["load_inputs", "plan", "tool_loop", "draft", "validate", "compute", "summarise"]


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
