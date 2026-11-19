"""S4 quality plan: IQC, IPQC, EOL, OQC, lab, rework and compliance (docs/05 §2).

P3b additions (ADR-028): a ``CheckRecord`` per check carrying the docs/15 §3 fields, the
basis the sizing used (volumes, lot days, inspection level) and both test-policy impacts.
All additions have defaults, so P1 fixtures still validate.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from fl_domain.base import Code, DomainModel, LocalId, NonNeg
from fl_domain.prov import Prov

__all__ = [
    "AqlPlan",
    "CheckRecord",
    "ComplianceLink",
    "InlineCheck",
    "IqcItem",
    "LabItem",
    "PatrolCheck",
    "PolicyImpact",
    "QualityBasis",
    "QualityPlan",
    "ReworkLoad",
    "SampledCheck",
    "TestPolicy",
]

Layer = Literal["iqc", "ipqc", "eol", "oqc", "lab"]
Level = Literal["S-1", "S-2", "S-3", "S-4", "I", "II", "III"]
TestPolicy = Literal["functional_100_capacity_sampled", "capacity_100"]


class AqlPlan(DomainModel):
    """Single sampling plan (ISO 2859-1 / IS 2500, normal inspection)."""

    lot_size: int = Field(ge=1)
    inspection_level: Level = "II"
    aql: float = Field(gt=0)
    code_letter: str = Field(pattern=r"^[A-R]$")
    sample_size: int = Field(ge=1)
    accept: int = Field(ge=0)
    reject: int = Field(ge=1)
    full_inspection: bool = Field(
        default=False, description="The table's sample size reached the lot size: inspect all."
    )
    table_row: str = Field(
        default="",
        description="Where the numbers came from, e.g. 'Table I: lot 19600, level II → M'.",
    )


class IqcItem(DomainModel):
    part_no: LocalId
    commodity_class: Code | None = None
    check_types: list[Code] = Field(min_length=1)
    plan: AqlPlan
    lots_per_month: float = Field(ge=0)
    check_time_s: Prov[float]
    inspector_h_per_day: Prov[float]
    name: str | None = None
    lot_days: float | None = Field(default=None, gt=0, description="Days of use in one lot.")
    inline_checks: list[Code] = Field(
        default_factory=list, description="Checks of this class done on the line (see inline)."
    )


class InlineCheck(DomainModel):
    """A 100% (or sampled) test that runs as a routing operation (ADR-013)."""

    check_type: Code
    op_no: LocalId
    coverage_pct: float = Field(default=100, gt=0, le=100)


class PatrolCheck(DomainModel):
    check_type: Code
    per_shift: float = Field(ge=0)
    duration_s: Prov[float]


class SampledCheck(DomainModel):
    check_type: Code
    plan: AqlPlan | None = None
    sample_pct: float | None = Field(default=None, gt=0, le=100)
    inspector_h_per_day: Prov[float]
    frequency: str | None = None


class LabItem(DomainModel):
    name: str
    check_type: Code | None = None
    machine_class: Code | None = None
    qty: int = Field(ge=1)
    frequency: str


class ReworkLoad(DomainModel):
    op_no: LocalId
    fpy: float = Field(gt=0, le=1)
    rework_time_s: Prov[float]
    units_per_day: NonNeg
    load_s_per_day: Prov[float]


class ComplianceLink(DomainModel):
    pack_key: str = Field(description="e.g. compliance.power_bank.bis_crs")
    title: str
    supported_by_checks: list[Code] = Field(default_factory=list)
    note: str = "Informational and dated; verify with a certification body."


class CheckRecord(DomainModel):
    """One check with the fields an inspection instruction needs (docs/15 §3)."""

    id: LocalId
    check_type: Code
    name: str = Field(min_length=1)
    layer: Layer
    applies_to: str = Field(min_length=1, description="Part number, operation or 'product'.")
    characteristic: str = Field(min_length=1)
    limit: str = Field(min_length=1)
    method: str = Field(min_length=1)
    instrument_class: str = Field(min_length=1)
    frequency: str = Field(min_length=1)
    reaction: str = Field(min_length=1)
    sample: str = Field(min_length=1, description="Sample size and the table row, or 100 %.")
    certifications: list[str] = Field(default_factory=list, description="Pack keys supported.")


class QualityBasis(DomainModel):
    """The volumes and defaults the sizing used, so the user can see what drives it."""

    annual_volume: Prov[float] | None = None
    working_days_per_year: Prov[float] | None = None
    daily_by_variant: dict[LocalId, float] = Field(default_factory=dict)
    iqc_lot_days: Prov[float] | None = None
    inspection_level: Level = "II"


class PolicyImpact(DomainModel):
    """Both power bank test policies, computed side by side (docs/05 §2, ADR-018)."""

    policy: TestPolicy
    label: str
    coverage_pct: float = Field(gt=0, le=100)
    rack_hours_per_day: float = Field(ge=0)
    channels: int = Field(ge=0, description="Test channels with the catalog margin.")


class QualityPlan(DomainModel):
    """S4 artifact."""

    iqc: list[IqcItem] = Field(default_factory=list)
    first_article_per_shift: bool = True
    patrol: list[PatrolCheck] = Field(default_factory=list)
    inline: list[InlineCheck] = Field(default_factory=list)
    eol: list[InlineCheck] = Field(default_factory=list)
    oqc: list[SampledCheck] = Field(default_factory=list)
    lab: list[LabItem] = Field(default_factory=list)
    rework: list[ReworkLoad] = Field(default_factory=list)
    compliance: list[ComplianceLink] = Field(default_factory=list)
    test_policy: TestPolicy | None = Field(default=None, description="Power bank only (ADR-018).")
    capacity_sample_pct: float | None = Field(default=None, gt=0, le=100)
    inspector_h_per_day: Prov[float] | None = None
    quarantine_area_m2: Prov[float] | None = None
    checks: list[CheckRecord] = Field(default_factory=list)
    basis: QualityBasis | None = None
    policy_impact: list[PolicyImpact] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    proposer: str = ""

    @model_validator(mode="after")
    def _policy(self) -> Self:
        if self.test_policy == "functional_100_capacity_sampled" and not self.capacity_sample_pct:
            raise ValueError("capacity_sample_pct is required for the sampled test policy")
        return self
