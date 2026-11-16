"""S1 intake: the product template (wizard input) and ProductSpec (docs/03 §1-3).

``ProductTemplate`` is what the wizard submits: plain values, validated for the required
tier. ``ProductSpec`` is the S1 artifact: the same structure with provenance on every
non-identifier leaf, plus structured per-variant parameters that later stages use
(e.g. ``cells_per_pack``, ``n_screws``).

Field descriptions and examples on the template are shown by the wizard as plain-language
help (the JSON Schema is the single source for that text).
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from fl_domain.base import Code, DomainModel, LocalId
from fl_domain.prov import Prov

__all__ = [
    "Archetype",
    "AssemblyNode",
    "Attachment",
    "BomColumnMap",
    "BomHint",
    "Category",
    "Demand",
    "Door",
    "FinanceInputs",
    "OperatingPatternInput",
    "PartRecord",
    "PastedPart",
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
BomHint = Literal["have_full", "have_partial", "none"]

MIX_TOLERANCE_PCT = 0.01


class RampStep(DomainModel):
    year: int = Field(ge=1, le=30, description="Year number (1 = first year).")
    pct_of_target: float = Field(
        ge=0, le=200, description="Production in that year as a percent of the target."
    )


class Plot(DomainModel):
    length_m: float = Field(gt=0, description="Plot length in metres.", examples=[120])
    width_m: float = Field(gt=0, description="Plot width in metres.", examples=[80])
    road_side: Side = Field(description="Which side of the plot faces the access road.")


class Door(DomainModel):
    id: LocalId = Field(description="Short door label.", examples=["D1"])
    wall: Side = Field(description="Wall the door is in.")
    offset_m: float = Field(ge=0, description="Distance from the wall's left corner, in metres.")
    width_m: float = Field(gt=0, description="Door width in metres.")
    height_m: float = Field(gt=0, description="Door height in metres.")
    kind: Literal["dock", "personnel", "roller_shutter", "emergency"] = Field(
        default="roller_shutter", description="Type of door."
    )


class Shed(DomainModel):
    length_m: float = Field(gt=0, description="Inside length of the shed in metres.", examples=[60])
    width_m: float = Field(gt=0, description="Inside width of the shed in metres.", examples=[30])
    clear_height_m: float = Field(
        gt=0, description="Height under the roof beams in metres.", examples=[8]
    )
    column_grid_m: tuple[float, float] = Field(
        description="Spacing of the roof columns along and across the shed, in metres.",
        examples=[[10, 15]],
    )
    doors: list[Door] = Field(default_factory=list, description="Doors in the shed walls.")


class Site(DomainModel):
    state: Literal["KA"] = Field(description="Karnataka only in the POC.")
    district: str = Field(
        min_length=2,
        max_length=100,
        description="District where the factory will be. It sets the wage zone and local rules.",
        examples=["Bengaluru Rural"],
    )
    mode: Literal["greenfield", "existing_shed"] = Field(
        description="Greenfield: we size a new building on your plot. "
        "Existing shed: we fit the factory into a shed you already have."
    )
    plot: Plot | None = Field(default=None, description="Plot size (greenfield). Recommended.")
    shed: Shed | None = Field(
        default=None, description="Shed dimensions (required for an existing shed)."
    )


class OperatingPatternInput(DomainModel):
    """Wizard defaults (docs/03 §1.2); S5 captures the full pattern."""

    shifts_per_day: int = Field(
        default=1, ge=1, le=3, description="Working shifts per day.", examples=[1]
    )
    days_per_week: int = Field(
        default=6, ge=1, le=7, description="Working days per week.", examples=[6]
    )
    target_oee: float = Field(
        default=0.85,
        gt=0,
        le=1,
        description="Overall equipment effectiveness you aim for (0 to 1). 0.85 is a common "
        "target.",
        examples=[0.85],
    )


class FinanceInputs(DomainModel):
    budget_inr: float | None = Field(
        default=None,
        ge=0,
        description="Total money available for the factory, in rupees.",
        examples=[50000000],
    )
    land_cost_inr_per_sqm: float | None = Field(
        default=None,
        ge=0,
        description="Land price per square metre, if known. We suggest one if blank.",
        examples=[4500],
    )
    debt_equity: tuple[float, float] = Field(
        default=(70, 30),
        description="Share of loan and own money, in percent.",
        examples=[[70, 30]],
    )
    interest_rate_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Loan interest rate per year, in percent.",
        examples=[10.5],
    )
    loan_tenure_years: int | None = Field(
        default=None, ge=1, le=30, description="Loan term in years.", examples=[7]
    )
    discount_rate_pct: float = Field(
        default=12,
        ge=0,
        le=100,
        description="Rate used to value future cash (NPV), in percent.",
        examples=[12],
    )
    horizon_years: int = Field(
        default=5, ge=1, le=30, description="Years covered by the financial plan.", examples=[5]
    )


class BomColumnMap(DomainModel):
    """Which column of an uploaded BoM file holds which field (confirmed in the wizard)."""

    part_no: str | None = Field(default=None, description="Column with the part number.")
    name: str | None = Field(default=None, description="Column with the part name.")
    qty: str | None = Field(default=None, description="Column with the quantity per product.")
    uom: str | None = Field(default=None, description="Column with the unit of measure.")
    material: str | None = Field(default=None, description="Column with the material.")
    variant_qty: dict[str, str] = Field(
        default_factory=dict,
        description="Per-variant quantity columns: variant code → column name.",
    )
    confirmed: bool = Field(default=False, description="The user checked this mapping.")


class Attachment(DomainModel):
    id: LocalId
    kind: Literal["step", "stl", "photo", "pdf", "bom_csv", "text"]
    file_ref: str
    note: str | None = Field(default=None, max_length=500)
    bom_columns: BomColumnMap | None = Field(
        default=None, description="Column mapping for a BoM file (kind bom_csv)."
    )


class PastedPart(DomainModel):
    """One row of the optional parts table typed or pasted in the wizard (docs/03 §2)."""

    name: str = Field(
        min_length=1, max_length=200, description="Part name.", examples=["21700 cell"]
    )
    qty: float | None = Field(
        default=None, gt=0, description="Quantity per product, if known.", examples=[2]
    )
    note: str | None = Field(default=None, max_length=500, description="Anything else.")


class Targets(DomainModel):
    unit_cost_inr: float | None = Field(
        default=None,
        ge=0,
        description="Target cost to make one unit, in rupees.",
        examples=[650],
    )
    quality_level: Literal["basic", "standard", "premium"] = Field(
        default="standard", description="How much checking and testing you want."
    )
    automation_level: Literal["manual", "semi", "high"] = Field(
        default="manual", description="How much of the work machines should do."
    )


class Demand(DomainModel):
    annual_volume: int | None = Field(
        default=None,
        ge=1,
        description="Units per year across all variants. Recommended: we suggest one if blank.",
        examples=[300000],
    )
    ramp_plan: list[RampStep] | None = Field(
        default=None, description="How fast production grows to the target, year by year."
    )


# ── Template (wizard input) ───────────────────────────────────────────


class TemplateVariant(DomainModel):
    code: LocalId = Field(description="Short code for the variant.", examples=["PB10K"])
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Variant name.",
        examples=["Power bank 10,000 mAh"],
    )
    mix_pct: float = Field(
        gt=0,
        le=100,
        description="Share of total volume, in percent. All variants add up to 100.",
        examples=[60],
    )
    selling_price_inr: float | None = Field(
        default=None,
        ge=0,
        description="Selling price per unit, in rupees. Recommended.",
        examples=[1499],
    )
    differences: str = Field(
        default="",
        max_length=2000,
        description="How this variant differs from the others, in plain words.",
        examples=["4 × 21700 cells, large enclosure, 6 screws"],
    )


class TemplateProduct(DomainModel):
    name: str = Field(
        min_length=1,
        max_length=200,
        description="What the product is called.",
        examples=["Power bank"],
    )
    category: Category = Field(
        description="Electronics (circuit boards, cells) or mechanical (motors, metal, plastic)."
    )
    archetype: Archetype | None = Field(
        default=None,
        description="Closest known product type. Leave blank and intake will choose one.",
    )
    description: str = Field(
        min_length=1,
        max_length=5000,
        description="A short description: what it does, the main parts, what you buy and what "
        "you make.",
        examples=["Li-ion power bank with 21700 cells, a bought charge board and a moulded case."],
    )
    variants: list[TemplateVariant] = Field(
        min_length=1, max_length=10, description="Product variants (sizes or models)."
    )
    demand: Demand = Field(default_factory=Demand)
    targets: Targets = Field(default_factory=Targets)


class ProductTemplate(DomainModel):
    """ProductTemplate v1 (docs/03 §1.2): what the web wizard submits."""

    template_version: Literal[1] = 1
    product: TemplateProduct
    site: Site
    operating_pattern: OperatingPatternInput = Field(default_factory=OperatingPatternInput)
    finance_inputs: FinanceInputs = Field(default_factory=FinanceInputs)
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)
    bom_hint: BomHint = Field(
        default="none",
        description="Do you have a bill of materials? A hint only: the BoM stage still checks "
        "what you upload.",
    )
    parts_paste: list[PastedPart] | None = Field(
        default=None,
        max_length=500,
        description="Optional parts list typed or pasted here, for when you have no BoM file.",
    )
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
        ids = [a.id for a in self.attachments]
        if len(set(ids)) != len(ids):
            raise ValueError("attachment ids must be unique")
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
    bom_hint: BomHint = "none"
    structure: Structure = Field(default_factory=Structure)
    extensions: dict[str, dict[str, Prov[object]]] = Field(default_factory=dict)
