"""Async repository over the pipeline tables (asyncpg through SQLAlchemy Core).

Used by the agents worker and by the API so both write runs, events and change sets the
same way. Artifacts are validated by the caller (lifecycle / stage builder) before writing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import and_, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fl_agents.store import tables as t
from fl_domain.common import STAGE_ORDER, StageKey
from fl_domain.library.catalog import Catalog, CatalogEntry, CatalogManifest
from fl_domain.pipeline import check_transition
from fl_domain.project import StageRun, StageStatus

__all__ = ["Row", "Store", "make_engine", "to_stage_run"]

Row = dict[str, Any]


def make_engine(url: str, app_name: str) -> AsyncEngine:
    """Async engine with ``application_name`` set per service (P0 watch item)."""
    return create_async_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={"server_settings": {"application_name": app_name}},
    )


def _now() -> datetime:
    return datetime.now(UTC)


def to_stage_run(row: Row) -> StageRun:
    return StageRun.model_validate(
        {
            "id": row["id"],
            "project_id": row["project_id"],
            "stage": row["stage"],
            "version": row["version"],
            "status": row["status"],
            "inputs_hash": row["inputs_hash"],
            "upstream": row["upstream"] or {},
            "params": row["params"] or {},
            "artifact": row["artifact"] or {},
            "summary": row["summary"] or "",
            "assumptions": row["assumptions"] or [],
            "questions": row["questions"] or [],
            "issues": row["issues"] or [],
            "notices": row["notices"] or [],
            "placeholder": row["placeholder"],
            "tokens_used": row["tokens_used"],
            "token_budget": row["token_budget"],
            "created_at": row["created_at"],
        }
    )


@lru_cache(maxsize=1)
def _entry_adapter() -> TypeAdapter[CatalogEntry]:
    return TypeAdapter(CatalogEntry)


class Store:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._catalogs: dict[str, Catalog] = {}

    # ── projects ──────────────────────────────────────────────────────

    async def project(self, pid: UUID) -> Row | None:
        async with self.engine.connect() as c:
            r = (await c.execute(select(t.project).where(t.project.c.id == pid))).first()
        return dict(r._mapping) if r else None

    async def update_project(self, pid: UUID, **vals: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(update(t.project).where(t.project.c.id == pid).values(**vals))

    async def latest_library_versions(self) -> tuple[str | None, dict[str, str]]:
        """Highest loaded catalog version and per-state pack versions (pinned at creation)."""
        q = text(
            """
            SELECT DISTINCT ON (kind, scope) kind, scope, version FROM library_version
            ORDER BY kind, scope, string_to_array(version, '.')::int[] DESC
            """
        )
        async with self.engine.connect() as c:
            rows = (await c.execute(q)).all()
        catalog = next((r.version for r in rows if r.kind == "catalog"), None)
        packs = {r.scope: r.version for r in rows if r.kind == "pack"}
        return catalog, packs

    async def catalog(self, version: str) -> Catalog:
        """The pinned catalog version, rebuilt from ``catalog_entry`` (cached per process)."""
        if version in self._catalogs:
            return self._catalogs[version]
        q = (
            select(t.catalog_entry.c.data, t.library_version.c.loaded_at)
            .join(t.library_version, t.library_version.c.id == t.catalog_entry.c.version_id)
            .where(
                and_(t.library_version.c.kind == "catalog", t.library_version.c.version == version)
            )
            .order_by(t.catalog_entry.c.code)
        )
        async with self.engine.connect() as c:
            rows = (await c.execute(q)).all()
        if not rows:
            raise LookupError(f"catalog {version} is not loaded")
        adapter = _entry_adapter()
        loaded: datetime = rows[0].loaded_at
        cat = Catalog(
            manifest=CatalogManifest(
                version=version, effective_date=loaded.date(), files=["catalog_entry"]
            ),
            entries=[adapter.validate_python(r.data) for r in rows],
        )
        self._catalogs[version] = cat
        return cat

    async def pack_value(self, scope: str, version: str, key: str, on: date) -> Row | None:
        q = text(
            """
            SELECT pv.* FROM pack_value pv JOIN library_version lv ON lv.id = pv.version_id
            WHERE lv.kind = 'pack' AND lv.scope = :scope AND lv.version = :version
              AND pv.key = :key AND NOT pv.superseded
              AND pv.effective_from <= :on AND (pv.effective_to IS NULL OR pv.effective_to >= :on)
            ORDER BY pv.effective_from DESC LIMIT 1
            """
        )
        async with self.engine.connect() as c:
            r = (
                await c.execute(q, {"scope": scope, "version": version, "key": key, "on": on})
            ).first()
        return dict(r._mapping) if r else None

    # ── attachments ───────────────────────────────────────────────────

    async def next_attachment_id(self, pid: UUID) -> str:
        async with self.engine.begin() as c:
            n = (
                await c.execute(
                    update(t.project)
                    .where(t.project.c.id == pid)
                    .values(attachment_seq=t.project.c.attachment_seq + 1)
                    .returning(t.project.c.attachment_seq)
                )
            ).scalar_one()
        return f"att_{n:02d}"

    async def add_attachment(self, **vals: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(insert(t.attachment).values(**vals))

    async def attachments(self, pid: UUID) -> list[Row]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    select(t.attachment)
                    .where(t.attachment.c.project_id == pid)
                    .order_by(t.attachment.c.id)
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    async def attachment(self, pid: UUID, aid: str) -> Row | None:
        async with self.engine.connect() as c:
            r = (
                await c.execute(
                    select(t.attachment).where(
                        and_(t.attachment.c.project_id == pid, t.attachment.c.id == aid)
                    )
                )
            ).first()
        return dict(r._mapping) if r else None

    async def update_attachment(self, pid: UUID, aid: str, **vals: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                update(t.attachment)
                .where(and_(t.attachment.c.project_id == pid, t.attachment.c.id == aid))
                .values(**vals)
            )

    async def delete_attachment(self, pid: UUID, aid: str) -> Row | None:
        async with self.engine.begin() as c:
            r = (
                await c.execute(
                    t.attachment.delete()
                    .where(and_(t.attachment.c.project_id == pid, t.attachment.c.id == aid))
                    .returning(t.attachment)
                )
            ).first()
        return dict(r._mapping) if r else None

    # ── stage runs ────────────────────────────────────────────────────

    async def run(self, run_id: UUID) -> Row | None:
        async with self.engine.connect() as c:
            r = (await c.execute(select(t.stage_run).where(t.stage_run.c.id == run_id))).first()
        return dict(r._mapping) if r else None

    async def runs(self, pid: UUID, stage: StageKey) -> list[Row]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    select(t.stage_run)
                    .where(and_(t.stage_run.c.project_id == pid, t.stage_run.c.stage == stage))
                    .order_by(t.stage_run.c.version.desc())
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    async def run_version(self, pid: UUID, stage: StageKey, version: int) -> Row | None:
        async with self.engine.connect() as c:
            r = (
                await c.execute(
                    select(t.stage_run).where(
                        and_(
                            t.stage_run.c.project_id == pid,
                            t.stage_run.c.stage == stage,
                            t.stage_run.c.version == version,
                        )
                    )
                )
            ).first()
        return dict(r._mapping) if r else None

    async def latest_runs(self, pid: UUID) -> dict[str, Row]:
        """Latest version per stage."""
        q = (
            select(t.stage_run)
            .where(t.stage_run.c.project_id == pid)
            .distinct(t.stage_run.c.stage)
            .order_by(t.stage_run.c.stage, t.stage_run.c.version.desc())
        )
        async with self.engine.connect() as c:
            rows = (await c.execute(q)).all()
        return {r.stage: dict(r._mapping) for r in rows}

    async def latest_run(self, pid: UUID, stage: StageKey) -> Row | None:
        return (await self.latest_runs(pid)).get(stage)

    async def latest_approved(self, pid: UUID, stage: StageKey) -> Row | None:
        async with self.engine.connect() as c:
            r = (
                await c.execute(
                    select(t.stage_run)
                    .where(
                        and_(
                            t.stage_run.c.project_id == pid,
                            t.stage_run.c.stage == stage,
                            t.stage_run.c.status == "approved",
                        )
                    )
                    .order_by(t.stage_run.c.version.desc())
                    .limit(1)
                )
            ).first()
        return dict(r._mapping) if r else None

    async def create_run(
        self,
        pid: UUID,
        stage: StageKey,
        *,
        status: StageStatus,
        inputs_hash: str,
        upstream: dict[str, int] | None = None,
        params: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
        summary: str = "",
        assumptions: list[Any] | None = None,
        questions: list[Any] | None = None,
        issues: list[Any] | None = None,
        notices: list[str] | None = None,
        placeholder: bool = False,
        token_budget: int = 0,
        work: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> Row:
        """Insert the next version of ``stage`` (versions are never reused or overwritten)."""
        async with self.engine.begin() as c:
            # Serialise version numbering per (project, stage).
            await c.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{pid}:{stage}"}
            )
            cur = (
                await c.execute(
                    select(func.coalesce(func.max(t.stage_run.c.version), 0)).where(
                        and_(t.stage_run.c.project_id == pid, t.stage_run.c.stage == stage)
                    )
                )
            ).scalar_one()
            row = (
                await c.execute(
                    insert(t.stage_run)
                    .values(
                        id=uuid.uuid4(),
                        project_id=pid,
                        stage=stage,
                        version=int(cur) + 1,
                        status=status,
                        inputs_hash=inputs_hash,
                        upstream=upstream or {},
                        params=params or {},
                        artifact=artifact,
                        summary=summary,
                        assumptions=assumptions or [],
                        questions=questions or [],
                        issues=issues or [],
                        notices=notices or [],
                        placeholder=placeholder,
                        tokens_used=0,
                        token_budget=token_budget,
                        work=work or {},
                        thread_id=thread_id,
                    )
                    .returning(t.stage_run)
                )
            ).one()
        return dict(row._mapping)

    async def update_run(self, run_id: UUID, **vals: Any) -> Row:
        if "status" in vals:
            cur = await self.run(run_id)
            if cur is None:
                raise LookupError(f"stage run {run_id} not found")
            check_transition(cur["status"], vals["status"])
        vals["updated_at"] = _now()
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    update(t.stage_run)
                    .where(t.stage_run.c.id == run_id)
                    .values(**vals)
                    .returning(t.stage_run)
                )
            ).one()
        return dict(row._mapping)

    async def merge_work(self, run_id: UUID, patch: dict[str, Any]) -> None:
        """Shallow-merge keys into ``stage_run.work`` (per-source results survive restarts)."""
        async with self.engine.begin() as c:
            await c.execute(
                update(t.stage_run)
                .where(t.stage_run.c.id == run_id)
                .values(work=t.stage_run.c.work.op("||")(patch), updated_at=_now())
            )

    async def add_tokens(self, run_id: UUID, n: int) -> int:
        async with self.engine.begin() as c:
            used = (
                await c.execute(
                    update(t.stage_run)
                    .where(t.stage_run.c.id == run_id)
                    .values(tokens_used=t.stage_run.c.tokens_used + n)
                    .returning(t.stage_run.c.tokens_used)
                )
            ).scalar_one()
        return int(used)

    # ── LLM calls ─────────────────────────────────────────────────────

    async def log_llm_call(self, **vals: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(insert(t.llm_call).values(**vals))

    async def llm_usage(self, pid: UUID) -> list[Row]:
        q = text(
            """
            SELECT sr.stage, lc.role, lc.model, lc.status, count(*) AS calls,
                   sum(lc.prompt_tokens) AS prompt_tokens,
                   sum(lc.completion_tokens) AS completion_tokens
            FROM llm_call lc LEFT JOIN stage_run sr ON sr.id = lc.stage_run_id
            WHERE lc.project_id = :pid
            GROUP BY sr.stage, lc.role, lc.model, lc.status
            ORDER BY sr.stage, lc.role
            """
        )
        async with self.engine.connect() as c:
            rows = (await c.execute(q, {"pid": pid})).all()
        return [dict(r._mapping) for r in rows]

    # ── events ────────────────────────────────────────────────────────

    async def emit(
        self,
        pid: UUID,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        stage: str | None = None,
        run_id: UUID | None = None,
    ) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                insert(t.stage_event).values(
                    project_id=pid, stage=stage, run_id=run_id, kind=kind, payload=payload or {}
                )
            )

    async def events_after(self, pid: UUID, after_id: int, limit: int = 500) -> list[Row]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    select(t.stage_event)
                    .where(and_(t.stage_event.c.project_id == pid, t.stage_event.c.id > after_id))
                    .order_by(t.stage_event.c.id)
                    .limit(limit)
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    async def stage_events(self, pid: UUID, stage: str, limit: int = 500) -> list[Row]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    select(t.stage_event)
                    .where(and_(t.stage_event.c.project_id == pid, t.stage_event.c.stage == stage))
                    .order_by(t.stage_event.c.id)
                    .limit(limit)
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    # ── threads ───────────────────────────────────────────────────────

    async def new_thread(self, pid: UUID, started_from: str) -> str:
        """Supersede any active thread and start a new one (ADR-025)."""
        async with self.engine.begin() as c:
            await c.execute(
                update(t.pipeline_thread)
                .where(
                    and_(
                        t.pipeline_thread.c.project_id == pid,
                        t.pipeline_thread.c.status == "active",
                    )
                )
                .values(status="superseded")
            )
            n = (
                await c.execute(select(func.count()).where(t.pipeline_thread.c.project_id == pid))
            ).scalar_one()
            tid = f"{pid}:{int(n) + 1}"
            await c.execute(
                insert(t.pipeline_thread).values(
                    thread_id=tid, project_id=pid, status="active", started_from=started_from
                )
            )
        return tid

    async def thread(self, thread_id: str) -> Row | None:
        async with self.engine.connect() as c:
            r = (
                await c.execute(
                    select(t.pipeline_thread).where(t.pipeline_thread.c.thread_id == thread_id)
                )
            ).first()
        return dict(r._mapping) if r else None

    async def active_thread(self, pid: UUID) -> Row | None:
        async with self.engine.connect() as c:
            r = (
                await c.execute(
                    select(t.pipeline_thread)
                    .where(
                        and_(
                            t.pipeline_thread.c.project_id == pid,
                            t.pipeline_thread.c.status == "active",
                        )
                    )
                    .order_by(t.pipeline_thread.c.created_at.desc())
                    .limit(1)
                )
            ).first()
        return dict(r._mapping) if r else None

    async def set_thread_status(self, thread_id: str, status: str) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                update(t.pipeline_thread)
                .where(t.pipeline_thread.c.thread_id == thread_id)
                .values(status=status)
            )

    async def supersede_threads(self, pid: UUID) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                update(t.pipeline_thread)
                .where(
                    and_(
                        t.pipeline_thread.c.project_id == pid,
                        t.pipeline_thread.c.status == "active",
                    )
                )
                .values(status="superseded")
            )

    # ── change sets ───────────────────────────────────────────────────

    async def next_change_set_id(self, pid: UUID) -> str:
        async with self.engine.connect() as c:
            n = (
                await c.execute(select(func.count()).where(t.change_set.c.project_id == pid))
            ).scalar_one()
        return f"CS-{int(n) + 1:03d}"

    async def add_change_set(self, **vals: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(insert(t.change_set).values(**vals))

    async def change_set(self, pid: UUID, cs_id: str) -> Row | None:
        async with self.engine.connect() as c:
            r = (
                await c.execute(
                    select(t.change_set).where(
                        and_(t.change_set.c.project_id == pid, t.change_set.c.id == cs_id)
                    )
                )
            ).first()
        return dict(r._mapping) if r else None

    async def change_sets(self, pid: UUID) -> list[Row]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    select(t.change_set)
                    .where(t.change_set.c.project_id == pid)
                    .order_by(t.change_set.c.created_at.desc())
                )
            ).all()
        return [dict(r._mapping) for r in rows]

    async def update_change_set(self, pid: UUID, cs_id: str, **vals: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                update(t.change_set)
                .where(and_(t.change_set.c.project_id == pid, t.change_set.c.id == cs_id))
                .values(**vals)
            )

    # ── pipeline overview ─────────────────────────────────────────────

    async def pipeline(self, pid: UUID) -> list[Row]:
        latest = await self.latest_runs(pid)
        out: list[Row] = []
        for s in STAGE_ORDER:
            r = latest.get(s)
            out.append(
                {
                    "stage": s,
                    "status": r["status"] if r else "pending",
                    "version": r["version"] if r else None,
                    "run_id": r["id"] if r else None,
                    "placeholder": bool(r["placeholder"]) if r else False,
                    "updated_at": r["updated_at"] if r else None,
                }
            )
        return out
