"""S2 BoM stage (docs/03 §4, ADR-022): proposer (tool loop) + deterministic BoM engine.

- ``plan``: read the approved ProductSpec, the user's BoM rows (exact per-variant
  quantities from the file) and the CAD parts; decide whether the proposer is needed (always
  for case A; for B/C only if some lines cannot be classified by rules).
- ``tools``: one action, ``proposer``: the ``reasoner`` role in a tool loop
  (catalog_search, catalog_get, bom_skeleton, pack_get, calc_mass, ask_user, submit_draft),
  max 12 tool calls, transcript saved after every turn so a pause never repeats a call.
  No model key → the rules engine proposes (notice).
- ``draft``: ``fl_engines.bom.build_bom`` with the user's review decisions (``work.answers``).
- ``summarise``: the ``writer`` role (or a plain fallback).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fl_agents.llm.errors import LlmUnavailable
from fl_agents.llm.router import Tool
from fl_agents.pipeline.stage import StageCtx, StageSpec, Summary
from fl_agents.stages.bom import prompts
from fl_agents.stages.bom.schemas import ProposerDraft, SummaryOut
from fl_agents.stages.intake.bomfile import bom_rows, propose_mapping, read_table
from fl_domain.artifacts.product_spec import BomColumnMap
from fl_domain.common import StageKey
from fl_domain.library.catalog import ArchetypeEntry, CommodityClassEntry, MaterialEntry
from fl_engines.bom import (
    BuildInput,
    CadPart,
    ClassRules,
    Decisions,
    ExtraLine,
    Proposal,
    UserRow,
    build_bom,
    skeleton_quantities,
)

__all__ = ["BomSpec", "parse_decisions"]


def parse_decisions(answers: dict[str, Any]) -> Decisions:
    """Namespaced answer keys from the review UI → engine decisions."""
    d = Decisions()
    for key, val in answers.items():
        ns, _, ref = str(key).partition(":")
        v = str(val)
        if ns == "line" and v in ("accepted", "rejected"):
            d.review[ref] = v
        elif ns == "qty":
            try:
                d.qty[ref] = float(v)
            except ValueError:
                continue
        elif ns == "mb" and v in ("make", "buy"):
            d.make_buy[ref] = v
        elif ns == "mbconfirm" and v in ("yes", "true", "1"):
            d.confirmed.add(ref)
        elif ns == "mat":
            d.material[ref] = v
        elif ns == "finding":
            d.findings[ref] = v
        elif ns == "exclude" and v in ("yes", "true", "1"):
            d.excluded_classes.add(ref)
    return d


class BomSpec(StageSpec):
    key: StageKey = "bom"

    # ── inputs ────────────────────────────────────────────────────────

    async def _spec(self, ctx: StageCtx) -> dict[str, Any]:
        ver = (ctx.run["upstream"] or {}).get("intake")
        row = await ctx.rt.store.run_version(ctx.project_id, "intake", int(ver)) if ver else None
        if row is None or not row["artifact"]:
            raise RuntimeError("the approved intake (S1) product spec is missing")
        art: dict[str, Any] = row["artifact"]
        return art

    async def _archetype(self, ctx: StageCtx, spec: dict[str, Any]) -> ArchetypeEntry:
        cat = await ctx.catalog()
        code = spec["product"]["archetype"]["value"]
        a = cat.by_code().get(code)
        if not isinstance(a, ArchetypeEntry):
            raise RuntimeError(f"archetype {code} is not in catalog {cat.manifest.version}")
        return a

    @staticmethod
    def _variants(spec: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, float]]]:
        variants = [v["code"] for v in spec["product"]["variants"]]
        params = {
            v["code"]: {k: float(p["value"]) for k, p in (v.get("params") or {}).items()}
            for v in spec["product"]["variants"]
        }
        return variants, params

    async def plan(self, ctx: StageCtx) -> None:
        if "rows" in ctx.work:
            return
        spec = await self._spec(ctx)
        arch = await self._archetype(ctx, spec)
        variants, _ = self._variants(spec)
        mappings = {a["id"]: a.get("bom_columns") for a in spec.get("attachments", [])}
        rows: list[dict[str, Any]] = []
        cad: list[dict[str, Any]] = []
        for att in await ctx.rt.store.attachments(ctx.project_id):
            if att["kind"] == "bom_csv":
                path = ctx.rt.settings.uploads_dir / att["path"]
                headers, raw = await asyncio.to_thread(read_table, path, att["filename"])
                m = mappings.get(att["id"])
                mapping = (
                    BomColumnMap.model_validate(m) if m else propose_mapping(headers, variants)
                )
                rows += bom_rows(headers, raw, mapping, att["id"], variants)
            elif att["kind"] in ("step", "stl") and att["status"] == "done":
                for p in (att["meta"] or {}).get("parts", []):
                    leaves = [p["name"]] if not p.get("instances") else list(p["instances"])
                    cad.append(
                        {
                            "name": p["name"],
                            "count": int(p.get("count", 1)),
                            "bbox_mm": p.get("bbox_mm"),
                            "volume_mm3": p.get("volume_mm3"),
                            "instances": leaves,
                            "ref": f"attachment:{att['id']}",
                        }
                    )
        cat = await ctx.catalog()
        rules = ClassRules(cat, arch)
        unclassified = [
            {"part_no": r["part_no"], "name": r["name"]}
            for r in rows
            if rules.classify(r["name"]).commodity_class is None
        ]
        case = "A" if not rows else "BC"
        # Suggestions the user rejected in earlier runs are not proposed again (docs/03 §4.6).
        rejected: list[str] = []
        for prev in await ctx.rt.store.runs(ctx.project_id, "bom"):
            if prev["id"] != ctx.run_id and prev["artifact"]:
                rejected = list(prev["artifact"].get("rejected_suggestions", []))
                break
        await ctx.save_work(
            {
                "rows": rows,
                "cad": cad,
                "case_hint": case,
                "unclassified": unclassified,
                "rejected_before": rejected,
                "need_proposer": case == "A" or bool(unclassified),
            }
        )

    async def next_tool(self, ctx: StageCtx) -> str | None:
        if ctx.work.get("need_proposer") and "proposal" not in ctx.work:
            return "proposer"
        return None

    # ── proposer (tool loop) ──────────────────────────────────────────

    async def run_tool(self, ctx: StageCtx, action: str) -> None:
        if action != "proposer":
            return
        if not ctx.rt.router.available("reasoner"):
            await ctx.save_work(
                {
                    "proposal": {"proposer": "rules engine"},
                    "_notices_extra": ["No model key for 'reasoner': the rules engine proposed."],
                }
            )
            return
        spec = await self._spec(ctx)
        arch = await self._archetype(ctx, spec)
        cat = await ctx.catalog()
        variants, params = self._variants(spec)
        sq = skeleton_quantities(arch, variants, params)
        skeleton = [
            {
                "ref": s.ref,
                "name": s.name,
                "class": s.commodity,
                "kind": s.kind,
                "qty": {v: q for v, q in sq[s.ref].items() if q is not None},
                "uom": s.uom,
            }
            for s in arch.bom_skeleton
            if s.kind != "assembly"
        ]
        classes = {e.code: e.name for e in cat.entries if isinstance(e, CommodityClassEntry)}
        mats = sorted(e.code for e in cat.entries if isinstance(e, MaterialEntry))
        cad_parts = [
            {"name": c["name"], "count": c["count"], "bbox_mm": c["bbox_mm"]}
            for c in ctx.work.get("cad", [])
        ]
        facts = {
            "product": spec["product"]["name"],
            "product_type": arch.code,
            "description": spec["product"]["description"]["value"][:600],
            "variants": {v: params[v] for v in variants},
        }
        limit = ctx.rt.settings.tool_calls_for(self.key, ctx.project["stage_budgets"] or {}) + int(
            ctx.work.get("tool_calls_extra", 0)
        )
        transcript = ctx.work.get("proposer_transcript") or prompts.proposer_messages(
            "A" if ctx.work.get("case_hint") == "A" else "BC",
            facts,
            skeleton,
            cad_parts,
            ctx.work.get("unclassified", []),
            classes,
            mats,
            limit,
        )
        asked: list[str] = list(ctx.work.get("asked", []))

        async def catalog_search(a: dict[str, Any]) -> Any:
            from fl_library.db import search_catalog

            hits = await asyncio.to_thread(
                search_catalog,
                ctx.rt.settings.database_url,
                str(a.get("query", ""))[:200],
                a.get("kind"),
                ctx.project["catalog_version"],
                5,
            )
            return [{k: h[k] for k in ("code", "kind", "name", "score")} for h in hits]

        async def catalog_get(a: dict[str, Any]) -> Any:
            e = cat.by_code().get(str(a.get("code", "")))
            if e is None:
                return {"error": "unknown code"}
            return e.model_dump(mode="json", exclude={"provenance", "notes", "description"})

        async def bom_skeleton(_a: dict[str, Any]) -> Any:
            return skeleton

        async def pack_get(a: dict[str, Any]) -> Any:
            ver = (ctx.project["pack_versions"] or {}).get("KA")
            row = await ctx.rt.store.pack_value(
                "KA", ver or "", str(a.get("key", "")), datetime.now(UTC).date()
            )
            if row is None:
                return {"error": "unknown key or no active value"}
            return {k: row[k] for k in ("key", "value", "unit", "source_title", "verify")}

        async def calc_mass(a: dict[str, Any]) -> Any:
            part = next(
                (c for c in ctx.work.get("cad", []) if c["name"] == a.get("cad_part")), None
            )
            mat = cat.by_code().get(str(a.get("material", "")))
            if part is None or not isinstance(mat, MaterialEntry) or not part.get("volume_mm3"):
                return {"error": "unknown CAD part or material"}
            g = float(part["volume_mm3"]) / 1000 * mat.density_g_cm3
            return {
                "mass_g": round(g, 2),
                "formula": "volume_mm3 / 1000 × density_g_cm3",
                "note": "explanation only: engines compute stored masses",
            }

        async def ask_user(a: dict[str, Any]) -> Any:
            q = str(a.get("question", ""))[:300]
            if q:
                asked.append(q)
                await ctx.save_work({"asked": asked})
            return {"ok": True, "note": "the question is shown to the user at review"}

        tools = [
            Tool(
                "catalog_search",
                "Search the process catalog (hybrid search).",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "kind": {"type": "string"}},
                    "required": ["query"],
                },
                catalog_search,
            ),
            Tool(
                "catalog_get",
                "Get one catalog entry by code.",
                {
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                },
                catalog_get,
            ),
            Tool(
                "bom_skeleton",
                "The product type's BoM skeleton with quantities per variant.",
                {"type": "object", "properties": {}},
                bom_skeleton,
            ),
            Tool(
                "pack_get",
                "A Karnataka data-pack value by key.",
                {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                pack_get,
            ),
            Tool(
                "calc_mass",
                "Engine mass of one CAD part for a material (explanation only).",
                {
                    "type": "object",
                    "properties": {"cad_part": {"type": "string"}, "material": {"type": "string"}},
                    "required": ["cad_part", "material"],
                },
                calc_mass,
            ),
            Tool(
                "ask_user",
                "Add a question for the user (shown at review; non-blocking).",
                {
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
                ask_user,
            ),
        ]

        async def save(msgs: list[dict[str, Any]]) -> None:
            await ctx.save_work({"proposer_transcript": msgs})

        async def on_tool(name: str, args: dict[str, Any], result: str) -> None:
            await ctx.emit("tool_call", {"tool": name, "args": args, "result": result[:200]})

        try:
            draft = await ctx.rt.router.tool_loop(
                "reasoner",
                transcript,
                tools,
                ProposerDraft,
                ctx.call_ctx(),
                max_tool_calls=limit,
                save=save,
                on_tool=on_tool,
            )
        except LlmUnavailable as e:
            await ctx.save_work(
                {
                    "proposal": {"proposer": "rules engine"},
                    "_notices_extra": [
                        f"The model was unavailable ({e}); the rules engine proposed."
                    ],
                }
            )
            return
        await ctx.save_work(
            {"proposal": {"proposer": "AI (reasoner)", **draft.model_dump(), "asked": asked}}
        )

    # ── draft (deterministic) ─────────────────────────────────────────

    async def draft(self, ctx: StageCtx) -> dict[str, Any] | None:
        spec = await self._spec(ctx)
        arch = await self._archetype(ctx, spec)
        cat = await ctx.catalog()
        variants, params = self._variants(spec)
        p = ctx.work.get("proposal") or {"proposer": "rules engine"}
        skel_refs = {s.ref for s in arch.bom_skeleton}
        classes = {e.code for e in cat.entries if isinstance(e, CommodityClassEntry)}
        mats = {e.code for e in cat.entries if isinstance(e, MaterialEntry)}
        cad_names = {c["name"] for c in ctx.work.get("cad", [])}
        proposal = Proposal(
            proposer=str(p.get("proposer", "rules engine")),
            skeleton_keep={
                d["ref"]: (bool(d.get("keep", True)), str(d.get("reason", "")))
                for d in p.get("skeleton", [])
                if d.get("ref") in skel_refs
            },
            extra_lines=[
                ExtraLine(
                    x["name"],
                    x["commodity_class"],
                    float(x["qty"]),
                    x.get("uom", "unit"),
                    x.get("reason", ""),
                )
                for x in p.get("extra_lines", [])
                if x.get("commodity_class") in classes
            ],
            classify={
                c["part_no"]: c["commodity_class"]
                for c in p.get("classify", [])
                if c.get("commodity_class") in classes
            },
            cad_map={
                c["cad_part"]: c["skeleton_ref"]
                for c in p.get("cad_links", [])
                if c.get("cad_part") in cad_names and c.get("skeleton_ref") in skel_refs
            },
            materials={
                m["target"]: m["material"]
                for m in p.get("materials", [])
                if m.get("material") in mats
            },
            questions=[str(q) for q in p.get("asked", [])],
        )
        inp = BuildInput(
            catalog=cat,
            archetype=arch,
            variants=variants,
            params=params,
            rows=[
                UserRow(
                    r["part_no"], r["name"], r["qty_by_variant"], r["uom"], r["material"], r["ref"]
                )
                for r in ctx.work.get("rows", [])
            ],
            cad=[
                CadPart(
                    c["name"],
                    c["count"],
                    tuple(c["bbox_mm"]) if c.get("bbox_mm") else None,
                    c.get("volume_mm3"),
                    tuple(c["instances"]),
                    c["ref"],
                )
                for c in ctx.work.get("cad", [])
            ],
            proposal=proposal,
            decisions=parse_decisions(ctx.work.get("answers", {})),
            rejected_before=set(ctx.work.get("rejected_before", [])),
            catalog_version=ctx.project["catalog_version"] or "",
        )
        bom = build_bom(inp)
        return bom.model_dump(mode="json")

    # ── summarise ─────────────────────────────────────────────────────

    async def summarise(self, ctx: StageCtx, artifact: dict[str, Any] | None) -> Summary:
        assert artifact is not None
        notices = list(ctx.work.get("_notices_extra", []))
        ebom = artifact.get("ebom", [])
        suggested = sum(1 for ln in ebom if ln.get("review") == "suggested")
        open_f = [f for f in artifact.get("findings", []) if f.get("status") == "open"]
        unconfirmed = sum(
            1 for d in artifact.get("make_buy_decisions", []) if not d.get("confirmed")
        )
        case_text = {
            "A": "drafted from CAD and the product type (no BoM file)",
            "B": "built from your complete BoM file",
            "C": "built from your BoM file, with probable gaps",
        }.get(str(artifact.get("case")), "built")
        facts = "\n".join(
            [
                f"Case: {case_text}",
                f"Proposer: {artifact.get('proposer')}",
                f"Lines: {len(ebom)}; suggestions waiting: {suggested}; open questions: "
                f"{len(open_f)}; make/buy decisions to confirm: {unconfirmed}",
                *[f"Finding: {f['text']}" for f in open_f[:5]],
                *[
                    f"Total {t['variant']}: {t['cost_inr']['base']:.0f} INR (catalog estimates)"
                    for t in artifact.get("totals", [])
                    if t.get("cost_inr")
                ],
            ]
        )
        text = (
            f"The BoM was {case_text}. {suggested} suggestion(s) and {len(open_f)} question(s) "
            f"need your decision; {unconfirmed} make/buy decision(s) to confirm."
        )
        if ctx.rt.router.available("writer"):
            try:
                out = await ctx.llm(
                    "writer", prompts.summary_messages(facts), SummaryOut, out_tokens=400
                )
                text = out.summary.strip()
                if out.highlights:
                    text += "\n" + "\n".join(f"• {h.strip()}" for h in out.highlights[:5])
            except LlmUnavailable:
                notices.append("Summary written without the model (writer unavailable).")
        return Summary(text=text, questions=[], notices=notices)
