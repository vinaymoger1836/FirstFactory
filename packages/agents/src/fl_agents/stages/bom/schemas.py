"""What the BoM proposer submits (``submit_draft``). Choices only: no prices or masses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CadLink",
    "ClassifyLine",
    "ExtraLineOut",
    "MaterialPick",
    "ProposerDraft",
    "SkeletonDecision",
    "SummaryOut",
]


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SkeletonDecision(_Out):
    ref: str = Field(description="Skeleton line ref.")
    keep: bool = Field(default=True, description="False if the product clearly lacks it.")
    reason: str = Field(default="", max_length=200)


class ExtraLineOut(_Out):
    name: str = Field(max_length=120)
    commodity_class: str = Field(description="One of the allowed class codes.")
    qty: float = Field(gt=0)
    uom: str = "unit"
    reason: str = Field(default="", max_length=200)


class CadLink(_Out):
    cad_part: str = Field(description="CAD part name exactly as listed.")
    skeleton_ref: str = Field(description="Skeleton line ref it is.")


class ClassifyLine(_Out):
    part_no: str
    commodity_class: str


class MaterialPick(_Out):
    target: str = Field(description="Part number or skeleton ref.")
    material: str = Field(description="One of the allowed material codes.")


class ProposerDraft(_Out):
    skeleton: list[SkeletonDecision] = Field(default_factory=list, max_length=60)
    extra_lines: list[ExtraLineOut] = Field(default_factory=list, max_length=15)
    cad_links: list[CadLink] = Field(default_factory=list, max_length=60)
    classify: list[ClassifyLine] = Field(default_factory=list, max_length=200)
    materials: list[MaterialPick] = Field(default_factory=list, max_length=100)
    notes: list[str] = Field(default_factory=list, max_length=5)


class SummaryOut(_Out):
    summary: str
    highlights: list[str] = Field(default_factory=list, max_length=5)
