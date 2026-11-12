"""S6 inventory, storage zones and material flow (docs/06 §1)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import Code, DomainModel, LocalId, NonNeg
from fl_domain.prov import Prov

__all__ = [
    "Flow",
    "InventoryDesign",
    "MheItem",
    "StockPolicy",
    "StorageClass",
    "UtilityNeeds",
    "Zone",
    "ZoneConstraint",
]

StorageClass = Literal[
    "general_rack",
    "small_parts_bin",
    "esd",
    "li_ion_hazard",
    "flammable",
    "bulk_floor",
    "quarantine",
    "line_side",
    "fg",
]


class StockPolicy(DomainModel):
    part_no: LocalId
    abc: Literal["A", "B", "C"]
    annual_value_inr: Prov[float]
    lead_time_days: Prov[float]
    service_level: float = Field(gt=0, lt=1)
    z: float = Field(gt=0)
    demand_cv: float = Field(default=0.2, ge=0)
    safety_stock: Prov[float]
    reorder_point: Prov[float]
    order_qty: Prov[float]
    days_of_cover: Prov[float]
    storage_class: StorageClass
    zone: LocalId


class ZoneConstraint(DomainModel):
    kind: Literal[
        "hazard_li_ion", "esd", "near_dock", "on_boundary", "keep_apart", "adjacent", "ventilated"
    ]
    target: LocalId | None = Field(default=None, description="Other zone id, if relational.")
    distance_m: float | None = Field(default=None, ge=0)


class UtilityNeeds(DomainModel):
    kw: NonNeg = 0
    air_l_min: NonNeg = 0
    water_l_day: NonNeg = 0
    exhaust: bool = False
    hvac: Literal["none", "ventilation", "evaporative", "cooling", "ac"] = "ventilation"


class Zone(DomainModel):
    id: LocalId = Field(description="e.g. Z-STORE-LI")
    kind: Code = Field(description="Catalog zone_kind code.")
    name: str = ""
    area_m2: float = Field(gt=0)
    min_aspect: float = Field(default=1.0, gt=0)
    max_aspect: float = Field(default=3.0, gt=0)
    constraints: list[ZoneConstraint] = Field(default_factory=list)
    utilities: UtilityNeeds = Field(default_factory=UtilityNeeds)
    height_m: float = Field(default=6.0, gt=0)
    hazards: list[Code] = Field(default_factory=list)


class Flow(DomainModel):
    from_zone: LocalId
    to_zone: LocalId
    loads_per_day: NonNeg
    unit_load: str


class MheItem(DomainModel):
    machine_class: Code
    qty: int = Field(ge=1)
    moves_per_day: NonNeg = 0
    rule: str = ""


class InventoryDesign(DomainModel):
    """S6 artifact (docs/06 §1.3)."""

    policies: list[StockPolicy] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    flow_matrix: list[Flow] = Field(default_factory=list)
    mhe: list[MheItem] = Field(default_factory=list)
    fg_days_of_cover: float = Field(default=7, ge=0)

    def zone(self, zone_id: str) -> Zone | None:
        return next((z for z in self.zones if z.id == zone_id), None)
