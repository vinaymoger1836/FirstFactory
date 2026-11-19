"""Structured output of the S3 proposer (a single ``reasoner`` call for assemblies the
catalog's typical routing does not cover). The model only chooses catalog processes and part
numbers; it never writes a time unless the catalog formula cannot be evaluated."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["EstimateOut", "RoutingProposalOut", "StepOut"]


class StepOut(BaseModel):
    process: str = Field(description="Catalog process code from the candidate list.")
    builds: str = Field(description="Part number of the make assembly (or product) it works on.")
    consumes: list[str] = Field(default_factory=list, description="Part numbers used up here.")
    sample_pct: float = Field(default=100, description="Share of units through this step.")
    reason: str = Field(default="", description="One short sentence: why this step.")


class EstimateOut(BaseModel):
    process: str
    seconds: float = Field(description="Seconds per unit; only for a process marked unresolved.")
    reason: str = Field(description="What the estimate is based on.")


class RoutingProposalOut(BaseModel):
    steps: list[StepOut] = Field(default_factory=list)
    estimates: list[EstimateOut] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
