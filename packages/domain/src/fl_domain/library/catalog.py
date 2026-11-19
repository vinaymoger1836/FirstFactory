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
    "MaterialEntry",
    "MtmElementEntry",
    "MtmUse",
    "ParamSpec",
    "PolicyEntry",
    "ProcessEntry",
    "QualityBlock",
    "SamplingPlanEntry",
    "SkeletonLine",
    "SpecField",
    "TimeModel",
    "VendorPolicy",
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
    "material",
    "sampling_plan",
    "policy",
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


class MtmUse(DomainModel):
    """One MTM element used by a manual process (docs/05 §1.2 priority 3)."""

    element: Code
    count: float = Field(gt=0, default=1)


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
    mtm: list[MtmUse] = Field(
        default_factory=list,
        description="MTM elements, used for the time when the formula cannot be evaluated.",
    )
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
    # docs/15 §3 fields (catalog 2026.11.5). `method` is above; limits are written against the
    # product specification because numeric limits belong to the product, not the catalog.
    characteristic: str | None = Field(default=None, description="What is checked.")
    limit: str | None = Field(default=None, description="Accept limit, in words.")
    instrument_class: str | None = Field(default=None, description="Kind of instrument or fixture.")
    frequency: str | None = Field(default=None, description="Default frequency, in words.")
    reaction: str | None = Field(default=None, description="What to do on failure.")
    supports_compliance: list[str] = Field(
        default_factory=list, description="Data-pack compliance keys this check supports."
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
    category: Literal[
        "production", "storage", "quality", "logistics", "welfare", "office", "utility"
    ]
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


class SpecField(DomainModel):
    """One specification attribute a line of a commodity class should carry (ADR-027).

    The catalog says *which* attributes identify a part of this class (a bearing: bore, outside
    diameter, width, seal). Values live on the BoM line, never in the catalog.
    """

    code: Code
    name: str = Field(min_length=2)
    kind: Literal["number", "text", "choice"] = "number"
    unit: str | None = Field(default=None, description="Unit for a number field, e.g. mm, V, W.")
    choices: list[str] = Field(default_factory=list, description="Allowed values for a choice.")
    required: bool = Field(
        default=True, description="Needed before vendor research can search by specification."
    )

    @model_validator(mode="after")
    def _choice_has_options(self) -> Self:
        if self.kind == "choice" and not self.choices:
            raise ValueError("a choice field needs choices")
        return self


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
    make_route: list[Code] = Field(
        default_factory=list,
        description="Processes that would make this class in-house (make/buy preview, P3).",
    )
    default_material: Code | None = Field(
        default=None, description="Material assumed when a line gives none (mass from CAD)."
    )
    cad_rarely_modelled: bool = Field(
        default=False,
        description="CAD seldom shows this class (cells, ICs, labels, packaging, consumables, "
        "fasteners): a BoM line without a CAD match is not flagged (docs/03 §4.6).",
    )
    spec_fields: list[SpecField] = Field(
        default_factory=list,
        description="Attributes that identify a part of this class (ADR-027, catalog 2026.11.3).",
    )


# ── material ──────────────────────────────────────────────────────────


class MaterialEntry(_Entry):
    """A material for mass (CAD volume × density) and indicative cost (P3)."""

    kind: Literal["material"] = "material"
    category: Literal["polymer", "metal", "composite", "paper", "cell", "electronic"]
    density_g_cm3: float = Field(gt=0, description="Typical density, g/cm³.")
    price_inr_per_kg: PriceRange | None = None


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
    builds: str | None = Field(
        default=None,
        description="Skeleton ref this step works on (an assembly, a bought part) or 'product'. "
        "Empty = the item of the previous step.",
    )
    consumes: list[str] = Field(
        default_factory=list, description="Skeleton refs consumed here (parts and sub-assemblies)."
    )


class PatrolUse(DomainModel):
    check: Code
    per_shift: float = Field(gt=0)


class OqcUse(DomainModel):
    check: Code
    aql: float | None = Field(default=None, gt=0)
    sample_pct: float | None = Field(default=None, gt=0, le=100)
    frequency: str | None = None
    per_week_per_variant: float | None = Field(
        default=None, gt=0, description="Units tested per variant per week (periodic checks)."
    )


class LabUse(DomainModel):
    name: str
    check: Code
    machine_class: Code | None = None
    qty: int = Field(default=1, ge=1)
    frequency: str
    per_week_per_variant: float | None = Field(
        default=None, gt=0, description="Units tested per variant per week."
    )


class QualityBlock(DomainModel):
    """The checks a product type usually has beyond IQC and inline tests (docs/05 §2)."""

    patrol: list[PatrolUse] = Field(default_factory=list)
    oqc: list[OqcUse] = Field(default_factory=list)
    lab: list[LabUse] = Field(default_factory=list)


class ArchetypeEntry(_Entry):
    kind: Literal["archetype"] = "archetype"
    category: Literal["electronics", "mechanical"]
    parameters: list[ParamSpec] = Field(default_factory=list)
    example_variants: list[ExampleVariant] = Field(default_factory=list)
    bom_skeleton: list[SkeletonLine] = Field(default_factory=list)
    typical_routing: list[RoutingStep] = Field(default_factory=list)
    compliance_pack_keys: list[str] = Field(default_factory=list)
    test_policy: dict[str, float | str] = Field(default_factory=dict)
    quality: QualityBlock = Field(default_factory=QualityBlock)
    variant_deltas: str = ""


# ── MTM element ───────────────────────────────────────────────────────


class MtmElementEntry(_Entry):
    kind: Literal["mtm_element"] = "mtm_element"
    category: Literal["reach_move", "grasp_release", "position", "body", "eye", "tool", "pack"]
    time_s: float = Field(gt=0)
    tmu: float | None = Field(default=None, gt=0, description="1 TMU = 0.036 s.")


# ── sampling plan ─────────────────────────────────────────────────────

InspectionLevel = Literal["S-1", "S-2", "S-3", "S-4", "I", "II", "III"]


class LotRange(DomainModel):
    min_lot: int = Field(ge=1)
    max_lot: int | None = Field(default=None, description="None = no upper limit.")
    letters: dict[InspectionLevel, str]


class SamplingPlanEntry(_Entry):
    """ISO 2859-1 single sampling, normal inspection: code letters and plans (ADR-028).

    ``single_normal[letter][aql]`` is ``"Ac/Re"``, ``"v"`` (use the first plan below) or
    ``"^"`` (use the first plan above), exactly as the standard's table prints them.
    """

    kind: Literal["sampling_plan"] = "sampling_plan"
    standard: str
    levels: list[InspectionLevel]
    sample_sizes: dict[str, int]
    lot_ranges: list[LotRange] = Field(min_length=1)
    aqls: list[float]
    single_normal: dict[str, dict[str, str]]


class VendorPolicy(DomainModel):
    """How vendor research scores and filters (docs/09 §2.2). Data, not code."""

    weights: dict[str, float] = Field(
        default_factory=dict,
        description="Score weights summing to 100: relevance, certifications, evidence, region.",
    )
    directory_domains: list[str] = Field(
        default_factory=list, description="Listing sites: weaker evidence than a maker's own site."
    )
    directory_factor: float = Field(
        default=0.4, ge=0, le=1, description="Evidence score of a listing site, as a share."
    )
    max_vendors_per_class: int = Field(default=8, ge=1, le=8)
    max_classes: int = Field(default=8, ge=1)
    pages_per_class: int = Field(default=3, ge=1)
    cache_ttl_days: int = Field(default=30, ge=1)
    region_state_names: dict[str, list[str]] = Field(
        default_factory=dict, description="State code → names a page may use, e.g. KA → Karnataka."
    )


class PolicyEntry(_Entry):
    """Named defaults that stages use before the user sets a value (estimates, shown as
    assumptions): ``values`` for numbers, ``vendors`` for vendor research (docs/09 §2.2)."""

    kind: Literal["policy"] = "policy"
    values: dict[str, float] = Field(default_factory=dict)
    vendors: VendorPolicy | None = None


CatalogEntry = Annotated[
    ProcessEntry
    | MachineClassEntry
    | CheckTypeEntry
    | ZoneKindEntry
    | CommodityClassEntry
    | ArchetypeEntry
    | MtmElementEntry
    | MaterialEntry
    | SamplingPlanEntry
    | PolicyEntry,
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
