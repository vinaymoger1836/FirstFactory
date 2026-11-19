"""Catalog tests (docs/10 §1.4) and pack tests (docs/10 §2.3), run by the loader.

Catalog:
  1. schema validation for every entry (done on load, ``fl_library.io``);
  2. codes unique; every reference (machine_class, check, commodity, zone_kind, process,
     archetype) resolves;
  3. every formula evaluates with the golden/example product parameters to a finite,
     positive number of seconds, using only declared parameters (each with a unit);
  4. ranges ordered (low ≤ base ≤ high).
Pack:
  1. schema; every value has a source and an effective date (schema);
  2. exactly one active value per key on the pack's as-of date (so no expired value is
     used without a newer one, and no two values compete);
  3. ``verify: true`` values are collected for the assumptions register.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from fl_domain.base import Issue
from fl_domain.common import Assumption, PriceRange, Range
from fl_domain.library.catalog import (
    ArchetypeEntry,
    Catalog,
    CheckTypeEntry,
    CommodityClassEntry,
    MachineClassEntry,
    MaterialEntry,
    PolicyEntry,
    ProcessEntry,
    SamplingPlanEntry,
    ZoneKindEntry,
)
from fl_domain.library.pack import Pack
from fl_domain.units import is_valid_unit
from fl_engines.expr import ExprError, evaluate, evaluate_with_variables, names_in, parse

__all__ = [
    "check_catalog",
    "check_cross",
    "check_pack",
    "formula_value",
    "pack_assumptions",
    "param_sets",
]


def _err(code: str, msg: str, path: str = "") -> Issue:
    return Issue(code=code, message=msg, path=path)


def _warn(code: str, msg: str, path: str = "") -> Issue:
    return Issue(code=code, message=msg, path=path, severity="warning")


def _ref(
    out: list[Issue], codes: set[str], values: Iterable[str | None], what: str, where: str
) -> None:
    for v in values:
        if v is not None and v not in codes:
            out.append(_err("catalog.ref", f"unknown {what} '{v}'", where))


def _ordered(out: list[Issue], r: PriceRange | Range | None, where: str) -> None:
    if r is not None and not (r.low <= r.base <= r.high):
        out.append(_err("catalog.range", f"range not ordered: {r.low}/{r.base}/{r.high}", where))


def param_sets(
    catalog: Catalog, proc: ProcessEntry
) -> list[tuple[str, dict[str, float], dict[str, str]]]:
    """(label, parameters, declared units) for every test case of a process formula."""
    by_code = catalog.by_code()
    out: list[tuple[str, dict[str, float], dict[str, str]]] = []
    declared: dict[str, str] = {}
    for a in proc.applies_to.archetype:
        arch = by_code.get(a)
        if not isinstance(arch, ArchetypeEntry):
            continue
        units = {p.name: p.unit for p in arch.parameters}
        declared.update(units)
        out += [(f"{a}:{v.code}", dict(v.params), units) for v in arch.example_variants]
    if proc.example_params:
        out.append(("example_params", dict(proc.example_params), declared))
    return out


def _machine_params(catalog: Catalog, code: str | None) -> dict[str, float]:
    m = catalog.by_code().get(code or "")
    return dict(m.params) if isinstance(m, MachineClassEntry) else {}


def _check_formula(
    out: list[Issue],
    where: str,
    label: str,
    formula: str,
    variables: dict[str, str],
    params: dict[str, float],
    units: dict[str, str],
    extra_env: dict[str, float],
    *,
    positive: bool = True,
) -> float | None:
    for text in [formula, *variables.values()]:
        try:
            parse(text)
        except ExprError as exc:
            out.append(_err("catalog.formula", f"'{text}': {exc}", where))
            return None
    used = set().union(*(names_in(t) for t in [formula, *variables.values()]))
    for name in sorted(used & set(params)):
        if name not in units:
            out.append(_err("catalog.units", f"parameter '{name}' has no declared unit", where))
    try:
        value, _ = evaluate_with_variables(formula, variables, {**extra_env, **params})
    except ExprError as exc:
        out.append(_err("catalog.formula", f"[{label}] {exc}", where))
        return None
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        out.append(_err("catalog.formula", f"[{label}] '{formula}' gives {value}", where))
        return None
    return value


def check_catalog(catalog: Catalog) -> list[Issue]:
    out: list[Issue] = []
    seen: dict[str, str] = {}
    for e in catalog.entries:
        if e.code in seen:
            out.append(_err("catalog.dupe", f"code '{e.code}' used by {seen[e.code]} and {e.kind}"))
        seen[e.code] = e.kind
    procs = catalog.codes("process")
    machines = catalog.codes("machine_class")
    checks = catalog.codes("check_type")
    zones = catalog.codes("zone_kind")
    commodities = catalog.codes("commodity_class")
    archetypes = catalog.codes("archetype")
    materials = catalog.codes("material")
    mtm = catalog.codes("mtm_element")
    needs_check_fields = _version_tuple(catalog.manifest.version) >= (2026, 11, 5)

    for e in catalog.entries:
        where = f"{e.kind}:{e.code}"
        if isinstance(e, ProcessEntry):
            _ref(out, machines, [e.machine_class], "machine_class", where)
            _ref(out, checks, e.quality_checks, "check_type", where)
            _ref(out, commodities, [c.commodity for c in e.consumables], "commodity", where)
            _ref(out, commodities, e.applies_to.commodity, "commodity", where)
            _ref(out, archetypes, e.applies_to.archetype, "archetype", where)
            _ref(out, zones, [e.zone_kind], "zone_kind", where)
            _ref(out, procs, [e.rework.op if e.rework else None], "rework process", where)
            _ordered(out, e.price_inr, where)
            _ref(out, mtm, [m.element for m in e.mtm], "mtm_element", where)
            for c in e.consumables:
                if not is_valid_unit(c.uom):
                    out.append(_err("catalog.units", f"bad uom '{c.uom}'", where))
            mparams = _machine_params(catalog, e.machine_class)
            if e.batch and e.batch.batch_size_param not in mparams:
                out.append(
                    _err(
                        "catalog.ref",
                        f"machine param '{e.batch.batch_size_param}' missing on {e.machine_class}",
                        where,
                    )
                )
            cases = param_sets(catalog, e)
            if not cases:
                out.append(
                    _err(
                        "catalog.formula",
                        "no archetype example or example_params to test the formula",
                        where,
                    )
                )
            env = {**mparams, **e.constants}
            for label, params, units in cases:
                _check_formula(
                    out,
                    where,
                    label,
                    e.time_model.formula,
                    e.time_model.variables,
                    params,
                    units,
                    env,
                )
                if e.batch:
                    _check_formula(out, where, label, e.batch.cycle_formula, {}, params, units, env)
                for c in e.consumables:
                    _check_formula(
                        out, where, label, c.qty_expr, {}, params, units, env, positive=False
                    )
        elif isinstance(e, MachineClassEntry):
            _ordered(out, e.price_inr, where)
        elif isinstance(e, CheckTypeEntry):
            _ref(out, procs, [e.process], "process", where)
            _ref(out, machines, [e.machine_class], "machine_class", where)
            if e.sampling == "aql" and e.default_aql is None:
                out.append(_err("catalog.aql", "AQL sampling needs default_aql", where))
            if needs_check_fields:
                for fname in (
                    "characteristic",
                    "limit",
                    "instrument_class",
                    "frequency",
                    "reaction",
                ):
                    if not (getattr(e, fname) or "").strip():
                        out.append(
                            _err(
                                "catalog.check_fields",
                                f"check type lacks '{fname}' (docs/15 §3)",
                                where,
                            )
                        )
        elif isinstance(e, SamplingPlanEntry):
            out += _check_sampling(e, where)
        elif isinstance(e, PolicyEntry):
            if e.vendors and e.vendors.weights:
                total = sum(e.vendors.weights.values())
                if abs(total - 100) > 1e-6:
                    out.append(
                        _err("catalog.policy", f"vendor weights sum to {total}, not 100", where)
                    )
        elif isinstance(e, ZoneKindEntry):
            _ref(out, zones, [r.target_kind for r in e.rules], "zone_kind", where)
            _ordered(out, e.fitout_inr_per_m2, where)
            if e.min_aspect > e.max_aspect:
                out.append(_err("catalog.range", "min_aspect > max_aspect", where))
        elif isinstance(e, CommodityClassEntry):
            _ref(out, checks, e.iqc_checks, "check_type", where)
            _ref(out, commodities, [e.parent], "commodity parent", where)
            _ordered(out, e.lead_time_days, where)
            _ordered(out, e.unit_price_inr, where)
            _ref(out, procs, e.make_route, "make_route process", where)
            _ref(out, materials, [e.default_material], "material", where)
            if not is_valid_unit(e.uom):
                out.append(_err("catalog.units", f"bad uom '{e.uom}'", where))
            if e.iqc_checks and e.default_aql is None:
                out.append(_warn("catalog.aql", "IQC checks without default_aql", where))
        elif isinstance(e, ArchetypeEntry):
            out += _check_archetype(catalog, e, where)
        elif isinstance(e, MaterialEntry):
            _ordered(out, e.price_inr_per_kg, where)
    return out


def _check_archetype(catalog: Catalog, a: ArchetypeEntry, where: str) -> list[Issue]:
    out: list[Issue] = []
    by_code = catalog.by_code()
    declared = {p.name: p.unit for p in a.parameters}
    for p in a.parameters:
        if not is_valid_unit(p.unit):
            out.append(_err("catalog.units", f"parameter {p.name}: bad unit '{p.unit}'", where))
    for v in a.example_variants:
        for missing in sorted(set(declared) - set(v.params)):
            out.append(_err("catalog.params", f"{v.code} lacks parameter '{missing}'", where))
        for extra in sorted(set(v.params) - set(declared)):
            out.append(_err("catalog.params", f"{v.code} sets undeclared '{extra}'", where))
    refs = {s.ref for s in a.bom_skeleton}
    for s in a.bom_skeleton:
        _ref(out, catalog.codes("commodity_class"), [s.commodity], "commodity", where)
        if s.parent is not None and s.parent not in refs:
            out.append(_err("catalog.ref", f"skeleton {s.ref}: unknown parent '{s.parent}'", where))
        if not is_valid_unit(s.uom):
            out.append(_err("catalog.units", f"skeleton {s.ref}: bad uom '{s.uom}'", where))
        for v in a.example_variants:
            _check_formula(
                out,
                f"{where}.bom_skeleton.{s.ref}",
                v.code,
                s.qty_expr,
                {},
                v.params,
                declared,
                {},
                positive=False,
            )
    skeleton_refs = refs | {"product"}
    for step in a.typical_routing:
        for r in [step.builds, *step.consumes]:
            if r is not None and r not in skeleton_refs:
                out.append(
                    _err(
                        "catalog.ref",
                        f"routing step {step.process}: unknown skeleton ref '{r}'",
                        where,
                    )
                )
    checks = catalog.codes("check_type")
    machines = catalog.codes("machine_class")
    q = a.quality
    used_checks = [
        *(u.check for u in q.patrol),
        *(u.check for u in q.oqc),
        *(u.check for u in q.lab),
    ]
    _ref(out, checks, used_checks, "check_type", where)
    _ref(out, machines, [u.machine_class for u in q.lab], "machine_class", where)
    for step in a.typical_routing:
        proc = by_code.get(step.process)
        if not isinstance(proc, ProcessEntry):
            out.append(
                _err("catalog.ref", f"typical routing: unknown process '{step.process}'", where)
            )
        elif a.code not in proc.applies_to.archetype:
            out.append(
                _err("catalog.ref", f"process '{step.process}' does not apply to {a.code}", where)
            )
    return out


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def _check_sampling(e: SamplingPlanEntry, where: str) -> list[Issue]:
    """The table is complete: every letter has a size and a cell per AQL; letters resolve;
    arrows point at a plan; lot ranges are contiguous."""
    out: list[Issue] = []
    letters = set(e.sample_sizes)
    sizes = [e.sample_sizes[k] for k in sorted(e.sample_sizes)]
    if sizes != sorted(sizes):
        out.append(_err("catalog.sampling", "sample sizes must grow with the code letter", where))
    prev_max = 1
    for i, lr in enumerate(e.lot_ranges):
        if lr.min_lot != prev_max + 1:
            out.append(_err("catalog.sampling", f"lot range {i} is not contiguous", where))
        prev_max = lr.max_lot or prev_max
        if lr.max_lot is None and i != len(e.lot_ranges) - 1:
            out.append(_err("catalog.sampling", "only the last lot range may be open", where))
        for lv in e.levels:
            if lr.letters.get(lv) not in letters:
                out.append(
                    _err("catalog.sampling", f"lot range {i}: no letter for level {lv}", where)
                )
    for letter, row in e.single_normal.items():
        if letter not in letters:
            out.append(_err("catalog.sampling", f"plan row '{letter}' has no sample size", where))
        for aql, cell in row.items():
            if cell not in ("v", "^") and not _is_plan(cell):
                out.append(_err("catalog.sampling", f"{letter}/{aql}: bad cell '{cell}'", where))
        if len(row) != len(e.aqls):
            out.append(_err("catalog.sampling", f"row {letter} has {len(row)} AQL cells", where))
    for letter in letters - set(e.single_normal):
        out.append(_err("catalog.sampling", f"no plan row for letter {letter}", where))
    # every arrow must reach a plan in its column
    keys = list(next(iter(e.single_normal.values())).keys()) if e.single_normal else []
    order = sorted(e.single_normal)
    for aql in keys:
        col = [e.single_normal[ltr].get(aql, "") for ltr in order]
        if not any(_is_plan(c) for c in col):
            out.append(_err("catalog.sampling", f"AQL {aql}: no plan in the column", where))
    return out


def _is_plan(cell: str) -> bool:
    ac, _, re_ = cell.partition("/")
    return ac.isdigit() and re_.isdigit() and int(re_) > int(ac)


def check_pack(pack: Pack, today: date | None = None) -> list[Issue]:
    out: list[Issue] = []
    as_of = pack.manifest.as_of
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, v in enumerate(pack.values):
        by_key[v.key].append(i)
        if v.retrieved_at > (today or date.max):
            out.append(_err("pack.retrieved", f"{v.key}: retrieved_at is in the future"))
    for key, idxs in sorted(by_key.items()):
        vals = [pack.values[i] for i in idxs]
        groups = {v.group for v in vals}
        if len(groups) > 1:
            out.append(_err("pack.group", f"{key}: values in several groups {sorted(groups)}"))
        active = [v for v in vals if v.active_on(as_of)]
        if not active:
            latest = max(vals, key=lambda v: v.effective_from)
            reason = (
                "expired with no newer value"
                if latest.effective_to and latest.effective_to < as_of
                else "no value in force"
            )
            out.append(_err("pack.expired", f"{key}: {reason} on {as_of}"))
        elif len(active) > 1:
            out.append(
                _err(
                    "pack.overlap",
                    f"{key}: {len(active)} values active on {as_of}; mark older ones superseded",
                )
            )
        for v in vals:
            if v.unit and not is_valid_unit(v.unit):
                out.append(_err("pack.units", f"{key}: bad unit '{v.unit}'"))
    if pack.manifest.next_review < as_of:
        out.append(_err("pack.review", "next_review is before as_of"))
    if today and today > pack.manifest.next_review:
        out.append(_warn("pack.review", f"pack review overdue since {pack.manifest.next_review}"))
    return out


def check_cross(catalog: Catalog, pack: Pack) -> list[Issue]:
    """Catalog ↔ pack: archetype compliance keys exist in the pack."""
    out: list[Issue] = []
    keys = set(pack.keys())
    for e in catalog.entries:
        if isinstance(e, ArchetypeEntry):
            for k in e.compliance_pack_keys:
                if k not in keys:
                    out.append(
                        _err(
                            "cross.pack_key",
                            f"archetype {e.code}: pack key {k} missing in {pack.manifest.state}",
                        )
                    )
        elif isinstance(e, CheckTypeEntry):
            for k in e.supports_compliance:
                if k not in keys:
                    out.append(
                        _err(
                            "cross.pack_key",
                            f"check {e.code}: pack key {k} missing in {pack.manifest.state}",
                        )
                    )
    return out


def pack_assumptions(pack: Pack) -> list[Assumption]:
    """``verify: true`` active values, as assumptions-register rows (docs/10 §2.3)."""
    rows: list[Assumption] = []
    for v in pack.values:
        if v.verify and v.active_on(pack.manifest.as_of):
            rows.append(
                Assumption(
                    id=f"pack.{pack.manifest.state}.{v.key}",
                    path=v.key,
                    value=v.value,
                    unit=v.unit,
                    source="pack",
                    confidence=v.confidence,
                    ref=v.source_url,
                    note=f"verify: {v.source_title}" + (f" — {v.note}" if v.note else ""),
                )
            )
    return rows


def formula_value(
    catalog: Catalog, process_code: str, params: dict[str, float]
) -> tuple[float, float | None]:
    """Per-unit seconds (and batch cycle seconds) of a process for given product params.

    Used by the golden fixture check only; engines own this calculation from P3 on.
    """
    proc = catalog.by_code()[process_code]
    assert isinstance(proc, ProcessEntry)
    env = {**_machine_params(catalog, proc.machine_class), **proc.constants, **params}
    per_unit, _ = evaluate_with_variables(proc.time_model.formula, proc.time_model.variables, env)
    batch = evaluate(proc.batch.cycle_formula, env) if proc.batch else None
    return per_unit, batch
