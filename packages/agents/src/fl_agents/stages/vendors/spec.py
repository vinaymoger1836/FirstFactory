"""Vendor research stage (docs/09 §2, ADR-028): parallel to S3, starts when the BoM is approved.

One class per tool step (resumable, progress per class):
  shared cache → web search (query = catalog class name + region only) → fetch top pages
  (SSRF guard, robots.txt) → ``extractor`` role reads each page → the engine keeps a vendor
  only with evidence found in the page text → score, dedupe, cap at 8 → cache (30 days).

Web trouble never fails the stage: it becomes a gap on the class and the stage still
completes. The report is a list of leads, approved automatically (nothing to decide).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fl_agents.llm.errors import CassetteMiss, LlmOutputInvalid, LlmUnavailable
from fl_agents.pipeline.stage import StageCtx, StageSpec, Summary
from fl_agents.stages.common import bom_of
from fl_agents.stages.vendors import prompts
from fl_agents.stages.vendors.schemas import PageVendorsOut
from fl_agents.web import FixtureMissError, SearchUnavailableError, WebError
from fl_domain.artifacts.vendors import VendorClass
from fl_domain.common import StageKey
from fl_domain.library.catalog import CommodityClassEntry, PolicyEntry, VendorPolicy
from fl_engines.vendors import (
    Candidate,
    Verified,
    dedupe,
    score_vendor,
    to_vendor,
    verify_candidate,
)

__all__ = ["VendorsSpec"]

log = logging.getLogger(__name__)


class VendorsSpec(StageSpec):
    key: StageKey = "vendors"
    auto_approve = True

    async def _policy(self, ctx: StageCtx) -> VendorPolicy:
        cat = await ctx.catalog()
        for e in cat.entries:
            if isinstance(e, PolicyEntry) and e.vendors is not None:
                return e.vendors
        return VendorPolicy(
            weights={"relevance": 40, "certifications": 15, "evidence": 30, "region": 15}
        )

    async def _site(self, ctx: StageCtx) -> tuple[str, str]:
        """(state code, a name for the region to put in the search query)."""
        tpl = ctx.project["template"] or {}
        state = str((tpl.get("site") or {}).get("state") or "KA")
        pol = await self._policy(ctx)
        names = pol.region_state_names.get(state) or [state]
        return state, names[0]

    # ── plan ──────────────────────────────────────────────────────────

    async def plan(self, ctx: StageCtx) -> None:
        if "classes" in ctx.work:
            return
        bom = await bom_of(ctx)
        cat = await ctx.catalog()
        classes = {e.code: e for e in cat.entries if isinstance(e, CommodityClassEntry)}
        pol = await self._policy(ctx)
        spend: dict[str, float] = {}
        for ln in bom.mbom:
            it = bom.item(ln.child)
            if it is None or it.kind in ("assembly", "product") or it.make_or_buy.value != "buy":
                continue
            if it.commodity_class in classes:
                cost = (ln.ext_cost_inr.base if ln.ext_cost_inr else 0.0) + ln.qty.value * 1e-6
                spend[it.commodity_class] = spend.get(it.commodity_class, 0.0) + cost
        ranked = sorted(spend, key=lambda c: (-spend[c], c))
        keep, skipped = ranked[: pol.max_classes], ranked[pol.max_classes :]
        await ctx.save_work(
            {
                "classes": [{"code": c, "name": classes[c].name} for c in keep],
                "skipped": skipped,
                "done": {},
                "pages": {},
            }
        )

    async def next_tool(self, ctx: StageCtx) -> str | None:
        done = ctx.work.get("done", {})
        for c in ctx.work.get("classes", []):
            if c["code"] not in done:
                return f"class:{c['code']}"
        return None

    # ── one class ─────────────────────────────────────────────────────

    async def run_tool(self, ctx: StageCtx, action: str) -> None:
        code = action.partition(":")[2]
        cat = await ctx.catalog()
        cc = cat.by_code().get(code)
        if not isinstance(cc, CommodityClassEntry):
            await self._finish(ctx, code, VendorClass(vendor_class=code, gaps=["unknown class"]))
            return
        pol = await self._policy(ctx)
        state, region_name = await self._site(ctx)
        store, s = ctx.rt.store, ctx.rt.settings
        key = f"{code}|{state}"
        refresh = "refresh" in str((ctx.run["params"] or {}).get("note", "")).lower()
        if s.vendor_cache_enabled and not refresh:
            row = await store.vendor_cache_get(key)
            if row is not None:
                vc = VendorClass.model_validate(row["payload"])
                vc.cached_at = row["fetched_at"].isoformat(timespec="seconds")
                await self._finish(ctx, code, vc)
                return

        web = ctx.rt.web
        query = f"{cc.name} manufacturers and suppliers in {region_name}, India"
        gaps: list[str] = []
        queries = [query]
        urls: list[str] = []
        try:
            results = await web.search(query, max_results=pol.pages_per_class + 3)
            seen: set[str] = set()
            for r in results:
                if r.url not in seen:
                    seen.add(r.url)
                    urls.append(r.url)
            urls = urls[: pol.pages_per_class]
            if not urls:
                gaps.append("the search returned no pages")
        except (SearchUnavailableError, FixtureMissError) as e:
            gaps.append(str(e))
        pages: dict[str, Any] = dict(ctx.work.get("pages", {}))
        verified: list[tuple[Verified, float, dict[str, float]]] = []
        read = dropped = 0
        phrases = [cc.name, *cc.aliases]
        for url in urls:
            entry = pages.get(url)
            if entry is None:
                try:
                    page = await web.fetch(url)
                except WebError as e:
                    gaps.append(f"{url}: {e}"[:200])
                    continue
                if len(page.text) < 200:
                    gaps.append(f"{url}: the page has almost no text")
                    continue
                msgs = prompts.extractor_messages(
                    cc.name, cc.aliases, page.title, page.url, page.text
                )
                try:
                    out = await ctx.llm("extractor", msgs, PageVendorsOut, out_tokens=1500)
                    cands = [v.model_dump() for v in out.vendors]
                except LlmOutputInvalid:
                    cands = []
                    gaps.append(f"{url}: the model's answer could not be read")
                except LlmUnavailable as e:
                    cands = []
                    gaps.append(f"{url}: the model could not read this page ({str(e)[:80]})")
                except CassetteMiss:
                    cands = []
                    gaps.append(f"{url}: no recorded model answer for this page (replay)")
                entry = {
                    "url": page.url,
                    "fetched_at": page.fetched_at,
                    "text": page.text[:30_000],
                    "candidates": cands,
                }
                pages[url] = entry
                await ctx.save_work({"pages": pages})
            read += 1
            kept = 0
            for c in entry["candidates"]:
                v = verify_candidate(
                    Candidate(
                        name=c.get("name") or "",
                        quote=c.get("evidence_quote") or "",
                        city=c.get("city"),
                        state=c.get("state"),
                        products=list(c.get("products") or []),
                        certifications=list(c.get("certifications") or []),
                        moq=c.get("moq"),
                        indicative_price=c.get("indicative_price"),
                        lead_time=c.get("lead_time"),
                        contact_url=c.get("contact_url"),
                    ),
                    entry["text"],
                    entry["url"],
                    entry["fetched_at"],
                    pol,
                )
                if v is None:
                    continue
                sc, parts = score_vendor(v, pol, phrases, state)
                verified.append((v, sc, parts))
                kept += 1
            if kept == 0:
                dropped += 1
        ranked = dedupe(verified)
        ranked.sort(key=lambda t: -t[1])
        vendors = [to_vendor(v, sc, parts) for v, sc, parts in ranked[: pol.max_vendors_per_class]]
        if not vendors and not gaps:
            gaps.append("no vendor with evidence was found on the pages read")
        vc = VendorClass(
            vendor_class=code,
            name=cc.name,
            vendors=vendors,
            queries_run=queries,
            gaps=gaps,
            pages_read=read,
            pages_dropped=dropped,
            notes="Searched by class name and region; specification fields are not on BoM "
            "lines yet (ADR-027).",
        )
        if s.vendor_cache_enabled and not gaps:
            await store.vendor_cache_put(
                key, code, state, vc.model_dump(mode="json"), pol.cache_ttl_days
            )
        await self._finish(ctx, code, vc)

    async def _finish(self, ctx: StageCtx, code: str, vc: VendorClass) -> None:
        done = dict(ctx.work.get("done", {}))
        done[code] = vc.model_dump(mode="json")
        await ctx.save_work({"done": done})
        await ctx.emit(
            "tool_call",
            {
                "tool": "vendor_class",
                "args": {"class": code},
                "result": f"{len(vc.vendors)} vendors",
            },
        )

    # ── draft ─────────────────────────────────────────────────────────

    async def draft(self, ctx: StageCtx) -> dict[str, Any] | None:
        state, _ = await self._site(ctx)
        done = ctx.work.get("done", {})
        classes = [done[c["code"]] for c in ctx.work.get("classes", []) if c["code"] in done]
        s = ctx.rt.settings
        notes: list[str] = []
        if s.web_mode == "replay":
            notes.append("Results come from recorded searches and pages (replay mode).")
        elif not ctx.rt.web.can_search:
            notes.append("No search key is set (TAVILY_API_KEY): classes show a gap, not vendors.")
        return {
            "classes": classes,
            "region": state,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "notes": notes,
            "skipped_classes": ctx.work.get("skipped", []),
        }

    async def summarise(self, ctx: StageCtx, artifact: dict[str, Any] | None) -> Summary:
        assert artifact is not None
        cls = artifact["classes"]
        n = sum(len(c["vendors"]) for c in cls)
        cached = sum(1 for c in cls if c.get("cached_at"))
        gaps = sum(1 for c in cls if c["gaps"])
        text = (
            f"{n} vendor lead(s) with source links across {len(cls)} bought class(es); "
            f"{cached} from the shared cache, {gaps} with gaps."
        )
        if artifact.get("skipped_classes"):
            text += (
                f" {len(artifact['skipped_classes'])} class(es) were not searched (limit per run)."
            )
        text += " Leads, not endorsements: verify before you buy."
        return Summary(text=text, questions=[], notices=list(artifact.get("notes", [])))
