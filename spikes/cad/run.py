"""Spike a: CadQuery/OCP inside the cad-worker image, on amd64 and arm64.

Builds a small multi-solid "power bank" assembly, writes it as STEP, then — as a fresh load —
reads the STEP back, lists solids + bounding boxes, and exports GLB. The GLB is validated with
trimesh. Run (per arch):

    docker run --rm --platform linux/arm64 --network none --read-only --tmpfs /tmp \
      -v ./spikes/cad:/spike:ro -v ./spikes/cad/out:/out factory-cad-worker:<tag> \
      python /spike/run.py
"""

import json
import platform
import resource
import time
from importlib.metadata import version
from pathlib import Path

OUT = Path("/out") if Path("/out").exists() else Path("/tmp/out")
OUT.mkdir(parents=True, exist_ok=True)
arch = platform.machine()
res: dict = {"arch": arch, "python": platform.python_version()}
timings: dict[str, float] = {}


def tick(label: str, t0: float) -> float:
    timings[label] = round(time.perf_counter() - t0, 3)
    return time.perf_counter()


t = time.perf_counter()
import cadquery as cq  # noqa: E402
import OCP  # noqa: E402

t = tick("import_cadquery_s", t)
res["versions"] = {
    "cadquery": version("cadquery"),
    "cadquery-ocp": version("cadquery-ocp"),
    "ocp_module": getattr(OCP, "__version__", "n/a"),
    "trimesh": version("trimesh"),
}

# ── 1. sample STEP (mm): shell, PCB, two 18650-size cells, USB-C block ──
shell = cq.Workplane("XY").box(150, 75, 24).faces(">Z").shell(-1.6)
pcb = cq.Workplane("XY").box(60, 40, 1.6).translate((40, 0, -6))
cell = cq.Workplane("XY").cylinder(65, 9).rotate((0, 0, 0), (0, 1, 0), 90)
port = cq.Workplane("XY").box(9, 3.5, 3.2).translate((73, 0, -4))
assy = (
    cq.Assembly(name="PB10K")
    .add(shell, name="shell", color=cq.Color(0.15, 0.15, 0.18))
    .add(pcb, name="pcba", color=cq.Color(0.1, 0.5, 0.2))
    .add(cell, name="cell_1", loc=cq.Location((-30, -18, 0)), color=cq.Color(0.2, 0.3, 0.8))
    .add(cell, name="cell_2", loc=cq.Location((-30, 18, 0)), color=cq.Color(0.2, 0.3, 0.8))
    .add(port, name="usb_c", color=cq.Color(0.7, 0.7, 0.7))
)
step_path = OUT / f"sample-{arch}.step"
assy.export(str(step_path))
t = tick("write_step_s", t)
res["step_bytes"] = step_path.stat().st_size

# ── 2. load STEP fresh, list solids + bboxes ──
loaded = cq.importers.importStep(str(step_path))
solids = loaded.solids().vals()
res["solids"] = [
    {
        "i": i,
        "bbox_mm": [round(v, 2) for v in (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)],
        "size_mm": [round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)],
        "volume_mm3": round(s.Volume(), 1),
    }
    for i, s in enumerate(solids)
    for bb in [s.BoundingBox()]
]
overall = loaded.val().BoundingBox()
res["overall_size_mm"] = [round(overall.xlen, 2), round(overall.ylen, 2), round(overall.zlen, 2)]
t = tick("load_step_and_list_s", t)

# Names survive via the XCAF reader (needed for the assembly tree in P2).
try:
    named = cq.Assembly.importStep(str(step_path))
    res["xcaf_names"] = sorted(c.name for c in named.children)
    t = tick("load_step_xcaf_s", t)
except Exception as exc:  # cadquery API differences are a finding, not a crash
    res["xcaf_names"] = f"unavailable: {type(exc).__name__}: {exc}"[:200]
    named = None

# ── 3. export GLB ──
glb_path = OUT / f"sample-{arch}.glb"
src = named if isinstance(named, cq.Assembly) else cq.Assembly().add(loaded, name="part")
src.export(str(glb_path), tolerance=0.1, angularTolerance=0.2)
t = tick("export_glb_s", t)
res["glb_bytes"] = glb_path.stat().st_size

import trimesh  # noqa: E402

scene = trimesh.load(str(glb_path))
res["glb_check"] = {
    "geometries": len(scene.geometry),
    "triangles": int(sum(len(g.faces) for g in scene.geometry.values())),
    "extents": [round(float(v), 2) for v in scene.extents],
    "note": "extents show OCCT keeps model units (mm) and swaps to Y-up; glTF expects metres, so the viewer/exporter must scale by 0.001",
}
tick("validate_glb_s", t)
res["timings"] = timings
res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
print("SPIKE_RESULT " + json.dumps(res))
