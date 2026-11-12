"""P1 host-side checks, run by scripts/e2e.py with the workspace environment:

    uv run python scripts/e2e_p1.py --out <json>

1. Golden fixtures G1-G7 against the catalog; G1/G2 routings built from catalog codes only
   and passed through the docs/04 §5 Bom and Routing validators.
2. Negative controls: each check must *fail* on a deliberately broken input, proving the
   validators bite (bad reference, formula injection, expired pack value, overlapping pack
   values, unknown process in a routing, MBOM line not consumed, BoM cycle).
3. Schema round-trip data: writes packages/ts-types/src/generated/roundtrip.check.ts, which
   types real Python-built objects (G1/G2 template, Bom, Routing; catalog entry; pack value)
   against the generated TypeScript. ``pnpm -C packages/ts-types typecheck`` then compiles it.

Writes a JSON list of {check, ok, detail} to --out. Exit code 0 when everything passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from fl_domain.artifacts.bom import BomLine
from fl_domain.prov import Prov
from fl_domain.validators import ValidationContext, validate_bom, validate_routing
from fl_library.checks import check_catalog, check_pack
from fl_library.golden import PRODUCT_CASES, check_golden, product_case
from fl_library.io import load_catalog, load_pack

ROOT = Path(__file__).resolve().parents[1]
results: list[dict[str, Any]] = []


def record(check: str, ok: bool, detail: str = "") -> None:
    results.append({"check": check, "ok": ok, "detail": detail})


def errors(issues: list[Any]) -> list[str]:
    return [str(i) for i in issues if i.severity == "error"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    catalog, cat_issues = load_catalog(ROOT / "library" / "catalog")
    pack, pack_issues = load_pack(ROOT / "library" / "packs" / "KA")
    if catalog is None or pack is None:
        record("library loads", False, "; ".join(map(str, cat_issues + pack_issues))[:500])
        args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        return 1

    # 1. Golden fixtures.
    for r in check_golden(ROOT / "golden", catalog):
        detail = "; ".join(map(str, r.issues))[:300]
        if r.case in PRODUCT_CASES:
            f = r.facts
            detail = detail or (
                f"{f.get('operations')} ops / {f.get('mbom_lines')} MBOM lines, "
                f"{len(f.get('process_codes', []))} catalog processes, validators clean"
            )
        record(f"golden {r.case} fixture", r.ok, detail)

    # 2. Negative controls (each must produce an error).
    bad = catalog.model_copy(deep=True)
    proc = next(e for e in bad.entries if e.code == "spot_weld_nickel")
    proc.machine_class = "no_such_machine"  # type: ignore[union-attr]
    proc.time_model.formula = "n_welds * __import__('os').getpid()"  # type: ignore[union-attr]
    errs = errors(check_catalog(bad))
    record(
        "negative: catalog bad reference + formula injection rejected",
        any("no_such_machine" in e for e in errs) and any("formula" in e for e in errs),
        f"{len(errs)} errors",
    )

    expired = pack.model_copy(deep=True)
    for v in expired.values:
        if v.key == "labour.weekly_hours_max":
            v.effective_to = date(2026, 6, 30)
    errs = errors(check_pack(expired))
    record(
        "negative: expired pack value rejected",
        any("expired" in e for e in errs),
        errs[0] if errs else "no error",
    )

    overlap = pack.model_copy(deep=True)
    for v in overlap.values:
        if v.key == "power.ht2a.demand_charge" and v.value == 350:
            v.superseded = False
    errs = errors(check_pack(overlap))
    record(
        "negative: two active pack values rejected",
        any("overlap" in e for e in errs),
        errs[0] if errs else "no error",
    )

    _, built = product_case("G1", ROOT / "golden" / "power_bank", catalog)
    bom, routing = built["bom"], built["routing"]
    ctx = ValidationContext(catalog=catalog, bom=bom)  # type: ignore[arg-type]
    unknown = routing.model_copy(deep=True)  # type: ignore[union-attr]
    unknown.operations[2].process.code = "laser_weld_magic"
    errs = errors(validate_routing(unknown, ctx))
    record(
        "negative: routing with a non-catalog process rejected",
        any("laser_weld_magic" in e for e in errs),
        errs[0] if errs else "no error",
    )

    unconsumed = routing.model_copy(deep=True)  # type: ignore[union-attr]
    unconsumed.operations[7].consumes = []  # OP-080 screw_drive no longer consumes SCREW-M2
    errs = errors(validate_routing(unconsumed, ctx))
    record(
        "negative: MBOM line not consumed by any op rejected",
        any("SCREW-M2" in e for e in errs),
        errs[0] if errs else "no error",
    )

    cyclic = bom.model_copy(deep=True)  # type: ignore[union-attr]
    cyclic.mbom.append(
        BomLine(
            parent="SA-CELLPACK",
            child="SA-CORE",
            uom="unit",
            level="mbom",
            qty=Prov(value=1.0, source="user", confidence=1.0),
        )
    )
    errs = errors(validate_bom(cyclic, ctx))
    record(
        "negative: BoM cycle rejected",
        any("cycle" in e for e in errs),
        errs[0] if errs else "no error",
    )

    # 3. Round-trip: Python objects → JSON → typed against generated TS.
    consts: list[str] = []
    for gid, folder in PRODUCT_CASES.items():
        _, objs = product_case(gid, ROOT / "golden" / folder, catalog)
        for name, type_name in (
            ("template", "ProductTemplate"),
            ("bom", "Bom"),
            ("routing", "Routing"),
        ):
            data = objs[name].model_dump(mode="json")
            consts.append(
                f"export const {gid.lower()}_{name}: T.{type_name} = "
                f"{json.dumps(data, ensure_ascii=False)};"
            )
    entry = next(e for e in catalog.entries if e.code == "capacity_cycle_test")
    consts.append(
        f"export const catalog_entry: T.ProcessEntry = "
        f"{json.dumps(entry.model_dump(mode='json'), ensure_ascii=False)};"
    )
    pv = pack.active("power.ht2a.demand_charge")
    assert pv is not None
    consts.append(
        f"export const pack_value: T.PackValue = "
        f"{json.dumps(pv.model_dump(mode='json'), ensure_ascii=False)};"
    )
    gen = ROOT / "packages" / "ts-types" / "src" / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "roundtrip.check.ts").write_text(
        "// GENERATED by scripts/e2e_p1.py: real Python-built objects typed against the\n"
        "// generated domain types. tsc must accept them (schema round-trip check).\n"
        'import type * as T from "./index";\n\n' + "\n".join(consts) + "\n",
        encoding="utf-8",
    )
    record("round-trip data written", True, f"{len(consts)} typed constants")

    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
