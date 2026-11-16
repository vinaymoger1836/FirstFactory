"""Structured outputs the intake agent asks the model for (validated by the router)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BomColumnGuess",
    "DocExtraction",
    "ParamValue",
    "SummaryOut",
    "TextExtraction",
    "VisionExtraction",
]


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ParamValue(_Out):
    name: str = Field(description="One of the allowed parameter names.")
    value: float
    unit: str | None = None
    variant: str | None = Field(
        default=None, description="Variant code the value applies to, or null for all variants."
    )
    evidence: str = Field(description="The exact words from the text that state the value.")
    page: int | None = Field(default=None, description="PDF page number (datasheets only).")


class TextExtraction(_Out):
    archetype_guess: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=20)
    params: list[ParamValue] = Field(default_factory=list, max_length=60)


class DocSpec(_Out):
    name: str
    value: str
    unit: str | None = None
    page: int | None = None


class DocExtraction(_Out):
    params: list[ParamValue] = Field(default_factory=list, max_length=60)
    other_specs: list[DocSpec] = Field(default_factory=list, max_length=30)


class VisionExtraction(_Out):
    components: list[str] = Field(default_factory=list, max_length=20)
    features: list[str] = Field(default_factory=list, max_length=20)
    size_cues: list[str] = Field(default_factory=list, max_length=10)
    archetype_guess: str | None = None


class SummaryOut(_Out):
    summary: str = Field(description="Two to four plain sentences for the user.")
    highlights: list[str] = Field(default_factory=list, max_length=5)


class BomColumnGuess(_Out):
    part_no: str | None = None
    name: str | None = None
    qty: str | None = None
    uom: str | None = None
    material: str | None = None
