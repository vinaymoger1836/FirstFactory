"""Stage-run lifecycle: inputs hash, approval, stale propagation, edits and change sets.

docs/02 §2 and §6, ADR-005, ADR-025. Both the pipeline graph and the API go through these
functions so a version is created, approved or marked stale in one way only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fl_agents.store.repo import Row, Store
from fl_domain.artifacts import ARTIFACT_MODELS
from fl_domain.base import Issue
from fl_domain.common import STAGE_DEPS, STAGE_ORDER, Assumption, StageKey, downstream_closure
from fl_domain.pipeline import (
    ChangeSetError,
    apply_change_ops,
    content_sha,
    diff,
    inputs_hash,
    pointer_get,
)
from fl_domain.project import ChangeOp
from fl_domain.prov import LOW_CONFIDENCE
from fl_domain.validators import ValidationContext, validate_json

__all__ = [
    "EditError",
    "apply_change_set",
    "approve_run",
    "assumptions_for",
    "compute_inputs",
    "edit_values",
    "preview_change_set",
    "propagate_stale",
    "topo_order",
    "validate_artifact_dict",
]


class EditError(ValueError):
    """An edit or change set that cannot be applied (with validator issues if any)."""

    def __init__(self, message: str, issues: list[Issue] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


def topo_order(stages: tuple[StageKey, ...] | list[StageKey] | None = None) -> list[StageKey]:
    """Stages in dependency order (STAGE_ORDER is not topological: vendors feeds finance)."""
    wanted = set(stages) if stages is not None else set(STAGE_ORDER)
    done: list[StageKey] = []
    seen: set[StageKey] = set()

    def visit(s: StageKey) -> None:
        if s in seen:
            return
        seen.add(s)
        for d in STAGE_DEPS[s]:
            visit(d)
        done.append(s)

    for s in STAGE_ORDER:
        visit(s)
    return [s for s in done if s in wanted]


# ── inputs hash ───────────────────────────────────────────────────────


async def compute_inputs(
    store: Store, project: Row, stage: StageKey, params: dict[str, Any] | None = None
) -> tuple[str, dict[str, int], bool]:
    """(inputs_hash, upstream versions, all dependencies approved) for ``stage`` now."""
    pid: UUID = project["id"]
    upstream: dict[str, int] = {}
    for_hash: dict[str, tuple[int, str]] = {}
    ready = True
    for dep in STAGE_DEPS[stage]:
        r = await store.latest_approved(pid, dep)
        if r is None:
            ready = False
            continue
        upstream[dep] = r["version"]
        for_hash[dep] = (r["version"], content_sha(r["artifact"]))
    extra: dict[str, Any] = {}
    if stage == "intake":
        atts = await store.attachments(pid)
        extra = {
            "template": content_sha(project["template"] or {}),
            "attachments": [[a["id"], a["sha256"]] for a in atts],
        }
    h = inputs_hash(
        stage,
        for_hash,
        project["catalog_version"] or "",
        project["pack_versions"] or {},
        params,
        extra,
    )
    return h, upstream, ready


# ── assumptions ───────────────────────────────────────────────────────


def _is_prov(node: Any) -> bool:
    return isinstance(node, dict) and {"value", "source", "confidence"} <= node.keys()


def assumptions_for(stage: StageKey, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Assumptions register rows: every low-confidence or LLM/web value (ADR-016)."""
    out: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if _is_prov(node):
            if node["confidence"] < LOW_CONFIDENCE or node["source"] in ("llm", "web"):
                out.append(
                    Assumption(
                        id=f"A-{len(out) + 1:03d}",
                        stage=stage,
                        path=path,
                        value=node["value"],
                        unit=node.get("unit"),
                        source=node["source"],
                        confidence=node["confidence"],
                        ref=node.get("ref"),
                        note=node.get("note"),
                    ).model_dump(mode="json")
                )
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(artifact, "")
    return out


# ── validation ────────────────────────────────────────────────────────


async def validate_artifact_dict(
    store: Store, project: Row, stage: StageKey, data: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """Validate with the stage model and domain validator; return the normalised JSON."""
    catalog = None
    if project.get("catalog_version"):
        try:
            catalog = await store.catalog(project["catalog_version"])
        except LookupError:
            catalog = None
    model, issues = validate_json(stage, data, ValidationContext(catalog=catalog))
    if model is None:
        return None, issues
    return model.model_dump(mode="json"), issues


# ── approval and staleness ────────────────────────────────────────────


async def propagate_stale(store: Store, pid: UUID, changed: StageKey) -> list[StageKey]:
    """Mark every downstream run built on an outdated upstream as stale (transitively)."""
    latest = await store.latest_runs(pid)
    approved: dict[str, int | None] = {}
    for s in STAGE_ORDER:
        r = await store.latest_approved(pid, s)
        approved[s] = r["version"] if r else None
    stale: set[StageKey] = {s for s, r in latest.items() if r["status"] == "stale"}  # type: ignore[misc]
    marked: list[StageKey] = []
    for s in topo_order(downstream_closure(changed)):
        r = latest.get(s)
        if r is None or r["status"] in ("stale", "pending"):
            continue
        ups: dict[str, int] = r["upstream"] or {}
        outdated = any(d in stale for d in STAGE_DEPS[s]) or any(
            ups.get(d) != approved[d] for d in STAGE_DEPS[s]
        )
        if outdated:
            await store.update_run(r["id"], status="stale")
            await store.emit(
                pid,
                "status",
                {"stage": s, "status": "stale", "version": r["version"], "because": changed},
                stage=s,
                run_id=r["id"],
            )
            stale.add(s)
            marked.append(s)
    return marked


async def approve_run(store: Store, run: Row) -> list[StageKey]:
    """Approve one run version; returns the stages that became stale."""
    if run["status"] != "approved":
        await store.update_run(run["id"], status="approved")
    pid: UUID = run["project_id"]
    await store.emit(
        pid,
        "status",
        {"stage": run["stage"], "status": "approved", "version": run["version"]},
        stage=run["stage"],
        run_id=run["id"],
    )
    return await propagate_stale(store, pid, run["stage"])


# ── edits and change sets ─────────────────────────────────────────────


def _user_value_op(artifact: dict[str, Any], path: str, value: Any, note: str | None) -> ChangeOp:
    """A replace op for a user edit. Prov leaves become source=user, confidence 1.0."""
    try:
        old = pointer_get(artifact, path)
    except ChangeSetError as e:
        raise EditError(str(e)) from e
    if _is_prov(old):
        new = {
            **old,
            "value": value,
            "source": "user",
            "confidence": 1.0,
            "ref": "user-edit",
            "note": note or f"Edited by the user (was {old['value']!r}, source {old['source']})",
        }
        return ChangeOp(op="replace", path=path, old=old, new=new)
    return ChangeOp(op="replace", path=path, old=old, new=value)


async def _new_version_from_ops(
    store: Store,
    project: Row,
    base: Row,
    ops: list[ChangeOp],
    *,
    approve: bool,
    origin: str,
    reason: str,
) -> tuple[Row, list[ChangeOp]]:
    stage: StageKey = base["stage"]
    if base["placeholder"]:
        raise EditError(f"{stage} is a placeholder (not built yet); nothing to edit")
    try:
        new_doc = apply_change_ops(base["artifact"] or {}, ops)
    except ChangeSetError as e:
        raise EditError(str(e)) from e
    normalised, issues = await validate_artifact_dict(store, project, stage, new_doc)
    errors = [i for i in issues if i.severity == "error"]
    if normalised is None or errors:
        raise EditError("the edited artifact does not validate", errors or issues)
    changes = diff(base["artifact"] or {}, normalised)
    if not changes:
        raise EditError("no change")
    row = await store.create_run(
        project["id"],
        stage,
        status="approved" if approve else "awaiting_approval",
        inputs_hash=base["inputs_hash"],
        upstream=base["upstream"],
        params={**(base["params"] or {}), "edited_from": base["version"], "edit_origin": origin},
        artifact=normalised,
        summary=base["summary"],
        assumptions=assumptions_for(stage, normalised),
        questions=base["questions"],
        issues=[i.model_dump(mode="json") for i in issues],
        notices=[*(base["notices"] or []), f"Version created by {origin} edit: {reason}"],
        token_budget=base["token_budget"],
        thread_id=base["thread_id"],
    )
    # The running graph was built on the old version; a new thread re-runs what is stale.
    await store.supersede_threads(project["id"])
    await store.emit(
        project["id"],
        "version",
        {"stage": stage, "version": row["version"], "status": row["status"], "origin": origin},
        stage=stage,
        run_id=row["id"],
    )
    if approve:
        await approve_run(store, row)
    return row, changes


async def edit_values(
    store: Store,
    project: Row,
    stage: StageKey,
    base_version: int,
    edits: list[dict[str, Any]],
    *,
    approve: bool,
    reason: str = "user edit",
) -> Row:
    """User edits ``[{path, value, note?}]`` on a version → a new version (docs/02 §5)."""
    base = await store.run_version(project["id"], stage, base_version)
    if base is None:
        raise EditError(f"{stage} v{base_version} not found")
    if not edits:
        raise EditError("no edits given")
    ops = [
        _user_value_op(base["artifact"] or {}, e["path"], e["value"], e.get("note")) for e in edits
    ]
    cs_id = await store.next_change_set_id(project["id"])
    row, changes = await _new_version_from_ops(
        store, project, base, ops, approve=approve, origin="user", reason=reason
    )
    await store.add_change_set(
        project_id=project["id"],
        id=cs_id,
        stage=stage,
        base_version=base_version,
        result_version=row["version"],
        ops=[o.model_dump(mode="json") for o in ops],
        diff=[c.model_dump(mode="json") for c in changes],
        reason=reason,
        origin="user",
        status="applied",
        applied_at=datetime.now(UTC),
    )
    return row


async def preview_change_set(
    store: Store,
    project: Row,
    stage: StageKey,
    base_version: int,
    ops: list[ChangeOp],
    *,
    reason: str,
    origin: str,
) -> Row:
    """Validate a change set and store it as `proposed` with its diff (shown before apply)."""
    if stage not in ARTIFACT_MODELS:
        raise EditError(f"unknown stage {stage}")
    base = await store.run_version(project["id"], stage, base_version)
    if base is None:
        raise EditError(f"{stage} v{base_version} not found")
    try:
        new_doc = apply_change_ops(base["artifact"] or {}, ops)
    except ChangeSetError as e:
        raise EditError(str(e)) from e
    normalised, issues = await validate_artifact_dict(store, project, stage, new_doc)
    errors = [i for i in issues if i.severity == "error"]
    if normalised is None or errors:
        raise EditError("the change set does not validate", errors or issues)
    cs_id = await store.next_change_set_id(project["id"])
    await store.add_change_set(
        project_id=project["id"],
        id=cs_id,
        stage=stage,
        base_version=base_version,
        ops=[o.model_dump(mode="json") for o in ops],
        diff=[c.model_dump(mode="json") for c in diff(base["artifact"] or {}, normalised)],
        reason=reason,
        origin=origin,
        status="proposed",
    )
    cs = await store.change_set(project["id"], cs_id)
    assert cs is not None
    return cs


async def apply_change_set(store: Store, project: Row, cs_id: str) -> Row:
    """Apply a proposed change set: new approved version, downstream stale (docs/02 §6)."""
    cs = await store.change_set(project["id"], cs_id)
    if cs is None:
        raise EditError(f"change set {cs_id} not found")
    if cs["status"] != "proposed":
        raise EditError(f"change set {cs_id} is already {cs['status']}")
    base = await store.run_version(project["id"], cs["stage"], cs["base_version"])
    if base is None:
        raise EditError("base version not found")
    ops = [ChangeOp.model_validate(o) for o in cs["ops"]]
    row, _ = await _new_version_from_ops(
        store, project, base, ops, approve=True, origin=cs["origin"], reason=cs["reason"]
    )
    await store.update_change_set(
        project["id"],
        cs_id,
        status="applied",
        result_version=row["version"],
        applied_at=datetime.now(UTC),
    )
    return row
