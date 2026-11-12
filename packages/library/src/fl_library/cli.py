"""``fl-library`` command line.

    fl-library validate [--library DIR]           catalog + pack tests, no database
    fl-library load     [--library DIR]           validate, then load into Postgres
    fl-library search   "spot welding" [--kind process]
    fl-library golden   [--golden DIR]            G1-G7 fixtures against the catalog
    fl-library pack-table [--state KA]            markdown table of every pack value

``load`` reads DATABASE_URL (the API's SQLAlchemy URL is accepted). It exits non-zero on
any validation error, so a broken library stops the deploy (the compose ``migrate`` step).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from fl_domain.base import Issue
from fl_domain.library.catalog import Catalog
from fl_domain.library.pack import Pack
from fl_library.checks import check_catalog, check_cross, check_pack, pack_assumptions
from fl_library.io import find_library_dir, load_catalog, load_pack


def _print(issues: list[Issue], label: str) -> None:
    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity == "warning"]
    for i in errors + warns:
        print(f"  {label}: {i}")
    print(f"  {label}: {len(errors)} error(s), {len(warns)} warning(s)")


def validate_all(
    library: Path, today: date | None = None
) -> tuple[Catalog | None, list[Pack], list[Issue]]:
    """Load and test catalog and every pack under ``library/packs``."""
    today = today or date.today()  # noqa: DTZ011 (calendar date, no time zone needed)
    issues: list[Issue] = []
    catalog, load_issues = load_catalog(library / "catalog")
    issues += load_issues
    if catalog is not None:
        issues += check_catalog(catalog)
    packs: list[Pack] = []
    for pack_dir in sorted(p for p in (library / "packs").iterdir() if p.is_dir()):
        pack, p_issues = load_pack(pack_dir)
        issues += p_issues
        if pack is not None:
            issues += check_pack(pack, today)
            packs.append(pack)
            if catalog is not None:
                issues += check_cross(catalog, pack)
    return catalog, packs, issues


def _summary(catalog: Catalog | None, packs: list[Pack]) -> None:
    if catalog is not None:
        kinds: dict[str, int] = {}
        for e in catalog.entries:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        counts = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
        print(f"  catalog {catalog.manifest.version}: {len(catalog.entries)} entries ({counts})")
    for p in packs:
        verify = sum(1 for v in p.values if v.verify)
        print(
            f"  pack {p.manifest.state} {p.manifest.version}: {len(p.values)} values, "
            f"{len(p.keys())} keys, {verify} marked verify, "
            f"{len(pack_assumptions(p))} active assumptions"
        )


def cmd_validate(args: argparse.Namespace) -> int:
    library = find_library_dir(args.library)
    print(f"==> Validating library at {library}")
    catalog, packs, issues = validate_all(library)
    _summary(catalog, packs)
    _print(issues, "library")
    return 1 if any(i.severity == "error" for i in issues) or catalog is None else 0


def cmd_load(args: argparse.Namespace) -> int:
    from fl_library.db import load_library

    library = find_library_dir(args.library)
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    print(f"==> Loading library from {library}")
    catalog, packs, issues = validate_all(library)
    _summary(catalog, packs)
    _print(issues, "validation")
    if catalog is None or any(i.severity == "error" for i in issues):
        print("Library validation failed; nothing loaded.", file=sys.stderr)
        return 1
    warnings = [i for i in issues if i.severity == "warning"]
    for r in load_library(url, catalog, packs, warnings):
        print(f"  {r.kind} {r.scope} {r.version}: {r.status} ({r.rows} rows)")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from fl_library.db import search_catalog

    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    rows = search_catalog(url, args.query, kind=args.kind, version=args.version, limit=args.limit)
    if args.json:
        print(json.dumps(rows, default=float))
    else:
        for r in rows:
            print(f"  {r['score']:.3f}  {r['kind']:<16} {r['code']:<28} {r['name']}")
    return 0 if rows else 1


def cmd_golden(args: argparse.Namespace) -> int:
    from fl_library.golden import check_golden

    library = find_library_dir(args.library)
    golden = Path(args.golden) if args.golden else library.parent / "golden"
    catalog, cat_issues = load_catalog(library / "catalog")
    if catalog is None:
        _print(cat_issues, "catalog")
        return 1
    reports = check_golden(golden, catalog)
    out: list[dict[str, Any]] = []
    for r in reports:
        print(f"  [{'PASS' if r.ok else 'FAIL'}] {r.case}")
        for i in r.issues:
            print(f"      {i}")
        out.append(r.model_dump(mode="json"))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0 if all(r.ok for r in reports) else 1


def _cell(v: Any) -> str:
    text = json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else str(v)
    return text.replace("|", "\\|").replace("\n", " ")


def cmd_pack_table(args: argparse.Namespace) -> int:
    library = find_library_dir(args.library)
    pack, issues = load_pack(library / "packs" / args.state)
    if pack is None:
        _print(issues, "pack")
        return 1
    print("| Key | Value | Unit | Effective | Status | Source | Kind | Verify |")
    print("|---|---|---|---|---|---|---|---|")
    for v in sorted(pack.values, key=lambda x: (x.group, x.key, x.effective_from)):
        eff = f"{v.effective_from}" + (f" → {v.effective_to}" if v.effective_to else " →")
        status = "superseded" if v.superseded else (
            "active" if v.active_on(pack.manifest.as_of) else "history/future"
        )
        src = f"[{_cell(v.source_title)}]({v.source_url})" if v.source_url else _cell(
            v.source_title)
        value = "unknown" if v.value is None else _cell(v.value)
        print(f"| `{v.key}` | {value} | {v.unit or ''} | {eff} | {status} | {src} | "
              f"{v.source_kind} | {'**yes**' if v.verify else 'no'} |")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="fl-library", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", help="library/ directory (default: auto)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    p = sub.add_parser("load")
    p.add_argument("--database-url")
    p.set_defaults(fn=cmd_load)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--kind")
    p.add_argument("--version")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.add_argument("--database-url")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("golden")
    p.add_argument("--golden")
    p.add_argument("--json", help="also write the reports to this file")
    p.set_defaults(fn=cmd_golden)
    p = sub.add_parser("pack-table")
    p.add_argument("--state", default="KA")
    p.set_defaults(fn=cmd_pack_table)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
