"""Uploads (docs/03 §2, docs/11 §2): allowed types only, magic-byte sniffing, size limits,
images re-encoded (metadata stripped), max 20 files. Parsing of CAD and PDF happens later
in the sandboxed workers; only BoM tables are read here, for the column-mapping step."""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from fl_agents.llm import CallCtx, LlmError
from fl_agents.pipeline.runtime import runtime
from fl_agents.stages.intake.bomfile import propose_mapping, read_table
from fl_agents.stages.intake.schemas import BomColumnGuess
from fl_api.db import store
from fl_api.routes.projects import CurrentRow
from fl_api.settings import get_settings
from fl_domain.artifacts.product_spec import BomColumnMap

router = APIRouter(prefix="/projects/current/attachments", tags=["attachments"])

# extension → (kind, size class)
EXTENSIONS: dict[str, tuple[str, str]] = {
    ".step": ("step", "cad"),
    ".stp": ("step", "cad"),
    ".stl": ("stl", "cad"),
    ".jpg": ("photo", "image"),
    ".jpeg": ("photo", "image"),
    ".png": ("photo", "image"),
    ".webp": ("photo", "image"),
    ".pdf": ("pdf", "pdf"),
    ".csv": ("bom_csv", "table"),
    ".xlsx": ("bom_csv", "table"),
}
CHUNK = 1024 * 1024


class AttachmentOut(BaseModel):
    id: str
    kind: str
    filename: str
    mime: str
    size_bytes: int
    status: str
    sha256: str
    bom: dict[str, Any] | None = None
    cad: dict[str, Any] | None = None
    has_glb: bool = False


def _out(row: dict[str, Any]) -> AttachmentOut:
    meta = row["meta"] or {}
    return AttachmentOut(
        id=row["id"],
        kind=row["kind"],
        filename=row["filename"],
        mime=row["mime"],
        size_bytes=row["size_bytes"],
        status=row["status"],
        sha256=row["sha256"],
        bom=meta.get("bom"),
        cad={k: meta[k] for k in ("tree", "parts", "extents_m", "solids") if k in meta} or None,
        has_glb=bool(row["derived_path"]),
    )


def _limit_bytes(size_class: str) -> int:
    s = get_settings()
    mb = {
        "cad": s.max_cad_mb,
        "image": s.max_image_mb,
        "pdf": s.max_pdf_mb,
        "table": s.max_table_mb,
    }[size_class]
    return mb * 1024 * 1024


def sniff(ext: str, head: bytes, size: int, raw: bytes | None = None) -> str | None:
    """Return the MIME type if the bytes match the extension, else None."""
    if ext in (".step", ".stp"):
        return (
            "model/step"
            if head.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"ISO-10303-21")
            else None
        )
    if ext == ".stl":
        if head[:5].lower() == b"solid" and b"\x00" not in head[:512]:
            return "model/stl"
        if size >= 84:
            n = int.from_bytes(head[80:84], "little")
            if size == 84 + 50 * n:
                return "model/stl"
        return None
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg" if head.startswith(b"\xff\xd8\xff") else None
    if ext == ".png":
        return "image/png" if head.startswith(b"\x89PNG\r\n\x1a\n") else None
    if ext == ".webp":
        return "image/webp" if head[:4] == b"RIFF" and head[8:12] == b"WEBP" else None
    if ext == ".pdf":
        return "application/pdf" if head.startswith(b"%PDF-") else None
    if ext == ".xlsx":
        if not head.startswith(b"PK\x03\x04") or raw is None:
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            return None
        ok = "xl/workbook.xml" in names
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ok else None
    if ext == ".csv":
        if b"\x00" in head:
            return None
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start < len(head) - 4:  # a multi-byte char cut at the end of the window is fine
                return None
        return "text/csv"
    return None


def _reencode_image(raw: bytes, max_px: int) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        rgb = im.convert("RGB")
        rgb.thumbnail((max_px, max_px))
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=88, optimize=True)  # no EXIF or other metadata
        return out.getvalue()


async def _bom_preview(
    row: dict[str, Any], path: Path, template: dict[str, Any] | None
) -> dict[str, Any]:
    headers, rows = await asyncio.to_thread(read_table, path, row["filename"])
    codes = [v["code"] for v in ((template or {}).get("product") or {}).get("variants", [])]
    mapping = propose_mapping(headers, codes)
    source = "rules"
    if (
        headers
        and (mapping.name is None or mapping.qty is None)
        and runtime().router.available("extractor")
    ):
        try:
            guess = await runtime().router.structured(
                "extractor",
                [
                    {
                        "role": "user",
                        "content": "These are the column headers of a bill of materials. "
                        "Say which header holds each field; use null when none does. "
                        f"Headers: {headers[:40]}",
                    }
                ],
                BomColumnGuess,
                CallCtx(project_id=row["project_id"]),
                out_tokens=150,
            )
            fill = {
                f: getattr(guess, f)
                for f in ("part_no", "name", "qty", "uom", "material")
                if getattr(mapping, f) is None and getattr(guess, f) in headers
            }
            if fill:
                mapping = mapping.model_copy(update=fill)
                source = "rules+extractor"
        except LlmError:
            pass
    warnings = []
    if mapping.qty is None and not mapping.variant_qty:
        warnings.append("No quantity column found. Pick one, or the BoM stage will ask.")
    if mapping.name is None and mapping.part_no is None:
        warnings.append("No part name or number column found.")
    return {
        "headers": headers,
        "sample": rows[:5],
        "row_count": len(rows),
        "proposal": mapping.model_dump(mode="json"),
        "mapping_source": source,
        "warnings": warnings,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    row: CurrentRow,
    file: Annotated[UploadFile, File()],
    note: Annotated[str | None, Form(max_length=500)] = None,
) -> AttachmentOut:
    s = get_settings()
    pid = row["id"]
    name = Path(file.filename or "upload").name[:200]
    ext = Path(name).suffix.lower()
    if ext not in EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Allowed files: STEP/STP, STL, JPG/PNG/WEBP, PDF, CSV/XLSX",
        )
    kind, size_class = EXTENSIONS[ext]
    if len(await store().attachments(pid)) >= s.max_files_per_project:
        raise HTTPException(status.HTTP_409_CONFLICT, f"At most {s.max_files_per_project} files")
    limit = _limit_bytes(size_class)
    buf = bytearray()
    while chunk := await file.read(CHUNK):
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{name} is larger than {limit // (1024 * 1024)} MB",
            )
    raw = bytes(buf)
    mime = sniff(ext, raw[:4096], len(raw), raw if ext == ".xlsx" else None)
    if mime is None or not raw:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"{name} does not look like a {ext[1:].upper()} file",
        )
    if kind == "photo":
        try:
            raw = await asyncio.to_thread(_reencode_image, raw, s.image_max_px)
        except Exception as e:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"{name} could not be read as an image"
            ) from e
        mime, ext = "image/jpeg", ".jpg"
    aid = await store().next_attachment_id(pid)
    rel = f"{pid}/{aid}{ext}"
    dest = s.uploads_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(dest.write_bytes, raw)
    att: dict[str, Any] = {
        "project_id": pid,
        "id": aid,
        "kind": kind,
        "filename": name,
        "mime": mime,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "path": rel,
        "status": "uploaded",
        "meta": {"note": note} if note else {},
    }
    if kind == "bom_csv":
        try:
            att["meta"]["bom"] = await _bom_preview(att, dest, row["template"])
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name} could not be read as a table"
            ) from e
    await store().add_attachment(**att)
    saved = await store().attachment(pid, aid)
    assert saved is not None
    return _out(saved)


@router.get("")
async def list_attachments(row: CurrentRow) -> list[AttachmentOut]:
    return [_out(a) for a in await store().attachments(row["id"])]


@router.get("/{aid}")
async def get_attachment(aid: str, row: CurrentRow) -> AttachmentOut:
    a = await store().attachment(row["id"], aid)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return _out(a)


@router.post("/{aid}/bom-mapping")
async def check_mapping(aid: str, mapping: BomColumnMap, row: CurrentRow) -> dict[str, Any]:
    """Check a mapping the user edited: every column must exist; warn on a missing qty."""
    a = await store().attachment(row["id"], aid)
    if a is None or a["kind"] != "bom_csv":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BoM file not found")
    headers = set(((a["meta"] or {}).get("bom") or {}).get("headers", []))
    cols = [
        mapping.part_no,
        mapping.name,
        mapping.qty,
        mapping.uom,
        mapping.material,
        *mapping.variant_qty.values(),
    ]
    unknown = [c for c in cols if c and c not in headers]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown columns: {unknown}")
    warnings = []
    if mapping.qty is None and not mapping.variant_qty:
        warnings.append("No quantity column: every line will need a quantity in the BoM stage.")
    return {"ok": True, "warnings": warnings}


@router.delete("/{aid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(aid: str, row: CurrentRow) -> None:
    if row["status"] == "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Files cannot be removed after submit")
    a = await store().delete_attachment(row["id"], aid)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    (get_settings().uploads_dir / a["path"]).unlink(missing_ok=True)


@router.get("/{aid}/glb")
async def glb(aid: str, row: CurrentRow) -> FileResponse:
    a = await store().attachment(row["id"], aid)
    if a is None or not a["derived_path"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No 3D preview for this file")
    path = (get_settings().derived_dir / a["derived_path"]).resolve()
    if not path.is_relative_to(get_settings().derived_dir.resolve()) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No 3D preview for this file")
    return FileResponse(path, media_type="model/gltf-binary", filename=f"{aid}.glb")
