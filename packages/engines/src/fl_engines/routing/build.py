"""S3 routing engine (docs/05 §1, ADR-028). Deterministic: no LLM imports, numbers from data.

``build_routing`` turns the approved BoM, the product parameters, the archetype's typical
routing and (for gaps) a model proposal into a ``Routing``:

1. steps: the archetype's ``typical_routing`` resolved to BoM part numbers through the
   skeleton refs in ``builds``/``consumes``, plus proposal steps for assemblies no step builds;
2. time standards in priority order (docs/05 §1.2): user value, catalog formula (safe
   evaluator), MTM elements, model estimate (confidence < 0.5, always flagged);
3. precedence from the data flow: operations on the same item run in step order, and an
   operation that consumes an item waits for the last operation that produces it;
4. every MBOM line is consumed by an operation; lines no step names go to the first
   operation of their parent assembly, with a finding saying so.

Anything the engine cannot settle becomes a ``RoutingFinding``, never a guessed number.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise

from fl_domain.artifacts.bom import Bom
from fl_domain.artifacts.routing import (
    BatchSpec,
    Operation,
    Precedence,
    Routing,
    RoutingFinding,
    RoutingTotals,
    TaskType,
    TimeMethod,
    TimeStandard,
)
from fl_domain.common import CatalogRef
from fl_domain.library.catalog import (
    ArchetypeEntry,
    Catalog,
    MachineClassEntry,
    MtmElementEntry,
    ProcessEntry,
)
from fl_domain.prov import Prov, Source
from fl_engines.bom import ClassRules, assembly_id, leaf_part_no
from fl_engines.expr import ExprError, evaluate, evaluate_with_variables

__all__ = [
    "BuildInput",
    "Decisions",
    "Gap",
    "Proposal",
    "ProposedStep",
    "build_routing",
    "routing_gaps",
]

LLM_CONFIDENCE = 0.3  # model estimates: always below 0.5 (docs/05 §1.2)
OP_STEP = 10


# ── inputs ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProposedStep:
    """A step the model proposes for a gap; part numbers are BoM part numbers."""

    process: str
    builds: str
    consumes: tuple[str, ...] = ()
    sample_pct: float = 100.0
    reason: str = ""


@dataclass
class Proposal:
    proposer: str = "catalog typical routing"
    steps: list[ProposedStep] = field(default_factory=list)
    estimates: dict[str, float] = field(default_factory=dict)  # process code → s per unit
    reasons: dict[str, str] = field(default_factory=dict)  # process code → why this time


@dataclass
class Decisions:
    time: dict[str, dict[str, float]] = field(default_factory=dict)  # op_key → variant|* → s
    drop: set[str] = field(default_factory=set)  # op_key
    answered: set[str] = field(default_factory=set)  # finding ids the user acknowledged


@dataclass
class BuildInput:
    catalog: Catalog
    archetype: ArchetypeEntry
    bom: Bom
    variants: list[str]
    params: dict[str, dict[str, float]]  # variant → S1 parameters
    mix: dict[str, float]  # variant → share of units (sums to 1)
    proposal: Proposal = field(default_factory=Proposal)
    decisions: Decisions = field(default_factory=Decisions)
    catalog_version: str = ""


@dataclass(frozen=True)
class Gap:
    """A make assembly no operation builds yet: the model is asked about it."""

    part_no: str
    name: str
    child_classes: tuple[str, ...]


@dataclass
class _Step:
    process: ProcessEntry
    item: str
    consumes: list[str]
    sample_pct: float
    note: str | None
    origin: str  # catalog | proposal
    reason: str = ""


# ── BoM structure ─────────────────────────────────────────────────────


class _Structure:
    """Maps skeleton refs to the BoM's part numbers."""

    def __init__(self, cat: Catalog, arch: ArchetypeEntry, bom: Bom) -> None:
        self.bom = bom
        self.items = {i.part_no: i for i in bom.items}
        rules = ClassRules(cat, arch)
        self.asm_refs = {s.ref for s in arch.bom_skeleton if s.kind == "assembly"}
        self.leaf_ref: dict[str, str] = {}
        direct = {leaf_part_no(s.ref): s.ref for s in arch.bom_skeleton if s.kind != "assembly"}
        for it in bom.items:
            if it.kind in ("assembly", "product"):
                continue
            if it.part_no in direct:
                self.leaf_ref[it.part_no] = direct[it.part_no]
            elif it.commodity_class and (
                ref := rules.skeleton_line_for(it.commodity_class, it.name)
            ):
                self.leaf_ref[it.part_no] = ref

    def resolve(self, ref: str) -> list[str]:
        if ref == "product":
            return [self.bom.root]
        if ref in self.asm_refs:
            pn = assembly_id(ref)
            return [pn] if pn in self.items else []
        return [pn for pn in self.items if self.leaf_ref.get(pn) == ref]

    def bought_assembly(self, pn: str) -> bool:
        it = self.items.get(pn)
        return it is not None and it.kind == "assembly" and it.make_or_buy.value == "buy"

    def name(self, pn: str) -> str:
        it = self.items.get(pn)
        return it.name if it else pn


def _processes(cat: Catalog) -> dict[str, ProcessEntry]:
    return {e.code: e for e in cat.entries if isinstance(e, ProcessEntry)}


# ── gaps ──────────────────────────────────────────────────────────────


def routing_gaps(inp: BuildInput) -> list[Gap]:
    """Make assemblies that no catalog step builds (before any proposal)."""
    st = _Structure(inp.catalog, inp.archetype, inp.bom)
    steps, _ = _catalog_steps(inp, st)
    built = {s.item for s in steps}
    out: list[Gap] = []
    for it in inp.bom.items:
        if it.kind != "assembly" or st.bought_assembly(it.part_no) or it.part_no in built:
            continue
        classes = tuple(
            sorted(
                {
                    c
                    for ln in inp.bom.mbom
                    if ln.parent == it.part_no
                    and (child := inp.bom.item(ln.child)) is not None
                    and (c := child.commodity_class)
                }
            )
        )
        out.append(Gap(it.part_no, it.name, classes))
    root = inp.bom.root
    if root not in built and not any(g.part_no == root for g in out):
        out.append(Gap(root, "Final assembly of the product", ()))
    return out


# ── steps ─────────────────────────────────────────────────────────────


def _catalog_steps(inp: BuildInput, st: _Structure) -> tuple[list[_Step], list[RoutingFinding]]:
    procs = _processes(inp.catalog)
    steps: list[_Step] = []
    findings: list[RoutingFinding] = []
    last_item: str | None = None
    for i, rs in enumerate(inp.archetype.typical_routing):
        proc = procs.get(rs.process)
        if proc is None:
            continue
        item: str | None
        if rs.builds is not None:
            targets = st.resolve(rs.builds)
            item = targets[0] if targets else None
        else:
            item = last_item
        if item is None or st.bought_assembly(item):
            why = (
                f"the BoM has no item for '{rs.builds}'"
                if item is None
                else f"{item} is bought, not made"
            )
            findings.append(
                RoutingFinding(
                    id=f"F-SKIP-{i + 1:02d}",
                    kind="step_skipped",
                    text=f"Step '{proc.name}' is not in the routing: {why}.",
                    status="info",
                )
            )
            continue
        last_item = item
        consumes: list[str] = []
        for ref in rs.consumes:
            consumes += [pn for pn in st.resolve(ref) if pn not in consumes]
        steps.append(_Step(proc, item, consumes, rs.sample_pct, rs.note, "catalog"))
    return steps, findings


def _proposal_steps(
    inp: BuildInput, st: _Structure, procs: dict[str, ProcessEntry]
) -> tuple[list[_Step], list[RoutingFinding]]:
    steps: list[_Step] = []
    findings: list[RoutingFinding] = []
    for i, ps in enumerate(inp.proposal.steps):
        proc = procs.get(ps.process)
        if proc is None or ps.builds not in st.items:
            findings.append(
                RoutingFinding(
                    id=f"F-PROP-{i + 1:02d}",
                    kind="step_skipped",
                    text=f"A proposed step '{ps.process}' on '{ps.builds}' was dropped: "
                    "not a catalog process or not a BoM item.",
                    status="info",
                )
            )
            continue
        consumes = [c for c in ps.consumes if c in st.items]
        steps.append(_Step(proc, ps.builds, consumes, ps.sample_pct, None, "proposal", ps.reason))
    return steps, findings


# ── time standards ────────────────────────────────────────────────────


def _machine(cat: Catalog, code: str | None) -> MachineClassEntry | None:
    m = cat.by_code().get(code or "")
    return m if isinstance(m, MachineClassEntry) else None


def _formula_time(
    proc: ProcessEntry, mc: MachineClassEntry | None, params: dict[str, float]
) -> float | None:
    env = {**(mc.params if mc else {}), **proc.constants, **params}
    try:
        value, _ = evaluate_with_variables(proc.time_model.formula, proc.time_model.variables, env)
    except (ExprError, ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _mtm_time(cat: Catalog, proc: ProcessEntry) -> float | None:
    if not proc.mtm:
        return None
    by = cat.by_code()
    total = 0.0
    for use in proc.mtm:
        el = by.get(use.element)
        if not isinstance(el, MtmElementEntry):
            return None
        total += use.count * el.time_s
    return total if total > 0 else None


def _prov(
    value: float, source: Source, conf: float, ref: str | None, note: str | None
) -> Prov[float]:
    return Prov[float](value=value, unit="s", source=source, confidence=conf, ref=ref, note=note)


@dataclass
class _Timed:
    time: TimeStandard
    variant_times: dict[str, TimeStandard]
    finding: RoutingFinding | None = None


def _time_for_step(
    inp: BuildInput, step: _Step, op_key: str, mc: MachineClassEntry | None
) -> _Timed | None:
    """Time standards per variant; None when no source can give a number."""
    proc = step.process
    ref = f"catalog:{inp.catalog_version}:process/{proc.code}"
    conf = proc.provenance.confidence
    user = inp.decisions.time.get(op_key, {})
    per: dict[str, TimeStandard] = {}
    for v in inp.variants:
        params = inp.params.get(v, {})
        setup = (
            _prov(proc.setup_s, "library", conf, ref, "Changeover setup from the catalog")
            if proc.setup_s
            else None
        )
        method: TimeMethod
        if v in user or "*" in user:
            secs = user.get(v, user.get("*", 0.0))
            cyc = _prov(secs, "user", 1.0, "user-edit", "Entered by the user")
            method = "user"
        elif (t := _formula_time(proc, mc, params)) is not None:
            cyc = _prov(t, "library", conf, ref, proc.time_model.formula)
            method = "catalog_formula"
        elif (t := _mtm_time(inp.catalog, proc)) is not None:
            cyc = _prov(t, "library", min(conf, 0.4), ref, "MTM elements from the catalog")
            method = "mtm_table"
        elif proc.code in inp.proposal.estimates:
            why = inp.proposal.reasons.get(proc.code) or "Model estimate"
            cyc = _prov(
                inp.proposal.estimates[proc.code], "llm", LLM_CONFIDENCE, "llm-estimate", why
            )
            method = "llm_estimate"
        else:
            return None
        per[v] = TimeStandard(
            cycle_s=cyc,
            setup_s=setup,
            method=method,
            distribution=proc.time_model.distribution,
            spread_pct=proc.time_model.spread_pct,
            formula=proc.time_model.formula,
        )
    first = per[inp.variants[0]]
    values = [per[v].cycle_s.value for v in inp.variants]
    if max(values) - min(values) <= 1e-9:
        return _Timed(first, {})
    weighted = sum(inp.mix.get(v, 0.0) * per[v].cycle_s.value for v in inp.variants)
    base_method = first.method
    methods = {per[v].method for v in inp.variants}
    wm = base_method if len(methods) == 1 else sorted(methods)[0]
    src = first.cycle_s.source
    mixed = TimeStandard(
        cycle_s=_prov(
            round(weighted, 6),
            src,
            min(per[v].cycle_s.confidence for v in inp.variants),
            ref,
            "Mix-weighted over the variants",
        ),
        setup_s=first.setup_s,
        method=wm,
        distribution=first.distribution,
        spread_pct=first.spread_pct,
        formula=first.formula,
    )
    return _Timed(mixed, dict(per))


# ── the build ─────────────────────────────────────────────────────────


def _batch(inp: BuildInput, step: _Step, mc: MachineClassEntry | None) -> BatchSpec | None:
    proc = step.process
    if proc.batch is None or mc is None:
        return None
    env = {**mc.params, **proc.constants, **inp.params.get(inp.variants[0], {})}
    try:
        cycle = evaluate(proc.batch.cycle_formula, env)
    except (ExprError, ValueError, ZeroDivisionError):
        return None
    size = int(mc.params.get(proc.batch.batch_size_param, 0))
    if size < 1:
        return None
    ref = f"catalog:{inp.catalog_version}:process/{proc.code}"
    return BatchSpec(
        batch_size=size,
        cycle_s=_prov(cycle, "library", proc.provenance.confidence, ref, proc.batch.cycle_formula),
        availability=float(mc.params.get("availability", 1.0)),
    )


def build_routing(inp: BuildInput) -> Routing:
    cat = inp.catalog
    st = _Structure(cat, inp.archetype, inp.bom)
    procs = _processes(cat)
    findings: list[RoutingFinding] = []

    steps, f1 = _catalog_steps(inp, st)
    psteps, f2 = _proposal_steps(inp, st, procs)
    findings += f1 + f2
    steps += psteps
    proposer = (
        inp.proposal.proposer
        if psteps or not inp.archetype.typical_routing
        else "catalog typical routing"
    )

    # Operations (a step without a time is a blocking finding, not an operation).
    ops: list[Operation] = []
    taken: dict[str, int] = defaultdict(int)
    order: list[_Step] = []
    for step in steps:
        key = f"{step.process.code}@{step.item}"
        taken[key] += 1
        if taken[key] > 1:
            key = f"{key}#{taken[key]}"
        if key in inp.decisions.drop:
            findings.append(
                RoutingFinding(
                    id=f"F-DROP-{len(findings) + 1:02d}",
                    kind="note",
                    text=f"You removed '{step.process.name}' on {step.item}.",
                    op_key=key,
                    status="info",
                )
            )
            continue
        mc = _machine(cat, step.process.machine_class)
        timed = _time_for_step(inp, step, key, mc)
        if timed is None:
            findings.append(
                RoutingFinding(
                    id=f"F-TIME-{len(findings) + 1:02d}",
                    kind="time_missing",
                    text=f"No time for '{step.process.name}' on {step.item}: the catalog formula "
                    "needs product parameters this product does not have. Enter seconds per unit.",
                    op_key=key,
                    part_no=step.item,
                    blocking=True,
                )
            )
            continue
        if timed.time.method == "llm_estimate" or any(
            t.method == "llm_estimate" for t in timed.variant_times.values()
        ):
            findings.append(
                RoutingFinding(
                    id=f"F-EST-{len(findings) + 1:02d}",
                    kind="estimate",
                    text=f"'{step.process.name}' on {step.item} uses a model estimate "
                    f"({timed.time.cycle_s.value:g} s), confidence below 0.5. Replace it with a "
                    "measured or catalog time.",
                    op_key=key,
                    part_no=step.item,
                    status="info",
                )
            )
        proc = step.process
        ops.append(
            Operation(
                op_no=f"OP-{(len(ops) + 1) * OP_STEP:03d}",
                item=step.item,
                process=CatalogRef(code=proc.code, version=inp.catalog_version or None),
                description=proc.name + (f" — {step.note}" if step.note else ""),
                workcenter_class=proc.machine_class or "manual",
                task_type=proc.task_type,
                consumes=list(step.consumes),
                produces=step.item,
                time=timed.time,
                quality_gate=proc.quality_checks[0] if proc.quality_checks else None,
                sample_pct=step.sample_pct,
                batch=_batch(inp, step, mc),
                variant_times=timed.variant_times,
                op_key=key,
                origin="proposal" if step.origin == "proposal" else "catalog",
                note=step.reason or step.note,
            )
        )
        order.append(step)

    # Lines no step names: first operation of the parent assembly (with a finding).
    consumed = {c for o in ops for c in o.consumes}
    first_op: dict[str, Operation] = {}
    for o in ops:
        first_op.setdefault(o.item, o)
    seen_default: set[str] = set()
    for ln in inp.bom.mbom:
        if ln.child in consumed or st.bought_assembly(ln.parent):
            continue
        target = first_op.get(ln.parent)
        if target is None:
            if ln.parent not in seen_default:
                seen_default.add(ln.parent)
                findings.append(
                    RoutingFinding(
                        id=f"F-NOOP-{len(seen_default):02d}",
                        kind="no_process",
                        text=f"No operation builds {st.name(ln.parent)} ({ln.parent}). Add a "
                        "process for it (re-run with a note, or ask for one in the catalog).",
                        part_no=ln.parent,
                    )
                )
            continue
        target.consumes.append(ln.child)
        consumed.add(ln.child)
        findings.append(
            RoutingFinding(
                id=f"F-DEF-{len(findings) + 1:02d}",
                kind="default_consumption",
                text=f"{ln.child} is not named by any routing step; it is used at "
                f"{target.op_no} ({target.description.split(' — ')[0]}) because that is the first "
                f"operation on {ln.parent}.",
                op_key=target.op_key,
                part_no=ln.child,
                status="info",
            )
        )

    precedence = _precedence(ops)
    totals = _totals(ops, inp.variants)
    return Routing(
        variants=list(inp.variants),
        operations=ops,
        precedence=precedence,
        catalog_version=inp.catalog_version,
        proposer=proposer,
        findings=findings,
        totals=totals,
    )


def _precedence(ops: list[Operation]) -> list[Precedence]:
    """Same-item operations in order; a consumer waits for the last producer of what it uses."""
    edges: dict[tuple[str, str], None] = {}
    by_item: dict[str, list[Operation]] = defaultdict(list)
    for o in ops:
        by_item[o.item].append(o)
    for chain in by_item.values():
        for a, b in pairwise(chain):
            edges[(a.op_no, b.op_no)] = None
    producers: dict[str, list[Operation]] = defaultdict(list)
    for o in ops:
        producers[o.produces].append(o)
    for o in ops:
        for c in o.consumes:
            made = [p for p in producers.get(c, []) if p.op_no != o.op_no]
            if made:
                edges[(made[-1].op_no, o.op_no)] = None
    return [Precedence(from_op=a, to_op=b) for a, b in edges]


_KIND: dict[TaskType, str] = {
    "manual": "manual_s",
    "machine_paced": "machine_s",
    "batch": "machine_s",
    "long_test": "test_s",
    "offline_rack": "test_s",
    "inspection": "test_s",
}


def _totals(ops: list[Operation], variants: list[str]) -> list[RoutingTotals]:
    out: list[RoutingTotals] = []
    for v in variants:
        acc = {"manual_s": 0.0, "machine_s": 0.0, "test_s": 0.0}
        for o in ops:
            if o.variants and v not in o.variants:
                continue
            t = o.variant_times.get(v, o.time)
            acc[_KIND[o.task_type]] += t.cycle_s.value * o.sample_pct / 100
        out.append(
            RoutingTotals(
                variant=v,
                cycle_s=round(sum(acc.values()), 3),
                manual_s=round(acc["manual_s"], 3),
                machine_s=round(acc["machine_s"], 3),
                test_s=round(acc["test_s"], 3),
            )
        )
    return out
