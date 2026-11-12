"""Typed domain model of record (ADR-002).

- ``fl_domain.prov``       Prov[T] provenance wrapper (ADR-016)
- ``fl_domain.artifacts``  one model per stage artifact (ProductSpec … ReportPack)
- ``fl_domain.library``    catalog and data-pack schemas (docs/10)
- ``fl_domain.validators`` docs/04 §5 validators
- ``fl_domain.schema``     JSON Schema export (feeds packages/ts-types)
"""

from fl_domain.base import DomainModel, Issue
from fl_domain.common import Assumption, CatalogRef, StageKey
from fl_domain.prov import Prov, Source

__all__ = [
    "Assumption",
    "CatalogRef",
    "DomainModel",
    "Issue",
    "Prov",
    "Source",
    "StageKey",
]
