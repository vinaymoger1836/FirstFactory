"""S2 BoM: items, EBOM/MBOM lines and make/buy decisions (docs/03 §4, docs/04 §3).

P3 additions (docs/03 §4.6-4.9, ADR-022): every line records where it came from
(``origin``) and whether the user has decided on it (``review``); findings carry
suggestions and questions; engine numbers (mass, cost) come with a calculation trail.
All additions have defaults, so P1 fixtures still validate.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from fl_domain.base import Code, DomainModel, LocalId
from fl_domain.common import Range
from fl_domain.prov import Prov

__all__ = [
    "Bom",
    "BomCase",
    "BomFinding",
    "BomLine",
    "CalcStep",
    "FindingKind",
    "Item",
    "ItemKind",
    "LineOrigin",
    "LineReview",
    "MakeBuyDecision",
    "MakeBuyPreview",
    "UserLineSnapshot",
    "VariantTotals",
]

ItemKind = Literal["product", "assembly", "part", "consumable", "packaging"]
MakeBuy = Literal["make", "buy"]
LineOrigin = Literal["user_bom", "cad", "skeleton", "reasoner", "engine"]
LineReview = Literal["given", "suggested", "accepted", "rejected"]
FindingKind = Literal["missing_class", "cad_unmatched", "qty_conflict", "line_quality", "note"]
BomCase = Literal["A", "B", "C"]


class CalcStep(DomainModel):
    """One step of an engine calculation shown under "▾ calculation" (ADR-001)."""

    label: str
    formula: str = ""
    value: float | None = None
    unit: str | None = None
    source: str = Field(default="engine", description="Where the inputs came from.")


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
    cad_refs: list[str] = Field(
        default_factory=list, description="CAD solid names this item is matched to."
    )
    unit_cost_inr: Range | None = Field(default=None, description="Engine: indicative, per unit.")
    calc: list[CalcStep] = Field(default_factory=list)


class BomLine(DomainModel):
    id: LocalId | None = Field(default=None, description="Stable line id (P3).")
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
    origin: LineOrigin = Field(default="engine", description="Where the line came from.")
    review: LineReview = Field(
        default="given", description="'suggested' lines must be accepted or rejected first."
    )
    commodity_class: Code | None = None
    reason: str | None = Field(default=None, description="Why a suggested line is proposed.")
    ext_cost_inr: Range | None = Field(default=None, description="Engine: qty × unit cost.")
    ext_mass_g: float | None = Field(default=None, description="Engine: qty × unit mass.")


class MakeBuyPreview(DomainModel):
    """Catalog-only preview of the effect of making in-house (docs/03 §4.3)."""

    capex_inr: Range | None = None
    area_m2: Range | None = None
    operators_per_shift: Range | None = None
    note: str | None = None
    processes: list[Code] = Field(default_factory=list)


class MakeBuyDecision(DomainModel):
    part_no: LocalId
    recommendation: MakeBuy
    decision: Prov[MakeBuy]
    rationale: str = ""
    preview: MakeBuyPreview | None = None
    confirmed: bool = Field(default=False, description="The user confirmed this decision.")


class BomFinding(DomainModel):
    """A suggestion or question from the gap check, CAD cross-check or line checks."""

    id: LocalId
    kind: FindingKind
    rank: int = Field(description="Sort order: missing parts, conflicts, quality, notes.")
    text: str
    commodity_class: Code | None = None
    line_ids: list[LocalId] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["open", "accepted", "rejected", "answered", "info"] = "open"
    answer: str | None = None


class UserLineSnapshot(DomainModel):
    """A user BoM row after normalisation, kept to prove user lines were not rewritten."""

    part_no: LocalId
    name: str
    qty_by_variant: dict[str, float]
    uom: str
    ref: str


class VariantTotals(DomainModel):
    variant: LocalId
    cost_inr: Range | None = None
    mass_g: float | None = None
    lines_without_cost: int = 0
    lines_without_mass: int = 0


class Bom(DomainModel):
    """S2 artifact. ``items`` is the item master; lines reference part numbers."""

    root: LocalId = Field(description="part_no of the product item.")
    variants: list[LocalId] = Field(min_length=1)
    items: list[Item] = Field(min_length=1)
    ebom: list[BomLine] = Field(default_factory=list)
    mbom: list[BomLine] = Field(min_length=1)
    make_buy_decisions: list[MakeBuyDecision] = Field(default_factory=list)
    commodity_classes: list[Code] = Field(default_factory=list)
    case: BomCase | None = Field(default=None, description="A CAD only, B complete, C partial.")
    findings: list[BomFinding] = Field(default_factory=list)
    user_snapshot: list[UserLineSnapshot] = Field(default_factory=list)
    rejected_suggestions: list[str] = Field(
        default_factory=list, description="Never proposed again in this project."
    )
    excluded_classes: list[Code] = Field(
        default_factory=list, description="MBOM-only classes the user switched off."
    )
    totals: list[VariantTotals] = Field(default_factory=list)
    proposer: str = Field(default="", description="'AI (reasoner)' or 'rules engine'.")

    def item(self, part_no: str) -> Item | None:
        return next((i for i in self.items if i.part_no == part_no), None)

    def lines_for(self, variant: str, level: Literal["ebom", "mbom"] = "mbom") -> list[BomLine]:
        lines = self.mbom if level == "mbom" else self.ebom
        return [ln for ln in lines if ln.variant in (None, variant)]
