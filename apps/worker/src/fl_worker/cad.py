"""CAD extraction, run as a child process of the cad-worker (docs/03 §3.1, docs/11 §2).

``python -m fl_worker.cad <kind> <input> <out.glb>`` prints one JSON object:
``{"tree": AssemblyNode, "parts": [...], "solids": n, "extents_m": [x, y, z]}``.

The parent sets CPU-time and memory limits and a wall-clock timeout; the container has no
network, a read-only root and a tmpfs /tmp. The GLB is scaled by 0.001 (OCCT writes
millimetres; glTF is metres, P0 spike a).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

MM_TO_M = 0.001
_SUFFIX = re.compile(r"[\s_\-.]*\(?\d+\)?$")


def _local_id(text: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.\-/]+", "-", text.strip()).strip("-./")[:80]
    return s if s and s[0].isalnum() else fallback


def _shape_of(obj: Any) -> Any:
    import cadquery as cq

    if isinstance(obj, cq.Workplane):
        vals = obj.vals()
        return cq.Compound.makeCompound([v for v in vals if isinstance(v, cq.Shape)])
    return obj


def _solid_info(shape: Any) -> dict[str, Any]:
    bb = shape.BoundingBox()
    return {
        "bbox_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
        "volume_mm3": round(float(shape.Volume()), 1),
        "area_mm2": round(float(shape.Area()), 1),
    }


def _scale_glb(path: Path) -> list[float]:
    import trimesh

    scene = trimesh.load(str(path), force="scene")
    scene.apply_scale(MM_TO_M)
    scene.export(str(path))
    return [round(float(x), 5) for x in scene.extents]


def extract_step(src: Path, glb: Path) -> dict[str, Any]:
    import cadquery as cq

    assy = cq.Assembly.importStep(str(src))
    leaves: list[dict[str, Any]] = []
    counter = {"n": 0}

    def walk(a: Any) -> dict[str, Any]:
        counter["n"] += 1
        node: dict[str, Any] = {
            "id": f"N{counter['n']:03d}",
            "name": str(a.name or f"node {counter['n']}")[:200],
            "children": [],
            "part_no": None,
        }
        if a.obj is not None:
            shape = _shape_of(a.obj)
            solids = shape.Solids() if hasattr(shape, "Solids") else []
            if solids:
                info = _solid_info(shape)
                leaves.append({"name": node["name"], **info, "solids": len(solids)})
                node["leaf"] = len(leaves) - 1
        for child in a.children:
            node["children"].append(walk(child))
        return node

    tree = walk(assy)
    if not leaves:  # plain STEP without assembly structure
        shape = cq.importers.importStep(str(src))
        for i, solid in enumerate(shape.solids().vals(), start=1):
            leaves.append({"name": f"solid {i}", **_solid_info(solid), "solids": 1})
            tree["children"].append(
                {
                    "id": f"S{i:03d}",
                    "name": f"solid {i}",
                    "children": [],
                    "part_no": None,
                    "leaf": len(leaves) - 1,
                }
            )
        assy = cq.Assembly(shape, name="model")

    # Group identical parts: same base name (cell_1, cell_2 → cell) and same geometry.
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    leaf_part: dict[int, int] = {}
    for i, leaf in enumerate(leaves):
        base = _SUFFIX.sub("", leaf["name"]) or leaf["name"]
        sig = (base.lower(), tuple(sorted(leaf["bbox_mm"])), round(leaf["volume_mm3"]))
        if sig not in groups:
            groups[sig] = {**leaf, "name": base, "count": 0, "instances": []}
        groups[sig]["count"] += 1
        groups[sig]["instances"].append(leaf["name"])
        leaf_part[i] = list(groups).index(sig)
    parts = list(groups.values())

    def link(node: dict[str, Any]) -> dict[str, Any]:
        leaf = node.pop("leaf", None)
        if leaf is not None:
            node["part_no"] = f"P{leaf_part[leaf] + 1:03d}"
        node["children"] = [link(c) for c in node["children"]]
        return node

    tree = link(tree)
    for i, p in enumerate(parts, start=1):
        p["ref"] = f"P{i:03d}"

    assy.export(str(glb), tolerance=0.1, angularTolerance=0.2)
    extents = _scale_glb(glb)
    return {
        "tree": tree,
        "parts": parts,
        "solids": sum(p["solids"] for p in leaves),
        "extents_m": extents,
        "units": "mm (bbox); m (GLB)",
    }


def extract_stl(src: Path, glb: Path) -> dict[str, Any]:
    import trimesh

    mesh = trimesh.load(str(src), force="mesh")
    ext = [round(float(x), 3) for x in mesh.extents]
    part = {
        "name": src.stem[:200] if src.stem else "mesh",
        "bbox_mm": ext,
        "volume_mm3": round(float(abs(mesh.volume)), 1) if mesh.is_watertight else 0.0,
        "area_mm2": round(float(mesh.area), 1),
        "count": 1,
        "solids": 1,
        "ref": "P001",
    }
    mesh.apply_scale(MM_TO_M)
    mesh.export(str(glb))
    tree = {"id": "N001", "name": part["name"], "children": [], "part_no": "P001"}
    return {
        "tree": tree,
        "parts": [part],
        "solids": 1,
        "extents_m": [round(x * MM_TO_M, 5) for x in ext],
        "units": "mm (bbox); m (GLB)",
    }


def main() -> int:
    kind, src, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    result = extract_step(src, out) if kind == "step" else extract_stl(src, out)
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
