"""S1 intake: the product template (wizard input) and ProductSpec (docs/03 §1-3).

``ProductTemplate`` is what the wizard submits: plain values, validated for the required
tier. ``ProductSpec`` is the S1 artifact: the same structure with provenance on every
non-identifier leaf, plus structured per-variant parameters that later stages use
(e.g. ``cells_per_pack``, ``n_screws``).
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from fl_domain.base import Code, DomainModel, LocalId
from fl_domain.prov import Prov

__all__ = [
    "Archetype",
    "Attachment",
    "Category",
    "Demand",
    "FinanceInputs",
    "OperatingPatternInput",
    "PartRecord",
    "Plot",
    "ProductSpec",
    "ProductTemplate",
    "RampStep",
    "Shed",
    "Site",
    "SpecDemand",
    "SpecProduct",
    "SpecTargets",
    "SpecVariant",
    "Structure",
    "Targets",
    "TemplateProduct",
    "TemplateVariant",
]

Category = Literal["electronics", "mechanical"]
Archetype = Literal["power_bank", "ceiling_fan", "generic_electronics", "generic_mechanical"]
Side = Literal["N", "E", "S", "W"]

MIX_TOLERANCE_PCT = 0.01


class RampStep(DomainModel):
    year: int = Field(ge=1, le=30)
    pct_of_target: float = Field(ge=0, le=200)


class Plot(DomainModel):
    length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)
    road_side: Side


class Door(DomainModel):
    id: LocalId
    wall: Side
    offset_m: float = Field(ge=0)
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    kind: Literal["dock", "personnel", "roller_shutter", "emergency"] = "roller_shutter"


class Shed(DomainModel):
    length_m: float = Field(gt=0)
    width_m: float = Field(gt=0)
    clear_height_m: float = Field(gt=0)
    column_grid_m: tuple[float, float]
    doors: list[Door] = Field(default_factory=list)


class Site(DomainModel):
    state: Literal["KA"] = Field(description="Karnataka only in the POC.")
    district: str = Field(min_length=2)
    mode: Literal["greenfield", "existing_shed"]
    plot: Plot | None = None
    shed: Shed | None = None


class OperatingPatternInput(DomainModel):
    """Wizard defaults (docs/03 §1.2); S5 captures the full pattern."""

    shifts_per_day: int = Field(default=1, ge=1, le=3)
    days_per_week: int = Field(default=6, ge=1, le=7)
    target_oee: float = Field(default=0.85, gt=0, le=1)


class FinanceInputs(DomainModel):
    budget_inr: float | None = Field(default=None, ge=0)
    land_cost_inr_per_sqm: float | None = Field(default=None, ge=0)
    debt_equity: tuple[float, float] = (70, 30)
    interest_rate_pct: float | None = Field(default=None, ge=0, le=100)
    loan_tenure_years: int | None = Field(default=None, ge=1, le=30)
    discount_rate_pct: float = Field(default=12, ge=0, le=100)
    horizon_years: int = Field(default=5, ge=1, le=30)


class Attachment(DomainModel):
    id: LocalId
    kind: Literal["step", "stl", "photo", "pdf", "bom_csv", "text"]
    file_ref: str
    note: str | None = None


class Targets(DomainModel):
    unit_cost_inr: float | None = Field(default=None, ge=0)
    quality_level: Literal["basic", "standard", "premium"] = "standard"
    automation_level: Literal["manual", "semi", "high"] = "manual"


class Demand(DomainModel):
    annual_volume: int | None = Field(default=None, ge=1)
    ramp_plan: list[RampStep] | None = None


# ── Template (wizard input) ───────────────────────────────────────────


class TemplateVariant(DomainModel):
    code: LocalId
    name: str
    mix_pct: float = Field(gt=0, le=100)
    selling_price_inr: float | None = Field(default=None, ge=0)
    differences: str = ""


class TemplateProduct(DomainModel):
    name: str = Field(min_length=1)
    category: Category
    archetype: Archetype | None = Field(default=None, description="Set by intake if blank.")
    description: str = Field(min_length=1)
    variants: list[TemplateVariant] = Field(min_length=1)
    demand: Demand = Field(default_factory=Demand)
    targets: Targets = Field(default_factory=Targets)


class ProductTemplate(DomainModel):
    """ProductTemplate v1 (docs/03 §1.2): what the web wizard submits."""

    template_version: Literal[1] = 1
    product: TemplateProduct
    site: Site
    operating_pattern: OperatingPatternInput = Field(default_factory=OperatingPatternInput)
    finance_inputs: FinanceInputs = Field(default_factory=FinanceInputs)
    attachments: list[Attachment] = Field(default_factory=list)
    extensions: dict[str, dict[str, object]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        total = sum(v.mix_pct for v in self.product.variants)
        if abs(total - 100.0) > MIX_TOLERANCE_PCT:
            raise ValueError(f"variant mix must sum to 100%, got {total:g}%")
        codes = [v.code for v in self.product.variants]
        if len(set(codes)) != len(codes):
            raise ValueError("variant codes must be unique")
        if self.site.mode == "existing_shed" and self.site.shed is None:
            raise ValueError("site.shed is required when site.mode is existing_shed")
        return self


# ── ProductSpec (S1 artifact) ─────────────────────────────────────────


class SpecVariant(DomainModel):
    code: LocalId
    name: str
    mix_pct: Prov[float]
    selling_price_inr: Prov[float] | None = None
    differences: str = ""
    params: dict[Code, Prov[float]] = Field(
        default_factory=dict,
        description="Structured product parameters used by catalog formulas "
        "(names follow the archetype's parameter list).",
    )


class SpecDemand(DomainModel):
    annual_volume: Prov[int] | None = None
    ramp_plan: list[RampStep] | None = None


class SpecTargets(DomainModel):
    unit_cost_inr: Prov[float] | None = None
    quality_level: Literal["basic", "standard", "premium"] = "standard"
    automation_level: Literal["manual", "semi", "high"] = "manual"


class SpecProduct(DomainModel):
    name: str = Field(min_length=1)
    category: Category
    archetype: Prov[Archetype]
    description: Prov[str]
    variants: list[SpecVariant] = Field(min_length=1)
    demand: SpecDemand = Field(default_factory=SpecDemand)
    targets: SpecTargets = Field(default_factory=SpecTargets)


class PartRecord(DomainModel):
    part_no: LocalId
    name: str
    qty: Prov[float]
    material: Prov[str] | None = None
    mass_g: Prov[float] | None = None
    bbox_mm: Prov[tuple[float, float, float]] | None = None
    process_hint: str | None = None
    make_or_buy: Prov[Literal["make", "buy"]] | None = None


class AssemblyNode(DomainModel):
    id: LocalId
    name: str
    children: list[AssemblyNode] = Field(default_factory=list)
    part_no: LocalId | None = None


class Structure(DomainModel):
    assemblies: list[AssemblyNode] = Field(default_factory=list)
    parts: list[PartRecord] = Field(default_factory=list)


class ProductSpec(DomainModel):
    """S1 artifact: the validated template with provenance (docs/03 §3.3)."""

    template_version: Literal[1] = 1
    product: SpecProduct
    site: Site
    operating_pattern: OperatingPatternInput = Field(default_factory=OperatingPatternInput)
    finance_inputs: FinanceInputs = Field(default_factory=FinanceInputs)
    attachments: list[Attachment] = Field(default_factory=list)
    structure: Structure = Field(default_factory=Structure)
    extensions: dict[str, dict[str, Prov[object]]] = Field(default_factory=dict)
