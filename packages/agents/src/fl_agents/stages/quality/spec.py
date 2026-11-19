"""S4 quality plan stage (docs/05 §2, ADR-013, ADR-018, ADR-028): engine only, no model.

Everything in the plan is a lookup or a calculation from the approved BoM and routing, the
catalog (check types, ISO 2859-1 table, defaults) and the data pack (compliance items), so
the stage spends no model tokens. The user's choices arrive as answers:
``policy:test`` (power bank test policy), ``aql:<part_no>``, ``level:iqc``, ``lotdays:iqc``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fl_agents.pipeline.stage import StageCtx, StageSpec, Summary
from fl_agents.stages.common import archetype_of, bom_of, spec_facts, upstream_artifact
from fl_domain.artifacts.routing import Routing
from fl_domain.common import StageKey
from fl_domain.library.catalog import CheckTypeEntry
from fl_engines.quality import BuildInput, Decisions, build_quality

__all__ = ["QualitySpec", "parse_decisions"]

LEVELS = ("S-1", "S-2", "S-3", "S-4", "I", "II", "III")


def parse_decisions(answers: dict[str, Any]) -> Decisions:
    d = Decisions()
    for key, val in answers.items():
        ns, _, ref = str(key).partition(":")
        v = str(val)
        if ns == "policy" and v in ("functional_100_capacity_sampled", "capacity_100"):
            d.policy = v
        elif ns == "aql":
            try:
                d.aql[ref] = float(v)
            except ValueError:
                continue
        elif ns == "level" and v in LEVELS:
            d.level = v
        elif ns == "lotdays":
            try:
                d.lot_days = float(v) if float(v) > 0 else None
            except ValueError:
                continue
    return d


class QualitySpec(StageSpec):
    key: StageKey = "quality"

    async def draft(self, ctx: StageCtx) -> dict[str, Any] | None:
        row = await ctx.rt.store.latest_approved(ctx.project_id, "intake")
        if row is None or not row["artifact"]:
            raise RuntimeError("the approved intake (S1) product spec is missing")
        spec: dict[str, Any] = row["artifact"]
        arch = await archetype_of(ctx, spec)
        variants, _params, mix = spec_facts(spec)
        cat = await ctx.catalog()
        routing = Routing.model_validate(await upstream_artifact(ctx, "routing"))
        keys = list(arch.compliance_pack_keys)
        for e in cat.entries:
            if isinstance(e, CheckTypeEntry):
                keys += [k for k in e.supports_compliance if k not in keys]
        titles: dict[str, str] = {}
        ver = (ctx.project["pack_versions"] or {}).get("KA", "")
        today = datetime.now(UTC).date()
        for k in keys:
            r = await ctx.rt.store.pack_value("KA", ver, k, today)
            if r is not None:
                titles[k] = str(r.get("title") or k)
        vol = (spec["product"].get("demand") or {}).get("annual_volume")
        op = spec.get("operating_pattern") or {}
        inp = BuildInput(
            catalog=cat,
            archetype=arch,
            bom=await bom_of(ctx),
            routing=routing,
            variants=variants,
            mix=mix,
            annual_volume=float(vol["value"]) if vol else None,
            days_per_week=int(op.get("days_per_week", 6)),
            shifts_per_day=int(op.get("shifts_per_day", 1)),
            pack_titles=titles,
            decisions=parse_decisions(ctx.work.get("answers", {})),
            catalog_version=ctx.project["catalog_version"] or "",
        )
        return build_quality(inp).model_dump(mode="json")

    async def summarise(self, ctx: StageCtx, artifact: dict[str, Any] | None) -> Summary:
        assert artifact is not None
        layers: dict[str, int] = {}
        for c in artifact.get("checks", []):
            layers[c["layer"]] = layers.get(c["layer"], 0) + 1
        iqc = artifact.get("iqc", [])
        hours = (artifact.get("inspector_h_per_day") or {}).get("value")
        lines = [
            f"{len(artifact.get('checks', []))} checks: "
            + ", ".join(f"{n} {k.upper()}" for k, n in sorted(layers.items()))
            + ".",
            f"{len(iqc)} incoming items have an ISO 2859-1 sampling plan.",
        ]
        if hours is not None:
            lines.append(f"Inspection work: about {hours:.2f} h per day (catalog times).")
        impact = artifact.get("policy_impact") or []
        if impact:
            lines.append(
                "Test policy "
                + artifact["test_policy"].replace("_", " ")
                + ": "
                + "; ".join(f"{i['label']} needs {i['channels']} test channels" for i in impact)
                + "."
            )
        notes = [n for n in artifact.get("notes", []) if "volume" in n or "sampling table" in n]
        return Summary(text="\n".join(lines), questions=[], notices=notes)
