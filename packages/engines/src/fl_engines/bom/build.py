"""S2 BoM engine (docs/03 §4, ADR-022). Deterministic: no LLM imports, numbers from data.

``build_bom`` turns S1 facts, the user's BoM rows, CAD parts, an optional model proposal
and the user's review decisions into a ``Bom``:

1. lines: user rows (case B/C) or the archetype skeleton matched to CAD (case A);
2. findings: gap check by commodity class, CAD cross-check, line quality (all suggestions
   or questions, never silent changes);
3. decisions applied (accept/reject, quantities, materials, make/buy; ``source=user``);
4. EBOM → MBOM by the skeleton's sub-assemblies; only given/accepted lines;
5. make/buy recommendation and catalog-only preview;
6. mass (CAD volume × density) and indicative cost per line, totals per variant, each with
   its calculation trail.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from fl_domain.artifacts.bom import (
    Bom,
    BomFinding,
    BomLine,
    CalcStep,
    CoverageChip,
    Item,
    MakeBuyDecision,
    MakeBuyPreview,
    UserLineSnapshot,
    VariantTotals,
)
from fl_domain.common import PriceRange, Range
from fl_domain.library.catalog import (
    ArchetypeEntry,
    Catalog,
    MachineClassEntry,
    MaterialEntry,
    ProcessEntry,
    SkeletonLine,
)
from fl_domain.prov import Prov
from fl_domain.units import is_valid_unit, registry
from fl_engines.bom.rules import ClassRules, compact, tokens
from fl_engines.expr import ExprError, evaluate

__all__ = [
    "BuildInput",
    "CadPart",
    "Decisions",
    "ExtraLine",
    "Proposal",
    "UserRow",
    "build_bom",
    "normalise_uom",
    "skeleton_quantities",
]

ROOT = "PRODUCT"
UOM_ALIASES = {
    "pcs": "unit",
    "pc": "unit",
    "nos": "unit",
    "no": "unit",
    "ea": "unit",
    "each": "unit",
    "units": "unit",
    "unit": "unit",
    "piece": "unit",
    "pieces": "unit",
    "set": "unit",
    "mtr": "m",
    "meter": "m",
    "metre": "m",
    "ltr": "l",
    "litre": "l",
    "liter": "l",
    "gm": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
}
RANK = {
    "missing_class": 10,
    "cad_unmatched": 20,
    "qty_conflict": 30,
    "line_quality": 40,
    "note": 90,
}


# ── inputs ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UserRow:
    part_no: str
    name: str
    qty_by_variant: dict[str, float | None]
    uom: str | None
    material: str | None
    ref: str


@dataclass(frozen=True)
class CadPart:
    name: str
    count: int
    bbox_mm: tuple[float, float, float] | None
    volume_mm3: float | None
    instances: tuple[str, ...]
    ref: str


@dataclass(frozen=True)
class ExtraLine:
    name: str
    commodity_class: str
    qty: float
    uom: str
    reason: str


@dataclass
class Proposal:
    """What the model (or the rules fallback) proposed. Never numbers for engine fields."""

    proposer: str = "rules engine"
    skeleton_keep: dict[str, tuple[bool, str]] = field(default_factory=dict)
    extra_lines: list[ExtraLine] = field(default_factory=list)
    classify: dict[str, str] = field(default_factory=dict)  # part_no → class
    cad_map: dict[str, str] = field(default_factory=dict)  # CAD part name → skeleton ref
    cad_class: dict[str, str] = field(default_factory=dict)  # CAD part name → class code
    materials: dict[str, str] = field(default_factory=dict)  # part_no / skeleton ref → material
    questions: list[str] = field(default_factory=list)


@dataclass
class Decisions:
    review: dict[str, str] = field(default_factory=dict)  # line id → accepted | rejected
    qty: dict[str, float] = field(default_factory=dict)  # line id → qty
    make_buy: dict[str, str] = field(default_factory=dict)  # part_no → make | buy
    confirmed: set[str] = field(default_factory=set)  # part_no
    material: dict[str, str] = field(default_factory=dict)  # part_no → material code
    findings: dict[str, str] = field(default_factory=dict)  # finding id → answer
    excluded_classes: set[str] = field(default_factory=set)


@dataclass
class BuildInput:
    catalog: Catalog
    archetype: ArchetypeEntry
    variants: list[str]
    params: dict[str, dict[str, float]]  # variant → parameter values (S1)
    rows: list[UserRow] = field(default_factory=list)
    cad: list[CadPart] = field(default_factory=list)
    proposal: Proposal = field(default_factory=Proposal)
    decisions: Decisions = field(default_factory=Decisions)
    rejected_before: set[str] = field(default_factory=set)
    catalog_version: str = ""


# ── helpers ───────────────────────────────────────────────────────────


def _local_id(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.\-/]+", "-", text.strip()).strip("-./")
    return (s or "X")[:72]


def _fid(kind: str, key: str) -> str:
    h = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:6]
    return _local_id(f"F-{kind[:4].upper()}-{key}")[:60] + f"-{h}"


def normalise_uom(uom: str | None) -> tuple[str, bool]:
    """(uom, known). Common shop-floor spellings map to pint units."""
    if not uom or not uom.strip():
        return "unit", True
    u = uom.strip().lower().rstrip(".")
    u = UOM_ALIASES.get(u, u)
    return (u, True) if is_valid_unit(u) else (u, False)


def _convert(qty: float, from_u: str, to_u: str) -> float | None:
    if from_u == to_u:
        return qty
    try:
        return float(registry().Quantity(qty, from_u).to(to_u).magnitude)
    except Exception:  # incompatible or unknown units
        return None


def _prov(
    value: Any,
    source: str,
    conf: float,
    ref: str | None = None,
    note: str | None = None,
    unit: str | None = None,
) -> Prov[Any]:
    return Prov[Any](value=value, unit=unit, source=source, confidence=conf, ref=ref, note=note)


def _range_mul(r: PriceRange | Range, k: float) -> Range:
    return Range(low=r.low * k, base=r.base * k, high=r.high * k, unit="INR")


def _range_add(a: Range | None, b: Range | None) -> Range | None:
    if a is None:
        return b
    if b is None:
        return a
    return Range(low=a.low + b.low, base=a.base + b.base, high=a.high + b.high, unit=a.unit)


def skeleton_quantities(
    archetype: ArchetypeEntry, variants: list[str], params: dict[str, dict[str, float]]
) -> dict[str, dict[str, float | None]]:
    """qty per skeleton ref and variant, from ``qty_expr`` with the S1 parameters (safe)."""
    out: dict[str, dict[str, float | None]] = {}
    for s in archetype.bom_skeleton:
        out[s.ref] = {}
        for v in variants:
            try:
                out[s.ref][v] = round(float(evaluate(s.qty_expr, params.get(v, {}))), 6)
            except (ExprError, ValueError, ZeroDivisionError):
                out[s.ref][v] = None
    return out


# ── the build ─────────────────────────────────────────────────────────


@dataclass
class _Line:
    id: str
    part_no: str
    name: str
    qty: dict[str, float]  # variant → qty (> 0)
    uom: str
    origin: Literal["user_bom", "cad", "skeleton", "reasoner", "engine"]
    review: Literal["given", "suggested", "accepted", "rejected"]
    commodity_class: str | None
    skeleton_ref: str | None = None
    qty_source: str = "bom_file"
    qty_conf: float = 0.9
    qty_ref: str | None = None
    qty_note: str | None = None
    reason: str | None = None
    material_hint: str | None = None
    cad_refs: list[str] = field(default_factory=list)
    suggest_key: str | None = None  # remembered when rejected


def _cad_class(rules: ClassRules, part: CadPart, prop: Proposal) -> tuple[str | None, str | None]:
    """(class, skeleton ref) for a CAD part: the model's mapping first, then the rules."""
    ref = prop.cad_map.get(part.name)
    if ref:
        sk = next((s for s in rules.archetype.bom_skeleton if s.ref == ref), None)
        if sk is not None and sk.commodity:
            return sk.commodity, sk.ref
    m = rules.classify(part.name.replace("_", " "))
    if m.commodity_class is None:
        return None, None
    return m.commodity_class, rules.skeleton_line_for(m.commodity_class, part.name)


def build_bom(inp: BuildInput) -> Bom:
    cat, arch, dec, prop = inp.catalog, inp.archetype, inp.decisions, inp.proposal
    rules = ClassRules(cat, arch)
    classes = rules.classes
    by_code = cat.by_code()
    materials = {e.code: e for e in cat.entries if isinstance(e, MaterialEntry)}
    skel = {s.ref: s for s in arch.bom_skeleton}
    skel_qty = skeleton_quantities(arch, inp.variants, inp.params)
    findings: list[BomFinding] = []
    lines: list[_Line] = []
    snapshot: list[UserLineSnapshot] = []
    rejected_now: set[str] = set(inp.rejected_before)

    def qty_map(per_variant: dict[str, float | None]) -> dict[str, float]:
        return {v: float(q) for v, q in per_variant.items() if q is not None and q > 0}

    # 1. Lines ─────────────────────────────────────────────────────────
    if inp.rows:
        seen_pn: dict[str, int] = {}
        for row in inp.rows:
            uom, known = normalise_uom(row.uom)
            cls = prop.classify.get(row.part_no) or rules.classify(row.name).commodity_class
            cls_source = "llm" if row.part_no in prop.classify else "rules"
            q = qty_map(row.qty_by_variant)
            snapshot.append(
                UserLineSnapshot(
                    part_no=row.part_no, name=row.name, qty_by_variant=q, uom=uom, ref=row.ref
                )
            )
            seen_pn[row.part_no] = seen_pn.get(row.part_no, 0) + 1
            if not known:
                findings.append(
                    BomFinding(
                        id=_fid("line_quality", f"uom-{row.part_no}"),
                        kind="line_quality",
                        rank=RANK["line_quality"],
                        text=f"{row.part_no}: unknown unit '{row.uom}' (kept as written).",
                        status="info",
                    )
                )
            lid = f"L-{_local_id(row.part_no)}"
            if not q:
                fid = _fid("line_quality", f"qty-{row.part_no}")
                ans = dec.findings.get(fid)
                val = _num(ans)
                findings.append(
                    BomFinding(
                        id=fid,
                        kind="line_quality",
                        rank=RANK["line_quality"],
                        text=f"{row.part_no} {row.name}: the file gives no quantity. How many per "
                        "product?",
                        line_ids=[lid],
                        status="answered" if val else "open",
                        answer=ans,
                    )
                )
                if not val:
                    continue
                q = dict.fromkeys(inp.variants, val)
                lines.append(
                    _Line(
                        lid,
                        row.part_no,
                        row.name,
                        q,
                        uom,
                        "user_bom",
                        "given",
                        cls,
                        qty_source="user",
                        qty_conf=1.0,
                        qty_ref=f"answer:{fid}",
                        material_hint=row.material,
                    )
                )
                continue
            lines.append(
                _Line(
                    lid,
                    row.part_no,
                    row.name,
                    q,
                    uom,
                    "user_bom",
                    "given",
                    cls,
                    qty_ref=row.ref,
                    material_hint=row.material,
                    qty_note=None if cls_source == "rules" else "class proposed by the model",
                )
            )
        for pn, n in seen_pn.items():
            if n > 1:
                findings.append(
                    BomFinding(
                        id=_fid("line_quality", f"dupe-{pn}"),
                        kind="line_quality",
                        rank=RANK["line_quality"],
                        text=f"Part number {pn} appears {n} times in the file.",
                        status="info",
                    )
                )
        unclassified = [ln for ln in lines if ln.commodity_class is None]
        for ln in unclassified:
            findings.append(
                BomFinding(
                    id=_fid("note", f"class-{ln.part_no}"),
                    kind="note",
                    rank=RANK["note"],
                    text=f"{ln.part_no} {ln.name}: no commodity class could be assigned; it is "
                    "kept under final assembly.",
                    line_ids=[ln.id],
                    status="info",
                )
            )
    else:
        # Case A: the archetype skeleton, matched to CAD parts.
        cad_to_ref: dict[str, str] = {}
        for part in inp.cad:
            cls, ref = _cad_class(rules, part, prop)
            if ref is not None and ref not in cad_to_ref.values():
                cad_to_ref[part.name] = ref
        ref_to_cad = {r: p for p, r in cad_to_ref.items()}
        for s in arch.bom_skeleton:
            if s.kind == "assembly":
                continue
            keep, why = prop.skeleton_keep.get(s.ref, (True, ""))
            q = qty_map(skel_qty[s.ref])
            if not q:
                continue
            if not keep:
                fid = _fid("note", f"drop-{s.ref}")
                ans = dec.findings.get(fid)
                findings.append(
                    BomFinding(
                        id=fid,
                        kind="note",
                        rank=RANK["note"],
                        text=f"The proposer left out '{s.name}': "
                        f"{why or 'not justified by the spec'}. Add it anyway?",
                        commodity_class=s.commodity,
                        options=["Add it", "Leave it out"],
                        status="answered" if ans else "info",
                        answer=ans,
                    )
                )
                if ans != "Add it":
                    continue
            if f"ref:{s.ref}" in inp.rejected_before:
                continue
            cad_name = ref_to_cad.get(s.ref)
            cad_part = next((p for p in inp.cad if p.name == cad_name), None)
            ln = _Line(
                id=f"S-{_local_id(s.ref)}",
                part_no=_local_id(s.ref.upper().replace("_", "-")),
                name=s.name,
                qty=q,
                uom=normalise_uom(s.uom)[0],
                origin="cad" if cad_part else "skeleton",
                review="given" if cad_part else "suggested",
                commodity_class=s.commodity,
                skeleton_ref=s.ref,
                qty_source="library",
                qty_conf=0.5,
                qty_ref=f"catalog:{inp.catalog_version}:{arch.code}/bom_skeleton/{s.ref}",
                qty_note=f"qty_expr '{s.qty_expr}' with the product parameters",
                reason=None if cad_part else f"{arch.name} usually has: {s.name}",
                cad_refs=list(cad_part.instances) if cad_part else [],
                suggest_key=f"ref:{s.ref}",
            )
            if cad_part is not None:
                ln.qty_note = (ln.qty_note or "") + f"; matched to CAD cad_part '{cad_part.name}'"
                if cad_part.count not in ln.qty.values():
                    fid = _fid("qty_conflict", f"cad-{s.ref}")
                    ans = dec.findings.get(fid)
                    findings.append(
                        BomFinding(
                            id=fid,
                            kind="qty_conflict",
                            rank=RANK["qty_conflict"],
                            text=f"CAD shows {cad_part.count} × {cad_part.name}; skeleton gives "
                            f"{', '.join(f'{v}: {q:g}' for v, q in ln.qty.items())} for {s.name}.",
                            line_ids=[ln.id],
                            options=["Keep the skeleton quantities", "Use the CAD count"],
                            candidates=[{"cad": cad_part.count, "skeleton": ln.qty}],
                            status="answered" if ans else "open",
                            answer=ans,
                        )
                    )
                    if ans == "Use the CAD count":
                        ln.qty = {v: float(cad_part.count) for v in ln.qty}
                        ln.qty_source, ln.qty_conf, ln.qty_ref = "user", 1.0, f"answer:{fid}"
            lines.append(ln)
        for x in prop.extra_lines:
            if (
                x.commodity_class not in classes
                or f"class:{x.commodity_class}:{x.name}" in inp.rejected_before
            ):
                continue
            uom, _ = normalise_uom(x.uom)
            lines.append(
                _Line(
                    id=f"R-{_local_id(x.name)}",
                    part_no=_local_id(x.name.upper().replace(" ", "-")),
                    name=x.name,
                    qty={v: float(x.qty) for v in inp.variants} if x.qty > 0 else {},
                    uom=uom,
                    origin="reasoner",
                    review="suggested",
                    commodity_class=x.commodity_class,
                    qty_source="llm",
                    qty_conf=0.4,
                    qty_note="quantity proposed by the model",
                    reason=x.reason,
                    suggest_key=f"class:{x.commodity_class}:{x.name}",
                )
            )
        lines = [ln for ln in lines if ln.qty]
        # CAD parts no skeleton line explains → suggested lines (never added silently: the
        # user accepts or rejects each one; rejected ones are remembered).
        for part in inp.cad:
            if part.name in cad_to_ref or f"cad:{part.name}" in inp.rejected_before:
                continue
            cls = prop.cad_class.get(part.name)
            if cls not in classes:
                cls, _ = _cad_class(rules, part, Proposal())
            lines.append(
                _Line(
                    id=f"C-{_local_id(part.name)}",
                    part_no=_local_id("CAD-" + part.name.upper()),
                    name=part.name.replace("_", " "),
                    qty={v: float(part.count) for v in inp.variants},
                    uom="unit",
                    origin="cad",
                    review="suggested",
                    commodity_class=cls,
                    qty_source="cad",
                    qty_conf=0.9,
                    qty_ref=part.ref,
                    qty_note=f"{part.count} × in the CAD model",
                    reason=f"CAD part '{part.name}'"
                    + (f"; class '{cls}'" if cls else "; no catalog class matched"),
                    cad_refs=list(part.instances),
                    suggest_key=f"cad:{part.name}",
                )
            )

    # 2. Gap check and CAD cross-check (cases B/C) ─────────────────────
    case: Literal["A", "B", "C"] = "A"
    if inp.rows:
        present = {ln.commodity_class for ln in lines if ln.commodity_class}
        expected: dict[str, SkeletonLine] = {}
        for s in arch.bom_skeleton:
            if s.commodity and s.kind != "assembly" and s.commodity not in expected:
                expected[s.commodity] = s
        missing = [c for c in expected if c not in present]
        case = "C" if missing else "B"
        for c in missing:
            key = f"class:{c}"
            if key in inp.rejected_before or c in dec.excluded_classes:
                continue
            s = expected[c]
            q = qty_map(skel_qty[s.ref])
            if not q:
                continue
            lid = f"G-{_local_id(c)}"
            fid = _fid("missing_class", c)
            lines.append(
                _Line(
                    id=lid,
                    part_no=_local_id(s.ref.upper().replace("_", "-")),
                    name=s.name,
                    qty=q,
                    uom=normalise_uom(s.uom)[0],
                    origin="skeleton",
                    review="suggested",
                    commodity_class=c,
                    skeleton_ref=s.ref,
                    qty_source="library",
                    qty_conf=0.5,
                    qty_ref=f"catalog:{inp.catalog_version}:{arch.code}/bom_skeleton/{s.ref}",
                    qty_note=f"qty_expr '{s.qty_expr}' with the product parameters",
                    reason=f"Your BoM has no {classes[c].name.lower()}; a "
                    f"{arch.name.lower()} usually needs: {s.name}",
                    suggest_key=key,
                )
            )
            findings.append(
                BomFinding(
                    id=fid,
                    kind="missing_class",
                    rank=RANK["missing_class"],
                    text=f"Probably missing: {classes[c].name} (e.g. {s.name}).",
                    commodity_class=c,
                    line_ids=[lid],
                    options=["Accept", "Reject"],
                )
            )
        # CAD cross-check
        for part in inp.cad:
            cls, _ref = _cad_class(rules, part, prop)
            match = next(
                (
                    ln
                    for ln in lines
                    if ln.origin == "user_bom"
                    and (
                        compact(ln.part_no) == compact(part.name)
                        or (cls is not None and ln.commodity_class == cls)
                        or (
                            len(tokens(ln.name) & tokens(part.name.replace("_", " "))) >= 1
                            and cls is None
                        )
                    )
                ),
                None,
            )
            if match is None:
                if f"cad:{part.name}" in inp.rejected_before:
                    continue
                fid = _fid("cad_unmatched", part.name)
                ans = dec.findings.get(fid)
                findings.append(
                    BomFinding(
                        id=fid,
                        kind="cad_unmatched",
                        rank=RANK["cad_unmatched"],
                        text=f"CAD shows '{part.name}' ({part.count} ×), not in your BoM. Add it?",
                        commodity_class=cls,
                        options=["Add it", "Ignore"],
                        status="answered" if ans else "open",
                        answer=ans,
                    )
                )
                if ans == "Add it":
                    lines.append(
                        _Line(
                            id=f"C-{_local_id(part.name)}",
                            part_no=_local_id("CAD-" + part.name.upper()),
                            name=part.name.replace("_", " "),
                            qty={v: float(part.count) for v in inp.variants},
                            uom="unit",
                            origin="cad",
                            review="accepted",
                            commodity_class=cls,
                            qty_source="cad",
                            qty_conf=0.9,
                            qty_ref=part.ref,
                            cad_refs=list(part.instances),
                        )
                    )
                elif ans == "Ignore":
                    rejected_now.add(f"cad:{part.name}")
                continue
            match.cad_refs = list(dict.fromkeys([*match.cad_refs, *part.instances]))
            if part.count not in match.qty.values():
                fid = _fid("qty_conflict", f"{match.part_no}-{part.name}")
                ans = dec.findings.get(fid)
                findings.append(
                    BomFinding(
                        id=fid,
                        kind="qty_conflict",
                        rank=RANK["qty_conflict"],
                        text=f"CAD has {part.count} × {part.name}; your BoM says "
                        f"{', '.join(f'{v}: {q:g}' for v, q in match.qty.items())} for "
                        f"{match.part_no}.",
                        line_ids=[match.id],
                        options=["Keep my BoM", "Use the CAD count"],
                        candidates=[
                            {"source": "cad", "value": part.count},
                            {"source": "bom_file", "value": match.qty},
                        ],
                        status="answered" if ans else "open",
                        answer=ans,
                    )
                )
                if ans == "Use the CAD count":
                    match.qty = {v: float(part.count) for v in match.qty}
                    match.qty_source, match.qty_conf, match.qty_ref = "user", 1.0, f"answer:{fid}"
        cad_classes = {_cad_class(rules, p, prop)[0] for p in inp.cad}
        if inp.cad:
            for ln in lines:
                if ln.origin != "user_bom" or ln.cad_refs or ln.commodity_class in cad_classes:
                    continue
                cc = classes.get(ln.commodity_class or "")
                if cc is not None and cc.cad_rarely_modelled:
                    continue
                findings.append(
                    BomFinding(
                        id=_fid("note", f"nocad-{ln.part_no}"),
                        kind="note",
                        rank=RANK["note"],
                        text=f"{ln.part_no} {ln.name} has no matching part in the CAD model.",
                        line_ids=[ln.id],
                        status="info",
                    )
                )

    # 3. Decisions ─────────────────────────────────────────────────────
    for ln in lines:
        d = dec.review.get(ln.id)
        if ln.review == "suggested" and d in ("accepted", "rejected"):
            ln.review = "accepted" if d == "accepted" else "rejected"
        if ln.review == "rejected" and ln.suggest_key:
            rejected_now.add(ln.suggest_key)
        if ln.id in dec.qty and dec.qty[ln.id] > 0:
            ln.qty = {v: float(dec.qty[ln.id]) for v in ln.qty}
            ln.qty_source, ln.qty_conf, ln.qty_ref = "user", 1.0, "user-edit"
            ln.qty_note = "edited by the user"
    for f in findings:
        if f.kind == "missing_class" and f.line_ids:
            ln_ = next((x for x in lines if x.id == f.line_ids[0]), None)
            if ln_ is not None and ln_.review in ("accepted", "rejected"):
                f.status = ln_.review
                f.answer = ln_.review

    # 4. Items ─────────────────────────────────────────────────────────
    sk_by_class: dict[str, SkeletonLine] = {}
    for s in arch.bom_skeleton:
        if s.commodity and s.commodity not in sk_by_class:
            sk_by_class[s.commodity] = s
    cad_by_instance = {i: p for p in inp.cad for i in p.instances}
    items: dict[str, Item] = {}
    item_calc: dict[str, list[CalcStep]] = {}
    unit_mass: dict[str, float] = {}
    unit_cost: dict[str, Range] = {}

    def recommendation(cls: str | None, sk_ref: str | None) -> tuple[str, str]:
        s = skel.get(sk_ref or "") or (sk_by_class.get(cls) if cls else None)
        if s is not None:
            return s.make_or_buy, f"{arch.name} skeleton ({s.name})"
        cc = classes.get(cls or "")
        if cc is not None:
            return cc.default_make_or_buy, f"class default for {cc.name}"
        return "buy", "no catalog guidance; buy assumed"

    for ln in lines:
        if ln.part_no in items:
            continue
        cc = classes.get(ln.commodity_class or "")
        sk_item = skel.get(ln.skeleton_ref or "") or (
            sk_by_class.get(ln.commodity_class or "") if ln.commodity_class else None
        )
        kind: Literal["part", "consumable", "packaging"] = (
            sk_item.kind if sk_item is not None and sk_item.kind != "assembly" else "part"
        )
        rec, _why = recommendation(ln.commodity_class, ln.skeleton_ref)
        mb = dec.make_buy.get(ln.part_no, rec)
        # material: user > model > BoM file column > class default
        mat_code, mat_src = None, ""
        if ln.part_no in dec.material:
            mat_code, mat_src = dec.material[ln.part_no], "user"
        elif ln.part_no in prop.materials or (ln.skeleton_ref or "") in prop.materials:
            mat_code = prop.materials.get(ln.part_no) or prop.materials.get(ln.skeleton_ref or "")
            mat_src = "llm"
        elif ln.material_hint:
            mat_code = _match_material(materials, ln.material_hint)
            mat_src = "bom_file"
        if mat_code is None and cc is not None and cc.default_material:
            mat_code, mat_src = cc.default_material, "library"
        mat = materials.get(mat_code or "")
        calc: list[CalcStep] = []
        mass: Prov[float] | None = None
        vols = [cad_by_instance[i].volume_mm3 for i in ln.cad_refs if i in cad_by_instance]
        vol = vols[0] if vols else None
        if mat is not None and vol:
            m_g = vol / 1000.0 * mat.density_g_cm3
            unit_mass[ln.part_no] = m_g
            calc.append(CalcStep(label="CAD volume", value=round(vol, 1), unit="mm³", source="cad"))
            calc.append(
                CalcStep(
                    label=f"Density of {mat.name}",
                    value=mat.density_g_cm3,
                    unit="g/cm³",
                    source=f"catalog material {mat.code}",
                )
            )
            calc.append(
                CalcStep(
                    label="Mass per unit",
                    formula="volume / 1000 × density",
                    value=round(m_g, 2),
                    unit="g",
                )
            )
            mass = _prov(
                round(m_g, 2),
                "engine",
                0.8 if mat_src in ("user", "bom_file") else 0.5,
                ref=f"cad:{ln.cad_refs[0]}",
                note=f"CAD volume × {mat.code} density",
                unit="g",
            )
        cost: Range | None = None
        if mb == "buy" and cc is not None and cc.unit_price_inr is not None:
            factor = _convert(1.0, ln.uom, normalise_uom(cc.uom)[0])
            if factor is not None:
                cost = _range_mul(cc.unit_price_inr, factor)
                calc.append(
                    CalcStep(
                        label=f"Catalog price, {cc.name}",
                        formula=f"{cc.unit_price_inr.base:g} INR per {cc.uom}"
                        + (f" × {factor:g} ({ln.uom} → {cc.uom})" if factor != 1 else ""),
                        value=round(cost.base, 2),
                        unit="INR",
                        source=f"catalog {cc.code} ({cc.unit_price_inr.source})",
                    )
                )
        elif (
            mb == "make"
            and mat is not None
            and mat.price_inr_per_kg is not None
            and ln.part_no in unit_mass
        ):
            kg = unit_mass[ln.part_no] / 1000.0
            cost = _range_mul(mat.price_inr_per_kg, kg)
            calc.append(
                CalcStep(
                    label=f"Material cost, {mat.name}",
                    formula=f"{kg:.4f} kg × {mat.price_inr_per_kg.base:g} INR/kg",
                    value=round(cost.base, 2),
                    unit="INR",
                    source=f"catalog material {mat.code}",
                )
            )
        if cost is not None:
            unit_cost[ln.part_no] = cost
        item_calc[ln.part_no] = calc
        all_variants = set(inp.variants)
        variants = sorted({v for x in lines if x.part_no == ln.part_no for v in x.qty})
        items[ln.part_no] = Item(
            part_no=ln.part_no,
            name=ln.name,
            kind=kind,
            material=_prov(
                mat_code,
                mat_src if mat_src != "rules" else "library",
                1.0 if mat_src == "user" else 0.9 if mat_src == "bom_file" else 0.5,
            )
            if mat_code
            else None,
            mass_g=mass,
            commodity_class=ln.commodity_class,
            make_or_buy=_prov(
                mb,
                "user" if ln.part_no in dec.make_buy else "library",
                1.0 if ln.part_no in dec.make_buy else 0.5,
            ),
            variants=[] if set(variants) == all_variants else variants,
            cad_refs=ln.cad_refs,
            unit_cost_inr=cost,
            calc=calc,
        )

    # 5. EBOM lines (flat under the product: the design view) ──────────
    def line_rows(
        ln: _Line,
        level: Literal["ebom", "mbom"],
        parent: str,
        sub: str | None,
        ebom_ref: str | None,
        lid: str,
    ) -> list[BomLine]:
        qs = ln.qty
        same = len(set(qs.values())) == 1 and set(qs) == set(inp.variants)
        groups = [(None, next(iter(qs.values())))] if same else sorted(qs.items())
        out: list[BomLine] = []
        for v, q in groups:
            ext_cost = None
            ext_mass = None
            if ln.part_no in unit_cost:
                ext_cost = _range_mul(unit_cost[ln.part_no], q)
            if ln.part_no in unit_mass and ln.uom == "unit":
                ext_mass = round(unit_mass[ln.part_no] * q, 3)
            elif ln.uom in ("g", "kg"):
                ext_mass = round(_convert(q, ln.uom, "g") or 0.0, 3)
            out.append(
                BomLine(
                    id=lid if v is None else f"{lid}-{v}",
                    parent=parent,
                    child=ln.part_no,
                    qty=_prov(q, ln.qty_source, ln.qty_conf, ln.qty_ref, ln.qty_note),
                    uom=ln.uom,
                    variant=v,
                    level=level,
                    ebom_ref=ebom_ref if ebom_ref is None or v is None else f"{ebom_ref}-{v}",
                    subassembly=sub,
                    origin=ln.origin,
                    review=ln.review,
                    commodity_class=ln.commodity_class,
                    reason=ln.reason,
                    ext_cost_inr=ext_cost,
                    ext_mass_g=ext_mass,
                )
            )
        return out

    ebom: list[BomLine] = []
    for ln in lines:
        ebom += line_rows(ln, "ebom", ROOT, None, None, ln.id)

    # 6. MBOM: given/accepted lines grouped into the skeleton's sub-assemblies ──
    asm_lines = {s.ref: s for s in arch.bom_skeleton if s.kind == "assembly"}

    def asm_id(ref: str) -> str:
        return _local_id("SA-" + ref.upper().replace("_", ""))

    used_asm: set[str] = set()
    mbom: list[BomLine] = []
    for ln in lines:
        if ln.review not in ("given", "accepted"):
            continue
        if ln.commodity_class in dec.excluded_classes:
            continue
        sk_line = skel.get(ln.skeleton_ref or "") or (
            sk_by_class.get(ln.commodity_class or "") if ln.commodity_class else None
        )
        parent_ref = sk_line.parent if sk_line is not None else None
        parent = asm_id(parent_ref) if parent_ref in asm_lines else ROOT
        sub = sk_line.subassembly if sk_line is not None else "final assembly"
        if parent_ref in asm_lines:
            r: str | None = parent_ref
            while r is not None and r in asm_lines:
                used_asm.add(r)
                r = asm_lines[r].parent
        mbom += line_rows(ln, "mbom", parent, sub, ln.id, f"M-{ln.id}")
    for ref in sorted(used_asm):
        a = asm_lines[ref]
        aid = asm_id(ref)
        parent = asm_id(a.parent) if a.parent in asm_lines else ROOT
        items[aid] = Item(
            part_no=aid,
            name=a.name,
            kind="assembly",
            make_or_buy=_prov(
                dec.make_buy.get(aid, a.make_or_buy),
                "user" if aid in dec.make_buy else "library",
                1.0 if aid in dec.make_buy else 0.5,
            ),
        )
        mbom.append(
            BomLine(
                id=f"M-{aid}",
                parent=parent,
                child=aid,
                qty=_prov(1.0, "library", 0.5, f"catalog:{inp.catalog_version}:{arch.code}"),
                uom="unit",
                level="mbom",
                subassembly=a.subassembly,
                origin="engine",
                review="given",
            )
        )
    items[ROOT] = Item(
        part_no=ROOT, name="Product", kind="product", make_or_buy=_prov("make", "library", 1.0)
    )

    # 7. Make/buy decisions and preview ───────────────────────────────
    procs = {e.code: e for e in cat.entries if isinstance(e, ProcessEntry)}
    decisions_out: list[MakeBuyDecision] = []
    for pn, it in items.items():
        if it.kind not in ("part", "assembly") or pn == ROOT:
            continue
        cc = classes.get(it.commodity_class or "")
        route: list[str] = []
        if cc is not None and cc.make_route:
            route = list(cc.make_route)
        elif it.kind == "assembly":
            child_classes = {
                ln.commodity_class for ln in mbom if ln.parent == pn and ln.commodity_class
            }
            route = [
                r.process
                for r in arch.typical_routing
                if r.process in procs and set(procs[r.process].applies_to.commodity) & child_classes
            ]
        if (
            it.kind == "part"
            and not route
            and (cc is None or cc.default_make_or_buy == "buy")
            and it.make_or_buy.value == "buy"
            and pn not in dec.make_buy
        ):
            continue  # nothing to decide: bought, no in-house route in the catalog
        sk_ref = next((x.skeleton_ref for x in lines if x.part_no == pn), None)
        rec, why = (
            recommendation(it.commodity_class, sk_ref)
            if it.kind == "part"
            else (
                asm_lines[next(r for r in asm_lines if asm_id(r) == pn)].make_or_buy,
                f"{arch.name} skeleton",
            )
        )
        preview = (
            _preview(route, procs, by_code)
            if route
            else MakeBuyPreview(note="No in-house route in the catalog for this item.")
        )
        decisions_out.append(
            MakeBuyDecision(
                part_no=pn,
                recommendation="make" if rec == "make" else "buy",
                decision=it.make_or_buy,
                rationale=why,
                preview=preview,
                confirmed=pn in dec.confirmed,
            )
        )
        if it.make_or_buy.value == "make" and it.kind == "part":
            has_proc = any(
                (it.commodity_class or "") in p.applies_to.commodity for p in procs.values()
            )
            if not has_proc:
                items[pn] = it.model_copy(update={"uncatalogued": True})

    # 8. Totals per variant (MBOM leaf lines) ─────────────────────────
    totals: list[VariantTotals] = []
    for v in inp.variants:
        cost_t: Range | None = None
        mass_t = 0.0
        no_cost = no_mass = 0
        for ml in mbom:
            if ml.variant not in (None, v) or items[ml.child].kind == "assembly":
                continue
            if ml.ext_cost_inr is None:
                no_cost += 1
            cost_t = _range_add(cost_t, ml.ext_cost_inr)
            if ml.ext_mass_g is None:
                no_mass += 1
            else:
                mass_t += ml.ext_mass_g
        totals.append(
            VariantTotals(
                variant=v,
                cost_inr=Range(
                    low=round(cost_t.low, 2),
                    base=round(cost_t.base, 2),
                    high=round(cost_t.high, 2),
                    unit="INR",
                )
                if cost_t
                else None,
                mass_g=round(mass_t, 2) if mass_t else None,
                lines_without_cost=no_cost,
                lines_without_mass=no_mass,
            )
        )

    for question in prop.questions[:5]:
        findings.append(
            BomFinding(
                id=_fid("note", f"ask-{question[:40]}"),
                kind="note",
                rank=RANK["note"],
                text=f"The proposer asks: {question}",
                status="info",
            )
        )
    findings.sort(key=lambda f: (f.rank, f.id))
    coverage: list[CoverageChip] = []
    seen_cls: set[str] = set()
    for s in arch.bom_skeleton:
        cc_code = s.commodity
        if not cc_code or s.kind == "assembly" or cc_code in seen_cls or cc_code not in classes:
            continue
        c = cc_code
        seen_cls.add(c)
        of_class = [ln for ln in lines if ln.commodity_class == c]
        state: Literal["covered", "suggested", "rejected", "missing", "excluded"]
        if c in dec.excluded_classes:
            state = "excluded"
        elif any(ln.review in ("given", "accepted") for ln in of_class):
            state = "covered"
        elif any(ln.review == "suggested" for ln in of_class):
            state = "suggested"
        elif (
            f"class:{c}" in rejected_now
            or any(ln.review == "rejected" for ln in of_class)
            or (s.ref and f"ref:{s.ref}" in rejected_now)
        ):
            state = "rejected"
        else:
            state = "missing"
        coverage.append(
            CoverageChip(
                commodity_class=c,
                label=classes[c].name,
                state=state,
                mbom_only=s.kind == "consumable",
            )
        )
    return Bom(
        root=ROOT,
        variants=inp.variants,
        items=list(items.values()),
        ebom=ebom,
        mbom=mbom,
        make_buy_decisions=decisions_out,
        commodity_classes=sorted({i.commodity_class for i in items.values() if i.commodity_class}),
        case=case,
        findings=findings,
        user_snapshot=snapshot,
        rejected_suggestions=sorted(rejected_now),
        excluded_classes=sorted(dec.excluded_classes),
        totals=totals,
        coverage=coverage,
        proposer=prop.proposer,
    )


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        v = float(str(text).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 and math.isfinite(v) else None


def _match_material(materials: dict[str, MaterialEntry], text: str) -> str | None:
    t = compact(text)
    for m in materials.values():
        if compact(m.code) == t or compact(m.name) == t or any(compact(a) == t for a in m.aliases):
            return m.code
    for m in materials.values():
        if any(compact(a) and compact(a) in t for a in m.aliases):
            return m.code
    return None


def _preview(
    route: list[str], procs: dict[str, ProcessEntry], by_code: dict[str, Any]
) -> MakeBuyPreview:
    capex: Range | None = None
    area = 0.0
    ops = 0.0
    for code in route:
        p = procs.get(code)
        if p is None:
            continue
        if p.machine_class:
            mc = by_code.get(p.machine_class)
            if isinstance(mc, MachineClassEntry):
                capex = _range_add(capex, _range_mul(mc.price_inr, 1.0))
        if p.price_inr is not None:
            capex = _range_add(capex, _range_mul(p.price_inr, 1.0))
        area += p.footprint_m2
        ops += p.operators_per_machine
    return MakeBuyPreview(
        capex_inr=capex,
        area_m2=Range(low=round(area, 1), base=round(area, 1), high=round(area, 1), unit="m**2"),
        operators_per_shift=Range(low=ops, base=ops, high=ops, unit="worker"),
        note="Indicative, catalog estimates: one station per process; S5 sizes the real line.",
        processes=route,
    )
