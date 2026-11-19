"""Deterministic sample files for the P2 end-to-end check and the manual wizard check.

Generated at e2e time into ``golden/samples/out/`` (git-ignored; owner's rule 1):

    step  → pb10k.step (5 named parts, as in P0 spike a), fan1200.step   [cad-worker image]
    pdf   → pb10k-datasheet.pdf (states 9,000 mAh: conflicts with the wizard's 10,000)  [worker image]
    host  → pb-bom.csv, fan-bom.csv (golden BoMs) and *-bom-partial.csv (G8 b), pb-photo.png [host]

Usage: python golden/samples/make_samples.py {step|pdf|host} OUT_DIR
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

DATASHEET_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@page { size: A4; margin: 18mm; } body { font-family: 'DejaVu Sans', sans-serif; font-size: 11pt; }
h1 { font-size: 18pt; } table { border-collapse: collapse; } td { padding: 3pt 10pt 3pt 0; }
</style></head><body>
<h1>PB10K power bank: product datasheet</h1>
<p>Model PB10K. Portable charger built from cylindrical Li-ion cells with a BMS and charge board.</p>
<table>
<tr><td>Model</td><td>PB10K</td></tr>
<tr><td>Rated capacity</td><td>9,000 mAh</td></tr>
<tr><td>Cells</td><td>2 x 21700 Li-ion</td></tr>
<tr><td>Charge current</td><td>5000 mA</td></tr>
<tr><td>Output ports</td><td>3 (2 x USB-A, 1 x USB-C)</td></tr>
<tr><td>Indicator LEDs</td><td>4</td></tr>
<tr><td>Enclosure</td><td>PC/ABS, 4 screws</td></tr>
<tr><td>Weight</td><td>210 g</td></tr>
</table>
</body></html>"""


def make_step(out: Path) -> None:
    import cadquery as cq

    shell = cq.Workplane("XY").box(150, 75, 24).faces(">Z").shell(-1.6)
    pcb = cq.Workplane("XY").box(60, 40, 1.6).translate((40, 0, -6))
    cell = cq.Workplane("XY").cylinder(65, 9).rotate((0, 0, 0), (0, 1, 0), 90)
    port = cq.Workplane("XY").box(9, 3.5, 3.2).translate((73, 0, -4))
    pb = (
        cq.Assembly(name="PB10K")
        .add(shell, name="shell", color=cq.Color(0.15, 0.15, 0.18))
        .add(pcb, name="pcba", color=cq.Color(0.1, 0.5, 0.2))
        .add(cell, name="cell_1", loc=cq.Location((-30, -18, 0)), color=cq.Color(0.2, 0.3, 0.8))
        .add(cell, name="cell_2", loc=cq.Location((-30, 18, 0)), color=cq.Color(0.2, 0.3, 0.8))
        .add(port, name="usb_c", color=cq.Color(0.7, 0.7, 0.7))
    )
    pb.export(str(out / "pb10k.step"))

    canopy = cq.Workplane("XY").cylinder(60, 70)
    rod = cq.Workplane("XY").cylinder(300, 10)
    shell_r = cq.Workplane("XY").cylinder(60, 110)
    stator = cq.Workplane("XY").cylinder(30, 60)
    blade = cq.Workplane("XY").box(450, 110, 4).translate((330, 0, 0))
    fan = (
        cq.Assembly(name="F1200")
        .add(canopy, name="canopy", loc=cq.Location((0, 0, 400)))
        .add(rod, name="down_rod", loc=cq.Location((0, 0, 220)))
        .add(shell_r, name="rotor_shell")
        .add(stator, name="stator")
        .add(blade, name="blade_1", loc=cq.Location((0, 0, 0), (0, 0, 1), 0))
        .add(blade, name="blade_2", loc=cq.Location((0, 0, 0), (0, 0, 1), 120))
        .add(blade, name="blade_3", loc=cq.Location((0, 0, 0), (0, 0, 1), 240))
    )
    fan.export(str(out / "fan1200.step"))


def make_pdf(out: Path) -> None:
    from weasyprint import HTML

    HTML(string=DATASHEET_HTML).write_pdf(str(out / "pb10k-datasheet.pdf"))


def make_host(out: Path) -> None:
    import yaml
    from PIL import Image, ImageDraw

    # G8 BoM files: the golden BoM, and a "partial" one without the label, user-manual,
    # carton and solder-consumable lines (docs/14 §4 G8 b).
    removed = {"labels", "user_manual", "packaging_carton", "consumable_solder"}
    for case, prefix, variants in (
        ("power_bank", "pb", ["PB10K", "PB20K"]),
        ("ceiling_fan", "fan", ["F1200", "F900"]),
    ):
        bom = yaml.safe_load((HERE.parent / case / "bom.yaml").read_text(encoding="utf-8"))
        items = {i["part_no"]: i for i in bom["items"]}
        qty: dict[str, dict[str, float]] = {}
        uom: dict[str, str] = {}
        for line in bom["mbom"]:
            child = line["child"]
            if items[child]["kind"] in ("assembly", "product"):
                continue
            for v in variants:
                if line.get("variant") in (None, v):
                    qty.setdefault(child, {}).setdefault(v, 0.0)
                    qty[child][v] += float(line["qty"])
            uom[child] = line.get("uom", "pcs")
        for suffix, keep in (
            ("", lambda _pn: True),
            ("-partial", lambda pn: items[pn].get("commodity_class") not in removed),
        ):
            with (out / f"{prefix}-bom{suffix}.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Part No", "Description", *(f"Qty {v}" for v in variants), "UoM"])
                for pn in sorted(qty):
                    if keep(pn):
                        w.writerow(
                            [
                                pn,
                                items[pn]["name"],
                                *(f"{qty[pn].get(v, 0):g}" for v in variants),
                                uom[pn],
                            ]
                        )

    # A schematic "photo": a dark power bank with four LEDs and two ports.
    im = Image.new("RGB", (640, 400), (236, 238, 242))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((90, 90, 550, 310), radius=36, fill=(40, 42, 48))
    for i in range(4):
        d.ellipse((150 + i * 26, 270, 162 + i * 26, 282), fill=(80, 180, 255))
    d.rectangle((540, 170, 556, 186), fill=(170, 170, 170))
    d.rectangle((540, 206, 556, 230), fill=(170, 170, 170))
    d.rounded_rectangle((300, 104, 340, 118), radius=6, fill=(90, 92, 100))
    im.save(out / "pb-photo.png", format="PNG", optimize=False)


def main() -> int:
    what, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    {"step": make_step, "pdf": make_pdf, "host": make_host}[what](out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
