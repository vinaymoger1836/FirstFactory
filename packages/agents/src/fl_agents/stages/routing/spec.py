"""S3 routing stage (docs/05 §1, ADR-028): catalog routing + deterministic engine.

- ``plan``: read the approved BoM and product spec; the engine lists assemblies that the
  archetype's typical routing does not cover (``routing_gaps``).
- ``tools`` (``proposer``): only when there are gaps and a model is available, one
  ``reasoner`` call proposes catalog processes for them (and seconds, flagged, for a process
  whose formula cannot be evaluated for this product). Without a model the gaps become
  findings the user sees.
- ``draft``: ``fl_engines.routing.build_routing`` with the user's review decisions.
- ``summarise``: plain text from the artifact (numbers come from the engine).
"""

from __future__ import annotations

from typing import Any

from fl_agents.llm.errors import LlmUnavailable
from fl_agents.pipeline.stage import StageCtx, StageSpec, Summary
from fl_agents.stages.common import archetype_of, bom_of, spec_facts
from fl_agents.stages.routing import prompts
from fl_agents.stages.routing.schemas import RoutingProposalOut
from fl_domain.common import StageKey
from fl_domain.library.catalog import MachineClassEntry, ProcessEntry
from fl_engines.expr import ExprError, names_in
from fl_engines.routing import (
    BuildInput,
    Decisions,
    Proposal,
    ProposedStep,
    build_routing,
    routing_gaps,
)

__all__ = ["RoutingSpec", "parse_decisions"]


def parse_decisions(answers: dict[str, Any]) -> Decisions:
    """Namespaced answer keys from the review UI → engine decisions.

    ``time:<op_key>:<variant|*>`` seconds, ``drop:<op_key>`` yes, ``ack:<finding id>`` yes.
    """
    d = Decisions()
    for key, val in answers.items():
        ns, _, rest = str(key).partition(":")
        if ns == "time":
            op_key, _, variant = rest.rpartition(":")
            try:
                secs = float(val)
            except (TypeError, ValueError):
                continue
            if op_key and secs > 0:
                d.time.setdefault(op_key, {})[variant or "*"] = secs
        elif ns == "drop" and str(val) in ("yes", "true", "1"):
            d.drop.add(rest)
        elif ns == "ack" and str(val) in ("yes", "true", "1"):
            d.answered.add(rest)
    return d


class RoutingSpec(StageSpec):
    key: StageKey = "routing"

    async def _input(self, ctx: StageCtx, proposal: Proposal | None = None) -> BuildInput:
        spec = await self._spec(ctx)
        arch = await archetype_of(ctx, spec)
        variants, params, mix = spec_facts(spec)
        return BuildInput(
            catalog=await ctx.catalog(),
            archetype=arch,
            bom=await bom_of(ctx),
            variants=variants,
            params=params,
            mix=mix,
            proposal=proposal or Proposal(),
            decisions=parse_decisions(ctx.work.get("answers", {})),
            catalog_version=ctx.project["catalog_version"] or "",
        )

    async def _spec(self, ctx: StageCtx) -> dict[str, Any]:
        """The S1 product spec the BoM was built from (the BoM depends on it)."""
        row = await ctx.rt.store.latest_approved(ctx.project_id, "intake")
        if row is None or not row["artifact"]:
            raise RuntimeError("the approved intake (S1) product spec is missing")
        art: dict[str, Any] = row["artifact"]
        return art

    # ── plan ──────────────────────────────────────────────────────────

    async def plan(self, ctx: StageCtx) -> None:
        if "gaps" in ctx.work:
            return
        inp = await self._input(ctx)
        gaps = routing_gaps(inp)
        await ctx.save_work(
            {
                "gaps": [
                    {"part_no": g.part_no, "name": g.name, "classes": list(g.child_classes)}
                    for g in gaps
                ],
                "need_proposer": bool(gaps),
            }
        )

    async def next_tool(self, ctx: StageCtx) -> str | None:
        if ctx.work.get("need_proposer") and "proposal" not in ctx.work:
            return "proposer"
        return None

    # ── proposer ──────────────────────────────────────────────────────

    async def run_tool(self, ctx: StageCtx, action: str) -> None:
        if action != "proposer":
            return
        if not ctx.rt.router.available("reasoner"):
            await ctx.save_work(
                {
                    "proposal": {"proposer": "catalog typical routing"},
                    "_notices_extra": [
                        "No model key for 'reasoner': assemblies without a catalog routing are "
                        "listed for you instead of proposed."
                    ],
                }
            )
            return
        inp = await self._input(ctx)
        gaps = ctx.work.get("gaps", [])
        gap_classes = {c for g in gaps for c in g["classes"]}
        resolvable_with = set(inp.params[inp.variants[0]])
        cands: list[tuple[int, dict[str, Any]]] = []
        by_code = inp.catalog.by_code()
        for e in inp.catalog.entries:
            if not isinstance(e, ProcessEntry):
                continue
            arch_ok = not e.applies_to.archetype or inp.archetype.code in e.applies_to.archetype
            generic = {"generic_mechanical", "generic_electronics"} & set(e.applies_to.archetype)
            hit = bool(gap_classes & set(e.applies_to.commodity))
            if not (arch_ok or generic or hit):
                continue
            mc = by_code.get(e.machine_class or "")
            known = (
                set(e.constants)
                | (set(mc.params) if isinstance(mc, MachineClassEntry) else set())
                | set(e.time_model.variables)
            )
            try:
                needs = set().union(
                    *(names_in(t) for t in [e.time_model.formula, *e.time_model.variables.values()])
                )
            except ExprError:
                needs = set()
            missing = sorted(needs - known - resolvable_with)
            cands.append(
                (
                    0 if hit else 1 if arch_ok else 2,
                    {
                        "code": e.code,
                        "name": e.name,
                        "task": e.task_type,
                        "resolvable": not missing,
                        **({"missing_parameters": missing} if missing else {}),
                    },
                )
            )
        cands.sort(key=lambda t: (t[0], t[1]["code"]))
        items = [
            {"part_no": i.part_no, "name": i.name, "kind": i.kind, "class": i.commodity_class}
            for i in inp.bom.items
            if i.kind in ("assembly", "product") or i.commodity_class
        ][:60]
        facts = {
            "name": (await self._spec(ctx))["product"]["name"],
            "type": inp.archetype.code,
            "variant_parameters": sorted(resolvable_with),
        }
        msgs = prompts.proposer_messages(facts, gaps, items, [c for _, c in cands][:40])
        try:
            out = await ctx.llm("reasoner", msgs, RoutingProposalOut, out_tokens=1500)
        except LlmUnavailable as e:
            await ctx.save_work(
                {
                    "proposal": {"proposer": "catalog typical routing"},
                    "_notices_extra": [f"The model was unavailable ({e}); gaps are listed."],
                }
            )
            return
        await ctx.save_work(
            {
                "proposal": {
                    "proposer": "AI (reasoner)",
                    "steps": [s.model_dump() for s in out.steps],
                    "estimates": [x.model_dump() for x in out.estimates],
                    "questions": out.questions,
                }
            }
        )

    # ── draft ─────────────────────────────────────────────────────────

    async def draft(self, ctx: StageCtx) -> dict[str, Any] | None:
        p = ctx.work.get("proposal") or {}
        proposal = Proposal(
            proposer=str(p.get("proposer", "catalog typical routing")),
            steps=[
                ProposedStep(
                    process=str(s["process"]),
                    builds=str(s["builds"]),
                    consumes=tuple(str(c) for c in s.get("consumes", [])),
                    sample_pct=float(s.get("sample_pct", 100) or 100),
                    reason=str(s.get("reason", "")),
                )
                for s in p.get("steps", [])
            ],
            estimates={
                str(x["process"]): float(x["seconds"])
                for x in p.get("estimates", [])
                if float(x["seconds"]) > 0
            },
            reasons={str(x["process"]): str(x.get("reason", "")) for x in p.get("estimates", [])},
        )
        inp = await self._input(ctx, proposal)
        routing = build_routing(inp)
        return routing.model_dump(mode="json")

    # ── summarise ─────────────────────────────────────────────────────

    async def summarise(self, ctx: StageCtx, artifact: dict[str, Any] | None) -> Summary:
        assert artifact is not None
        ops = artifact["operations"]
        findings = artifact.get("findings", [])
        blocking = [f for f in findings if f.get("blocking") and f.get("status") == "open"]
        est = [o for o in ops if o["time"]["method"] == "llm_estimate"]
        by_method: dict[str, int] = {}
        for o in ops:
            by_method[o["time"]["method"]] = by_method.get(o["time"]["method"], 0) + 1
        lines = [
            f"{len(ops)} operations for {len(artifact['variants'])} variant(s), built from "
            f"{artifact.get('proposer') or 'the catalog'}.",
            *[
                f"{t['variant']}: {t['cycle_s']:.1f} s of work per unit (manual "
                f"{t['manual_s']:.1f}, machine {t['machine_s']:.1f}, test {t['test_s']:.1f})."
                for t in artifact.get("totals", [])
            ],
            "Times: "
            + ", ".join(f"{n} by {m.replace('_', ' ')}" for m, n in sorted(by_method.items()))
            + ".",
        ]
        if est:
            lines.append(f"{len(est)} time(s) are model estimates (flagged, low confidence).")
        if blocking:
            lines.append(f"{len(blocking)} item(s) need your decision before approval.")
        notices = list(ctx.work.get("_notices_extra", []))
        return Summary(text="\n".join(lines), questions=[], notices=notices)
