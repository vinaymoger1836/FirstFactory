"""BoM file reading and column mapping (docs/03 §2, §3.1). Rules first, no LLM here.

Shared by the API (mapping proposal shown in the wizard) and S1 (raw lines into
``structure.parts`` with ``source=bom_file``). Cleaning and gap checks belong to S2.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from fl_domain.artifacts.product_spec import BomColumnMap

__all__ = [
    "MAX_ROWS",
    "SYNONYMS",
    "bom_rows",
    "parse_qty",
    "propose_mapping",
    "read_table",
    "rows_to_parts",
]

MAX_ROWS = 5000
MAX_COLS = 60

# Header synonyms (lower case, punctuation removed). Matching is exact on the normalised
# header first, then "contains" for the longer forms.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "part_no": (
        "part no",
        "part number",
        "partno",
        "part",
        "pn",
        "p n",
        "item no",
        "item number",
        "item code",
        "code",
        "sku",
        "mpn",
        "part id",
        "id",
    ),
    "name": (
        "name",
        "part name",
        "description",
        "desc",
        "item",
        "item name",
        "component",
        "component name",
        "title",
    ),
    "qty": ("qty", "quantity", "qty per", "qty per unit", "quantity per", "count", "nos", "no off"),
    "uom": ("uom", "unit", "units", "unit of measure", "um"),
    "material": ("material", "matl", "mat", "material grade"),
}


def _norm(h: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", h.lower()).split())


def read_table(path: Path, filename: str) -> tuple[list[str], list[list[str]]]:
    """Headers and string rows of the first sheet (CSV or XLSX), capped in size."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            rows: list[list[str]] = []
            for r in ws.iter_rows(values_only=True, max_col=MAX_COLS):
                rows.append(["" if v is None else str(v).strip() for v in r])
                if len(rows) > MAX_ROWS:
                    break
        finally:
            wb.close()
    else:
        raw = path.read_bytes()[: 20 * 1024 * 1024]
        text = raw.decode("utf-8-sig", errors="replace")
        try:
            dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(text[:4096], ",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = [
            [c.strip() for c in r[:MAX_COLS]]
            for _, r in zip(
                range(MAX_ROWS + 1), csv.reader(io.StringIO(text), dialect), strict=False
            )
        ]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return [], []
    headers = [h or f"column_{i + 1}" for i, h in enumerate(rows[0])]
    return headers, rows[1 : MAX_ROWS + 1]


def propose_mapping(headers: list[str], variant_codes: list[str]) -> BomColumnMap:
    """Map headers to fields by synonyms; per-variant qty columns by variant code."""
    normed = {h: _norm(h) for h in headers}
    used: set[str] = set()
    found: dict[str, str | None] = {}
    variant_qty: dict[str, str] = {}
    for code in variant_codes:
        c = _norm(code)
        for h, n in normed.items():
            if h not in used and (n == c or n.endswith(" " + c) or n.startswith(c + " ")):
                variant_qty[code] = h
                used.add(h)
                break
    for field in ("part_no", "name", "qty", "uom", "material"):
        syn = SYNONYMS[field]
        hit = next((h for h, n in normed.items() if h not in used and n in syn), None)
        if hit is None:
            hit = next(
                (
                    h
                    for h, n in normed.items()
                    if h not in used and any(len(s) > 3 and s in n for s in syn)
                ),
                None,
            )
        found[field] = hit
        if hit:
            used.add(hit)
    return BomColumnMap(
        part_no=found["part_no"],
        name=found["name"],
        qty=found["qty"],
        uom=found["uom"],
        material=found["material"],
        variant_qty=variant_qty,
        confirmed=False,
    )


_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_qty(text: str) -> float | None:
    m = _NUM.search(text.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _local_id(text: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.\-/]+", "-", text.strip()).strip("-./")[:80]
    return s if s and s[0].isalnum() else fallback


def rows_to_parts(
    headers: list[str], rows: list[list[str]], mapping: BomColumnMap, att_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Raw BoM lines as PartRecord dicts (source bom_file). Returns (parts, notices)."""
    idx = {h: i for i, h in enumerate(headers)}

    def cell(row: list[str], col: str | None) -> str:
        if col is None or col not in idx or idx[col] >= len(row):
            return ""
        return row[idx[col]]

    parts: list[dict[str, Any]] = []
    notices: list[str] = []
    seen: dict[str, int] = {}
    missing_qty = 0
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        name = cell(row, mapping.name) or cell(row, mapping.part_no)
        if not name:
            continue
        pn = _local_id(cell(row, mapping.part_no), f"BOM-{n:04d}")
        if pn in seen:
            seen[pn] += 1
            pn = f"{pn}-dup{seen[pn]}"
        else:
            seen[pn] = 1
        per_variant = {
            code: q
            for code, col in mapping.variant_qty.items()
            if (q := parse_qty(cell(row, col))) is not None
        }
        qty = parse_qty(cell(row, mapping.qty)) if mapping.qty else None
        note_bits: list[str] = []
        if qty is None and per_variant:
            qty = max(per_variant.values())
            note_bits.append(
                "per variant: " + ", ".join(f"{k}={v:g}" for k, v in sorted(per_variant.items()))
            )
        if qty is None:
            missing_qty += 1
        uom = cell(row, mapping.uom) or None
        ref = f"attachment:{att_id}#row{n}"
        rec: dict[str, Any] = {
            "part_no": pn,
            "name": name[:200],
            "qty": {
                "value": qty if qty is not None else 0.0,
                "unit": None,
                "source": "bom_file",
                "confidence": 0.9 if qty is not None else 0.2,
                "ref": ref,
                "note": "; ".join(
                    [*note_bits, *([f"unit in file: {uom}"] if uom else [])]
                    + (["quantity missing in the file"] if qty is None else [])
                )
                or None,
            },
        }
        mat = cell(row, mapping.material)
        if mat:
            rec["material"] = {
                "value": mat[:100],
                "unit": None,
                "source": "bom_file",
                "confidence": 0.9,
                "ref": ref,
                "note": None,
            }
        parts.append(rec)
    if missing_qty:
        notices.append(
            f"{missing_qty} BoM line(s) in {att_id} have no quantity; the BoM stage will ask."
        )
    if mapping.qty is None and not mapping.variant_qty:
        notices.append(f"No quantity column was mapped for {att_id}.")
    return parts, notices


def bom_rows(
    headers: list[str],
    rows: list[list[str]],
    mapping: BomColumnMap,
    att_id: str,
    variants: list[str],
) -> list[dict[str, Any]]:
    """Raw BoM rows for S2 with exact per-variant quantities (None where the file is blank).

    Values are taken as written (no cleaning): S2 normalises units and keeps a snapshot to
    prove user lines are not rewritten (docs/03 §4.5).
    """
    idx = {h: i for i, h in enumerate(headers)}

    def cell(row: list[str], col: str | None) -> str:
        if col is None or col not in idx or idx[col] >= len(row):
            return ""
        return row[idx[col]].strip()

    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for n, row in enumerate(rows, start=2):
        name = cell(row, mapping.name) or cell(row, mapping.part_no)
        if not name:
            continue
        pn = _local_id(cell(row, mapping.part_no), f"BOM-{n:04d}")
        if pn in seen:
            seen[pn] += 1
            pn = f"{pn}-dup{seen[pn]}"
        else:
            seen[pn] = 1
        if mapping.variant_qty:
            qty = {
                v: parse_qty(cell(row, mapping.variant_qty[v]))
                if v in mapping.variant_qty
                else None
                for v in variants
            }
        else:
            q = parse_qty(cell(row, mapping.qty)) if mapping.qty else None
            qty = dict.fromkeys(variants, q)
        out.append(
            {
                "part_no": pn,
                "name": name[:200],
                "qty_by_variant": qty,
                "uom": cell(row, mapping.uom) or None,
                "material": cell(row, mapping.material) or None,
                "ref": f"attachment:{att_id}#row{n}",
            }
        )
    return out
