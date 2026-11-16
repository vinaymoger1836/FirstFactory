"""BoM workbook (docs/03 §4.8): EBOM, MBOM, Make-Buy, Variants, Open items. openpyxl."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

__all__ = ["bom_workbook"]

_HEAD = Font(bold=True)
_FILL = PatternFill("solid", fgColor="E5ECFF")
NOTE = (
    "Draft BoM. Costs and masses are engine numbers from catalog estimates; verify with "
    "vendors. Lines marked 'suggested' were never added to the MBOM."
)


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list[Any]]) -> None:
    ws = wb.create_sheet(title)
    ws.append([NOTE])
    ws.append(header)
    for c in ws[2]:
        c.font, c.fill = _HEAD, _FILL
    for r in rows:
        ws.append(r)
    for i, h in enumerate(header, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r)])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
    ws.freeze_panes = "A3"


def _base(line: dict[str, Any]) -> str:
    lid = str(line.get("id") or "")
    v = line.get("variant")
    return lid[: -len(v) - 1] if v and lid.endswith(f"-{v}") else lid


def bom_workbook(bom: dict[str, Any], title: str) -> bytes:
    wb = Workbook()
    assert wb.active is not None
    wb.remove(wb.active)
    variants: list[str] = bom["variants"]
    items = {i["part_no"]: i for i in bom["items"]}

    # EBOM: one row per line id, a quantity column per variant.
    grouped: dict[str, dict[str, Any]] = {}
    for ln in bom["ebom"]:
        g = grouped.setdefault(_base(ln), {"line": ln, "qty": {}})
        for v in [ln["variant"]] if ln.get("variant") else variants:
            g["qty"][v] = ln["qty"]["value"]
    ebom_rows = []
    for g in grouped.values():
        ln = g["line"]
        it = items.get(ln["child"], {})
        mat = (it.get("material") or {}).get("value")
        ebom_rows.append(
            [
                ln["child"],
                it.get("name", ""),
                *[g["qty"].get(v, "") for v in variants],
                ln["uom"],
                mat or "",
                ln.get("commodity_class") or "",
                ln.get("origin", ""),
                ln.get("review", ""),
                ln["qty"]["confidence"],
            ]
        )
    _sheet(
        wb,
        "EBOM",
        [
            "Part",
            "Description",
            *[f"Qty {v}" for v in variants],
            "UoM",
            "Material",
            "Commodity class",
            "Origin",
            "Review",
            "Confidence",
        ],
        ebom_rows,
    )
    _sheet(
        wb,
        "MBOM",
        [
            "Parent",
            "Sub-assembly",
            "Part",
            "Description",
            "Variant",
            "Qty",
            "UoM",
            "EBOM ref",
            "Cost base (INR)",
            "Mass (g)",
        ],
        [
            [
                ln["parent"],
                ln.get("subassembly") or "",
                ln["child"],
                items.get(ln["child"], {}).get("name", ""),
                ln.get("variant") or "all",
                ln["qty"]["value"],
                ln["uom"],
                ln.get("ebom_ref") or "",
                round((ln.get("ext_cost_inr") or {}).get("base", 0) or 0, 2) or "",
                ln.get("ext_mass_g") or "",
            ]
            for ln in bom["mbom"]
        ],
    )
    _sheet(
        wb,
        "Make-Buy",
        [
            "Part",
            "Recommendation",
            "Decision",
            "Confirmed",
            "Capex low",
            "Capex base",
            "Capex high",
            "Area m²",
            "Operators/shift",
            "Processes",
            "Rationale",
            "Note",
        ],
        [
            [
                d["part_no"],
                d["recommendation"],
                d["decision"]["value"],
                "yes" if d.get("confirmed") else "no",
                *(
                    [d["preview"]["capex_inr"][k] for k in ("low", "base", "high")]
                    if d.get("preview") and d["preview"].get("capex_inr")
                    else ["", "", ""]
                ),
                ((d.get("preview") or {}).get("area_m2") or {}).get("base", ""),
                ((d.get("preview") or {}).get("operators_per_shift") or {}).get("base", ""),
                ", ".join((d.get("preview") or {}).get("processes", [])),
                d.get("rationale", ""),
                (d.get("preview") or {}).get("note") or "",
            ]
            for d in bom["make_buy_decisions"]
        ],
    )
    _sheet(
        wb,
        "Variants",
        ["Part", "Description", "Variant", "Qty", "UoM"],
        [
            [
                ln["child"],
                items.get(ln["child"], {}).get("name", ""),
                ln["variant"],
                ln["qty"]["value"],
                ln["uom"],
            ]
            for ln in bom["mbom"]
            if ln.get("variant")
        ],
    )
    open_rows: list[list[Any]] = []
    for g in grouped.values():
        ln = g["line"]
        low = ln["qty"]["confidence"] < 0.6
        ai = (
            ln.get("origin") in ("skeleton", "reasoner", "engine")
            and ln.get("review") == "accepted"
        )
        if low or ai or ln.get("review") == "suggested":
            open_rows.append(
                [
                    ln["child"],
                    items.get(ln["child"], {}).get("name", ""),
                    ln.get("origin", ""),
                    ln.get("review", ""),
                    ln["qty"]["confidence"],
                    ln.get("reason") or "",
                ]
            )
    for f in bom.get("findings", []):
        if f.get("status") in ("open", "info"):
            open_rows.append(["", f["text"], f["kind"], f["status"], "", ""])
    _sheet(
        wb,
        "Open items",
        ["Part", "Description", "Origin / kind", "Review / status", "Confidence", "Reason"],
        open_rows,
    )
    wb.properties.title = f"BoM — {title}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
