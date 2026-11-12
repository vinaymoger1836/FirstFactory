"""S2 BoM: items, EBOM/MBOM lines and make/buy decisions (docs/03 §4, docs/04 §3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import Code, DomainModel, LocalId
from fl_domain.common import Range
from fl_domain.prov import Prov

__all__ = ["Bom", "BomLine", "Item", "ItemKind", "MakeBuyDecision", "MakeBuyPreview"]

ItemKind = Literal["product", "assembly", "part", "consumable", "packaging"]
MakeBuy = Literal["make", "buy"]


class Item(DomainModel):
    part_no: LocalId
    name: str
    kind: ItemKind
    material: Prov[str] | None = None
    mass_g: Prov[float] | None = None
    bbox_mm: Prov[tuple[float, float, float]] | None = None
    commodity_class: Code | None = None
    make_or_buy: Prov[MakeBuy]
    variants: list[LocalId] = Field(
        default_factory=list, description="Variant codes this item belongs to ([] = all)."
    )
    uncatalogued: bool = Field(
        default=False, description="A 'make' item with no candidate catalog process."
    )


class BomLine(DomainModel):
    parent: LocalId
    child: LocalId
    qty: Prov[float]
    uom: str
    variant: LocalId | None = Field(default=None, description="None = common to all variants.")
    level: Literal["ebom", "mbom"]
    ebom_ref: str | None = Field(
        default=None, description="For MBOM lines: 'parent>child' of the EBOM line it came from."
    )
    subassembly: str | None = Field(
        default=None, description="Build grouping, e.g. 'cell pack', 'motor', 'final assembly'."
    )


class MakeBuyPreview(DomainModel):
    """Catalog-only preview of the effect of making in-house (docs/03 §4.3)."""

    capex_inr: Range | None = None
    area_m2: Range | None = None
    operators_per_shift: Range | None = None
    note: str | None = None


class MakeBuyDecision(DomainModel):
    part_no: LocalId
    recommendation: MakeBuy
    decision: Prov[MakeBuy]
    rationale: str = ""
    preview: MakeBuyPreview | None = None


class Bom(DomainModel):
    """S2 artifact. ``items`` is the item master; lines reference part numbers."""

    root: LocalId = Field(description="part_no of the product item.")
    variants: list[LocalId] = Field(min_length=1)
    items: list[Item] = Field(min_length=1)
    ebom: list[BomLine] = Field(default_factory=list)
    mbom: list[BomLine] = Field(min_length=1)
    make_buy_decisions: list[MakeBuyDecision] = Field(default_factory=list)
    commodity_classes: list[Code] = Field(default_factory=list)

    def item(self, part_no: str) -> Item | None:
        return next((i for i in self.items if i.part_no == part_no), None)

    def lines_for(self, variant: str, level: Literal["ebom", "mbom"] = "mbom") -> list[BomLine]:
        lines = self.mbom if level == "mbom" else self.ebom
        return [ln for ln in lines if ln.variant in (None, variant)]
