"""Archetype classification by rules (docs/03 §3.2 item 5). Deterministic; no LLM imports.

Order of evidence:
1. an archetype the user picked in the wizard wins (``source=user``);
2. otherwise every catalog archetype of the product's category is scored by keyword hits
   (catalog aliases, name, BoM-skeleton line names, parameter names) in the product text,
   CAD part names and photo components;
3. a clear winner is used (``source=library`` because the rules come from the catalog);
4. otherwise the extractor's guess is used when it names a known archetype of the same
   category (``source=llm``);
5. otherwise ``generic_<category>`` with a notice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fl_domain.library.catalog import ArchetypeEntry
from fl_domain.prov import Source

__all__ = ["MIN_SCORE", "Classification", "classify_archetype", "keyword_set"]

MIN_SCORE = 2.0
# A match in another category than the one chosen needs more than one keyword's worth of
# evidence: "3015 24 V fan" must not become a BLDC ceiling fan because it says "fan".
CROSS_CATEGORY_MIN_SCORE = 5.0
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "and",
        "the",
        "with",
        "per",
        "for",
        "of",
        "on",
        "in",
        "to",
        "a",
        "an",
        "n",
        "unit",
        "units",
        "assembly",
        "part",
        "label",
        "labels",
        "box",
        "carton",
        "manual",
        "user",
        "screws",
        "screw",
        "adhesive",
        "kit",
        "set",
        "incl",
        "rating",
        "qr",
        "retail",
        "shipper",
        "final",
        "core",
        "mm",
        "ma",
        "kg",
        "g",
        "s",
    }
)


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def keyword_set(entry: ArchetypeEntry) -> dict[str, float]:
    """Weighted keywords for one archetype: aliases and name count most."""
    kw: dict[str, float] = {}

    def add(text: str, weight: float) -> None:
        for w in _words(text):
            kw[w] = max(kw.get(w, 0.0), weight)

    for alias in entry.aliases:
        add(alias, 3.0)
        compact = alias.lower().replace(" ", "")
        if compact.isalnum():
            kw[compact] = max(kw.get(compact, 0.0), 3.0)
    add(entry.name, 2.0)
    add(entry.code.replace("_", " "), 3.0)
    for line in entry.bom_skeleton:
        add(line.name, 1.0)
    for p in entry.parameters:
        add(p.name.replace("_", " "), 0.5)
    return kw


@dataclass(frozen=True)
class Classification:
    archetype: str
    source: Source
    confidence: float
    note: str
    scores: dict[str, float]
    notice: str | None = None


def classify_archetype(
    archetypes: list[ArchetypeEntry],
    category: str,
    *,
    user_choice: str | None,
    texts: list[str],
    cad_names: list[str],
    vision_terms: list[str],
    llm_guess: str | None,
) -> Classification:
    codes = {a.code for a in archetypes}
    if user_choice and user_choice in codes:
        return Classification(
            archetype=user_choice,
            source="user",
            confidence=1.0,
            note="Chosen in the wizard",
            scores={},
        )
    # Score every known archetype; the chosen category is preferred, but a clear match in
    # the other category still wins (the wizard's category defaults to electronics).
    pool = [a for a in archetypes if not a.code.startswith("generic_")]
    in_cat = {a.code for a in pool if a.category == category}
    corpus = _words(" ".join(texts)) + _words(" ".join(cad_names)) + _words(" ".join(vision_terms))
    joined = " ".join(" ".join(texts + cad_names + vision_terms).lower().split())
    scores: dict[str, float] = {}
    for a in pool:
        kw = keyword_set(a)
        hits = {w for w in corpus if w in kw}
        score = sum(kw[w] for w in hits)
        # Multi-word aliases ("ceiling fan") as phrases.
        score += sum(3.0 for alias in a.aliases if " " in alias and alias.lower() in joined)
        scores[a.code] = round(score, 2)
    generic = f"generic_{category}"

    def decisive(cands: dict[str, float], floor: float = MIN_SCORE) -> tuple[str, float] | None:
        ranked = sorted(cands.items(), key=lambda kv: (-kv[1], kv[0]))
        if (
            ranked
            and ranked[0][1] >= floor
            and (len(ranked) == 1 or ranked[0][1] >= 1.5 * ranked[1][1])
        ):
            return ranked[0]
        return None

    hit = decisive({k: v for k, v in scores.items() if k in in_cat})
    other = None if hit else decisive(scores, CROSS_CATEGORY_MIN_SCORE)
    pick = hit or other
    if pick is not None:
        best, score = pick
        conf = min(0.9, 0.5 + score / 40)
        cross = hit is None
        return Classification(
            archetype=best,
            source="library",
            confidence=round(conf - (0.1 if cross else 0.0), 2),
            note=f"Keyword match against the catalog archetype (score {score:g})",
            scores=scores,
            notice=(
                f"Matched '{best}', which the catalog files under a different category than "
                f"'{category}'. Change the product type in the template if this is wrong."
                if cross
                else None
            ),
        )
    if llm_guess and llm_guess in codes and not llm_guess.startswith("generic_"):
        entry = next(a for a in archetypes if a.code == llm_guess)
        if entry.category == category:
            return Classification(
                archetype=llm_guess,
                source="llm",
                confidence=0.5,
                note="Suggested by the extractor; keyword rules were not decisive",
                scores=scores,
            )
    return Classification(
        archetype=generic if generic in codes else sorted(codes)[0],
        source="library",
        confidence=0.3,
        note="No known archetype matched; generic fallback",
        scores=scores,
        notice=(
            "The product did not match a known product type, so a generic "
            f"{category} template is used. Expect more questions in the BoM stage."
        ),
    )
