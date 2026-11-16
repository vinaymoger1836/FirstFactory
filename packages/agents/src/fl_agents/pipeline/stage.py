"""Stage subgraph template (docs/02 §3): the same shape for every stage.

    load_inputs → plan → tool_loop ⇄ tools → draft → validate ─fail (≤2)→ repair → validate
                                                         └ pass → compute → summarise → approval

A stage supplies a :class:`StageSpec` with hooks; this module turns it into a LangGraph
subgraph. Graph state holds IDs only (ADR-003); drafts, per-source results and artifacts
live in ``stage_run`` rows.

Pauses (ADR-025): a hook that raises ``PauseRequired`` (token budget or provider quota)
sets the run to ``paused`` and interrupts; on resume the budget is raised and the same
node runs again. Hooks keep finished work in ``stage_run.work`` so nothing is redone.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict, TypeVar
from uuid import UUID

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from fl_agents.lifecycle import (
    approve_run,
    assumptions_for,
    compute_inputs,
    validate_artifact_dict,
)
from fl_agents.llm.errors import LlmError, PauseRequired
from fl_agents.llm.router import CallCtx, Role
from fl_agents.pipeline.runtime import Runtime, runtime
from fl_agents.store.repo import Row
from fl_domain.base import Issue
from fl_domain.common import StageKey
from fl_domain.library.catalog import Catalog

__all__ = [
    "MAX_REPAIRS",
    "STEPS",
    "StageCtx",
    "StageSpec",
    "StageState",
    "Summary",
    "build_stage_graph",
    "keep_first",
]

log = logging.getLogger(__name__)

MAX_REPAIRS = 2
STEPS = (
    "load_inputs",
    "plan",
    "tool_loop",
    "draft",
    "validate",
    "compute",
    "summarise",
    "approval",
)
T = TypeVar("T", bound=BaseModel)


def keep_first(a: str | None, b: str | None) -> str | None:
    """Reducer for keys every branch carries unchanged (project and thread IDs)."""
    return a or b


class StageState(TypedDict, total=False):
    project_id: Annotated[str, keep_first]
    thread_id: Annotated[str, keep_first]
    run_id: str | None
    action: str | None
    repairs: int


@dataclass
class Summary:
    text: str
    questions: list[dict[str, Any]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


@dataclass
class StageCtx:
    """What a hook sees: services plus the project and the current run row."""

    rt: Runtime
    stage: StageKey
    project: Row
    run: Row

    @property
    def work(self) -> dict[str, Any]:
        w: dict[str, Any] = self.run["work"] or {}
        return w

    @property
    def run_id(self) -> UUID:
        rid: UUID = self.run["id"]
        return rid

    @property
    def project_id(self) -> UUID:
        pid: UUID = self.project["id"]
        return pid

    def call_ctx(self) -> CallCtx:
        return CallCtx(
            project_id=self.project_id, run_id=self.run_id, budget=int(self.run["token_budget"])
        )

    async def refresh(self) -> Row:
        row = await self.rt.store.run(self.run_id)
        assert row is not None
        self.run = row
        return row

    async def save_work(self, patch: dict[str, Any]) -> None:
        await self.rt.store.merge_work(self.run_id, patch)
        await self.refresh()

    async def llm(self, role: Role, messages: list[dict[str, Any]], out: type[T], **kw: Any) -> T:
        return await self.rt.router.structured(role, messages, out, self.call_ctx(), **kw)

    async def catalog(self) -> Catalog:
        return await self.rt.store.catalog(self.project["catalog_version"])

    async def emit(self, kind: str, payload: dict[str, Any]) -> None:
        await self.rt.store.emit(
            self.project_id,
            kind,
            {"stage": self.stage, **payload},
            stage=self.stage,
            run_id=self.run_id,
        )


class StageSpec:
    """Hooks for one stage. Defaults make an engine-only stage with nothing to plan."""

    key: StageKey
    placeholder: bool = False
    auto_approve: bool = False

    async def plan(self, ctx: StageCtx) -> None:
        return None

    async def next_tool(self, ctx: StageCtx) -> str | None:
        return None

    async def run_tool(self, ctx: StageCtx, action: str) -> None:
        return None

    async def draft(self, ctx: StageCtx) -> dict[str, Any] | None:
        return None

    async def repair(
        self, ctx: StageCtx, draft: dict[str, Any], issues: list[Issue]
    ) -> dict[str, Any] | None:
        return None

    async def compute(self, ctx: StageCtx, artifact: dict[str, Any]) -> dict[str, Any]:
        return artifact

    async def summarise(self, ctx: StageCtx, artifact: dict[str, Any] | None) -> Summary:
        return Summary(text="")


# ── helpers ───────────────────────────────────────────────────────────


async def _ctx(spec: StageSpec, state: StageState) -> StageCtx:
    rt = runtime()
    project = await rt.store.project(UUID(state["project_id"]))
    run = await rt.store.run(UUID(str(state["run_id"])))
    assert project is not None
    assert run is not None
    return StageCtx(rt=rt, stage=spec.key, project=project, run=run)


async def _progress(ctx: StageCtx, step: str) -> None:
    i = STEPS.index(step)
    await ctx.emit(
        "progress",
        {
            "step": step,
            "index": i + 1,
            "total": len(STEPS),
            "pct": round(100 * (i + 1) / len(STEPS)),
            "version": ctx.run["version"],
        },
    )


async def _fail(ctx: StageCtx, issues: list[Issue]) -> Command[Any]:
    await ctx.refresh()
    if ctx.run["status"] in ("running", "paused"):
        await ctx.rt.store.update_run(
            ctx.run_id, status="failed", issues=[i.model_dump(mode="json") for i in issues]
        )
    await ctx.emit(
        "status",
        {"status": "failed", "version": ctx.run["version"], "issues": [i.message for i in issues]},
    )
    return Command(goto=END)


async def _pausable[R](
    ctx: StageCtx, node: str, fn: Callable[[], Awaitable[R]]
) -> tuple[R | None, Command[Any] | None]:
    """Run a hook; on budget/quota exhaustion pause, interrupt, and retry the node."""
    try:
        return await fn(), None
    except PauseRequired as e:
        run = await ctx.refresh()
        if run["status"] == "running":
            await ctx.rt.store.update_run(ctx.run_id, status="paused")
            await ctx.emit("paused", {"version": run["version"], **e.payload()})
        decision: dict[str, Any] = interrupt(
            {
                "kind": e.kind,
                "stage": ctx.stage,
                "run_id": str(ctx.run_id),
                "version": run["version"],
                **e.payload(),
            }
        )
        run = await ctx.refresh()
        extra = int(decision.get("extra_budget") or 0)
        if extra <= 0 and e.kind == "budget":
            extra = ctx.rt.settings.budget_for(ctx.stage)
        await ctx.rt.store.update_run(
            ctx.run_id, status="running", token_budget=int(run["token_budget"]) + extra
        )
        await ctx.emit("status", {"status": "running", "version": run["version"], "resumed": True})
        return None, Command(goto=node)
    except LlmError as e:
        return None, await _fail(ctx, [Issue(code="llm", message=str(e))])
    except GraphBubbleUp:
        raise  # LangGraph control flow (interrupts), never a failure
    except Exception as e:  # a bug or bad input must fail the stage, not leave it "running"
        log.exception("stage %s step %s failed", ctx.stage, node)
        return None, await _fail(ctx, [Issue(code="error", message=f"{type(e).__name__}: {e}")])


async def _thread_active(state: StageState) -> bool:
    th = await runtime().store.thread(state["thread_id"])
    return th is not None and th["status"] == "active"


def _issue_list(raw: list[Any]) -> list[Issue]:
    return [Issue.model_validate(i) for i in raw]


# ── graph builder ─────────────────────────────────────────────────────


def build_stage_graph(spec: StageSpec) -> CompiledStateGraph[Any, Any, Any, Any]:
    stage = spec.key

    async def load_inputs(state: StageState) -> Command[Any]:
        rt = runtime()
        if not await _thread_active(state):
            return Command(goto=END)
        pid = UUID(state["project_id"])
        project = await rt.store.project(pid)
        assert project is not None
        h, upstream, ready = await compute_inputs(rt.store, project, stage)
        if not ready:
            # An upstream stage is not approved (failed or replaced): nothing to do here.
            return Command(goto=END, update={"run_id": None})
        ok = await rt.store.latest_approved(pid, stage)
        latest = await rt.store.latest_run(pid, stage)
        if ok and ok["inputs_hash"] == h and (latest is None or latest["status"] != "stale"):
            await rt.store.emit(
                pid,
                "skipped",
                {"stage": stage, "version": ok["version"]},
                stage=stage,
                run_id=ok["id"],
            )
            return Command(goto=END, update={"run_id": str(ok["id"])})
        if (
            latest
            and latest["thread_id"] == state["thread_id"]
            and latest["inputs_hash"] == h
            and latest["status"] in ("running", "paused", "awaiting_approval")
        ):
            run = latest  # node re-executed after a restart: carry on with the same run
        else:
            run = await rt.store.create_run(
                pid,
                stage,
                status="running",
                inputs_hash=h,
                upstream=upstream,
                params={},
                placeholder=spec.placeholder,
                token_budget=rt.settings.budget_for(stage, project["stage_budgets"] or {}),
                thread_id=state["thread_id"],
            )
        ctx = StageCtx(rt=rt, stage=stage, project=project, run=run)
        await ctx.emit("status", {"status": "running", "version": run["version"]})
        await _progress(ctx, "load_inputs")
        nxt = "approval" if run["status"] == "awaiting_approval" else "plan"
        return Command(goto=nxt, update={"run_id": str(run["id"]), "repairs": 0})

    async def plan(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        await _progress(ctx, "plan")
        _, cmd = await _pausable(ctx, "plan", lambda: spec.plan(ctx))
        return cmd or Command(goto="tool_loop")

    async def tool_loop(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        action = await spec.next_tool(ctx)
        if action is None:
            return Command(goto="draft", update={"action": None})
        await _progress(ctx, "tool_loop")
        return Command(goto="tools", update={"action": action})

    async def tools(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        action = str(state.get("action"))
        _, cmd = await _pausable(ctx, "tools", lambda: spec.run_tool(ctx, action))
        return cmd or Command(goto="tool_loop")

    async def draft(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        await _progress(ctx, "draft")
        result, cmd = await _pausable(ctx, "draft", lambda: spec.draft(ctx))
        if cmd:
            return cmd
        if result is None:  # placeholder or nothing to draft
            await ctx.save_work({"_draft": None})
            return Command(goto="summarise")
        await ctx.save_work({"_draft": result})
        return Command(goto="validate")

    async def validate(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        await _progress(ctx, "validate")
        data = ctx.work.get("_draft") or {}
        normalised, issues = await validate_artifact_dict(ctx.rt.store, ctx.project, stage, data)
        errors = [i for i in issues if i.severity == "error"]
        await ctx.rt.store.update_run(
            ctx.run_id, issues=[i.model_dump(mode="json") for i in issues]
        )
        if normalised is not None and not errors:
            await ctx.save_work({"_valid": normalised})
            return Command(goto="compute")
        repairs = int(state.get("repairs") or 0)
        if repairs >= MAX_REPAIRS:
            return await _fail(ctx, errors or issues)
        return Command(goto="repair", update={"repairs": repairs + 1})

    async def repair(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        issues = _issue_list(ctx.run["issues"] or [])
        result, cmd = await _pausable(
            ctx, "repair", lambda: spec.repair(ctx, ctx.work.get("_draft") or {}, issues)
        )
        if cmd:
            return cmd
        if result is None:
            return await _fail(ctx, [i for i in issues if i.severity == "error"] or issues)
        await ctx.save_work({"_draft": result})
        return Command(goto="validate")

    async def compute(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        await _progress(ctx, "compute")
        try:
            artifact = await spec.compute(ctx, ctx.work["_valid"])
        except Exception as e:  # engine errors fail the stage with the message shown
            log.exception("compute failed for %s", stage)
            return await _fail(ctx, [Issue(code="compute", message=str(e))])
        normalised, issues = await validate_artifact_dict(
            ctx.rt.store, ctx.project, stage, artifact
        )
        errors = [i for i in issues if i.severity == "error"]
        if normalised is None or errors:
            return await _fail(ctx, errors or issues)
        await ctx.rt.store.update_run(
            ctx.run_id,
            artifact=normalised,
            issues=[i.model_dump(mode="json") for i in issues],
            assumptions=assumptions_for(stage, normalised),
        )
        return Command(goto="summarise")

    async def summarise(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        await _progress(ctx, "summarise")
        artifact = ctx.run["artifact"]
        result, cmd = await _pausable(ctx, "summarise", lambda: spec.summarise(ctx, artifact))
        if cmd:
            return cmd
        assert result is not None
        notices = list(dict.fromkeys([*(ctx.run["notices"] or []), *result.notices]))
        await ctx.rt.store.update_run(
            ctx.run_id, summary=result.text, questions=result.questions, notices=notices
        )
        return Command(goto="approval")

    async def approval(state: StageState) -> Command[Any]:
        ctx = await _ctx(spec, state)
        if spec.auto_approve:
            await _progress(ctx, "approval")
            await approve_run(ctx.rt.store, ctx.run)
            return Command(goto=END)
        if ctx.run["status"] == "running":
            await ctx.rt.store.update_run(ctx.run_id, status="awaiting_approval")
            await _progress(ctx, "approval")
            await ctx.emit("status", {"status": "awaiting_approval", "version": ctx.run["version"]})
        decision: dict[str, Any] = interrupt(
            {
                "kind": "approval",
                "stage": stage,
                "run_id": str(ctx.run_id),
                "version": ctx.run["version"],
            }
        )
        if not await _thread_active(state):
            return Command(goto=END)
        return await _decide(ctx, decision)

    async def _decide(ctx: StageCtx, decision: dict[str, Any]) -> Command[Any]:
        store = ctx.rt.store
        action = decision.get("action")
        if action == "approve":
            version = int(decision.get("version") or ctx.run["version"])
            row = await store.run_version(ctx.project_id, stage, version)
            if row is None or row["status"] not in ("awaiting_approval", "approved"):
                return Command(goto="approval")
            await approve_run(store, row)
            return Command(goto=END, update={"run_id": str(row["id"])})
        if action in ("answer", "rerun", "reject"):
            await ctx.refresh()
            old = ctx.run
            note = str(decision.get("note") or "")
            answers: dict[str, Any] = dict(decision.get("answers") or {})
            if action == "answer":
                questions = [
                    {
                        **q,
                        "answer": str(answers[q["id"]]) if q["id"] in answers else q.get("answer"),
                    }
                    for q in (old["questions"] or [])
                ]
                work = {
                    **(old["work"] or {}),
                    "answers": {**(old["work"] or {}).get("answers", {}), **answers},
                }
                params = old["params"] or {}
                goto = "draft"
            else:
                questions = []
                work = {}
                params = {**(old["params"] or {}), "note": note}
                goto = "plan"
            if old["status"] == "awaiting_approval":
                await store.update_run(
                    old["id"], status="rejected" if action == "reject" else "stale"
                )
            new = await store.create_run(
                ctx.project_id,
                stage,
                status="running",
                inputs_hash=old["inputs_hash"],
                upstream=old["upstream"],
                params=params,
                questions=questions,
                placeholder=old["placeholder"],
                token_budget=int(old["token_budget"]),
                work=work,
                thread_id=old["thread_id"],
            )
            await store.emit(
                ctx.project_id,
                "version",
                {"stage": stage, "version": new["version"], "status": "running", "action": action},
                stage=stage,
                run_id=new["id"],
            )
            return Command(goto=goto, update={"run_id": str(new["id"]), "repairs": 0})
        return Command(goto="approval")

    g: StateGraph[StageState, Any, StageState, StageState] = StateGraph(StageState)
    for name, fn in (
        ("load_inputs", load_inputs),
        ("plan", plan),
        ("tool_loop", tool_loop),
        ("tools", tools),
        ("draft", draft),
        ("validate", validate),
        ("repair", repair),
        ("compute", compute),
        ("summarise", summarise),
        ("approval", approval),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "load_inputs")
    return g.compile(name=stage)
