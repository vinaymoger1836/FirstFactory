"""Prompts for the S3 proposer. Short on purpose (docs/02 §4 budget: 30k tokens)."""

from __future__ import annotations

import json
from typing import Any

SYSTEM = (
    "You plan the manufacturing operations for assemblies of a product, choosing only from the "
    "candidate catalog processes. Rules: use process codes exactly as listed; 'builds' and "
    "'consumes' are part numbers from the BoM; order steps in the sequence they happen; a "
    "process marked resolvable=false cannot compute its time for this product, so give "
    "'estimates' with seconds per unit and a reason (an estimate is flagged for the user); "
    "never invent a process, a part number, a price or a standard. If nothing fits an "
    "assembly, leave it out and add a short question. Reply with the JSON object only."
)


def proposer_messages(
    facts: dict[str, Any],
    gaps: list[dict[str, Any]],
    items: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    user = {
        "task": "Propose operations for the assemblies in 'gaps'.",
        "product": facts,
        "gaps": gaps,
        "bom_items": items,
        "candidate_processes": candidates,
    }
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
    ]
