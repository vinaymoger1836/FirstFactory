# P3b — S3 routing, S4 quality plan, vendor research

**Status:** complete. **End-to-end check: 176/176** in replay mode (recorded model answers, searches and pages; no network, no quota) on amd64, 2026-11-19, against the final code. A recording pass with `--record-new` passed 175/175 first (the planted-page check needs replay). Results are in `e2e-results/p3.json` (git-ignored).

**Scope:** this finishes Phase 3 (P3a built S2). P3b is S3 routing, S4 quality plan (with the docs/15 §3 check fields) and vendor research. I made no commits. Everything is in the working tree: new files include `golden/web/` and 41 new cassettes in `golden/cassettes/`, which you commit with the code.

## Acceptance (docs/14 §5, P3)

| Criterion | Result |
|---|---|
| G1/G2 produce complete MBOMs and routings; all validators pass | **Pass.** G1: 13 operations, G2: 15, each equal to `golden/*/routing.yaml` (process, item, consumed parts) and with the golden precedence exactly (G2's parallel bearing press included). Every MBOM line is consumed. No validator error. |
| LLM-estimated times are flagged | **Pass.** All G1/G2 times are catalog formulas (0 model tokens in S3 and S4). A planted LLM estimate with confidence 0.9 is refused (`routing.llm_conf`, 422); one with confidence 0.3 is accepted, raises a `routing.llm_estimate` warning and lands in the assumptions register. |
| G8 (all three BoM cases) | Unchanged from P3a; still passing in the same run. |
| Every G1/G2 check carries the docs/15 §3 fields | **Pass.** G1: 26 check records, G2: 36; characteristic, limit, method, instrument class, sample, frequency and reaction are non-empty on every one, and every inline and end-of-line check is bound to a routing operation. |
| AQL sample sizes match the standard table | **Pass.** The e2e re-derives ISO 2859-1 (level II, single, normal) from the standard's rules, not from the catalog. All 11 G1 and 16 G2 incoming items match (letter, sample size, accept, reject). Example: G1 cells, lot 19,600 → code M, n = 315, accept 5 / reject 6 at AQL 0.65. |
| G1 vendor run: every vendor has a source URL; a planted page without evidence gives no vendor | **Pass.** Every vendor has a source URL and a quote, and the quote and name are in the recorded page text. The planted page was read and dropped (class `li_ion_cell`: 3 pages read, 3 dropped, no vendor leaked). |

Also checked: G1 policy channels 8 (2 % capacity test) and 398 (100 %), equal to `golden/power_bank/expected.yaml`; the policy toggle gives a new version with the capacity test at 100 %; varnish oven batch of 60 and 7,200 s; rework load = units × (1 − FPY) × rework time; second project reuses the shared vendor cache; the SSRF guard blocks metadata, loopback, private, IPv6 loopback, `file:` and credential URLs inside the agents container.

## 1. What was built

| Area | Result |
|---|---|
| Catalog 2026.11.5 | Check types gain `characteristic`, `limit`, `instrument_class`, `frequency`, `reaction`, `supports_compliance` (all 24). Routing steps gain `builds`/`consumes`; archetypes gain a `quality` block. New kinds `sampling_plan` (ISO 2859-1 Table I and II-A) and `policy` (`operating_defaults`, `vendor_research`). The loader enforces the new fields and checks the sampling table. Existing projects stay pinned to their version. |
| S3 routing (`fl_engines/routing`, `stages/routing`) | Steps from the archetype's typical routing, mapped to the BoM's part numbers; precedence derived from the data flow; time standards in priority order (user, catalog formula, MTM, model estimate) with the safe evaluator; per-variant times with a mix-weighted base; batch spec from the machine class; findings for gaps, defaults and estimates; a step with no time source is a blocking finding. One `reasoner` call only for assemblies the catalog routing does not cover. |
| S4 quality plan (`fl_engines/quality`, `stages/quality`) | No model. IQC plans from the catalog's ISO 2859-1 table (follows the standard's arrows, 100 % when the sample reaches the lot), inline and EOL checks bound to routing operations, patrol, OQC, lab, rework from FPY, quarantine estimate, compliance links from the data pack, both power bank test policies computed side by side. Review decisions: test policy, inspection level, lot days, AQL per item. |
| Vendors (`fl_engines/vendors`, `fl_agents/web`, `stages/vendors`) | Search (Tavily) with class name and region only, fetch through an SSRF guard with `robots.txt` and polite delays, extraction by the `extractor` role, engine-side evidence check, score, dedupe, shared cache (`vendor_cache`, migration 0004, 30 days). Web trouble becomes a gap, never a failed stage. Approves itself. |
| Domain and validators | Additive fields on `Routing`, `QualityPlan`, `VendorReport` (docs/04). New checks: unconsumed MBOM lines (downgraded to a warning where a finding already explains the gap), check records bound to operations, vendor duplicates and evidence. Validators now receive the approved upstream BoM and routing. |
| API | Answers accept `time:`, `drop:`, `ack:` (routing) and `policy:`, `aql:`, `level:`, `lotdays:` (quality). Routing approval is blocked while a blocking finding is open. |
| Web | Routing panel (operations with time, source and edit, precedence graph with a text list, open items), quality panel (all checks with details, incoming sampling with editable AQL and level, test policy chooser, line/OQC/lab/rework, compliance) and vendor panel (leads with score, evidence quotes and source links, gaps). Tabs have roving focus and arrow keys; both themes use the shared tokens; motion respects `prefers-reduced-motion`. |
| Record and replay | LLM mode `record_missing` (answer from recorded cassettes, record only what is missing) and web fixtures under `golden/web`. `scripts/make_web_fixtures.py plant` adds the evidence-free test page and its hand-written model answer. |
| Docs | ADR-028 (*Proposed*); as-built sections in docs 04, 05, 09, 10; `docs/versions.md`; docs/14 §0 status. |

## 2. End-to-end check (176 checks)

| Group | Checks |
|---|---|
| P0 to P2 and P3a (adapted: S3 and S4 are real stages that wait for approval; 8 stubs remain) | 135 |
| S3 per product: awaiting approval and validators pass, equal to golden routing, precedence, MBOM consumed, times equal catalog inputs, formulas only and 0 tokens (+ G2 oven batch) | 13 |
| S4 per product: validators and 0 tokens, §3 fields, bound to operations, AQL equal the standard, rework, compliance, approved (+ G1 cells, 8/398 channels, policy toggle) | 19 |
| Vendors per product: stage finished, source URL and quote, evidence in the recorded page (+ G1 planted page, G2 shared cache) | 7 |
| LLM estimate refused at 0.9, flagged at 0.3 | 2 |
| SSRF guard | 1 |

## 3. Measured

| Item | Value |
|---|---|
| S3 and S4 model tokens, G1 and G2 | 0 (engine only) |
| Vendor research, one product | up to 8 classes × 3 pages, one `extractor` call per page, 1,500 output tokens allowed per call |
| New cassettes | 41 (extractor answers, one hand-written for the planted page); cassettes now 85 files, 724 kB |
| Web fixtures | 14 searches, 34 pages (one planted), 781 kB |
| Vendor yield (recorded run) | about 4 leads with evidence per product, 8 to 12 gaps (see §4) |
| G1 quality plan | 26 checks, 11 AQL plans, about 4.4 inspector-hours a day (catalog times), 8 or 398 test channels |
| G2 quality plan | 36 checks, 16 AQL plans, about 6.0 inspector-hours a day |

## 4. Problems found and fixed

| Problem | Fix |
|---|---|
| S4 failed: it read the BoM from its pinned upstream, but S4 depends on routing only | The BoM comes from the latest approved version (`bom_of`) |
| The vendor stage failed when the Groq JSON mode rejected one page | A provider refusal, an unreadable answer or a cassette miss on a page is now a gap on that class; output budget raised to 1,500 tokens (gpt-oss reasoning counts) |
| Fan winding resistance appeared at the winding machine and at its own test operation | A check with its own routing operation attaches only to that operation |
| Redirected pages were stored under the requested URL, so evidence could not be found by its final URL | The e2e indexes fixtures by both URLs |
| A recording pass would overwrite all 44 P3a cassettes and could change golden results | New `record_missing` mode records only what is missing |
| e2e GET calls timed out once on a Windows–Docker hiccup | GETs retry on timeout |

## 5. Deviations and limits

- **Vendor yield is low.** Many search hits are data-broker or social sites that answer 403/429 or forbid bots in `robots.txt`, and `gpt-oss-20b` sometimes returns JSON the router cannot read. All of these are shown as gaps. A better yield needs a search that excludes those domains, or a stronger extractor model; neither changes the evidence rules.
- **Search is by class name, not by specification.** `BomLine.spec` (ADR-027) is not built; vendor research reports this on each class.
- **Quality limits are text against the product specification.** The catalog has no numeric limits (rule 4); you set them in your inspection instructions.
- **S3 and S4 use a deterministic summary, no `writer` call,** because every number is an engine number and golden runs need no cassettes for them.
- **Working days, IQC lot days and pallet footprint are catalog estimates** (`policy:operating_defaults`, confidence 0.4) until S5 and S6 replace them. The quarantine area (one held lot per incoming item) is only an order of magnitude.
- **MTM time elements are wired but unused:** no catalog process lists MTM elements yet.
- **ISO 2859-1 source.** The table comes from a public reference page labelled ISO 2859-1 (URL in the catalog entry), not from the standard text. Table I letters were checked against that page's sample sizes for every lot range and level. Verify against a licensed copy.
- **Owner's rules:** no unit tests and no per-change test runs (engines were exercised once on the golden BoMs before the end-to-end run); no commits; no model keys in fixtures (scanned).
- **Docker:** I started Docker Desktop (per-user install) to run the check.

## 6. Items for you

1. **Approve ADR-028** (and ADR-026/027 still *Proposed*) in `docs/12-adrs.md`.
2. **Commit** the new `golden/web/` fixtures and cassettes with the code; they are the replay data (public page text, no keys).
3. **Verify the sampling table** against IS 2500 (Part 1) / ISO 2859-1 when you can.
4. **Decide on vendor yield:** exclude broker domains in the search, or use a stronger extractor model. Either needs a re-record with `--record-new` after deleting the affected fixtures.
5. **Next: P4** (capacity and inventory). S5 will read `quality.capacity_sample_pct`, the inline and EOL checks and the routing's per-variant times.

## 7. Files

- **New:**
  - `library/catalog/{sampling_plans,policies}.yaml`
  - `packages/engines/src/fl_engines/{routing,quality,vendors}/`
  - `packages/agents/src/fl_agents/{web/,stages/common.py,stages/routing/,stages/quality/,stages/vendors/}`
  - `apps/api/migrations/versions/0004_vendor_cache.py`
  - `apps/web/src/components/{routing,quality,vendors,ui}/`, `apps/web/src/lib/stageTypes.ts`
  - `scripts/e2e_p3b.py`, `scripts/make_web_fixtures.py`
  - `golden/web/`, 41 files in `golden/cassettes/`
  - this report
- **Changed:**
  - catalog: `catalog.yaml`, `archetypes.yaml`, `check_types.yaml`
  - `fl_domain`: `library/catalog.py`, `artifacts/{routing,quality,vendors}.py`, `validators.py`, `schema.py`
  - `fl_engines/bom` (`assembly_id`, `leaf_part_no`), `fl_library/checks.py`
  - `fl_agents`: `lifecycle.py`, `llm/router.py`, `pipeline/{graph,runtime}.py`, `settings.py`, `store/{repo,tables}.py`, `pyproject.toml` (httpx), `uv.lock`
  - `fl_api`: `main.py`, `routes/pipeline.py`
  - web: `routes/StageView.tsx`
  - `infra/docker-compose.yml`, `infra/docker-compose.e2e.yml`
  - `scripts/e2e.py`, `e2e_p2.py`, `e2e_p3.py`
  - docs 04, 05, 09, 10, 12, 14, `versions.md`
