"""P2 end-to-end checks (docs/14 §5 P2 acceptance). Standard library only; called by e2e.py.

Runs the real pipeline through Caddy with recorded LLM responses (LLM_MODE=replay):

  1. G1 and G2 wizard samples run S1 to awaiting_approval; the ProductSpec validates, the
     archetype is the user's, and the variant parameters equal golden/*/params.yaml.
  2. G1 with the STEP sample, a datasheet PDF, a BoM CSV and a photo: assembly tree and GLB
     (glTF, metres), BoM column mapping by rules, bom_file parts, a datasheet conflict that
     becomes a question, answered → new version with source=user.
  3. Approving S1 runs the 12 stubs to approved placeholders; editing the approved S1 marks
     all 12 stale; "re-run stale" brings them back. A change set is previewed and applied.
  4. Budget exhaustion pauses S1 with a resumable state; continuing finishes it without
     repeating any model call.
  5. Negative controls: cassette miss, bad upload type, oversized upload, engines import no
     LLM code.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "golden" / "samples"
OUT = SAMPLES / "out"

Record = Callable[[str, bool, str], bool]


class Api:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base, self.token = base, token

    def call(
        self, method: str, path: str, body: Any = None, timeout: float = 30
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Project-Token", self.token)
        return _send(req, timeout)

    def upload(
        self,
        path: Path,
        name: str | None = None,
        raw: bytes | None = None,
        variants: str = "",
    ) -> tuple[int, Any]:
        boundary = uuid.uuid4().hex
        content = raw if raw is not None else path.read_bytes()
        fname = name or path.name
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + content
            + (
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="variants"\r\n\r\n'
                f"{variants}\r\n--{boundary}--\r\n"
            ).encode()
        )
        req = urllib.request.Request(
            f"{self.base}/projects/current/attachments", data=body, method="POST"
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        if self.token:
            req.add_header("X-Project-Token", self.token)
        return _send(req, 120)

    def raw(self, path: str) -> tuple[int, bytes]:
        req = urllib.request.Request(f"{self.base}{path}")
        if self.token:
            req.add_header("X-Project-Token", self.token)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


def _send(req: urllib.request.Request, timeout: float) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read().decode(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw


def _yaml_json(path: Path) -> Any:
    uv = shutil.which("uv") or "uv"
    code = (
        "import json,sys,yaml; "
        "print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))"
    )
    p = subprocess.run(
        [uv, "run", "--frozen", "python", "-c", code, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(p.stdout)


# ── helpers ───────────────────────────────────────────────────────────


def new_project(base: str, name: str) -> Api:
    s, body = Api(base).call("POST", "/projects", {"name": name})
    assert s == 201, body
    return Api(base, body["token"])


def stage_status(api: Api) -> dict[str, dict[str, Any]]:
    s, body = api.call("GET", "/projects/current/pipeline")
    return {x["stage"]: x for x in body["stages"]} if s == 200 else {}


def wait_for(
    api: Api, pred: Callable[[dict[str, dict[str, Any]]], bool], timeout: float = 240
) -> dict:
    end = time.time() + timeout
    st: dict[str, dict[str, Any]] = {}
    while time.time() < end:
        st = stage_status(api)
        if st and pred(st):
            return st
        time.sleep(1)
    return st


def intake_settled(st: dict) -> bool:
    return st.get("intake", {}).get("status") in (
        "awaiting_approval",
        "failed",
        "paused",
        "approved",
    )


def latest(api: Api, stage: str = "intake") -> dict[str, Any]:
    _, body = api.call("GET", f"/projects/current/stages/{stage}")
    return body["latest"] or {}


def submit(api: Api, template: dict, budgets: dict | None = None) -> tuple[int, Any]:
    s, body = api.call("PUT", "/projects/current/template", template)
    if s != 200:
        return s, body
    return api.call("POST", "/projects/current/submit", {"stage_budgets": budgets or {}})


def params_of(run: dict) -> dict[str, dict[str, Any]]:
    variants = ((run.get("artifact") or {}).get("product") or {}).get("variants", [])
    return {v["code"]: v["params"] for v in variants}


def sse_has(api: Api, kinds: set[str], timeout: float = 10) -> list[str]:
    """Read the project's event stream (replayed from the start) and list event names."""
    req = urllib.request.Request(f"{api.base}/projects/current/events?after=0")
    req.add_header("X-Project-Token", api.token or "")
    seen: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                t = line.decode().strip()
                if t.startswith("event:"):
                    seen.append(t.split(":", 1)[1].strip())
                    if kinds <= set(seen):
                        break
    except OSError:
        pass
    return seen


def glb_ok(data: bytes) -> tuple[bool, str]:
    if len(data) < 20 or data[:4] != b"glTF":
        return False, "not a GLB"
    jlen = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20 : 20 + jlen])
    return True, f"{len(doc.get('meshes', []))} meshes, {len(data)} bytes"


# ── sample files ──────────────────────────────────────────────────────


def make_samples(compose: list[str], record: Record) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv") or "uv"
    ok = True
    p = subprocess.run(
        [uv, "run", "--frozen", "python", "golden/samples/make_samples.py", "host", str(OUT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    ok &= record(
        "samples: BoM CSV and photo (host)",
        p.returncode == 0,
        "" if p.returncode == 0 else (p.stderr or "")[-200:],
    )
    for what, service in (("step", "cad-worker"), ("pdf", "worker")):
        p = subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "-v",
                f"{SAMPLES.as_posix()}:/samples:ro",
                "-v",
                f"{OUT.as_posix()}:/out",
                "--entrypoint",
                "python",
                service,
                "/samples/make_samples.py",
                what,
                "/out",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        ok &= record(
            f"samples: {what} (in the {service} image)",
            p.returncode == 0,
            "" if p.returncode == 0 else (p.stderr or "")[-200:],
        )
    names = sorted(f.name for f in OUT.iterdir())
    record(
        "samples present",
        {"pb10k.step", "fan1200.step", "pb10k-datasheet.pdf", "pb-bom.csv", "pb-photo.png"}
        <= set(names),
        ", ".join(names),
    )
    return ok


# ── the checks ────────────────────────────────────────────────────────


def p2_checks(
    base: str, compose: list[str], psql: Callable[[str], str], record: Record, mode: str
) -> None:
    print(f"==> P2 pipeline (LLM_MODE={mode})", flush=True)
    s, ready = Api(base).call("GET", "/health/ready")
    agents = (ready or {}).get("checks", {}).get("agents", {}) if isinstance(ready, dict) else {}
    record(
        "ready: agents (pipeline worker)",
        bool(agents.get("ok")),
        f"llm_mode={agents.get('llm_mode')}",
    )
    reg = psql(
        "SELECT to_regclass('checkpoints') IS NOT NULL, to_regclass('stage_run') IS NOT NULL"
    )
    record("LangGraph checkpoint tables and stage_run exist", reg == "t|t", reg)

    make_samples(compose, record)
    golden = {
        "G1": (
            _yaml_json(ROOT / "golden/power_bank/template.yaml"),
            _yaml_json(ROOT / "golden/power_bank/params.yaml"),
        ),
        "G2": (
            _yaml_json(ROOT / "golden/ceiling_fan/template.yaml"),
            _yaml_json(ROOT / "golden/ceiling_fan/params.yaml"),
        ),
    }

    # 1. G1 and G2 wizard samples (text only).
    apis: dict[str, Api] = {}
    for gid, (tpl, params) in golden.items():
        api = new_project(base, f"E2E {gid}")
        apis[gid] = api
        s, body = submit(api, tpl)
        record(f"{gid}: template saved and submitted", s == 200, str(body)[:120])
        st = wait_for(api, intake_settled)
        status = st.get("intake", {}).get("status")
        run = latest(api)
        record(
            f"{gid}: S1 intake → awaiting_approval",
            status == "awaiting_approval",
            f"status={status}; issues={[i['message'] for i in run.get('issues', [])][:2]}",
        )
        arche = ((run.get("artifact") or {}).get("product") or {}).get("archetype", {})
        record(
            f"{gid}: archetype {params['archetype']} (source user)",
            arche.get("value") == params["archetype"] and arche.get("source") == "user",
            str(arche)[:120],
        )
        got = params_of(run)
        diffs = [
            f"{code}.{k}: {got.get(code, {}).get(k, {}).get('value')} != {v}"
            for code, vals in params["variants"].items()
            for k, v in vals.items()
            if abs(float(got.get(code, {}).get(k, {}).get("value", -1)) - float(v)) > 1e-9
        ]
        sources = sorted({p["source"] for vals in got.values() for p in vals.values()})
        record(
            f"{gid}: variant parameters equal golden params.yaml",
            not diffs and bool(got),
            "; ".join(diffs[:4]) or f"sources: {', '.join(sources)}",
        )
        lib_params = {
            (c, k) for c, vals in got.items() for k, p in vals.items() if p["source"] == "library"
        }
        assumed = {a["path"] for a in run.get("assumptions", [])}
        record(
            f"{gid}: catalog-default parameters are in the assumptions register",
            all(any(f"/params/{k}" in a for a in assumed) for _, k in lib_params),
            f"{len(lib_params)} catalog defaults, {len(run.get('assumptions', []))} assumptions",
        )
        _, usage = api.call("GET", "/projects/current/llm-usage")
        rows = usage or []
        statuses = sorted({u["status"] for u in rows})
        total = sum(u["prompt_tokens"] + u["completion_tokens"] for u in rows)
        want = "replay" if mode == "replay" else "record"
        record(
            f"{gid}: model calls are {want}ed and within the 30k intake budget",
            bool(rows) and statuses == [want] and total <= 30_000,
            f"{sum(u['calls'] for u in rows)} calls, {total} tokens, status {statuses}",
        )
    seen = sse_has(apis["G1"], {"progress", "status"})
    record(
        "SSE (token header): progress and status events for G1",
        "progress" in seen and "status" in seen,
        f"{len(seen)} events",
    )

    # 2. G1 with files.
    tpl, _ = golden["G1"]
    api = new_project(base, "E2E G1 files")
    atts: list[dict] = []
    uploaded: dict[str, dict] = {}
    for fname in ("pb10k.step", "pb10k-datasheet.pdf", "pb-bom.csv", "pb-photo.png"):
        s, a = api.upload(OUT / fname, variants="PB10K,PB20K")
        ok = s == 201 and isinstance(a, dict)
        record(f"upload {fname}", ok, f"HTTP {s} {a if not ok else a['kind']}")
        if ok:
            uploaded[fname] = a
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
    bom = uploaded.get("pb-bom.csv", {}).get("bom") or {}
    prop = bom.get("proposal") or {}
    record(
        "BoM column mapping proposed by rules (part no, name, per-variant qty)",
        prop.get("part_no") == "Part No"
        and prop.get("name") == "Description"
        and prop.get("variant_qty") == {"PB10K": "Qty PB10K", "PB20K": "Qty PB20K"}
        and bom.get("mapping_source") == "rules",
        json.dumps(prop)[:160],
    )
    s, body = submit(api, {**tpl, "attachments": atts, "bom_hint": "have_full"})
    record("G1+files: submitted", s == 200, str(body)[:120])
    st = wait_for(api, intake_settled, timeout=300)
    run = latest(api)
    record(
        "G1+files: S1 → awaiting_approval",
        st.get("intake", {}).get("status") == "awaiting_approval",
        f"status={st.get('intake', {}).get('status')}; notices={run.get('notices', [])[:2]}",
    )
    step = uploaded.get("pb10k.step", {})
    _, att = api.call("GET", f"/projects/current/attachments/{step.get('id')}")
    cad = (att or {}).get("cad") or {}
    leaves: list[str] = []

    def walk(n: dict) -> None:
        if not n.get("children"):
            leaves.append(n["name"])
        for c in n.get("children", []):
            walk(c)

    if cad.get("tree"):
        walk(cad["tree"])
    record(
        "STEP → assembly tree with the 5 named parts",
        sorted(leaves) == ["cell_1", "cell_2", "pcba", "shell", "usb_c"],
        ", ".join(sorted(leaves)),
    )
    parts = {p["name"]: p for p in cad.get("parts", [])}
    sizes = {k: sorted(round(x, 1) for x in v["bbox_mm"]) for k, v in parts.items()}
    record(
        "STEP → part bboxes exact; identical cells counted (qty 2)",
        sizes.get("shell") == [24.0, 75.0, 150.0]
        and sizes.get("cell") == [18.0, 18.0, 65.0]
        and parts.get("cell", {}).get("count") == 2,
        json.dumps(sizes)[:160],
    )
    s, glb = api.raw(f"/projects/current/attachments/{step.get('id')}/glb")
    ok, detail = glb_ok(glb) if s == 200 else (False, f"HTTP {s}")
    ext = cad.get("extents_m") or []
    record(
        "GLB served and scaled to metres (0.001)",
        ok and bool(ext) and max(ext) < 1.0 and abs(max(ext) - 0.15) < 0.01,
        f"{detail}; extents_m {ext}",
    )
    spec_parts = ((run.get("artifact") or {}).get("structure") or {}).get("parts", [])
    by_src: dict[str, int] = {}
    for p in spec_parts:
        by_src[p["qty"]["source"]] = by_src.get(p["qty"]["source"], 0) + 1
    record(
        "ProductSpec parts from CAD and the BoM file (source cad / bom_file)",
        by_src.get("cad", 0) == 4 and by_src.get("bom_file", 0) == bom.get("row_count"),
        json.dumps(by_src),
    )
    photo_ok = any(
        k.startswith("photo_") for k in ((run.get("artifact") or {}).get("extensions") or {})
    ) or any("photo" in n.lower() for n in run.get("notices", []))
    record(
        "photo processed by the vision role (or skipped with a notice)",
        photo_ok,
        "; ".join(n for n in run.get("notices", []) if "photo" in n.lower())[:120] or "vision used",
    )
    qs = [
        q
        for q in run.get("questions", [])
        if q.get("path") == "/product/variants/0/params/capacity_mah"
    ]
    record(
        "datasheet 9,000 mAh vs text 10,000 mAh → a choice question",
        bool(qs) and qs[0]["kind"] == "choice" and len(qs[0]["options"]) >= 2,
        json.dumps(qs[0]["options"] if qs else [q["text"] for q in run.get("questions", [])])[:200],
    )
    if qs:
        q = qs[0]
        pick = next(
            (
                o
                for o, c in zip(q["options"], q["candidates"], strict=False)
                if float(c["value"]) == 10000.0
            ),
            q["options"][0],
        )
        v1 = run["version"]
        s, body = api.call(
            "POST", "/projects/current/stages/intake/answer", {"answers": {q["id"]: pick}}
        )
        st = wait_for(
            api,
            lambda st: (
                st["intake"]["version"]
                and st["intake"]["version"] > v1
                and st["intake"]["status"] == "awaiting_approval"
            ),
        )
        run2 = latest(api)
        cap = params_of(run2).get("PB10K", {}).get("capacity_mah", {})
        record(
            "answer → new version, value from the user",
            run2.get("version", 0) > v1
            and cap.get("value") == 10000
            and cap.get("source") == "user",
            f"v{run2.get('version')}: {cap}",
        )

    # 3. Approve G1 → stubs; edit → stale; re-run; change set.
    api = apis["G1"]
    s, body = api.call("POST", "/projects/current/stages/intake/approve", {})
    record("approve S1 (G1)", s == 200, str(body)[:100])
    st = wait_for(api, lambda st: all(x["status"] == "approved" for x in st.values()), timeout=240)
    placeholders = sum(1 for k, x in st.items() if k != "intake" and x["placeholder"])
    record(
        "all 13 stages approved; the 12 stubs are placeholders",
        all(x["status"] == "approved" for x in st.values()) and placeholders == 12,
        ", ".join(f"{k}:{x['status']}" for k, x in st.items() if x["status"] != "approved")
        or "13/13 approved",
    )
    run = latest(api)
    vol = ((run["artifact"]["product"]["demand"] or {}).get("annual_volume") or {}).get("value")
    s, body = api.call(
        "POST",
        "/projects/current/stages/intake/edit",
        {
            "version": run["version"],
            "approve": True,
            "reason": "e2e: volume change",
            "edits": [
                {"path": "/product/demand/annual_volume", "value": int(vol or 300000) + 10000}
            ],
        },
    )
    st = stage_status(api)
    stale = sorted(k for k, x in st.items() if x["status"] == "stale")
    record(
        "editing approved S1 → new approved version; all 12 downstream stubs stale",
        s == 200
        and body.get("status") == "approved"
        and body["artifact"]["product"]["demand"]["annual_volume"]["source"] == "user"
        and len(stale) == 12,
        f"S1 v{body.get('version') if isinstance(body, dict) else '?'}; stale={len(stale)}",
    )
    s, body = api.call("POST", "/projects/current/rerun-stale", {})
    st = wait_for(api, lambda st: all(x["status"] == "approved" for x in st.values()), timeout=240)
    record(
        "re-run stale: S1 skipped (hash unchanged), stubs approved again as v2",
        s == 200
        and st["intake"]["version"] == 2
        and all(
            x["status"] == "approved" and x["version"] == 2 for k, x in st.items() if k != "intake"
        ),
        ", ".join(f"{k}:v{x['version']}" for k, x in list(st.items())[:4]),
    )
    run = latest(api)
    old = run["artifact"]["product"]["variants"][0]["params"]["n_screws"]["value"]
    op = {
        "op": "replace",
        "path": "/product/variants/0/params/n_screws/value",
        "old": old,
        "new": old + 1,
    }
    s, cs = api.call(
        "POST",
        "/projects/current/change-sets",
        {
            "stage": "intake",
            "base_version": run["version"],
            "ops": [op],
            "reason": "e2e change set",
        },
    )
    record(
        "change set preview: validated, diff shown",
        s == 201
        and cs["status"] == "proposed"
        and any(d["path"].endswith("n_screws/value") for d in cs["diff"]),
        json.dumps(cs.get("diff") if isinstance(cs, dict) else cs)[:160],
    )
    if s == 201:
        s2, applied = api.call("POST", f"/projects/current/change-sets/{cs['id']}/apply", {})
        record(
            "change set applied → new version; downstream stale",
            s2 == 200 and applied["version"] == run["version"] + 1 and len(applied["stale"]) == 12,
            str(applied)[:120],
        )
    s, bad = api.call(
        "POST",
        "/projects/current/change-sets",
        {
            "stage": "intake",
            "base_version": run["version"],
            "reason": "stale old",
            "ops": [
                {
                    "op": "replace",
                    "path": "/product/variants/0/params/n_screws/value",
                    "old": 999,
                    "new": 1,
                }
            ],
        },
    )
    record("change set with a stale `old` value is rejected", s == 422, str(bad)[:120])

    # 4. Budget exhaustion → paused → continue.
    tpl2, _ = golden["G2"]
    api = new_project(base, "E2E G2 budget")
    submit(api, tpl2, {"intake": 400})
    st = wait_for(api, intake_settled)
    run = latest(api)
    tid = psql(f"SELECT thread_id FROM stage_run WHERE id = '{run.get('id')}'")
    ckpt = psql(f"SELECT count(*) FROM checkpoints WHERE thread_id = '{tid}'")
    record(
        "budget exhausted → S1 paused with a checkpoint (resumable)",
        st["intake"]["status"] == "paused" and int(ckpt or 0) > 0,
        f"status={st['intake']['status']}, checkpoints={ckpt}",
    )
    s, _ = api.call("POST", "/projects/current/stages/intake/resume", {"extra_budget": 30000})
    st = wait_for(api, lambda st: st["intake"]["status"] in ("awaiting_approval", "failed"))
    calls = psql(
        "SELECT count(*) || '|' || count(DISTINCT cassette_key) FROM llm_call lc "
        "JOIN stage_run sr ON sr.id = lc.stage_run_id "
        f"WHERE sr.thread_id = '{tid}' AND lc.status IN ('replay','record','ok')"
    )
    n, distinct = [*calls.split("|"), "", ""][:2]
    record(
        "continue → awaiting_approval; no model call repeated",
        s == 200 and st["intake"]["status"] == "awaiting_approval" and n == distinct and n != "0",
        f"status={st['intake']['status']}; calls {n}, distinct {distinct}",
    )

    # 5. Negative controls.
    if mode == "replay":
        api = new_project(base, "E2E cassette miss")
        bad_tpl = json.loads(json.dumps(golden["G2"][0]))
        bad_tpl["product"]["description"] += " Changed after recording."
        submit(api, bad_tpl)
        st = wait_for(api, intake_settled)
        run = latest(api)
        record(
            "negative: changed prompt in replay → stage failed with 'cassette miss'",
            st["intake"]["status"] == "failed"
            and any("cassette miss" in i["message"] for i in run.get("issues", [])),
            "; ".join(i["message"] for i in run.get("issues", []))[:160],
        )
    api = new_project(base, "E2E uploads")
    s, body = api.upload(OUT / "pb-bom.csv", name="fake.pdf")
    record("negative: wrong file content for its type → 415", s == 415, str(body)[:100])
    s, body = api.upload(
        OUT / "pb-photo.png",
        name="huge.png",
        raw=b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024 + 10),
    )
    record("negative: image over 10 MB → 413", s == 413, str(body)[:100])
    hits = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "packages/engines/src").rglob("*.py")
        if any(w in p.read_text(encoding="utf-8") for w in ("fl_agents", "litellm", "langgraph"))
    ]
    record("engines import no agent or LLM code (CLAUDE.md rule 1)", not hits, ", ".join(hits))


def web_checks(record: Record) -> None:
    print("==> Web app (typecheck, lint, build)", flush=True)
    pnpm = shutil.which("pnpm") or "pnpm"
    for script in ("typecheck", "lint", "build"):
        p = subprocess.run(
            [pnpm, "-C", "apps/web", script], cwd=ROOT, capture_output=True, text=True, check=False
        )
        text = (p.stdout + p.stderr).strip()
        record(f"web {script}", p.returncode == 0, text.splitlines()[-1][:160] if text else "")
