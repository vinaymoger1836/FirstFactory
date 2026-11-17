"""BoM proposer prompt. Compact and deterministic (no IDs, dates or random order)."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["proposer_messages", "summary_messages"]

SYSTEM = """You are the BoM proposer of a factory-planning tool. You propose; engines calculate;
the user approves. Rules:
- Choose only from the lists given (skeleton refs, commodity class codes, material codes,
  CAD part names). Never invent prices, masses or part numbers.
- Tools are optional: use them only to check a catalog entry or a CAD part. Keep tool calls
  few (the limit is {limit}).
- Finish with exactly one submit_draft call."""

TASK_A = """Case A: there is no BoM file. Draft the BoM from the product type's skeleton.
- For each skeleton line decide keep=true/false (false only if the facts clearly rule it out).
- Link CAD parts to skeleton lines where they are the same thing (cad_links).
- For every other CAD part, give the closest allowed class code in cad_classes (use
  catalog_search to find it; leave it empty if no class fits). Each becomes a suggested line.
- Add extra_lines only for parts this product clearly needs that the skeleton lacks.
- Pick a material for CAD-linked lines when the default would be wrong."""

TASK_BC = """Case B/C: the user gave a BoM file. Never change the user's lines.
- Classify each listed unclassified line into one class code (classify).
- Pick a material for a line only when its name states one (materials)."""


def proposer_messages(
    case: str,
    facts: dict[str, Any],
    skeleton: list[dict[str, Any]],
    cad_parts: list[dict[str, Any]],
    unclassified: list[dict[str, Any]],
    classes: dict[str, str],
    materials: list[str],
    tool_limit: int,
) -> list[dict[str, Any]]:
    data = {
        "facts": facts,
        "skeleton": skeleton,
        "cad_parts": cad_parts,
        "unclassified_lines": unclassified,
        "class_codes": classes,
        "material_codes": materials,
    }
    return [
        {"role": "system", "content": SYSTEM.format(limit=tool_limit)},
        {
            "role": "user",
            "content": (TASK_A if case == "A" else TASK_BC)
            + "\n\nDATA:\n"
            + json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        },
    ]


def summary_messages(facts: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Write a short summary of this bill of materials for a first-time factory "
                "founder: what it contains, what still needs their decision. Plain words; no "
                "numbers that are not in the facts. Highlights: up to 5 short items.\n\n"
                f"FACTS:\n{facts}"
            ),
        }
    ]
