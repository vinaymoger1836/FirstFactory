"""Parallel stage P1: vendor research (docs/09 §2). Leads, not endorsements.

P3b additions (ADR-028): where each vendor's evidence came from (maker site or listing
site), how the score was made up, and per-class cache and search status.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl

from fl_domain.base import Code, DomainModel

__all__ = ["Vendor", "VendorClass", "VendorEvidence", "VendorReport"]


class VendorEvidence(DomainModel):
    url: HttpUrl
    fetched_at: str
    snippet: str = Field(min_length=1, description="Quoted text that supports the record.")
    source_kind: Literal["maker", "directory", "other"] = "other"


class Vendor(DomainModel):
    name: str = Field(min_length=1)
    city: str | None = None
    state: str | None = None
    products: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    moq: str | None = None
    indicative_price: str | None = None
    lead_time: str | None = None
    contact_url: HttpUrl | None = None
    evidence: list[VendorEvidence] = Field(
        min_length=1, description="No evidence, no vendor (docs/09 §2.2)."
    )
    score: float = Field(ge=0, le=100)
    domain: str
    score_parts: dict[str, float] = Field(
        default_factory=dict, description="relevance, certifications, evidence, region"
    )


class VendorClass(DomainModel):
    vendor_class: Code = Field(description="Commodity or machine class code.")
    vendors: list[Vendor] = Field(default_factory=list, max_length=8)
    notes: str = ""
    queries_run: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    name: str = ""
    cached_at: str | None = Field(
        default=None, description="Set when the result came from the shared cache."
    )
    pages_read: int = 0
    pages_dropped: int = Field(default=0, description="Pages that gave no vendor with evidence.")


class VendorReport(DomainModel):
    """Vendor stage artifact."""

    classes: list[VendorClass] = Field(default_factory=list)
    region: str = "KA"
    disclaimer: str = "Leads, not endorsements; verify before purchase."
    generated_at: str = ""
    notes: list[str] = Field(default_factory=list)
    skipped_classes: list[Code] = Field(
        default_factory=list, description="Bought classes not searched (limit per run)."
    )
