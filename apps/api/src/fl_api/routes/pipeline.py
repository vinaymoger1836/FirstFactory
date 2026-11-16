"""Pipeline view, stage runs, approvals, edits and change sets (docs/02 §2, §5, §6).

Actions that the waiting graph must see (approve, answer, re-run, reject, continue) are
queued as ``pipeline_advance`` jobs with a resume payload; the agents service applies them.
Edits and change sets are applied here through ``fl_agents.lifecycle`` (new version,
downstream stale, old thread superseded).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from fl_agents.lifecycle import (
    EditError,
    apply_change_set,
    approval_issues,
    edit_values,
    preview_change_set,
)
from fl_agents.queue import defer_advance
from fl_agents.stages.bom.workbook import bom_workbook
from fl_agents.store import Row, to_stage_run
from fl_api.db import store
from fl_api.routes.projects import CurrentRow
from fl_domain.common import STAGE_DEPS, STAGE_ORDER, StageKey
from fl_domain.project import ChangeOp

router = APIRouter(prefix="/projects/current", tags=["pipeline"])


class StageSummary(BaseModel):
    stage: str
    status: str
    version: int | None
    run_id: UUID | None
    placeholder: bool
    depends_on: list[str]


class PipelineOut(BaseModel):
    stages: list[StageSummary]
    thread_status: str | None
    stale: list[str]


class ActionIn(BaseModel):
    version: int | None = None
    note: str | None = Field(default=None, max_length=2000)
    answers: dict[str, str | float | int] = Field(default_factory=dict)
    extra_budget: int | None = Field(default=None, ge=0, le=1_000_000)
    extra_tool_calls: int | None = Field(default=None, ge=0, le=100)


class EditItem(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    value: Any
    note: str | None = Field(default=None, max_length=500)


class EditIn(BaseModel):
    version: int
    edits: list[EditItem] = Field(min_length=1, max_length=200)
    approve: bool = True
    reason: str = Field(default="user edit", max_length=500)


class ChangeSetIn(BaseModel):
    stage: StageKey
    base_version: int
    ops: list[ChangeOp] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    origin: Literal["user", "simulation", "agent"] = "user"


def _check_stage(stage: str) -> StageKey:
    if stage not in STAGE_ORDER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown stage {stage}")
    return stage


def _run_out(r: Row) -> dict[str, Any]:
    out = to_stage_run(r).model_dump(mode="json")
    out["artifact"] = r["artifact"]  # None for placeholders (not built yet)
    out["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
    return out


def _edit_error(e: EditError) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        {"message": str(e), "issues": [i.model_dump(mode="json") for i in e.issues]},
    )


@router.get("/pipeline")
async def pipeline(row: CurrentRow) -> PipelineOut:
    stages = await store().pipeline(row["id"])
    th = await store().active_thread(row["id"])
    return PipelineOut(
        stages=[StageSummary(**s, depends_on=list(STAGE_DEPS[s["stage"]])) for s in stages],
        thread_status=th["status"] if th else None,
        stale=[s["stage"] for s in stages if s["status"] == "stale"],
    )


@router.get("/stages/{stage}")
async def stage_runs(stage: str, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    runs = await store().runs(row["id"], key)
    return {
        "stage": key,
        "depends_on": list(STAGE_DEPS[key]),
        "latest": _run_out(runs[0]) if runs else None,
        "versions": [
            {
                "version": r["version"],
                "status": r["status"],
                "placeholder": r["placeholder"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in runs
        ],
    }


@router.get("/stages/{stage}/runs/{version}")
async def stage_run(stage: str, version: int, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    r = await store().run_version(row["id"], key, version)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{stage} v{version} not found")
    return _run_out(r)


async def _resume(row: Row, stage: StageKey, payload: dict[str, Any]) -> dict[str, Any]:
    th = await store().active_thread(row["id"])
    if th is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No pipeline run is waiting. Use 'Re-run stale stages' to start one.",
        )
    job = await defer_advance(str(row["id"]), th["thread_id"], {"stage": stage, **payload})
    return {"queued": True, "job_id": job, "thread_id": th["thread_id"]}


@router.post("/stages/{stage}/approve")
async def approve(stage: str, body: ActionIn, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    latest = await store().latest_run(row["id"], key)
    if latest is None or latest["status"] != "awaiting_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, f"{stage} is not awaiting approval")
    version = body.version or latest["version"]
    if version != latest["version"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only the latest version can be approved")
    blockers = approval_issues(key, latest["artifact"])
    if blockers:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": f"{len(blockers)} item(s) still need a decision before approval",
                "issues": [i.model_dump(mode="json") for i in blockers],
            },
        )
    return await _resume(row, key, {"action": "approve", "version": version})


@router.post("/stages/{stage}/answer")
async def answer(stage: str, body: ActionIn, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    latest = await store().latest_run(row["id"], key)
    if latest is None or latest["status"] != "awaiting_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, f"{stage} is not waiting for answers")
    known = {q["id"] for q in latest["questions"] or []}
    review_ns = ("line:", "qty:", "mb:", "mbconfirm:", "mat:", "finding:", "exclude:")
    unknown = [
        q for q in body.answers if q not in known and not (key == "bom" and q.startswith(review_ns))
    ]
    if not body.answers or unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown questions: {unknown}")
    return await _resume(row, key, {"action": "answer", "answers": body.answers})


@router.post("/stages/{stage}/rerun")
async def rerun(stage: str, body: ActionIn, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    latest = await store().latest_run(row["id"], key)
    if latest is not None and latest["status"] == "awaiting_approval":
        return await _resume(row, key, {"action": "rerun", "note": body.note or ""})
    # Nothing waiting at this stage: start a new pipeline thread from here.
    return await _new_thread(row, key)


@router.post("/stages/{stage}/reject")
async def reject(stage: str, body: ActionIn, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    latest = await store().latest_run(row["id"], key)
    if latest is None or latest["status"] != "awaiting_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, f"{stage} is not awaiting approval")
    if not (body.note or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say what is wrong (a note)")
    return await _resume(row, key, {"action": "reject", "note": body.note})


@router.post("/stages/{stage}/resume")
async def resume(stage: str, body: ActionIn, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    latest = await store().latest_run(row["id"], key)
    if latest is None or latest["status"] != "paused":
        raise HTTPException(status.HTTP_409_CONFLICT, f"{stage} is not paused")
    return await _resume(
        row,
        key,
        {
            "action": "continue",
            "extra_budget": body.extra_budget or 0,
            "extra_tool_calls": body.extra_tool_calls or 0,
        },
    )


@router.post("/stages/{stage}/edit")
async def edit(stage: str, body: EditIn, row: CurrentRow) -> dict[str, Any]:
    key = _check_stage(stage)
    latest = await store().latest_run(row["id"], key)
    if latest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{stage} has no runs")
    if latest["status"] in ("running", "paused"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{stage} is still running")
    try:
        new = await edit_values(
            store(),
            row,
            key,
            body.version,
            [e.model_dump() for e in body.edits],
            approve=body.approve,
            reason=body.reason,
        )
    except EditError as e:
        raise _edit_error(e) from e
    return _run_out(new)


async def _new_thread(row: Row, start: StageKey) -> dict[str, Any]:
    if row["template"] is None or row["status"] != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Submit the project first")
    thread_id = await store().new_thread(row["id"], start)
    job = await defer_advance(str(row["id"]), thread_id, None)
    await store().emit(
        row["id"], "pipeline", {"status": "started", "thread_id": thread_id, "from": start}
    )
    return {"queued": True, "job_id": job, "thread_id": thread_id}


@router.post("/rerun-stale")
async def rerun_stale(row: CurrentRow) -> dict[str, Any]:
    """Start a new thread: unchanged stages pass through by inputs hash (ADR-025)."""
    stages = await store().pipeline(row["id"])
    stale = [
        s["stage"] for s in stages if s["status"] in ("stale", "failed", "rejected", "pending")
    ]
    first: StageKey = next((s for s in STAGE_ORDER if s in stale), "intake")
    return {**await _new_thread(row, first), "stale": stale}


@router.get("/change-sets")
async def list_change_sets(row: CurrentRow) -> list[dict[str, Any]]:
    return [
        {
            **cs,
            "project_id": str(cs["project_id"]),
            "created_at": cs["created_at"].isoformat(),
            "applied_at": cs["applied_at"].isoformat() if cs["applied_at"] else None,
        }
        for cs in await store().change_sets(row["id"])
    ]


@router.post("/change-sets", status_code=status.HTTP_201_CREATED)
async def propose_change_set(body: ChangeSetIn, row: CurrentRow) -> dict[str, Any]:
    """Validate and store a change set; returns its diff for the user to review."""
    try:
        cs = await preview_change_set(
            store(),
            row,
            body.stage,
            body.base_version,
            body.ops,
            reason=body.reason,
            origin=body.origin,
        )
    except EditError as e:
        raise _edit_error(e) from e
    return {
        "id": cs["id"],
        "stage": cs["stage"],
        "base_version": cs["base_version"],
        "diff": cs["diff"],
        "status": cs["status"],
    }


@router.post("/change-sets/{cs_id}/apply")
async def apply(cs_id: str, row: CurrentRow) -> dict[str, Any]:
    try:
        new = await apply_change_set(store(), row, cs_id)
    except EditError as e:
        raise _edit_error(e) from e
    stale = [s["stage"] for s in await store().pipeline(row["id"]) if s["status"] == "stale"]
    return {"version": new["version"], "stage": new["stage"], "stale": stale}


@router.get("/stages/{stage}/runs/{version}/workbook")
async def bom_workbook_download(stage: str, version: int, row: CurrentRow) -> Response:
    """The BoM workbook (docs/03 §4.8) for one BoM version."""
    key = _check_stage(stage)
    if key != "bom":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Only the BoM stage has a workbook")
    r = await store().run_version(row["id"], key, version)
    if r is None or not r["artifact"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{stage} v{version} has no BoM")
    data = await asyncio.to_thread(bom_workbook, r["artifact"], row["name"])
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="bom-v{version}.xlsx"'},
    )


@router.get("/stages/{stage}/audit")
async def audit(stage: str, row: CurrentRow) -> list[dict[str, Any]]:
    """Who-did-what log for a stage: status changes, versions, warnings, tool calls."""
    key = _check_stage(stage)
    events = await store().stage_events(row["id"], key)
    return [
        {
            "id": e["id"],
            "kind": e["kind"],
            "at": e["created_at"].isoformat(),
            **(e["payload"] or {}),
        }
        for e in events
        if e["kind"] in ("status", "version", "warning", "tool_call", "paused", "skipped")
    ]


@router.get("/catalog/materials")
async def materials(row: CurrentRow) -> list[dict[str, Any]]:
    """Materials of the project's pinned catalog (for the BoM review dropdown)."""
    from fl_domain.library.catalog import MaterialEntry

    cat = await store().catalog(row["catalog_version"])
    return [
        {"code": e.code, "name": e.name, "density_g_cm3": e.density_g_cm3}
        for e in cat.entries
        if isinstance(e, MaterialEntry)
    ]


@router.get("/llm-usage")
async def llm_usage(row: CurrentRow) -> list[dict[str, Any]]:
    return [
        {
            **u,
            "prompt_tokens": int(u["prompt_tokens"] or 0),
            "completion_tokens": int(u["completion_tokens"] or 0),
        }
        for u in await store().llm_usage(row["id"])
    ]
