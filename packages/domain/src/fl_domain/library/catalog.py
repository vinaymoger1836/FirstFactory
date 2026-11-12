"""Process catalog schema (docs/10 §1). The YAML in ``library/catalog`` validates against it.

Numbers in the catalog are *data*: engines read them, never hard-code them (CLAUDE.md
rule 3). Every entry carries provenance; estimates are labelled ``catalog-estimate`` and
shown with lower confidence in projects.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from fl_domain.base import Code, DomainModel, NonNeg
from fl_domain.common import PriceRange, Range

__all__ = [
    "ArchetypeEntry",
    "Catalog",
    "CatalogEntry",
    "CatalogManifest",
    "CatalogProvenance",
    "CheckTypeEntry",
    "CommodityClassEntry",
    "ConsumableUse",
    "EntryKind",
    "ExampleVariant",
    "MachineClassEntry",
    "MtmElementEntry",
    "ParamSpec",
    "ProcessEntry",
    "SkeletonLine",
    "TimeModel",
    "ZoneKindEntry",
]

EntryKind = Literal[
    "process",
    "machine_class",
    "check_type",
    "zone_kind",
    "commodity_class",
    "archetype",
    "mtm_element",
]
Skill = Literal["unskilled", "semi_skilled", "skilled", "highly_skilled"]
TaskType = Literal["manual", "machine_paced", "long_test", "offline_rack", "batch", "inspection"]
QualityLayer = Literal["iqc", "ipqc", "eol", "oqc", "lab"]
StorageClass = Literal[
    "general_rack", "small_parts_bin", "esd", "li_ion_hazard", "flammable", "bulk_floor"
]


class CatalogProvenance(DomainModel):
    """Where an entry's numbers come from (docs/10 §1.2: source, as_of, confidence)."""

    source: Literal["catalog-estimate", "public-data", "standard", "vendor-quote"]
    as_of: str = Field(pattern=r"^\d{4}-\d{2}(-\d{2})?$")
    confidence: float = Field(ge=0, le=1)
    urls: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _label(self) -> Self:
        if self.source == "catalog-estimate" and self.confidence > 0.6:
            raise ValueError("catalog estimates must carry confidence <= 0.6")
        if self.source in ("public-data", "standard", "vendor-quote") and not self.urls:
            raise ValueError(f"source '{self.source}' needs at least one URL")
        return self


class _Entry(DomainModel):
    code: Code
    name: str = Field(min_length=3)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    provenance: CatalogProvenance
    notes: str | None = None


# ── process ───────────────────────────────────────────────────────────


class TimeModel(DomainModel):
    """Seconds per unit from a safe-evaluated formula (no eval; fl_engines.expr)."""

    formula: str = Field(min_length=1, max_length=400)
    variables: dict[str, str] = Field(
        default_factory=dict, description="Derived variables, evaluated in order."
    )
    unit: Literal["s"] = "s"
    distribution: Literal["fixed", "triangular", "lognormal"] = "triangular"
    spread_pct: float = Field(default=15, ge=0, le=100)


class BatchModel(DomainModel):
    batch_size_param: str = Field(
        description="Machine-class param holding the batch capacity, e.g. 'batch_capacity'."
    )
    cycle_formula: str = Field(description="Seconds per batch.")


class Rework(DomainModel):
    op: Code
    time_s: float = Field(gt=0)


class ConsumableUse(DomainModel):
    commodity: Code
    qty_expr: str
    uom: str


class Utilities(DomainModel):
    kw: NonNeg = 0
    demand_factor: float = Field(default=0.5, ge=0, le=1)
    air_l_min: NonNeg = 0
    exhaust: bool = False
    water_l_h: NonNeg = 0


class AppliesTo(DomainModel):
    archetype: list[Code] = Field(default_factory=list)
    commodity: list[Code] = Field(default_factory=list)


class ProcessEntry(_Entry):
    kind: Literal["process"] = "process"
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    task_type: TaskType = "manual"
    machine_class: Code | None = Field(default=None, description="None = manual workbench.")
    operators_per_machine: float = Field(default=1, ge=0)
    skill: Skill = "semi_skilled"
    time_model: TimeModel
    constants: dict[str, float] = Field(
        default_factory=dict, description="Process constants usable in formulas."
    )
    batch: BatchModel | None = None
    setup_s: NonNeg = 0
    fpy: float = Field(default=0.99, gt=0, le=1)
    rework: Rework | None = None
    consumables: list[ConsumableUse] = Field(default_factory=list)
    quality_checks: list[Code] = Field(default_factory=list)
    utilities: Utilities = Field(default_factory=Utilities)
    footprint_m2: float = Field(gt=0, description="Including operator space.")
    height_m: float = Field(default=1.8, gt=0)
    zone_kind: Code
    hazards: list[Code] = Field(default_factory=list)
    price_inr: PriceRange | None = Field(default=None, description="Fixtures and tooling.")
    mtbf_h: float | None = Field(default=None, gt=0)
    mttr_h: float | None = Field(default=None, gt=0)
    example_params: dict[str, float] = Field(
        default_factory=dict,
        description="Parameters used to test the formula when no archetype example applies.",
    )

    @model_validator(mode="after")
    def _batch(self) -> Self:
        if self.task_type == "batch" and self.batch is None:
            raise ValueError("batch processes need a 'batch' block")
        if self.task_type in ("machine_paced", "batch", "offline_rack") and not self.machine_class:
            raise ValueError(f"task_type '{self.task_type}' needs a machine_class")
        return self


# ── machine class ─────────────────────────────────────────────────────


class MachineClassEntry(_Entry):
    kind: Literal["machine_class"] = "machine_class"
    category: Literal["production", "test", "handling", "storage", "utility", "it", "lab"]
    kw: NonNeg = 0
    demand_factor: float = Field(default=0.5, ge=0, le=1)
    heat_fraction: float = Field(default=1.0, ge=0, le=1)
    air_l_min: NonNeg = 0
    footprint_m2: NonNeg = 0
    height_m: NonNeg = 0
    price_inr: PriceRange
    life_years: float = Field(default=10, gt=0)
    mtbf_h: float | None = Field(default=None, gt=0)
    mttr_h: float | None = Field(default=None, gt=0)
    params: dict[str, float] = Field(
        default_factory=dict,
        description="Sizing parameters, e.g. batch_capacity, availability, rack_hours_per_day.",
    )


# ── check type ────────────────────────────────────────────────────────


class CheckTypeEntry(_Entry):
    kind: Literal["check_type"] = "check_type"
    layers: list[QualityLayer] = Field(min_length=1)
    method: str
    measures: list[str] = Field(default_factory=list)
    time_s: float | None = Field(default=None, gt=0, description="Fixed time per unit, if any.")
    process: Code | None = Field(default=None, description="Process that performs it inline.")
    machine_class: Code | None = None
    sampling: Literal["100pct", "aql", "sampled_pct", "periodic"]
    default_aql: float | None = Field(default=None, gt=0)
    standards: list[str] = Field(
        default_factory=list, description="Standard names, informational (verify)."
    )


# ── zone kind ─────────────────────────────────────────────────────────


class ZoneRule(DomainModel):
    kind: Literal[
        "hazard_li_ion", "esd", "near_dock", "on_boundary", "keep_apart", "adjacent", "ventilated"
    ]
    target_kind: Code | None = None
    distance_m: float | None = Field(default=None, ge=0)


class ZoneKindEntry(_Entry):
    kind: Literal["zone_kind"] = "zone_kind"
    category: Literal["production", "storage", "quality", "logistics", "welfare", "office", "utility"]
    lighting_w_m2: float = Field(ge=0)
    lux: float = Field(ge=0)
    default_height_m: float = Field(gt=0)
    min_aspect: float = Field(default=1.0, gt=0)
    max_aspect: float = Field(default=3.0, gt=0)
    floor: Literal["concrete", "epoxy", "esd", "tile", "paved", "chemical_resistant"]
    hvac: Literal["none", "ventilation", "evaporative", "cooling", "ac"]
    hazards: list[Code] = Field(default_factory=list)
    rules: list[ZoneRule] = Field(default_factory=list)
    area_per_person_m2: float | None = Field(default=None, gt=0)
    fitout_inr_per_m2: PriceRange | None = None


# ── commodity class ───────────────────────────────────────────────────


class CommodityClassEntry(_Entry):
    kind: Literal["commodity_class"] = "commodity_class"
    uom: str
    storage_class: StorageClass
    hazards: list[Code] = Field(default_factory=list)
    lead_time_days: Range
    pack_qty: float = Field(gt=0, description="Units per carton/bin.")
    cartons_per_pallet: float | None = Field(default=None, gt=0)
    iqc_checks: list[Code] = Field(default_factory=list)
    default_aql: float | None = Field(default=None, gt=0)
    default_make_or_buy: Literal["make", "buy"] = "buy"
    unit_price_inr: PriceRange | None = None
    parent: Code | None = Field(default=None, description="Taxonomy parent class.")


# ── archetype ─────────────────────────────────────────────────────────


class ParamSpec(DomainModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    unit: str
    description: str


class ExampleVariant(DomainModel):
    code: str
    name: str
    params: dict[str, float]


class SkeletonLine(DomainModel):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    commodity: Code | None = None
    kind: Literal["assembly", "part", "consumable", "packaging"]
    qty_expr: str
    uom: str
    make_or_buy: Literal["make", "buy"]
    subassembly: str
    parent: str | None = Field(default=None, description="ref of the parent assembly line.")
    note: str | None = None


class RoutingStep(DomainModel):
    process: Code
    note: str | None = None
    sample_pct: float = Field(default=100, gt=0, le=100)


class ArchetypeEntry(_Entry):
    kind: Literal["archetype"] = "archetype"
    category: Literal["electronics", "mechanical"]
    parameters: list[ParamSpec] = Field(default_factory=list)
    example_variants: list[ExampleVariant] = Field(default_factory=list)
    bom_skeleton: list[SkeletonLine] = Field(default_factory=list)
    typical_routing: list[RoutingStep] = Field(default_factory=list)
    compliance_pack_keys: list[str] = Field(default_factory=list)
    test_policy: dict[str, float | str] = Field(default_factory=dict)
    variant_deltas: str = ""


# ── MTM element ───────────────────────────────────────────────────────


class MtmElementEntry(_Entry):
    kind: Literal["mtm_element"] = "mtm_element"
    category: Literal["reach_move", "grasp_release", "position", "body", "eye", "tool", "pack"]
    time_s: float = Field(gt=0)
    tmu: float | None = Field(default=None, gt=0, description="1 TMU = 0.036 s.")


CatalogEntry = Annotated[
    ProcessEntry
    | MachineClassEntry
    | CheckTypeEntry
    | ZoneKindEntry
    | CommodityClassEntry
    | ArchetypeEntry
    | MtmElementEntry,
    Field(discriminator="kind"),
]


class CatalogManifest(DomainModel):
    version: str = Field(pattern=r"^\d{4}\.\d{2}\.\d+$")
    effective_date: date
    description: str = ""
    files: list[str] = Field(min_length=1)


class Catalog(DomainModel):
    """A full catalog version: manifest plus all entries."""

    manifest: CatalogManifest
    entries: list[CatalogEntry]

    def by_code(self) -> dict[str, CatalogEntry]:
        return {e.code: e for e in self.entries}

    def of_kind(self, kind: EntryKind) -> list[CatalogEntry]:
        return [e for e in self.entries if e.kind == kind]

    def codes(self, kind: EntryKind) -> set[str]:
        return {e.code for e in self.entries if e.kind == kind}
