"""Shared base types: the strict model base, identifiers and validation issues."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

__all__ = [
    "Code",
    "DomainModel",
    "Issue",
    "LocalId",
    "NonNeg",
    "Pct",
    "PosFloat",
    "Severity",
    "Unit01",
]


class DomainModel(BaseModel):
    """Base for every domain and library model.

    Unknown fields are rejected so typos in YAML or LLM output fail loudly instead of
    being silently dropped.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_default=True,
        # Serialized output always contains defaulted fields, so the serialization schema
        # (and the generated TypeScript) marks them required.
        json_schema_serialization_defaults_required=True,
    )


# Stable, readable IDs inside a project: OP-030, WC-WELD-01, Z-STORE-LI, PB10K, cell_21700.
LocalId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$", min_length=1, max_length=80)
]
# Library codes: lower snake case (spot_weld_nickel, li_ion_cell).
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=2, max_length=64)]

NonNeg = Annotated[float, Field(ge=0)]
PosFloat = Annotated[float, Field(gt=0)]
Unit01 = Annotated[float, Field(ge=0, le=1)]
Pct = Annotated[float, Field(ge=0, le=100)]

Severity = Literal["error", "warning"]


class Issue(DomainModel):
    """One validator finding. Errors block a stage; warnings are shown with the artifact."""

    code: str
    message: str
    path: str = ""
    severity: Severity = "error"

    def __str__(self) -> str:
        where = f" at {self.path}" if self.path else ""
        return f"[{self.severity}] {self.code}{where}: {self.message}"
