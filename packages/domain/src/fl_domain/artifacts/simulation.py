"""S10 simulation results (docs/08)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fl_domain.base import DomainModel, LocalId

__all__ = ["Experiment", "Kpi", "Recommendation", "SimResult"]


class Kpi(DomainModel):
    name: str
    unit: str
    mean: float
    ci_low: float
    ci_high: float
    by_variant: dict[LocalId, float] = Field(default_factory=dict)


class Experiment(DomainModel):
    id: LocalId
    scenario: str
    seeds: list[int] = Field(min_length=1)
    replications: int = Field(ge=1)
    warmup_s: float = Field(ge=0)
    run_length_s: float = Field(gt=0)
    common_random_numbers: bool = True
    kpis: list[Kpi] = Field(default_factory=list)
    bottleneck: LocalId | None = None
    analytic_throughput_bound_per_h: float | None = Field(default=None, ge=0)


class Recommendation(DomainModel):
    id: LocalId
    text: str
    change_set: LocalId | None = None
    expected_effect: str = ""
    status: Literal["proposed", "simulated", "applied", "rejected"] = "proposed"


class SimResult(DomainModel):
    """S10 artifact."""

    model_hash: str
    baseline: Experiment
    scenarios: list[Experiment] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    utilisation_by_station: dict[LocalId, float] = Field(default_factory=dict)
