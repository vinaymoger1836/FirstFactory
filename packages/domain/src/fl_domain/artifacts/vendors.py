"""Parallel stage P1: vendor research (docs/09 §2). Leads, not endorsements."""

from __future__ import annotations

from pydantic import Field, HttpUrl

from fl_domain.base import Code, DomainModel

__all__ = ["Vendor", "VendorClass", "VendorEvidence", "VendorReport"]


class VendorEvidence(DomainModel):
    url: HttpUrl
    fetched_at: str
    snippet: str = Field(min_length=1, description="Quoted text that supports the record.")


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


class VendorClass(DomainModel):
    vendor_class: Code = Field(description="Commodity or machine class code.")
    vendors: list[Vendor] = Field(default_factory=list, max_length=8)
    notes: str = ""
    queries_run: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class VendorReport(DomainModel):
    """Vendor stage artifact."""

    classes: list[VendorClass] = Field(default_factory=list)
    region: str = "KA"
    disclaimer: str = "Leads, not endorsements; verify before purchase."
