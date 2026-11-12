"""S12 report pack (docs/09 §3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import DomainModel
from fl_domain.common import Assumption, StageKey

__all__ = ["ReportFile", "ReportPack", "ReportSection", "VersionManifest"]


class ReportSection(DomainModel):
    number: int = Field(ge=1, le=13)
    title: str
    source_stages: list[StageKey] = Field(default_factory=list)
    summary: str = ""


class ReportFile(DomainModel):
    format: Literal["web", "pdf", "xlsx", "dxf", "sysml", "zip", "json"]
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    validated: bool | None = Field(default=None, description="e.g. SysML parsed by sysmlpy.")


class VersionManifest(DomainModel):
    artifact_versions: dict[StageKey, int]
    catalog_version: str
    pack_versions: dict[str, str]


class ReportPack(DomainModel):
    """S12 artifact."""

    sections: list[ReportSection] = Field(default_factory=list)
    files: list[ReportFile] = Field(default_factory=list)
    versions: VersionManifest
    assumptions: list[Assumption] = Field(default_factory=list)
    executive_summary: str = ""
    disclaimer: str = (
        "Engineering pre-feasibility study. Regulatory and price values are dated and "
        "must be verified before decisions."
    )
