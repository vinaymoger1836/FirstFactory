"""Helpers the engine-driven stages (S3, S4, vendors) share: upstream artifacts and spec facts."""

from __future__ import annotations

from typing import Any

from fl_agents.pipeline.stage import StageCtx
from fl_domain.artifacts.bom import Bom
from fl_domain.common import StageKey
from fl_domain.library.catalog import ArchetypeEntry

__all__ = ["archetype_of", "bom_of", "spec_facts", "upstream_artifact"]


async def upstream_artifact(ctx: StageCtx, stage: StageKey) -> dict[str, Any]:
    """The approved upstream artifact this run was started from."""
    ver = (ctx.run["upstream"] or {}).get(stage)
    row = await ctx.rt.store.run_version(ctx.project_id, stage, int(ver)) if ver else None
    if row is None or not row["artifact"]:
        raise RuntimeError(f"the approved {stage} artifact is missing")
    art: dict[str, Any] = row["artifact"]
    return art


async def bom_of(ctx: StageCtx) -> Bom:
    """The BoM this run is built on: the pinned upstream version, or (for S4, which depends on
    routing only) the latest approved one."""
    if (ctx.run["upstream"] or {}).get("bom"):
        return Bom.model_validate(await upstream_artifact(ctx, "bom"))
    row = await ctx.rt.store.latest_approved(ctx.project_id, "bom")
    if row is None or not row["artifact"]:
        raise RuntimeError("the approved bom artifact is missing")
    return Bom.model_validate(row["artifact"])


async def archetype_of(ctx: StageCtx, spec: dict[str, Any]) -> ArchetypeEntry:
    cat = await ctx.catalog()
    code = spec["product"]["archetype"]["value"]
    a = cat.by_code().get(code)
    if not isinstance(a, ArchetypeEntry):
        raise RuntimeError(f"archetype {code} is not in catalog {cat.manifest.version}")
    return a


def spec_facts(
    spec: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, float]], dict[str, float]]:
    """(variant codes, parameters per variant, share of units per variant summing to 1)."""
    vs = spec["product"]["variants"]
    variants = [v["code"] for v in vs]
    params = {
        v["code"]: {k: float(p["value"]) for k, p in (v.get("params") or {}).items()} for v in vs
    }
    total = sum(float(v["mix_pct"]["value"]) for v in vs) or 100.0
    mix = {v["code"]: float(v["mix_pct"]["value"]) / total for v in vs}
    return variants, params, mix
