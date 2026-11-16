"""S1 merge rules (docs/03 §3.2). Deterministic; no LLM imports.

Candidate values for one field come from several sources. The engine:

1. ranks them by source priority ``user > cad > datasheet > bom_file > vision > llm >
   library`` (then by confidence); unsupported values (confidence < 0.35) rank last;
2. converts units to the winner's unit with pint;
3. treats numeric values within 5 % of the winner as agreeing;
4. raises a choice **question** when another candidate with confidence ≥ 0.5 disagrees
   with the winner by more than 5 % and both have confidence ≥ 0.5;
5. never asks about a field the user has already set or answered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from fl_domain.prov import Source
from fl_domain.units import registry

__all__ = [
    "AGREE_REL_TOL",
    "CONFLICT_MIN_CONFIDENCE",
    "SOURCE_PRIORITY",
    "Candidate",
    "Conflict",
    "MergeResult",
    "merge_candidates",
]

SOURCE_PRIORITY: tuple[Source, ...] = (
    "user",
    "cad",
    "datasheet",
    "bom_file",
    "vision",
    "llm",
    "web",
    "engine",
    "library",
)
AGREE_REL_TOL = 0.05
CONFLICT_MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Candidate:
    """One proposed value for a field (``path`` is a JSON pointer into ProductSpec)."""

    path: str
    value: Any
    source: Source
    confidence: float
    unit: str | None = None
    ref: str | None = None
    note: str | None = None

    def prov(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "ref": self.ref,
            "note": self.note,
        }


@dataclass(frozen=True)
class Conflict:
    """Disagreeing sources for one field: becomes a question for the user."""

    path: str
    chosen: Candidate
    others: tuple[Candidate, ...]


@dataclass
class MergeResult:
    values: dict[str, Candidate] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)


UNSUPPORTED_BELOW = 0.35


def _rank(c: Candidate) -> tuple[int, float]:
    """Source priority, then confidence. A value below ``UNSUPPORTED_BELOW`` (e.g. a model
    value whose evidence quote was not found) ranks after every supported source, so it
    never overrides a catalog default."""
    if c.confidence < UNSUPPORTED_BELOW and c.source != "user":
        return (len(SOURCE_PRIORITY), -c.confidence)
    return (SOURCE_PRIORITY.index(c.source), -c.confidence)


def _to_unit(value: float, unit: str | None, target: str | None) -> float | None:
    """``value`` in ``target`` units; None if the units are incompatible."""
    if unit == target or not unit or not target:
        return value
    try:
        q = registry().Quantity(value, unit).to(target)
    except Exception:  # pint raises several error types for unknown or mismatched units
        return None
    return float(q.magnitude)


def _agrees(a: float, b: float) -> bool:
    if a == b:
        return True
    denom = max(abs(a), abs(b))
    return denom == 0 or abs(a - b) / denom <= AGREE_REL_TOL


def _numeric(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(float(v))


def merge_candidates(candidates: list[Candidate]) -> MergeResult:
    by_path: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_path.setdefault(c.path, []).append(c)
    out = MergeResult()
    for path in sorted(by_path):
        ranked = sorted(by_path[path], key=_rank)
        winner = ranked[0]
        if _numeric(winner.value):
            converted: list[Candidate] = []
            for c in ranked[1:]:
                if not _numeric(c.value):
                    continue
                v = _to_unit(float(c.value), c.unit, winner.unit)
                if v is None:
                    continue
                converted.append(
                    Candidate(
                        path=c.path,
                        value=v,
                        source=c.source,
                        confidence=c.confidence,
                        unit=winner.unit,
                        ref=c.ref,
                        note=c.note,
                    )
                )
            disagree = [
                c
                for c in converted
                if c.confidence >= CONFLICT_MIN_CONFIDENCE
                and not _agrees(float(winner.value), float(c.value))
            ]
            if (
                disagree
                and winner.source != "user"
                and winner.confidence >= CONFLICT_MIN_CONFIDENCE
            ):
                # One entry per distinct value, best-ranked source first.
                seen: list[float] = [float(winner.value)]
                uniq: list[Candidate] = []
                for c in disagree:
                    if not any(_agrees(float(c.value), s) for s in seen):
                        seen.append(float(c.value))
                        uniq.append(c)
                out.conflicts.append(Conflict(path=path, chosen=winner, others=tuple(uniq)))
        elif winner.source != "user":
            others = tuple(
                c
                for c in ranked[1:]
                if c.confidence >= CONFLICT_MIN_CONFIDENCE
                and winner.confidence >= CONFLICT_MIN_CONFIDENCE
                and str(c.value).strip().lower() != str(winner.value).strip().lower()
            )
            if others:
                out.conflicts.append(Conflict(path=path, chosen=winner, others=others[:3]))
        out.values[path] = winner
    return out
