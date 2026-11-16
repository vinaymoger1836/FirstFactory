"""Export what the web wizard needs from the domain model and golden fixtures (P2).

Writes one JSON file: ``{"template_schema": <validation-mode ProductTemplate schema>,
"samples": {"G1": <template>, "G2": <template>}}``. Each sample is validated as a
``ProductTemplate`` first, so the wizard's "Load sample" can never offer an invalid input.
Run by packages/ts-types/scripts/generate.mjs; the output is git-ignored.

    uv run --frozen python scripts/export_web_data.py --out path.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from fl_domain.artifacts import ProductTemplate
from fl_domain.schema import build_model_schema

REPO = Path(__file__).resolve().parents[1]
SAMPLES = {
    "G1": ("power_bank", "Power bank (2 variants)"),
    "G2": ("ceiling_fan", "BLDC ceiling fan (2 variants, existing shed)"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    samples = {}
    for key, (folder, label) in SAMPLES.items():
        raw = yaml.safe_load((REPO / "golden" / folder / "template.yaml").read_text("utf-8"))
        tpl = ProductTemplate.model_validate(raw).model_dump(mode="json")
        samples[key] = {"label": label, "template": tpl}
    data = {
        "template_schema": build_model_schema("ProductTemplate", "validation"),
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"export_web_data: wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
