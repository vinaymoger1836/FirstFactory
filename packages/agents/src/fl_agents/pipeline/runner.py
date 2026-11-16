"""Pipeline runner: procrastinate task on queue ``pipeline`` in the agents service (ADR-024).

``pipeline_advance(project_id, thread_id, resume)`` starts a thread or resumes one
interrupt in it (approval, answers, re-run, budget continue). Jobs for one project never
overlap (procrastinate lock ``project:<id>``); the worker runs two at a time, which is the
POC limit of two concurrent pipelines (docs/02 §3).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from fl_agents.pipeline.graph import build_pipeline
from fl_agents.pipeline.runtime import runtime
from fl_agents.queue import QUEUE_PIPELINE, app, with_app_name

__all__ = ["advance", "checkpointer", "pending_interrupts", "pipeline_graph", "setup_checkpointer"]

log = logging.getLogger(__name__)

RECURSION_LIMIT = 400

_pool: AsyncConnectionPool[Any] | None = None
_graph: CompiledStateGraph[Any, Any, Any, Any] | None = None


async def checkpointer() -> AsyncPostgresSaver:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            with_app_name(runtime().settings.queue_database_url, "fl-agents-checkpoint"),
            max_size=4,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await _pool.open()
    return AsyncPostgresSaver(_pool)


async def setup_checkpointer() -> None:
    """Create LangGraph's checkpoint tables (run by the compose ``migrate`` step).

    One short-lived connection, not the pool: a pool left open keeps reconnecting after
    the event loop ends and the one-shot ``migrate`` container never exits.
    """
    url = with_app_name(runtime().settings.queue_database_url, "fl-agents-setup")
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()


async def pipeline_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    global _graph
    if _graph is None:
        _graph = build_pipeline(await checkpointer())
    return _graph


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


async def pending_interrupts(thread_id: str) -> list[dict[str, Any]]:
    graph = await pipeline_graph()
    state = await graph.aget_state(_config(thread_id))
    return [{"id": i.id, **(i.value or {})} for i in state.interrupts]


@app.task(name="pipeline_advance", queue=QUEUE_PIPELINE)
async def advance(project_id: str, thread_id: str, resume: dict[str, Any] | None = None) -> str:
    rt = runtime()
    th = await rt.store.thread(thread_id)
    if th is None or th["status"] != "active":
        log.info("thread %s is not active; nothing to do", thread_id)
        return "inactive"
    graph = await pipeline_graph()
    cfg = _config(thread_id)
    if resume is None:
        await graph.ainvoke({"project_id": project_id, "thread_id": thread_id}, cfg)
    else:
        stage = resume.get("stage")
        targets = [i for i in await pending_interrupts(thread_id) if i.get("stage") == stage]
        if not targets:
            log.warning("no pending interrupt for stage %s in %s", stage, thread_id)
            await rt.store.emit(
                UUID(project_id),
                "warning",
                {"stage": stage, "message": "Nothing is waiting for this action."},
                stage=stage,
            )
            return "no-interrupt"
        await graph.ainvoke(Command(resume={targets[0]["id"]: resume}), cfg)
    state = await graph.aget_state(cfg)
    if not state.next:
        await rt.store.set_thread_status(thread_id, "done")
        await rt.store.emit(
            UUID(project_id), "pipeline", {"status": "done", "thread_id": thread_id}
        )
        return "done"
    return "waiting"
