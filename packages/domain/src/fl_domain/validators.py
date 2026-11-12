"""Domain validators (docs/04 §5). Run on every artifact write.

Each validator takes the artifact and a :class:`ValidationContext` holding whatever
upstream artifacts, catalog and pack are available, and returns a list of
:class:`~fl_domain.base.Issue`. Errors block a stage; warnings are shown. Cross-artifact
checks are skipped (not failed) when the upstream artifact is not in the context.

These are checks, not calculations: they compare numbers engines produced against rules.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from fl_domain.artifacts import ARTIFACT_MODELS
from fl_domain.artifacts.bom import Bom
from fl_domain.artifacts.capacity import CapacityDesign
from fl_domain.artifacts.finance import FinanceModel
from fl_domain.artifacts.inventory import InventoryDesign
from fl_domain.artifacts.layout import Layout, Rect
from fl_domain.artifacts.product_spec import ProductSpec
from fl_domain.artifacts.quality import QualityPlan
from fl_domain.artifacts.routing import Routing
from fl_domain.artifacts.utilities import UtilityDesign
from fl_domain.artifacts.workorders import Roster, WorkOrdersShifts
from fl_domain.base import Issue
from fl_domain.common import StageKey
from fl_domain.library.catalog import ArchetypeEntry, Catalog, CommodityClassEntry, ProcessEntry
from fl_domain.library.pack import Pack
from fl_domain.prov import Prov
from fl_domain.units import is_valid_unit

__all__ = [
    "ValidationContext",
    "find_cycle",
    "validate_artifact",
    "validate_bom",
    "validate_capacity",
    "validate_finance",
    "validate_inventory",
    "validate_json",
    "validate_layout",
    "validate_product_spec",
    "validate_quality",
    "validate_roster",
    "validate_routing",
    "validate_utilities",
    "validate_workorders_shifts",
]

MIX_TOL_PCT = 0.01
REL_TOL = 1e-6
ABS_TOL_INR = 1.0
OUTDOOR_ZONE_KINDS = frozenset({"utility_yard"})


@dataclass(frozen=True)
class ValidationContext:
    catalog: Catalog | None = None
    pack: Pack | None = None
    on: date | None = None
    spec: ProductSpec | None = None
    bom: Bom | None = None
    routing: Routing | None = None
    quality: QualityPlan | None = None
    capacity: CapacityDesign | None = None
    inventory: InventoryDesign | None = None
    utilities: UtilityDesign | None = None

    def pack_number(self, key: str) -> float | None:
        if self.pack is None:
            return None
        v = self.pack.active(key, self.on)
        if v is None or isinstance(v.value, bool) or not isinstance(v.value, int | float):
            return None
        return float(v.value)


def _err(code: str, msg: str, path: str = "") -> Issue:
    return Issue(code=code, message=msg, path=path, severity="error")


def _warn(code: str, msg: str, path: str = "") -> Issue:
    return Issue(code=code, message=msg, path=path, severity="warning")


def _dupes(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen and v not in out:
            out.append(v)
        seen.add(v)
    return out


def _walk_prov(obj: Any, path: str = "") -> Iterator[tuple[str, Prov[Any]]]:
    if isinstance(obj, Prov):
        yield path, obj
        return
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from _walk_prov(getattr(obj, name), f"{path}.{name}" if path else name)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_prov(v, f"{path}.{k}")
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj):
            yield from _walk_prov(v, f"{path}[{i}]")


def _check_units(obj: BaseModel) -> list[Issue]:
    return [
        _err("unit.invalid", f"unknown unit '{p.unit}'", path)
        for path, p in _walk_prov(obj)
        if p.unit is not None and not is_valid_unit(p.unit)
    ]


def find_cycle(edges: Iterable[tuple[str, str]]) -> list[str] | None:
    """Return one cycle as a node list, or None if the directed graph is acyclic."""
    graph: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        graph[a].append(b)
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if state.get(m) == 1:
                return [*stack[stack.index(m) :], m]
            if m not in state and (found := visit(m)):
                return found
        stack.pop()
        state[n] = 2
        return None

    for node in list(graph):
        if node not in state and (cycle := visit(node)):
            return cycle
    return None


def _ancestors(edges: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        preds[b].add(a)
    memo: dict[str, set[str]] = {}

    def anc(n: str) -> set[str]:
        if n not in memo:
            memo[n] = set()
            out: set[str] = set()
            for p in preds.get(n, ()):
                out |= {p} | anc(p)
            memo[n] = out
        return memo[n]

    return {n: anc(n) for n in list(preds)}


# ── ProductSpec ───────────────────────────────────────────────────────


def validate_product_spec(spec: ProductSpec, ctx: ValidationContext) -> list[Issue]:
    """Required tier present; variant mix = 100%; units valid."""
    out: list[Issue] = []
    p = spec.product
    if not p.name.strip():
        out.append(_err("spec.required", "product name is required", "product.name"))
    if not p.description.value.strip() and not spec.attachments:
        out.append(
            _err("spec.required", "need a CAD file, photo, BoM file or written description")
        )
    if not spec.site.district.strip():
        out.append(_err("spec.required", "site district is required", "site.district"))
    total = sum(v.mix_pct.value for v in p.variants)
    if abs(total - 100.0) > MIX_TOL_PCT:
        out.append(_err("spec.mix", f"variant mix sums to {total:g}%, not 100%", "product"))
    for code in _dupes(v.code for v in p.variants):
        out.append(_err("spec.variant_dupe", f"duplicate variant code {code}"))
    if spec.site.mode == "existing_shed" and spec.site.shed is None:
        out.append(_err("spec.required", "shed dimensions required for existing_shed", "site"))
    out += _check_units(spec)
    if ctx.catalog is not None:
        arch = ctx.catalog.by_code().get(p.archetype.value)
        if isinstance(arch, ArchetypeEntry):
            declared = {ps.name for ps in arch.parameters}
            for i, v in enumerate(p.variants):
                for missing in sorted(declared - set(v.params)):
                    out.append(
                        _warn(
                            "spec.param_missing",
                            f"archetype parameter '{missing}' not set for {v.code}",
                            f"product.variants[{i}].params",
                        )
                    )
    return out


# ── Bom ───────────────────────────────────────────────────────────────


def validate_bom(bom: Bom, ctx: ValidationContext) -> list[Issue]:
    """Complete MBOM per variant; no cycles; commodity classes exist; make items routable."""
    out: list[Issue] = []
    items = {i.part_no: i for i in bom.items}
    for d in _dupes(i.part_no for i in bom.items):
        out.append(_err("bom.item_dupe", f"duplicate part_no {d}"))
    root = items.get(bom.root)
    if root is None or root.kind != "product":
        out.append(_err("bom.root", f"root '{bom.root}' must be an item of kind 'product'"))
    variants = set(bom.variants)
    for level, lines in (("ebom", bom.ebom), ("mbom", bom.mbom)):
        for i, ln in enumerate(lines):
            path = f"{level}[{i}]"
            if ln.level != level:
                out.append(_err("bom.level", f"line level '{ln.level}' in {level} list", path))
            for ref in (ln.parent, ln.child):
                if ref not in items:
                    out.append(_err("bom.ref", f"unknown part_no '{ref}'", path))
            if ln.qty.value <= 0:
                out.append(_err("bom.qty", f"quantity must be > 0 (got {ln.qty.value})", path))
            if not is_valid_unit(ln.uom):
                out.append(_err("unit.invalid", f"unknown uom '{ln.uom}'", path))
            if ln.variant is not None and ln.variant not in variants:
                out.append(_err("bom.variant", f"orphan variant '{ln.variant}'", path))
        if cycle := find_cycle((ln.parent, ln.child) for ln in lines):
            out.append(_err("bom.cycle", f"{level} has a cycle: {' > '.join(cycle)}"))
    for it in bom.items:
        for v in it.variants:
            if v not in variants:
                out.append(_err("bom.variant", f"item {it.part_no} lists unknown variant {v}"))
    # Completeness per variant: root has children, and every assembly reached has children.
    for v in bom.variants:
        lines = bom.lines_for(v)
        children: dict[str, list[str]] = defaultdict(list)
        for ln in lines:
            children[ln.parent].append(ln.child)
        if not children.get(bom.root):
            out.append(_err("bom.incomplete", f"MBOM for {v} has no lines under the root"))
            continue
        todo, seen = [bom.root], set[str]()
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            it = items.get(n)
            if it is not None and it.kind in ("product", "assembly") and not children.get(n):
                out.append(_err("bom.incomplete", f"assembly {n} has no MBOM lines for {v}"))
            todo.extend(children.get(n, []))
    out += _check_units(bom)
    if ctx.catalog is not None:
        commodity = ctx.catalog.codes("commodity_class")
        for c in bom.commodity_classes:
            if c not in commodity:
                out.append(_err("bom.commodity", f"commodity class '{c}' not in catalog"))
        processes = [e for e in ctx.catalog.entries if isinstance(e, ProcessEntry)]
        for it in bom.items:
            if it.commodity_class and it.commodity_class not in commodity:
                out.append(
                    _err("bom.commodity", f"{it.part_no}: unknown class '{it.commodity_class}'")
                )
            if it.make_or_buy.value == "make" and it.kind == "part":
                has_process = any(
                    it.commodity_class in p.applies_to.commodity for p in processes
                )
                if not has_process and not it.uncatalogued:
                    out.append(
                        _err(
                            "bom.make_uncatalogued",
                            f"make item {it.part_no} has no catalog process; "
                            "flag it 'uncatalogued'",
                        )
                    )
                elif it.uncatalogued:
                    out.append(_warn("bom.uncatalogued", f"{it.part_no} is uncatalogued"))
    for d in bom.make_buy_decisions:
        if d.part_no not in items:
            out.append(_err("bom.ref", f"make/buy decision for unknown part {d.part_no}"))
    return out


# ── Routing ───────────────────────────────────────────────────────────


def validate_routing(r: Routing, ctx: ValidationContext) -> list[Issue]:
    """Acyclic precedence; every MBOM line consumed; workcenter class and time method set."""
    out: list[Issue] = []
    ops = {o.op_no: o for o in r.operations}
    for d in _dupes(o.op_no for o in r.operations):
        out.append(_err("routing.op_dupe", f"duplicate op_no {d}"))
    edges = [(p.from_op, p.to_op) for p in r.precedence]
    for a, b in edges:
        for ref in (a, b):
            if ref not in ops:
                out.append(_err("routing.ref", f"precedence references unknown op {ref}"))
    if cycle := find_cycle(edges):
        out.append(_err("routing.cycle", f"precedence graph has a cycle: {' > '.join(cycle)}"))
    variants = set(r.variants)
    catalog = ctx.catalog.by_code() if ctx.catalog else None
    machine_classes = ctx.catalog.codes("machine_class") if ctx.catalog else set()
    check_codes = ctx.catalog.codes("check_type") if ctx.catalog else set()
    for i, o in enumerate(r.operations):
        path = f"operations[{i}]"
        if not o.workcenter_class.strip():
            out.append(_err("routing.workcenter", f"{o.op_no} has no workcenter class", path))
        for t_path, t in [("time", o.time), *((f"variant_times.{k}", v) for k, v in
                                              o.variant_times.items())]:
            if t.method == "llm_estimate":
                if t.cycle_s.confidence >= 0.5:
                    out.append(
                        _err("routing.llm_conf", "LLM estimates must have confidence < 0.5",
                             f"{path}.{t_path}")
                    )
                out.append(_warn("routing.llm_estimate", f"{o.op_no} time is an LLM estimate",
                                 f"{path}.{t_path}"))
            if t.cycle_s.value <= 0:
                out.append(_err("routing.time", "cycle time must be > 0", f"{path}.{t_path}"))
        for v in [*o.variant_times, *o.variants]:
            if v not in variants:
                out.append(_err("routing.variant", f"{o.op_no}: unknown variant {v}", path))
        if catalog is not None:
            proc = catalog.get(o.process.code)
            if not isinstance(proc, ProcessEntry):
                out.append(_err("routing.process", f"{o.op_no}: '{o.process.code}' is not a "
                                                   "catalog process", path))
            if o.workcenter_class != "manual" and o.workcenter_class not in machine_classes:
                out.append(_err("routing.workcenter", f"{o.op_no}: unknown machine class "
                                                      f"'{o.workcenter_class}'", path))
            if o.quality_gate and o.quality_gate not in check_codes:
                out.append(_err("routing.check", f"{o.op_no}: unknown check '{o.quality_gate}'",
                                path))
    out += _check_units(r)
    if ctx.bom is not None:
        consumed = {c for o in r.operations for c in o.consumes}
        for i, ln in enumerate(ctx.bom.mbom):
            if ln.child not in consumed:
                out.append(_err("routing.unconsumed",
                                f"MBOM line {ln.parent}>{ln.child} is not consumed by any op",
                                f"bom.mbom[{i}]"))
        items = {it.part_no: it for it in ctx.bom.items}
        producers: dict[str, list[str]] = defaultdict(list)
        for o in r.operations:
            producers[o.produces].append(o.op_no)
        anc = _ancestors(edges)
        for o in r.operations:
            for c in o.consumes:
                made_by = [p for p in producers.get(c, []) if p != o.op_no]
                if made_by:
                    if not any(p in anc.get(o.op_no, set()) for p in made_by):
                        out.append(_err("routing.order", f"{o.op_no} consumes {c} before an op "
                                                         "that produces it"))
                else:
                    it = items.get(c)
                    if it is None:
                        out.append(_err("routing.ref", f"{o.op_no} consumes unknown item {c}"))
                    elif it.make_or_buy.value != "buy" and it.kind in ("assembly", "part"):
                        out.append(_err("routing.order", f"{o.op_no} consumes make item {c} "
                                                         "that no op produces"))
    return out


# ── QualityPlan ───────────────────────────────────────────────────────


def validate_quality(q: QualityPlan, ctx: ValidationContext) -> list[Issue]:
    """IQC items exist in MBOM; inline checks map to ops; AQL plans valid for lot sizes."""
    out: list[Issue] = []
    plans = [(f"iqc[{i}].plan", x.plan) for i, x in enumerate(q.iqc)]
    plans += [(f"oqc[{i}].plan", x.plan) for i, x in enumerate(q.oqc) if x.plan]
    for path, p in plans:
        if p.sample_size > p.lot_size:
            out.append(_err("quality.aql", f"sample {p.sample_size} > lot {p.lot_size}", path))
        if p.reject != p.accept + 1:
            out.append(_err("quality.aql", "single sampling needs Re = Ac + 1", path))
    if ctx.bom is not None:
        mbom_children = {ln.child for ln in ctx.bom.mbom}
        for i, x in enumerate(q.iqc):
            if x.part_no not in mbom_children:
                out.append(_err("quality.iqc_ref", f"IQC part {x.part_no} not in MBOM",
                                f"iqc[{i}]"))
    if ctx.routing is not None:
        op_nos = {o.op_no for o in ctx.routing.operations}
        for name, checks in (("inline", q.inline), ("eol", q.eol)):
            for i, c in enumerate(checks):
                if c.op_no not in op_nos:
                    out.append(_err("quality.op_ref", f"{c.check_type} maps to unknown op "
                                                      f"{c.op_no}", f"{name}[{i}]"))
        for i, rw in enumerate(q.rework):
            if rw.op_no not in op_nos:
                out.append(_err("quality.op_ref", f"rework for unknown op {rw.op_no}",
                                f"rework[{i}]"))
    if ctx.catalog is not None:
        checks_known = ctx.catalog.codes("check_type")
        used = [c for x in q.iqc for c in x.check_types]
        used += [c.check_type for c in [*q.inline, *q.eol]]
        used += [c.check_type for c in [*q.patrol, *q.oqc]]
        for c in sorted(set(used) - checks_known):
            out.append(_err("quality.check", f"unknown check type '{c}'"))
    if ctx.pack is not None:
        for i, link in enumerate(q.compliance):
            if not ctx.pack.history(link.pack_key):
                out.append(_err("quality.compliance", f"pack key {link.pack_key} not in pack",
                                f"compliance[{i}]"))
    out += _check_units(q)
    return out


# ── CapacityDesign ────────────────────────────────────────────────────


def validate_capacity(cd: CapacityDesign, ctx: ValidationContext) -> list[Issue]:
    """Each op assigned once per strategy line; loads within PCT × (1 + overload);
    precedence respected along each line."""
    out: list[Issue] = []
    keys = [s.key for s in cd.strategies]
    for d in _dupes(keys):
        out.append(_err("capacity.strategy_dupe", f"strategy {d} appears twice"))
    if cd.chosen is not None and cd.chosen not in keys:
        out.append(_err("capacity.chosen", f"chosen strategy {cd.chosen} not computed"))
    pct = cd.takt.pct_s.value
    for s in cd.strategies:
        sp = f"strategies[{s.key}]"
        for d in _dupes(st.id for st in s.stations):
            out.append(_err("capacity.station_dupe", f"duplicate station {d}", sp))
        lines: dict[str | None, list[Any]] = defaultdict(list)
        for st in s.stations:
            lines[st.line].append(st)
            limit = s.pct_s_by_line.get(st.line or "", pct) * (1 + s.allowed_overload)
            for v, load in st.load_s_by_variant.items():
                if load / st.parallel_positions > limit * (1 + REL_TOL):
                    out.append(_err("capacity.overload",
                                    f"station {st.id} load {load:g} s for {v} exceeds "
                                    f"{limit:g} s", sp))
        if ctx.routing is None:
            continue
        r = ctx.routing
        for line, stations in lines.items():
            line_vars = set(s.line_variants.get(line or "", r.variants))
            expected = {o.op_no for o in r.operations
                        if not o.variants or line_vars & set(o.variants)}
            assigned = [op for st in stations for op in st.ops]
            for d in _dupes(assigned):
                out.append(_err("capacity.assign", f"op {d} assigned twice on line {line}", sp))
            for missing in sorted(expected - set(assigned)):
                out.append(_err("capacity.assign", f"op {missing} not assigned on line {line}",
                                sp))
            for extra in sorted(set(assigned) - {o.op_no for o in r.operations}):
                out.append(_err("capacity.assign", f"unknown op {extra}", sp))
            seq = {op: st.sequence for st in stations for op in st.ops}
            for p in r.precedence:
                if p.from_op in seq and p.to_op in seq and seq[p.from_op] > seq[p.to_op]:
                    out.append(_err("capacity.precedence",
                                    f"{p.from_op} is after {p.to_op} on line {line}", sp))
    out += _check_units(cd)
    return out


# ── InventoryDesign ───────────────────────────────────────────────────


def validate_inventory(inv: InventoryDesign, ctx: ValidationContext) -> list[Issue]:
    """Every bought MBOM item has a policy; zones valid; hazard items in hazard zones."""
    out: list[Issue] = []
    zones = {z.id: z for z in inv.zones}
    for d in _dupes(z.id for z in inv.zones):
        out.append(_err("inventory.zone_dupe", f"duplicate zone {d}"))
    for i, pol in enumerate(inv.policies):
        if pol.zone not in zones:
            out.append(_err("inventory.zone_ref", f"{pol.part_no} stored in unknown zone "
                                                  f"{pol.zone}", f"policies[{i}]"))
    for i, f in enumerate(inv.flow_matrix):
        for z in (f.from_zone, f.to_zone):
            if z not in zones:
                out.append(_err("inventory.zone_ref", f"flow references unknown zone {z}",
                                f"flow_matrix[{i}]"))
    if ctx.bom is not None:
        policy_parts = {p.part_no for p in inv.policies}
        children = {ln.child for ln in ctx.bom.mbom}
        for it in ctx.bom.items:
            if it.part_no in children and it.make_or_buy.value == "buy" and \
                    it.part_no not in policy_parts:
                out.append(_err("inventory.policy", f"no stock policy for {it.part_no}"))
        if ctx.catalog is not None:
            classes = {
                e.code: e for e in ctx.catalog.entries if isinstance(e, CommodityClassEntry)
            }
            kinds = ctx.catalog.by_code()
            items = {it.part_no: it for it in ctx.bom.items}
            for pol in inv.policies:
                it = items.get(pol.part_no)
                cls = classes.get(it.commodity_class or "") if it else None
                zone = zones.get(pol.zone)
                if cls is None or zone is None or not cls.hazards:
                    continue
                zk = kinds.get(zone.kind)
                zone_hazards = set(zone.hazards) | set(getattr(zk, "hazards", []))
                for h in cls.hazards:
                    if h not in zone_hazards:
                        out.append(_err("inventory.hazard", f"{pol.part_no} ({h}) stored in "
                                                            f"non-{h} zone {zone.id}"))
    if ctx.catalog is not None:
        zone_kinds = ctx.catalog.codes("zone_kind")
        for z in inv.zones:
            if z.kind not in zone_kinds:
                out.append(_err("inventory.zone_kind", f"zone {z.id}: unknown kind {z.kind}"))
    out += _check_units(inv)
    return out


# ── Roster / work orders ──────────────────────────────────────────────


def validate_roster(roster: Roster, ctx: ValidationContext) -> list[Issue]:
    """Hours within pack limits; every station staffed every shift."""
    out: list[Issue] = []
    daily = ctx.pack_number("labour.daily_hours_max")
    weekly = ctx.pack_number("labour.weekly_hours_max")
    spread = ctx.pack_number("labour.spread_over_max_h")
    ot_cap = ctx.pack_number("labour.overtime_cap_quarter_h")
    rest = ctx.pack_number("labour.weekly_rest_days")
    if ctx.pack is not None and None in (daily, weekly):
        out.append(_err("roster.pack", "pack lacks active daily/weekly hour limits"))
    for i, sh in enumerate(roster.shifts):
        path = f"shifts[{i}]"
        if daily is not None and sh.paid_hours > daily:
            out.append(_err("roster.daily_hours",
                            f"{sh.id}: {sh.paid_hours:g} h/day exceeds {daily:g}", path))
        if weekly is not None and sh.paid_hours * sh.days_per_week > weekly:
            out.append(_err("roster.weekly_hours",
                            f"{sh.id}: {sh.paid_hours * sh.days_per_week:g} h/week exceeds "
                            f"{weekly:g}", path))
        if spread is not None and sh.spread_over_h > spread:
            out.append(_err("roster.spread_over",
                            f"{sh.id}: spread-over {sh.spread_over_h:g} h exceeds {spread:g}",
                            path))
        if rest is not None and sh.days_per_week > 7 - rest:
            out.append(_err("roster.weekly_rest", f"{sh.id}: no weekly rest day", path))
    if ot_cap is not None and roster.overtime_h_per_quarter > ot_cap:
        out.append(_err("roster.overtime", f"overtime {roster.overtime_h_per_quarter:g} h/quarter "
                                           f"exceeds {ot_cap:g}"))
    for i, c in enumerate(roster.compliance_report):
        if not c.ok:
            out.append(_err("roster.compliance", c.message or f"{c.rule_key} violated",
                            f"compliance_report[{i}]"))
    strat = ctx.capacity.chosen_strategy() if ctx.capacity else None
    if strat is not None:
        staffed = {(s.station, s.shift): s.operators for s in roster.station_staffing}
        for sh in roster.shifts:
            for st in strat.stations:
                if st.operators > 0 and staffed.get((st.id, sh.id), 0) < st.operators:
                    out.append(_err("roster.unstaffed",
                                    f"station {st.id} understaffed in shift {sh.id}"))
    out += _check_units(roster)
    return out


def validate_workorders_shifts(w: WorkOrdersShifts, ctx: ValidationContext) -> list[Issue]:
    out = validate_roster(w.roster, ctx)
    rel = w.workorders.release
    if ctx.capacity is not None and ctx.capacity.chosen and rel.strategy != ctx.capacity.chosen:
        out.append(_err("workorders.strategy",
                        f"release logic for {rel.strategy}, chosen is {ctx.capacity.chosen}"))
    if ctx.routing is not None:
        variants = set(ctx.routing.variants)
        for v in [*rel.sequence, *rel.daily_qty_by_variant, *rel.batch_size_by_variant]:
            if v not in variants:
                out.append(_err("workorders.variant", f"unknown variant {v} in release logic"))
    return out


# ── UtilityDesign ─────────────────────────────────────────────────────


def validate_utilities(u: UtilityDesign, ctx: ValidationContext) -> list[Issue]:
    """Transformer ≥ MD × headroom; every equipment load counted exactly once."""
    out: list[Issue] = []
    e = u.electrical
    if e.supply == "HT":
        need = e.max_demand_kva.value * e.transformer_headroom
        if e.transformer_kva is None:
            out.append(_err("utilities.transformer", "HT supply needs a transformer"))
        elif e.transformer_kva.value + REL_TOL < need:
            out.append(_err("utilities.transformer",
                            f"transformer {e.transformer_kva.value:g} kVA < MD × headroom "
                            f"{need:g} kVA"))
    for d in _dupes(ld.ref for ld in e.loads):
        out.append(_err("utilities.double_count", f"load {d} counted more than once"))
    connected = sum(ld.qty * ld.kw_each for ld in e.loads)
    if abs(connected - e.connected_kw.value) > max(0.01, REL_TOL * connected):
        out.append(_err("utilities.reconcile",
                        f"connected kW {e.connected_kw.value:g} ≠ Σ loads {connected:g}"))
    if ctx.capacity is not None:
        refs = {ld.ref: ld for ld in e.loads}
        for eq in ctx.capacity.equipment_list:
            ref = f"capacity:equipment:{eq.machine_class}"
            ld = refs.get(ref)
            if ld is None:
                out.append(_err("utilities.missing_load", f"equipment {eq.machine_class} "
                                                          "not counted"))
            elif ld.qty != eq.qty:
                out.append(_err("utilities.missing_load",
                                f"{eq.machine_class}: {ld.qty} counted, {eq.qty} installed"))
    out += _check_units(u)
    return out


# ── Layout ────────────────────────────────────────────────────────────


def _inside(inner: Rect, outer: Rect) -> bool:
    eps = 1e-6
    return (inner.x >= outer.x - eps and inner.y >= outer.y - eps
            and inner.x + inner.w <= outer.x + outer.w + eps
            and inner.y + inner.h <= outer.y + outer.h + eps)


def _overlap(a: Rect, b: Rect) -> bool:
    eps = 1e-6
    return (a.x < b.x + b.w - eps and b.x < a.x + a.w - eps
            and a.y < b.y + b.h - eps and b.y < a.y + a.h - eps)


def _gap(a: Rect, b: Rect) -> float:
    dx = max(b.x - (a.x + a.w), a.x - (b.x + b.w), 0.0)
    dy = max(b.y - (a.y + a.h), a.y - (b.y + b.h), 0.0)
    return float((dx * dx + dy * dy) ** 0.5)


def validate_layout(lay: Layout, ctx: ValidationContext) -> list[Issue]:
    """No overlaps; inside boundary; aisles ≥ minimum; keep-apart; exit travel distance."""
    out: list[Issue] = []
    if not _inside(lay.building, lay.site):
        out.append(_err("layout.boundary", "building is outside the site"))
    zones = {z.zone: z for z in lay.zones}
    for d in _dupes(z.zone for z in lay.zones):
        out.append(_err("layout.zone_dupe", f"zone {d} placed twice"))
    for z in lay.zones:
        outer = lay.site if z.kind in OUTDOOR_ZONE_KINDS else lay.building
        if not _inside(z.rect, outer):
            out.append(_err("layout.boundary", f"zone {z.zone} outside its boundary"))
    placed = list(lay.zones)
    for i, a in enumerate(placed):
        for b in placed[i + 1 :]:
            if _overlap(a.rect, b.rect):
                out.append(_err("layout.overlap", f"zones {a.zone} and {b.zone} overlap"))
    for ai in lay.aisles:
        if ai.width_m + REL_TOL < lay.rules.min_aisle_m:
            out.append(_err("layout.aisle", f"aisle {ai.id} {ai.width_m:g} m < minimum "
                                            f"{lay.rules.min_aisle_m:g} m"))
        for z in lay.zones:
            if _overlap(ai.rect, z.rect):
                out.append(_err("layout.overlap", f"aisle {ai.id} runs through zone {z.zone}"))
    for ka in lay.rules.keep_apart:
        a, b = zones.get(ka.zone_a), zones.get(ka.zone_b)
        if a is None or b is None:
            continue
        if (g := _gap(a.rect, b.rect)) + REL_TOL < ka.min_distance_m:
            out.append(_err("layout.keep_apart", f"{ka.zone_a}–{ka.zone_b} {g:.1f} m < "
                                                 f"{ka.min_distance_m:g} m"))
    indoor = [z for z in lay.zones if z.kind not in OUTDOOR_ZONE_KINDS]
    if indoor and not lay.exits:
        out.append(_err("layout.exits", "building has no exits"))
    for z in indoor if lay.exits else []:
        r = z.rect
        corners = [(r.x, r.y), (r.x + r.w, r.y), (r.x, r.y + r.h), (r.x + r.w, r.y + r.h)]
        worst = max(min(abs(cx - ex.x) + abs(cy - ex.y) for ex in lay.exits)
                    for cx, cy in corners)
        if worst > lay.rules.max_travel_m + REL_TOL:
            out.append(_err("layout.travel", f"zone {z.zone}: travel {worst:.1f} m > "
                                             f"{lay.rules.max_travel_m:g} m"))
    for v in lay.metrics.violations:
        out.append(_err("layout.violation", v))
    return out


# ── FinanceModel ──────────────────────────────────────────────────────


def validate_finance(fm: FinanceModel, ctx: ValidationContext) -> list[Issue]:
    """Totals reconcile with their lines and with source artifacts."""
    out: list[Issue] = []
    if fm.scenario("base") is None:
        out.append(_err("finance.base", "base scenario missing"))
    for s in fm.scenarios:
        sp = f"scenarios[{s.scenario}]"
        cap = sum(ln.amount_inr for ln in s.capex)
        if abs(cap - s.capex_total_inr) > ABS_TOL_INR:
            out.append(_err("finance.reconcile", f"capex total {s.capex_total_inr:,.0f} ≠ Σ lines "
                                                 f"{cap:,.0f}", sp))
        opx = sum(ln.annual_inr for ln in s.opex)
        if abs(opx - s.opex_total_inr) > ABS_TOL_INR:
            out.append(_err("finance.reconcile", f"opex total {s.opex_total_inr:,.0f} ≠ Σ lines "
                                                 f"{opx:,.0f}", sp))
        if ctx.capacity is not None:
            installed = {eq.machine_class: eq.qty for eq in ctx.capacity.equipment_list}
            costed: dict[str, float] = defaultdict(float)
            for ln in s.capex:
                if ln.source_ref.startswith("capacity:equipment:"):
                    costed[ln.source_ref.rsplit(":", 1)[1]] += ln.qty
            for cls, qty in installed.items():
                if costed.get(cls, 0) != qty:
                    out.append(_err("finance.reconcile", f"{cls}: {costed.get(cls, 0):g} costed, "
                                                         f"{qty} installed", sp))
            for cls in sorted(set(costed) - set(installed)):
                out.append(_err("finance.reconcile", f"{cls} costed but not installed", sp))
    lo, base, hi = fm.scenario("low"), fm.scenario("base"), fm.scenario("high")
    if lo and base and hi and not (lo.capex_total_inr <= base.capex_total_inr
                                   <= hi.capex_total_inr):
        out.append(_warn("finance.range", "capex is not ordered low ≤ base ≤ high"))
    return out


# ── dispatch ──────────────────────────────────────────────────────────

_VALIDATORS: dict[StageKey, Callable[[Any, ValidationContext], list[Issue]]] = {
    "intake": validate_product_spec,
    "bom": validate_bom,
    "routing": validate_routing,
    "quality": validate_quality,
    "capacity": validate_capacity,
    "inventory": validate_inventory,
    "workorders_shifts": validate_workorders_shifts,
    "utilities": validate_utilities,
    "layout": validate_layout,
    "finance": validate_finance,
}


def validate_artifact(stage: StageKey, artifact: BaseModel, ctx: ValidationContext) -> list[Issue]:
    """Run the stage's domain validator (schema validation already happened on parse)."""
    model = ARTIFACT_MODELS[stage]
    if not isinstance(artifact, model):
        return [_err("schema.type", f"expected {model.__name__}, got {type(artifact).__name__}")]
    fn = _VALIDATORS.get(stage)
    return fn(artifact, ctx) if fn else []


def validate_json(
    stage: StageKey, data: dict[str, Any], ctx: ValidationContext
) -> tuple[BaseModel | None, list[Issue]]:
    """Parse ``data`` with the stage model, then run the domain validator.

    Returns the parsed artifact (or None) and all issues, with schema errors given exact
    paths so the agent's repair step can fix them (docs/02 §3 step 5).
    """
    model = ARTIFACT_MODELS[stage]
    try:
        artifact = model.model_validate(data)
    except ValidationError as exc:
        return None, [
            _err("schema", cast(str, e["msg"]), ".".join(str(p) for p in e["loc"]))
            for e in exc.errors()
        ]
    return artifact, validate_artifact(stage, artifact, ctx)
