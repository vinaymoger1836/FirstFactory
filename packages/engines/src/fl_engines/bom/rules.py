"""Commodity-class rules for BoM lines and CAD parts (docs/03 §4.6). Deterministic.

A name is matched against phrases of each class: the archetype's skeleton line names for
that class, the class's catalog aliases and its name. Classes the archetype's skeleton
expects get a small boost, so "shell" in a power bank is not read as a fan rotor shell.
Lines the rules cannot place are left for the model (closed list of class codes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fl_domain.library.catalog import ArchetypeEntry, Catalog, CommodityClassEntry

__all__ = ["MIN_SCORE", "ClassMatch", "ClassRules", "compact", "tokens"]

MIN_SCORE = 0.5
SKELETON_BOOST = 0.15
MARGIN = 0.05
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "and",
        "with",
        "the",
        "of",
        "for",
        "on",
        "in",
        "to",
        "a",
        "an",
        "mm",
        "kg",
        "g",
        "ml",
        "pcs",
        "nos",
        "x",
        "per",
        "set",
        "unit",
        "units",
        "type",
        "grade",
    }
)


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("es") and not w.endswith("ses"):
        return w[:-2] if w[-3] in "hx" else w[:-1]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP)


def compact(text: str) -> str:
    return "".join(_WORD.findall(text.lower()))


@dataclass(frozen=True)
class ClassMatch:
    commodity_class: str | None
    score: float
    phrase: str = ""
    second: str | None = None


class ClassRules:
    """Prepared phrases per class for one archetype and catalog version."""

    def __init__(self, catalog: Catalog, archetype: ArchetypeEntry) -> None:
        self.catalog = catalog
        self.archetype = archetype
        self.classes: dict[str, CommodityClassEntry] = {
            e.code: e for e in catalog.entries if isinstance(e, CommodityClassEntry)
        }
        self.skeleton_classes = {s.commodity for s in archetype.bom_skeleton if s.commodity}
        phrases: dict[str, list[str]] = {c: [] for c in self.classes}
        for s in archetype.bom_skeleton:
            if s.commodity in phrases:
                phrases[s.commodity].append(s.name)
        for c, e in self.classes.items():
            phrases[c] += [*e.aliases, e.name]
        self.phrases = phrases

    @staticmethod
    def _phrase_score(name: str, phrase: str) -> float:
        if not phrase.strip():
            return 0.0
        if compact(phrase) == compact(name):
            return 1.1
        pt, nt = tokens(phrase), tokens(name)
        if not pt:
            return 0.0
        if pt <= nt:
            # A full match of a longer phrase is more specific ("blade sleeve" over "blade").
            return 1.0 + 0.02 * (len(pt) - 1)
        return len(pt & nt) / len(pt)

    def classify(self, name: str) -> ClassMatch:
        scored: list[tuple[float, str, str]] = []
        for c, ps in self.phrases.items():
            best, best_p = 0.0, ""
            for p in ps:
                s = self._phrase_score(name, p)
                if s > best:
                    best, best_p = s, p
            if best > 0:
                if c in self.skeleton_classes:
                    best += SKELETON_BOOST
                scored.append((best, c, best_p))
        if not scored:
            return ClassMatch(None, 0.0)
        scored.sort(key=lambda t: (-t[0], t[1]))
        top = scored[0]
        second = scored[1] if len(scored) > 1 else None
        if top[0] < MIN_SCORE or (second is not None and top[0] - second[0] < MARGIN):
            return ClassMatch(None, round(top[0], 3), top[2], second[1] if second else None)
        return ClassMatch(top[1], round(min(top[0], 1.0), 3), top[2], second[1] if second else None)

    def skeleton_line_for(self, commodity: str, name: str) -> str | None:
        """The skeleton ref of this class whose name fits ``name`` best (first on a tie)."""
        lines = [s for s in self.archetype.bom_skeleton if s.commodity == commodity]
        if not lines:
            return None
        return max(lines, key=lambda s: self._phrase_score(name, s.name)).ref
