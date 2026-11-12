"""Read library YAML into domain models. Schema errors are returned as Issues, not raised.

Catalog layout (``library/catalog``)::

    catalog.yaml            manifest: version, effective_date, files
    processes.yaml          kind: process, entries: [...]
    ...

Pack layout (``library/packs/<STATE>``)::

    pack.yaml               manifest: state, version, as_of, next_review, files
    labour.yaml             values: [...]
    ...

Top-level keys starting with ``x-`` hold YAML anchors (shared provenance) and are ignored.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from fl_domain.base import Issue
from fl_domain.library.catalog import Catalog, CatalogEntry, CatalogManifest
from fl_domain.library.pack import Pack, PackManifest, PackValue

__all__ = ["find_library_dir", "load_catalog", "load_pack", "read_yaml"]

_ENTRY = TypeAdapter(CatalogEntry)
MAX_FILE_BYTES = 2_000_000


def find_library_dir(explicit: str | Path | None = None) -> Path:
    """``--library``, then ``FL_LIBRARY_DIR``, then the nearest ``library/`` upwards."""
    if explicit:
        return Path(explicit)
    if env := os.environ.get("FL_LIBRARY_DIR"):
        return Path(env)
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / "library" / "catalog" / "catalog.yaml").is_file():
            return d / "library"
    raise FileNotFoundError("library/ not found; pass --library or set FL_LIBRARY_DIR")


def read_yaml(path: Path) -> Any:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _schema_issues(exc: ValidationError, where: str) -> list[Issue]:
    return [
        Issue(
            code="schema",
            message=str(e["msg"]),
            path=f"{where}." + ".".join(str(p) for p in e["loc"]),
        )
        for e in exc.errors()
    ]


def load_catalog(catalog_dir: Path) -> tuple[Catalog | None, list[Issue]]:
    issues: list[Issue] = []
    try:
        manifest = CatalogManifest.model_validate(read_yaml(catalog_dir / "catalog.yaml"))
    except ValidationError as exc:
        return None, _schema_issues(exc, "catalog.yaml")
    entries: list[Any] = []
    for name in manifest.files:
        path = catalog_dir / name
        if not path.is_file():
            issues.append(Issue(code="file.missing", message=f"{name} listed but missing"))
            continue
        doc = read_yaml(path) or {}
        kind = doc.get("kind")
        for i, raw in enumerate(doc.get("entries") or []):
            if isinstance(raw, dict) and kind and "kind" not in raw:
                raw = {"kind": kind, **raw}
            try:
                entries.append(_ENTRY.validate_python(raw))
            except ValidationError as exc:
                code = raw.get("code", "?") if isinstance(raw, dict) else "?"
                issues += _schema_issues(exc, f"{name}[{i}:{code}]")
    if issues:
        return None, issues
    return Catalog(manifest=manifest, entries=entries), []


def load_pack(pack_dir: Path) -> tuple[Pack | None, list[Issue]]:
    issues: list[Issue] = []
    try:
        manifest = PackManifest.model_validate(read_yaml(pack_dir / "pack.yaml"))
    except ValidationError as exc:
        return None, _schema_issues(exc, "pack.yaml")
    values: list[PackValue] = []
    for name in manifest.files:
        path = pack_dir / name
        if not path.is_file():
            issues.append(Issue(code="file.missing", message=f"{name} listed but missing"))
            continue
        doc = read_yaml(path) or {}
        for i, raw in enumerate(doc.get("values") or []):
            try:
                values.append(PackValue.model_validate(raw))
            except ValidationError as exc:
                key = raw.get("key", "?") if isinstance(raw, dict) else "?"
                issues += _schema_issues(exc, f"{name}[{i}:{key}]")
    if issues:
        return None, issues
    return Pack(manifest=manifest, values=values), []
