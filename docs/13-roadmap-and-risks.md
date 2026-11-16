# 13 — Roadmap and risks

## 1. Phases

Status at 2026-11-13: **P0 and P1 are complete** (reports in the repo: `docs/phase-reports/P0.md`, `P1.md`). Next is P2.

| Phase | Name | Outcome | Depends on |
|---|---|---|---|
| P0 ✅ | Foundations and spikes | Monorepo, Docker Compose (multi-arch), FastAPI + React shells, Postgres, CI; spikes: OCP/OR-Tools/WeasyPrint on ARM64, LiteLLM → Groq + Gemini, `sysml2kit` vs own serializer. Done: `make e2e` 14/14 on amd64 and arm64 (QEMU); queue = procrastinate (ADR-019, Proposed); own SysML serializer kept; spike c (live Groq/Gemini calls) still needs the owner's API keys | — |
| P1 ✅ | Domain model, library, data pack | `packages/domain` with all artifact models + JSON Schema → TS; catalog seed (both archetypes); Karnataka pack with sources; golden fixtures; catalog/pack tests. Done: e2e 41/41; catalog 2026.11.1 (186 entries); pack KA 2026.11.1 (84 values, **70 marked `verify`**); `packages/library` added (ADR-021, Proposed) | P0 |
| P2 | Harness, wizard, intake | LangGraph pipeline skeleton with stage runs, approvals, staleness, SSE, LLM router with budgets; web wizard (adds the "Do you have a BoM?" question, BoM column-mapping confirmation and optional parts table); S1 intake (text, PDF, STEP, photo); pipeline UI | P1 |
| P3 | BoM, routing, quality | S2 with the three intake cases, gap and CAD cross-check, suggestions, editable review table, make/buy preview; S3 with time standards and precedence; S4 quality plan that records the check fields needed by the Factory Package (catalog bump to 2026.11.2); vendor research agent (parallel) | P2 |
| P4 | Capacity and inventory | S5 engine (takt, sizing rules, CP-SAT, strategies A/B/C, custom workcenters); S6 inventory and flow matrix | P3 |
| P5 | Work orders, shifts, utilities | S7 WO design, sequence, roster, compliance, welfare (no work instructions; they move to S12, doc 15); S8 utilities. **Start with the data confirmations in §3** | P4 |
| P6 | Layout and 3D | S9 block + line layout, greenfield/shed modes, 2D SVG, DXF, R3F viewer, editing with checks | P5 |
| P7 | Simulation | S10 model generator, experiments, KPIs, recommendations as change sets | P6 |
| P8 | Finance, reports, SysML | S11 finance; S12 report pack (web, PDF, XLSX incl. BoM workbook, DXF, SysML, ZIP); admin page | P7 |
| P8b | Factory Package | Documents step in S12: work instruction per station, inspection instruction and check sheet per check, flow chart, control plan, SOP set, training matrix, equipment list; coverage validators, review UI, PDF/XLSX/DOCX export, ZIP layout (doc 15; ADR-023) | P8 |
| P9 | Production hardening | Oracle deploy, Cloudflare Pages/Turnstile/Access, rate limits, global budget, retention job, backups, load test with both golden products; native arm64 run on Ampere; slim the 3.17 GB cad-worker image; pin GitHub Actions by SHA before enabling pushes | P8b |

The admin page can move earlier (P4/P5) if catalog editing becomes a bottleneck.

## 2. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Groq free-tier limits too low for multi-agent runs | High | High | Engines do arithmetic; small contexts; per-stage budgets; caching; queue; BYO key; paid role swap via config |
| R2 | Groq free models lack vision | Medium | Medium | `vision` role on Gemini free tier; photo intake optional |
| R3 | OCP/CadQuery or other deps fail on ARM64 | Medium | High | P0 spike; conda-forge; CAD worker could run on x86 elsewhere later |
| R4 | Oracle A1 capacity unavailable / account issues | Medium | High | Retry regions; keep x86 Docker Compose working for a cheap VPS fallback |
| R5 | Catalog estimates (times, prices, kW) inaccurate | High | High | Provenance + confidence; ranges; assumptions register; admin edits; user overrides; golden products reviewed by a manufacturing engineer |
| R6 | Regulatory values outdated | Medium | Medium | Dated pack with sources and `verify` flags; quarterly review; disclaimers |
| R7 | LLM invents vendors or facts | Medium | High | Evidence-required extraction; drop unsourced records; tests with fake pages |
| R8 | Solver too slow on 2 cores for larger products | Medium | Medium | Time limits; fallbacks; warm starts; problem size limits for POC |
| R9 | Simulation too slow | Medium | Medium | Aggregated events; fewer replications under load; worker queue |
| R10 | Abuse of open endpoints (no login) | Medium | Medium | Turnstile, rate limits, global budget, upload sandboxing |
| R11 | Users treat outputs as certified engineering/legal advice | Medium | High | Grade labels per output; disclaimers; "verify with" notes per section |
| R12 | SysML v2 libraries immature | Medium | Low | Own serializer for a defined subset; validator in CI |
| R13 | Scope creep (more products, states) | High | Medium | Archetype + pack structure makes additions data work; POC limited to two golden products |
| R14 | Mixed-model sequencing effects missed by static balance | Medium | Medium | Per-variant overload limit + simulation check |
| R15 | Users treat AI-written work instructions or inspection sheets as validated | Medium | High | Label *Draft — validate on the shop floor* on every document; blank sign-off block; coverage validators so safety and quality content cannot be dropped; doc 15 §7 limits |
| R16 | A wrong or incomplete BoM flows into every later stage | Medium | High | Three-case S2 with gap check and CAD cross-check; AI additions only as suggestions; strict approval gate (no unresolved suggestions); origin and confidence on every line |
| R17 | Pack values marked `verify` (70 of 84 at P1) are wrong or out of date | High | Medium | Confirm the items in §3 before P5 and P6; reports show the verify flag and source; quarterly review |
| R18 | Token cost of the documents step exceeds free-tier quota | Medium | Medium | Per-document budget and resume; per-document caching by `inputs_hash`; template text with parameters for SOPs; measure in P8b |

## 3. Open items (decide during implementation)

Decided: worker queue is procrastinate (P0; ADR-019 awaiting approval). SysML writer is our own serializer (P0).

Waiting on the owner:

- Approve or reject ADR-019, ADR-020, ADR-021 and the SysML recommendation (P0/P1 reports).
- Add real `GROQ_API_KEY` and `GEMINI_API_KEY` to `.env` and run spike c to record real rate-limit headers and confirm default model names per role (P0 §5c).
- Spot-check three pack values: `power.ht2a.demand_charge` (365 from news vs 350 in the order PDF), `labour.overtime_cap_quarter_h` (144 central vs 145 Karnataka 2023), `welfare.canteen_min_workers` (100 vs 50).

**Before P5 (utilities, roster):** confirm the KERC March 2026 review order text and effective date, the electricity tax rate, the Karnataka OSH rules (hours, spread-over, overtime, night shift), welfare thresholds and fire thresholds (all `null` now). **Before P6 (layout):** KIADB ground coverage, FAR and setbacks, and the NBC exit travel distance (45 m is an unsourced working value).

Still open:

- S4 check fields for the Factory Package and the catalog `check_type` additions — P3.
- `python-docx` licence and arm64 check; DOCX export layout — P8b.
- SysML: bundle OMG library files for `SI::*` or keep omitting it — P8.
- Free GLB asset set for machine classes and its licence — P6.
- Whether the SysML v2 export has a real consumer (decides its priority) — owner.
- Later: Excel template import; more states; accounts and sharing; 3D replay of simulation; paid LLM option.
