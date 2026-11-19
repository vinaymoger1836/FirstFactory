"""ISO 2859-1 / IS 2500 single sampling, normal inspection: table lookup (docs/05 §2).

Pure lookup in the catalog's ``sampling_plan`` entry: nothing about the standard is coded
here. Table I gives the code letter from lot size and inspection level; Table II-A gives the
plan for a letter and an AQL. An arrow in the table sends the lookup to the first plan below
("v") or above ("^"); a sample size that reaches the lot size means inspecting every item.
"""

from __future__ import annotations

from dataclasses import dataclass

from fl_domain.library.catalog import SamplingPlanEntry

__all__ = ["AqlResult", "aql_key", "lookup_plan"]


@dataclass(frozen=True)
class AqlResult:
    lot_size: int
    level: str
    aql: float
    letter: str  # code letter of the plan used (after following an arrow)
    table_letter: str  # code letter from Table I
    sample_size: int
    accept: int
    reject: int
    full_inspection: bool
    row: str  # where the numbers came from, in words


def aql_key(table: SamplingPlanEntry, aql: float) -> str:
    """The table's column key for an AQL value (keys are strings such as '0.65')."""
    for key in next(iter(table.single_normal.values())):
        if abs(float(key) - aql) < 1e-9:
            return key
    raise ValueError(f"AQL {aql:g} is not a column of {table.code}")


def _table_letter(table: SamplingPlanEntry, lot_size: int, level: str) -> str:
    for lr in table.lot_ranges:
        if lot_size >= lr.min_lot and (lr.max_lot is None or lot_size <= lr.max_lot):
            return lr.letters[level]  # type: ignore[index]
    return table.lot_ranges[0].letters[level]  # type: ignore[index]  # lots below the first range


def _is_plan(cell: str) -> bool:
    ac, _, re_ = cell.partition("/")
    return ac.isdigit() and re_.isdigit()


def lookup_plan(
    table: SamplingPlanEntry, lot_size: int, aql: float, level: str = "II"
) -> AqlResult:
    """Sample size, accept and reject numbers for a lot, level and AQL."""
    if level not in table.levels:
        raise ValueError(f"inspection level {level} is not in {table.code}")
    key = aql_key(table, aql)
    letters = sorted(table.sample_sizes)  # alphabetical = growing sample size
    first = _table_letter(table, max(lot_size, 1), level)
    cell = table.single_normal[first][key]
    i = letters.index(first)
    step = 0 if _is_plan(cell) else (1 if cell == "v" else -1)
    while not _is_plan(cell):
        i += step
        if not 0 <= i < len(letters):
            raise ValueError(f"no plan for AQL {aql:g} in {table.code}")
        cell = table.single_normal[letters[i]][key]
    letter = letters[i]
    ac_s, _, re_s = cell.partition("/")
    n = table.sample_sizes[letter]
    full = n >= lot_size
    note = f"Table I: lot {lot_size}, level {level} → {first}"
    if letter != first:
        note += f"; the arrow at AQL {aql:g} leads to {letter}"
    note += f"; Table II-A: n = {n}, Ac {ac_s}, Re {re_s}"
    return AqlResult(
        lot_size=lot_size,
        level=level,
        aql=aql,
        letter=letter,
        table_letter=first,
        sample_size=min(n, lot_size) if full else n,
        accept=int(ac_s),
        reject=int(re_s),
        full_inspection=full,
        row=note,
    )
