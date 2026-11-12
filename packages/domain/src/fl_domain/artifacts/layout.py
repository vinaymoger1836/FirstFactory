"""S9 layout: site, building, placed zones, lines, aisles, exits (docs/07 §4).

Coordinates are metres in the site frame, origin at the south-west corner, x east, y north.
Rectangles are axis-aligned after rotation (rotation is 0 or 90 degrees).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import Code, DomainModel, LocalId, NonNeg

__all__ = [
    "Aisle",
    "Exit",
    "KeepApart",
    "Layout",
    "LayoutMetrics",
    "LayoutRules",
    "LinePlacement",
    "PlacedZone",
    "Rect",
    "StationPlacement",
]


class Rect(DomainModel):
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class PlacedZone(DomainModel):
    zone: LocalId
    kind: Code
    rect: Rect
    rotation: Literal[0, 90] = 0
    pinned: bool = False


class LinePlacement(DomainModel):
    id: LocalId
    shape: Literal["I", "U", "L"]
    zone: LocalId
    path: list[tuple[float, float]] = Field(min_length=2)


class StationPlacement(DomainModel):
    station: LocalId
    rect: Rect
    rotation: Literal[0, 90, 180, 270] = 0


class Aisle(DomainModel):
    id: LocalId
    rect: Rect
    width_m: float = Field(gt=0)
    kind: Literal["main", "cross", "pedestrian", "forklift"] = "main"


class Exit(DomainModel):
    id: LocalId
    x: float
    y: float
    width_m: float = Field(gt=0)


class KeepApart(DomainModel):
    zone_a: LocalId
    zone_b: LocalId
    min_distance_m: float = Field(ge=0)
    reason: str = ""


class LayoutRules(DomainModel):
    """Rule values copied from the pack/catalog when the layout was computed."""

    min_aisle_m: float = Field(gt=0)
    max_travel_m: float = Field(gt=0)
    keep_apart: list[KeepApart] = Field(default_factory=list)
    pack_version: str | None = None


class LayoutMetrics(DomainModel):
    flow_distance_m_per_day: NonNeg = 0
    area_utilisation: float = Field(default=0, ge=0, le=1)
    violations: list[str] = Field(default_factory=list)


class Layout(DomainModel):
    """S9 artifact."""

    mode: Literal["greenfield", "existing_shed"]
    site: Rect
    building: Rect
    columns: list[tuple[float, float]] = Field(default_factory=list)
    zones: list[PlacedZone] = Field(default_factory=list)
    lines: list[LinePlacement] = Field(default_factory=list)
    stations: list[StationPlacement] = Field(default_factory=list)
    aisles: list[Aisle] = Field(default_factory=list)
    doors: list[Exit] = Field(default_factory=list)
    exits: list[Exit] = Field(default_factory=list)
    rules: LayoutRules
    metrics: LayoutMetrics = Field(default_factory=LayoutMetrics)
    scene_ref: str | None = Field(default=None, description="Path of scene.json for the viewer.")
