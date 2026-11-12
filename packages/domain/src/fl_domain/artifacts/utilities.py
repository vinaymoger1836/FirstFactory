"""S8 utilities: electrical, air, HVAC, water, fire and utility rooms (docs/06 §3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import DomainModel, LocalId, NonNeg
from fl_domain.prov import Prov

__all__ = [
    "AirSystem",
    "Electrical",
    "EquipmentLoad",
    "FireSystem",
    "HvacSystem",
    "HvacZone",
    "UtilityDesign",
    "UtilityRoom",
    "WaterSystem",
]


class EquipmentLoad(DomainModel):
    """One counted load. ``ref`` points at its origin so double counting can be detected."""

    ref: str = Field(description="e.g. 'capacity:equipment:spot_welder_cd' or 'zone:Z-OFFICE'")
    group: Literal[
        "process", "test", "lighting", "hvac", "compressor", "it_office", "test_rack", "other"
    ]
    qty: int = Field(ge=1)
    kw_each: NonNeg
    demand_factor: float = Field(ge=0, le=1)
    load_factor: float = Field(default=0.6, ge=0, le=1)
    hours_per_year: NonNeg = 0


class Electrical(DomainModel):
    loads: list[EquipmentLoad] = Field(default_factory=list)
    connected_kw: Prov[float]
    demand_kw: Prov[float]
    diversity_factor: float = Field(gt=0, le=1)
    power_factor_target: float = Field(default=0.95, gt=0, le=1)
    max_demand_kva: Prov[float]
    growth_margin: float = Field(default=0.20, ge=0)
    contract_demand_kva: Prov[float]
    supply: Literal["LT", "HT"]
    transformer_headroom: float = Field(default=1.25, ge=1)
    transformer_kva: Prov[float] | None = None
    dg_kva: Prov[float] | None = None
    ups_kva: Prov[float] | None = None
    apfc_kvar: Prov[float] | None = None
    energy_kwh_year: Prov[float]
    annual_cost_inr: Prov[float] | None = None
    tariff_pack_keys: list[str] = Field(default_factory=list)


class AirSystem(DomainModel):
    demand_l_min: Prov[float]
    leakage_pct: float = Field(default=15, ge=0, le=100)
    compressor_kw: Prov[float]
    receiver_l: Prov[float]
    dryer: bool = True


class HvacZone(DomainModel):
    zone: LocalId
    heat_load_kw: Prov[float]
    cooling_tr: Prov[float]
    mode: Literal["ventilation", "evaporative", "cooling", "ac"]
    exhaust_ach: float | None = Field(default=None, ge=0)


class HvacSystem(DomainModel):
    zones: list[HvacZone] = Field(default_factory=list)
    total_tr: Prov[float] | None = None


class WaterSystem(DomainModel):
    domestic_l_day: Prov[float]
    process_l_day: Prov[float]
    fire_tank_l: Prov[float] | None = None
    rwh_l: Prov[float] | None = None


class FireSystem(DomainModel):
    detection_zones: list[LocalId] = Field(default_factory=list)
    suppression_zones: list[LocalId] = Field(default_factory=list)
    extinguishers: int = Field(default=0, ge=0)
    hydrant_required: bool | None = None
    sprinkler_required: bool | None = None
    noc_required: bool = True


class UtilityRoom(DomainModel):
    id: LocalId
    kind: Literal[
        "transformer_yard", "lt_panel", "dg_yard", "compressor_room", "water_tank", "fire_pump"
    ]
    area_m2: float = Field(gt=0)
    on_boundary: bool = False
    keep_apart_from: list[LocalId] = Field(default_factory=list)


class UtilityDesign(DomainModel):
    """S8 artifact (docs/06 §3.6)."""

    electrical: Electrical
    air: AirSystem | None = None
    hvac: HvacSystem = Field(default_factory=HvacSystem)
    water: WaterSystem | None = None
    fire: FireSystem = Field(default_factory=FireSystem)
    rooms: list[UtilityRoom] = Field(default_factory=list)
