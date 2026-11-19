"""S3 routing: operations, time standards and the precedence graph (docs/05 §1)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import Code, DomainModel, Issue, LocalId
from fl_domain.common import CatalogRef
from fl_domain.prov import Prov

__all__ = [
    "BatchSpec",
    "Operation",
    "Precedence",
    "Routing",
    "RoutingFinding",
    "RoutingTotals",
    "TaskType",
    "TimeMethod",
    "TimeStandard",
    "routing_approval_issues",
]

OpOrigin = Literal["catalog", "proposal", "user"]
FindingKind = Literal[
    "step_skipped", "no_process", "time_missing", "default_consumption", "estimate", "note"
]

TimeMethod = Literal["user", "catalog_formula", "mtm_table", "llm_estimate", "measured"]
Distribution = Literal["fixed", "triangular", "lognormal"]
# Sizing rule per task type (docs/05 §3.2).
TaskType = Literal["manual", "machine_paced", "long_test", "offline_rack", "batch", "inspection"]


class TimeStandard(DomainModel):
    """Time per unit. ``cycle_s`` is engine-computed from the catalog unless user-set."""

    cycle_s: Prov[float]
    setup_s: Prov[float] | None = None
    handling_s: float = Field(default=0, ge=0)
    method: TimeMethod
    distribution: Distribution = "triangular"
    spread_pct: float = Field(default=15, ge=0, le=100)
    formula: str | None = Field(default=None, description="Catalog formula used, for display.")


class BatchSpec(DomainModel):
    batch_size: int = Field(ge=1)
    cycle_s: Prov[float]
    availability: float = Field(default=1.0, gt=0, le=1)


class Operation(DomainModel):
    op_no: LocalId = Field(description="OP-010, OP-020, ...")
    item: LocalId = Field(description="Item (assembly level) this operation works on.")
    process: CatalogRef
    description: str
    workcenter_class: str = Field(min_length=1, description="Machine class code or 'manual'.")
    task_type: TaskType = "manual"
    consumes: list[LocalId] = Field(default_factory=list, description="Part numbers consumed.")
    produces: LocalId
    time: TimeStandard
    quality_gate: Code | None = Field(default=None, description="Check type code, if any.")
    sample_pct: float = Field(
        default=100, gt=0, le=100, description="Share of units that pass through this op."
    )
    batch: BatchSpec | None = None
    variant_times: dict[LocalId, TimeStandard] = Field(default_factory=dict)
    variants: list[LocalId] = Field(
        default_factory=list, description="Variants that use this op ([] = all)."
    )
    op_key: str | None = Field(
        default=None,
        description="Stable key '<process>@<item>': review decisions use it, not the op number.",
    )
    origin: OpOrigin = "catalog"
    note: str | None = None


class Precedence(DomainModel):
    from_op: LocalId
    to_op: LocalId


class RoutingFinding(DomainModel):
    """Something the engine could not settle alone: a gap, a default or an estimate."""

    id: LocalId
    kind: FindingKind
    text: str
    op_key: str | None = None
    part_no: LocalId | None = None
    blocking: bool = Field(default=False, description="Approval waits until it is resolved.")
    status: Literal["open", "answered", "info"] = "open"
    options: list[str] = Field(default_factory=list)


class RoutingTotals(DomainModel):
    """Time per unit by kind of work, weighted by the share of units that pass each op."""

    variant: LocalId
    cycle_s: float
    manual_s: float
    machine_s: float
    test_s: float


class Routing(DomainModel):
    """S3 artifact."""

    variants: list[LocalId] = Field(min_length=1)
    operations: list[Operation] = Field(min_length=1)
    precedence: list[Precedence] = Field(default_factory=list)
    catalog_version: str
    proposer: str = Field(default="", description="'catalog typical routing', 'AI (reasoner)', ...")
    findings: list[RoutingFinding] = Field(default_factory=list)
    totals: list[RoutingTotals] = Field(default_factory=list)

    def op(self, op_no: str) -> Operation | None:
        return next((o for o in self.operations if o.op_no == op_no), None)


def routing_approval_issues(r: Routing) -> list[Issue]:
    """What must be settled before S3 can be approved: every blocking finding."""
    return [
        Issue(code=f"routing.{f.kind}", message=f.text, path=f.op_key or "", severity="error")
        for f in r.findings
        if f.blocking and f.status == "open"
    ]
