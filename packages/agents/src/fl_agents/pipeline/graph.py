"""Parent pipeline graph: one node per stage, edges from STAGE_DEPS (docs/02 §1, §3).

Each node is a compiled stage subgraph. A stage with several dependencies waits for all of
them (LangGraph join edge). The checkpointer is given to the parent only; subgraphs
inherit it, so approvals can pause the graph indefinitely.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from fl_domain.common import STAGE_DEPS, STAGE_ORDER, StageKey
from fl_agents.pipeline.stage import StageSpec, build_stage_graph, keep_first
from fl_agents.pipeline.stubs import StubSpec

__all__ = ["PipelineState", "build_pipeline", "stage_specs"]


class PipelineState(TypedDict, total=False):
    project_id: Annotated[str, keep_first]
    thread_id: Annotated[str, keep_first]


def stage_specs() -> dict[StageKey, StageSpec]:
    from fl_agents.stages.intake.spec import IntakeSpec

    specs: dict[StageKey, StageSpec] = {s: StubSpec(s) for s in STAGE_ORDER}
    specs["intake"] = IntakeSpec()
    return specs


def build_pipeline(
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    specs = stage_specs()
    g: StateGraph[PipelineState, Any, PipelineState, PipelineState] = StateGraph(PipelineState)
    for s in STAGE_ORDER:
        g.add_node(s, build_stage_graph(specs[s]))
    dependents = {s for deps in STAGE_DEPS.values() for s in deps}
    for s in STAGE_ORDER:
        deps = STAGE_DEPS[s]
        if not deps:
            g.add_edge(START, s)
        elif len(deps) == 1:
            g.add_edge(deps[0], s)
        else:
            g.add_edge(list(deps), s)
        if s not in dependents:
            g.add_edge(s, END)
    return g.compile(checkpointer=checkpointer)
