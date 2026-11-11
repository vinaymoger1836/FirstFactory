"""Spike b: OR-Tools CP-SAT and WeasyPrint inside the worker image, on amd64 and arm64.

CP-SAT solves SALBP-1 for Jackson's 11-task instance (Scholl data set) at cycle time 10
with a time limit and 2 workers (the VM has 2 OCPU). WeasyPrint renders a two-page PDF.

    docker run --rm --platform linux/arm64 --network none \
      -v ./spikes/solvers:/spike:ro factory-worker:<tag> python /spike/run.py
"""

import io
import json
import math
import platform
import resource
import time
from importlib.metadata import version

res: dict = {"arch": platform.machine(), "python": platform.python_version()}

# ── CP-SAT: SALBP-1, Jackson (n=11) ────────────────────────────────
t0 = time.perf_counter()
from ortools.sat.python import cp_model  # noqa: E402

res["import_ortools_s"] = round(time.perf_counter() - t0, 3)
# Jackson instance (Scholl 1993 data set): task times and precedence arcs (1-based).
TIMES = [6, 2, 5, 7, 1, 2, 3, 6, 5, 5, 4]
ARCS = [(1, 2), (1, 3), (1, 4), (1, 5), (2, 6), (3, 7), (4, 7), (5, 7), (6, 8), (7, 9),
        (8, 10), (9, 11), (10, 11)]  # fmt: skip


def salbp1(cycle: int) -> dict:
    n = len(TIMES)
    m_max = n
    model = cp_model.CpModel()
    x = {(i, k): model.new_bool_var(f"x{i}_{k}") for i in range(n) for k in range(m_max)}
    st = [model.new_int_var(0, m_max - 1, f"s{i}") for i in range(n)]
    used = [model.new_bool_var(f"u{k}") for k in range(m_max)]
    for i in range(n):
        model.add_exactly_one(x[i, k] for k in range(m_max))
        model.add(st[i] == sum(k * x[i, k] for k in range(m_max)))
    for k in range(m_max):
        model.add(sum(TIMES[i] * x[i, k] for i in range(n)) <= cycle * used[k])
        if k:
            model.add(used[k] <= used[k - 1])
    for a, b in ARCS:
        model.add(st[a - 1] <= st[b - 1])
    model.minimize(sum(used))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_workers = 2
    t = time.perf_counter()
    status = solver.solve(model)
    stations = int(solver.objective_value)
    assign = [int(solver.value(s)) for s in st]
    loads = [sum(TIMES[i] for i in range(n) if assign[i] == k) for k in range(stations)]
    return {
        "cycle": cycle,
        "status": solver.status_name(status),
        "stations": stations,
        "lower_bound": math.ceil(sum(TIMES) / cycle),
        "station_loads": loads,
        "precedence_ok": all(assign[a - 1] <= assign[b - 1] for a, b in ARCS),
        "solve_s": round(time.perf_counter() - t, 3),
    }


res["cpsat"] = [salbp1(c) for c in (7, 10, 13)]
res["ortools"] = version("ortools")

# ── WeasyPrint ────────────────────────────────────────────────────
t0 = time.perf_counter()
from weasyprint import HTML  # noqa: E402

res["import_weasyprint_s"] = round(time.perf_counter() - t0, 3)
rows = "".join(f"<tr><td>OP-{i:03d}</td><td>{t} s</td></tr>" for i, t in enumerate(TIMES * 6, 1))
html = f"""<html><head><style>
@page {{ size: A4; margin: 18mm; @bottom-right {{ content: counter(page) " / " counter(pages); }} }}
body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 10pt; }}
table {{ border-collapse: collapse; width: 100%; }} td {{ border: 1px solid #999; padding: 4px; }}
h1 {{ color: #2f5bea; }}</style></head>
<body><h1>Spike report ₹ · m² · kVA</h1><table>{rows * 2}</table></body></html>"""
t = time.perf_counter()
doc = HTML(string=html).render()
buf = io.BytesIO()
doc.write_pdf(buf)
res["weasyprint"] = {
    "version": version("weasyprint"),
    "pages": len(doc.pages),
    "pdf_bytes": buf.tell(),
    "pdf_magic_ok": buf.getvalue()[:5] == b"%PDF-",
    "render_s": round(time.perf_counter() - t, 3),
}
res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
print("SPIKE_RESULT " + json.dumps(res))
