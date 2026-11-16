"""Placeholder stages (S2-S12, vendors) until their phases build them (ADR-025).

A stub runs the full subgraph template with no-op hooks and writes an approved
``placeholder`` run with no artifact, so versions, hashes and staleness behave exactly as
they will for real stages.
"""

from __future__ import annotations

from typing import Any

from fl_agents.pipeline.stage import StageCtx, StageSpec, Summary
from fl_domain.common import StageKey

__all__ = ["BUILT_IN", "StubSpec"]

# Phase in which each stage is built (docs/13 §1); shown on the placeholder.
BUILT_IN: dict[str, str] = {
    "bom": "P3",
    "routing": "P3",
    "quality": "P3",
    "vendors": "P3",
    "capacity": "P4",
    "inventory": "P4",
    "workorders_shifts": "P5",
    "utilities": "P5",
    "layout": "P6",
    "simulation": "P7",
    "finance": "P8",
    "reports": "P8",
}


class StubSpec(StageSpec):
    placeholder = True
    auto_approve = True

    def __init__(self, key: StageKey) -> None:
        self.key = key

    async def summarise(self, ctx: StageCtx, artifact: dict[str, Any] | None) -> Summary:
        phase = BUILT_IN.get(self.key, "a later phase")
        return Summary(
            text=f"Placeholder: this stage is built in {phase}. Nothing was computed.",
            notices=[f"Not built yet ({phase}). Approved automatically as a placeholder."],
        )
