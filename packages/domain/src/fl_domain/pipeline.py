"""Pure pipeline rules: inputs hash, status transitions and change-set application.

No database or LLM code here (docs/02 §2, §6; ADR-005). The agents store and the API both
call these so the rules live in one place.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from fl_domain.common import StageKey
from fl_domain.project import ChangeOp, StageStatus

__all__ = [
    "HASH_EXCLUDED_PARAMS",
    "TRANSITIONS",
    "ChangeSetError",
    "TransitionError",
    "apply_change_ops",
    "canonical_json",
    "check_transition",
    "content_sha",
    "diff",
    "inputs_hash",
    "pointer_get",
]

# Stage parameters that never change what a stage computes (budgets, UI hints).
HASH_EXCLUDED_PARAMS = frozenset({"token_budget", "stage_budgets", "note", "ui"})

# Allowed status changes (docs/02 §2 lifecycle plus `paused`, ADR-025).
TRANSITIONS: dict[StageStatus, frozenset[StageStatus]] = {
    "pending": frozenset({"running", "stale", "failed"}),
    "running": frozenset({"awaiting_approval", "approved", "failed", "paused", "stale"}),
    "paused": frozenset({"running", "failed", "stale"}),
    "awaiting_approval": frozenset({"approved", "rejected", "running", "stale"}),
    "approved": frozenset({"stale"}),
    "rejected": frozenset({"running", "stale"}),
    "stale": frozenset({"running"}),
    "failed": frozenset({"running", "stale"}),
}


class TransitionError(ValueError):
    """A status change the lifecycle does not allow."""


class ChangeSetError(ValueError):
    """A change op that does not apply to the artifact (wrong path or stale `old`)."""


def check_transition(old: StageStatus, new: StageStatus) -> None:
    if old == new:
        return
    if new not in TRANSITIONS[old]:
        raise TransitionError(f"stage status cannot change from {old} to {new}")


def canonical_json(obj: Any) -> str:
    """Stable JSON text: sorted keys, no whitespace, non-ASCII kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_sha(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def inputs_hash(
    stage: StageKey,
    upstream: Mapping[str, tuple[int, str]],
    catalog_version: str,
    pack_versions: Mapping[str, str],
    params: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Hash of everything a stage run depends on (docs/02 §2).

    ``upstream`` maps each dependency stage to (approved version, artifact content sha).
    ``extra`` carries stage-specific inputs (for intake: template and attachment hashes).
    Budgets and UI keys in ``params`` are ignored.
    """
    clean = {k: v for k, v in (params or {}).items() if k not in HASH_EXCLUDED_PARAMS}
    payload = {
        "stage": stage,
        "upstream": {k: [v[0], v[1]] for k, v in sorted(upstream.items())},
        "catalog": catalog_version,
        "packs": dict(sorted(pack_versions.items())),
        "params": clean,
        "extra": dict(extra or {}),
    }
    return content_sha(payload)


# ── JSON pointer (RFC 6901) ───────────────────────────────────────────


def _tokens(path: str) -> list[str]:
    if path in ("", "/"):
        return []
    if not path.startswith("/"):
        raise ChangeSetError(f"path must be a JSON pointer starting with '/': {path!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in path[1:].split("/")]


def _step(node: Any, tok: str, path: str) -> Any:
    if isinstance(node, dict):
        if tok not in node:
            raise ChangeSetError(f"{path}: key {tok!r} not found")
        return node[tok]
    if isinstance(node, list):
        try:
            return node[int(tok)]
        except (ValueError, IndexError) as e:
            raise ChangeSetError(f"{path}: index {tok!r} not found") from e
    raise ChangeSetError(f"{path}: cannot descend into a {type(node).__name__}")


def pointer_get(doc: Any, path: str) -> Any:
    node = doc
    for tok in _tokens(path):
        node = _step(node, tok, path)
    return node


def apply_change_ops(artifact: Mapping[str, Any], ops: list[ChangeOp]) -> dict[str, Any]:
    """Apply change ops to a copy of ``artifact``; ``old`` must match for replace/remove.

    The result is not validated here: callers validate it with the stage's artifact model
    before writing a new version.
    """
    doc: dict[str, Any] = copy.deepcopy(dict(artifact))
    for op in ops:
        toks = _tokens(op.path)
        if not toks:
            raise ChangeSetError("a change op cannot replace the whole artifact")
        parent: Any = doc
        for tok in toks[:-1]:
            parent = _step(parent, tok, op.path)
        last = toks[-1]
        if op.op == "add":
            if isinstance(parent, list):
                if last == "-":
                    parent.append(copy.deepcopy(op.new))
                else:
                    try:
                        parent.insert(int(last), copy.deepcopy(op.new))
                    except ValueError as e:
                        raise ChangeSetError(f"{op.path}: bad list index") from e
            elif isinstance(parent, dict):
                if last in parent:
                    raise ChangeSetError(f"{op.path}: already exists (use replace)")
                parent[last] = copy.deepcopy(op.new)
            else:
                raise ChangeSetError(f"{op.path}: parent is not a container")
            continue
        current = _step(parent, last, op.path)
        if op.old is not None and canonical_json(current) != canonical_json(op.old):
            raise ChangeSetError(
                f"{op.path}: value changed since the change set was made "
                f"(expected {canonical_json(op.old)}, found {canonical_json(current)})"
            )
        if op.op == "remove":
            if isinstance(parent, list):
                parent.pop(int(last))
            else:
                del parent[last]
        elif isinstance(parent, list):
            parent[int(last)] = copy.deepcopy(op.new)
        else:
            parent[last] = copy.deepcopy(op.new)
    return doc


def _esc(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def diff(a: Any, b: Any, path: str = "") -> list[ChangeOp]:
    """Minimal change ops turning ``a`` into ``b`` (dict keys recursively, lists by index)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[ChangeOp] = []
        for k in sorted(a.keys() | b.keys()):
            p = f"{path}/{_esc(str(k))}"
            if k not in b:
                out.append(ChangeOp(op="remove", path=p, old=a[k]))
            elif k not in a:
                out.append(ChangeOp(op="add", path=p, new=b[k]))
            else:
                out += diff(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += diff(x, y, f"{path}/{i}")
        return out
    if canonical_json(a) == canonical_json(b):
        return []
    return [ChangeOp(op="replace", path=path or "/", old=a, new=b)]
