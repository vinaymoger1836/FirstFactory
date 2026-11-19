"""S4 quality plan engine (docs/05 §2, ADR-013, ADR-018, ADR-028). Deterministic.

``build_quality`` turns the approved BoM and routing, the volumes and the catalog into a
``QualityPlan``:

* IQC: every counted, bought item whose class has IQC checks gets an AQL plan from the
  catalog's ISO 2859-1 table (lot = days of use per catalog default); checks that the routing
  already does on the line for that very item (cell grading) are inline, not sampled;
* IPQC / EOL: checks of each routing operation's process become inline or EOL checks bound to
  that operation (so S5 balances them as line tasks); periodic checks become patrol checks;
* OQC and lab: from the archetype's quality block;
* rework: (1 − FPY) × volume × rework time from the catalog;
* test policy (ADR-018): both policies computed side by side, one chosen;
* compliance links from the data pack's keys; a ``CheckRecord`` per check with the docs/15 §3
  fields.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fl_domain.artifacts.bom import Bom
from fl_domain.artifacts.quality import (
    AqlPlan,
    CheckRecord,
    ComplianceLink,
    InlineCheck,
    IqcItem,
    LabItem,
    PatrolCheck,
    PolicyImpact,
    QualityBasis,
    QualityPlan,
    ReworkLoad,
    SampledCheck,
    TestPolicy,
)
from fl_domain.artifacts.routing import Operation, Routing
from fl_domain.library.catalog import (
    ArchetypeEntry,
    Catalog,
    CheckTypeEntry,
    CommodityClassEntry,
    MachineClassEntry,
    PolicyEntry,
    ProcessEntry,
    SamplingPlanEntry,
)
from fl_domain.prov import Prov, Source
from fl_engines.quality.aql import AqlResult, lookup_plan

__all__ = ["BuildInput", "Decisions", "build_quality"]

NOT_DEFINED = "Not defined in this catalog version"


@dataclass
class Decisions:
    policy: str | None = None  # TestPolicy
    aql: dict[str, float] = field(default_factory=dict)  # part_no → AQL
    level: str | None = None
    lot_days: float | None = None


@dataclass
class BuildInput:
    catalog: Catalog
    archetype: ArchetypeEntry
    bom: Bom
    routing: Routing
    variants: list[str]
    mix: dict[str, float]
    annual_volume: float | None
    days_per_week: int
    shifts_per_day: int
    pack_titles: dict[str, str] = field(default_factory=dict)  # compliance key → title
    decisions: Decisions = field(default_factory=Decisions)
    catalog_version: str = ""


def _prov(
    value: float, source: Source, conf: float, ref: str | None, note: str | None
) -> Prov[float]:
    return Prov[float](value=value, unit=None, source=source, confidence=conf, ref=ref, note=note)


def _hours(value: float, conf: float, note: str) -> Prov[float]:
    return Prov[float](value=value, unit="hour", source="engine", confidence=conf, note=note)


def _fmt(x: float) -> str:
    return f"{x:g}"


def _sample_text(r: AqlResult) -> str:
    if r.full_inspection:
        return f"100 % of the lot ({r.lot_size}): the table's sample size reaches the lot size"
    return (
        f"n = {r.sample_size}, accept {r.accept} / reject {r.reject} "
        f"(ISO 2859-1 level {r.level}, code {r.letter}, AQL {_fmt(r.aql)}; lot {r.lot_size})"
    )


def _plan(r: AqlResult) -> AqlPlan:
    return AqlPlan(
        lot_size=r.lot_size,
        inspection_level=r.level,
        aql=r.aql,
        code_letter=r.letter,
        sample_size=r.sample_size,
        accept=r.accept,
        reject=r.reject,
        full_inspection=r.full_inspection,
        table_row=r.row,
    )


class _Ctx:
    """Catalog lookups used throughout one build."""

    def __init__(self, inp: BuildInput) -> None:
        cat = inp.catalog
        self.checks = {e.code: e for e in cat.entries if isinstance(e, CheckTypeEntry)}
        self.procs = {e.code: e for e in cat.entries if isinstance(e, ProcessEntry)}
        self.classes = {e.code: e for e in cat.entries if isinstance(e, CommodityClassEntry)}
        self.machines = {e.code: e for e in cat.entries if isinstance(e, MachineClassEntry)}
        self.table = next((e for e in cat.entries if isinstance(e, SamplingPlanEntry)), None)
        pol = {e.code: e for e in cat.entries if isinstance(e, PolicyEntry)}
        self.defaults = pol["operating_defaults"].values if "operating_defaults" in pol else {}
        self.records: list[CheckRecord] = []
        self.notes: list[str] = []

    def record(
        self,
        rid: str,
        ct: CheckTypeEntry,
        layer: str,
        applies_to: str,
        frequency: str,
        sample: str,
    ) -> None:
        self.records.append(
            CheckRecord(
                id=rid,
                check_type=ct.code,
                name=ct.name,
                layer=layer,
                applies_to=applies_to,
                characteristic=ct.characteristic or ", ".join(ct.measures) or NOT_DEFINED,
                limit=ct.limit or NOT_DEFINED,
                method=ct.method,
                instrument_class=ct.instrument_class or NOT_DEFINED,
                frequency=frequency or ct.frequency or NOT_DEFINED,
                reaction=ct.reaction or NOT_DEFINED,
                sample=sample,
                certifications=list(ct.supports_compliance),
            )
        )


def _daily_use(bom: Bom, part_no: str, daily: dict[str, float]) -> tuple[float, str]:
    """Units of ``part_no`` used per day and the line's unit of measure."""
    total, uom = 0.0, "unit"
    for ln in bom.mbom:
        if ln.child != part_no:
            continue
        uom = ln.uom
        for v, d in daily.items():
            if ln.variant in (None, v):
                total += d * ln.qty.value
    return total, uom


def build_quality(inp: BuildInput) -> QualityPlan:
    c = _Ctx(inp)
    dec = inp.decisions
    level = dec.level or "II"
    weeks = c.defaults.get("working_weeks_per_year")
    working_days = inp.days_per_week * weeks if weeks else None
    lot_days = dec.lot_days or c.defaults.get("iqc_lot_days", 7.0)
    daily: dict[str, float] = {}
    basis = QualityBasis(inspection_level=level)
    if inp.annual_volume and working_days:
        daily = {v: inp.annual_volume * inp.mix.get(v, 0) / working_days for v in inp.variants}
        basis = QualityBasis(
            annual_volume=_prov(inp.annual_volume, "user", 0.8, None, "From the product spec"),
            working_days_per_year=_prov(
                working_days,
                "library",
                0.4,
                f"catalog:{inp.catalog_version}:policy/operating_defaults",
                f"{inp.days_per_week} days a week x {_fmt(weeks or 0)} weeks; "
                "S5 sets the real pattern",
            ),
            daily_by_variant={v: round(d, 3) for v, d in daily.items()},
            iqc_lot_days=_prov(
                lot_days,
                "user" if dec.lot_days else "library",
                1.0 if dec.lot_days else 0.4,
                None
                if dec.lot_days
                else f"catalog:{inp.catalog_version}:policy/operating_defaults",
                "Days of use in one incoming lot; S6 replaces it with the order policy",
            ),
            inspection_level=level,
        )
    else:
        c.notes.append(
            "No annual volume in the product spec: sample sizes, inspector hours and rework "
            "are not sized. Add the volume in S1."
        )
    if c.table is None:
        c.notes.append("The catalog has no sampling table: IQC and OQC plans are not sized.")

    plan = QualityPlan(basis=basis, proposer="quality engine (no model)")
    ops = inp.routing.operations
    op_by_proc: dict[str, list[Operation]] = {}
    for o in ops:
        op_by_proc.setdefault(o.process.code, []).append(o)

    inspector_h = 0.0
    # ── IQC ───────────────────────────────────────────────────────────
    if c.table is not None and daily:
        done: set[str] = set()
        for ln in inp.bom.mbom:
            pn = ln.child
            it = inp.bom.item(pn)
            if pn in done or it is None or it.kind in ("assembly", "product"):
                continue
            if it.make_or_buy.value != "buy" or not it.commodity_class:
                continue
            done.add(pn)
            cc = c.classes.get(it.commodity_class)
            if cc is None or not cc.iqc_checks:
                continue
            use, uom = _daily_use(inp.bom, pn, daily)
            if uom != "unit" or use <= 0:
                c.notes.append(
                    f"{it.name} ({pn}) is counted in {uom}, not pieces: checked by supplier "
                    "certificate and a first-delivery test, no AQL plan."
                )
                continue
            lot = max(2, math.ceil(use * lot_days))
            aql = dec.aql.get(pn) or cc.default_aql
            sampled: list[CheckTypeEntry] = []
            inline_codes: list[str] = []
            for code in cc.iqc_checks:
                ct = c.checks.get(code)
                if ct is None:
                    continue
                line_op = next(
                    (
                        o
                        for o in op_by_proc.get(ct.process or "", [])
                        if ct.sampling == "100pct" and o.item == pn
                    ),
                    None,
                )
                if line_op is not None:
                    inline_codes.append(code)
                    plan.inline.append(InlineCheck(check_type=code, op_no=line_op.op_no))
                    c.record(
                        f"CK-{line_op.op_no}-{code}",
                        ct,
                        "iqc",
                        line_op.op_no,
                        "Every incoming unit (100 %), as a line operation",
                        f"100 % of incoming units at {line_op.op_no}",
                    )
                else:
                    sampled.append(ct)
            if not sampled or aql is None:
                if sampled and aql is None:
                    c.notes.append(f"{it.name} has no AQL in the catalog; set one to size IQC.")
                continue
            res = lookup_plan(c.table, lot, aql, level)
            t = sum(ct.time_s or 0.0 for ct in sampled)
            missing = [ct.code for ct in sampled if not ct.time_s]
            if missing:
                c.notes.append(
                    f"No inspection time for {', '.join(missing)}: not counted in hours."
                )
            h = res.sample_size * t / lot_days / 3600
            inspector_h += h
            ref = f"catalog:{inp.catalog_version}:check_type"
            plan.iqc.append(
                IqcItem(
                    part_no=pn,
                    commodity_class=it.commodity_class,
                    name=it.name,
                    check_types=[ct.code for ct in sampled],
                    plan=_plan(res),
                    lots_per_month=round((working_days or 0) / 12 / lot_days, 3),
                    check_time_s=_prov(t, "library", 0.4, ref, "Sum of the catalog check times"),
                    inspector_h_per_day=_hours(h, 0.4, "samples x check time / lot days"),
                    lot_days=lot_days,
                    inline_checks=inline_codes,
                )
            )
            for ct in sampled:
                c.record(
                    f"CK-IQC-{pn}-{ct.code}",
                    ct,
                    "iqc",
                    pn,
                    f"Every lot (about every {_fmt(lot_days)} days)",
                    _sample_text(res),
                )

    # ── IPQC, EOL, patrol, rework ────────────────────────────────────
    policy_check = str(inp.archetype.test_policy.get("check", ""))
    policy_op: Operation | None = None
    for o in ops:
        proc = c.procs.get(o.process.code)
        if policy_check and proc is not None and policy_check in proc.quality_checks:
            policy_op = o
            break
    cap_pct: float | None = None
    if inp.archetype.test_policy and policy_op is not None:
        wanted = str(dec.policy or inp.archetype.test_policy.get("default", ""))
        chosen: TestPolicy = (
            "capacity_100" if wanted == "capacity_100" else "functional_100_capacity_sampled"
        )
        sample = float(inp.archetype.test_policy.get("capacity_sample_pct", 2))
        cap_pct = sample if chosen == "functional_100_capacity_sampled" else 100.0
        plan.test_policy = chosen
        plan.capacity_sample_pct = cap_pct
        plan.policy_impact = _policy_impact(c, policy_op, daily, sample)

    patrol_use = {u.check: u for u in inp.archetype.quality.patrol}
    patrol_done: set[str] = set()
    seen_inline: set[tuple[str, str]] = {(x.check_type, x.op_no) for x in plan.inline}
    for o in ops:
        proc = c.procs.get(o.process.code)
        if proc is None:
            continue
        codes = list(proc.quality_checks)
        codes += [k for k, ct in c.checks.items() if ct.process == proc.code and k not in codes]
        for code in codes:
            ct = c.checks.get(code)
            if ct is None or (code, o.op_no) in seen_inline:
                continue
            if ct.process and ct.process in op_by_proc and o.process.code != ct.process:
                continue  # the check has its own operation in the routing: it belongs there
            if ct.sampling == "periodic":
                pu = patrol_use.get(code)
                if pu is None or code in patrol_done:
                    continue
                patrol_done.add(code)
                t = ct.time_s or 0.0
                plan.patrol.append(
                    PatrolCheck(
                        check_type=code,
                        per_shift=pu.per_shift,
                        duration_s=_prov(
                            t, "library", 0.4, f"catalog:{inp.catalog_version}", "Check time"
                        ),
                    )
                )
                inspector_h += pu.per_shift * inp.shifts_per_day * t / 3600
                c.record(
                    f"CK-PAT-{code}",
                    ct,
                    "ipqc",
                    o.op_no,
                    f"{_fmt(pu.per_shift)} per shift",
                    f"{_fmt(pu.per_shift)} samples per shift",
                )
                continue
            if ct.sampling == "aql":
                continue
            is_policy = bool(policy_check) and code == policy_check and cap_pct is not None
            coverage = cap_pct if is_policy and cap_pct else o.sample_pct
            final = o.item == inp.bom.root and "eol" in ct.layers
            seen_inline.add((code, o.op_no))
            (plan.eol if final else plan.inline).append(
                InlineCheck(check_type=code, op_no=o.op_no, coverage_pct=coverage or 100)
            )
            c.record(
                f"CK-{o.op_no}-{code}",
                ct,
                "eol" if final else "ipqc",
                o.op_no,
                "Every unit (100 %)"
                if (coverage or 100) >= 100
                else f"{_fmt(coverage or 0)} % of units",
                "100 % of units"
                if (coverage or 100) >= 100
                else f"{_fmt(coverage or 0)} % of output (sampled)",
            )
        if proc.rework and daily:
            share = (
                cap_pct
                if o.op_no == (policy_op.op_no if policy_op else "") and cap_pct
                else o.sample_pct
            ) / 100
            units = sum(daily.values()) * share
            load = units * (1 - proc.fpy) * proc.rework.time_s
            ref = f"catalog:{inp.catalog_version}:process/{proc.code}"
            plan.rework.append(
                ReworkLoad(
                    op_no=o.op_no,
                    fpy=proc.fpy,
                    rework_time_s=_prov(
                        proc.rework.time_s,
                        "library",
                        proc.provenance.confidence,
                        ref,
                        "Rework time",
                    ),
                    units_per_day=round(units, 3),
                    load_s_per_day=_prov(
                        round(load, 3),
                        "engine",
                        proc.provenance.confidence,
                        ref,
                        "(1 - FPY) x units x rework time",
                    ),
                )
            )

    # ── OQC and lab ──────────────────────────────────────────────────
    n_var = len(inp.variants)
    total_daily = sum(daily.values())
    for u in inp.archetype.quality.oqc:
        ct = c.checks.get(u.check)
        if ct is None:
            continue
        t = ct.time_s or 0.0
        if ct.sampling == "aql" and c.table is not None and total_daily > 0:
            aql = u.aql or ct.default_aql
            if aql is None:
                continue
            res = lookup_plan(c.table, max(2, math.ceil(total_daily)), aql, level)
            h = res.sample_size * t / 3600
            inspector_h += h
            plan.oqc.append(
                SampledCheck(
                    check_type=u.check,
                    plan=_plan(res),
                    inspector_h_per_day=_hours(h, 0.4, "one lot per day: samples x check time"),
                    frequency="Every day's output is one lot",
                )
            )
            c.record(
                f"CK-OQC-{u.check}",
                ct,
                "oqc",
                "product",
                "Each day's packed output (one lot)",
                _sample_text(res),
            )
        elif u.per_week_per_variant:
            per_day = u.per_week_per_variant * n_var / max(inp.days_per_week, 1)
            h = per_day * t / 3600
            inspector_h += h
            plan.oqc.append(
                SampledCheck(
                    check_type=u.check,
                    inspector_h_per_day=_hours(h, 0.4, "units per week x check time"),
                    frequency=u.frequency,
                )
            )
            c.record(
                f"CK-OQC-{u.check}",
                ct,
                "oqc",
                "product",
                u.frequency or ct.frequency or "",
                u.frequency or "Per the schedule",
            )
    for lu in inp.archetype.quality.lab:
        ct = c.checks.get(lu.check)
        if ct is None:
            continue
        plan.lab.append(
            LabItem(
                name=lu.name,
                check_type=lu.check,
                machine_class=lu.machine_class,
                qty=lu.qty,
                frequency=lu.frequency,
            )
        )
        if lu.per_week_per_variant:
            inspector_h += (
                lu.per_week_per_variant
                * n_var
                / max(inp.days_per_week, 1)
                * (ct.time_s or 0)
                / 3600
            )
        c.record(f"CK-LAB-{lu.check}", ct, "lab", "product", lu.frequency, lu.frequency)

    # ── compliance ───────────────────────────────────────────────────
    keys = list(inp.archetype.compliance_pack_keys)
    for ct in (c.checks[r.check_type] for r in c.records):
        keys += [k for k in ct.supports_compliance if k not in keys]
    used = {r.check_type for r in c.records}
    for k in keys:
        if k not in inp.pack_titles:
            c.notes.append(f"Compliance item {k} is not in the data pack; not linked.")
            continue
        sup = sorted(code for code in used if k in c.checks[code].supports_compliance)
        plan.compliance.append(
            ComplianceLink(pack_key=k, title=inp.pack_titles[k], supported_by_checks=sup)
        )

    plan.checks = c.records
    if daily:
        plan.inspector_h_per_day = _hours(
            round(inspector_h, 4), 0.4, "IQC + patrol + OQC + lab, from catalog times"
        )
        plan.quarantine_area_m2 = _quarantine(c, plan, inp)
    plan.notes = [
        *c.notes,
        "First-article inspection is done at the start of every shift and after each changeover; "
        "its time is not sized here (changeovers are set in S5).",
    ]
    return plan


def _quarantine(c: _Ctx, plan: QualityPlan, inp: BuildInput) -> Prov[float] | None:
    per_pallet = c.defaults.get("default_units_per_pallet", 0)
    pallet_m2 = c.defaults.get("pallet_footprint_m2")
    lots = c.defaults.get("quarantine_lots_per_class", 1)
    if not pallet_m2 or not per_pallet or not plan.iqc:
        return None
    area = 0.0
    for x in plan.iqc:
        cc = c.classes.get(x.commodity_class or "")
        upp = (
            cc.pack_qty * cc.cartons_per_pallet
            if cc is not None and cc.cartons_per_pallet
            else per_pallet
        )
        area += math.ceil(x.plan.lot_size / upp) * pallet_m2 * lots
    return Prov[float](
        value=round(area, 2),
        unit="m**2",
        source="engine",
        confidence=0.3,
        ref=f"catalog:{inp.catalog_version}:policy/operating_defaults",
        note="Pallet places for one held lot of each incoming item; S6 sizes the real store",
    )


def _policy_impact(
    c: _Ctx, op: Operation, daily: dict[str, float], sample_pct: float
) -> list[PolicyImpact]:
    """Rack hours and test channels for both test policies (ADR-018), from the catalog."""
    mc = c.machines.get(op.workcenter_class)
    if not daily or mc is None:
        return []
    rack_h = float(mc.params.get("rack_hours_per_day", 0))
    margin = float(mc.params.get("sizing_margin", 0))
    out: list[PolicyImpact] = []
    for policy, pct, label in (
        (
            "functional_100_capacity_sampled",
            sample_pct,
            f"100 % functional test + {_fmt(sample_pct)} % capacity test",
        ),
        ("capacity_100", 100.0, "100 % capacity test"),
    ):
        hours = sum(
            d * pct / 100 * op.variant_times.get(v, op.time).cycle_s.value / 3600
            for v, d in daily.items()
        )
        channels = math.ceil(round(hours / rack_h * (1 + margin), 9)) if rack_h else 0
        out.append(
            PolicyImpact(
                policy=policy,
                label=label,
                coverage_pct=pct,
                rack_hours_per_day=round(hours, 3),
                channels=channels,
            )
        )
    return out
