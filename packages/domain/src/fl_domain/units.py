"""Unit validation with pint (CLAUDE.md conventions: SI internally via pint, INR for money).

Only *checks* unit strings here; conversion lives in the engines.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pint

__all__ = ["is_valid_unit", "registry"]

# Counting units and money that pint does not know out of the box.
_EXTRA_DEFINITIONS = (
    "INR = [currency] = inr = rupee",
    "unit = [count] = units = pcs = pc = each = ea = nos",
    "lakh_INR = 100000 * INR",
    "crore_INR = 10000000 * INR",
    "carton = [carton]",
    "pallet = [pallet]",
    "shift = [shift]",
    "worker = [person] = person = persons = workers = head",
)


@lru_cache(maxsize=1)
def registry() -> Any:
    ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
    for line in _EXTRA_DEFINITIONS:
        ureg.define(line)
    return ureg


@lru_cache(maxsize=512)
def is_valid_unit(unit: str) -> bool:
    """True if pint can parse ``unit`` (e.g. 'mAh', 'kVA', 'INR/kVA/month', 'm**2')."""
    text = unit.strip()
    if not text:
        return False
    if text in ("%", "pct", "percent"):
        return True
    try:
        registry().parse_units(text.replace("%", "percent"))
    except Exception:
        return False
    return True
