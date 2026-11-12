"""S7 work-order design and roster (docs/06 §2)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import DomainModel, LocalId, NonNeg
from fl_domain.prov import Prov

__all__ = [
    "ComplianceCheck",
    "HeadcountLine",
    "LabourCostLine",
    "ReleaseLogic",
    "Roster",
    "Shift",
    "StationStaffing",
    "Traceability",
    "WorkInstruction",
    "WorkOrder",
    "WorkOrderDesign",
    "WorkOrderTemplate",
    "WorkOrdersShifts",
]


class WorkOrderTemplate(DomainModel):
    header_fields: list[str] = Field(
        default_factory=lambda: [
            "wo_no", "variant", "quantity", "due_date", "routing_version", "bom_version"
        ]
    )
    includes_operation_list: bool = True
    includes_kit_list: bool = True
    quality_checkpoints: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class Traceability(DomainModel):
    chain: list[str] = Field(description="e.g. ['cell lot', 'pack serial', 'unit QR']")
    serial_format: str
    label_content: list[str] = Field(default_factory=list)


class ReleaseLogic(DomainModel):
    strategy: Literal["A", "B", "C"]
    rule: str
    sequence: list[LocalId] = Field(
        default_factory=list, description="Repeating variant sequence (mixed-model)."
    )
    daily_qty_by_variant: dict[LocalId, float] = Field(default_factory=dict)
    batch_size_by_variant: dict[LocalId, int] = Field(default_factory=dict)


class WorkOrder(DomainModel):
    wo_no: LocalId
    type: Literal["production", "rework", "lab_sample", "changeover"]
    variant: LocalId | None = None
    quantity: int = Field(ge=0)
    due_date: str


class WorkInstruction(DomainModel):
    station: LocalId
    steps: list[str] = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    draft: Literal[True] = True
    banner: str = "Draft, to be validated on the shop floor."


class WorkOrderDesign(DomainModel):
    """S7 artifact (part 1)."""

    template: WorkOrderTemplate = Field(default_factory=WorkOrderTemplate)
    wo_types: list[Literal["production", "rework", "lab_sample", "changeover"]] = Field(
        default_factory=lambda: ["production", "rework", "lab_sample", "changeover"]
    )
    traceability: Traceability
    release: ReleaseLogic
    sample_week: list[WorkOrder] = Field(default_factory=list)
    work_instructions: list[WorkInstruction] = Field(default_factory=list)


class Shift(DomainModel):
    id: LocalId
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    paid_hours: float = Field(gt=0, le=24)
    break_minutes: float = Field(ge=0)
    spread_over_h: float = Field(gt=0, le=24)
    days_per_week: int = Field(ge=1, le=7)
    night: bool = False


class HeadcountLine(DomainModel):
    role: str
    grade: Literal["unskilled", "semi_skilled", "skilled", "highly_skilled", "staff"]
    shift: LocalId
    count: int = Field(ge=0)
    basis: str = ""


class StationStaffing(DomainModel):
    station: LocalId
    shift: LocalId
    operators: int = Field(ge=0)


class ComplianceCheck(DomainModel):
    rule_key: str = Field(description="Pack key, e.g. labour.weekly_hours_max")
    limit: float | None
    actual: float
    ok: bool
    pack_version: str
    message: str = ""


class LabourCostLine(DomainModel):
    grade: str
    headcount: int = Field(ge=0)
    monthly_wage_inr: Prov[float]
    statutory_loading_pct: Prov[float]
    annual_cost_inr: Prov[float]


class Roster(DomainModel):
    """S7 artifact (part 2)."""

    shifts: list[Shift] = Field(min_length=1)
    headcount_by_role_shift: list[HeadcountLine] = Field(default_factory=list)
    station_staffing: list[StationStaffing] = Field(default_factory=list)
    relievers_pct: float = Field(default=9, ge=0, le=100)
    overtime_h_per_quarter: NonNeg = 0
    rota_template: list[str] = Field(default_factory=list)
    compliance_report: list[ComplianceCheck] = Field(default_factory=list)
    welfare_zones: list[LocalId] = Field(default_factory=list)
    labour_cost: list[LabourCostLine] = Field(default_factory=list)


class WorkOrdersShifts(DomainModel):
    """The S7 stage writes both artifacts together."""

    workorders: WorkOrderDesign
    roster: Roster
