"""Types shared by several artifacts: stage keys, catalog references, assumptions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from fl_domain.base import Code, DomainModel, LocalId
from fl_domain.prov import Source

__all__ = [
    "STAGE_DEPS",
    "STAGE_ORDER",
    "Assumption",
    "CatalogRef",
    "PriceRange",
    "Range",
    "StageKey",
    "downstream_closure",
    "upstream_closure",
]

StageKey = Literal[
    "intake",
    "bom",
    "routing",
    "quality",
    "capacity",
    "inventory",
    "workorders_shifts",
    "utilities",
    "layout",
    "simulation",
    "finance",
    "reports",
    "vendors",
]

STAGE_ORDER: tuple[StageKey, ...] = (
    "intake",
    "bom",
    "routing",
    "quality",
    "capacity",
    "inventory",
    "workorders_shifts",
    "utilities",
    "layout",
    "simulation",
    "finance",
    "reports",
    "vendors",
)

# Direct dependencies of each stage (docs/02 §1). The stage graph is a DAG; the parent
# LangGraph graph and stale propagation are both built from this map.
STAGE_DEPS: dict[StageKey, tuple[StageKey, ...]] = {
    "intake": (),
    "bom": ("intake",),
    "routing": ("bom",),
    "quality": ("routing",),
    "capacity": ("routing", "quality"),
    "inventory": ("bom", "capacity"),
    "workorders_shifts": ("routing", "capacity"),
    "utilities": ("capacity", "inventory", "workorders_shifts"),
    "layout": ("quality", "capacity", "inventory", "workorders_shifts", "utilities"),
    "simulation": ("capacity", "workorders_shifts", "layout"),
    "finance": (
        "bom",
        "capacity",
        "inventory",
        "workorders_shifts",
        "utilities",
        "layout",
        "vendors",
    ),
    "reports": (
        "intake",
        "bom",
        "routing",
        "quality",
        "capacity",
        "inventory",
        "workorders_shifts",
        "utilities",
        "layout",
        "simulation",
        "finance",
        "vendors",
    ),
    "vendors": ("bom",),
}


def downstream_closure(stage: StageKey) -> tuple[StageKey, ...]:
    """Every stage that depends on ``stage`` directly or transitively, in STAGE_ORDER."""
    found: set[StageKey] = set()
    frontier: list[StageKey] = [stage]
    while frontier:
        cur = frontier.pop()
        for s, deps in STAGE_DEPS.items():
            if cur in deps and s not in found:
                found.add(s)
                frontier.append(s)
    return tuple(s for s in STAGE_ORDER if s in found)


def upstream_closure(stage: StageKey) -> tuple[StageKey, ...]:
    """Every stage ``stage`` depends on, directly or transitively, in STAGE_ORDER."""
    found: set[StageKey] = set()
    frontier: list[StageKey] = [stage]
    while frontier:
        for d in STAGE_DEPS[frontier.pop()]:
            if d not in found:
                found.add(d)
                frontier.append(d)
    return tuple(s for s in STAGE_ORDER if s in found)


class CatalogRef(DomainModel):
    """Reference to a library entry by code and the catalog version it was taken from."""

    code: Code
    version: str | None = Field(default=None, description="Catalog version, e.g. 2026.11.1")


class Range(DomainModel):
    """A low/base/high range (finance and catalog figures are always ranges, docs/09 §1)."""

    low: float
    base: float
    high: float
    unit: str | None = None

    def ordered(self) -> bool:
        return self.low <= self.base <= self.high


class PriceRange(DomainModel):
    """A price range in INR with its date and label (catalog estimate, quote, web)."""

    low: float = Field(ge=0)
    base: float = Field(ge=0)
    high: float = Field(ge=0)
    as_of: str = Field(pattern=r"^\d{4}-\d{2}(-\d{2})?$")
    source: str = Field(description="'catalog-estimate', a URL, or 'vendor-quote:<id>'")


class Assumption(DomainModel):
    """One row of the assumptions register (docs/09 §3.1 item 12)."""

    id: LocalId
    stage: StageKey | None = None
    path: str = Field(description="Where the value lives, e.g. 'variants[0].params.n_screws'")
    value: Any = None
    unit: str | None = None
    source: Source | Literal["pack", "catalog"]
    confidence: float = Field(ge=0, le=1)
    ref: str | None = None
    note: str | None = None
