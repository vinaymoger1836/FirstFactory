"""P3a end-to-end checks: S2 BoM stage and golden case G8 (docs/14 §4). Standard library only.

G8 (a) golden BoM file → case B, user lines unchanged, MBOM = golden MBOM;
G8 (b) partial file → case C, exactly the removed classes suggested, nothing added before
       acceptance, approval blocked, rejected suggestions remembered on re-run;
G8 (c) STEP only → case A, CAD-matched lines origin=cad, the rest skeleton/reasoner and
       suggested, approval blocked while any line is suggested.
Plus: make/buy per ADR-017 with catalog preview, mass = CAD volume × density, totals
reconcile, workbook sheets, tool-call budget pause, validator negative control, downstream.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Callable
from typing import Any

from e2e_p2 import (
    OUT,
    ROOT,
    Api,
    _yaml_json,
    latest,
    new_project,
    review_bom,
    stage_status,
    wait_for,
)

Record = Callable[[str, bool, str], bool]

CASES = {
    "G1": {"folder": "power_bank", "codes": "PB10K,PB20K", "prefix": "pb", "step": "pb10k.step"},
    "G2": {"folder": "ceiling_fan", "codes": "F1200,F900", "prefix": "fan", "step": "fan1200.step"},
}
REMOVED = {"labels", "user_manual", "packaging_carton", "consumable_solder"}


def _bom_project(
    base: str, gid: str, files: list[str], name: str, budgets: dict[str, int] | None = None
) -> tuple[Api, dict[str, Any]]:
    c = CASES[gid]
    tpl = _yaml_json(ROOT / f"golden/{c['folder']}/template.yaml")
    api = new_project(base, name)
    atts = []
    for f in files:
        _s, a = api.upload(OUT / f, variants=c["codes"])
        atts.append(
            {
                "id": a["id"],
                "kind": a["kind"],
                "file_ref": a["filename"],
                "note": None,
                "bom_columns": (
                    {**a["bom"]["proposal"], "confirmed": True} if a.get("bom") else None
                ),
            }
        )
    api.call("PUT", "/projects/current/template", {**tpl, "attachments": atts})
    api.call("POST", "/projects/current/submit", {"stage_budgets": budgets or {}})
    wait_for(api, lambda st: st["intake"]["status"] in ("awaiting_approval", "failed"), 240)
    api.call("POST", "/projects/current/stages/intake/approve", {})
    st = wait_for(
        api, lambda st: st["bom"]["status"] in ("awaiting_approval", "failed", "paused"), 300
    )
    return api, st


def _base(ln: dict[str, Any]) -> str:
    lid = ln.get("id") or ""
    v = ln.get("variant")
    return lid[: -len(v) - 1] if v and lid.endswith(f"-{v}") else lid


def _csv_rows(path: str) -> dict[str, dict[str, Any]]:
    text = (OUT / path).read_text(encoding="utf-8").strip().splitlines()
    head = text[0].split(",")
    out: dict[str, dict[str, Any]] = {}
    for line in text[1:]:
        cells = next(iter(__import__("csv").reader([line])))
        row = dict(zip(head, cells, strict=False))
        out[row["Part No"]] = row
    return out


def _golden_mbom(folder: str) -> set[tuple[Any, ...]]:
    bom = _yaml_json(ROOT / f"golden/{folder}/bom.yaml")
    root = bom["root"]
    return {
        (
            ln["subassembly"],
            "ROOT" if ln["parent"] == root else ln["parent"],
            ln["child"],
            ln.get("variant"),
            round(float(ln["qty"]), 6),
            {"pcs": "unit"}.get(ln.get("uom", "pcs"), ln.get("uom", "pcs")),
        )
        for ln in bom["mbom"]
    }


def _engine_mbom(art: dict[str, Any]) -> set[tuple[Any, ...]]:
    return {
        (
            ln["subassembly"],
            "ROOT" if ln["parent"] == art["root"] else ln["parent"],
            ln["child"],
            ln.get("variant"),
            round(float(ln["qty"]["value"]), 6),
            ln["uom"],
        )
        for ln in art["mbom"]
    }


def _sheets(xlsx: bytes) -> tuple[list[str], int]:
    z = zipfile.ZipFile(io.BytesIO(xlsx))
    names = re.findall(r'<sheet [^>]*name="([^"]+)"', z.read("xl/workbook.xml").decode())
    ebom = z.read("xl/worksheets/sheet1.xml").decode()
    return names, len(re.findall(r"<row ", ebom))


def p3_checks(base: str, psql: Callable[[str], str], record: Record, mode: str) -> None:
    print(f"==> P3a S2 BoM and G8 (LLM_MODE={mode})", flush=True)
    for gid, c in CASES.items():
        folder = c["folder"]
        # ── G8 (a): complete golden BoM ─────────────────────────────────
        api, st = _bom_project(base, gid, [f"{c['prefix']}-bom.csv"], f"E2E {gid} G8a")
        run = latest(api, "bom")
        art = run.get("artifact") or {}
        record(
            f"{gid} G8a: S2 → awaiting_approval, case B",
            st["bom"]["status"] == "awaiting_approval" and art.get("case") == "B",
            f"status={st['bom']['status']}, case={art.get('case')}, issues={[i['message'] for i in run.get('issues', [])][:2]}",
        )
        csv_rows = _csv_rows(f"{c['prefix']}-bom.csv")
        variants = art.get("variants", [])
        grouped: dict[str, dict[str, Any]] = {}
        for ln in art.get("ebom", []):
            if ln["origin"] == "user_bom":
                g = grouped.setdefault(ln["child"], {})
                for v in [ln["variant"]] if ln.get("variant") else variants:
                    g[v] = ln["qty"]["value"]
        diffs = []
        for pn, row in csv_rows.items():
            want = {v: float(row[f"Qty {v}"]) for v in variants if float(row[f"Qty {v}"]) > 0}
            if grouped.get(pn) != want:
                diffs.append(f"{pn}: {grouped.get(pn)} != {want}")
        record(
            f"{gid} G8a: every user line unchanged (qty per variant = file)",
            not diffs and set(grouped) == set(csv_rows),
            "; ".join(diffs[:3]) or f"{len(grouped)} lines",
        )
        gold, eng = _golden_mbom(folder), _engine_mbom(art)
        record(
            f"{gid} G8a: MBOM equals the golden MBOM",
            gold == eng,
            f"missing {sorted(gold - eng, key=str)[:2]} extra {sorted(eng - gold, key=str)[:2]}"
            if gold != eng
            else f"{len(eng)} lines",
        )
        s, body = api.call("POST", "/projects/current/stages/bom/approve", {})
        record(
            f"{gid} G8a: approval blocked until make/buy is confirmed (409)",
            s == 409,
            str(body)[:120],
        )
        decisions = {d["part_no"]: d for d in art.get("make_buy_decisions", [])}
        items = {i["part_no"]: i for i in art.get("items", [])}
        if gid == "G1":
            ok = (
                decisions.get("PCBA-BMS", {}).get("recommendation") == "buy"
                and decisions.get("SA-CELLPACK", {}).get("recommendation") == "make"
                and items.get("CELL-21700", {}).get("make_or_buy", {}).get("value") == "buy"
            )
            prev = decisions.get("PCBA-BMS", {}).get("preview") or {}
        else:
            ok = (
                decisions.get("CTRL-PCBA", {}).get("recommendation") == "buy"
                and decisions.get("SA-STATOR", {}).get("recommendation") == "make"
                and items.get("ROTOR-1200", {}).get("make_or_buy", {}).get("value") == "buy"
            )
            prev = decisions.get("CTRL-PCBA", {}).get("preview") or {}
        record(
            f"{gid}: make/buy per ADR-017 (PCBA buy, in-house assembly make)",
            ok,
            ", ".join(f"{k}:{d['recommendation']}" for k, d in list(decisions.items())[:6]),
        )
        cap = prev.get("capex_inr") or {}
        record(
            f"{gid}: PCBA make preview from catalog 2026.11.2 (capex range, m², operators)",
            bool(cap)
            and cap["low"] <= cap["base"] <= cap["high"]
            and prev.get("area_m2")
            and prev.get("operators_per_shift"),
            f"capex {cap.get('low')}–{cap.get('high')}, processes {prev.get('processes')}",
        )
        for t in art.get("totals", []):
            leaf = [
                ln
                for ln in art["mbom"]
                if ln.get("variant") in (None, t["variant"])
                and items.get(ln["child"], {}).get("kind") != "assembly"
            ]
            total = sum((ln.get("ext_cost_inr") or {}).get("base", 0) for ln in leaf)
            record(
                f"{gid}: totals reconcile with the MBOM lines ({t['variant']})",
                t.get("cost_inr") is not None and abs(total - t["cost_inr"]["base"]) < 0.05,
                f"lines {total:.2f} vs total {(t.get('cost_inr') or {}).get('base')}",
            )
        run2 = review_bom(api)
        s, body = api.call("POST", "/projects/current/stages/bom/approve", {})
        st = wait_for(
            api,
            lambda st: st["bom"]["status"] == "approved" and st["routing"]["status"] == "approved",
            240,
        )
        record(
            f"{gid} G8a: confirm make/buy → v{run2.get('version')} → approved; routing stub follows",
            s == 200 and st["bom"]["status"] == "approved" and st["routing"]["placeholder"],
            f"bom {st['bom']['status']} v{st['bom']['version']}",
        )
        s, xlsx = api.raw(f"/projects/current/stages/bom/runs/{run2.get('version')}/workbook")
        names, rows = _sheets(xlsx) if s == 200 else ([], 0)
        n_lines = len({_base(ln) for ln in run2["artifact"]["ebom"]})
        record(
            f"{gid}: BoM workbook has 5 sheets; EBOM rows = lines",
            names == ["EBOM", "MBOM", "Make-Buy", "Variants", "Open items"] and rows - 2 == n_lines,
            f"sheets {names}, EBOM rows {rows - 2} vs {n_lines} lines",
        )
        if gid == "G1":
            # validator negative control: a user line's qty changed without user provenance
            art2 = run2["artifact"]
            idx = next(i for i, ln in enumerate(art2["ebom"]) if ln["origin"] == "user_bom")
            old = art2["ebom"][idx]["qty"]["value"]
            s, body = api.call(
                "POST",
                "/projects/current/change-sets",
                {
                    "stage": "bom",
                    "base_version": run2["version"],
                    "reason": "planted",
                    "ops": [
                        {
                            "op": "replace",
                            "path": f"/ebom/{idx}/qty/value",
                            "old": old,
                            "new": old + 1,
                        }
                    ],
                },
            )
            record(
                "negative: user line changed without user provenance → rejected (user lines unchanged)",
                s == 422 and "user_changed" in json.dumps(body),
                str(body)[:140],
            )
            wait_for(
                api,
                lambda st: all(x["status"] == "approved" for k, x in st.items() if k != "intake"),
                240,
            )
            # downstream: editing the approved BoM (a recorded user edit) marks downstream stale
            s, body = api.call(
                "POST",
                "/projects/current/stages/bom/edit",
                {
                    "version": run2["version"],
                    "approve": True,
                    "reason": "e2e qty edit",
                    "edits": [{"path": f"/ebom/{idx}/qty", "value": old + 1}],
                },
            )
            stale = [k for k, x in stage_status(api).items() if x["status"] == "stale"]
            record(
                "editing the approved BoM (user edit) → new version; 11 downstream stages stale",
                s == 200 and len(stale) == 11,
                f"stale {len(stale)}: {stale[:4]}",
            )

        # ── G8 (b): partial BoM ─────────────────────────────────────────
        api, st = _bom_project(base, gid, [f"{c['prefix']}-bom-partial.csv"], f"E2E {gid} G8b")
        run = latest(api, "bom")
        art = run.get("artifact") or {}
        full_classes = {
            i.get("commodity_class")
            for i in _yaml_json(ROOT / f"golden/{folder}/bom.yaml")["items"]
        }
        expect = REMOVED & full_classes
        sug = {ln["commodity_class"] for ln in art.get("ebom", []) if ln["review"] == "suggested"}
        record(
            f"{gid} G8b: case C; exactly the removed classes suggested",
            art.get("case") == "C" and sug == expect,
            f"case {art.get('case')}; suggested {sorted(sug)} vs removed {sorted(expect)}",
        )
        sug_parts = {ln["child"] for ln in art.get("ebom", []) if ln["review"] == "suggested"}
        in_mbom = sug_parts & {ln["child"] for ln in art.get("mbom", [])}
        record(
            f"{gid} G8b: no suggestion is in the MBOM before acceptance",
            not in_mbom,
            str(sorted(in_mbom)),
        )
        s, _ = api.call("POST", "/projects/current/stages/bom/approve", {})
        record(
            f"{gid} G8b: approval blocked while lines are suggested (409)", s == 409, f"HTTP {s}"
        )
        run2 = review_bom(api, reject_classes=("labels",))
        v2 = run2["version"]
        api.call("POST", "/projects/current/stages/bom/rerun", {"note": "re-check the BoM"})
        wait_for(
            api,
            lambda st, v2=v2: (
                (st["bom"]["version"] or 0) > v2 and st["bom"]["status"] == "awaiting_approval"
            ),
            240,
        )
        art3 = latest(api, "bom").get("artifact") or {}
        label_lines = [
            ln
            for ln in art3.get("ebom", [])
            if ln["commodity_class"] == "labels" and ln["origin"] != "user_bom"
        ]
        chip = next(
            (c_ for c_ in art3.get("coverage", []) if c_["commodity_class"] == "labels"), {}
        )
        record(
            f"{gid} G8b: a rejected suggestion is not proposed again on re-run",
            not label_lines
            and "class:labels" in art3.get("rejected_suggestions", [])
            and chip.get("state") == "rejected",
            f"label lines {len(label_lines)}, chip {chip.get('state')}",
        )
        review_bom(api)
        s, _ = api.call("POST", "/projects/current/stages/bom/approve", {})
        st = wait_for(api, lambda st: st["bom"]["status"] == "approved", 120)
        record(
            f"{gid} G8b: after review the BoM is approved",
            s == 200 and st["bom"]["status"] == "approved",
            f"HTTP {s}",
        )

        # ── G8 (c): STEP only ───────────────────────────────────────────
        api, st = _bom_project(base, gid, [c["step"]], f"E2E {gid} G8c")
        run = latest(api, "bom")
        art = run.get("artifact") or {}
        cad = [ln for ln in art.get("ebom", []) if ln["origin"] == "cad"]
        others = [ln for ln in art.get("ebom", []) if ln["origin"] != "cad"]
        record(
            f"{gid} G8c: case A; CAD-matched lines origin=cad; all others skeleton/reasoner and suggested",
            art.get("case") == "A"
            and bool(cad)
            and all(
                ln["origin"] in ("skeleton", "reasoner") and ln["review"] == "suggested"
                for ln in others
            ),
            f"cad {sorted({ln['child'] for ln in cad})}; others {len(others)}; proposer {art.get('proposer')}",
        )
        s, _ = api.call("POST", "/projects/current/stages/bom/approve", {})
        record(
            f"{gid} G8c: approval blocked while lines are suggested (409)", s == 409, f"HTTP {s}"
        )
        audit = api.call("GET", "/projects/current/stages/bom/audit")[1] or []
        tools = [e for e in audit if e["kind"] == "tool_call"]
        record(
            f"{gid} G8c: proposer is the model (tool loop), ≤ 12 tool calls",
            art.get("proposer", "").startswith("AI") and len(tools) <= 12,
            f"{art.get('proposer')}, {len(tools)} tool calls",
        )
        if gid == "G1":
            items = {i["part_no"]: i for i in art.get("items", [])}
            _, atts = api.call("GET", "/projects/current/attachments")
            parts = {
                p["name"]: p for a in atts or [] for p in ((a.get("cad") or {}).get("parts") or [])
            }
            cell = items.get("CELLS", {})
            want = (parts.get("cell", {}).get("volume_mm3") or 0) / 1000 * 2.80
            got = (cell.get("mass_g") or {}).get("value")
            record(
                "mass = CAD volume × catalog density (cells, li_ion_cylindrical 2.80 g/cm³), within 1 %",
                bool(got) and abs(got - want) <= 0.01 * want,
                f"{got} g vs {want:.2f} g",
            )
        review_bom(api)
        s, _ = api.call("POST", "/projects/current/stages/bom/approve", {})
        st = wait_for(api, lambda st: st["bom"]["status"] == "approved", 120)
        record(
            f"{gid} G8c: after accepting the draft the BoM is approved",
            s == 200 and st["bom"]["status"] == "approved",
            f"HTTP {s}",
        )

    # ── tool-call budget: pause and continue ──────────────────────────────
    api, st = _bom_project(base, "G1", [CASES["G1"]["step"]], "E2E tool budget", {"bom_tools": 0})
    run = latest(api, "bom")
    record(
        "tool-call budget reached → S2 paused (resumable)",
        st["bom"]["status"] == "paused",
        f"status {st['bom']['status']}",
    )
    s, _ = api.call("POST", "/projects/current/stages/bom/resume", {"extra_tool_calls": 12})
    st = wait_for(api, lambda st: st["bom"]["status"] in ("awaiting_approval", "failed"), 300)
    record(
        "continue with more tool calls → awaiting_approval",
        s == 200 and st["bom"]["status"] == "awaiting_approval",
        f"status {st['bom']['status']}",
    )
