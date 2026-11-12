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
    ProcessEntry,
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
            for c in e.consumables:
                if not is_valid_unit(c.uom):
                    out.append(_err("catalog.units", f"bad uom '{c.uom}'", where))
            mparams = _machine_params(catalog, e.machine_class)
            if e.batch and e.batch.batch_size_param not in mparams:
                out.append(_err("catalog.ref", f"machine param '{e.batch.batch_size_param}' "
                                               f"missing on {e.machine_class}", where))
            cases = param_sets(catalog, e)
            if not cases:
                out.append(_err("catalog.formula", "no archetype example or example_params to "
                                                   "test the formula", where))
            env = {**mparams, **e.constants}
            for label, params, units in cases:
                _check_formula(out, where, label, e.time_model.formula, e.time_model.variables,
                               params, units, env)
                if e.batch:
                    _check_formula(out, where, label, e.batch.cycle_formula, {}, params, units,
                                   env)
                for c in e.consumables:
                    _check_formula(out, where, label, c.qty_expr, {}, params, units, env,
                                   positive=False)
        elif isinstance(e, MachineClassEntry):
            _ordered(out, e.price_inr, where)
        elif isinstance(e, CheckTypeEntry):
            _ref(out, procs, [e.process], "process", where)
            _ref(out, machines, [e.machine_class], "machine_class", where)
            if e.sampling == "aql" and e.default_aql is None:
                out.append(_err("catalog.aql", "AQL sampling needs default_aql", where))
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
            if not is_valid_unit(e.uom):
                out.append(_err("catalog.units", f"bad uom '{e.uom}'", where))
            if e.iqc_checks and e.default_aql is None:
                out.append(_warn("catalog.aql", "IQC checks without default_aql", where))
        elif isinstance(e, ArchetypeEntry):
            out += _check_archetype(catalog, e, where)
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
            out.append(_err("catalog.ref", f"skeleton {s.ref}: unknown parent '{s.parent}'",
                            where))
        if not is_valid_unit(s.uom):
            out.append(_err("catalog.units", f"skeleton {s.ref}: bad uom '{s.uom}'", where))
        for v in a.example_variants:
            _check_formula(out, f"{where}.bom_skeleton.{s.ref}", v.code, s.qty_expr, {},
                           v.params, declared, {}, positive=False)
    for step in a.typical_routing:
        proc = by_code.get(step.process)
        if not isinstance(proc, ProcessEntry):
            out.append(_err("catalog.ref", f"typical routing: unknown process '{step.process}'",
                            where))
        elif a.code not in proc.applies_to.archetype:
            out.append(_err("catalog.ref", f"process '{step.process}' does not apply to "
                                           f"{a.code}", where))
    return out


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
            out.append(_err("pack.overlap", f"{key}: {len(active)} values active on {as_of}; "
                                            "mark older ones superseded"))
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
                    out.append(_err("cross.pack_key", f"archetype {e.code}: pack key {k} "
                                                      f"missing in {pack.manifest.state}"))
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
