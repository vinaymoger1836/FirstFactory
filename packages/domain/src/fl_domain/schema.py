"""JSON Schema export for the whole domain model (docs/04 §1).

``python -m fl_domain.schema --out path.json`` writes one schema whose root object has a
property per top-level model, so every model (and everything it references) is reachable
for code generators. ``packages/ts-types`` turns it into TypeScript at build time.

``--mode validation`` gives the input-side schema (fields with defaults are optional), used
by the wizard for ProductTemplate. The default ``serialization`` mode describes what the
API returns (every field present).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from fl_domain.artifacts import ARTIFACT_MODELS, ProductTemplate
from fl_domain.base import Issue
from fl_domain.common import Assumption
from fl_domain.library.catalog import (
    ArchetypeEntry,
    Catalog,
    CheckTypeEntry,
    CommodityClassEntry,
    MachineClassEntry,
    MtmElementEntry,
    ProcessEntry,
    ZoneKindEntry,
)
from fl_domain.library.pack import Pack, PackValue
from fl_domain.project import ChangeSet, Project, Question, StageRun

__all__ = ["SCHEMA_ID", "build_schema", "top_level_models"]

SCHEMA_ID = "https://factory-layout.local/schema/domain.json"
Mode = Literal["validation", "serialization"]


def top_level_models() -> dict[str, type[BaseModel]]:
    models: dict[str, type[BaseModel]] = {
        "ProductTemplate": ProductTemplate,
        "Project": Project,
        "StageRun": StageRun,
        "Question": Question,
        "ChangeSet": ChangeSet,
        "Assumption": Assumption,
        "Issue": Issue,
        "Catalog": Catalog,
        "ProcessEntry": ProcessEntry,
        "MachineClassEntry": MachineClassEntry,
        "CheckTypeEntry": CheckTypeEntry,
        "ZoneKindEntry": ZoneKindEntry,
        "CommodityClassEntry": CommodityClassEntry,
        "ArchetypeEntry": ArchetypeEntry,
        "MtmElementEntry": MtmElementEntry,
        "Pack": Pack,
        "PackValue": PackValue,
    }
    for model in ARTIFACT_MODELS.values():
        models[model.__name__] = model
    return models


def _strip_titles(node: Any) -> Any:
    """Drop field-level titles (Pydantic adds one per field); keep model titles.

    Code generators turn every titled sub-schema into its own named alias (``OpNo3``);
    without field titles, field types are inlined and only models become named types.
    """
    if isinstance(node, dict):
        if "$ref" in node:  # siblings of $ref (description) would make generators fork types
            return {"$ref": node["$ref"]}
        # The keyword is a string; a *field* named "title" (in "properties") is a dict.
        return {
            k: _strip_titles(v)
            for k, v in node.items()
            if not (k == "title" and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


_ANY_JSON = ["number", "string", "boolean", "object", "array", "null"]
_TYPED = {"type", "$ref", "anyOf", "oneOf", "allOf", "enum", "const", "items", "properties"}


def _explicit_any(schema: Any) -> None:
    """Make ``Any`` fields explicit ("any JSON value") instead of an empty schema.

    An empty sub-schema is valid JSON Schema for "anything", but code generators read it
    differently (json-schema-to-typescript emits an object type), so a numeric pack value
    would not type-check in TypeScript.
    """
    if not isinstance(schema, dict):
        return
    subs: list[Any] = list(schema.get("properties", {}).values())
    subs += [schema[k] for k in ("items", "additionalProperties") if isinstance(schema.get(k), dict)]
    for k in ("anyOf", "oneOf", "allOf", "prefixItems"):
        subs += schema.get(k, [])
    for sub in subs:
        if isinstance(sub, dict) and not (_TYPED & sub.keys()):
            sub["type"] = list(_ANY_JSON)
        _explicit_any(sub)


def build_schema(mode: Mode = "serialization") -> dict[str, Any]:
    models = top_level_models()
    refs, defs = models_json_schema(
        [(m, mode) for m in models.values()], ref_template="#/$defs/{model}"
    )
    raw_defs: dict[str, Any] = defs.get("$defs", {})
    clean_defs = {
        name: {**_strip_titles(d), **({"title": d["title"]} if "title" in d else {})}
        for name, d in raw_defs.items()
    }
    for d in clean_defs.values():
        _explicit_any(d)
    properties = {name: refs[(model, mode)] for name, model in models.items()}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "FactoryDomain",
        "description": "All top-level models of the Factory Layout Platform domain.",
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
        "$defs": clean_defs,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, help="Write to this file instead of stdout.")
    ap.add_argument("--mode", choices=["validation", "serialization"], default="serialization")
    args = ap.parse_args(argv)
    text = json.dumps(build_schema(args.mode), indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        sys.stderr.write(f"fl_domain.schema: wrote {args.out}\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
