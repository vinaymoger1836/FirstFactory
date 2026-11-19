"""Plant a page without evidence into the recorded vendor fixtures (docs/14 §5 P3).

After a recording pass (``scripts/e2e.py --record-new``) ``golden/web`` holds real searches and
pages. This adds one *planted* page to the search results of one class: marketing text that
names a company but states nothing a record could be built from, plus a recorded model answer
that "finds" a vendor with an invented quote. The engine must drop it (the quote is not on the
page), so the class shows no vendor from that page. The e2e replays this and checks.

    uv run python scripts/make_web_fixtures.py plant [--class li_ion_cell]

Run again to refresh; ``golden/web/planted.json`` records what was planted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from fl_agents.llm.cassette import CassetteStore, request_key
from fl_agents.llm.router import _schema_prompt
from fl_agents.stages.vendors import prompts
from fl_agents.stages.vendors.schemas import PageVendorsOut
from fl_agents.web.text import html_to_text
from fl_domain.library.catalog import CommodityClassEntry
from fl_library.io import load_catalog

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "golden" / "web"
CASSETTES = ROOT / "golden" / "cassettes"
HOST = "planted-supplier.example"
URL = f"https://{HOST}/best-suppliers-india"

HTML = """<html><head><title>Top suppliers in India - read this first</title></head><body>
<h1>The best suppliers in India</h1>
<p>Looking for quality? Our community has the answers. Click here to read more about sourcing
and to join our newsletter for weekly tips on procurement trends and market news.</p>
<p>Prices change often and every buyer has a different need. Compare, ask questions and
decide for yourself. This page does not list any company, product, price or address.</p>
<p>Phantom Power Pvt Ltd is mentioned here only as a name used in examples.</p>
<p>Copyright 2026. All rights reserved. Terms of use. Privacy policy. Contact the editor.</p>
</body></html>"""

# What a careless model might "find" on that page: a vendor with an invented quote.
INVENTED = {
    "vendors": [
        {
            "name": "Phantom Power Pvt Ltd",
            "evidence_quote": "Phantom Power Pvt Ltd is India's leading BIS registered "
            "manufacturer with 20 years of experience",
            "city": "Bengaluru",
            "state": "Karnataka",
            "products": ["21700 lithium-ion cells"],
            "certifications": ["BIS registered"],
            "moq": "100 pcs",
            "indicative_price": "Rs 85 per cell",
            "lead_time": "7 days",
            "contact_url": f"https://{HOST}/contact",
        }
    ]
}


def key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plant(class_code: str) -> int:
    cat, issues = load_catalog(ROOT / "library" / "catalog")
    if cat is None:
        print("catalog invalid:", issues, file=sys.stderr)
        return 1
    cc = cat.by_code().get(class_code)
    if not isinstance(cc, CommodityClassEntry):
        print(f"unknown class {class_code}", file=sys.stderr)
        return 1
    searches = sorted((WEB / "search").glob("*.json"))
    target = None
    for f in searches:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data["query"].startswith(cc.name):
            target = (f, data)
            break
    if target is None:
        print(
            f"no recorded search for class '{class_code}' ({cc.name}); record first with "
            "scripts/e2e.py --record-new",
            file=sys.stderr,
        )
        return 1
    f, data = target
    data["results"] = [r for r in data["results"] if r["url"] != URL]
    data["results"].insert(
        0,
        {
            "title": "Top suppliers in India - read this first",
            "url": URL,
            "snippet": "Looking for quality? Our community has the answers.",
        },
    )
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    title, text = html_to_text(HTML)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    page = {"url": URL, "requested": URL, "title": title, "text": text, "fetched_at": now}
    pdir = WEB / "pages"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{key(URL)}.json").write_text(
        json.dumps(page, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    msgs = prompts.extractor_messages(cc.name, cc.aliases, title, URL, text)
    full = [{"role": "system", "content": _schema_prompt(PageVendorsOut)}, *msgs]
    k = request_key(
        "extractor", full, PageVendorsOut.__name__, PageVendorsOut.model_json_schema(), 0.0
    )
    CassetteStore(CASSETTES).put(
        k,
        role="extractor",
        model="planted (hand-written, not a model answer)",
        schema_name=PageVendorsOut.__name__,
        messages=full,
        content=json.dumps(INVENTED),
        prompt_tokens=0,
        completion_tokens=0,
    )
    (WEB / "planted.json").write_text(
        json.dumps({"class": class_code, "url": URL, "host": HOST, "planted_at": now}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"planted {URL} into {f.name} (class {class_code}); cassette {k[:12]}…")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["plant"])
    ap.add_argument("--class", dest="cls", default="li_ion_cell")
    args = ap.parse_args()
    return plant(args.cls)


if __name__ == "__main__":
    raise SystemExit(main())
