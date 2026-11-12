"""Golden fixtures (docs/14 §4): load G1-G7 and check them against the catalog.

P1 acceptance: "Golden routings for G1 and G2 can be expressed using catalog codes only."
For each product case this module

1. validates ``template.yaml`` as a ProductTemplate;
2. builds the domain ``Bom`` from ``bom.yaml`` and the domain ``Routing`` from
   ``routing.yaml``, where every operation names a catalog *process code* and takes its
   machine class, task type, quality gate and time standard from the catalog (formula
   evaluated with the case's product parameters by the safe evaluator);
3. runs the docs/04 §5 Bom and Routing validators with the catalog in context;
4. checks the catalog-derived inputs that the exact assertions depend on
   (``expected.yaml: catalog_inputs``), e.g. functional test 60 s, capacity test 6 h/10 h.

The expected numbers themselves (takt, PCT, positions, channels, ovens) are asserted by
the engines from P4 on; P1 stores them.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from fl_domain.artifacts.bom import Bom, BomLine, Item
from fl_domain.artifacts.product_spec import ProductTemplate
from fl_domain.artifacts.routing import BatchSpec, Operation, Precedence, Routing, TimeStandard
from fl_domain.base import DomainModel, Issue
from fl_domain.common import CatalogRef
from fl_domain.library.catalog import Catalog, MachineClassEntry, ProcessEntry
from fl_domain.prov import Prov
from fl_domain.validators import ValidationContext, validate_bom, validate_routing
from fl_library.checks import formula_value
from fl_library.io import read_yaml

__all__ = ["GoldenReport", "check_golden"]

PRODUCT_CASES = {"G1": "power_bank", "G2": "ceiling_fan"}


def _p(value: Any, unit: str | None = None, note: str | None = None) -> Any:
    return Prov(value=value, unit=unit, source="user", confidence=1.0, ref="golden", note=note)


# ── fixture file formats ──────────────────────────────────────────────


class FxItem(DomainModel):
    part_no: str
    name: str
    kind: Literal["product", "assembly", "part", "consumable", "packaging"]
    commodity_class: str | None = None
    make_or_buy: Literal["make", "buy"]
    variants: list[str] = Field(default_factory=list)


class FxLine(DomainModel):
    parent: str
    child: str
    qty: float
    uom: str = "unit"
    variant: str | None = None
    subassembly: str | None = None


class FxBom(DomainModel):
    root: str
    items: list[FxItem]
    mbom: list[FxLine]


class FxOp(DomainModel):
    op_no: str
    process: str
    item: str
    produces: str
    consumes: list[str] = Field(default_factory=list)
    variants: list[str] = Field(default_factory=list)
    sample_pct: float = 100
    quality_gate: str | None = None
    note: str | None = None


class FxRouting(DomainModel):
    operations: list[FxOp]
    precedence: Literal["sequential"] | list[tuple[str, str]] = "sequential"


class FxParams(DomainModel):
    archetype: str
    variants: dict[str, dict[str, float]]


class FxCatalogInput(DomainModel):
    process: str | None = None
    machine_class: str | None = None
    variant: str | None = None
    quantity: Literal["per_unit_s", "batch_cycle_s", "param"]
    param: str | None = None
    expected: float
    why: str


class FxExpected(DomainModel):
    case: str
    assertions: dict[str, Any]
    catalog_inputs: list[FxCatalogInput] = Field(default_factory=list)


class GoldenReport(DomainModel):
    case: str
    ok: bool
    issues: list[Issue] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)


# ── G1 / G2 ───────────────────────────────────────────────────────────


def _time(
    catalog: Catalog, proc: ProcessEntry, params: dict[str, dict[str, float]], variants: list[str]
) -> tuple[TimeStandard, dict[str, TimeStandard], BatchSpec | None]:
    per: dict[str, TimeStandard] = {}
    batch: BatchSpec | None = None
    for v in variants:
        unit_s, cycle_s = formula_value(catalog, proc.code, params[v])
        per[v] = TimeStandard(
            cycle_s=Prov(
                value=unit_s, unit="s", source="library", confidence=0.4, ref=f"catalog:{proc.code}"
            ),
            setup_s=Prov(value=proc.setup_s, unit="s", source="library", confidence=0.4),
            method="catalog_formula",
            distribution=proc.time_model.distribution,
            spread_pct=proc.time_model.spread_pct,
            formula=proc.time_model.formula,
        )
        if proc.batch and cycle_s is not None:
            m = catalog.by_code()[proc.machine_class or ""]
            assert isinstance(m, MachineClassEntry)
            batch = BatchSpec(
                batch_size=int(m.params[proc.batch.batch_size_param]),
                cycle_s=Prov(value=cycle_s, unit="s", source="library", confidence=0.4),
                availability=m.params.get("availability", 1.0),
            )
    first = per[variants[0]]
    overrides = {v: t for v, t in per.items() if t.cycle_s.value != first.cycle_s.value}
    return first, overrides, batch


def _product_case(gid: str, case_dir: Path, catalog: Catalog) -> GoldenReport:
    issues: list[Issue] = []
    facts: dict[str, Any] = {}
    try:
        template = ProductTemplate.model_validate(read_yaml(case_dir / "template.yaml"))
        params = FxParams.model_validate(read_yaml(case_dir / "params.yaml"))
        fx_bom = FxBom.model_validate(read_yaml(case_dir / "bom.yaml"))
        fx_route = FxRouting.model_validate(read_yaml(case_dir / "routing.yaml"))
        expected = FxExpected.model_validate(read_yaml(case_dir / "expected.yaml"))
    except (ValidationError, OSError) as exc:
        return GoldenReport(
            case=gid, ok=False, issues=[Issue(code="golden.schema", message=str(exc)[:2000])]
        )
    variants = [v.code for v in template.product.variants]
    if set(params.variants) != set(variants):
        issues.append(Issue(code="golden.params", message="params.yaml variants ≠ template"))
    if template.product.archetype != params.archetype:
        issues.append(Issue(code="golden.params", message="archetype mismatch"))

    bom = Bom(
        root=fx_bom.root,
        variants=variants,
        items=[
            Item(
                part_no=i.part_no,
                name=i.name,
                kind=i.kind,
                commodity_class=i.commodity_class,
                make_or_buy=_p(i.make_or_buy),
                variants=i.variants,
            )
            for i in fx_bom.items
        ],
        mbom=[
            BomLine(
                parent=ln.parent,
                child=ln.child,
                qty=_p(ln.qty, ln.uom),
                uom=ln.uom,
                variant=ln.variant,
                level="mbom",
                subassembly=ln.subassembly,
            )
            for ln in fx_bom.mbom
        ],
        commodity_classes=sorted({i.commodity_class for i in fx_bom.items if i.commodity_class}),
    )
    by_code = catalog.by_code()
    ops: list[Operation] = []
    for o in fx_route.operations:
        proc = by_code.get(o.process)
        if not isinstance(proc, ProcessEntry):
            issues.append(
                Issue(
                    code="golden.process",
                    path=o.op_no,
                    message=f"'{o.process}' is not a catalog process",
                )
            )
            continue
        if params.archetype not in proc.applies_to.archetype:
            issues.append(
                Issue(
                    code="golden.process",
                    path=o.op_no,
                    message=f"{o.process} does not apply to {params.archetype}",
                )
            )
        op_vars = o.variants or variants
        try:
            time, overrides, batch = _time(catalog, proc, params.variants, op_vars)
        except Exception as exc:
            issues.append(Issue(code="golden.formula", path=o.op_no, message=str(exc)))
            continue
        ops.append(
            Operation(
                op_no=o.op_no,
                item=o.item,
                process=CatalogRef(code=o.process, version=catalog.manifest.version),
                description=proc.name,
                workcenter_class=proc.machine_class or "manual",
                task_type=proc.task_type,
                consumes=o.consumes,
                produces=o.produces,
                time=time,
                quality_gate=o.quality_gate
                or (proc.quality_checks[0] if proc.quality_checks else None),
                sample_pct=o.sample_pct,
                batch=batch,
                variant_times=overrides,
                variants=o.variants,
            )
        )
    if fx_route.precedence == "sequential":
        prec = [
            Precedence(from_op=a.op_no, to_op=b.op_no)
            for a, b in itertools.pairwise(fx_route.operations)
        ]
    else:
        prec = [Precedence(from_op=a, to_op=b) for a, b in fx_route.precedence]
    routing = Routing(
        variants=variants, operations=ops, precedence=prec, catalog_version=catalog.manifest.version
    )

    ctx = ValidationContext(catalog=catalog, bom=bom)
    issues += [i for i in validate_bom(bom, ctx) if i.severity == "error"]
    issues += [i for i in validate_routing(routing, ctx) if i.severity == "error"]

    for ci in expected.catalog_inputs:
        got: float | None = None
        if ci.quantity == "param" and ci.machine_class and ci.param:
            m = by_code.get(ci.machine_class)
            got = m.params.get(ci.param) if isinstance(m, MachineClassEntry) else None
        elif ci.process and ci.variant:
            unit_s, cycle_s = formula_value(catalog, ci.process, params.variants[ci.variant])
            got = unit_s if ci.quantity == "per_unit_s" else cycle_s
        label = ci.process or ci.machine_class or "?"
        if got is None or abs(got - ci.expected) > 1e-9:
            issues.append(
                Issue(
                    code="golden.catalog_input",
                    path=label,
                    message=f"{ci.why}: catalog gives {got}, fixture expects {ci.expected}",
                )
            )
        facts[f"{label}:{ci.variant or ci.param}:{ci.quantity}"] = got
    facts["operations"] = len(ops)
    facts["process_codes"] = sorted({o.process.code for o in ops})
    facts["mbom_lines"] = len(bom.mbom)
    facts["assertions"] = expected.assertions
    return GoldenReport(case=gid, ok=not issues, issues=issues, facts=facts)


# ── G3-G7: fixture integrity ──────────────────────────────────────────


class FxSalbp(DomainModel):
    file: str
    n: int
    t_sum: float
    optima: dict[int, int] = Field(description="cycle time → optimal station count")


def _read_in2(path: Path) -> tuple[list[float], list[tuple[int, int]]]:
    lines = [ln.strip() for ln in path.read_text(encoding="ascii").splitlines() if ln.strip()]
    n = int(lines[0])
    times = [float(x) for x in lines[1 : 1 + n]]
    arcs: list[tuple[int, int]] = []
    for ln in lines[1 + n :]:
        a, b = (int(x) for x in ln.split(","))
        if a == -1:
            break
        arcs.append((a, b))
    return times, arcs


def _qap_brute(flow: Sequence[Sequence[float]], dist: Sequence[Sequence[float]]) -> float:
    n = len(flow)
    return min(
        sum(flow[i][j] * dist[p[i]][p[j]] for i in range(n) for j in range(n))
        for p in itertools.permutations(range(n))
    )


def _aux_cases(golden: Path) -> list[GoldenReport]:
    reports: list[GoldenReport] = []
    for gid, rel in (
        ("G3", "utilities/g3.yaml"),
        ("G4", "finance/g4.yaml"),
        ("G7", "benchmarks/queueing/g7.yaml"),
    ):
        path = golden / rel
        ok = path.is_file() and isinstance(read_yaml(path), dict)
        reports.append(
            GoldenReport(
                case=gid,
                ok=ok,
                facts={"file": rel},
                issues=[] if ok else [Issue(code="golden.missing", message=rel)],
            )
        )
    # G5: SALBP instances intact (n and Σt match Scholl's table).
    issues: list[Issue] = []
    salbp_dir = golden / "benchmarks" / "salbp"
    meta = read_yaml(salbp_dir / "optima.yaml")
    facts: dict[str, Any] = {}
    for name, raw in (meta.get("instances") or {}).items():
        inst = FxSalbp.model_validate(raw)
        times, arcs = _read_in2(salbp_dir / inst.file)
        if len(times) != inst.n or abs(sum(times) - inst.t_sum) > 1e-9:
            issues.append(Issue(code="golden.salbp", message=f"{name}: n/Σt differ from table"))
        if any(not (1 <= a <= inst.n and 1 <= b <= inst.n) for a, b in arcs):
            issues.append(Issue(code="golden.salbp", message=f"{name}: arc out of range"))
        facts[name] = {
            "n": len(times),
            "t_sum": sum(times),
            "arcs": len(arcs),
            "optima": inst.optima,
        }
    reports.append(GoldenReport(case="G5", ok=not issues, issues=issues, facts=facts))
    # G6: stored optimum equals brute force.
    qap = read_yaml(golden / "benchmarks" / "qap" / "g6.yaml")
    best = _qap_brute(qap["flow"], qap["distance"])
    ok = abs(best - float(qap["expected"]["optimal_cost"])) < 1e-9
    reports.append(
        GoldenReport(
            case="G6",
            ok=ok,
            facts={"brute_force_cost": best},
            issues=[] if ok else [Issue(code="golden.qap", message=f"brute force {best} ≠ stored")],
        )
    )
    return reports


def check_golden(golden: Path, catalog: Catalog) -> list[GoldenReport]:
    reports = [_product_case(gid, golden / d, catalog) for gid, d in PRODUCT_CASES.items()]
    return reports + _aux_cases(golden)
