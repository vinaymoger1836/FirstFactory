"""End-to-end check (`make e2e` / `uv run scripts/e2e.py`). Standard library only.

P1 adds (docs/14 §5 P1 acceptance):
  - the compose `migrate` step validates the catalog and KA pack and loads them into Postgres
    (a validation error stops the stack); versions, row counts and the pgvector HNSW index
    are checked in the database; hybrid catalog search returns the expected entries;
  - reloading is idempotent, the same version with different content is refused, and a
    broken library makes the loader fail with nothing written;
  - host side (scripts/e2e_p1.py): G1-G7 fixtures, G1/G2 routings in catalog codes only
    through the docs/04 §5 validators, negative controls;
  - schema round-trip: Pydantic → JSON Schema → TypeScript, compiled with real objects.

P0 scope (kept): build + start the stack, then verify through Caddy:
  - liveness and readiness (Postgres+pgvector, Redis, worker and cad-worker heartbeats)
  - project create → only the token hash is stored → token resolves; a bad token is rejected
  - SSE demo streams every progress event and a final `done` event
  - a job round-trips through the queue on each worker (engines and cad)
Golden cases G1-G7 (docs/14 §4) are listed and reported as not yet implemented; later phases
replace those entries with real runs.

Options: --no-build  skip image build     --down  stop the stack afterwards
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "infra/docker-compose.yml"]
PHASE = "P1"
GOLDEN = {
    "G1": "Power bank: demand, takt, PCT, test positions/channels",
    "G2": "BLDC fan: demand, takt, PCT, run-in positions, varnish ovens",
    "G3": "Utilities: MD 180 kVA → transformer 250 kVA",
    "G4": "Finance: NPV 8.14, IRR 15.24%",
    "G5": "Balancing: Scholl SALBP-1 optima",
    "G6": "Layout: QAP equals brute force",
    "G7": "Simulation: serial line throughput; M/M/1 L=4",
}

# What each golden case asserts at this phase (fixtures and catalog routing are checked in
# host_checks; the exact numbers need the engines of later phases).
GOLDEN_STATUS = {
    "G1": "FIXTURE: routing in catalog codes + validators pass; exact numbers from P4",
    "G2": "FIXTURE: routing in catalog codes + validators pass; exact numbers from P4",
    "G3": "FIXTURE: stored; asserted from P5",
    "G4": "FIXTURE: stored; asserted from P8",
    "G5": "FIXTURE: instances verified against Scholl's table; asserted from P4",
    "G6": "FIXTURE: optimum re-verified by brute force; layout engine from P6",
    "G7": "FIXTURE: stored; asserted from P7",
}

results: list[dict] = []


def env_value(key: str, default: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].split("#", 1)[0].strip() or default
    return default


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": name, "ok": ok, "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def http(
    method: str,
    url: str,
    body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 10,
) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 (local URL)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def sh(args: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, check=check, text=True, capture_output=capture)


def compose_exec(service: str, code: str) -> subprocess.CompletedProcess:
    return sh([*COMPOSE, "exec", "-T", service, "python", "-c", code], check=False, capture=True)


def compose_run(service: str, *args: str) -> subprocess.CompletedProcess:
    return sh([*COMPOSE, "exec", "-T", service, *args], check=False, capture=True)


def psql(sql: str) -> str:
    user, db = env_value("POSTGRES_USER", "factory"), env_value("POSTGRES_DB", "factory")
    q = sh(
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", user, "-d", db, "-tA", "-F|", "-c", sql],
        check=False,
        capture=True,
    )
    return q.stdout.strip()


SAME_VERSION_CHANGED = """
import os, sys
from pathlib import Path
from fl_library.io import load_catalog
from fl_library.db import load_library
cat, _ = load_catalog(Path(os.environ["FL_LIBRARY_DIR"]) / "catalog")
cat.entries[0].name = cat.entries[0].name + " (edited)"
try:
    load_library(os.environ["DATABASE_URL"], cat, [], [])
except ValueError as exc:
    print("refused:", exc); sys.exit(0)
print("accepted"); sys.exit(1)
"""

BROKEN_LIBRARY = """
import shutil, subprocess, sys
from pathlib import Path
src, dst = Path("/app/library"), Path("/tmp/badlib")
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(src, dst)
f = dst / "packs" / "KA" / "labour.yaml"
old = "effective_from: 2026-05-08"
f.write_text(f.read_text().replace(old, old + chr(10) + "    effective_to: 2026-06-30", 1))
p = subprocess.run(["fl-library", "--library", str(dst), "load"], capture_output=True, text=True)
text = p.stdout + p.stderr
print(p.returncode, [ln for ln in text.splitlines() if "expired" in ln][:1], text[-120:])
sys.exit(0 if p.returncode != 0 and "nothing loaded" in text else 1)
"""

SEARCHES = [
    ("battery spot welding", "process", "spot_weld_nickel"),
    ("hi-pot dielectric withstand test", "check_type", "hipot"),
    ("varnish curing oven", "machine_class", "cure_oven_batch"),
    ("lithium battery hazardous store", "zone_kind", "li_ion_store"),
    ("magnet wire", "commodity_class", "copper_winding_wire"),
]


def _last(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def library_checks() -> None:
    print("==> Library in Postgres (P1)", flush=True)
    logs = sh([*COMPOSE, "logs", "--no-log-prefix", "migrate"], check=False, capture=True)
    out = logs.stdout + logs.stderr
    summary = [ln.strip() for ln in out.splitlines() if "catalog 2026" in ln or "pack KA" in ln]
    record(
        "migrate: catalog + pack validation passed, then loaded",
        "validation: 0 error(s)" in out and "Library validation failed" not in out,
        " | ".join(dict.fromkeys(summary))[:300],
    )
    rows = psql(
        "SELECT kind, scope, version, entry_count, verify_count FROM library_version "
        "ORDER BY kind, scope, version"
    )
    record(
        "library_version rows (catalog + KA pack)",
        "catalog|catalog|" in rows and "pack|KA|" in rows,
        rows.replace("\n", "; "),
    )
    counts = psql(
        "SELECT (SELECT count(*) FROM catalog_entry) || '|' || (SELECT count(*) FROM pack_value)"
        " || '|' || (SELECT count(*) FROM catalog_entry WHERE embedding IS NOT NULL)"
    )
    parts = counts.split("|")
    ok = len(parts) == 3 and parts[0] == parts[2] and int(parts[0]) >= 150 and int(parts[1]) >= 40
    record(
        "catalog entries and pack values stored, every entry embedded",
        ok,
        f"catalog_entry {parts[0]}, pack_value {parts[1]}" if len(parts) == 3 else counts,
    )
    plan = psql(
        "SET enable_seqscan = off; EXPLAIN SELECT code FROM catalog_entry ORDER BY embedding "
        "<=> (SELECT embedding FROM catalog_entry WHERE code = 'spot_weld_nickel' LIMIT 1) "
        "LIMIT 5"
    )
    used = "ix_catalog_entry_embedding" in plan
    record(
        "pgvector HNSW index serves nearest-neighbour search",
        used,
        "Index Scan using ix_catalog_entry_embedding" if used else plan[:200],
    )
    for query, kind, want in SEARCHES:
        p = compose_run(
            "api", "fl-library", "search", query, "--kind", kind, "--limit", "3", "--json"
        )
        try:
            lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
            hits = json.loads(lines[-1] if lines else "[]")
        except json.JSONDecodeError:
            hits = []
        top = hits[0]["code"] if hits else "none"
        record(
            f"search '{query}' ({kind}) → {want}",
            top == want,
            ", ".join(f"{h['code']} {h['score']:.2f}" for h in hits) or _last(p.stderr),
        )
    p = compose_run("api", "fl-library", "load")
    record(
        "reload of the same versions is a no-op",
        p.returncode == 0 and "unchanged" in p.stdout and ": loaded (" not in p.stdout,
        " | ".join(ln.strip() for ln in p.stdout.splitlines() if "unchanged" in ln)[:200]
        or _last(p.stderr),
    )
    p = compose_exec("api", SAME_VERSION_CHANGED)
    record(
        "same version with different content is refused",
        p.returncode == 0,
        _last(p.stdout + p.stderr),
    )
    p = compose_exec("api", BROKEN_LIBRARY)
    record(
        "broken pack (expired value) fails the loader, nothing loaded",
        p.returncode == 0,
        _last(p.stdout + p.stderr),
    )


def host_checks() -> None:
    print("==> Golden fixtures, negative controls, schema round-trip (host)", flush=True)
    out = ROOT / "e2e-results" / "p1-host.json"
    out.parent.mkdir(exist_ok=True)
    uv = shutil.which("uv") or "uv"
    p = sh(
        [uv, "run", "--frozen", "python", "scripts/e2e_p1.py", "--out", str(out)],
        check=False,
        capture=True,
    )
    try:
        host = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        host = []
        record("host checks ran", False, (p.stdout + p.stderr)[-300:])
    for r in host:
        record(r["check"], r["ok"], r["detail"])
    pnpm = shutil.which("pnpm") or "pnpm"
    p = sh([pnpm, "-C", "packages/ts-types", "typecheck"], check=False, capture=True)
    text = (p.stdout + p.stderr).strip()
    exported = next((ln for ln in text.splitlines() if "exported types" in ln), "")
    record(
        "schema round-trip: Pydantic → JSON Schema → TS compiles with real objects",
        p.returncode == 0,
        exported.split("(")[-1].rstrip(")") if p.returncode == 0 else text[-300:],
    )


def wait_ready(base: str, timeout: float) -> tuple[int, dict | str]:
    end = time.time() + timeout
    status, body = 0, ""
    while time.time() < end:
        try:
            status, body = http("GET", f"{base}/health/ready", timeout=5)
            if status == 200:
                return status, body
        except OSError:
            pass
        time.sleep(2)
    return status, body


QUEUE_ROUNDTRIP = """
import asyncio, sys
from fl_worker.queue import app, {task}
async def main():
    async with app.open_async():
        job_id = await {task}.defer_async()
        for _ in range(120):
            jobs = await app.job_manager.list_jobs_async(id=job_id)
            if not jobs:            # successful jobs are deleted (delete_jobs="successful")
                print("done"); return
            status = getattr(jobs[0].status, "value", jobs[0].status)
            if status == "succeeded":
                print("done"); return
            if status in ("failed", "cancelled", "aborted"):
                print("job", status); sys.exit(1)
            await asyncio.sleep(0.5)
        print("timeout"); sys.exit(1)
asyncio.run(main())
"""


def main() -> int:
    # Windows consoles default to cp1252; keep output readable everywhere.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--down", action="store_true")
    args = ap.parse_args()

    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
        print("Created .env from .env.example (placeholder values; git-ignored).")
    port = env_value("CADDY_HTTP_PORT", "8080")
    base = f"http://localhost:{port}"

    print("==> Starting stack" + (" (no build)" if args.no_build else " (build)"), flush=True)
    up = [*COMPOSE, "up", "-d", "--remove-orphans", "--wait", "--wait-timeout", "300"]
    up += [] if args.no_build else ["--build"]
    t0 = time.time()
    if sh(up, check=False).returncode != 0:
        record("compose up", False, "docker compose up failed")
        return finish(args)
    record("compose up", True, f"{time.time() - t0:.0f} s")

    print("==> Health", flush=True)
    status, body = wait_ready(base, timeout=180)
    s_live, live = http("GET", f"{base}/health")
    record("GET /health", s_live == 200 and isinstance(live, dict) and live.get("status") == "ok")
    checks = body.get("checks", {}) if isinstance(body, dict) else {}
    record("GET /health/ready", status == 200, f"HTTP {status}")
    for name in ("postgres", "redis", "worker", "cad-worker"):
        c = checks.get(name, {})
        extra = (
            c.get("pgvector")
            or c.get("error")
            or " ".join(str(c[k]) for k in ("arch", "host") if c.get(k))
        )
        record(f"  ready: {name}", bool(c.get("ok")), str(extra or ""))

    print("==> Projects (capability token)", flush=True)
    s, created = http("POST", f"{base}/projects", {"name": "E2E power bank"})
    token = created.get("token", "") if isinstance(created, dict) else ""
    record("POST /projects → 201 + 128-bit token", s == 201 and len(token) == 22, f"HTTP {s}")
    if token:
        s, cur = http("GET", f"{base}/projects/current", headers={"X-Project-Token": token})
        record(
            "token resolves the project",
            s == 200 and isinstance(cur, dict) and cur.get("name") == "E2E power bank",
        )
        s, _ = http("GET", f"{base}/projects/current", headers={"X-Project-Token": "A" * 22})
        record("unknown token → 404", s == 404)
        user, db = env_value("POSTGRES_USER", "factory"), env_value("POSTGRES_DB", "factory")
        q = sh(
            [
                *COMPOSE,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                user,
                "-d",
                db,
                "-tAc",
                f"SELECT token_hash FROM project WHERE id = '{created['id']}'",  # noqa: S608
            ],
            check=False,
            capture=True,
        )
        stored = q.stdout.strip()
        record(
            "DB stores SHA-256 hash, not the token",
            stored == hashlib.sha256(token.encode()).hexdigest() and token not in stored,
        )

    print("==> SSE", flush=True)
    events: list[str] = []
    try:
        req = urllib.request.Request(f"{base}/events/demo?interval_ms=30")  # noqa: S310
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            ctype = r.headers.get("content-type", "")
            for line in r:
                line = line.decode().strip()
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                    if events[-1] == "done":
                        break
        record(
            "GET /events/demo streams via Caddy",
            events.count("progress") == 7
            and events[-1] == "done"
            and ctype.startswith("text/event-stream"),
            f"{events.count('progress')} progress + {events[-1] if events else 'none'}",
        )
    except OSError as exc:
        record("GET /events/demo streams via Caddy", False, str(exc))

    print("==> Queue round-trip", flush=True)
    for service, task in (("worker", "ping"), ("cad-worker", "cad_ping")):
        p = compose_exec(service, QUEUE_ROUNDTRIP.format(task=task))
        record(
            f"job `{task}` completes on {service}",
            p.returncode == 0 and "done" in p.stdout,
            (p.stdout + p.stderr).strip()[-200:],
        )

    library_checks()
    host_checks()

    print("==> Golden cases (docs/14 §4)", flush=True)
    for gid, desc in GOLDEN.items():
        print(f"  [{GOLDEN_STATUS[gid].split(':')[0]}] {gid} {desc} — {GOLDEN_STATUS[gid]}")

    return finish(args)


def finish(args: argparse.Namespace) -> int:
    out = ROOT / "e2e-results"
    out.mkdir(exist_ok=True)
    ok = all(r["ok"] for r in results) and bool(results)
    summary = {
        "phase": PHASE,
        "ok": ok,
        "checks": results,
        "golden": GOLDEN_STATUS,
        "host": {"platform": sys.platform, "machine": os.environ.get("PROCESSOR_ARCHITECTURE", "")},
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    name = f"{PHASE.lower()}.json"
    (out / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    passed = sum(r["ok"] for r in results)
    print(f"\n{'PASSED' if ok else 'FAILED'}: {passed}/{len(results)} checks · e2e-results/{name}")
    if args.down:
        sh([*COMPOSE, "down"], check=False)
    else:
        print("Stack left running. Stop with: make down")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
