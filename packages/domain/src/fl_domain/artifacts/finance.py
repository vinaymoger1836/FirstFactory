"""S11 finance (docs/09 §1). Every figure is computed per scenario: low, base, high."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import DomainModel, LocalId

__all__ = [
    "CapexGroup",
    "CapexLine",
    "FinanceModel",
    "FinanceScenario",
    "OpexLine",
    "PnlYear",
    "SensitivityBar",
    "UnitCost",
]

CapexGroup = Literal[
    "production_equipment",
    "test_equipment",
    "quality_lab",
    "material_handling",
    "it_traceability",
    "building",
    "land",
    "utilities",
    "certification",
    "pre_operative",
    "contingency",
]
Scenario = Literal["low", "base", "high"]


class CapexLine(DomainModel):
    group: CapexGroup
    item: str
    qty: float = Field(default=1, ge=0)
    amount_inr: float = Field(ge=0)
    source_ref: str = Field(
        description="Origin: 'capacity:equipment:<class>', 'pack:<key>', 'catalog:<code>', "
        "'user', or 'percent:<base>'."
    )
    life_years: float | None = Field(default=None, gt=0)


class OpexLine(DomainModel):
    category: Literal[
        "materials",
        "direct_labour",
        "indirect_labour",
        "power",
        "consumables",
        "maintenance",
        "rent",
        "insurance",
        "warranty",
        "logistics",
        "admin",
    ]
    annual_inr: float = Field(ge=0)
    source_ref: str


class UnitCost(DomainModel):
    variant: LocalId
    material_inr: float = Field(ge=0)
    conversion_inr: float = Field(ge=0)
    total_inr: float = Field(ge=0)
    selling_price_inr: float | None = Field(default=None, ge=0)
    contribution_inr: float | None = None


class PnlYear(DomainModel):
    year: int = Field(ge=0)
    volume: float = Field(ge=0)
    revenue_inr: float
    opex_inr: float
    ebitda_inr: float
    depreciation_inr: float
    interest_inr: float
    pbt_inr: float
    cash_flow_inr: float


class SensitivityBar(DomainModel):
    driver: Literal["selling_price", "volume", "material_cost", "capex", "labour_cost"]
    delta_pct: float
    npv_low_inr: float
    npv_high_inr: float


class FinanceScenario(DomainModel):
    scenario: Scenario
    capex: list[CapexLine] = Field(default_factory=list)
    capex_total_inr: float = Field(ge=0)
    working_capital_inr: float
    opex: list[OpexLine] = Field(default_factory=list)
    opex_total_inr: float = Field(ge=0)
    unit_costs: list[UnitCost] = Field(default_factory=list)
    pnl: list[PnlYear] = Field(default_factory=list)
    cash_flows_inr: list[float] = Field(default_factory=list)
    discount_rate_pct: float = Field(ge=0)
    npv_inr: float
    irr_pct: float | None = None
    payback_years: float | None = None
    break_even_units_per_year: float | None = None
    dscr_min: float | None = None


class FinanceModel(DomainModel):
    """S11 artifact."""

    scenarios: list[FinanceScenario] = Field(min_length=1)
    sensitivity: list[SensitivityBar] = Field(default_factory=list)
    incentives_note: str = "Karnataka industrial-policy incentives listed for information only."

    def scenario(self, key: Scenario) -> FinanceScenario | None:
        return next((s for s in self.scenarios if s.scenario == key), None)
