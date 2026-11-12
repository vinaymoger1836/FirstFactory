"""Regional data-pack schema (docs/10 §2, ADR-010).

Every value has effective dates, a source, a retrieval date, a confidence and a ``verify``
flag. A key may hold several values over time (history); the active one on a date is the
non-superseded value whose effective window covers that date.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from fl_domain.base import DomainModel

__all__ = ["Pack", "PackManifest", "PackValue", "SourceKind"]

SourceKind = Literal["primary", "secondary", "derived", "estimate", "none"]
PackGroup = Literal[
    "labour",
    "welfare",
    "wages",
    "statutory",
    "power",
    "building",
    "fire",
    "water",
    "climate",
    "compliance",
    "land",
]


class PackValue(DomainModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    group: PackGroup
    title: str
    value: Any = Field(description="Number, string, bool, object, or null when unknown.")
    unit: str | None = None
    effective_from: date
    effective_to: date | None = None
    superseded: bool = Field(
        default=False, description="Kept for history; a later instrument replaced it."
    )
    applies_to: str | None = Field(default=None, description="e.g. 'BESCOM', 'central sphere'.")
    source_kind: SourceKind
    source_url: str | None = None
    source_title: str
    retrieved_at: date
    confidence: float = Field(ge=0, le=1)
    verify: bool
    note: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError(f"{self.key}: effective_to is before effective_from")
        if self.value is None and not self.verify:
            raise ValueError(f"{self.key}: an unknown (null) value must be marked verify: true")
        if self.source_kind in ("primary", "secondary") and not self.source_url:
            raise ValueError(f"{self.key}: {self.source_kind} source needs source_url")
        if self.source_kind in ("estimate", "none", "secondary") and not self.verify:
            raise ValueError(f"{self.key}: {self.source_kind} values must be marked verify: true")
        if self.source_kind == "estimate" and self.confidence > 0.5:
            raise ValueError(f"{self.key}: estimates must carry confidence <= 0.5")
        if self.source_kind in ("derived", "estimate", "none") and not self.note:
            raise ValueError(f"{self.key}: {self.source_kind} values need a note explaining them")
        return self

    def active_on(self, day: date) -> bool:
        if self.superseded or day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


class PackManifest(DomainModel):
    state: Literal["KA"]
    version: str = Field(pattern=r"^\d{4}\.\d{2}\.\d+$")
    as_of: date = Field(description="Date the pack was compiled; validity is checked on it.")
    next_review: date
    description: str = ""
    files: list[str] = Field(min_length=1)


class Pack(DomainModel):
    manifest: PackManifest
    values: list[PackValue] = Field(min_length=1)

    def keys(self) -> list[str]:
        return sorted({v.key for v in self.values})

    def history(self, key: str) -> list[PackValue]:
        return sorted(
            (v for v in self.values if v.key == key), key=lambda v: v.effective_from
        )

    def active(self, key: str, day: date | None = None) -> PackValue | None:
        on = day or self.manifest.as_of
        hits = [v for v in self.values if v.key == key and v.active_on(on)]
        return hits[0] if len(hits) == 1 else None
