"""File jobs: ``cad_extract`` (queue ``cad``) and ``pdf_extract`` (queue ``engines``).

Each job runs the parser in a child process with CPU-time and address-space limits and a
wall-clock timeout (docs/03 §3.1: 60 s, 2 GB for CAD), then writes the result into
``attachment.meta`` and sets ``attachment.status``. The S1 stage polls for that status.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fl_worker.queue import QUEUE_CAD, QUEUE_ENGINES, app

log = logging.getLogger(__name__)

UPLOADS = Path(os.environ.get("UPLOADS_DIR", "/data/uploads"))
DERIVED = Path(os.environ.get("DERIVED_DIR", "/data/derived"))
# Big STEP files take longer to read; override with CAD_TIMEOUT_S if the VM is slow.
CAD_TIMEOUT_S = int(os.environ.get("CAD_TIMEOUT_S", "180"))
# CAD: memory is capped by the container (2 GB cgroup limit). An address-space rlimit is not
# used because OCP reserves far more virtual memory than it touches.
CAD_MEM_BYTES = 0
PDF_TIMEOUT_S = 60
PDF_MEM_BYTES = 1_024 * 1024 * 1024


def _limits(cpu_s: int, mem: int) -> Any:
    def apply() -> None:
        if sys.platform == "win32":  # containers are Linux; this only keeps type checks happy
            return
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        if mem:
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))

    return apply


async def _run_child(args: list[str], timeout_s: int, mem: int) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_limits(timeout_s, mem),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"timed out after {timeout_s} s") from None
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"parser exited with {proc.returncode}: {tail[0][:300]}")
    data: dict[str, Any] = json.loads(out)
    return data


async def _attachment(project_id: str, attachment_id: str) -> dict[str, Any] | None:
    async with await psycopg.AsyncConnection.connect(
        os.environ.get("QUEUE_DATABASE_URL", ""), application_name="fl-worker-files"
    ) as conn:
        cur = await conn.execute(
            "SELECT id, kind, path, meta FROM attachment WHERE project_id = %s AND id = %s",
            (project_id, attachment_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {"id": row[0], "kind": row[1], "path": row[2], "meta": row[3] or {}}


async def _finish(
    project_id: str, attachment_id: str, status: str, meta: dict[str, Any], derived: str | None
) -> None:
    async with await psycopg.AsyncConnection.connect(
        os.environ.get("QUEUE_DATABASE_URL", ""), application_name="fl-worker-files"
    ) as conn:
        await conn.execute(
            "UPDATE attachment SET status = %s, meta = meta || %s, derived_path = %s "
            "WHERE project_id = %s AND id = %s",
            (status, Jsonb(meta), derived, project_id, attachment_id),
        )


@app.task(name="cad_extract", queue=QUEUE_CAD)
async def cad_extract(project_id: str, attachment_id: str) -> str:
    att = await _attachment(project_id, attachment_id)
    if att is None:
        return "missing"
    src = UPLOADS / att["path"]
    rel = f"{project_id}/{attachment_id}.glb"
    out = DERIVED / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = await _run_child(
            ["-m", "fl_worker.cad", att["kind"], str(src), str(out)], CAD_TIMEOUT_S, CAD_MEM_BYTES
        )
    except Exception as e:
        log.warning("cad_extract %s/%s failed: %s", project_id, attachment_id, e)
        await _finish(project_id, attachment_id, "failed", {"error": str(e)[:500]}, None)
        return "failed"
    await _finish(project_id, attachment_id, "done", result, rel)
    return "done"


@app.task(name="pdf_extract", queue=QUEUE_ENGINES)
async def pdf_extract(project_id: str, attachment_id: str) -> str:
    att = await _attachment(project_id, attachment_id)
    if att is None:
        return "missing"
    try:
        result = await _run_child(
            ["-m", "fl_worker.pdf", str(UPLOADS / att["path"])], PDF_TIMEOUT_S, PDF_MEM_BYTES
        )
    except Exception as e:
        log.warning("pdf_extract %s/%s failed: %s", project_id, attachment_id, e)
        await _finish(project_id, attachment_id, "failed", {"error": str(e)[:500]}, None)
        return "failed"
    await _finish(project_id, attachment_id, "done", result, None)
    return "done"
