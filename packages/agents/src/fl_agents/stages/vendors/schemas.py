"""Structured output of the page extractor (``extractor`` role). Untrusted: the engine checks
every field against the page text before a record is kept (docs/09 §2.2)."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["PageVendorsOut", "VendorOut"]


class VendorOut(BaseModel):
    name: str = Field(description="Company name exactly as written on the page.")
    evidence_quote: str = Field(
        description="A sentence or line copied character for character from the page that "
        "names the company and what it makes or sells (at least 20 characters)."
    )
    city: str | None = None
    state: str | None = None
    products: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    moq: str | None = None
    indicative_price: str | None = None
    lead_time: str | None = None
    contact_url: str | None = None


class PageVendorsOut(BaseModel):
    vendors: list[VendorOut] = Field(default_factory=list, max_length=3)
