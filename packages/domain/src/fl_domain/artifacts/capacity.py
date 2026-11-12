"""S5 capacity and line balancing (docs/05 §3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import Code, DomainModel, LocalId, NonNeg
from fl_domain.common import PriceRange
from fl_domain.prov import Prov

__all__ = [
    "CapacityDesign",
    "CustomWorkcenter",
    "EquipmentItem",
    "OperatingPattern",
    "Station",
    "Strategy",
    "StrategyKey",
    "Takt",
    "TestPosition",
    "Workcenter",
]

StrategyKey = Literal["A", "B", "C"]


class OperatingPattern(DomainModel):
    """Captured at the start of S5 (ADR-013). Defaults are the docs/05 §3.1 values."""

    shifts_per_day: int = Field(default=1, ge=1, le=3)
    days_per_week: int = Field(default=6, ge=1, le=7)
    working_days_per_year: int = Field(default=300, ge=1, le=366)
    shift_length_s: float = Field(default=28800, gt=0)
    planned_stops_s: float = Field(default=1800, ge=0)
    target_oee: float = Field(default=0.85, gt=0, le=1)


class Takt(DomainModel):
    daily_demand: Prov[float]
    daily_demand_by_variant: dict[LocalId, Prov[float]]
    net_available_s: Prov[float]
    takt_s: Prov[float]
    pct_s: Prov[float] = Field(description="Planned cycle time = takt × target OEE.")


class Workcenter(DomainModel):
    id: LocalId = Field(description="e.g. WC-WELD-01")
    machine_class: str = Field(description="Machine class code or 'manual'.")
    zone_kind: Code
    station_ids: list[LocalId] = Field(default_factory=list)


class Station(DomainModel):
    id: LocalId
    workcenter: LocalId
    ops: list[LocalId] = Field(min_length=1)
    operators: int = Field(ge=0)
    machines: dict[str, int] = Field(default_factory=dict)
    load_s_by_variant: dict[LocalId, float] = Field(default_factory=dict)
    load_s_weighted: NonNeg = 0
    utilisation: float = Field(ge=0)
    sequence: int = Field(ge=1, description="Position along the line (1 = first).")
    line: LocalId | None = Field(
        default=None, description="Line id; strategy B uses one dedicated line per variant."
    )
    parallel_positions: int = Field(
        default=1, ge=1, description="Identical positions sharing the load (long tests)."
    )


class EquipmentItem(DomainModel):
    machine_class: Code
    qty: int = Field(ge=1)
    station_ids: list[LocalId] = Field(default_factory=list)
    price_inr: PriceRange | None = None
    rule: str = Field(default="", description="Sizing rule applied, for display.")


class TestPosition(DomainModel):
    op_no: LocalId
    kind: Literal["long_test", "offline_rack"]
    positions: int = Field(ge=1)
    rule: str
    inputs: dict[str, float] = Field(default_factory=dict)


class CustomWorkcenter(DomainModel):
    id: LocalId
    name: str
    footprint_m2: float = Field(gt=0)
    operators: int = Field(ge=0)
    kw: float = Field(ge=0)
    utilities: dict[str, float] = Field(default_factory=dict)
    ops: list[LocalId] = Field(default_factory=list)


class Strategy(DomainModel):
    key: StrategyKey
    name: str
    allowed_overload: float = Field(default=0.10, ge=0, le=1)
    pct_s_by_line: dict[LocalId, float] = Field(
        default_factory=dict, description="Per-line PCT when lines have their own takt (B)."
    )
    line_variants: dict[LocalId, list[LocalId]] = Field(
        default_factory=dict, description="Variants built on each line ({} = one shared line)."
    )
    stations: list[Station]
    operators: int = Field(ge=0)
    machines: dict[str, int] = Field(default_factory=dict)
    test_positions: list[TestPosition] = Field(default_factory=list)
    line_area_m2: Prov[float] | None = None
    balance_efficiency: float = Field(ge=0, le=1)
    smoothness_index: NonNeg = 0
    capex_delta_inr: PriceRange | None = None
    notes: str = ""


class CapacityDesign(DomainModel):
    """S5 artifact (docs/05 §3.5)."""

    operating_pattern: OperatingPattern
    takt: Takt
    strategies: list[Strategy] = Field(min_length=1)
    chosen: StrategyKey | None = None
    workcenters: list[Workcenter] = Field(default_factory=list)
    equipment_list: list[EquipmentItem] = Field(default_factory=list)
    custom_workcenters: list[CustomWorkcenter] = Field(default_factory=list)
    recommendation: str = ""

    def chosen_strategy(self) -> Strategy | None:
        return next((s for s in self.strategies if s.key == self.chosen), None)
