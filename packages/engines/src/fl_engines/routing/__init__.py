"""S3 routing engine (docs/05 §1)."""

from fl_engines.routing.build import (
    BuildInput,
    Decisions,
    Gap,
    Proposal,
    ProposedStep,
    build_routing,
    routing_gaps,
)

__all__ = [
    "BuildInput",
    "Decisions",
    "Gap",
    "Proposal",
    "ProposedStep",
    "build_routing",
    "routing_gaps",
]
