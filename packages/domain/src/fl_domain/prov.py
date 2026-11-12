"""Provenance wrapper (ADR-016, docs/03 §1.3, docs/04 §3).

Every leaf value that is not a pure identifier is stored as ``Prov[T]`` so the UI can show
where it came from and how much to trust it, and the assumptions register can be built
automatically. User edits carry ``source="user"`` and are never overwritten by re-runs.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import Field

from fl_domain.base import DomainModel

__all__ = ["LOW_CONFIDENCE", "Prov", "Source", "is_assumption"]

T = TypeVar("T")

Source = Literal[
    "user", "cad", "datasheet", "vision", "llm", "library", "web", "engine", "bom_file"
]

# Values below this confidence, or from an LLM or the web, go to the assumptions register
# (docs/09 §3.1 item 12).
LOW_CONFIDENCE = 0.6


class Prov(DomainModel, Generic[T]):
    """A value with its unit, source, confidence and a reference to the evidence."""

    value: T
    unit: str | None = None
    source: Source
    confidence: float = Field(ge=0, le=1)
    ref: str | None = Field(
        default=None, description="Evidence pointer, e.g. 'attachment:att_03#page2'."
    )
    note: str | None = None


def is_assumption(p: Prov[object]) -> bool:
    """True when the value belongs in the assumptions register."""
    return p.confidence < LOW_CONFIDENCE or p.source in ("llm", "web")
