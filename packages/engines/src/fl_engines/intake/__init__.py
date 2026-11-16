"""S1 intake engines: merge rules and archetype classification (docs/03 §3.2)."""

from fl_engines.intake.classify import Classification, classify_archetype
from fl_engines.intake.merge import Candidate, Conflict, MergeResult, merge_candidates

__all__ = [
    "Candidate",
    "Classification",
    "Conflict",
    "MergeResult",
    "classify_archetype",
    "merge_candidates",
]
