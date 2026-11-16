"""S1 source processors (docs/03 §3.1). Each returns a ``SourceResult`` that the stage
stores in ``stage_run.work`` under ``src:<id>``, so a resumed run never repeats a source.

At most one model call per source, so a pause (budget or quota) never repeats a call.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

from fl_domain.artifacts.product_spec import BomColumnMap, ProductTemplate
from fl_domain.library.catalog import ArchetypeEntry, ParamSpec
from fl_agents.llm.errors import LlmUnavailable
from fl_agents.pipeline.stage import StageCtx
from fl_agents.queue import defer_cad_extract, defer_pdf_extract
from fl_agents.stages.intake import prompts
from fl_agents.stages.intake.bomfile import propose_mapping, read_table, rows_to_parts
from fl_agents.stages.intake.schemas import (
    DocExtraction,
    ParamValue,
    TextExtraction,
    VisionExtraction,
)

__all__ = [
    "SourceResult",
    "allowed_params",
    "evidence_ok",
    "process_attachment",
    "process_parts_paste",
    "process_text",
    "product_text",
]

MAX_DOC_CHARS = 12_000


@dataclass
class SourceResult:
    candidates: list[dict[str, Any]] = field(default_factory=list)  # merge.Candidate fields
    parts: list[dict[str, Any]] = field(default_factory=list)  # PartRecord JSON
    assemblies: list[dict[str, Any]] = field(default_factory=list)  # AssemblyNode JSON
    extensions: dict[str, dict[str, Any]] = field(default_factory=dict)
    terms: list[str] = field(default_factory=list)  # classification evidence
    llm_guess: str | None = None
    notices: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ── shared helpers ────────────────────────────────────────────────────


def product_text(t: ProductTemplate) -> str:
    lines = [f"Product: {t.product.name}", f"Category: {t.product.category}"]
    lines.append(f"Description: {t.product.description.strip()}")
    for v in t.product.variants:
        diff = f" Differences: {v.differences.strip()}" if v.differences.strip() else ""
        lines.append(f"Variant {v.code} ({v.name}).{diff}")
    return "\n".join(lines)


def allowed_params(
    archetypes: list[ArchetypeEntry], category: str, chosen: str | None
) -> list[ParamSpec]:
    """Closed parameter list: the chosen archetype's, else every archetype's of the category."""
    pool = [a for a in archetypes if a.code == chosen] or [
        a for a in archetypes if a.category == category
    ]
    out: dict[str, ParamSpec] = {}
    for a in pool:
        for p in a.parameters:
            out.setdefault(p.name, p)
    return list(out.values())


_WS = re.compile(r"\s+")
_NUMS = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def evidence_ok(text: str, evidence: str, value: float) -> bool:
    """The quote is in the source text and states the number (anti-hallucination guard)."""
    if not evidence or _norm(evidence) not in _norm(text):
        return False
    for m in _NUMS.findall(evidence):
        try:
            if abs(float(m.replace(",", "")) - value) <= 1e-9 * max(1.0, abs(value)):
                return True
        except ValueError:
            continue
    return False


def _param_candidates(
    params: list[ParamValue],
    allowed: dict[str, ParamSpec],
    variant_codes: list[str],
    source_text: str,
    *,
    source: str,
    good_conf: float,
    bad_conf: float,
    ref_base: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in params:
        spec = allowed.get(p.name)
        if spec is None:
            continue
        targets = [p.variant] if p.variant in variant_codes else variant_codes
        if p.variant and p.variant not in variant_codes:
            continue  # names a variant that does not exist: drop rather than guess
        ok = evidence_ok(source_text, p.evidence, p.value)
        ref = f"{ref_base}#page{p.page}" if p.page else ref_base
        for code in targets:
            out.append(
                {
                    "path": f"variant:{code}:{p.name}",
                    "value": float(p.value),
                    "unit": p.unit or spec.unit,
                    "source": source,
                    "confidence": good_conf if ok else bad_conf,
                    "ref": ref,
                    "note": f'"{p.evidence[:160]}"' + ("" if ok else " (quote not found in source)"),
                }
            )
    return out


# ── processors ────────────────────────────────────────────────────────


async def process_text(
    ctx: StageCtx, t: ProductTemplate, archetypes: list[ArchetypeEntry]
) -> SourceResult:
    """Wizard text → structured parameters (``extractor``)."""
    params = allowed_params(archetypes, t.product.category, t.product.archetype)
    text = product_text(t)
    variants = [(v.code, v.name) for v in t.product.variants]
    res = SourceResult()
    if not ctx.rt.router.available("extractor"):
        res.notices.append("Text extraction skipped: no model key configured for 'extractor'.")
        return res
    out = await ctx.llm("extractor", prompts.text_messages(text, variants, params), TextExtraction)
    res.candidates = _param_candidates(
        out.params,
        {p.name: p for p in params},
        [v.code for v in t.product.variants],
        text,
        source="llm",
        good_conf=0.7,
        bad_conf=0.3,
        ref_base="template:product",
    )
    res.terms = [k for k in out.keywords if isinstance(k, str)][:10]
    res.llm_guess = out.archetype_guess
    return res


_QTY_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:x|×|pcs|nos|no\.?)\s+(.+)$", re.IGNORECASE)
_QTY_SUFFIX = re.compile(r"^(.+?)\s*(?:x|×)\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def process_parts_paste(t: ProductTemplate) -> SourceResult:
    """Typed or pasted parts → candidate lines without part numbers (rules only)."""
    res = SourceResult()
    for i, row in enumerate(t.parts_paste or [], start=1):
        name, qty, conf, note = row.name.strip(), row.qty, 1.0, row.note
        if qty is None:
            m = _QTY_PREFIX.match(name) or None
            if m:
                qty, name, conf = float(m.group(1)), m.group(2).strip(), 0.9
            elif m2 := _QTY_SUFFIX.match(name):
                name, qty, conf = m2.group(1).strip(), float(m2.group(2)), 0.9
        if qty is None:
            qty, conf = 1.0, 0.2
            note = "; ".join(filter(None, [note, "quantity not given"]))
        res.parts.append(
            {
                "part_no": f"PP-{i:03d}",
                "name": name[:200],
                "qty": {
                    "value": float(qty),
                    "unit": None,
                    "source": "user",
                    "confidence": conf,
                    "ref": f"template:parts_paste/{i - 1}",
                    "note": note,
                },
            }
        )
    return res


async def _wait_file_job(ctx: StageCtx, aid: str, kind: str) -> dict[str, Any]:
    """Defer the sandboxed file job once and wait for its result (or time out)."""
    store = ctx.rt.store
    pid: UUID = ctx.project_id
    att = await store.attachment(pid, aid)
    assert att is not None
    if att["status"] == "uploaded":
        await store.update_attachment(pid, aid, status="processing")
        if kind in ("step", "stl"):
            await defer_cad_extract(str(pid), aid)
        else:
            await defer_pdf_extract(str(pid), aid)
    deadline = time.monotonic() + ctx.rt.settings.file_job_timeout_s
    while True:
        att = await store.attachment(pid, aid)
        assert att is not None
        if att["status"] in ("done", "failed", "skipped"):
            return att
        if time.monotonic() > deadline:
            return {**att, "status": "failed", "meta": {**att["meta"], "error": "timed out"}}
        await asyncio.sleep(0.5)


def _cad_result(aid: str, meta: dict[str, Any], kind: str) -> SourceResult:
    res = SourceResult()
    conf = 0.95 if kind == "step" else 0.3
    for i, p in enumerate(meta.get("parts", []), start=1):
        bbox = p.get("bbox_mm")
        rec: dict[str, Any] = {
            "part_no": f"CAD-{aid[4:]}-{i:03d}",
            "name": str(p.get("name") or f"solid {i}")[:200],
            "qty": {
                "value": float(p.get("count", 1)),
                "unit": None,
                "source": "cad",
                "confidence": conf,
                "ref": f"attachment:{aid}",
                "note": "identical solids counted" if p.get("count", 1) > 1 else None,
            },
        }
        if bbox:
            rec["bbox_mm"] = {
                "value": [round(float(x), 3) for x in bbox],
                "unit": "mm",
                "source": "cad",
                "confidence": conf,
                "ref": f"attachment:{aid}",
                "note": f"volume {p.get('volume_mm3', 0):.0f} mm³, area {p.get('area_mm2', 0):.0f} mm²",
            }
        res.parts.append(rec)
        res.terms.append(str(p.get("name") or ""))
    tree = meta.get("tree")
    if tree:
        res.assemblies.append(tree)
    if kind == "stl":
        res.notices.append(f"{aid}: an STL is a single mesh; no assembly or part names (low confidence).")
    return res


async def process_attachment(
    ctx: StageCtx,
    t: ProductTemplate,
    att: dict[str, Any],
    archetypes: list[ArchetypeEntry],
) -> SourceResult:
    kind = att["kind"]
    aid = att["id"]
    res = SourceResult()
    if kind in ("step", "stl"):
        done = await _wait_file_job(ctx, aid, kind)
        if done["status"] != "done":
            res.notices.append(
                f"{att['filename']}: CAD could not be read ({done['meta'].get('error', 'failed')})."
            )
            return res
        return _cad_result(aid, done["meta"], kind)
    if kind == "pdf":
        done = await _wait_file_job(ctx, aid, kind)
        if done["status"] != "done":
            res.notices.append(
                f"{att['filename']}: PDF could not be read ({done['meta'].get('error', 'failed')})."
            )
            return res
        pages: list[tuple[int, str]] = []
        used = 0
        for p in done["meta"].get("pages", []):
            text = str(p.get("text", ""))[: MAX_DOC_CHARS - used]
            if not text:
                continue
            pages.append((int(p["page"]), text))
            used += len(text)
            if used >= MAX_DOC_CHARS:
                break
        if not pages:
            res.notices.append(f"{att['filename']}: no text found in the PDF (scanned image?).")
            return res
        if not ctx.rt.router.available("extractor"):
            res.notices.append(f"{att['filename']}: skipped, no model key for 'extractor'.")
            return res
        params = allowed_params(archetypes, t.product.category, t.product.archetype)
        variants = [(v.code, v.name) for v in t.product.variants]
        out = await ctx.llm("extractor", prompts.doc_messages(pages, variants, params), DocExtraction)
        full = "\n".join(txt for _, txt in pages)
        res.candidates = _param_candidates(
            out.params,
            {p.name: p for p in params},
            [v.code for v in t.product.variants],
            full,
            source="datasheet",
            good_conf=0.8,
            bad_conf=0.5,
            ref_base=f"attachment:{aid}",
        )
        specs = {
            re.sub(r"[^a-z0-9_]+", "_", s.name.lower()).strip("_")[:60] or f"spec_{i}": {
                "value": s.value[:200],
                "unit": None,
                "source": "datasheet",
                "confidence": 0.6,
                "ref": f"attachment:{aid}" + (f"#page{s.page}" if s.page else ""),
                "note": f"unit as written: {s.unit}" if s.unit else None,
            }
            for i, s in enumerate(out.other_specs, start=1)
        }
        if specs:
            res.extensions[f"datasheet_{aid}"] = specs
        return res
    if kind == "photo":
        if not ctx.rt.router.available("vision"):
            res.notices.append(
                f"{att['filename']}: photo intake skipped (vision model unavailable). "
                "Other inputs continue."
            )
            return res
        path = ctx.rt.settings.uploads_dir / att["path"]
        data_url = f"data:{att['mime']};base64," + base64.b64encode(path.read_bytes()).decode()
        try:
            out = await ctx.llm(
                "vision", prompts.vision_messages(data_url, t.product.name), VisionExtraction
            )
        except LlmUnavailable as e:
            res.notices.append(f"{att['filename']}: photo intake skipped ({e}).")
            return res
        res.terms = [*out.components, *out.features]
        res.llm_guess = out.archetype_guess
        ext = {
            name: {
                "value": "; ".join(vals)[:500],
                "unit": None,
                "source": "vision",
                "confidence": 0.35,
                "ref": f"attachment:{aid}",
                "note": "Seen in the photo; low confidence",
            }
            for name, vals in (
                ("components", out.components),
                ("features", out.features),
                ("size_cues", out.size_cues),
            )
            if vals
        }
        if ext:
            res.extensions[f"photo_{aid}"] = ext
        return res
    if kind == "bom_csv":
        path = ctx.rt.settings.uploads_dir / att["path"]
        headers, rows = await asyncio.to_thread(read_table, path, att["filename"])
        entry = next((a for a in t.attachments if a.id == aid), None)
        mapping = entry.bom_columns if entry and entry.bom_columns else None
        if mapping is None:
            mapping = propose_mapping(headers, [v.code for v in t.product.variants])
            res.notices.append(
                f"{att['filename']}: column mapping was not confirmed in the wizard; "
                "the automatic mapping was used."
            )
        parts, notices = rows_to_parts(headers, rows, BomColumnMap.model_validate(mapping), aid)
        res.parts, res.notices = parts, [*res.notices, *notices]
        res.terms = [p["name"] for p in parts[:40]]
        return res
    res.notices.append(f"{att['filename']}: file kind {kind} is not processed in intake.")
    return res
