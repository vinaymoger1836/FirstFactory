"""Stage artifact models (docs/04 §3). One model per stage; StageRun.artifact holds its JSON."""

from __future__ import annotations

from pydantic import BaseModel

from fl_domain.artifacts.bom import Bom, BomLine, Item, MakeBuyDecision
from fl_domain.artifacts.capacity import (
    CapacityDesign,
    EquipmentItem,
    OperatingPattern,
    Station,
    Strategy,
    Takt,
    TestPosition,
)
from fl_domain.artifacts.finance import FinanceModel, FinanceScenario
from fl_domain.artifacts.inventory import Flow, InventoryDesign, StockPolicy, Zone
from fl_domain.artifacts.layout import Layout, Rect
from fl_domain.artifacts.product_spec import ProductSpec, ProductTemplate
from fl_domain.artifacts.quality import QualityPlan
from fl_domain.artifacts.reports import ReportPack
from fl_domain.artifacts.routing import BatchSpec, Operation, Precedence, Routing, TimeStandard
from fl_domain.artifacts.simulation import SimResult
from fl_domain.artifacts.utilities import UtilityDesign
from fl_domain.artifacts.vendors import VendorReport
from fl_domain.artifacts.workorders import Roster, WorkOrderDesign, WorkOrdersShifts
from fl_domain.common import StageKey

__all__ = [
    "ARTIFACT_MODELS",
    "BatchSpec",
    "Bom",
    "BomLine",
    "CapacityDesign",
    "EquipmentItem",
    "FinanceModel",
    "FinanceScenario",
    "Flow",
    "InventoryDesign",
    "Item",
    "Layout",
    "MakeBuyDecision",
    "OperatingPattern",
    "Operation",
    "Precedence",
    "ProductSpec",
    "ProductTemplate",
    "QualityPlan",
    "Rect",
    "ReportPack",
    "Roster",
    "Routing",
    "SimResult",
    "Station",
    "StockPolicy",
    "Strategy",
    "Takt",
    "TestPosition",
    "TimeStandard",
    "UtilityDesign",
    "VendorReport",
    "WorkOrderDesign",
    "WorkOrdersShifts",
    "Zone",
]

ARTIFACT_MODELS: dict[StageKey, type[BaseModel]] = {
    "intake": ProductSpec,
    "bom": Bom,
    "routing": Routing,
    "quality": QualityPlan,
    "capacity": CapacityDesign,
    "inventory": InventoryDesign,
    "workorders_shifts": WorkOrdersShifts,
    "utilities": UtilityDesign,
    "layout": Layout,
    "simulation": SimResult,
    "finance": FinanceModel,
    "reports": ReportPack,
    "vendors": VendorReport,
}
