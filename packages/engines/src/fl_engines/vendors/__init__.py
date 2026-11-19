"""Vendor research engine (docs/09 §2.2): evidence check, score, dedupe."""

from fl_engines.vendors.engine import (
    Candidate,
    Verified,
    dedupe,
    norm,
    registrable_domain,
    score_vendor,
    to_vendor,
    verify_candidate,
)

__all__ = [
    "Candidate",
    "Verified",
    "dedupe",
    "norm",
    "registrable_domain",
    "score_vendor",
    "to_vendor",
    "verify_candidate",
]
