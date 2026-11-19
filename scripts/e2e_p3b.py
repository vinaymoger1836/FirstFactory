"""P3b end-to-end checks: S3 routing, S4 quality plan, vendor research (docs/14 §5 P3).
Standard library only; called by e2e.py after the P3a checks.

For G1 and G2, through the real pipeline with the golden BoM file:
  S3  complete routing equal to golden/*/routing.yaml (processes, items, consumed parts,
      precedence); times equal the catalog inputs in expected.yaml; all validators pass; no
      model tokens; an LLM-estimated time is flagged and one with confidence >= 0.5 is refused.
  S4  every check carries the docs/15 §3 fields; AQL sample sizes equal the ISO 2859-1 table
      (re-derived here from the standard's rules, not from the catalog); G1 policy channels 8
      and 398; the test-policy toggle changes the plan.
  P1  every vendor has a source URL and a quote that is really in the recorded page; a planted
      page without evidence yields no vendor; a second project reuses the shared cache.
  SSRF guard blocks private and metadata addresses inside the agents container.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from itertools import pairwise
from typing import Any

from e2e_p2 import ROOT, _yaml_json, latest, review_bom, wait_for
from e2e_p3 import CASES, _bom_project

Record = Callable[[str, bool, str], bool]
Exec = Callable[[str, str], Any]

# ── ISO 2859-1, re-derived from the standard's rules (independent of library/catalog) ──────
LOT_II = [
    (2, 8, "A"), (9, 15, "B"), (16, 25, "C"), (26, 50, "D"), (51, 90, "E"), (91, 150, "F"),
    (151, 280, "G"), (281, 500, "H"), (501, 1200, "J"), (1201, 3200, "K"), (3201, 10000, "L"),
    (10001, 35000, "M"), (35001, 150000, "N"), (150001, 500000, "P"), (500001, 10**12, "Q"),
]  # fmt: skip
LETTERS = "ABCDEFGHJKLMNPQR"
SIZE = dict(
    zip(LETTERS, [2, 3, 5, 8, 13, 20, 32, 50, 80, 125, 200, 315, 500, 800, 1250, 2000], strict=True)
)
AQLS = [
    0.010,
    0.015,
    0.025,
    0.040,
    0.065,
    0.10,
    0.15,
    0.25,
    0.40,
    0.65,
    1.0,
    1.5,
    2.5,
    4.0,
    6.5,
    10,
]
AC = [1, 2, 3, 5, 7, 10, 14, 21]


def iso2859(lot: int, aql: float) -> tuple[str, int, int, int]:
    """(letter, sample size, Ac, Re) for general level II, single sampling, normal."""
    i = next(k for k, (lo, hi, _l) in enumerate(LOT_II) if lo <= max(lot, 2) <= hi)
    i = LETTERS.index(LOT_II[i][2])
    j = min(range(16), key=lambda k: abs(AQLS[k] - aql))
    off = j - (14 - i)  # 0/1 sits on the diagonal; 1/2 three columns on, then one per column
    if off < 0:
        i, off = i - off, 0  # arrow down to the first plan below
    elif off in (1, 2):
        i, off = i - off, 0  # arrow up to the 0/1 plan above
    elif off > 10:
        i, off = i - (off - 10), 10  # arrow up past the last plan
    ac = 0 if off == 0 else AC[off - 3]
    n = SIZE[LETTERS[i]]
    return LETTERS[i], min(n, lot), ac, ac + 1


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(
        {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-", 0xA0: " "}
    )
    return re.sub(r"\s+", " ", t.casefold()).strip()


def _golden_routing(folder: str) -> dict[str, Any]:
    return _yaml_json(ROOT / f"golden/{folder}/routing.yaml")


def _bom_art(api: Any) -> dict[str, Any]:
    _, body = api.call("GET", "/projects/current/stages/bom")
    return (body.get("latest") or {}).get("artifact") or {}


def _answer_and_wait(api: Any, stage: str, answers: dict[str, str]) -> dict[str, Any]:
    v = latest(api, stage)["version"]
    api.call("POST", f"/projects/current/stages/{stage}/answer", {"answers": answers})
    wait_for(
        api,
        lambda st: (
            (st[stage]["version"] or 0) > v
            and st[stage]["status"] in ("awaiting_approval", "failed")
        ),
        180,
    )
    return latest(api, stage)


def _estimate_flag_checks(base: str, prefix: str, record: Record) -> None:
    """An LLM estimate is flagged; one with confidence >= 0.5 is refused (docs/05 §1.2)."""
    api, _ = _bom_project(base, "G2", [f"{prefix}-bom.csv"], "E2E G2 estimate flag")
    review_bom(api)
    api.call("POST", "/projects/current/stages/bom/approve", {})
    wait_for(api, lambda st: st["routing"]["status"] == "awaiting_approval", 240)
    r = latest(api, "routing")
    old = r["artifact"]["operations"][0]["time"]["cycle_s"]

    def payload(conf: float) -> dict[str, Any]:
        return {
            "stage": "routing",
            "base_version": r["version"],
            "reason": "planted",
            "ops": [
                {
                    "op": "replace",
                    "path": "/operations/0/time/method",
                    "old": "catalog_formula",
                    "new": "llm_estimate",
                },
                {
                    "op": "replace",
                    "path": "/operations/0/time/cycle_s",
                    "old": old,
                    "new": {**old, "source": "llm", "confidence": conf, "note": "planted"},
                },
            ],
        }

    s_bad, body_bad = api.call("POST", "/projects/current/change-sets", payload(0.9))
    record(
        "negative: an LLM-estimated time with confidence 0.9 is refused (routing.llm_conf)",
        s_bad == 422 and "routing.llm_conf" in json.dumps(body_bad),
        f"HTTP {s_bad}: {str(body_bad)[:100]}",
    )
    s_ok, cs = api.call("POST", "/projects/current/change-sets", payload(0.3))
    applied = (
        api.call("POST", f"/projects/current/change-sets/{cs['id']}/apply", {})
        if s_ok == 201
        else (0, None)
    )
    new = latest(api, "routing")
    flagged = any(i["code"] == "routing.llm_estimate" for i in new.get("issues", []))
    in_register = any(a["source"] == "llm" for a in new.get("assumptions", []))
    record(
        "an LLM-estimated time (confidence 0.3) is accepted but flagged in warnings and assumptions",
        s_ok == 201 and applied[0] == 200 and flagged and in_register,
        f"warning {flagged}, in assumptions register {in_register}",
    )


def p3b_checks(
    base: str, psql: Callable[[str], str], compose_exec: Exec, record: Record, mode: str
) -> None:
    print(f"==> P3b S3 routing, S4 quality, vendors (LLM_MODE={mode})", flush=True)
    psql("TRUNCATE vendor_cache")
    planted = ROOT / "golden" / "web" / "planted.json"
    for gid, c in CASES.items():
        folder = c["folder"]
        expected = _yaml_json(ROOT / f"golden/{folder}/expected.yaml")
        api, _st = _bom_project(base, gid, [f"{c['prefix']}-bom.csv"], f"E2E {gid} P3b")
        review_bom(api)
        s, _body = api.call("POST", "/projects/current/stages/bom/approve", {})
        st = wait_for(
            api, lambda st: st["routing"]["status"] in ("awaiting_approval", "failed"), 240
        )
        run = latest(api, "routing")
        art = run.get("artifact") or {}
        ops = art.get("operations", [])
        errors = [i["message"] for i in run.get("issues", []) if i["severity"] == "error"]
        record(
            f"{gid} S3: routing awaiting approval, all validators pass",
            s == 200
            and st["routing"]["status"] == "awaiting_approval"
            and not errors
            and bool(ops),
            f"status {st['routing']['status']}, {len(ops)} ops, errors {errors[:2]}",
        )

        # complete and equal to the golden routing
        g = _golden_routing(folder)
        gops = g["operations"]
        root = _yaml_json(ROOT / f"golden/{folder}/bom.yaml")["root"]  # the engine calls it PRODUCT
        want = [(o["process"], o["item"], sorted(o.get("consumes", []))) for o in gops]
        got = [
            (
                o["process"]["code"],
                root if o["item"] == "PRODUCT" else o["item"],
                sorted(o["consumes"]),
            )
            for o in ops
        ]
        record(
            f"{gid} S3: operations equal the golden routing (process, item, consumed parts)",
            want == got,
            f"{len(got)} ops; first difference {next(((w, x) for w, x in zip(want, got, strict=False) if w != x), 'none')}",
        )
        gnos = [o["op_no"] for o in gops]
        if g["precedence"] == "sequential":
            gprec = set(pairwise(gnos))
        else:
            gprec = {(a, b) for a, b in g["precedence"]}
        prec = {(p["from_op"], p["to_op"]) for p in art.get("precedence", [])}
        record(
            f"{gid} S3: precedence graph equals the golden precedence",
            prec == gprec,
            f"missing {sorted(gprec - prec)[:3]} extra {sorted(prec - gprec)[:3]}",
        )
        mbom_children = {ln["child"] for ln in _bom_art(api).get("mbom", [])}
        consumed = {x for o in ops for x in o["consumes"]}
        record(
            f"{gid} S3: every MBOM line is consumed by an operation",
            mbom_children <= consumed,
            f"unconsumed {sorted(mbom_children - consumed)[:4]}",
        )

        # times equal the catalog inputs (docs/14 §4, expected.yaml)
        bad: list[str] = []
        for ci in expected.get("catalog_inputs", []):
            if "process" not in ci:
                continue
            op = next((o for o in ops if o["process"]["code"] == ci["process"]), None)
            if op is None:
                bad.append(f"{ci['process']} missing")
                continue
            if ci["quantity"] == "per_unit_s":
                v = op["variant_times"].get(ci["variant"], op["time"])["cycle_s"]["value"]
            else:
                v = (op.get("batch") or {}).get("cycle_s", {}).get("value")
            if v is None or abs(float(v) - float(ci["expected"])) > 1e-6:
                bad.append(f"{ci['process']}/{ci['variant']}: {v} != {ci['expected']}")
        record(
            f"{gid} S3: times equal the catalog inputs of expected.yaml",
            not bad,
            "; ".join(bad[:3]) or "all match",
        )
        methods = {o["time"]["method"] for o in ops} | {
            t["method"] for o in ops for t in o["variant_times"].values()
        }
        record(
            f"{gid} S3: every time is a catalog formula (nothing LLM-estimated), no model tokens",
            methods == {"catalog_formula"} and run.get("tokens_used", 1) == 0,
            f"methods {sorted(methods)}, tokens {run.get('tokens_used')}",
        )
        if gid == "G2":
            batch = (
                next((o["batch"] for o in ops if o["process"]["code"] == "varnish_dip_cure"), None)
                or {}
            )
            record(
                "G2 S3: varnish oven batch = 60 stators, 7,200 s cycle",
                batch.get("batch_size") == 60 and abs(batch["cycle_s"]["value"] - 7200) < 1e-6,
                str(batch)[:120],
            )

        # approve routing → S4
        s, _ = api.call("POST", "/projects/current/stages/routing/approve", {})
        wait_for(api, lambda st: st["quality"]["status"] in ("awaiting_approval", "failed"), 240)
        qrun = latest(api, "quality")
        q = qrun.get("artifact") or {}
        qerrors = [i["message"] for i in qrun.get("issues", []) if i["severity"] == "error"]
        record(
            f"{gid} S4: quality plan awaiting approval, validators pass, no model tokens",
            qrun.get("status") == "awaiting_approval"
            and not qerrors
            and qrun.get("tokens_used", 1) == 0,
            f"{qrun.get('status')}, errors {qerrors[:2]}, tokens {qrun.get('tokens_used')}",
        )
        fields = (
            "characteristic",
            "limit",
            "method",
            "instrument_class",
            "frequency",
            "reaction",
            "sample",
        )
        checks = q.get("checks", [])
        lacking = [
            ck["id"]
            for ck in checks
            if any(
                not str(ck.get(f, "")).strip() or "Not defined" in str(ck.get(f)) for f in fields
            )
        ]
        record(
            f"{gid} S4: every check carries the docs/15 §3 fields",
            len(checks) >= 20 and not lacking,
            f"{len(checks)} checks; lacking {lacking[:3]}",
        )
        recs = {(ck["check_type"], ck["applies_to"]) for ck in checks}
        unlinked = [
            (x["check_type"], x["op_no"])
            for x in [*q.get("inline", []), *q.get("eol", [])]
            if (x["check_type"], x["op_no"]) not in recs
        ]
        record(
            f"{gid} S4: every inline and end-of-line check is bound to a routing operation with a record",
            not unlinked,
            str(unlinked[:3]),
        )

        # AQL sample sizes equal the standard
        diffs = []
        for x in q.get("iqc", []):
            p = x["plan"]
            letter, n, ac, re_ = iso2859(p["lot_size"], p["aql"])
            if (p["code_letter"], p["sample_size"], p["accept"], p["reject"]) != (
                letter,
                n,
                ac,
                re_,
            ):
                diffs.append(
                    f"{x['part_no']}: lot {p['lot_size']} AQL {p['aql']} → {p['code_letter']} {p['sample_size']} {p['accept']}/{p['reject']} != {letter} {n} {ac}/{re_}"
                )
        record(
            f"{gid} S4: AQL plans equal ISO 2859-1 (level II, single, normal) for every incoming item",
            len(q.get("iqc", [])) >= 8 and not diffs,
            f"{len(q.get('iqc', []))} items; {'; '.join(diffs[:2]) or 'all match'}",
        )
        if gid == "G1":
            cells = next((x for x in q["iqc"] if x["part_no"] == "CELL-21700"), {})
            p = cells.get("plan", {})
            record(
                "G1 S4: cells lot 19,600 (7 days x 2,800/day) → code M, n = 315, accept 5 / reject 6 at AQL 0.65",
                (
                    p.get("lot_size"),
                    p.get("code_letter"),
                    p.get("sample_size"),
                    p.get("accept"),
                    p.get("reject"),
                )
                == (19600, "M", 315, 5, 6),
                str(p)[:140],
            )
            impact = {i["policy"]: i["channels"] for i in q.get("policy_impact", [])}
            want_s = expected["assertions"]["capacity_test_sampled"]["channels"]
            want_f = expected["assertions"]["capacity_test_full"]["channels"]
            record(
                "G1 S4: both test policies computed: 8 channels sampled, 398 channels at 100 %",
                impact == {"functional_100_capacity_sampled": want_s, "capacity_100": want_f},
                str(impact),
            )
            run2 = _answer_and_wait(api, "quality", {"policy:test": "capacity_100"})
            q2 = run2.get("artifact") or {}
            cap = next((x for x in q2.get("eol", []) if x["check_type"] == "capacity_test"), {})
            record(
                "G1 S4: policy toggle → capacity test on 100 % of units in a new version",
                q2.get("test_policy") == "capacity_100"
                and cap.get("coverage_pct") == 100
                and run2["version"] > qrun["version"],
                f"policy {q2.get('test_policy')}, coverage {cap.get('coverage_pct')}",
            )
            _answer_and_wait(api, "quality", {"policy:test": "functional_100_capacity_sampled"})
        rw = (q.get("rework") or [None])[0]
        if rw:
            calc = rw["units_per_day"] * (1 - rw["fpy"]) * rw["rework_time_s"]["value"]
            record(
                f"{gid} S4: rework load = units x (1 - FPY) x rework time",
                abs(calc - rw["load_s_per_day"]["value"]) < 0.01,
                f"{rw['op_no']}: {calc:.2f} vs {rw['load_s_per_day']['value']}",
            )
        titles_ok = bool(q.get("compliance")) and all(
            x["title"] != x["pack_key"] for x in q["compliance"]
        )
        record(
            f"{gid} S4: compliance items link to the data pack with titles",
            titles_ok,
            str([x["pack_key"] for x in q.get("compliance", [])]),
        )
        s, _ = api.call("POST", "/projects/current/stages/quality/approve", {})
        st = wait_for(api, lambda st: st["quality"]["status"] == "approved", 120)
        record(
            f"{gid} S4: approved; later stages follow",
            s == 200 and st["quality"]["status"] == "approved",
            st["quality"]["status"],
        )

        # P1 vendors
        st = wait_for(api, lambda st: st["vendors"]["status"] in ("approved", "failed"), 900)
        vrun = latest(api, "vendors")
        vart = vrun.get("artifact") or {}
        classes = vart.get("classes", [])
        vendors = [(cl["vendor_class"], v) for cl in classes for v in cl["vendors"]]
        record(
            f"{gid} vendors: stage finished (web trouble becomes gaps, not a failure)",
            st["vendors"]["status"] == "approved" and not vrun.get("placeholder") and bool(classes),
            f"{st['vendors']['status']}, {len(classes)} classes, {len(vendors)} vendors, gaps {sum(len(cl['gaps']) for cl in classes)}",
        )
        no_source = [
            v["name"]
            for _c, v in vendors
            if not v["evidence"]
            or any(
                not e["url"].startswith("http") or not e["snippet"].strip() for e in v["evidence"]
            )
        ]
        record(
            f"{gid} vendors: every vendor shown has a source URL and a quoted snippet",
            not no_source,
            f"{len(vendors)} vendors; without source {no_source[:3]}",
        )
        unverified: list[str] = []
        missing_pages = 0
        for _c, v in vendors:
            for e in v["evidence"]:
                pf = (
                    ROOT
                    / "golden"
                    / "web"
                    / "pages"
                    / f"{hashlib.sha256(e['url'].encode()).hexdigest()}.json"
                )
                if not pf.is_file():
                    missing_pages += 1
                    continue
                text = norm(json.loads(pf.read_text(encoding="utf-8"))["text"])
                if norm(e["snippet"]) not in text or norm(v["name"]) not in text:
                    unverified.append(f"{v['name']} @ {e['url']}")
        record(
            f"{gid} vendors: each quoted snippet and vendor name is in the recorded page text",
            not unverified and (missing_pages == 0 or mode != "replay"),
            f"unverified {unverified[:2]}, pages not on disk {missing_pages}",
        )
        if gid == "G1":
            if mode == "replay" and planted.is_file():
                info = json.loads(planted.read_text(encoding="utf-8"))
                host = info["host"]
                leaked = [v["name"] for _c, v in vendors if host in json.dumps(v)]
                cl = next((x for x in classes if x["vendor_class"] == info["class"]), {})
                record(
                    "G1 vendors: a planted page without evidence produces no vendor",
                    not leaked and cl.get("pages_dropped", 0) >= 1 and cl.get("pages_read", 0) >= 1,
                    f"class {info['class']}: pages read {cl.get('pages_read')}, dropped {cl.get('pages_dropped')}, leaked {leaked}",
                )
            else:
                record(
                    "G1 vendors: planted-page check",
                    True,
                    "skipped: needs replay mode and golden/web/planted.json (scripts/make_web_fixtures.py)",
                )
        else:
            cached = [cl["vendor_class"] for cl in classes if cl.get("cached_at")]
            record(
                "G2 vendors: classes already researched for G1 come from the shared cache",
                bool(cached) or not any(cl["vendors"] for cl in classes),
                f"cached {cached[:4]}",
            )

        if gid == "G2":
            _estimate_flag_checks(base, c["prefix"], record)

    # SSRF guard inside the agents container
    code = (
        "import asyncio, sys\n"
        "from fl_agents.web import BlockedUrlError, check_public_url\n"
        "async def main():\n"
        "    bad = ['http://169.254.169.254/latest/meta-data/', 'http://127.0.0.1:8000/', 'http://10.0.0.1/', "
        "'http://[::1]/', 'http://localhost/', 'file:///etc/passwd', 'http://u:p@example.com/']\n"
        "    for u in bad:\n"
        "        try:\n"
        "            await check_public_url(u)\n"
        "        except BlockedUrlError:\n"
        "            continue\n"
        "        print('NOT BLOCKED', u); sys.exit(1)\n"
        "    print('all blocked')\n"
        "asyncio.run(main())\n"
    )
    p = compose_exec("agents", code)
    record(
        "SSRF guard: metadata, loopback, private, IPv6 loopback, file and credential URLs are blocked",
        p.returncode == 0 and "all blocked" in p.stdout,
        (p.stdout + p.stderr).strip()[-160:],
    )
