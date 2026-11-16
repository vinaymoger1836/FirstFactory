"""S1 intake stage (docs/03 §3): sources → candidates → merge → ProductSpec + questions.

- ``plan``: list the sources (wizard text, parts table, each attachment). No model call.
- ``tools``: one source per step (processors.py); results kept in ``work['src:<id>']``.
- ``draft``: deterministic: classify the archetype and merge candidates (fl_engines.intake),
  fill missing variant parameters from the catalog example as ``library`` assumptions,
  apply the user's answers (``source=user``), build the ProductSpec and the questions.
- ``summarise``: the ``writer`` role writes a short plain-language summary of the facts.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fl_domain.artifacts.product_spec import ProductTemplate
from fl_domain.common import StageKey
from fl_domain.library.catalog import ArchetypeEntry
from fl_engines.intake import Candidate, classify_archetype, merge_candidates
from fl_agents.llm.errors import LlmUnavailable
from fl_agents.pipeline.stage import StageCtx, StageSpec, Summary
from fl_agents.stages.intake import prompts
from fl_agents.stages.intake.processors import (
    SourceResult,
    process_attachment,
    process_parts_paste,
    process_text,
    product_text,
)
from fl_agents.stages.intake.schemas import SummaryOut

__all__ = ["IntakeSpec", "question_id"]

LIBRARY_DEFAULT_CONFIDENCE = 0.4


def question_id(path: str) -> str:
    """Stable question ID from the field path (answers survive re-merges)."""
    return "Q-" + hashlib.sha256(path.encode()).hexdigest()[:8]


def _prov(value: Any, source: str, confidence: float, ref: str | None = None,
          note: str | None = None, unit: str | None = None) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source": source, "confidence": confidence,
            "ref": ref, "note": note}


def _fmt(v: Any, unit: str | None) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,} {unit}".strip() if isinstance(v, int | float) and unit not in (None, "unit") else (
        f"{v:,}" if isinstance(v, int | float) else str(v)
    )


def _nearest_example(entry: ArchetypeEntry, code: str, known: dict[str, float]) -> Any:
    """Catalog example variant to take defaults from: same code, else closest known params."""
    if not entry.example_variants:
        return None
    for ex in entry.example_variants:
        if ex.code == code:
            return ex

    def distance(ex: Any) -> float:
        shared = [k for k in known if k in ex.params]
        if not shared:
            return float("inf")
        return sum(
            abs(known[k] - ex.params[k]) / max(abs(known[k]), abs(ex.params[k]), 1e-9)
            for k in shared
        ) / len(shared)

    return min(entry.example_variants, key=distance)


class IntakeSpec(StageSpec):
    key: StageKey = "intake"

    # ── plan / tools ──────────────────────────────────────────────────

    @staticmethod
    def _template(ctx: StageCtx) -> ProductTemplate:
        return ProductTemplate.model_validate(ctx.project["template"])

    async def plan(self, ctx: StageCtx) -> None:
        if "sources" in ctx.work:
            return
        t = self._template(ctx)
        sources = ["text"]
        if t.parts_paste:
            sources.append("parts")
        sources += [a["id"] for a in await ctx.rt.store.attachments(ctx.project_id)]
        await ctx.save_work({"sources": sources})

    async def next_tool(self, ctx: StageCtx) -> str | None:
        return next((s for s in ctx.work.get("sources", []) if f"src:{s}" not in ctx.work), None)

    async def _archetypes(self, ctx: StageCtx) -> list[ArchetypeEntry]:
        cat = await ctx.catalog()
        return [e for e in cat.entries if isinstance(e, ArchetypeEntry)]

    async def run_tool(self, ctx: StageCtx, action: str) -> None:
        t = self._template(ctx)
        archetypes = await self._archetypes(ctx)
        await ctx.emit("progress_detail", {"source": action})
        if action == "text":
            res = await process_text(ctx, t, archetypes)
        elif action == "parts":
            res = process_parts_paste(t)
        else:
            att = await ctx.rt.store.attachment(ctx.project_id, action)
            if att is None:
                res = SourceResult(notices=[f"Attachment {action} was removed before intake."])
            else:
                res = await process_attachment(ctx, t, att, archetypes)
        await ctx.save_work({f"src:{action}": res.to_json()})

    # ── draft (deterministic) ─────────────────────────────────────────

    async def draft(self, ctx: StageCtx) -> dict[str, Any] | None:
        t = self._template(ctx)
        archetypes = await self._archetypes(ctx)
        results = [
            SourceResult(**ctx.work[f"src:{s}"])
            for s in ctx.work.get("sources", [])
            if f"src:{s}" in ctx.work
        ]
        notices = [n for r in results for n in r.notices]

        # Archetype (rules first, then the extractor's guess, then generic_*).
        cad_names = [p["name"] for r in results for p in r.parts if p["qty"]["source"] == "cad"]
        guesses = [r.llm_guess for r in results if r.llm_guess]
        cls = classify_archetype(
            archetypes,
            t.product.category,
            user_choice=t.product.archetype,
            texts=[product_text(t), *[x for r in results for x in r.terms]],
            cad_names=cad_names,
            vision_terms=[],
            llm_guess=guesses[0] if guesses else None,
        )
        if cls.notice:
            notices.append(cls.notice)
        entry = next(a for a in archetypes if a.code == cls.archetype)
        units = {p.name: p.unit for p in entry.parameters}

        # Candidates: sources, then the user's answers.
        cands = [
            Candidate(**c) for r in results for c in r.candidates
            if c["path"].split(":")[-1] in units
        ]
        answers: dict[str, Any] = ctx.work.get("answers", {})
        prev_questions = {q["id"]: q for q in (ctx.run["questions"] or [])}
        for qid, ans in answers.items():
            q = prev_questions.get(qid)
            if q is None or not q.get("path") or ans in (None, ""):
                continue
            value = _answer_value(q, ans)
            if value is None:
                continue
            key = q.get("candidates", [{}])[0].get("merge_key") if q.get("candidates") else None
            cands.append(
                Candidate(
                    path=str(key or q["path"]),
                    value=value,
                    source="user",
                    confidence=1.0,
                    unit=q.get("unit"),
                    ref=f"answer:{qid}",
                    note="Answered by the user",
                )
            )

        # Library defaults for variant parameters nothing else gave (assumptions).
        have = {c.path for c in cands}
        for v in t.product.variants:
            known = {
                c.path.split(":")[-1]: float(c.value)
                for c in cands
                if c.path.startswith(f"variant:{v.code}:")
            }
            ex = _nearest_example(entry, v.code, known)
            if ex is None:
                continue
            for p in entry.parameters:
                key = f"variant:{v.code}:{p.name}"
                if key not in have and p.name in ex.params:
                    cands.append(
                        Candidate(
                            path=key,
                            value=float(ex.params[p.name]),
                            source="library",
                            confidence=LIBRARY_DEFAULT_CONFIDENCE,
                            unit=p.unit,
                            ref=f"catalog:{ctx.project['catalog_version']}:{entry.code}/"
                            f"example_variants/{ex.code}",
                            note=f"Typical value from the catalog example {ex.code}; please check",
                        )
                    )

        merged = merge_candidates(cands)
        codes = [v.code for v in t.product.variants]

        def pointer(key: str) -> str:
            _, code, name = key.split(":", 2)
            return f"/product/variants/{codes.index(code)}/params/{name}"

        # Build the ProductSpec JSON.
        variants: list[dict[str, Any]] = []
        for i, v in enumerate(t.product.variants):
            params: dict[str, Any] = {}
            for p in entry.parameters:
                c = merged.values.get(f"variant:{v.code}:{p.name}")
                if c is None:
                    continue
                params[p.name] = {**c.prov(), "unit": c.unit or p.unit}
            variants.append(
                {
                    "code": v.code,
                    "name": v.name,
                    "mix_pct": _prov(v.mix_pct, "user", 1.0, f"template:product/variants/{i}/mix_pct",
                                     unit="%"),
                    "selling_price_inr": (
                        _prov(v.selling_price_inr, "user", 1.0,
                              f"template:product/variants/{i}/selling_price_inr", unit="INR")
                        if v.selling_price_inr is not None
                        else None
                    ),
                    "differences": v.differences,
                    "params": params,
                }
            )
        demand = t.product.demand
        targets = t.product.targets
        parts = [p for r in results for p in r.parts]
        assemblies = [a for r in results for a in r.assemblies]
        extensions: dict[str, dict[str, Any]] = {}
        for r in results:
            extensions.update(r.extensions)
        for ns, values in t.extensions.items():
            extensions.setdefault(ns, {}).update(
                {k: _prov(val, "user", 1.0, f"template:extensions/{ns}/{k}") for k, val in values.items()}
            )
        spec: dict[str, Any] = {
            "template_version": 1,
            "product": {
                "name": t.product.name,
                "category": t.product.category,
                "archetype": _prov(cls.archetype, cls.source, cls.confidence, "rules:archetype",
                                   cls.note),
                "description": _prov(t.product.description, "user", 1.0,
                                     "template:product/description"),
                "variants": variants,
                "demand": {
                    "annual_volume": (
                        _prov(demand.annual_volume, "user", 1.0,
                              "template:product/demand/annual_volume")
                        if demand.annual_volume is not None
                        else None
                    ),
                    "ramp_plan": [r.model_dump() for r in demand.ramp_plan] if demand.ramp_plan else None,
                },
                "targets": {
                    "unit_cost_inr": (
                        _prov(targets.unit_cost_inr, "user", 1.0,
                              "template:product/targets/unit_cost_inr", unit="INR")
                        if targets.unit_cost_inr is not None
                        else None
                    ),
                    "quality_level": targets.quality_level,
                    "automation_level": targets.automation_level,
                },
            },
            "site": t.site.model_dump(mode="json"),
            "operating_pattern": t.operating_pattern.model_dump(mode="json"),
            "finance_inputs": t.finance_inputs.model_dump(mode="json"),
            "attachments": [a.model_dump(mode="json") for a in t.attachments],
            "bom_hint": t.bom_hint,
            "structure": {"assemblies": assemblies, "parts": parts},
            "extensions": extensions,
        }

        # Questions: conflicts first, then recommended fields left blank (docs/03 §1.1).
        questions: list[dict[str, Any]] = []
        for conf in merged.conflicts:
            ptr = pointer(conf.path) if conf.path.startswith("variant:") else conf.path
            name = conf.path.split(":")[-1]
            code = conf.path.split(":")[1] if conf.path.startswith("variant:") else ""
            allc = [conf.chosen, *conf.others]
            questions.append(
                {
                    "id": question_id(ptr),
                    "stage": "intake",
                    "kind": "choice",
                    "text": f"Sources disagree on {name.replace('_', ' ')}"
                    + (f" for {code}" if code else "")
                    + ". Which value is right?",
                    "path": ptr,
                    "unit": conf.chosen.unit,
                    "options": [
                        f"{_fmt(c.value, c.unit)} ({c.source}{', ' + c.ref if c.ref else ''})"
                        for c in allc
                    ],
                    "candidates": [
                        {
                            "value": c.value,
                            "unit": c.unit,
                            "source": c.source,
                            "confidence": c.confidence,
                            "ref": c.ref,
                            "merge_key": conf.path,
                        }
                        for c in allc
                    ],
                    "answer": None,
                }
            )
        if demand.annual_volume is None:
            questions.append(
                {
                    "id": question_id("/product/demand/annual_volume"),
                    "stage": "intake",
                    "kind": "number",
                    "text": "How many units per year (all variants) do you plan to make?",
                    "path": "/product/demand/annual_volume",
                    "unit": None,
                    "options": [],
                    "candidates": [],
                    "answer": None,
                }
            )
        # Keep earlier answered questions visible (history), without duplicates.
        ids = {q["id"] for q in questions}
        for q in prev_questions.values():
            if q.get("answer") not in (None, "") and q["id"] not in ids:
                questions.append(q)
        # Apply answers to plain (non-merged) fields such as annual volume.
        for q in questions:
            ans = answers.get(q["id"])
            if ans in (None, ""):
                continue
            q["answer"] = str(ans)
            if q["path"] == "/product/demand/annual_volume":
                val = _answer_value(q, ans)
                if val is not None:
                    spec["product"]["demand"]["annual_volume"] = _prov(
                        int(val), "user", 1.0, f"answer:{q['id']}", "Answered by the user"
                    )
        await ctx.save_work({"_questions": questions, "_notices": notices,
                             "_archetype_scores": cls.scores})
        return spec

    # ── summarise ─────────────────────────────────────────────────────

    async def summarise(self, ctx: StageCtx, artifact: dict[str, Any] | None) -> Summary:
        questions: list[dict[str, Any]] = ctx.work.get("_questions", [])
        notices: list[str] = ctx.work.get("_notices", [])
        assert artifact is not None
        facts = _facts(artifact, questions, notices)
        text = _fallback_summary(artifact, questions)
        if ctx.rt.router.available("writer"):
            try:
                out = await ctx.llm("writer", prompts.summary_messages(facts), SummaryOut,
                                    out_tokens=400)
                text = out.summary.strip()
                if out.highlights:
                    text += "\n" + "\n".join(f"• {h.strip()}" for h in out.highlights[:5])
            except LlmUnavailable:
                notices = [*notices, "Summary written without the model (writer unavailable)."]
        return Summary(text=text, questions=questions, notices=notices)


def _answer_value(q: dict[str, Any], ans: Any) -> Any:
    """Turn an answer into a value: an option label maps to its candidate value."""
    if q.get("kind") == "choice":
        opts = q.get("options") or []
        cands = q.get("candidates") or []
        if isinstance(ans, str) and ans in opts and len(cands) == len(opts):
            return cands[opts.index(ans)]["value"]
        if isinstance(ans, int) and not isinstance(ans, bool) and 0 <= ans < len(cands) and \
                not any(c["value"] == ans for c in cands):
            return cands[ans]["value"]
    if isinstance(ans, int | float) and not isinstance(ans, bool):
        return float(ans)
    if isinstance(ans, str):
        try:
            return float(ans.replace(",", ""))
        except ValueError:
            return ans
    return None


def _facts(spec: dict[str, Any], questions: list[dict[str, Any]], notices: list[str]) -> str:
    p = spec["product"]
    lines = [
        f"Product: {p['name']} ({p['category']})",
        f"Product type: {p['archetype']['value']} (from {p['archetype']['source']})",
    ]
    for v in p["variants"]:
        by_src: dict[str, int] = {}
        for prov in v["params"].values():
            by_src[prov["source"]] = by_src.get(prov["source"], 0) + 1
        src = ", ".join(f"{n} from {s}" for s, n in sorted(by_src.items()))
        lines.append(f"Variant {v['code']}: {len(v['params'])} parameters ({src})")
    st = spec["structure"]
    lines.append(f"Parts found in files: {len(st['parts'])}; assembly trees: {len(st['assemblies'])}")
    open_q = [q for q in questions if q.get("answer") in (None, "")]
    lines.append(f"Open questions: {len(open_q)}")
    lines += [f"Question: {q['text']}" for q in open_q[:5]]
    lines += [f"Notice: {n}" for n in notices[:5]]
    return "\n".join(lines)


def _fallback_summary(spec: dict[str, Any], questions: list[dict[str, Any]]) -> str:
    p = spec["product"]
    open_q = sum(1 for q in questions if q.get("answer") in (None, ""))
    return (
        f"Intake read {p['name']} as a {p['archetype']['value'].replace('_', ' ')} with "
        f"{len(p['variants'])} variant(s) and {len(spec['structure']['parts'])} part(s) from "
        f"files. {open_q} question(s) are open."
    )
